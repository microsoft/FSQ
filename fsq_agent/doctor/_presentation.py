# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from __future__ import annotations


_ACRONYMS = {
    "adb": "ADB",
    "ai": "AI",
    "api": "API",
    "appium": "Appium",
    "copilot": "Copilot",
    "http": "HTTP",
    "https": "HTTPS",
    "github": "GitHub",
    "llm": "LLM",
    "tls": "TLS",
    "url": "URL",
}
_PLATFORM_PREFIXES = {"android", "web", "windows", "macos"}
_OMITTED_PREFIXES = {"environment"}
_PREFIX_TITLES = {
    "config": "Configuration",
    "workspace": "Workspace",
}


def check_title(check_id: str) -> str:
    segments = [segment for segment in check_id.strip().split(".") if segment]
    if not segments:
        return "Unknown check"
    if segments[0] in _OMITTED_PREFIXES and len(segments) > 1:
        segments = segments[1:]
    elif segments[0] in _PLATFORM_PREFIXES and len(segments) > 2:
        segments = segments[1:]
    elif segments[0] == "provider" and len(segments) > 2:
        segments = segments[1:]
    elif segments[0] in _PREFIX_TITLES and len(segments) > 1:
        segments[0] = _PREFIX_TITLES[segments[0]]
    words = [word for segment in segments for word in segment.replace("-", "_").split("_") if word]
    if not words:
        return check_id.replace("_", " ")
    rendered = [_ACRONYMS.get(word.casefold(), word) for word in words]
    rendered[0] = rendered[0][0].upper() + rendered[0][1:] if rendered[0] else rendered[0]
    return " ".join(rendered)
