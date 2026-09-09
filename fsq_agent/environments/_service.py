# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from __future__ import annotations

import platform
from pathlib import Path
from typing import TYPE_CHECKING, Literal
from urllib.parse import urlparse

from fsq_agent.environments.providers._android import ANDROID_RUNTIME_PROVIDER, android_prerequisites
from fsq_agent.environments.providers._macos import MACOS_RUNTIME_PROVIDER, _macos_prerequisites
from fsq_agent.environments.providers._web import WEB_NAMES, WEB_RUNTIME_PROVIDER, web_candidate_paths
from fsq_agent.environments.providers._windows import WINDOWS_RUNTIME_PROVIDER
from fsq_agent.models import PlatformPrerequisiteCheck, PlatformRuntimeCheck, web_executable_matches_channel

if TYPE_CHECKING:
    from fsq_agent.environments.providers._runtime import RuntimeProvider

Platform = Literal["android", "web", "windows", "macos"]
_PROVIDERS: dict[Platform, RuntimeProvider] = {
    "android": ANDROID_RUNTIME_PROVIDER,
    "web": WEB_RUNTIME_PROVIDER,
    "windows": WINDOWS_RUNTIME_PROVIDER,
    "macos": MACOS_RUNTIME_PROVIDER,
}


def _web_candidate_paths(channel: str) -> list[Path]:
    return web_candidate_paths(channel)


class PlatformRuntimeService:
    def check(self, platform: Platform) -> PlatformRuntimeCheck:
        return _PROVIDERS[platform].check()

    def discover_web_executables(self, channel: str) -> list[Path]:
        candidates = _web_candidate_paths(channel)
        if platform.system() == "Windows":
            return sorted({candidate.expanduser().resolve() for candidate in candidates if candidate.is_file()})
        names = WEB_NAMES[channel]
        return sorted({candidate.expanduser().resolve() for candidate in candidates if candidate.is_file() and any(name in str(candidate).casefold() for name in names)})

    def web_executable_matches_channel(self, channel: str, executable: Path) -> bool:
        return web_executable_matches_channel(channel, executable)  # type: ignore[arg-type]

    def check_target_configuration(self, settings) -> tuple[bool, str, str]:
        try:
            valid = _target_configuration_valid(settings, self)
        except (AttributeError, TypeError, ValueError):
            return False, "Platform Target configuration is invalid.", "Repair the selected platform Target configuration."
        if not valid:
            return False, "Platform Target configuration is invalid.", "Repair the selected platform Target configuration."
        return True, "Platform Target configuration is ready.", ""

    def check_target_availability(self, settings, prerequisites: tuple[PlatformPrerequisiteCheck, ...] | None = None) -> tuple[bool, str, str]:
        if settings.harness.platform == "android":
            facts = prerequisites if prerequisites is not None else self.check_prerequisites(settings)
            failed = next((item for item in facts if item.status != "ready"), None)
            return (False, failed.message, failed.action or "Recheck Android environment.") if failed else (True, "The selected Android device and application are available.", "")
        configured, message, action = self.check_target_configuration(settings)
        if not configured:
            return configured, message, action
        selected = settings.harness.platform
        if selected == "macos" and platform.system() != "Darwin":
            return False, "macOS prerequisites require a macOS host.", "Run macOS platform tests on a macOS host."
        if selected == "web":
            executable = Path(settings.harness.web.browser_executable_path)
            if executable.is_file() and self.web_executable_matches_channel(settings.harness.web.channel, executable):
                return True, "Configured browser Target is available.", ""
            return False, "The configured browser Target is unavailable.", "Repair or reselect the browser Target."
        if selected == "windows":
            if Path(settings.harness.windows.app_path).is_file():
                return True, "Configured Windows application Target is available.", ""
            return False, "The configured Windows application Target is unavailable.", "Repair or reselect the Windows application Target."
        if selected == "macos":
            macos_prerequisites = prerequisites if prerequisites is not None else self.check_prerequisites(settings)
            failed = next((item for item in macos_prerequisites if item.status in {"unavailable", "error"}), None)
            if failed is not None:
                return False, failed.message, failed.action or "Repair the macOS host prerequisite."
            return True, "Configured macOS host prerequisites and application Target are available.", ""
        return False, "Unsupported platform target.", "Select a supported platform."

    def check_prerequisites(self, settings) -> tuple[PlatformPrerequisiteCheck, ...]:
        if settings.harness.platform == "android":
            return android_prerequisites(settings.harness.android)
        if settings.harness.platform != "macos":
            return ()
        return _macos_prerequisites(settings.harness.macos)


def _target_configuration_valid(settings, service: PlatformRuntimeService) -> bool:
    selected = settings.harness.platform
    if selected == "android":
        app_id = (settings.harness.android.app_id or "").strip()
        serial = (settings.harness.android.serial or "").strip()
        return bool(app_id) and not any(character.isspace() for character in app_id) and not any(character.isspace() for character in serial)
    if selected == "web":
        executable = settings.harness.web.browser_executable_path
        return executable is not None and Path(executable).is_file() and service.web_executable_matches_channel(settings.harness.web.channel, Path(executable))
    if selected == "windows":
        executable = settings.harness.windows.app_path
        return executable is not None and Path(executable).is_file()
    if selected == "macos":
        executable = settings.harness.macos.app_path
        has_identity = bool((settings.harness.macos.bundle_id or "").strip()) or executable is not None
        endpoint = urlparse(settings.harness.macos.appium_server_url or "")
        path_valid = executable is None or (Path(executable).exists() and (Path(executable).suffix.casefold() == ".app" or Path(executable).is_file()))
        return has_identity and path_valid and endpoint.scheme in {"http", "https"} and bool(endpoint.hostname) and endpoint.port is not None
    return False
