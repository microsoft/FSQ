# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from fsq_agent.application import ApplicationError, add_workspace_platform, create_workspace, update_workspace_platform
from fsq_agent.config import (
    WorkspaceConfig,
    inspect_registered_workspace,
    list_workspace_registry,
    load_registered_workspace,
    workspace_revision,
)
from fsq_agent.models import ConfigurationError, WorkspaceStatus


@dataclass(frozen=True)
class WorkspaceAPIError(Exception):
    status: int
    code: str
    message: str
    action: str


def list_workspaces(user_config_root: Path | None) -> dict[str, list[dict[str, Any]]]:
    workspaces: list[dict[str, Any]] = []
    for entry in list_workspace_registry(user_config_root):
        try:
            status = inspect_registered_workspace(entry.name, user_config_root)
        except (ConfigurationError, OSError):
            workspaces.append(
                {
                    "name": entry.name,
                    "rootPath": str(entry.root_path),
                    "status": "unavailable",
                    "message": "Workspace configuration is unavailable.",
                    "action": "Restore or repair the registered workspace root.",
                    "platforms": [],
                }
            )
            continue
        projected = _status_projection(status)
        _add_macos_diagnostic_eligibility(projected, entry.name, user_config_root)
        workspaces.append(projected)
    return {"workspaces": workspaces}


def get_workspace(name: str, user_config_root: Path | None) -> dict[str, Any]:
    status = inspect_registered_workspace(name, user_config_root)
    result = _status_projection(status)
    result["platforms"] = [_platform_summary(name, platform.platform, user_config_root) if platform.status == "available" else _platform_status_projection(platform) for platform in status.platforms]
    _add_macos_diagnostic_eligibility(result, name, user_config_root)
    return result


def _add_macos_diagnostic_eligibility(result: dict[str, Any], name: str, user_config_root: Path | None) -> None:
    macos = next((item for item in result["platforms"] if item["platform"] == "macos" and item["status"] == "unavailable"), None)
    if macos is None:
        return
    try:
        inspected = inspect_registered_workspace(name, user_config_root, validate_target_paths=False)
        valid = any(item.platform == "macos" and item.status == "available" for item in inspected.platforms)
    except (ConfigurationError, OSError):
        return
    if valid:
        macos.update(diagnosticAvailable=True, repairAvailable=True)


def create_workspace_request(body: dict[str, Any], user_config_root: Path | None) -> dict[str, Any]:
    _require_exact_fields(body, {"name", "selectedPath", "platforms"})
    name = body["name"]
    selected_path = body["selectedPath"]
    platforms = body["platforms"]
    if not isinstance(name, str) or not isinstance(selected_path, str) or not isinstance(platforms, list) or not platforms:
        raise WorkspaceAPIError(
            400,
            "invalid_workspace",
            "name and selectedPath must be strings and platforms must be a non-empty array.",
            "Correct the workspace fields and retry.",
        )
    try:
        configs: list[dict[str, object]] = []
        for item in platforms:
            if not isinstance(item, dict):
                raise TypeError("platform item must be an object")  # noqa: TRY301
            _require_exact_fields(item, {"platform", "target", "env"})
            configs.append(
                {
                    "platform": item["platform"],
                    "target": _target_input(item["target"]),
                    "env": item["env"],
                }
            )
    except (ValidationError, TypeError, ValueError) as exc:
        raise WorkspaceAPIError(
            400,
            "invalid_workspace",
            "Workspace configuration is invalid.",
            "Correct the workspace fields and retry.",
        ) from exc
    create_workspace(
        selected_path=Path(selected_path).expanduser(),
        name=name,
        platforms=configs,
        user_config_root=user_config_root,
    )
    return get_workspace(name, user_config_root)


def get_workspace_platform(name: str, platform: str, user_config_root: Path | None) -> dict[str, Any]:
    config = _load_exact_workspace_platform(name, platform, user_config_root)
    return _detail(config)


