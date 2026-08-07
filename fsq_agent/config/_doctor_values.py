# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from __future__ import annotations

from pathlib import Path
import re
from urllib.parse import urlsplit

from fsq_agent.config._loader import _parse_windows_launch_args, _validate_windows_backend_kind
from fsq_agent.models import ConfigurationError


def validate_doctor_environment_value(name: str, value: str) -> None:
    if not value or "\n" in value or "\r" in value:
        raise ConfigurationError("Platform environment value must be a non-empty single line.", context={"key": name})
    if name == "FSQ_WEB_BROWSER_EXECUTABLE_PATH":
        path = Path(value).expanduser()
        if not path.is_file() or path.name.casefold() not in {
            "chrome", "chrome.exe", "google chrome", "google-chrome", "google-chrome-stable"
        }:
            raise ConfigurationError("Web browser path must identify an existing Chrome executable.", context={"key": name})
    elif name == "FSQ_WINDOWS_APP_PATH" and not Path(value).expanduser().is_file():
        raise ConfigurationError("Windows application path must identify an existing file.", context={"key": name})
    elif name == "FSQ_WINDOWS_BACKEND_KIND":
        _validate_windows_backend_kind(value)
    elif name == "FSQ_WINDOWS_WINDOW_TITLE_RE":
        try:
            re.compile(value)
        except re.error as exc:
            raise ConfigurationError("Windows title regular expression is invalid.", context={"key": name}) from exc
    elif name == "FSQ_WINDOWS_LAUNCH_ARGS":
        _parse_windows_launch_args(value)
    elif name == "FSQ_MACOS_APPIUM_SERVER_URL":
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ConfigurationError("Appium server URL must be an http(s) URL.", context={"key": name})
    elif name == "FSQ_MACOS_APP_PATH" and not Path(value).expanduser().exists():
        raise ConfigurationError("macOS application path must exist.", context={"key": name})
    elif name == "FSQ_ANDROID_APP_ID" and not re.fullmatch(r"[A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)+", value):
        raise ConfigurationError("Android app id must be a dotted package id.", context={"key": name})
