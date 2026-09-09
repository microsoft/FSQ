# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path

from fsq_agent.application import RegisteredPlatformDoctorRequest, diagnose_platform_settings, diagnose_registered_platform
from fsq_agent.config import Settings, inspect_registered_workspace, load_registered_workspace, load_workspace_platform_settings, validate_strict_core_settings
from fsq_agent.providers import prepare_model_provider_session

from ._evidence import safe_exception_message
from ._targets import target_readiness


def load_control_plane_settings(workspace_name: str, platform: str, user_config_root: Path | None = None, *, diagnostic: bool = False) -> Settings:
    workspace = (
        load_registered_workspace(workspace_name, platform, user_config_root, allow_unavailable_target=diagnostic)
        if diagnostic
        else load_registered_workspace(workspace_name, platform, user_config_root)
    )
    return load_workspace_platform_settings(workspace.root_path, platform, user_config_root)


def readiness(workspace_name: str, platform: str, user_config_root: Path | None = None, *, target_id: str | None = None) -> dict[str, Any]:
    if platform in {"macos", "android"}:
        result = diagnose_registered_platform(RegisteredPlatformDoctorRequest(workspace_name=workspace_name, platform=platform, user_config_root=user_config_root, target_id=target_id))
        diagnosis = result.platforms[0]
        checks = diagnosis.checks
        target = next((item for item in (checks.runtime, checks.target_configuration, checks.target_availability) if item.status != "ready"), checks.target_availability)
        payload = _readiness_payload(
            result.workspace.name,
            platform,
            _record("ready", "Workspace identity is valid.", ""),
            _doctor_record(checks.configuration),
            _doctor_record(checks.provider),
            _doctor_record(target),
            _doctor_record(checks.strict_core),
        )
        payload.update(diagnosis_projection(diagnosis))
        return payload
    try:
        workspace_status = inspect_registered_workspace(workspace_name, user_config_root)
    except Exception as exc:  # noqa: BLE001
        workspace = _record("error", safe_exception_message(exc), "Select a registered workspace.")
        unavailable = _record("unavailable", "Workspace selection is unavailable.", "Select a registered workspace and platform.")
        return _readiness_payload(workspace_name, platform, workspace, unavailable, unavailable, unavailable, unavailable)

    workspace_name = workspace_status.name
    workspace_ready = workspace_status.root_path.is_dir() and not workspace_status.root_path.is_symlink()
    workspace = _record(
        "ready" if workspace_ready else "unavailable",
        "Workspace is ready." if workspace_ready else "Workspace is unavailable.",
        workspace_status.action if not workspace_ready else "",
    )
    selected_platform = next((item for item in workspace_status.platforms if item.platform == platform), None)
    if not workspace_ready or selected_platform is None or selected_platform.status != "available":
        if selected_platform is None:
            platform_status = _record("unavailable", "Platform configuration is not configured.", "Add the selected platform to this workspace.")
        else:
            platform_status = _record("unavailable", selected_platform.message, selected_platform.action)
        downstream = _record("unavailable", "Selected platform is unavailable.", "Select or repair a configured platform.")
        return _readiness_payload(workspace_name, platform, workspace, platform_status, downstream, downstream, downstream)

    try:
        settings = load_control_plane_settings(workspace_name, platform, user_config_root)
    except Exception as exc:  # noqa: BLE001
        platform_status = _record("error", safe_exception_message(exc), "Repair the selected platform configuration.")
        downstream = _record("unavailable", "Selected platform is unavailable.", "Repair the selected platform configuration.")
        return _readiness_payload(workspace_name, platform, workspace, platform_status, downstream, downstream, downstream)

    provider = provider_readiness(settings)
    platform_status = _record("ready", "Platform configuration is ready.", "")
    target_ready, target_message, target_action = target_readiness(settings)
    target = _record("ready" if target_ready else "unavailable", target_message, target_action)
    try:
        validate_strict_core_settings(settings)
    except Exception as exc:  # noqa: BLE001
        strict = _record("unavailable", safe_exception_message(exc, settings=settings), "Configure the selected platform for strict execution.")
    else:
        strict = _record("ready", "Provider-free strict execution is ready.", "")
    return _readiness_payload(workspace_name, platform, workspace, platform_status, provider, target, strict)


