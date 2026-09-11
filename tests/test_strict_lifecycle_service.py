# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import asyncio
import json
import subprocess
from pathlib import Path

import pytest

from fsq_agent._capability_bootstrap import build_capability_registry
from fsq_agent.case_dsl import FsqCaseLoader, FsqExecutableStepAdapter
from fsq_agent.config import Settings
from fsq_agent.core import EvidenceRecorder
from fsq_agent.execution.lifecycle import _run_shell_command, collect_strict_lifecycle_cases, run_strict_lifecycle_case
from fsq_agent.models import (
    ConfigurationError,
    ExecutableStep,
    FailureCategory,
    HarnessActionResult,
    HarnessArtifactRef,
    HarnessContext,
    PostActionDelaySettings,
    StepPhase,
)


class LifecycleHarness:
    def __init__(self) -> None:
        self.actions: list[str] = []

    def get_context(self) -> HarnessContext:
        return HarnessContext(platform="android", session_id="session-1")

    def action_space(self) -> dict[str, object]:
        return {}

    def before_action(self, step: ExecutableStep, context: HarnessContext) -> None:
        return None

    def invoke_action(self, step: ExecutableStep, context: HarnessContext) -> HarnessActionResult:
        self.actions.append(step.action_name)
        return HarnessActionResult(status="passed", action_name=step.action_name)

    def after_action(
        self,
        step: ExecutableStep,
        context: HarnessContext,
        action_result: HarnessActionResult | None,
    ) -> None:
        return None

    def capture_artifact(
        self,
        kind: str,
        reason: str,
        context: HarnessContext,
        step_id: str,
        phase: StepPhase,
    ) -> HarnessArtifactRef:
        return HarnessArtifactRef(
            artifact_id=f"{step_id}-{phase}-{kind}",
            kind=kind,
            path=Path(f"artifacts/raw/{step_id}-{phase}-{reason}.{kind}"),
        )

    def classify_error(self, error: BaseException, phase: StepPhase, step: ExecutableStep) -> FailureCategory:
        return "unknown"


def test_shared_lifecycle_runs_config_case_child_main_and_after_in_one_manifest(tmp_path: Path) -> None:
    child_path = tmp_path / "child.fsq.yaml"
    child_path.write_text(
        "schemaVersion: fsq.ai-test/v1\nname: Child\nplatform: android\n---\n- tapOn:\n    target: Child target\n",
        encoding="utf-8",
    )
    root_path = tmp_path / "root.fsq.yaml"
    root_path.write_text(
        "schemaVersion: fsq.ai-test/v1\n"
        "name: Root\n"
        "platform: android\n"
        "appId: com.example\n"
        "onCaseStart:\n"
        "- runCase: child.fsq.yaml\n"
        "onCaseComplete:\n"
        "- runShell: echo case-after\n"
        "---\n"
        "- launchApp: {}\n",
        encoding="utf-8",
    )
    settings = Settings(
        cases={"dir": tmp_path},
        case_lifecycle={
            "onCaseStart": [{"runShell": "echo config-before"}],
            "onCaseComplete": [{"runShell": "echo config-after"}],
        },
    )
    case = FsqCaseLoader().load_case(root_path)
    registry = build_capability_registry(platform="android")
    harness = LifecycleHarness()
    run_dir = tmp_path / "runs" / "root"
    recorder = EvidenceRecorder(run_id="root", output_dir=run_dir)
    cancellation_checks: list[str] = []

    artifact = run_strict_lifecycle_case(
        case_path=root_path,
        case=case,
        settings=settings,
        harness=harness,
        output_dir=run_dir,
        run_id="root",
        registry=registry,
        registry_snapshot=registry.snapshot(),
        post_action_delay_seconds=PostActionDelaySettings(platform=0, common=0),
        recorder=recorder,
        resolve_steps=lambda steps, _case: steps,
        cancellation_check=lambda: cancellation_checks.append("check"),
    )

    manifest = json.loads(artifact.evidence_manifest_path.read_text(encoding="utf-8"))
    steps = manifest["steps"]
    assert harness.actions == ["tap_on", "launch_app"]
    assert [step["metadata"].get("command") for step in steps if step["metadata"].get("command")] == [
        "echo config-before",
        "echo case-after",
        "echo config-after",
    ]
    assert any(step["metadata"].get("hook_action_name") == "runCase" for step in steps)
    assert any(step.get("source_ref", {}).get("metadata", {}).get("lifecycle_phase") == "case" for step in steps)
    assert len(cancellation_checks) >= 5
    assert artifact.path == run_dir / "core-report.md"