def add_workspace_platform_request(name: str, body: dict[str, Any], user_config_root: Path | None) -> dict[str, Any]:
    _require_exact_fields(body, {"platform", "target", "env"})
    platform = body["platform"]
    target = body["target"]
    env = body["env"]
    if not isinstance(platform, str) or not isinstance(target, dict) or not _valid_env(env):
        raise WorkspaceAPIError(
            400,
            "invalid_workspace",
            "platform must be a string, target must be an object, and env must contain string names and values.",
            "Correct the workspace fields and retry.",
        )
    add_workspace_platform(
        name=name,
        root_path=_workspace_root(name, user_config_root),
        platform=platform,
        target=_target_input(target),
        env=env,
        user_config_root=user_config_root,
    )
    return {
        "workspace": get_workspace(name, user_config_root),
        "platform": get_workspace_platform(name, platform, user_config_root),
    }


def update_workspace_platform_request(
    name: str,
    platform: str,
    body: dict[str, Any],
    user_config_root: Path | None,
) -> dict[str, Any]:
    _require_exact_fields(body, {"target", "env", "expectedRevision"})
    expected_revision = body["expectedRevision"]
    if not isinstance(expected_revision, str) or not expected_revision:
        raise WorkspaceAPIError(
            400,
            "invalid_workspace",
            "expectedRevision must be a non-empty string.",
            "Reload the workspace and retry.",
        )
    target = body["target"]
    env = body["env"]
    if not isinstance(target, dict) or not _valid_env(env):
        raise WorkspaceAPIError(
            400,
            "invalid_workspace",
            "target must be an object and env must contain string names and values.",
            "Correct the workspace fields and retry.",
        )
    _load_exact_workspace_platform(name, platform, user_config_root)
    update_workspace_platform(
        name=name,
        root_path=_workspace_root(name, user_config_root),
        platform=platform,
        target=_target_input(target),
        env=env,
        expected_revision=expected_revision,
        user_config_root=user_config_root,
    )
    return {
        "workspace": get_workspace(name, user_config_root),
        "platform": get_workspace_platform(name, platform, user_config_root),
    }


def map_workspace_exception(exc: BaseException) -> WorkspaceAPIError:
    if isinstance(exc, WorkspaceAPIError):
        return exc
    if isinstance(exc, ApplicationError):
        if exc.category.value == "unavailable":
            return WorkspaceAPIError(503, "workspace_runtime_unavailable", exc.message, exc.action or "Install the platform runtime and retry.")
        return WorkspaceAPIError(400, "invalid_workspace", exc.message, exc.action or "Correct the workspace configuration and retry.")
    if isinstance(exc, ConfigurationError):
        if exc.context.get("error_code") == "workspace_conflict":
            return WorkspaceAPIError(
                409,
                "workspace_conflict",
                "Workspace configuration changed since it was loaded.",
                "Reload the latest workspace before saving again.",
            )
        message = str(exc).splitlines()[0]
        lowered = message.casefold()
        if "not registered" in lowered:
            return WorkspaceAPIError(404, "workspace_not_found", "Workspace is not registered.", "Refresh the workspace list.")
        if "already registered" in lowered or "already exists" in lowered or "path is already registered" in lowered:
            return WorkspaceAPIError(409, "workspace_conflict", message, "Choose a different workspace name or selected folder.")
        if "workspace root is unavailable" in lowered or "unable to read" in lowered or "identity does not match" in lowered or "registered workspace path" in lowered:
            return WorkspaceAPIError(409, "workspace_unavailable", "Workspace configuration is unavailable.", "Repair the registered workspace configuration.")
        return WorkspaceAPIError(400, "invalid_workspace", message, "Correct the workspace configuration and retry.")
    if isinstance(exc, OSError):
        return WorkspaceAPIError(503, "workspace_storage_unavailable", "Unable to access workspace storage.", "Check local file permissions and retry.")
    return WorkspaceAPIError(500, "workspace_internal_error", "An unexpected workspace error occurred.", "Retry or inspect the local server logs.")


