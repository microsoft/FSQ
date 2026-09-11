# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import asyncio
import base64
import json
import time
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

import pytest

from fsq_agent.adapters.control_plane._cases import discover_cases, resolve_case
from fsq_agent.adapters.control_plane._evidence import (
    UI_SNAPSHOT_LIMIT_BYTES,
    EvidenceProjection,
    read_replay_frames,
    read_screenshot,
    read_step_artifacts,
    read_ui_snapshot,
    safe_exception_message,
    safe_text,
)
from fsq_agent.adapters.control_plane._execution import _run_explore, _run_strict, prepare_run
from fsq_agent.adapters.control_plane._readiness import provider_readiness, readiness
from fsq_agent.adapters.control_plane._replay import read_replay_video, replay_video_metadata, store_replay_video
from fsq_agent.adapters.control_plane._server import _RequestHandler
from fsq_agent.adapters.control_plane._state import BusyError, ControlPlaneState, TaskCancelledError
from fsq_agent.adapters.control_plane._targets import discover_targets
from fsq_agent.case_dsl import FsqCaseLoader
from fsq_agent.config import Settings
from fsq_agent.control_plane import ControlPlaneServer, ControlPlaneServerOptions
from fsq_agent.models import (
    AndroidDevice,
    AndroidDeviceDiscoveryResult,
    DynamicAgentOutcome,
    HarnessActionResult,
    HarnessArtifactRef,
    HarnessContext,
    RunEvent,
    RunnerEvent,
    RunnerStepResult,
    VerificationResult,
)


def _settings(tmp_path: Path, platform: str = "android") -> Settings:
    settings = Settings(harness={"platform": platform})
    settings.workspace.root_dir = tmp_path / ".fsq-agent-workspace"
    settings.workspace.root_dir.mkdir(parents=True, exist_ok=True)
    settings.workspace.config_path = settings.workspace.root_dir / ".fsq" / "config" / f"config.{platform}.yaml"
    settings.workspace.config_path.parent.mkdir(parents=True, exist_ok=True)
    settings.workspace.config_path.write_text(f"platform: {platform}\n", encoding="utf-8")
    settings.cases.dir = tmp_path / "cases"
    settings.cases.dir.mkdir(exist_ok=True)
    settings.output.root_dir = tmp_path / "output"
    settings.output.root_dir.mkdir(exist_ok=True)
    settings.output.runs_dir = settings.output.root_dir / "runs"
    settings.output.runs_dir.mkdir(exist_ok=True)
    return settings


def _case(path: Path, *, platform: str = "android", command: str = "waitMs:\n    duration_ms: 1", app_id: bool = True) -> None:
    app = "appId: com.example.app\n" if app_id else ""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"schemaVersion: fsq.ai-test/v1\nname: {path.stem}\nplatform: {platform}\n{app}---\n- {command}\n",
        encoding="utf-8",
    )


def _open_loopback(request: str | Request):
    url = request.full_url if isinstance(request, Request) else request
    parsed = urlparse(url)
    assert parsed.scheme == "http"
    assert parsed.hostname in {"127.0.0.1", "localhost"}
    return urlopen(request, timeout=5)  # noqa: S310 - restricted to this in-process loopback server.


def _post_json(url: str, payload: dict[str, object]) -> dict[str, object]:
    request = Request(url, method="POST", data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})  # noqa: S310
    with _open_loopback(request) as response:
        return json.loads(response.read())


def _wait_for_terminal(url: str) -> dict[str, object]:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        with _open_loopback(url) as response:
            snapshot = json.loads(response.read())
        if snapshot["terminal"]:
            return snapshot
        time.sleep(0.01)
    raise AssertionError("Control Plane run did not reach a terminal state")


@pytest.mark.parametrize("disconnect_error", [ConnectionAbortedError, ConnectionResetError, BrokenPipeError])
def test_request_handler_closes_connection_when_client_disconnects(
    monkeypatch: pytest.MonkeyPatch,
    disconnect_error: type[OSError],
) -> None:
    def raise_disconnect(_handler: BaseHTTPRequestHandler) -> None:
        raise disconnect_error()

    monkeypatch.setattr(BaseHTTPRequestHandler, "handle_one_request", raise_disconnect)
    handler = object.__new__(_RequestHandler)
    handler.close_connection = False

    handler.handle_one_request()

    assert handler.close_connection is True


def test_state_holds_single_active_task_through_cancellation() -> None:
    state = ControlPlaneState()
    request_id = state.reserve(workspace_name="checkout", platform="android", target_id="serial", mode="explore", source={"goal": "Do it"})

    snapshot = state.request_cancel(request_id)

    assert snapshot["status"] == "preparing"
    assert snapshot["cancelRequested"] is True
    with pytest.raises(BusyError):
        state.reserve(workspace_name="checkout", platform="web", target_id="chrome", mode="explore", source={"goal": "Other"})
    with pytest.raises(TaskCancelledError):
        state.raise_if_cancelled(request_id)

    state.finish(request_id, status="cancelled", summary="Run cancelled.")
    replacement = state.reserve(workspace_name="checkout", platform="web", target_id="chrome", mode="explore", source={"goal": "Other"})
    assert replacement != request_id


