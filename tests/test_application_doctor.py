# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from fsq_agent.application import ApplicationError, DoctorRequest, diagnose_workspace
from fsq_agent.config import load_workspace_platform_settings
from fsq_agent.models import PlatformPrerequisiteCheck, PlatformRuntimeCheck, WorkspacePlatformStatus, WorkspaceRegistryEntry, WorkspaceStatus


def _platform(platform: str, root: Path, status: str = "available") -> WorkspacePlatformStatus:
    return WorkspacePlatformStatus(
        platform=platform,
        config_path=root / ".fsq" / "config" / f"config.{platform}.yaml",
        status=status,
        message="available" if status == "available" else "damaged",
        action=None if status == "available" else f"Repair config.{platform}.yaml manually.",
    )


def _settings(platform: str):
    return SimpleNamespace(harness=SimpleNamespace(platform=platform))


def _base(monkeypatch: pytest.MonkeyPatch, root: Path, platforms: list[WorkspacePlatformStatus]) -> None:
    monkeypatch.setattr("fsq_agent.application.doctor.list_workspace_registry", lambda: [WorkspaceRegistryEntry(name="checkout", root_path=root)])
    monkeypatch.setattr(
        "fsq_agent.application.doctor.inspect_registered_workspace",
        lambda _name, **_kwargs: WorkspaceStatus(name="checkout", root_path=root, status="partial", message="checked", platforms=platforms),
    )
    monkeypatch.setattr("fsq_agent.application.doctor.load_workspace_platform_settings", lambda _root, platform: _settings(platform))
    monkeypatch.setattr("fsq_agent.application.doctor.validate_strict_core_settings", lambda _settings: None)
    monkeypatch.setattr("fsq_agent.application.doctor.CapabilityDefinitionFactory.platform_definitions", lambda *_args, **_kwargs: [])
    monkeypatch.setattr("fsq_agent.application.doctor.CommonPlatformTools.capability_definitions", list)
    monkeypatch.setattr("fsq_agent.application.doctor.check_dynamic_agent_readiness", lambda _settings: (True, "ready", ""))
    monkeypatch.setattr(
        "fsq_agent.application.doctor.PlatformRuntimeService.check",
        lambda _self, platform: PlatformRuntimeCheck(platform=platform, status="ready", ready=True, message="ready"),
    )
    monkeypatch.setattr("fsq_agent.application.doctor.PlatformRuntimeService.check_target_configuration", lambda *_args: (True, "ready", ""))
    monkeypatch.setattr("fsq_agent.application.doctor.PlatformRuntimeService.check_target_availability", lambda *_args: (True, "ready", ""))
    monkeypatch.setattr("fsq_agent.application.doctor.PlatformRuntimeService.check_prerequisites", lambda *_args: ())
    monkeypatch.setattr("fsq_agent.application.doctor.check_provider_readiness", lambda _settings: (True, "ready", ""))
    monkeypatch.setattr("fsq_agent.application.doctor.check_case_suggestion_readiness", lambda _settings: (True, "ready", ""))


