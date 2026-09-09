# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from __future__ import annotations

import json
import os
import platform
import plistlib
import shutil
import socket
import subprocess
import urllib.error
import urllib.request
from pathlib import Path
from typing import TYPE_CHECKING, TypeVar
from urllib.parse import urlparse

from fsq_agent.environments.providers._runtime import RuntimeProvider
from fsq_agent.models import PlatformPrerequisiteCheck

if TYPE_CHECKING:
    from collections.abc import Callable

_ProbeValue = TypeVar("_ProbeValue")
MACOS_RUNTIME_PROVIDER = RuntimeProvider(platform="macos", module="appium", required_host="Darwin")


def _prerequisite(identifier: str, ready: bool, message: str, action: str | None = None) -> PlatformPrerequisiteCheck:
    return PlatformPrerequisiteCheck(identifier=identifier, status="ready" if ready else "unavailable", message=message, action=None if ready else action)


def _macos_prerequisites(settings) -> tuple[PlatformPrerequisiteCheck, ...]:
    if platform.system() != "Darwin":
        return tuple(
            PlatformPrerequisiteCheck(identifier=name, status="not_applicable", message="macOS prerequisite checks require a macOS host.", action="Run macOS platform tests on a macOS host.")
            for name in ("xcode_installation", "xcode_developer_directory", "appium_cli", "appium_mac2_driver", "appium_endpoint", "application_path", "bundle_identifier")
        )
    errors: dict[str, PlatformPrerequisiteCheck] = {}

    def probe(identifier: str, operation: Callable[[], _ProbeValue]) -> _ProbeValue | None:
        try:
            return operation()
        except Exception:  # noqa: BLE001 - isolate each host probe without exposing its exception.
            errors[identifier] = PlatformPrerequisiteCheck(
                identifier=identifier,
                status="error",
                message=f"The {identifier.replace('_', ' ')} check could not be completed safely.",
                action="Inspect this prerequisite using the macOS platform preparation guide.",
            )
            return None

    developer = probe("xcode_developer_directory", _active_developer_directory)
    developer_ready = bool(developer and probe("xcode_developer_directory", lambda: _developer_directory_is_full_xcode(developer)))
    xcode = developer.parent.parent if developer is not None and developer_ready else probe("xcode_installation", _full_xcode_path)
    appium = probe("appium_cli", lambda: shutil.which("appium"))
    app_path = Path(settings.app_path) if settings.app_path is not None else None
    bundle_id = (settings.bundle_id or "").strip()
    app_ready = bool(app_path and probe("application_path", lambda: _macos_application_path_available(app_path)))
    bundle_ready = bool(bundle_id and probe("bundle_identifier", lambda: _macos_bundle_matches_target(bundle_id, app_path)))
    checks = [
        _prerequisite(
            "xcode_installation",
            xcode is not None,
            "Full Xcode is installed." if xcode else "Full Xcode is unavailable; Command Line Tools alone are insufficient for Appium Mac2.",
            "Install full Xcode from the Mac App Store.",
        ),
        _prerequisite(
            "xcode_developer_directory",
            developer_ready,
            "The active developer directory uses full Xcode." if developer_ready else "The active developer directory uses Command Line Tools or is unavailable.",
            "Run 'sudo xcode-select --switch /Applications/Xcode.app/Contents/Developer' after installing Xcode.",
        ),
        _prerequisite("appium_cli", appium is not None, "Appium CLI is available." if appium else "Appium CLI is unavailable.", "Install Appium CLI with 'npm install -g appium'."),
    ]
    if appium is None:
        checks.append(
            PlatformPrerequisiteCheck(
                identifier="appium_mac2_driver",
                status="not_applicable",
                message="Appium Mac2 driver check is blocked by the missing Appium CLI.",
                action="Install Appium CLI, then run 'appium driver install mac2'.",
            )
        )
    else:
        mac2_ready = bool(probe("appium_mac2_driver", lambda: _appium_mac2_driver_installed(appium)))
        checks.append(
            _prerequisite(
                "appium_mac2_driver",
                mac2_ready,
                "Appium Mac2 driver is installed." if mac2_ready else "Appium Mac2 driver is unavailable.",
                "Run 'appium driver install mac2', then 'appium driver doctor mac2'.",
            )
        )
    endpoint_ready = bool(probe("appium_endpoint", lambda: _endpoint_available(settings.appium_server_url)))
    checks.append(
        _prerequisite(
            "appium_endpoint",
            endpoint_ready,
            "The configured Appium endpoint is reachable." if endpoint_ready else "The configured Appium endpoint is unavailable.",
            "Start Appium on the configured host and port.",
        )
    )
    checks.append(
        _prerequisite(
            "application_path",
            app_ready,
            "The configured macOS application path is available." if app_ready else "The configured macOS application path is unavailable or is not an application bundle or executable.",
            "Configure an existing .app bundle or executable path.",
        )
        if app_path is not None
        else PlatformPrerequisiteCheck(identifier="application_path", status="not_applicable", message="No macOS application path is configured; bundle identifier discovery is used.")
    )
    checks.append(
        _prerequisite(
            "bundle_identifier",
            bundle_ready,
            "The configured bundle identifier resolves to the configured application."
            if bundle_ready
            else "The configured bundle identifier is unavailable or does not match the configured application.",
            "Configure the application's exact CFBundleIdentifier.",
        )
        if bundle_id
        else PlatformPrerequisiteCheck(identifier="bundle_identifier", status="not_applicable", message="No bundle identifier is configured; application path identity is used.")
    )
    commands = {
        "appium_cli": ("npm install -g appium",),
        "appium_mac2_driver": ("appium driver install mac2", "appium driver doctor mac2"),
    }
    if xcode == Path("/Applications/Xcode.app"):
        commands["xcode_developer_directory"] = ("sudo xcode-select --switch /Applications/Xcode.app/Contents/Developer",)
    elif not developer_ready and xcode is not None:
        checks[1] = checks[1].model_copy(update={"action": "Use xcode-select --switch with the Contents/Developer directory of your installed Xcode."})
    if (settings.appium_server_url or "").rstrip("/") == "http://127.0.0.1:4723":
        commands["appium_endpoint"] = ("appium --address 127.0.0.1 --port 4723",)
    return tuple(errors.get(check.identifier, check.model_copy(update={"commands": commands.get(check.identifier, ()) if check.status != "ready" else ()})) for check in checks)