def _readiness_payload(
    workspace_name: str,
    platform_id: str,
    workspace: dict[str, str],
    platform: dict[str, str],
    provider: dict[str, str],
    target: dict[str, str],
    strict: dict[str, str],
) -> dict[str, Any]:
    return {
        "workspaceName": workspace_name,
        "platformId": platform_id,
        "workspace": workspace,
        "platform": platform,
        "provider": provider,
        "target": target,
        "strict": strict,
    }


def provider_readiness(settings: Settings) -> dict[str, str]:
    session = None
    try:
        session = prepare_model_provider_session(settings)
        return _record("ready", "Model provider is configured for non-interactive use.", "")
    except Exception as exc:  # noqa: BLE001
        return _record("unavailable", safe_exception_message(exc, settings=settings), "Configure a Provider in Control Plane Config.")
    finally:
        if session is not None:
            session.close_sync()


def require_provider(settings: Settings) -> None:
    session = prepare_model_provider_session(settings)
    session.close_sync()


def _record(status: str, message: str, action: str) -> dict[str, str]:
    return {"status": status, "message": message, "action": action}


def _doctor_record(detail) -> dict[str, str]:
    return _record(detail.status if detail.status != "not_applicable" else "unavailable", detail.message or "", detail.action or "")


def diagnosis_projection(diagnosis) -> dict[str, Any]:
    payload = {
        "prerequisites": [item.model_dump(mode="json") for item in diagnosis.prerequisites],
        "commands": {key: _doctor_record(value) for key, value in (("caseCreate", diagnosis.commands.case_create), ("caseTest", diagnosis.commands.case_test))},
        "checkedAt": datetime.now(UTC).isoformat(),
    }
    if diagnosis.platform == "android":
        payload["targetId"] = diagnosis.target_id
    return payload


class PlatformPreflightError(ValueError):
    def __init__(self, diagnosis, mode: str, requires_provider: bool = False):
        self.details = diagnosis_projection(diagnosis)
        verdict = diagnosis.commands.case_create if mode == "explore" else diagnosis.commands.case_test
        if verdict.status == "ready" and requires_provider:
            verdict = diagnosis.checks.provider
        label = "Android" if diagnosis.platform == "android" else "macOS"
        self.message = verdict.message or f"{label} environment is not ready."
        self.action = verdict.action or f"Recheck the {label} environment."
        super().__init__(self.message)


class MacOSPreflightError(PlatformPreflightError):
    pass


def require_macos_preflight(settings: Settings, mode: str, *, requires_provider: bool = False) -> None:
    if settings.harness.platform != "macos":
        return
    diagnosis = diagnose_platform_settings(settings)
    verdict = diagnosis.commands.case_create if mode == "explore" else diagnosis.commands.case_test
    if verdict.status != "ready" or (requires_provider and diagnosis.checks.provider.status != "ready"):
        raise MacOSPreflightError(diagnosis, mode, requires_provider)


class AndroidPreflightError(PlatformPreflightError):
    pass


def require_android_preflight(settings: Settings, mode: str, *, requires_provider: bool = False) -> None:
    if settings.harness.platform != "android":
        return
    diagnosis = diagnose_platform_settings(settings)
    verdict = diagnosis.commands.case_create if mode == "explore" else diagnosis.commands.case_test
    if verdict.status != "ready" or (requires_provider and diagnosis.checks.provider.status != "ready"):
        raise AndroidPreflightError(diagnosis, mode, requires_provider)


__all__ = ["load_control_plane_settings", "provider_readiness", "readiness", "require_provider"]