def test_shared_lifecycle_uses_powershell_on_windows(monkeypatch) -> None:
    calls: list[tuple[object, dict[str, object]]] = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr("fsq_agent.execution.lifecycle.sys.platform", "win32")
    monkeypatch.setattr("fsq_agent.execution.lifecycle.subprocess.run", fake_run)

    command = "Remove-Item -LiteralPath 'C:\\temp\\test1' -Recurse -Force -Confirm:$false"
    result = _run_shell_command(command)

    assert result.returncode == 0
    assert calls[0][0] == ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command]


def test_shared_lifecycle_before_failure_skips_main_but_runs_after(tmp_path: Path, monkeypatch) -> None:
    case_path = tmp_path / "failure.fsq.yaml"
    case_path.write_text(
        "schemaVersion: fsq.ai-test/v1\nname: Failure\nplatform: android\nappId: com.example\nonCaseStart:\n- runShell: fail-before\nonCaseComplete:\n- runShell: pass-after\n---\n- launchApp: {}\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "fsq_agent.execution.lifecycle._run_shell_command",
        lambda command: subprocess.CompletedProcess(command, 3 if command == "fail-before" else 0, stdout="", stderr=""),
    )
    settings = Settings(cases={"dir": tmp_path})
    case = FsqCaseLoader().load_case(case_path)
    registry = build_capability_registry(platform="android")
    harness = LifecycleHarness()
    run_dir = tmp_path / "runs" / "failure"

    artifact = run_strict_lifecycle_case(
        case_path=case_path,
        case=case,
        settings=settings,
        harness=harness,
        output_dir=run_dir,
        run_id="failure",
        registry=registry,
        registry_snapshot=registry.snapshot(),
        recorder=EvidenceRecorder(run_id="failure", output_dir=run_dir),
        resolve_steps=lambda steps, _case: steps,
    )

    manifest = json.loads(artifact.evidence_manifest_path.read_text(encoding="utf-8"))
    assert harness.actions == []
    assert [step["metadata"]["command"] for step in manifest["steps"] if "command" in step["metadata"]] == ["fail-before", "pass-after"]
    assert [step["status"] for step in manifest["steps"]] == ["failed", "skipped", "passed"]
    assert manifest["steps"][1]["skip_reason"] == "lifecycle_start_failed"


def test_shared_lifecycle_propagates_cancellation_before_actions(tmp_path: Path, monkeypatch) -> None:
    case_path = tmp_path / "cancel.fsq.yaml"
    case_path.write_text(
        "schemaVersion: fsq.ai-test/v1\nname: Cancel\nplatform: android\nonCaseStart:\n- runShell: should-not-run\n---\n- launchApp: {}\n",
        encoding="utf-8",
    )
    shell_calls: list[str] = []
    monkeypatch.setattr(
        "fsq_agent.execution.lifecycle._run_shell_command",
        shell_calls.append,
    )
    settings = Settings(cases={"dir": tmp_path})
    case = FsqCaseLoader().load_case(case_path)
    registry = build_capability_registry(platform="android")

    with pytest.raises(asyncio.CancelledError, match="cancelled"):
        run_strict_lifecycle_case(
            case_path=case_path,
            case=case,
            settings=settings,
            harness=LifecycleHarness(),
            output_dir=tmp_path / "runs" / "cancel",
            run_id="cancel",
            registry=registry,
            registry_snapshot=registry.snapshot(),
            resolve_steps=lambda steps, _case: steps,
            cancellation_check=lambda: (_ for _ in ()).throw(asyncio.CancelledError("cancelled")),
        )

    assert shell_calls == []


