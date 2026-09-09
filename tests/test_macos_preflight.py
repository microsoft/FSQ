# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from pathlib import Path

import pytest

from fsq_agent.application import ApplicationError, DoctorPlatformResult, DoctorPrerequisite, DoctorStatusDetail
from fsq_agent.config import Settings


def diagnosis(*, ready=True, provider=True):
    ok = DoctorStatusDetail(status="ready", message="Ready.")
    bad = DoctorStatusDetail(status="unavailable", message="Appium is offline.", action="Start Appium, then recheck.")
    return DoctorPlatformResult(
        platform="macos",
        status="ready" if ready and provider else "unavailable",
        checks={
            name: ok if name != "provider" or provider else bad
            for name in (
                "configuration",
                "runtime",
                "target_configuration",
                "target_availability",
                "strict_core",
                "provider",
                "suggestion_analyzer",
                "dynamic_agent",
            )
        },
        commands={"case_test": ok if ready else bad, "case_test_suggest": ok if ready and provider else bad, "case_create": ok if ready and provider else bad},
        prerequisites=[DoctorPrerequisite(identifier="appium_endpoint", status="ready" if ready else "unavailable", message="Endpoint status.", commands=[])],
    )


@pytest.mark.parametrize(
    ("mode", "requires_provider", "provider_ready", "allowed"), [("explore", False, False, False), ("strict", False, False, True), ("strict", True, False, False), ("explore", False, True, True)]
)
def test_start_preflight_respects_mode_and_provider(monkeypatch, mode, requires_provider, provider_ready, allowed):
    from fsq_agent.adapters.control_plane._readiness import MacOSPreflightError, require_macos_preflight

    calls = []
    settings = Settings(harness={"platform": "macos"})
    monkeypatch.setattr("fsq_agent.adapters.control_plane._readiness.diagnose_platform_settings", lambda value: calls.append(value) or diagnosis(provider=provider_ready))
    if allowed:
        require_macos_preflight(settings, mode, requires_provider=requires_provider)
    else:
        with pytest.raises(MacOSPreflightError):
            require_macos_preflight(settings, mode, requires_provider=requires_provider)
    assert calls == [settings]


def test_start_failure_does_not_execute_or_leave_busy(monkeypatch, tmp_path):
    from fsq_agent.adapters.control_plane._readiness import MacOSPreflightError
    from fsq_agent.control_plane import ControlPlaneServer

    server = ControlPlaneServer()
    settings = Settings(harness={"platform": "macos"})
    settings.cases.dir = tmp_path / "cases"
    monkeypatch.setattr(server, "_load_settings", lambda *_args: settings)
    monkeypatch.setattr(server, "_run_source", lambda *_args: {})

    def blocked(**_kwargs):
        raise MacOSPreflightError(diagnosis(ready=False), "explore")

    monkeypatch.setattr("fsq_agent.adapters.control_plane._server.prepare_run", blocked)
    monkeypatch.setattr("fsq_agent.adapters.control_plane._server.start_execution", lambda *_args: pytest.fail("execution must not begin"))
    code, payload = server._start_run({"workspaceName": "test", "platform": "macos", "mode": "explore", "targetId": "macos-app", "goal": "Open Edge"})
    assert code == 400
    assert payload["code"] == "macos_preflight_failed"
    assert payload["details"]["prerequisites"][0]["identifier"] == "appium_endpoint"
    assert not any(tmp_path.iterdir())
    assert server.state.bootstrap()["busy"] is False


