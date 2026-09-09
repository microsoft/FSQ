# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from __future__ import annotations

import importlib.util
from typing import Any

from fsq_agent.application import diagnose_platform_settings
from fsq_agent.config import Settings, validate_strict_core_settings
from fsq_agent.core import AndroidDeviceDiscovery

from ._evidence import safe_exception_message

_TARGET_LABELS = {"android": "Device", "web": "Browser", "windows": "Application", "macos": "Application"}
_BACKEND_MODULES = {"android": "uiautomator2", "web": "playwright", "windows": "pywinauto", "macos": "appium"}


def discover_targets(settings: Settings, *, timeout_seconds: float = 5.0) -> dict[str, Any]:
    platform = settings.harness.platform
    if platform == "android":
        targets = _discover_android(timeout_seconds)
    else:
        targets = [_configured_target(settings)]
    return {"platform": platform, "targetLabel": _TARGET_LABELS[platform], "targets": targets}


def validate_target(settings: Settings, target_id: str) -> dict[str, Any]:
    normalized = target_id.strip()
    if not normalized:
        raise ValueError("targetId is required.")
    module = _BACKEND_MODULES[settings.harness.platform]
    if importlib.util.find_spec(module) is None:
        raise ValueError(f"The {settings.harness.platform} backend package is not installed.")
    targets = discover_targets(settings)["targets"]
    for target in targets:
        if target["id"] == normalized and target["selectable"]:
            return target
    raise ValueError("The selected target is unavailable. Refresh targets and select an available target.")


def target_readiness(settings: Settings) -> tuple[bool, str, str]:
    module = _BACKEND_MODULES[settings.harness.platform]
    if importlib.util.find_spec(module) is None:
        return False, f"The {settings.harness.platform} backend package is not installed.", "Install the platform optional dependencies."
    try:
        validate_strict_core_settings(settings)
    except Exception as exc:  # noqa: BLE001 - readiness turns boundary failures into safe status.
        return False, safe_exception_message(exc, settings=settings), "Configure the selected platform target and retry."
    if settings.harness.platform == "android":
        targets = _discover_android(5.0)
        if not any(target["selectable"] for target in targets):
            return False, str(targets[0]["description"]), "Connect and authorize an Android device, then refresh."
    return True, "Target configuration is ready.", ""


def _discover_android(timeout_seconds: float) -> list[dict[str, Any]]:
    discovery = AndroidDeviceDiscovery().discover(timeout_seconds=timeout_seconds)
    if discovery.error_code is not None:
        return [_android_discovery_error(discovery.error_code)]

    targets: list[dict[str, Any]] = []
    for device in discovery.devices:
        metadata = _target_metadata(device.metadata)
        selectable = device.state == "device"
        label = str(metadata.get("model") or metadata.get("device") or device.serial)
        description = "Android device is online." if selectable else f"Android device is {device.state}."
        targets.append(
            {
                "id": device.serial,
                "label": label,
                "description": description,
                "status": "ready" if selectable else device.state,
                "selectable": selectable,
                "isDefault": False,
                "metadata": {"transport": "adb", **metadata},
            }
        )
    selectable_targets = [target for target in targets if target["selectable"]]
    if len(selectable_targets) == 1:
        selectable_targets[0]["isDefault"] = True
    return targets or [_unavailable("adb-empty", "No Android devices were discovered.", "unavailable")]


def _target_metadata(metadata: dict[str, str]) -> dict[str, str]:
    return {key: value for key, value in metadata.items() if key in {"model", "device", "product", "transport_id"}}


def _android_discovery_error(error_code: str) -> dict[str, Any]:
    errors = {
        "adb_server_unavailable": ("adb-server-unavailable", "ADB server is unavailable. Start it manually, then recheck.", "unavailable"),
        "adb_endpoint_invalid": ("adb-endpoint-invalid", "ADB endpoint is invalid or unsupported. Repair local ADB endpoint settings.", "error"),
        "adb_missing": ("adb-missing", "ADB is not installed or not on PATH.", "missing"),
        "adb_timeout": ("adb-timeout", "ADB target discovery timed out.", "timeout"),
        "adb_start_failed": ("adb-error", "ADB target discovery could not start.", "error"),
        "adb_failed": ("adb-error", "ADB target discovery failed.", "error"),
    }
    target_id, description, status = errors.get(error_code, errors["adb_failed"])
    return _unavailable(target_id, description, status)


def _configured_target(settings: Settings) -> dict[str, Any]:
    platform = settings.harness.platform
    ready, message, _ = target_readiness_without_discovery(settings)
    if platform == "web":
        target_id = settings.harness.web.channel
        label = {
            "chromium": "Chromium",
            "chrome": "Google Chrome",
            "chrome-beta": "Google Chrome Beta",
            "chrome-dev": "Google Chrome Dev",
            "chrome-canary": "Google Chrome Canary",
            "msedge": "Microsoft Edge",
            "msedge-beta": "Microsoft Edge Beta",
            "msedge-dev": "Microsoft Edge Dev",
            "msedge-canary": "Microsoft Edge Canary",
        }[target_id]
        metadata = {"channel": settings.harness.web.channel, "headless": settings.harness.web.headless}
    elif platform == "windows":
        target_id = "windows-app"
        app_path = settings.harness.windows.app_path
        label = app_path.stem if app_path else "Windows application"
        metadata = {"backend": settings.harness.windows.backend_kind}
    else:
        target_id = "macos-app"
        app_path = settings.harness.macos.app_path
        label = settings.harness.macos.bundle_id or (app_path.stem if app_path else "macOS application")
        metadata = {"backend": settings.harness.macos.backend}
    return {
        "id": target_id,
        "label": label,
        "description": message,
        "status": "ready" if ready else "unavailable",
        "selectable": ready,
        "isDefault": ready,
        "metadata": metadata,
    }


def target_readiness_without_discovery(settings: Settings) -> tuple[bool, str, str]:
    if settings.harness.platform == "macos":
        result = diagnose_platform_settings(settings)
        checks = result.checks
        failed = next((item for item in (checks.runtime, checks.target_configuration, checks.target_availability) if item.status != "ready"), None)
        return (False, failed.message or "macOS environment is not ready.", failed.action or "Recheck environment.") if failed else (True, "macOS target is ready.", "")
    module = _BACKEND_MODULES[settings.harness.platform]
    if importlib.util.find_spec(module) is None:
        return False, f"The {settings.harness.platform} backend package is not installed.", "Install the platform optional dependencies."
    try:
        validate_strict_core_settings(settings)
    except Exception as exc:  # noqa: BLE001
        return False, safe_exception_message(exc, settings=settings), "Configure the selected platform target and retry."
    return True, "Configured local target is ready.", ""


def _unavailable(target_id: str, description: str, status: str) -> dict[str, Any]:
    return {"id": target_id, "label": "Unavailable", "description": description, "status": status, "selectable": False, "isDefault": False, "metadata": {}}


__all__ = ["discover_targets", "target_readiness", "validate_target"]