def test_shared_lifecycle_preflight_rejects_recursive_run_case(tmp_path: Path) -> None:
    case_path = tmp_path / "recursive.fsq.yaml"
    case_path.write_text(
        "schemaVersion: fsq.ai-test/v1\nname: Recursive\nplatform: android\nonCaseStart:\n- runCase: recursive.fsq.yaml\n---\n- launchApp: {}\n",
        encoding="utf-8",
    )
    settings = Settings(cases={"dir": tmp_path})
    case = FsqCaseLoader().load_case(case_path)

    with pytest.raises(ConfigurationError, match="Recursive lifecycle hook runCase detected"):
        collect_strict_lifecycle_cases(case_path=case_path, case=case, settings=settings)


def test_shared_lifecycle_preserves_repeated_shell_order_and_continues_after_failures(tmp_path: Path, monkeypatch) -> None:
    case_path = tmp_path / "repeated.fsq.yaml"
    case_path.write_text(
        "schemaVersion: fsq.ai-test/v1\nname: Repeated\nplatform: android\nonCaseComplete:\n- runShell: first\n- runShell: second\n---\n- launchApp: {}\n",
        encoding="utf-8",
    )
    calls: list[str] = []

    def run_shell(command: str):
        calls.append(command)
        return subprocess.CompletedProcess(command, 2, stdout="", stderr="")

    monkeypatch.setattr("fsq_agent.execution.lifecycle._run_shell_command", run_shell)
    settings = Settings(cases={"dir": tmp_path})
    case = FsqCaseLoader().load_case(case_path)
    registry = build_capability_registry(platform="android")
    run_dir = tmp_path / "runs" / "repeated"

    run_strict_lifecycle_case(
        case_path=case_path,
        case=case,
        settings=settings,
        harness=LifecycleHarness(),
        output_dir=run_dir,
        run_id="repeated",
        registry=registry,
        registry_snapshot=registry.snapshot(),
        resolve_steps=lambda steps, _case: steps,
    )

    manifest = json.loads((run_dir / "evidence-manifest.json").read_text(encoding="utf-8"))
    assert calls == ["first", "second"]
    assert [step["status"] for step in manifest["steps"][-2:]] == ["failed", "failed"]


def test_shared_lifecycle_uses_system_shell_off_windows(monkeypatch) -> None:
    calls: list[tuple[object, dict[str, object]]] = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr("fsq_agent.execution.lifecycle.sys.platform", "linux")
    monkeypatch.setattr("fsq_agent.execution.lifecycle.subprocess.run", fake_run)

    _run_shell_command("echo ready")

    assert calls == [("echo ready", {"shell": True, "capture_output": True, "text": True, "check": False})]


def test_shared_lifecycle_records_shell_startup_failure(tmp_path: Path, monkeypatch) -> None:
    case_path = tmp_path / "startup-failure.fsq.yaml"
    case_path.write_text(
        "schemaVersion: fsq.ai-test/v1\nname: Startup Failure\nplatform: android\nonCaseStart:\n- runShell: broken\n---\n- launchApp: {}\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "fsq_agent.execution.lifecycle._run_shell_command",
        lambda command: (_ for _ in ()).throw(OSError("cannot start shell")),
    )
    settings = Settings(cases={"dir": tmp_path})
    case = FsqCaseLoader().load_case(case_path)
    registry = build_capability_registry(platform="android")
    run_dir = tmp_path / "runs" / "startup-failure"

    run_strict_lifecycle_case(
        case_path=case_path,
        case=case,
        settings=settings,
        harness=LifecycleHarness(),
        output_dir=run_dir,
        run_id="startup-failure",
        registry=registry,
        registry_snapshot=registry.snapshot(),
        resolve_steps=lambda steps, _case: steps,
    )

    manifest = json.loads((run_dir / "evidence-manifest.json").read_text(encoding="utf-8"))
    assert manifest["steps"][0]["status"] == "failed"
    assert manifest["steps"][0]["error_message"] == "Shell hook failed to start (OSError)."