def test_doctor_reports_configured_platforms_in_canonical_order(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _base(monkeypatch, tmp_path, [_platform("macos", tmp_path), _platform("android", tmp_path), _platform("web", tmp_path)])

    result = diagnose_workspace(DoctorRequest(current_directory=tmp_path))

    assert result.status == "ready"
    assert [item.platform for item in result.platforms] == ["android", "web", "macos"]
    assert all(item.commands.case_test.status == "ready" for item in result.platforms)


def test_provider_failure_keeps_case_test_ready_and_ai_commands_unavailable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _base(monkeypatch, tmp_path, [_platform("web", tmp_path)])
    monkeypatch.setattr(
        "fsq_agent.application.doctor.check_provider_readiness",
        lambda _settings: (False, "Provider unavailable.", "Configure Provider."),
    )

    result = diagnose_workspace(DoctorRequest(current_directory=tmp_path))

    platform = result.platforms[0]
    assert result.status == "partial"
    assert platform.commands.case_test.status == "ready"
    assert platform.commands.case_test_suggest.status == "unavailable"
    assert platform.commands.case_create.status == "unavailable"
    assert result.actions == ("Configure Provider.",)


def test_doctor_projects_macos_prerequisites_and_collects_actions(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _base(monkeypatch, tmp_path, [_platform("macos", tmp_path)])
    monkeypatch.setattr(
        "fsq_agent.application.doctor.PlatformRuntimeService.check_prerequisites",
        lambda *_args: (
            PlatformPrerequisiteCheck(identifier="xcode_installation", status="unavailable", message="Full Xcode is unavailable.", action="Install full Xcode from the Mac App Store."),
            PlatformPrerequisiteCheck(identifier="appium_cli", status="unavailable", message="Appium CLI is unavailable.", action="Install Appium CLI with npm."),
        ),
    )

    result = diagnose_workspace(DoctorRequest(current_directory=tmp_path))

    assert [item.identifier for item in result.platforms[0].prerequisites] == ["xcode_installation", "appium_cli"]
    assert result.actions[:2] == ("Install full Xcode from the Mac App Store.", "Install Appium CLI with npm.")


def test_doctor_reuses_one_prerequisite_snapshot_for_target_summary(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _base(monkeypatch, tmp_path, [_platform("macos", tmp_path)])
    calls = []
    facts = (PlatformPrerequisiteCheck(identifier="appium_endpoint", status="ready", message="ready"),)
    monkeypatch.setattr("fsq_agent.application.doctor.PlatformRuntimeService.check_prerequisites", lambda *_args: calls.append("prerequisites") or facts)
    monkeypatch.setattr(
        "fsq_agent.application.doctor.PlatformRuntimeService.check_target_availability",
        lambda _self, _settings, supplied: calls.append(supplied) or (True, "ready", ""),
    )

    diagnose_workspace(DoctorRequest(current_directory=tmp_path))

    assert calls == ["prerequisites", facts]


def test_doctor_isolates_prerequisite_diagnostic_exception(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _base(monkeypatch, tmp_path, [_platform("macos", tmp_path)])
    monkeypatch.setattr("fsq_agent.application.doctor.PlatformRuntimeService.check_prerequisites", lambda *_args: (_ for _ in ()).throw(RuntimeError("secret detail")))

    result = diagnose_workspace(DoctorRequest(current_directory=tmp_path))

    prerequisite = result.platforms[0].prerequisites[0]
    assert prerequisite.identifier == "prerequisite_diagnostics"
    assert prerequisite.status == "error"
    assert "secret detail" not in result.model_dump_json()


def test_doctor_loads_persisted_workspace_target_instead_of_preset_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    browser = tmp_path / "Google Chrome" / "chrome"
    browser.parent.mkdir()
    browser.write_text("", encoding="utf-8")
    browser.chmod(0o755)
    config_dir = tmp_path / ".fsq" / "config"
    config_dir.mkdir(parents=True)
    (config_dir / "config.web.yaml").write_text(
        "\n".join(
            (
                "version: 2",
                "name: checkout",
                f"root_path: {tmp_path}",
                "platform: web",
                "target:",
                "  browser_channel: chrome",
                f"  browser_executable_path: {browser}",
                "env: {}",
            )
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr("fsq_agent.application.doctor.list_workspace_registry", lambda: [WorkspaceRegistryEntry(name="checkout", root_path=tmp_path)])
    monkeypatch.setattr(
        "fsq_agent.application.doctor.inspect_registered_workspace",
        lambda _name, **_kwargs: WorkspaceStatus(name="checkout", root_path=tmp_path, status="available", message="checked", platforms=[_platform("web", tmp_path)]),
    )
    monkeypatch.setattr(
        "fsq_agent.application.doctor.load_workspace_platform_settings",
        lambda root, platform: load_workspace_platform_settings(root, platform, tmp_path / "user-config"),
    )
    monkeypatch.setattr("fsq_agent.application.doctor.PlatformRuntimeService.check", lambda _self, platform: PlatformRuntimeCheck(platform=platform, status="ready", ready=True, message="ready"))
    monkeypatch.setattr(
        "fsq_agent.application.doctor.PlatformRuntimeService.check_target_configuration", lambda _self, settings: (settings.harness.web.browser_executable_path == browser, "checked", "repair")
    )
    monkeypatch.setattr("fsq_agent.application.doctor.PlatformRuntimeService.check_target_availability", lambda *_args: (True, "ready", ""))
    monkeypatch.setattr("fsq_agent.application.doctor.validate_strict_core_settings", lambda _settings: None)
    monkeypatch.setattr("fsq_agent.application.doctor.CapabilityDefinitionFactory.platform_definitions", lambda *_args, **_kwargs: [])
    monkeypatch.setattr("fsq_agent.application.doctor.CommonPlatformTools.capability_definitions", list)
    monkeypatch.setattr("fsq_agent.application.doctor.check_provider_readiness", lambda _settings: (False, "unconfigured", "configure"))
    monkeypatch.setattr("fsq_agent.application.doctor.check_case_suggestion_readiness", lambda _settings: (False, "unconfigured", "configure"))
    monkeypatch.setattr("fsq_agent.application.doctor.check_dynamic_agent_readiness", lambda _settings: (False, "unconfigured", "configure"))

    result = diagnose_workspace(DoctorRequest(current_directory=tmp_path))

    assert result.platforms[0].checks.target_configuration.status == "ready"
    assert result.platforms[0].commands.case_test.status == "ready"


def test_damaged_platform_does_not_abort_other_platform(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _base(monkeypatch, tmp_path, [_platform("android", tmp_path, "unavailable"), _platform("web", tmp_path)])

    result = diagnose_workspace(DoctorRequest(current_directory=tmp_path))

    assert result.status == "partial"
    assert result.platforms[0].status == "unavailable"
    assert result.platforms[1].status == "ready"


def test_component_exception_is_safe_and_other_checks_continue(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _base(monkeypatch, tmp_path, [_platform("web", tmp_path)])

    def fail(_settings):
        raise RuntimeError("secret backend detail")

    monkeypatch.setattr("fsq_agent.application.doctor.check_provider_readiness", fail)
    result = diagnose_workspace(DoctorRequest(current_directory=tmp_path))
    payload = result.model_dump_json()

    assert result.platforms[0].checks.provider.status == "error"
    assert result.platforms[0].checks.strict_core.status == "ready"
    assert "secret backend detail" not in payload


def test_target_configuration_failure_is_error_not_external_unavailable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _base(monkeypatch, tmp_path, [_platform("web", tmp_path)])
    monkeypatch.setattr(
        "fsq_agent.application.doctor.PlatformRuntimeService.check_target_configuration",
        lambda *_args: (False, "Target configuration is invalid.", "Repair Target configuration."),
    )

    result = diagnose_workspace(DoctorRequest(current_directory=tmp_path))

    detail = result.platforms[0].checks.target_configuration
    assert detail.status == "error"
    assert detail.code == "doctor.target_configuration_invalid"


def test_doctor_rejects_unregistered_current_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("fsq_agent.application.doctor.list_workspace_registry", list)

    with pytest.raises(ApplicationError) as error:
        diagnose_workspace(DoctorRequest(current_directory=tmp_path))

    assert error.value.code.value == "workspace.not_initialized"


def test_doctor_normalizes_unreadable_registry_as_workspace_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_registry():
        raise ValueError("unsafe registry detail")

    monkeypatch.setattr("fsq_agent.application.doctor.list_workspace_registry", fail_registry)

    with pytest.raises(ApplicationError) as error:
        diagnose_workspace(DoctorRequest(current_directory=tmp_path))

    assert error.value.category.value == "workspace_configuration"
    assert "unsafe registry detail" not in error.value.message


def test_doctor_result_collections_are_immutable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _base(monkeypatch, tmp_path, [_platform("web", tmp_path)])

    result = diagnose_workspace(DoctorRequest(current_directory=tmp_path))

    assert isinstance(result.platforms, tuple)
    assert isinstance(result.actions, tuple)


@pytest.mark.parametrize("defect", ["missing_app", "wrong_name", "wrong_root", "malformed_yaml"])
def test_doctor_distinguishes_missing_target_from_untrusted_config(tmp_path: Path, monkeypatch, defect: str) -> None:
    from fsq_agent.config import inspect_registered_workspace
    from fsq_agent.environments import PlatformRuntimeService

    config_dir = tmp_path / ".fsq" / "config"
    config_dir.mkdir(parents=True)
    config_path = config_dir / "config.macos.yaml"
    document = {
        "version": 2,
        "name": "checkout",
        "root_path": str(tmp_path),
        "platform": "macos",
        "target": {"app_path": str(tmp_path / "Deleted.app"), "bundle_id": "com.example.app"},
        "env": {},
    }
    if defect == "wrong_name":
        document["name"] = "other-workspace"
    if defect == "wrong_root":
        document["root_path"] = str(tmp_path / "elsewhere")
    config_path.write_text("[broken:" if defect == "malformed_yaml" else yaml.safe_dump(document), encoding="utf-8")
    original = config_path.read_bytes()
    registry = [WorkspaceRegistryEntry(name="checkout", root_path=tmp_path)]
    monkeypatch.setattr("fsq_agent.application.doctor.list_workspace_registry", lambda: registry)
    monkeypatch.setattr("fsq_agent.config._workspace.list_workspace_registry", lambda *_args: registry)
    monkeypatch.setattr("fsq_agent.config._loader.refresh_provider_settings", lambda settings, *_args: settings)
    monkeypatch.setattr("fsq_agent.application.doctor.check_provider_readiness", lambda *_args: (False, "unconfigured", "configure"))
    monkeypatch.setattr("fsq_agent.application.doctor.check_case_suggestion_readiness", lambda *_args: (False, "unconfigured", "configure"))
    monkeypatch.setattr("fsq_agent.environments._service.platform.system", lambda: "Darwin")
    monkeypatch.setattr("fsq_agent.environments.providers._macos._active_developer_directory", lambda: None)
    monkeypatch.setattr("fsq_agent.environments.providers._macos._full_xcode_path", lambda: None)
    monkeypatch.setattr("fsq_agent.environments.providers._macos.shutil.which", lambda _name: None)
    monkeypatch.setattr("fsq_agent.environments.providers._macos._endpoint_available", lambda _url: False)
    monkeypatch.setattr(PlatformRuntimeService, "check", lambda _self, platform: PlatformRuntimeCheck(platform=platform, ready=True, status="ready", message="ready"))

    assert inspect_registered_workspace("checkout").platforms[0].status == "unavailable"
    result = diagnose_workspace(DoctorRequest(current_directory=tmp_path)).platforms[0]
    if defect == "missing_app":
        assert result.checks.configuration.status == "ready"
        assert len(result.prerequisites) == 7
        assert next(item for item in result.prerequisites if item.identifier == "application_path").status == "unavailable"
    else:
        assert result.checks.configuration.status == "error"
        assert result.prerequisites == ()
    assert result.commands.case_test.status == "unavailable"
    assert config_path.read_bytes() == original