@pytest.mark.asyncio
async def test_explore_cancelled_before_async_task_starts_remains_cancelled(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(tmp_path, "web")
    monkeypatch.setattr("fsq_agent.adapters.control_plane._execution.validate_target", lambda *_args: None)
    monkeypatch.setattr("fsq_agent.adapters.control_plane._execution.validate_runtime_settings", lambda *_args: None)
    monkeypatch.setattr("fsq_agent.adapters.control_plane._execution.require_provider", lambda *_args: None)
    state = ControlPlaneState()
    request_id = state.reserve(workspace_name="checkout", platform="web", target_id="chrome", mode="explore", source={"goal": "Cancel immediately"})
    prepared = prepare_run(
        request_id=request_id,
        settings=settings,
        body={"mode": "explore", "workspaceName": "checkout", "platform": "web", "targetId": "chrome", "goal": "Cancel immediately"},
    )
    state.request_cancel(request_id)
    await _run_explore(prepared, state)
    snapshot = state.snapshot(request_id)
    assert snapshot["status"] == "cancelled"
    assert snapshot["summary"] == "Run cancelled."


def test_state_sequences_resumable_snapshots_and_releases_only_after_finalizing() -> None:
    state = ControlPlaneState()
    request_id = state.reserve(workspace_name="checkout", platform="web", target_id="chrome", mode="strict", source={"casePath": "a.fsq.yaml"})
    state.transition(request_id, "running")
    state.add_event(request_id, {"label": "one"})
    state.add_event(request_id, {"label": "two"})
    state.transition(request_id, "finalizing")

    snapshot = state.snapshot(request_id, after_sequence=1)

    assert [event["label"] for event in snapshot["events"]] == ["two"]
    assert snapshot["status"] == "finalizing"
    with pytest.raises(BusyError):
        state.reserve(workspace_name="checkout", platform="android", target_id="device", mode="explore", source={"goal": "blocked"})

    state.finish(request_id, status="success", summary="done", result={"status": "success"})
    assert state.snapshot(request_id)["terminal"] is True


def test_state_cancellation_during_finalizing_preserves_execution_authority() -> None:
    state = ControlPlaneState()
    request_id = state.reserve(workspace_name="checkout", platform="web", target_id="chrome", mode="explore", source={"goal": "Go"})
    state.transition(request_id, "running")
    state.transition(request_id, "finalizing")

    state.request_cancel(request_id)
    state.finish(request_id, status="success", summary="completed", result={"status": "success"}, report_available=True)

    snapshot = state.snapshot(request_id)
    assert snapshot["status"] == "success"
    assert snapshot["summary"] == "completed"
    assert snapshot["result"] == {"status": "success"}
    assert snapshot["cancelRequested"] is True


def test_state_cancel_on_terminal_task_is_idempotent_and_does_not_mutate() -> None:
    state = ControlPlaneState()
    request_id = state.reserve(workspace_name="checkout", platform="web", target_id="chrome", mode="explore", source={"goal": "Go"})
    state.finish(request_id, status="success", summary="done", result={"status": "success"}, report_available=True)
    before = state.snapshot(request_id)
    revision = state.revision()

    first = state.request_cancel(request_id)
    second = state.request_cancel(request_id)
    state.finish(request_id, status="failed", summary="late failure")

    assert first == second == before
    assert state.snapshot(request_id) == before
    assert state.revision() == revision


def test_android_target_discovery_normalizes_online_offline_and_unauthorized(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(tmp_path)
    monkeypatch.setattr(
        "fsq_agent.adapters.control_plane._targets.AndroidDeviceDiscovery.discover",
        lambda _self, **_kwargs: AndroidDeviceDiscoveryResult(
            devices=[
                AndroidDevice(serial="emulator-5554", state="device", metadata={"product": "sdk", "model": "Pixel_8", "transport_id": "1"}),
                AndroidDevice(serial="offline-1", state="offline"),
                AndroidDevice(serial="locked-1", state="unauthorized"),
            ]
        ),
    )

    payload = discover_targets(settings)

    assert payload["targetLabel"] == "Device"
    assert [(item["id"], item["status"], item["selectable"]) for item in payload["targets"]] == [
        ("emulator-5554", "ready", True),
        ("offline-1", "offline", False),
        ("locked-1", "unauthorized", False),
    ]
    assert payload["targets"][0]["isDefault"] is True
    assert payload["targets"][0]["metadata"]["model"] == "Pixel_8"


def test_android_target_discovery_has_no_default_with_multiple_online_devices(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(tmp_path)
    monkeypatch.setattr(
        "fsq_agent.adapters.control_plane._targets.AndroidDeviceDiscovery.discover",
        lambda _self, **_kwargs: AndroidDeviceDiscoveryResult(devices=[AndroidDevice(serial="device-1", state="device"), AndroidDevice(serial="device-2", state="device")]),
    )

    payload = discover_targets(settings)

    assert not any(target["isDefault"] for target in payload["targets"])


@pytest.mark.parametrize(
    ("error_code", "target_id", "status"),
    [
        ("adb_missing", "adb-missing", "missing"),
        ("adb_timeout", "adb-timeout", "timeout"),
        ("adb_start_failed", "adb-error", "error"),
        ("adb_failed", "adb-error", "error"),
    ],
)
def test_android_target_discovery_reports_command_failures(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, error_code: str, target_id: str, status: str) -> None:
    settings = _settings(tmp_path)
    monkeypatch.setattr(
        "fsq_agent.adapters.control_plane._targets.AndroidDeviceDiscovery.discover",
        lambda _self, **_kwargs: AndroidDeviceDiscoveryResult(error_code=error_code, error_message="Discovery failed."),
    )
    target = discover_targets(settings)["targets"][0]
    assert (target["id"], target["status"], target["selectable"]) == (target_id, status, False)


def test_configured_targets_are_safe_and_config_owned(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("fsq_agent.adapters.control_plane._targets.importlib.util.find_spec", lambda _name: object())
    monkeypatch.setattr("fsq_agent.adapters.control_plane._targets.validate_strict_core_settings", lambda _settings: None)
    expected = {"web": ("msedge-canary", "Browser"), "windows": ("windows-app", "Application"), "macos": ("macos-app", "Application")}
    for platform, (target_id, target_label) in expected.items():
        settings = _settings(tmp_path / platform, platform)
        if platform == "web":
            settings.harness.web.channel = "msedge-canary"
        if platform == "windows":
            settings.harness.windows.app_path = Path("C:/secret/location/app.exe")
        elif platform == "macos":
            settings.harness.macos.bundle_id = "com.example.app"
        payload = discover_targets(settings)
        assert payload["targetLabel"] == target_label
        assert payload["targets"][0]["id"] == target_id
        if platform == "web":
            assert payload["targets"][0]["label"] == "Microsoft Edge Canary"
        assert "secret/location" not in json.dumps(payload)


def test_case_discovery_is_recursive_sorted_validated_and_platform_filtered(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    _case(settings.cases.dir / "z.fsq.yaml")
    _case(settings.cases.dir / "nested" / "a.fsq.yaml")
    _case(settings.cases.dir / "wrong.fsq.yaml", platform="web", app_id=False)
    _case(settings.cases.dir / "excluded.FSQ.yaml")
    (settings.cases.dir / "broken.fsq.yaml").write_text("not: valid: yaml", encoding="utf-8")

    payload = discover_cases(settings)

    assert [entry["path"] for entry in payload["cases"]] == ["broken.fsq.yaml", "nested/a.fsq.yaml", "wrong.fsq.yaml", "z.fsq.yaml"]
    assert payload["cases"][1]["validationStatus"] == "validated"
    assert payload["cases"][1]["commandCount"] == 1
    assert payload["cases"][2]["selectable"] is False
    assert payload["cases"][0]["diagnostics"]


def test_case_discovery_rejects_symlink_escape_before_loading(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(tmp_path)
    outside = tmp_path / "outside.fsq.yaml"
    outside.write_text("outside", encoding="utf-8")
    linked = settings.cases.dir / "linked.fsq.yaml"
    linked.symlink_to(outside)
    loaded: list[Path] = []

    def track_load(_loader, path: Path):
        loaded.append(path)
        raise AssertionError("escaped case must not be read")

    monkeypatch.setattr("fsq_agent.adapters.control_plane._cases.FsqCaseLoader.load_case", track_load)

    payload = discover_cases(settings)

    assert loaded == []
    assert payload["cases"][0]["selectable"] is False
    assert "escapes" in payload["cases"][0]["diagnostics"][0]


def test_case_discovery_rejects_lifecycle_symlink_escape_before_loading(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(tmp_path)
    outside = tmp_path / "outside.fsq.yaml"
    _case(outside)
    linked = settings.cases.dir / "child.fsq.yaml"
    linked.symlink_to(outside)
    root_path = settings.cases.dir / "root.fsq.yaml"
    root_path.write_text(
        "schemaVersion: fsq.ai-test/v1\nname: Root\nplatform: android\nappId: com.example.app\nonCaseStart:\n  runCase: child.fsq.yaml\n---\n- waitMs:\n    duration_ms: 1\n",
        encoding="utf-8",
    )
    original_load = FsqCaseLoader.load_case
    loaded: list[Path] = []

    def track_load(loader: FsqCaseLoader, path: Path):
        loaded.append(path.resolve())
        return original_load(loader, path)

    monkeypatch.setattr("fsq_agent.adapters.control_plane._cases.FsqCaseLoader.load_case", track_load)

    payload = discover_cases(settings)

    root_entry = next(entry for entry in payload["cases"] if entry["path"] == "root.fsq.yaml")
    assert outside.resolve() not in loaded
    assert root_entry["selectable"] is False
    assert "escapes" in root_entry["diagnostics"][0]


def test_case_discovery_limit_and_contained_resolution(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    for index in range(3):
        _case(settings.cases.dir / f"{index}.fsq.yaml")

    payload = discover_cases(settings, limit=2)

    assert len(payload["cases"]) == 2
    assert payload["truncated"] is True
    assert resolve_case(settings, "0.fsq.yaml") == (settings.cases.dir / "0.fsq.yaml").resolve()
    with pytest.raises(ValueError, match="contained"):
        resolve_case(settings, "../outside.fsq.yaml")
    with pytest.raises(ValueError, match="relative"):
        resolve_case(settings, str((settings.cases.dir / "0.fsq.yaml").resolve()))


def test_case_discovery_derives_ai_requirement_from_registry_snapshots(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    _case(settings.cases.dir / "ai.fsq.yaml", command="assertWithAI:\n    prompt: Verify the page")

    entry = discover_cases(settings)["cases"][0]

    assert entry["selectable"] is True
    assert entry["requiresAiAssertion"] is True


def test_provider_readiness_is_noninteractive_and_closes_without_model_request(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    class Session:
        def close_sync(self) -> None:
            captured["closed"] = True

    def prepare(_settings):
        captured["prepared"] = True
        return Session()

    monkeypatch.setattr("fsq_agent.adapters.control_plane._readiness.prepare_model_provider_session", prepare)

    result = provider_readiness(_settings(tmp_path))

    assert result["status"] == "ready"
    assert captured == {"prepared": True, "closed": True}


def test_explore_preparation_normalizes_goal_and_overrides_only_android_serial(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("fsq_agent.adapters.control_plane._execution.require_android_preflight", lambda *_args, **_kwargs: None)
    settings = _settings(tmp_path)
    settings.harness.android.serial = "configured-device"
    calls: list[str] = []
    monkeypatch.setattr("fsq_agent.adapters.control_plane._execution.validate_target", lambda _settings, target: calls.append(f"target:{target}"))
    monkeypatch.setattr("fsq_agent.adapters.control_plane._execution.validate_runtime_settings", lambda _settings: calls.append("runtime"))
    monkeypatch.setattr("fsq_agent.adapters.control_plane._execution.require_provider", lambda _settings: calls.append("provider"))

    prepared = prepare_run(
        request_id="request-1",
        settings=settings,
        body={"mode": "explore", "workspaceName": "checkout", "platform": "android", "targetId": "selected-device", "goal": "  Verify   settings  "},
    )

    assert prepared.workspace_name == "checkout"
    assert prepared.platform_revision.startswith("sha256:")
    assert prepared.goal == "Verify settings"
    assert prepared.settings.harness.android.serial == "selected-device"
    assert settings.harness.android.serial == "configured-device"
    assert calls == ["runtime", "provider"]


def test_strict_preparation_validates_lifecycle_children_before_harness_creation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(tmp_path)
    _case(settings.cases.dir / "child.fsq.yaml", platform="web", app_id=False)
    root_path = settings.cases.dir / "root.fsq.yaml"
    root_path.write_text(
        "schemaVersion: fsq.ai-test/v1\nname: Root\nplatform: android\nappId: com.example.app\nonCaseStart:\n  runCase: child.fsq.yaml\n---\n- waitMs:\n    duration_ms: 1\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("fsq_agent.adapters.control_plane._execution.validate_target", lambda _settings, _target: None)

    with pytest.raises(ValueError, match="lifecycle child platform"):
        prepare_run(
            request_id="request-1",
            settings=settings,
            body={"mode": "strict", "workspaceName": "checkout", "platform": "android", "targetId": "device", "casePath": "root.fsq.yaml"},
        )


def test_strict_preparation_rejects_lifecycle_symlink_escape_before_loading(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(tmp_path)
    outside = tmp_path / "outside.fsq.yaml"
    _case(outside)
    linked = settings.cases.dir / "child.fsq.yaml"
    linked.symlink_to(outside)
    root_path = settings.cases.dir / "root.fsq.yaml"
    root_path.write_text(
        "schemaVersion: fsq.ai-test/v1\nname: Root\nplatform: android\nappId: com.example.app\nonCaseStart:\n  runCase: child.fsq.yaml\n---\n- waitMs:\n    duration_ms: 1\n",
        encoding="utf-8",
    )
    original_load = FsqCaseLoader.load_case
    loaded: list[Path] = []

    def track_load(loader: FsqCaseLoader, path: Path):
        loaded.append(path.resolve())
        return original_load(loader, path)

    monkeypatch.setattr("fsq_agent.adapters.control_plane._execution.validate_target", lambda _settings, _target: None)
    monkeypatch.setattr("fsq_agent.adapters.control_plane._execution.FsqCaseLoader.load_case", track_load)

    with pytest.raises(ValueError, match="escapes"):
        prepare_run(
            request_id="request-1",
            settings=settings,
            body={"mode": "strict", "workspaceName": "checkout", "platform": "android", "targetId": "device", "casePath": "root.fsq.yaml"},
        )

    assert loaded == [root_path.resolve()]


def test_strict_preparation_builds_registry_and_resolved_steps_without_provider(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("fsq_agent.adapters.control_plane._execution.require_android_preflight", lambda *_args, **_kwargs: None)
    settings = _settings(tmp_path)
    _case(settings.cases.dir / "strict.fsq.yaml")
    calls: list[bool] = []
    monkeypatch.setattr("fsq_agent.adapters.control_plane._execution.validate_target", lambda _settings, _target: None)
    monkeypatch.setattr(
        "fsq_agent.adapters.control_plane._execution.validate_strict_core_settings",
        lambda _settings, requires_ai_assertion=False: calls.append(requires_ai_assertion),
    )
    monkeypatch.setattr("fsq_agent.adapters.control_plane._execution.require_provider", lambda _settings: pytest.fail("provider must not be required"))

    prepared = prepare_run(
        request_id="request-1",
        settings=settings,
        body={"mode": "strict", "workspaceName": "checkout", "platform": "android", "targetId": "device", "casePath": "strict.fsq.yaml"},
    )

    assert prepared.registry_snapshot.resolve("waitMs") is not None
    assert [step.action_name for step in prepared.resolved_steps_by_path[prepared.case_path.resolve()]] == ["wait_ms"]
    assert prepared.requires_ai_assertion is False
    assert calls == [False]


def test_strict_preparation_gates_provider_from_registry_metadata(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("fsq_agent.adapters.control_plane._execution.require_android_preflight", lambda *_args, **_kwargs: None)
    settings = _settings(tmp_path)
    _case(settings.cases.dir / "ai.fsq.yaml", command="assertWithAI:\n    prompt: Verify the page")
    calls: list[str] = []
    monkeypatch.setattr("fsq_agent.adapters.control_plane._execution.validate_target", lambda _settings, _target: None)
    monkeypatch.setattr("fsq_agent.adapters.control_plane._execution.validate_strict_core_settings", lambda _settings, requires_ai_assertion=False: calls.append(f"strict:{requires_ai_assertion}"))
    monkeypatch.setattr("fsq_agent.adapters.control_plane._execution.require_provider", lambda _settings: calls.append("provider"))

    prepared = prepare_run(
        request_id="request-ai",
        settings=settings,
        body={"mode": "strict", "workspaceName": "checkout", "platform": "android", "targetId": "device", "casePath": "ai.fsq.yaml"},
    )

    assert prepared.requires_ai_assertion is True
    assert calls == ["strict:True", "provider"]


def test_strict_execution_composes_real_lifecycle_with_fake_harness(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("fsq_agent.adapters.control_plane._execution.require_android_preflight", lambda *_args, **_kwargs: None)
    settings = _settings(tmp_path)
    _case(settings.cases.dir / "wait.fsq.yaml")
    monkeypatch.setattr("fsq_agent.adapters.control_plane._execution.validate_target", lambda _settings, _target: None)
    monkeypatch.setattr("fsq_agent.adapters.control_plane._execution.validate_strict_core_settings", lambda *_args, **_kwargs: None)
    prepared = prepare_run(
        request_id="request-strict",
        settings=settings,
        body={"mode": "strict", "workspaceName": "checkout", "platform": "android", "targetId": "device", "casePath": "wait.fsq.yaml"},
    )
    state = ControlPlaneState()
    request_id = state.reserve(workspace_name="checkout", platform="android", target_id="device", mode="strict", source={"casePath": "wait.fsq.yaml"})
    prepared.request_id = request_id

    class FakeHarness:
        def __init__(self, store):
            self.store = store

        def get_context(self):
            return HarnessContext(platform="android", session_id="fake")

        def action_space(self):
            return {}

        def before_action(self, step, context):
            return None

        def invoke_action(self, step, context):
            return HarnessActionResult(status="passed", action_name=step.action_name)

        def after_action(self, step, context, action_result):
            return None

        def capture_artifact(self, kind, reason, context, step_id, phase):
            ref = self.store.write_bytes(kind=kind, step_id=step_id, phase=phase, name=reason, data=b"evidence")
            return HarnessArtifactRef(**ref.model_dump(exclude={"step_id", "step_execution_id", "phase"}))

        def classify_error(self, error, phase, step):
            return "unknown"

    monkeypatch.setattr("fsq_agent.adapters.control_plane._execution.HarnessFactory.create_harness", lambda *_args, **kwargs: FakeHarness(kwargs["artifact_store"]))

    _run_strict(prepared, state)

    snapshot = state.snapshot(request_id)
    assert snapshot["status"] == "success"
    assert snapshot["reportAvailable"] is True
    run_id = snapshot["runId"]
    assert isinstance(run_id, str)
    assert (settings.output.runs_dir / run_id / "core-report.json").is_file()
    assert (settings.output.runs_dir / run_id / "run.json").is_file()


@pytest.mark.asyncio
async def test_explore_execution_delegates_to_agent_and_records_without_changing_result(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(tmp_path)
    state = ControlPlaneState()
    request_id = state.reserve(workspace_name="checkout", platform="android", target_id="device", mode="explore", source={"goal": "Verify it"})
    prepared = type("Prepared", (), {"settings": settings, "request_id": request_id, "goal": "Verify it"})()
    captured: dict[str, object] = {}

    class FakeAgent:
        async def run_in_context(self, task, context, event_sink=None, **kwargs):
            captured["task"] = task
            event_sink(RunEvent(run_id=context.run_id, task_id=task.id, type="run_started", title="Started"))
            return DynamicAgentOutcome(
                task=task,
                steps=[],
                verification=VerificationResult(status="failed", summary="Expected failure"),
            )

    monkeypatch.setattr(
        "fsq_agent.adapters.control_plane._execution.FsqAgent.from_settings",
        lambda _settings, _runtime_factory, harness_factory=None: FakeAgent(),
    )

    def record(_self, **kwargs):
        captured["record"] = kwargs
        raise OSError("recording unavailable")

    monkeypatch.setattr("fsq_agent.adapters.control_plane._execution.RecordingService.record", record)

    await _run_explore(prepared, state)

    snapshot = state.snapshot(request_id)
    assert snapshot["status"] == "failed"
    assert snapshot["reportAvailable"] is True
    assert captured["task"].planning_reference_kind == "goal"
    assert captured["record"]["allow_failure"] is True
    assert captured["record"]["publication_directory"] is None
    assert any(event["label"] == "Dynamic recording" for event in snapshot["events"])


def test_evidence_projection_rejects_escape_and_reads_latest_artifacts(tmp_path: Path) -> None:
    state = ControlPlaneState()
    request_id = state.reserve(workspace_name="checkout", platform="android", target_id="device", mode="explore", source={"goal": "Go"})
    runs_dir = tmp_path / "runs"
    run_dir = runs_dir / "run-1"
    screenshot = run_dir / "artifacts" / "screen.png"
    snapshot = run_dir / "artifacts" / "tree.json"
    screenshot.parent.mkdir(parents=True)
    screenshot.write_bytes(b"png-data")
    snapshot.write_text('{"node":"safe"}', encoding="utf-8")
    outside = tmp_path / "secret.png"
    outside.write_bytes(b"secret")
    projection = EvidenceProjection(state, request_id, runs_dir, secret_values=("super-secret",))
    projection.bind_run("run-1")

    projection.project_runner_event(RunnerEvent(run_id="run-1", event_type="artifact_captured", step_id="step-1", payload={"kind": "screenshot", "path": "artifacts/screen.png"}))
    projection.project_runner_event(RunnerEvent(run_id="run-1", event_type="artifact_captured", step_id="step-1", payload={"kind": "ui_snapshot", "path": "artifacts/tree.json"}))
    projection.project_runner_event(RunnerEvent(run_id="run-1", event_type="artifact_captured", payload={"kind": "screenshot", "path": str(outside)}))
    projection.project_run_event(RunEvent(run_id="run-1", task_id="task", type="planning_update", title="Plan", message="token=super-secret"))

    screen_ref, _ = state.artifact(request_id, "screenshot")
    tree_ref, _ = state.artifact(request_id, "ui_snapshot")
    data, headers = read_screenshot(screen_ref)
    tree, _ = read_ui_snapshot(tree_ref)
    assert data == b"png-data"
    assert headers["X-Evidence-Revision"] == "1"
    assert tree["content"] == '{"node":"safe"}'
    assert "super-secret" not in json.dumps(state.snapshot(request_id))


@pytest.mark.parametrize(
    ("ready_title", "failure_title", "failure_category"),
    [("SDK agent ready", "SDK run failed", "sdk_error"), ("Agent runtime ready", "Agent run failed", "agent_runtime_error")],
)
def test_evidence_projection_preserves_runtime_labels_as_data(tmp_path: Path, ready_title: str, failure_title: str, failure_category: str) -> None:
    state = ControlPlaneState()
    request_id = state.reserve(workspace_name="checkout", platform="android", target_id="device", mode="explore", source={"goal": "Go"})
    projection = EvidenceProjection(state, request_id, tmp_path / "runs")
    projection.project_run_event(RunEvent(run_id="run-1", task_id="task", type="planning_update", title=ready_title))
    projection.project_run_event(
        RunEvent(run_id="run-1", task_id="task", type="run_failed", title=failure_title, message="Runtime failed.", payload={"failure_category": failure_category, "failure_reason": failure_category})
    )

    events = state.snapshot(request_id)["events"]
    assert [event["label"] for event in events] == [ready_title, failure_title]
    assert "status" not in events[0]
    assert events[1]["status"] == "failed"
    assert events[1]["payload"] == {"failure_category": failure_category, "failure_reason": failure_category}


def test_strict_step_results_project_to_case_steps_without_event_status_override(tmp_path: Path) -> None:
    state = ControlPlaneState()
    request_id = state.reserve(
        workspace_name="checkout",
        platform="android",
        target_id="device",
        mode="strict",
        source={"casePath": "recorded.fsq.yaml", "caseSteps": [{"stepId": "step-1", "index": 1, "authoredActionName": "tapOn", "actionName": "tap_on", "kind": "action"}]},
    )
    projection = EvidenceProjection(state, request_id, tmp_path / "runs")

    projection.project_runner_event(RunnerEvent(run_id="run-1", event_type="step_finish", step_id="step-1"))
    projection.project_step_result(RunnerStepResult(step_id="step-1", status="failed", duration_ms=12700, failure_category="target_resolution_error", error_message="Target was not found."))

    step = state.snapshot(request_id)["source"]["caseSteps"][0]
    assert step["status"] == "failed"
    assert step["durationMs"] == 12700
    assert step["failureCategory"] == "target_resolution_error"
    assert step["message"] == "Target was not found."


def test_strict_step_projection_preserves_exception_type_when_redacting_local_path(tmp_path: Path) -> None:
    long_local_path = "C:/Users/toyu/fsq_workspace/ws_001/.fsq/runs/android/" + ("very-long-segment/" * 12) + "artifact.png"
    state = ControlPlaneState()
    request_id = state.reserve(
        workspace_name="checkout",
        platform="android",
        target_id="device",
        mode="strict",
        source={"casePath": "recorded.fsq.yaml", "caseSteps": [{"stepId": "step-1", "index": 1, "authoredActionName": "launchApp", "actionName": "launch_app", "kind": "setup"}]},
    )
    projection = EvidenceProjection(state, request_id, tmp_path / "runs")

    projection.project_step_result(
        RunnerStepResult(
            step_id="step-1",
            status="failed",
            failure_category="action_error",
            error_message=f"FileNotFoundError: [Errno 2] No such file or directory: '{long_local_path}' (local path length: {len(long_local_path)})",
        )
    )

    step = state.snapshot(request_id)["source"]["caseSteps"][0]
    assert step["message"] == f"FileNotFoundError: [Errno 2] No such file or directory: '[local path]' (local path length: {len(long_local_path)})"


def test_terminal_strict_steps_without_results_are_marked_skipped() -> None:
    state = ControlPlaneState()
    request_id = state.reserve(
        workspace_name="checkout",
        platform="android",
        target_id="device",
        mode="strict",
        source={
            "casePath": "recorded.fsq.yaml",
            "caseSteps": [
                {"stepId": "step-1", "index": 1, "authoredActionName": "tapOn", "actionName": "tap_on", "kind": "action", "status": "failed"},
                {"stepId": "step-2", "index": 2, "authoredActionName": "assertVisible", "actionName": "assert_visible", "kind": "assertion"},
            ],
        },
    )

    state.finish(request_id, status="failed", summary="done")

    steps = state.snapshot(request_id)["source"]["caseSteps"]
    assert steps[0]["status"] == "failed"
    assert steps[1]["status"] == "incomplete"
    assert steps[1]["message"] == "No authoritative final result is available for this action."


def test_persisted_manifest_hydrates_strict_case_step_results(tmp_path: Path) -> None:
    state = ControlPlaneState()
    request_id = state.reserve(
        workspace_name="checkout",
        platform="android",
        target_id="device",
        mode="strict",
        source={"casePath": "recorded.fsq.yaml", "caseSteps": [{"stepId": "step-1", "index": 1, "authoredActionName": "tapOn", "actionName": "tap_on", "kind": "action"}]},
    )
    runs_dir = tmp_path / "runs"
    run_dir = runs_dir / "run-1"
    run_dir.mkdir(parents=True)
    (run_dir / "evidence-manifest.json").write_text(
        json.dumps({"steps": [{"step_id": "step-1", "status": "failed", "duration_ms": 12, "failure_category": "target_resolution_error", "error_message": "Target was not found."}]}),
        encoding="utf-8",
    )
    projection = EvidenceProjection(state, request_id, runs_dir)
    projection.bind_run("run-1")

    projection.load_persisted_manifest()

    step = state.snapshot(request_id)["source"]["caseSteps"][0]
    assert step["status"] == "failed"
    assert step["durationMs"] == 12
    assert step["failureCategory"] == "target_resolution_error"
    assert step["message"] == "Target was not found."


def test_evidence_projection_preserves_dynamic_runner_step_id(tmp_path: Path) -> None:
    state = ControlPlaneState()
    request_id = state.reserve(workspace_name="checkout", platform="windows", target_id="edge", mode="explore", source={"goal": "Go"})
    projection = EvidenceProjection(state, request_id, tmp_path / "runs")

    projection.project_run_event(
        RunEvent(
            run_id="run-1",
            task_id="task",
            type="tool_call_completed",
            title="Tool returned output",
            tool_name="click_on",
            payload={"runner_step_id": "agent-click-on-2", "status": "passed"},
        )
    )

    assert state.snapshot(request_id)["events"][0]["stepId"] == "agent-click-on-2"


def test_evidence_projection_selects_only_dynamic_rows_with_real_runner_step_ids(tmp_path: Path) -> None:
    state = ControlPlaneState()
    request_id = state.reserve(workspace_name="checkout", platform="windows", target_id="edge", mode="explore", source={"goal": "Go"})
    projection = EvidenceProjection(state, request_id, tmp_path / "runs")

    projection.project_run_event(RunEvent(run_id="run-1", task_id="task", type="tool_call_started", title="Tool call started", tool_name="click_on", tool_call_id="call-1"))
    projection.project_run_event(
        RunEvent(
            run_id="run-1",
            task_id="task",
            type="tool_call_completed",
            title="Tool returned output",
            tool_name="click_on",
            tool_call_id="call-1",
            payload={"runner_step_id": "agent-click-on-2", "status": "passed"},
        )
    )

    events = state.snapshot(request_id)["events"]
    assert "stepId" not in events[0]
    assert events[1]["stepId"] == "agent-click-on-2"

    projection.project_run_event(
        RunEvent(
            run_id="run-1",
            task_id="task",
            type="planning_update",
            title="Plan",
            payload={"step_id": "not-a-tool-step"},
        )
    )
    assert "stepId" not in state.snapshot(request_id)["events"][-1]


def test_evidence_projection_does_not_fabricate_status_for_generic_run_events(tmp_path: Path) -> None:
    state = ControlPlaneState()
    request_id = state.reserve(workspace_name="checkout", platform="windows", target_id="edge", mode="explore", source={"goal": "Go"})
    projection = EvidenceProjection(state, request_id, tmp_path / "runs")

    projection.project_run_event(
        RunEvent(
            run_id="run-1",
            task_id="task",
            type="tool_call_started",
            title="Tool call started",
            tool_name="read_knowledge_index",
            tool_call_id="call-1",
            tool_arguments={"query": "cats", "path": str(tmp_path / "secret" / "input.txt")},
            payload={"tool_origin": "agent_tool", "unsafe_path": str(tmp_path / "secret" / "file.txt")},
        )
    )
    projection.project_run_event(
        RunEvent(
            run_id="run-1",
            task_id="task",
            type="tool_call_completed",
            title="Tool returned output",
            tool_name="read_knowledge_index",
            payload={},
        )
    )

    events = state.snapshot(request_id)["events"]
    assert "status" not in events[0]
    assert events[0]["toolCallId"] == "call-1"
    assert events[0]["toolArguments"]["query"] == "cats"
    assert events[0]["payload"]["tool_origin"] == "agent_tool"
    assert str(tmp_path) not in json.dumps(events[0])
    assert events[1]["status"] == "completed"


def test_persisted_dynamic_events_hydrate_terminal_action_step_ids(tmp_path: Path) -> None:
    state = ControlPlaneState()
    request_id = state.reserve(workspace_name="checkout", platform="windows", target_id="edge", mode="explore", source={"goal": "Go"})
    runs_dir = tmp_path / "runs"
    run_dir = runs_dir / "run-1"
    run_dir.mkdir(parents=True)
    projection = EvidenceProjection(state, request_id, runs_dir)
    projection.bind_run("run-1")
    state.add_event(request_id, {"label": "click_on", "toolCallId": "call-1"})
    state.add_event(request_id, {"label": "click_on", "toolCallId": "call-1"})
    (run_dir / "events.jsonl").write_text(
        "\n".join(
            [
                json.dumps({"type": "tool_call_started", "sequence": 1, "tool_call_id": "call-1", "payload": {}}),
                json.dumps({"type": "tool_call_completed", "sequence": 2, "tool_call_id": "call-1", "payload": {"runner_step_id": "agent-click-on-2"}}),
            ]
        ),
        encoding="utf-8",
    )

    projection.load_persisted_step_ids()

    events = state.snapshot(request_id)["events"]
    assert events[0]["stepId"] == "agent-click-on-2"
    assert events[1]["stepId"] == "agent-click-on-2"
    state.finish(request_id, status="success", summary="done")
    assert len(state.snapshot(request_id, after_sequence=2)["events"]) == 2


def test_state_freezes_bound_run_directory(tmp_path: Path) -> None:
    state = ControlPlaneState()
    request_id = state.reserve(workspace_name="checkout", platform="windows", target_id="edge", mode="explore", source={"goal": "Go"})
    run_dir = (tmp_path / "runs" / "run-1").resolve()

    state.bind_run(request_id, "run-1", run_dir)

    assert state.run_directory(request_id) == run_dir


def test_terminal_step_artifacts_and_replay_frames_are_contained_and_ordered(tmp_path: Path) -> None:
    run_dir = tmp_path / "runs" / "run-1"
    artifacts_dir = run_dir / "artifacts"
    artifacts_dir.mkdir(parents=True)
    (artifacts_dir / "before.png").write_bytes(b"before")
    (artifacts_dir / "after.png").write_bytes(b"after")
    (artifacts_dir / "before.json").write_text('{"value":"before"}', encoding="utf-8")
    (artifacts_dir / "after.json").write_text('{"value":"after"}', encoding="utf-8")
    (run_dir / "evidence-manifest.json").write_text(
        json.dumps(
            {
                "artifacts": [
                    {"kind": "screenshot", "path": "artifacts/after.png", "step_id": "step-1", "phase": "finalize", "created_at": "2026-08-12T00:00:02Z"},
                    {"kind": "ui_snapshot", "path": "artifacts/before.json", "step_id": "step-1", "phase": "prepare", "created_at": "2026-08-12T00:00:01Z"},
                    {"kind": "screenshot", "path": "artifacts/before.png", "step_id": "step-1", "phase": "prepare", "created_at": "2026-08-12T00:00:01Z"},
                    {"kind": "ui_snapshot", "path": "artifacts/after.json", "step_id": "step-1", "phase": "finalize", "created_at": "2026-08-12T00:00:02Z"},
                    {"kind": "screenshot", "path": "../escape.png", "step_id": "step-1", "phase": "finalize"},
                ]
            }
        ),
        encoding="utf-8",
    )

    step = read_step_artifacts(run_dir, "step-1")
    replay = read_replay_frames(run_dir)

    assert [(item["kind"], item["phase"]) for item in step["artifacts"] if "error" not in item] == [
        ("screenshot", "before"),
        ("screenshot", "after"),
        ("ui_snapshot", "before"),
        ("ui_snapshot", "after"),
    ]
    assert base64.b64decode(step["artifacts"][0]["contentBase64"]) == b"before"
    assert [base64.b64decode(frame["contentBase64"]) for frame in replay["frames"]] == [b"before", b"after"]
    assert all("path" not in item for item in [*step["artifacts"], *replay["frames"]])


def test_step_and_replay_artifact_read_failures_are_item_local(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    run_dir = tmp_path / "run"
    artifacts = run_dir / "artifacts"
    artifacts.mkdir(parents=True)
    (artifacts / "bad.json").write_bytes(b"\xff\xfe")
    (run_dir / "evidence-manifest.json").write_text(
        json.dumps(
            {
                "artifacts": [
                    {"kind": "ui_snapshot", "path": "artifacts/bad.json", "step_id": "step-1", "phase": "prepare"},
                    {"kind": "screenshot", "path": "artifacts/missing.png", "step_id": "step-1", "phase": "prepare"},
                ]
            }
        ),
        encoding="utf-8",
    )

    step = read_step_artifacts(run_dir, "step-1")
    replay = read_replay_frames(run_dir)

    assert [(artifact["kind"], artifact["error"]) for artifact in step["artifacts"]] == [
        ("screenshot", "Screenshot is unreadable."),
        ("ui_snapshot", "UI snapshot is unreadable or is not UTF-8."),
    ]
    assert replay["available"] is False
    assert replay["frames"][0]["error"] == "Replay frame is unreadable."

    blocked = artifacts / "blocked.png"
    blocked.write_bytes(b"png")
    original_is_file = Path.is_file
    monkeypatch.setattr(Path, "is_file", lambda path, **kwargs: (_ for _ in ()).throw(PermissionError()) if path == blocked else original_is_file(path, **kwargs))
    (run_dir / "evidence-manifest.json").write_text(
        json.dumps({"artifacts": [{"kind": "screenshot", "path": "artifacts/blocked.png", "step_id": "step-2", "phase": "prepare"}]}),
        encoding="utf-8",
    )
    assert read_step_artifacts(run_dir, "step-2")["artifacts"][0]["error"] == "Screenshot is unreadable."
    monkeypatch.setattr(Path, "is_file", original_is_file)
    original_stat = Path.stat
    monkeypatch.setattr(Path, "stat", lambda path, **kwargs: (_ for _ in ()).throw(PermissionError()) if path == blocked else original_stat(path, **kwargs))
    assert read_step_artifacts(run_dir, "step-2")["artifacts"][0]["error"] == "Screenshot is unreadable."


def test_control_plane_replay_video_storage_metadata_and_ranges(tmp_path: Path) -> None:
    run_dir = tmp_path / "run-1"
    run_dir.mkdir()
    webm = b"\x1a\x45\xdf\xa3\x87\x42\x82\x84webm\x18\x53\x80\x67\x80"

    stored = store_replay_video(run_dir, "video/webm;codecs=vp8", base64.b64encode(webm).decode())
    metadata = replay_video_metadata(run_dir, "/api/control-plane/runs/request/replay-video/file")
    status, body, headers = read_replay_video(run_dir, "bytes=8-11")

    assert stored["available"] is True
    assert metadata["videoUrl"].endswith("/replay-video/file")
    assert (run_dir / "control-plane-replay" / "replay.webm").read_bytes() == webm
    assert (status, body) == (206, b"webm")
    assert headers == {"Content-Type": "video/webm", "Accept-Ranges": "bytes", "Content-Range": f"bytes 8-11/{len(webm)}"}
    with pytest.raises(ValueError, match="valid WebM"):
        store_replay_video(run_dir, "video/webm", base64.b64encode(b"not-webm").decode())
    with pytest.raises(ValueError, match="valid WebM"):
        store_replay_video(run_dir, "video/webm", base64.b64encode(b"\x1a\x45\xdf\xa3arbitrary-webm-bytes").decode())
    with pytest.raises(ValueError, match="valid WebM"):
        store_replay_video(run_dir, "video/webm", base64.b64encode(b"\x1a\x45\xdf\xa3\x89xx\x42\x82\x84webm\x18\x53\x80\x67\x80").decode())
    with pytest.raises(ValueError, match="valid WebM"):
        store_replay_video(run_dir, "video/webm", base64.b64encode(b"\x1a\x45\xdf\xa3\x87\x42\x82\x84webm\x18\x53\x80\x67").decode())
    with pytest.raises(ValueError, match="valid WebM"):
        store_replay_video(run_dir, "video/webm", base64.b64encode(b"\x1a\x45\xdf\xa3\x87\x42\x82\x84webm\x18\x53\x80\x67arbitrary").decode())
    with pytest.raises(ValueError, match="valid WebM"):
        store_replay_video(run_dir, "video/webm", base64.b64encode(webm + b"trailing").decode())
    unknown_segment = b"\x1a\x45\xdf\xa3\x87\x42\x82\x84webm\x18\x53\x80\x67\xff" + b"\x1f\x43\xb6\x75\x80"
    store_replay_video(run_dir, "video/webm", base64.b64encode(unknown_segment).decode())
    large_segment_payload = b"\x1f\x43\xb6\x75\x50\x00" + (b"x" * 4096)
    large_segment = b"\x1a\x45\xdf\xa3\x87\x42\x82\x84webm\x18\x53\x80\x67\x50\x06" + large_segment_payload
    store_replay_video(run_dir, "video/webm", base64.b64encode(large_segment).decode())
    assert replay_video_metadata(run_dir, "/video")["available"] is True
    video_path = run_dir / "control-plane-replay" / "replay.webm"
    video_path.write_bytes(b"invalid-existing-video")
    assert replay_video_metadata(run_dir, "/video")["available"] is False
    with pytest.raises(FileNotFoundError):
        read_replay_video(run_dir, None)


def test_server_terminal_evidence_and_replay_routes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(tmp_path, "windows")
    server = ControlPlaneServer(ControlPlaneServerOptions(static_path=tmp_path))
    monkeypatch.setattr("fsq_agent.adapters.control_plane._server.load_control_plane_settings", lambda *_args: settings)
    request_id = server.state.reserve(workspace_name="checkout", platform="windows", target_id="edge", mode="explore", source={"goal": "Go"})
    run_dir = settings.output.runs_dir / "run-1"
    artifact = run_dir / "artifacts" / "step.png"
    artifact.parent.mkdir(parents=True)
    artifact.write_bytes(b"png")
    (run_dir / "evidence-manifest.json").write_text(
        json.dumps({"artifacts": [{"kind": "screenshot", "path": "artifacts/step.png", "step_id": "step-1", "phase": "prepare", "created_at": "2026-08-12T00:00:01Z"}]}),
        encoding="utf-8",
    )
    server.state.bind_run(request_id, "run-1", run_dir.resolve())

    active_status, active_payload, _ = server.handle_get(f"/api/control-plane/runs/{request_id}/step-artifacts/step-1")
    server.state.finish(request_id, status="success", summary="done")
    step_status, step_payload, _ = server.handle_get(f"/api/control-plane/runs/{request_id}/step-artifacts/step-1")
    replay_status, replay_payload, _ = server.handle_get(f"/api/control-plane/runs/{request_id}/replay")
    upload_status, upload = server.handle_post(
        f"/api/control-plane/runs/{request_id}/replay-video",
        {"mimeType": "video/webm", "videoBase64": base64.b64encode(b"\x1a\x45\xdf\xa3\x87\x42\x82\x84webm\x18\x53\x80\x67\x80").decode()},
    )
    metadata_status, metadata, _ = server.handle_get(f"/api/control-plane/runs/{request_id}/replay-video")
    range_status, range_body, range_headers = server.handle_replay_video_file(request_id, "bytes=1-3")

    assert (active_status, active_payload["code"]) == (409, "run_not_terminal")
    assert step_status == replay_status == upload_status == metadata_status == 200
    assert step_payload["artifacts"][0]["phase"] == "before"
    assert base64.b64decode(replay_payload["frames"][0]["contentBase64"]) == b"png"
    assert upload["videoUrl"] == metadata["videoUrl"]
    assert (range_status, range_body, range_headers["Content-Range"]) == (206, b"\x45\xdf\xa3", "bytes 1-3/17")


def test_server_save_yaml_copies_terminal_explore_recording_to_cases(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(tmp_path, "web")
    server = ControlPlaneServer(ControlPlaneServerOptions(static_path=tmp_path))
    monkeypatch.setattr("fsq_agent.adapters.control_plane._server.load_control_plane_settings", lambda *_args: settings)
    request_id = server.state.reserve(workspace_name="checkout", platform="web", target_id="chrome", mode="explore", source={"goal": "Go"})
    run_dir = settings.output.runs_dir / "run-1"
    run_dir.mkdir(parents=True)
    (run_dir / "recorded.fsq.yaml").write_text("schemaVersion: fsq.ai-test/v1\nname: candidate\nplatform: web\n---\n- waitMs:\n    duration_ms: 1\n", encoding="utf-8")
    server.state.bind_cases_dir(request_id, settings.cases.dir.resolve())
    server.state.bind_run(request_id, "run-1", run_dir.resolve())

    active_status, active_payload = server.handle_post(f"/api/control-plane/runs/{request_id}/save-yaml", {"caseName": "checkout-flow"})
    server.state.finish(request_id, status="success", summary="done")
    status, payload = server.handle_post(f"/api/control-plane/runs/{request_id}/save-yaml", {"caseName": "checkout-flow"})

    assert (active_status, active_payload["code"]) == (409, "run_not_terminal")
    assert status == 200
    assert payload == {"savedPath": "checkout-flow.fsq.yaml", "message": "Saved YAML to cases/web/checkout-flow.fsq.yaml.", "outcome": "created", "draft": False}
    assert (settings.cases.dir / "checkout-flow.fsq.yaml").read_text(encoding="utf-8").startswith("schemaVersion: fsq.ai-test/v1")


def test_server_save_yaml_uses_frozen_cases_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    original_settings = _settings(tmp_path / "original", "web")
    changed_settings = _settings(tmp_path / "changed", "web")
    server = ControlPlaneServer(ControlPlaneServerOptions(static_path=tmp_path))
    monkeypatch.setattr("fsq_agent.adapters.control_plane._server.load_control_plane_settings", lambda *_args: changed_settings)
    request_id = server.state.reserve(workspace_name="checkout", platform="web", target_id="chrome", mode="explore", source={"goal": "Go"})
    run_dir = original_settings.output.runs_dir / "run-1"
    run_dir.mkdir(parents=True)
    (run_dir / "recorded.fsq.yaml").write_text("schemaVersion: fsq.ai-test/v1\nname: candidate\nplatform: web\n---\n- waitMs:\n    duration_ms: 1\n", encoding="utf-8")
    server.state.bind_cases_dir(request_id, original_settings.cases.dir.resolve())
    server.state.bind_run(request_id, "run-1", run_dir.resolve())
    server.state.finish(request_id, status="success", summary="done")

    status, payload = server.handle_post(f"/api/control-plane/runs/{request_id}/save-yaml", {"caseName": "frozen-flow"})

    assert status == 200
    assert payload["savedPath"] == "frozen-flow.fsq.yaml"
    assert (original_settings.cases.dir / "frozen-flow.fsq.yaml").is_file()
    assert not (changed_settings.cases.dir / "frozen-flow.fsq.yaml").exists()


@pytest.mark.parametrize("failure_kind", ["conflict", "recording", "case"])
def test_save_yaml_http_errors_preserve_existing_files(tmp_path: Path, monkeypatch, failure_kind: str) -> None:
    settings = _settings(tmp_path, "web")
    (tmp_path / "index.html").write_text("<!doctype html><title>Test</title>")
    server = ControlPlaneServer(ControlPlaneServerOptions(static_path=tmp_path, port=0))
    monkeypatch.setattr("fsq_agent.adapters.control_plane._server.load_control_plane_settings", lambda *_args: settings)
    request_id = server.state.reserve(workspace_name="checkout", platform="web", target_id="chrome", mode="explore", source={"goal": "Go"})
    run_dir = settings.output.runs_dir / "run-1"
    run_dir.mkdir()
    candidate = run_dir / "recorded.fsq.yaml"
    _case(candidate, platform="web", app_id=False)
    destination = settings.cases.dir / "occupied.fsq.yaml"
    _case(destination, platform="web", command="waitMs:\n    duration_ms: 2", app_id=False)
    if failure_kind == "recording":
        (run_dir / "recording.json").write_text('{"draft": "invalid"}')
    elif failure_kind == "case":
        candidate.write_text("invalid: [")
    before = {path: path.read_bytes() for path in [candidate, destination]}
    server.state.bind_cases_dir(request_id, settings.cases.dir.resolve())
    server.state.bind_run(request_id, "run-1", run_dir.resolve())
    server.state.finish(request_id, status="success", summary="done")
    server.start()
    try:
        with pytest.raises(HTTPError) as error:
            _post_json(f"{server.url}/api/control-plane/runs/{request_id}/save-yaml", {"caseName": "occupied"})
        payload = json.loads(error.value.read())
        assert error.value.code == (409 if failure_kind == "conflict" else 400)
        assert payload["code"] == ("case.publication_conflict" if failure_kind == "conflict" else "case.invalid")
        assert payload["action"]
        assert str(tmp_path) not in json.dumps(payload)
    finally:
        server.stop()
    assert {path: path.read_bytes() for path in before} == before


def test_server_save_yaml_rejects_strict_and_missing_recording(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(tmp_path)
    server = ControlPlaneServer(ControlPlaneServerOptions(static_path=tmp_path))
    monkeypatch.setattr("fsq_agent.adapters.control_plane._server.load_control_plane_settings", lambda *_args: settings)
    strict_request_id = server.state.reserve(workspace_name="checkout", platform="android", target_id="device", mode="strict", source={"casePath": "case.fsq.yaml"})
    strict_run_dir = settings.output.runs_dir / "strict-run"
    strict_run_dir.mkdir(parents=True)
    server.state.bind_cases_dir(strict_request_id, settings.cases.dir.resolve())
    server.state.bind_run(strict_request_id, "strict-run", strict_run_dir.resolve())
    server.state.finish(strict_request_id, status="success", summary="done")
    missing_request_id = server.state.reserve(workspace_name="checkout", platform="android", target_id="device", mode="explore", source={"goal": "Go"})
    missing_run_dir = settings.output.runs_dir / "missing-run"
    missing_run_dir.mkdir(parents=True)
    server.state.bind_cases_dir(missing_request_id, settings.cases.dir.resolve())
    server.state.bind_run(missing_request_id, "missing-run", missing_run_dir.resolve())
    server.state.finish(missing_request_id, status="success", summary="done")

    strict_status, strict_payload = server.handle_post(f"/api/control-plane/runs/{strict_request_id}/save-yaml", {"caseName": "strict-flow"})
    missing_status, missing_payload = server.handle_post(f"/api/control-plane/runs/{missing_request_id}/save-yaml", {"caseName": "missing-flow"})

    assert (strict_status, strict_payload["code"]) == (400, "invalid_request")
    assert (missing_status, missing_payload["code"]) == (404, "generated_yaml_unavailable")


def test_server_save_yaml_rejects_unsafe_case_names(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(tmp_path)
    server = ControlPlaneServer(ControlPlaneServerOptions(static_path=tmp_path))
    monkeypatch.setattr("fsq_agent.adapters.control_plane._server.load_control_plane_settings", lambda *_args: settings)
    request_id = server.state.reserve(workspace_name="checkout", platform="android", target_id="device", mode="explore", source={"goal": "Go"})
    run_dir = settings.output.runs_dir / "run-1"
    run_dir.mkdir(parents=True)
    (run_dir / "recorded.fsq.yaml").write_text("schemaVersion: fsq.ai-test/v1\nname: candidate\nplatform: web\n---\n- waitMs:\n    duration_ms: 1\n", encoding="utf-8")
    server.state.bind_cases_dir(request_id, settings.cases.dir.resolve())
    server.state.bind_run(request_id, "run-1", run_dir.resolve())
    server.state.finish(request_id, status="success", summary="done")

    for body in (
        {},
        {"caseName": "../escape"},
        {"caseName": "case.fsq.yaml"},
        {"caseName": ".hidden"},
        {"caseName": "bad*name"},
        {"caseName": "bad<name"},
        {"caseName": "bad>name"},
        {"caseName": 'bad"name'},
        {"caseName": "bad|name"},
        {"caseName": "bad\x7fname"},
        {"caseName": "bad\x80name"},
        {"caseName": "bad\x9fname"},
    ):
        status, payload = server.handle_post(f"/api/control-plane/runs/{request_id}/save-yaml", body)
        assert (status, payload["code"]) == (400, "invalid_request")


def test_safe_messages_redact_paths_credentials_and_configured_runtime_secrets(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CONTROL_PLANE_TEST_SECRET", "runtime-secret-value")
    settings = _settings(tmp_path)
    settings.runtime_secrets.set_values({"CONTROL_PLANE_TEST_SECRET": "runtime-secret-value"})
    message = safe_exception_message(
        ValueError(f"Failed at {tmp_path / 'private' / 'case.yaml'} with Bearer abc123 api_key=key-value and runtime-secret-value"),
        settings=settings,
    )

    assert "abc123" not in message
    assert "key-value" not in message
    assert "runtime-secret-value" not in message
    assert str(tmp_path) not in message
    assert "[local path]" in message
    assert safe_exception_message(RuntimeError("backend repr SecretObject(value='x')"), unexpected=True) == "An unexpected Control Plane error occurred."
    assert len(safe_text("x" * 2000, limit=64)) == 64


def test_case_diagnostics_use_safe_sanitizer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(tmp_path)
    case_path = settings.cases.dir / "unsafe.fsq.yaml"
    case_path.write_text("broken", encoding="utf-8")
    monkeypatch.setattr(
        "fsq_agent.adapters.control_plane._cases.FsqCaseLoader.load_case",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError(f"Invalid {tmp_path / 'private.yaml'} token=secret-token")),
    )

    diagnostic = discover_cases(settings)["cases"][0]["diagnostics"][0]

    assert str(tmp_path) not in diagnostic
    assert "secret-token" not in diagnostic


def test_sse_generator_yields_status_only_then_full_terminal_snapshot() -> None:
    server = ControlPlaneServer(ControlPlaneServerOptions(open_browser=False))
    request_id = server.state.reserve(workspace_name="checkout", platform="web", target_id="chrome", mode="explore", source={"goal": "Go"})
    server.state.add_event(request_id, {"label": "one"})
    stream = server.sse_snapshots(request_id, after_sequence=1, timeout=0)

    status_only = next(stream)
    server.state.finish(request_id, status="success", summary="done")
    terminal = next(stream)

    assert status_only["events"] == []
    assert status_only["status"] == "preparing"
    assert terminal["events"] == [{"label": "one", "sequence": 1}]
    assert terminal["terminal"] is True


def test_screenshot_endpoint_includes_frozen_platform_header(tmp_path: Path) -> None:
    server = ControlPlaneServer(ControlPlaneServerOptions(static_path=tmp_path))
    request_id = server.state.reserve(workspace_name="checkout", platform="windows", target_id="app", mode="explore", source={"goal": "Go"})
    screenshot = tmp_path / "screen.png"
    screenshot.write_bytes(b"png")
    server.state.set_artifact(request_id, "screenshot", {"path": screenshot, "mimeType": "image/png"})

    status, body, headers = server.handle_get(f"/api/control-plane/runs/{request_id}/screen")

    assert (status, body) == (200, b"png")
    assert headers["X-Evidence-Platform"] == "windows"


@pytest.mark.parametrize("platform", ["android", "web", "windows", "macos"])
def test_readiness_covers_all_supported_platforms(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, platform: str) -> None:
    settings = _settings(tmp_path / platform, platform)
    workspace_status = type(
        "WorkspaceStatus",
        (),
        {
            "name": "checkout",
            "root_path": settings.workspace.root_dir,
            "platforms": [type("PlatformStatus", (), {"platform": platform, "status": "available"})()],
        },
    )()
    monkeypatch.setattr("fsq_agent.adapters.control_plane._readiness.inspect_registered_workspace", lambda *_args: workspace_status)
    monkeypatch.setattr("fsq_agent.adapters.control_plane._readiness.load_control_plane_settings", lambda *_args: settings)
    monkeypatch.setattr("fsq_agent.adapters.control_plane._readiness.provider_readiness", lambda _settings: {"status": "ready", "message": "ready", "action": ""})
    monkeypatch.setattr("fsq_agent.adapters.control_plane._readiness.target_readiness", lambda _settings: (True, "ready", ""))
    monkeypatch.setattr("fsq_agent.adapters.control_plane._readiness.validate_strict_core_settings", lambda _settings: None)
    if platform in {"macos", "android"}:
        from fsq_agent.application import DoctorPlatformResult, DoctorResult

        ready = {"status": "ready", "message": "ready"}
        diagnosis = DoctorPlatformResult(
            platform=platform,
            status="ready",
            checks=dict.fromkeys(("configuration", "runtime", "target_configuration", "target_availability", "strict_core", "provider", "suggestion_analyzer", "dynamic_agent"), ready),
            commands=dict.fromkeys(("case_test", "case_test_suggest", "case_create"), ready),
        )
        monkeypatch.setattr(
            "fsq_agent.adapters.control_plane._readiness.diagnose_registered_platform",
            lambda _request: DoctorResult(status="ready", workspace={"name": "checkout", "root": tmp_path}, platforms=(diagnosis,), actions=()),
        )

    payload = readiness("checkout", platform)

    assert payload["workspaceName"] == "checkout"
    assert payload["platformId"] == platform
    assert {payload[key]["status"] for key in ("workspace", "platform", "provider", "target", "strict")} == {"ready"}


def test_readiness_and_case_discovery_do_not_require_cases_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(tmp_path, "web")
    settings.cases.dir.rmdir()
    workspace_status = type(
        "WorkspaceStatus",
        (),
        {
            "name": "checkout",
            "root_path": settings.workspace.root_dir,
            "platforms": [type("PlatformStatus", (), {"platform": "web", "status": "available"})()],
        },
    )()
    monkeypatch.setattr("fsq_agent.adapters.control_plane._readiness.inspect_registered_workspace", lambda *_args: workspace_status)
    monkeypatch.setattr("fsq_agent.adapters.control_plane._readiness.load_control_plane_settings", lambda *_args: settings)
    monkeypatch.setattr("fsq_agent.adapters.control_plane._readiness.provider_readiness", lambda _settings: {"status": "ready", "message": "ready", "action": ""})
    monkeypatch.setattr("fsq_agent.adapters.control_plane._readiness.target_readiness", lambda _settings: (True, "ready", ""))
    monkeypatch.setattr("fsq_agent.adapters.control_plane._readiness.validate_strict_core_settings", lambda _settings: None)

    payload = readiness("checkout", "web")

    assert payload["workspace"] == {"status": "ready", "message": "Workspace is ready.", "action": ""}
    assert payload["strict"]["status"] == "ready"
    assert discover_cases(settings) == {"platform": "web", "cases": [], "truncated": False}
    assert not settings.cases.dir.exists()


def test_ui_snapshot_limit_is_local_to_read(tmp_path: Path) -> None:
    path = tmp_path / "large.txt"
    path.write_bytes(b"x" * (UI_SNAPSHOT_LIMIT_BYTES + 1))
    with pytest.raises(OverflowError, match="512 KiB"):
        read_ui_snapshot({"path": path, "revision": 1})


def test_server_static_fallback_and_traversal_are_control_plane_scoped(tmp_path: Path) -> None:
    static = tmp_path / "static"
    entry = static / "control-plane" / "index.html"
    entry.parent.mkdir(parents=True)
    entry.write_text("control plane", encoding="utf-8")
    (static / "asset.js").write_text("asset", encoding="utf-8")
    server = ControlPlaneServer(ControlPlaneServerOptions(static_path=static, open_browser=False))

    assert server.static_response("/")[:2] == (200, b"control plane")
    assert server.static_response("/devices")[:2] == (200, b"control plane")
    assert server.static_response("/asset.js")[:2] == (200, b"asset")
    assert server.static_response("/../secret.txt")[0] == 404
    assert server.static_response("/missing.js")[0] == 404


def test_server_start_requires_frontend_build(tmp_path: Path) -> None:
    server = ControlPlaneServer(ControlPlaneServerOptions(static_path=tmp_path, open_browser=False))
    with pytest.raises(FileNotFoundError, match=r"npm ci.*npm run build"):
        server.start()


def test_server_bootstrap_and_errors_use_structured_shape(tmp_path: Path) -> None:
    server = ControlPlaneServer(ControlPlaneServerOptions(static_path=tmp_path))
    status, payload, _ = server.handle_get("/api/control-plane/bootstrap")
    invalid_status, error, _ = server.handle_get("/api/control-plane/readiness", {})

    assert status == 200
    assert payload["apiVersion"] == "1.0"
    assert payload["busy"] is False
    assert "workspace" not in payload
    assert [platform["id"] for platform in payload["platforms"]] == ["android", "web", "windows", "macos"]
    assert invalid_status == 400
    assert set(error) == {"code", "message", "action"}


def test_server_error_boundary_redacts_validation_and_hides_unexpected_repr(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    server = ControlPlaneServer(ControlPlaneServerOptions(static_path=tmp_path))
    monkeypatch.setattr(
        "fsq_agent.adapters.control_plane._server.load_control_plane_settings",
        lambda *_args: (_ for _ in ()).throw(ValueError(f"Invalid file {tmp_path / 'secret' / 'config.yaml'} api_key=credential-value")),
    )

    status, validation, _ = server.handle_get("/api/control-plane/targets", {"platform": ["web"]})
    monkeypatch.setattr(server.state, "bootstrap", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("BackendObject(secret='raw')")))
    unexpected_status, unexpected, _ = server.handle_get("/api/control-plane/bootstrap")

    assert status == 400
    assert "credential-value" not in validation["message"]
    assert str(tmp_path) not in validation["message"]
    assert unexpected_status == 500
    assert unexpected["message"] == "An unexpected Control Plane error occurred."
    assert "BackendObject" not in json.dumps(unexpected)


def test_server_run_start_busy_cancel_and_snapshot(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    server = ControlPlaneServer(ControlPlaneServerOptions(static_path=tmp_path))
    settings = _settings(tmp_path)
    captured: dict[str, object] = {}

    class Handle:
        def cancel(self) -> None:
            captured["cancelled"] = True

    monkeypatch.setattr(
        "fsq_agent.adapters.control_plane._server.load_control_plane_settings",
        lambda workspace_name, platform, user_config_root: captured.update(workspaceName=workspace_name, platform=platform) or settings,
    )
    monkeypatch.setattr("fsq_agent.adapters.control_plane._server.prepare_run", lambda **kwargs: kwargs)
    monkeypatch.setattr("fsq_agent.adapters.control_plane._server.start_execution", lambda prepared, state: Handle())

    missing_status, missing = server.handle_post(
        "/api/control-plane/runs",
        {"mode": "explore", "platform": "android", "targetId": "device", "goal": "Missing workspace"},
    )
    status, payload = server.handle_post(
        "/api/control-plane/runs",
        {"mode": "explore", "workspaceName": "checkout", "platform": "android", "targetId": "device", "goal": "Do it"},
    )
    busy_status, busy = server.handle_post(
        "/api/control-plane/runs",
        {"mode": "explore", "workspaceName": "checkout", "platform": "android", "targetId": "device", "goal": "Again"},
    )
    cancel_status, cancelled = server.handle_post(f"/api/control-plane/runs/{payload['requestId']}/cancel", {})
    snapshot_status, snapshot, _ = server.handle_get(f"/api/control-plane/runs/{payload['requestId']}")

    assert (missing_status, missing["code"]) == (400, "invalid_run")
    assert status == 202
    assert captured == {"workspaceName": "checkout", "platform": "android", "cancelled": True}
    assert busy_status == 409
    assert busy["code"] == "busy"
    assert cancel_status == 200
    assert cancelled["cancelRequested"] is True
    assert captured["cancelled"] is True
    assert snapshot_status == 200
    assert snapshot["workspaceName"] == "checkout"
    assert snapshot["source"] == {"goal": "Do it"}


def test_server_actual_http_dispatches_json(tmp_path: Path) -> None:
    static = tmp_path / "static"
    entry = static / "control-plane" / "index.html"
    entry.parent.mkdir(parents=True)
    entry.write_text("control plane", encoding="utf-8")
    server = ControlPlaneServer(ControlPlaneServerOptions(port=0, static_path=static, open_browser=False))
    server.start()
    try:
        with urlopen(f"{server.url}/api/control-plane/bootstrap", timeout=5) as response:  # noqa: S310 - loopback test server.
            payload = json.loads(response.read())
        with urlopen(f"{server.url}/", timeout=5) as response:  # noqa: S310 - loopback test server.
            body = response.read()
    finally:
        server.stop()

    assert payload["apiVersion"] == "1.0"
    assert body == b"control plane"


def test_server_actual_http_run_paths_sse_evidence_and_cancellation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    static = tmp_path / "static"
    entry = static / "control-plane" / "index.html"
    entry.parent.mkdir(parents=True)
    entry.write_text("control plane", encoding="utf-8")
    settings = _settings(tmp_path, "web")
    _case(settings.cases.dir / "strict.fsq.yaml", platform="web", app_id=False)
    monkeypatch.setattr("fsq_agent.adapters.control_plane._server.load_control_plane_settings", lambda *_args: settings)
    monkeypatch.setattr("fsq_agent.adapters.control_plane._execution.validate_target", lambda *_args: None)
    monkeypatch.setattr("fsq_agent.adapters.control_plane._execution.validate_runtime_settings", lambda *_args: None)
    monkeypatch.setattr("fsq_agent.adapters.control_plane._execution.validate_strict_core_settings", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("fsq_agent.adapters.control_plane._execution.require_provider", lambda *_args: None)
    monkeypatch.setattr("fsq_agent.adapters.control_plane._execution.RecordingService.record", lambda _self, **_kwargs: None)

    class FakeAgent:
        async def run_in_context(self, task, context, event_sink=None, **kwargs):
            run_id = context.run_id
            event_sink(RunEvent(run_id=run_id, task_id=task.id, type="run_started", title="Explore started"))
            if "cancel" in task.description.casefold():
                await asyncio.Event().wait()
            run_dir = settings.output.runs_dir / run_id
            assert (run_dir / "run.json").is_file()
            report = run_dir / "report.md"
            report.write_text("report", encoding="utf-8")
            return DynamicAgentOutcome(
                task=task,
                steps=[],
                verification=VerificationResult(status="success", summary="Explore complete"),
            )

    class FakeHarness:
        def __init__(self, store):
            self.store = store

        def get_context(self):
            return HarnessContext(platform="web", session_id="fake")

        def action_space(self):
            return {}

        def before_action(self, step, context):
            return None

        def invoke_action(self, step, context):
            return HarnessActionResult(status="passed", action_name=step.action_name)

        def after_action(self, step, context, action_result):
            return None

        def capture_artifact(self, kind, reason, context, step_id, phase):
            relative = Path("artifacts") / f"{step_id}-{phase}.{('png' if kind == 'screenshot' else 'json')}"
            path = self.store.run_dir / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            if kind == "screenshot":
                path.write_bytes(b"png-evidence")
            else:
                path.write_text('{"role":"window"}', encoding="utf-8")
            return HarnessArtifactRef(artifact_id=f"{step_id}-{kind}-{phase}", kind=kind, path=relative)

        def classify_error(self, error, phase, step):
            return "unknown"

    monkeypatch.setattr(
        "fsq_agent.adapters.control_plane._execution.FsqAgent.from_settings",
        lambda _settings, _runtime_factory, harness_factory=None: FakeAgent(),
    )
    monkeypatch.setattr(
        "fsq_agent.adapters.control_plane._execution.HarnessFactory.create_harness",
        lambda *_args, **kwargs: FakeHarness(kwargs["artifact_store"]),
    )
    server = ControlPlaneServer(ControlPlaneServerOptions(port=0, static_path=static, open_browser=False))
    server.start()
    try:
        explore = _post_json(f"{server.url}/api/control-plane/runs", {"mode": "explore", "workspaceName": "checkout", "platform": "web", "targetId": "chrome", "goal": "Verify available platform"})
        with _open_loopback(f"{server.url}/api/control-plane/runs/{explore['requestId']}/stream") as response:
            sse_lines: list[str] = []
            while not any('"terminal":true' in line for line in sse_lines):
                sse_lines.append(response.readline().decode())
            sse = "".join(sse_lines)
        assert "event: snapshot" in sse
        assert "Explore started" in sse
        assert '"terminal":true' in sse

        strict = _post_json(f"{server.url}/api/control-plane/runs", {"mode": "strict", "workspaceName": "checkout", "platform": "web", "targetId": "chrome", "casePath": "strict.fsq.yaml"})
        strict_snapshot = _wait_for_terminal(f"{server.url}/api/control-plane/runs/{strict['requestId']}")
        assert strict_snapshot["status"] == "success"
        assert strict_snapshot["screenshotRevision"] > 0
        assert strict_snapshot["uiSnapshotRevision"] > 0
        with _open_loopback(f"{server.url}/api/control-plane/runs/{strict['requestId']}/screen") as response:
            assert response.read() == b"png-evidence"
            assert response.headers["Content-Type"] == "image/png"
        with _open_loopback(f"{server.url}/api/control-plane/runs/{strict['requestId']}/ui-snapshot") as response:
            assert json.loads(response.read())["content"] == '{"role":"window"}'

        cancelling = _post_json(f"{server.url}/api/control-plane/runs", {"mode": "explore", "workspaceName": "checkout", "platform": "web", "targetId": "chrome", "goal": "Cancel this run"})
        cancelled = _post_json(f"{server.url}/api/control-plane/runs/{cancelling['requestId']}/cancel", {})
        assert cancelled["cancelRequested"] is True
        terminal = _wait_for_terminal(f"{server.url}/api/control-plane/runs/{cancelling['requestId']}")
        assert terminal["status"] == "cancelled"
    finally:
        server.stop()


def test_control_plane_cancellation_reaches_eligible_teardown(tmp_path, monkeypatch):
    monkeypatch.setattr("fsq_agent.adapters.control_plane._execution.require_android_preflight", lambda *_a, **_kw: None)
    monkeypatch.setattr("fsq_agent.adapters.control_plane._execution.validate_strict_core_settings", lambda *_a, **_kw: None)
    settings = _settings(tmp_path)
    _case(settings.cases.dir / "cleanup.fsq.yaml", command="launchApp: {}\n- waitMs: {duration_ms: 1}\n- killApp: {}")
    state = ControlPlaneState()
    request_id = state.reserve(workspace_name="checkout", platform="android", target_id="device", mode="strict", source={"casePath": "cleanup.fsq.yaml"})
    prepared = prepare_run(request_id=request_id, settings=settings, body={"mode": "strict", "workspaceName": "checkout", "platform": "android", "targetId": "device", "casePath": "cleanup.fsq.yaml"})
    called = []

    class Harness:
        def __init__(self, store):
            self.store = store

        def get_context(self):
            return HarnessContext(platform="android")

        def before_action(self, step, context):
            return None

        def invoke_action(self, step, context):
            called.append(step.action_name)
            if step.action_name == "launch_app":
                state.request_cancel(request_id)
            return HarnessActionResult(status="passed", action_name=step.action_name)

        def after_action(self, *args):
            return None

        def capture_artifact(self, kind, reason, context, step_id, phase):
            ref = self.store.write_bytes(kind=kind, step_id=step_id, phase=phase, name=reason, data=b"evidence")
            return HarnessArtifactRef(**ref.model_dump(exclude={"step_id", "step_execution_id", "phase"}))

        def classify_error(self, *args):
            return "unknown"

    monkeypatch.setattr("fsq_agent.adapters.control_plane._execution.HarnessFactory.create_harness", lambda *a, **kw: Harness(kw["artifact_store"]))
    _run_strict(prepared, state)
    assert called == ["launch_app", "kill_app"]
    assert state.snapshot(request_id)["status"] == "cancelled"