def test_shared_lifecycle_uses_pre_resolved_steps_without_lazy_resolution(tmp_path: Path) -> None:
    case_path = tmp_path / "resolved.fsq.yaml"
    case_path.write_text(
        "schemaVersion: fsq.ai-test/v1\nname: Resolved\nplatform: android\n---\n- launchApp: {}\n",
        encoding="utf-8",
    )
    settings = Settings(cases={"dir": tmp_path})
    case = FsqCaseLoader().load_case(case_path)
    registry = build_capability_registry(platform="android")
    resolved_steps = FsqExecutableStepAdapter(registry_snapshot=registry.snapshot()).to_executable_steps(case)
    harness = LifecycleHarness()

    run_strict_lifecycle_case(
        case_path=case_path,
        case=case,
        settings=settings,
        harness=harness,
        output_dir=tmp_path / "runs" / "resolved",
        run_id="resolved",
        registry=registry,
        registry_snapshot=registry.snapshot(),
        resolve_steps=lambda steps, _case: (_ for _ in ()).throw(AssertionError("lazy resolver called")),
        resolved_steps_by_path={case_path.resolve(): resolved_steps},
    )

    assert harness.actions == ["launch_app"]


def test_shared_lifecycle_uses_preloaded_child_snapshot_and_encloses_child_events(tmp_path: Path) -> None:
    child_path = tmp_path / "snapshot-child.fsq.yaml"
    child_path.write_text(
        "schemaVersion: fsq.ai-test/v1\nname: Snapshot Child\nplatform: android\n---\n- tapOn:\n    target: Child\n",
        encoding="utf-8",
    )
    root_path = tmp_path / "snapshot-root.fsq.yaml"
    root_path.write_text(
        "schemaVersion: fsq.ai-test/v1\nname: Snapshot Root\nplatform: android\nonCaseStart:\n- runCase: snapshot-child.fsq.yaml\n---\n- launchApp: {}\n",
        encoding="utf-8",
    )
    settings = Settings(cases={"dir": tmp_path})
    root_case = FsqCaseLoader().load_case(root_path)
    child_case = FsqCaseLoader().load_case(child_path)
    registry = build_capability_registry(platform="android")
    adapter = FsqExecutableStepAdapter(registry_snapshot=registry.snapshot())
    resolved = {
        root_path.resolve(): adapter.to_executable_steps(root_case),
        child_path.resolve(): adapter.to_executable_steps(child_case),
    }
    recorder = EvidenceRecorder(run_id="snapshot-root", output_dir=tmp_path / "runs" / "snapshot-root")
    events: list[tuple[str, str | None]] = []
    original_record_event = recorder.record_event

    def record_event(event):
        events.append((event.event_type, event.step_id))
        original_record_event(event)

    recorder.record_event = record_event  # type: ignore[method-assign]
    child_path.unlink()
    harness = LifecycleHarness()

    run_strict_lifecycle_case(
        case_path=root_path,
        case=root_case,
        settings=settings,
        harness=harness,
        output_dir=tmp_path / "runs" / "snapshot-root",
        run_id="snapshot-root",
        registry=registry,
        registry_snapshot=registry.snapshot(),
        recorder=recorder,
        resolve_steps=lambda steps, _case: (_ for _ in ()).throw(AssertionError("lazy resolution")),
        resolved_steps_by_path=resolved,
        cases_by_path={root_path.resolve(): root_case, child_path.resolve(): child_case},
    )

    parent_id = next(step_id for event_type, step_id in events if event_type == "step_start" and step_id and "hook-run-case" in step_id)
    child_id = next(step_id for event_type, step_id in events if event_type == "step_start" and step_id and step_id.startswith("snapshot-child-case-step"))
    parent_start = events.index(("step_start", parent_id))
    parent_phase_start = events.index(("phase_start", parent_id))
    child_start = events.index(("step_start", child_id))
    parent_phase_finish = events.index(("phase_finish", parent_id))
    parent_finish = events.index(("step_finish", parent_id))
    assert parent_start < parent_phase_start < child_start < parent_phase_finish < parent_finish
    assert harness.actions == ["tap_on", "launch_app"]