def _load_exact_workspace_platform(name: str, platform: str, user_config_root: Path | None) -> WorkspaceConfig:
    if not isinstance(name, str) or not name:
        raise WorkspaceAPIError(404, "workspace_not_found", "Workspace is not registered.", "Refresh the workspace list.")
    normalized_name = name.casefold()
    entry = next(
        (candidate for candidate in list_workspace_registry(user_config_root) if candidate.name.casefold() == normalized_name),
        None,
    )
    if entry is None:
        raise WorkspaceAPIError(404, "workspace_not_found", "Workspace is not registered.", "Refresh the workspace list.")
    try:
        return (
            load_registered_workspace(entry.name, platform, user_config_root, allow_unavailable_target=True)
            if platform == "macos"
            else load_registered_workspace(entry.name, platform, user_config_root)
        )
    except ConfigurationError as exc:
        raise WorkspaceAPIError(
            409,
            "workspace_unavailable",
            "Workspace configuration is unavailable.",
            "Repair the registered workspace configuration.",
        ) from exc
    except OSError as exc:
        raise WorkspaceAPIError(
            503,
            "workspace_storage_unavailable",
            "Unable to access workspace storage.",
            "Check local file permissions and retry.",
        ) from exc


def _detail(config: WorkspaceConfig) -> dict[str, Any]:
    config_path = config.root_path.resolve() / ".fsq" / "config" / f"config.{config.platform}.yaml"
    return {
        "name": config.name,
        "rootPath": str(config.root_path.resolve()),
        "configPath": str(config_path),
        "platform": config.platform,
        "target": _target_projection(config),
        "env": dict(config.env),
        "revision": workspace_revision(config_path),
    }


def _platform_summary(name: str, platform: str, user_config_root: Path | None) -> dict[str, Any]:
    detail = get_workspace_platform(name, platform, user_config_root)
    return {
        "platform": platform,
        "configPath": detail["configPath"],
        "status": "available",
        "message": "Platform configuration is available.",
        "target": detail["target"],
        "env": [{"name": env_name, "configured": True} for env_name in sorted(detail["env"])],
        "revision": detail["revision"],
    }


def _status_projection(status: WorkspaceStatus) -> dict[str, Any]:
    result: dict[str, Any] = {
        "name": status.name,
        "rootPath": str(status.root_path),
        "status": status.status,
        "message": status.message,
        "platforms": [_platform_status_projection(platform) for platform in status.platforms],
    }
    if status.action is not None:
        result["action"] = status.action
    return result


def _platform_status_projection(status: Any) -> dict[str, Any]:
    result: dict[str, Any] = {
        "platform": status.platform,
        "configPath": str(status.config_path),
        "status": status.status,
        "message": status.message,
    }
    if status.action is not None:
        result["action"] = status.action
    return result


def _valid_env(value: object) -> bool:
    return isinstance(value, dict) and all(isinstance(key, str) and isinstance(item, str) for key, item in value.items())


def _target_projection(config: WorkspaceConfig) -> dict[str, Any]:
    target = config.target.model_dump(mode="json", exclude_none=True)
    aliases = {
        "app_id": "appId",
        "browser_executable_path": "browserExecutablePath",
        "browser_channel": "browserChannel",
        "app_path": "appPath",
        "window_title_re": "windowTitleRe",
        "launch_args": "launchArgs",
        "bundle_id": "bundleId",
    }
    return {aliases.get(key, key): value for key, value in target.items()}


def _target_input(target: object) -> object:
    if not isinstance(target, dict):
        return target
    aliases = {
        "appId": "app_id",
        "browserExecutablePath": "browser_executable_path",
        "browserChannel": "browser_channel",
        "appPath": "app_path",
        "windowTitleRe": "window_title_re",
        "launchArgs": "launch_args",
        "bundleId": "bundle_id",
    }
    return {aliases.get(key, key): value for key, value in target.items()}


def _workspace_root(name: str, user_config_root: Path | None) -> Path:
    return inspect_registered_workspace(name, user_config_root).root_path


def _require_exact_fields(body: dict[str, Any], expected: set[str]) -> None:
    if set(body) != expected:
        names = ", ".join(sorted(expected))
        raise WorkspaceAPIError(
            400,
            "invalid_request",
            f"Request body must contain exactly {names}.",
            "Correct the request body and retry.",
        )


__all__ = [
    "WorkspaceAPIError",
    "add_workspace_platform_request",
    "create_workspace_request",
    "get_workspace",
    "get_workspace_platform",
    "list_workspaces",
    "map_workspace_exception",
    "update_workspace_platform_request",
]