def test_macos_load_for_repair_preserves_revision_and_validates_new_target(tmp_path: Path):
    from fsq_agent.config import create_workspace, load_registered_workspace, update_workspace_platform, workspace_revision
    from fsq_agent.models import ConfigurationError, WorkspacePlatformCreateInput

    selected = tmp_path / "workspace"
    selected.mkdir()
    user = tmp_path / "user"
    app = tmp_path / "Old.app"
    app.mkdir()
    create_workspace(selected_path=selected, name="test", platforms=[WorkspacePlatformCreateInput(platform="macos", target={"app_path": app}, env={})], user_config_root=user)
    app.rmdir()
    with pytest.raises(ConfigurationError):
        load_registered_workspace("test", "macos", user)
    current = load_registered_workspace("test", "macos", user, allow_unavailable_target=True)
    assert current.target.app_path == app
    path = selected / ".fsq/config/config.macos.yaml"
    revision = workspace_revision(path)
    with pytest.raises(ConfigurationError):
        update_workspace_platform(name="test", platform="macos", target={"app_path": tmp_path / "Missing.app"}, env={}, expected_revision=revision, user_config_root=user)
    assert workspace_revision(path) == revision
    replacement = tmp_path / "New.app"
    replacement.mkdir()
    updated = update_workspace_platform(name="test", platform="macos", target={"app_path": replacement}, env={}, expected_revision=revision, user_config_root=user)
    assert updated.target.app_path == replacement


def test_registered_diagnosis_and_ui_use_selected_user_root(tmp_path, monkeypatch):
    from fsq_agent.adapters.control_plane._readiness import readiness
    from fsq_agent.adapters.control_plane._workspaces import get_workspace_platform, list_workspaces
    from fsq_agent.application import RegisteredPlatformDoctorRequest, diagnose_registered_platform
    from fsq_agent.config import create_workspace
    from fsq_agent.models import WorkspacePlatformCreateInput

    user = tmp_path / "user"
    selected = tmp_path / "workspace"
    selected.mkdir()
    app = tmp_path / "Edge.app"
    app.mkdir()
    create_workspace(
        selected_path=selected, name="specific", platforms=[WorkspacePlatformCreateInput(platform="macos", target={"app_path": app}, env={"PRIVATE_TOKEN": "private-value"})], user_config_root=user
    )
    app.rmdir()

    def diagnosed(settings):
        assert settings.workspace.root_dir == selected
        return diagnosis(ready=False)

    monkeypatch.setattr("fsq_agent.application.doctor.diagnose_platform_settings", diagnosed)
    result = diagnose_registered_platform(RegisteredPlatformDoctorRequest(workspace_name="specific", platform="macos", user_config_root=user))
    assert len(result.platforms) == 1
    payload = readiness("specific", "macos", user)
    assert payload["commands"]["caseCreate"]["status"] == "unavailable"
    assert "private-value" not in str(payload)
    entry = list_workspaces(user)["workspaces"][0]
    assert entry["status"] == "unavailable"
    assert entry["platforms"][0]["diagnosticAvailable"] is True
    assert entry["platforms"][0]["repairAvailable"] is True
    assert get_workspace_platform("specific", "macos", user)["target"]["appPath"] == str(app)
    with pytest.raises(ApplicationError):
        diagnose_registered_platform(RegisteredPlatformDoctorRequest(workspace_name="specific", platform="macos", user_config_root=tmp_path / "other-user"))


@pytest.mark.parametrize("mode", ["explore", "strict"])
def test_real_preparation_rechecks_without_allocating_run(tmp_path, monkeypatch, mode):
    from fsq_agent.adapters.control_plane._execution import prepare_run
    from fsq_agent.adapters.control_plane._readiness import MacOSPreflightError

    settings = Settings(harness={"platform": "macos"})
    settings.workspace.config_path = tmp_path / "config.macos.yaml"
    settings.workspace.config_path.write_text("platform: macos")
    settings.cases.dir = tmp_path
    case = tmp_path / "test.fsq.yaml"
    case.write_text("schemaVersion: fsq.ai-test/v1\nname: test\nplatform: macos\n---\n- waitMs:\n    duration_ms: 1\n")
    calls = []
    monkeypatch.setattr("fsq_agent.adapters.control_plane._readiness.diagnose_platform_settings", lambda current: calls.append(current) or diagnosis(ready=False))
    body = {"mode": mode, "workspaceName": "specific", "platform": "macos", "targetId": "macos-app", **({"goal": "Open Edge"} if mode == "explore" else {"casePath": "test.fsq.yaml"})}
    with pytest.raises(MacOSPreflightError):
        prepare_run(request_id="test-request", settings=settings, body=body)
    assert len(calls) == 1
    assert calls[0] is not settings
    assert {p.name for p in tmp_path.iterdir()} == {"config.macos.yaml", "test.fsq.yaml"}