@pytest.mark.parametrize("cancel_boundary", ["child", "main"])
def test_shared_lifecycle_cancels_at_child_and_main_boundaries(tmp_path: Path, cancel_boundary: str) -> None:
    child_path = tmp_path / "cancel-child.fsq.yaml"
    child_path.write_text(
        "schemaVersion: fsq.ai-test/v1\nname: Cancel Child\nplatform: android\n---\n- tapOn:\n    target: Child\n",
        encoding="utf-8",
    )
    root_path = tmp_path / "cancel-root.fsq.yaml"
    before = "onCaseStart:\n- runCase: cancel-child.fsq.yaml\n" if cancel_boundary == "child" else ""
    root_path.write_text(
        f"schemaVersion: fsq.ai-test/v1\nname: Cancel Root\nplatform: android\n{before}---\n- launchApp: {{}}\n",
        encoding="utf-8",
    )
    settings = Settings(cases={"dir": tmp_path})
    root_case = FsqCaseLoader().load_case(root_path)
    registry = build_capability_registry(platform="android")
    checks = 0
    harness = LifecycleHarness()

    def cancel() -> None:
        nonlocal checks
        checks += 1
        threshold = 3 if cancel_boundary == "child" else 2
        if checks >= threshold:
            raise asyncio.CancelledError(f"cancelled at {cancel_boundary}")

    with pytest.raises(asyncio.CancelledError, match=f"cancelled at {cancel_boundary}"):
        run_strict_lifecycle_case(
            case_path=root_path,
            case=root_case,
            settings=settings,
            harness=harness,
            output_dir=tmp_path / "runs" / f"cancel-{cancel_boundary}",
            run_id=f"cancel-{cancel_boundary}",
            registry=registry,
            registry_snapshot=registry.snapshot(),
            resolve_steps=lambda steps, _case: steps,
            cancellation_check=cancel,
        )

    assert harness.actions == []


def test_repeated_child_invocations_keep_unique_execution_and_root_mapping(tmp_path: Path) -> None:
    child = tmp_path / "child.fsq.yaml"
    child.write_text("schemaVersion: fsq.ai-test/v1\nname: Child\nplatform: android\n---\n- tapOn:\n    target: Child\n")
    root = tmp_path / "root.fsq.yaml"
    root.write_text("schemaVersion: fsq.ai-test/v1\nname: Root\nplatform: android\nonCaseStart:\n- runCase: child.fsq.yaml\n- runCase: child.fsq.yaml\n---\n- launchApp: {}\n")
    case = FsqCaseLoader().load_case(root)
    registry = build_capability_registry(platform="android")
    harness = LifecycleHarness()
    run = tmp_path / "run"
    artifact = run_strict_lifecycle_case(
        case_path=root,
        case=case,
        settings=Settings(cases={"dir": tmp_path}),
        harness=harness,
        output_dir=run,
        run_id="run",
        registry=registry,
        registry_snapshot=registry.snapshot(),
        resolve_steps=lambda steps, _: steps,
    )
    bundle = EvidenceRecorder.recover_bundle(artifact.evidence_manifest_path.parent)
    leaves = [step for step in bundle.steps if not step.metadata.get("hook_action_name")]
    assert harness.actions == ["tap_on", "tap_on", "launch_app"]
    assert leaves[0].source_step_id == leaves[1].source_step_id
    assert leaves[0].step_execution_id != leaves[1].step_execution_id
    assert leaves[0].invocation_path != leaves[1].invocation_path
    assert leaves[2].metadata["root_invocation"] is True
    assert leaves[0].metadata["root_invocation"] is False


