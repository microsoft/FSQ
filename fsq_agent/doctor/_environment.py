# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from fsq_agent.config import read_env_values


PLATFORM_ENV: dict[str, tuple[str, ...]] = {
    "android": ("FSQ_ANDROID_APP_ID", "FSQ_ANDROID_SERIAL"),
    "web": ("FSQ_WEB_BROWSER_EXECUTABLE_PATH",),
    "windows": ("FSQ_WINDOWS_APP_PATH",),
    "macos": ("FSQ_MACOS_APPIUM_SERVER_URL", "FSQ_MACOS_BUNDLE_ID", "FSQ_MACOS_APP_PATH"),
}


def effective_environment(working_directory: Path, process_environment: Mapping[str, str]) -> dict[str, str]:
    try:
        effective = read_env_values(working_directory / ".env")
    except Exception:
        effective = {}
    effective.update({key: value for key, value in process_environment.items() if value.strip()})
    return effective


def platform_candidates(effective: Mapping[str, str]) -> list[str]:
    return [
        platform
        for platform, names in PLATFORM_ENV.items()
        if any(effective.get(name, "").strip() for name in names)
    ]