def _macos_application_path_available(path: Path) -> bool:
    return (path.is_dir() and path.suffix.casefold() == ".app") or (path.is_file() and os.access(path, os.X_OK))


def _macos_bundle_matches_target(bundle_id: str, app_path: Path | None) -> bool:
    if app_path is None:
        return _macos_bundle_is_installed(bundle_id)
    if app_path.is_file():
        # Executables inside app bundles use that bundle's identity, never another installed app.
        app_path = next((parent for parent in app_path.parents if parent.suffix.casefold() == ".app"), app_path)
    return _macos_bundle_for_app(app_path) == bundle_id


def _full_xcode_path() -> Path | None:
    candidates = [Path("/Applications/Xcode.app"), *sorted(Path("/Applications").glob("Xcode*.app")), *sorted((Path.home() / "Applications").glob("Xcode*.app"))]
    return next((path for path in candidates if (path / "Contents" / "Developer" / "usr" / "bin" / "xcodebuild").is_file()), None)


def _developer_directory_is_full_xcode(path: Path) -> bool:
    return path.name == "Developer" and path.parent.name == "Contents" and path.parent.parent.suffix.casefold() == ".app" and (path / "usr" / "bin" / "xcodebuild").is_file()


def _active_developer_directory(timeout_seconds: float = 5.0) -> Path | None:
    executable = shutil.which("xcode-select") or "/usr/bin/xcode-select"
    try:
        completed = subprocess.run(  # noqa: S603 - resolved system tool with one fixed read-only argument.
            [executable, "-p"], capture_output=True, text=True, timeout=timeout_seconds, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    value = completed.stdout.strip()
    return Path(value) if completed.returncode == 0 and value else None


def _appium_mac2_driver_installed(appium_path: str, timeout_seconds: float = 10.0) -> bool:
    try:
        completed = subprocess.run(  # noqa: S603 - resolved Appium executable with fixed read-only discovery arguments.
            [appium_path, "driver", "list", "--installed", "--json"], capture_output=True, text=True, timeout=timeout_seconds, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    if completed.returncode != 0:
        return False
    try:
        payload = json.loads(completed.stdout)
    except (TypeError, ValueError):
        return False
    return isinstance(payload, dict) and "mac2" in payload


def _macos_bundle_for_app(app_path: Path) -> str | None:
    try:
        with (app_path / "Contents" / "Info.plist").open("rb") as stream:
            payload = plistlib.load(stream)
    except (OSError, ValueError, TypeError, plistlib.InvalidFileException):
        return None
    value = payload.get("CFBundleIdentifier") if isinstance(payload, dict) else None
    return value.strip() if isinstance(value, str) and value.strip() else None


def _macos_bundle_is_installed(bundle_id: str, timeout_seconds: float = 5.0) -> bool:
    metadata_query = shutil.which("mdfind")
    if platform.system() != "Darwin" or metadata_query is None:
        return False
    try:
        completed = subprocess.run(  # noqa: S603 - resolved system metadata tool with a read-only exact bundle-id query.
            [metadata_query, f"kMDItemCFBundleIdentifier == '{bundle_id}'"],
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return completed.returncode == 0 and any(Path(line).exists() for line in completed.stdout.splitlines() if line.strip())


class _NoStatusRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _endpoint_available(url: str, timeout_seconds: float = 1.0) -> bool:
    parsed = urlparse(url)
    if parsed.hostname is None or parsed.port is None:
        return False
    try:
        with socket.create_connection((parsed.hostname, parsed.port), timeout=timeout_seconds):
            pass
    except OSError:
        return False
    status_url = url.rstrip("/") + "/status"
    try:
        opener = urllib.request.build_opener(_NoStatusRedirect())
        with opener.open(status_url, timeout=timeout_seconds) as response:
            if response.status != 200:
                return False
            payload = json.loads(response.read(65537))
    except (OSError, ValueError, urllib.error.URLError):
        return False
    if not isinstance(payload, dict) or not isinstance(payload.get("value"), dict):
        return False
    value = payload["value"]
    return value.get("ready") is True or ("ready" not in value and isinstance(value.get("build"), dict))