def test_failed_start_accounts_for_later_nested_and_main_leaves(tmp_path: Path, monkeypatch) -> None:
    child = tmp_path / "child.fsq.yaml"
    child.write_text("schemaVersion: fsq.ai-test/v1\nname: Child\nplatform: android\n---\n- tapOn:\n    target: Child\n")
    root = tmp_path / "root.fsq.yaml"
    root.write_text("schemaVersion: fsq.ai-test/v1\nname: Root\nplatform: android\nonCaseStart:\n- runShell: fail\n- runCase: child.fsq.yaml\n---\n- launchApp: {}\n")
    monkeypatch.setattr("fsq_agent.execution.lifecycle._run_shell_command", lambda command: subprocess.CompletedProcess(command, 2))
    case = FsqCaseLoader().load_case(root)
    registry = build_capability_registry(platform="android")
    harness = LifecycleHarness()
    run = tmp_path / "run"
    run_strict_lifecycle_case(
        case_path=root,
        case=case,
        settings=Settings(cases={"dir": tmp_path}),
        harness=harness,
        output_dir=run,
        run_id="run",
        registry=registry,
        registry_snapshot=registry.snapshot(),
        resolve_steps=lambda steps, _: steps,
    )
    bundle = EvidenceRecorder.recover_bundle(run)
    assert harness.actions == []
    skipped = [step for step in bundle.steps if step.status == "skipped" and not step.metadata.get("structural")]
    assert {step.action_name for step in skipped} == {"tap_on", "launch_app"}
    assert all(step.step_execution_id is None and step.skip_reason for step in skipped)
    assert len([step for step in bundle.planned_steps if not step.metadata.get("structural")]) == 3


@pytest.mark.parametrize("checkpoint_fails", [False, True])
def test_cancellation_keeps_partial_leaf_and_runs_completion_hook(tmp_path: Path, monkeypatch, caplog, checkpoint_fails: bool) -> None:
    import asyncio

    case_path = tmp_path / "cancelled.fsq.yaml"
    case_path.write_text("schemaVersion: fsq.ai-test/v1\nname: Cancelled\nplatform: android\nonCaseComplete:\n- runShell: cleanup\n---\n- tapOn:\n    target: Child\n- launchApp: {}\n")
    cleanup = []
    monkeypatch.setattr("fsq_agent.execution.lifecycle._run_shell_command", lambda command: (cleanup.append(command), subprocess.CompletedProcess(command, 0))[1])
    primary = asyncio.CancelledError("primary cancellation")
    if checkpoint_fails:
        original_record = EvidenceRecorder.record_step_result

        def record(recorder, result):
            if result.status == "incomplete":
                raise OSError("private checkpoint error")
            return original_record(recorder, result)

        monkeypatch.setattr(EvidenceRecorder, "record_step_result", record)

    class CancellingHarness(LifecycleHarness):
        def invoke_action(self, step, context):
            raise primary

    registry = build_capability_registry(platform="android")
    run = tmp_path / "run"
    with pytest.raises(asyncio.CancelledError) as raised:
        run_strict_lifecycle_case(
            case_path=case_path,
            case=FsqCaseLoader().load_case(case_path),
            settings=Settings(cases={"dir": tmp_path}),
            harness=CancellingHarness(),
            output_dir=run,
            run_id="run",
            registry=registry,
            registry_snapshot=registry.snapshot(),
            resolve_steps=lambda steps, _: steps,
        )
    assert raised.value is primary
    bundle = EvidenceRecorder.recover_bundle(run)
    assert cleanup == ["cleanup"]
    if checkpoint_fails:
        assert "Interrupted scope persistence failed (OSError)" in caplog.text
        assert "private checkpoint error" not in caplog.text
        return
    leaf_statuses = {step.action_name: step.status for step in bundle.steps}
    assert leaf_statuses == {"tap_on": "cancelled", "launch_app": "incomplete", "runShell": "passed"}


def test_start_failure_preserves_trailing_body_teardown(tmp_path, monkeypatch):
    case_path = tmp_path / "teardown.fsq.yaml"
    case_path.write_text("schemaVersion: fsq.ai-test/v1\nname: Teardown\nplatform: android\nonCaseStart:\n- runShell: fail\n---\n- launchApp: {}\n- killApp: {}\n")
    monkeypatch.setattr("fsq_agent.execution.lifecycle._run_shell_command", lambda command: subprocess.CompletedProcess(command, 1))
    case = FsqCaseLoader().load_case(case_path)
    registry = build_capability_registry(platform="android")
    harness = LifecycleHarness()
    run = tmp_path / "run"
    run_strict_lifecycle_case(
        case_path=case_path,
        case=case,
        settings=Settings(cases={"dir": tmp_path}),
        harness=harness,
        output_dir=run,
        run_id="run",
        registry=registry,
        registry_snapshot=registry.snapshot(),
        resolve_steps=lambda steps, _: steps,
    )
    assert harness.actions == ["kill_app"]
    bundle = EvidenceRecorder.recover_bundle(run)
    assert next(step for step in bundle.steps if step.action_name == "launch_app").status == "skipped"
    assert next(step for step in bundle.steps if step.action_name == "kill_app").status == "passed"


def test_source_snapshots_match_preloaded_parsed_case_after_disk_mutation(tmp_path):
    case_path = tmp_path / "source.fsq.yaml"
    original = "schemaVersion: fsq.ai-test/v1\nname: Source\nplatform: android\n---\n- tapOn:\n    target: Original\n"
    case_path.write_text(original)
    case = FsqCaseLoader().load_case(case_path)
    registry = build_capability_registry(platform="android")
    steps = FsqExecutableStepAdapter(registry_snapshot=registry.snapshot()).to_executable_steps(case)
    case_path.write_text(original.replace("Original", "Changed"))
    run = tmp_path / "run"
    run_strict_lifecycle_case(
        case_path=case_path,
        case=case,
        settings=Settings(cases={"dir": tmp_path}),
        harness=LifecycleHarness(),
        output_dir=run,
        run_id="run",
        registry=registry,
        registry_snapshot=registry.snapshot(),
        resolve_steps=lambda steps, _: steps,
        resolved_steps_by_path={case_path.resolve(): steps},
        cases_by_path={case_path.resolve(): case},
    )
    from fsq_agent.execution import load_run_metadata

    metadata = load_run_metadata(run)
    assert (run / metadata.source.snapshot_path).read_text() == original
    bundle = EvidenceRecorder.recover_bundle(run)
    assert metadata.source.digest in bundle.steps[0].source_step_id


def test_planned_and_skipped_structural_hooks_are_retained_without_leaf_double_count(tmp_path, monkeypatch):
    from fsq_agent.execution import RunLifecycleService

    child = tmp_path / "child.fsq.yaml"
    child.write_text("schemaVersion: fsq.ai-test/v1\nname: Child\nplatform: android\n---\n- tapOn:\n    target: Child\n")
    root = tmp_path / "root.fsq.yaml"
    root.write_text("schemaVersion: fsq.ai-test/v1\nname: Root\nplatform: android\nonCaseStart:\n- runShell: fail\n- runCase: child.fsq.yaml\n---\n- launchApp: {}\n")
    monkeypatch.setattr("fsq_agent.execution.lifecycle._run_shell_command", lambda command: subprocess.CompletedProcess(command, 2))
    registry = build_capability_registry(platform="android")
    run = tmp_path / "run"
    run_strict_lifecycle_case(
        case_path=root,
        case=FsqCaseLoader().load_case(root),
        settings=Settings(cases={"dir": tmp_path}),
        harness=LifecycleHarness(),
        output_dir=run,
        run_id="run",
        registry=registry,
        registry_snapshot=registry.snapshot(),
        resolve_steps=lambda steps, _: steps,
    )
    bundle = EvidenceRecorder.recover_bundle(run)
    planned = [step for step in bundle.planned_steps if step.action_name == "runCase"]
    structural = [step for step in bundle.steps if step.action_name == "runCase"]
    assert len(planned) == len(structural) == 1
    assert structural[0].status == "skipped"
    assert structural[0].metadata["structural"] is True
    assert RunLifecycleService.load_result(run).counts.total == 3
