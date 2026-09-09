# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

WebBrowserChannel = Literal["chromium", "chrome", "chrome-beta", "chrome-dev", "chrome-canary", "msedge", "msedge-beta", "msedge-dev", "msedge-canary"]

_EXECUTABLE_NAMES: dict[WebBrowserChannel, frozenset[str]] = {
    "chromium": frozenset({"chromium", "chromium.exe", "chromium-browser", "chrome.exe"}),
    "chrome": frozenset({"chrome", "chrome.exe", "google chrome", "google-chrome", "google-chrome-stable"}),
    "chrome-beta": frozenset({"chrome", "chrome.exe", "google chrome beta", "google-chrome-beta"}),
    "chrome-dev": frozenset({"chrome", "chrome.exe", "google chrome dev", "google-chrome-unstable"}),
    "chrome-canary": frozenset({"chrome", "chrome.exe", "google chrome canary"}),
    "msedge": frozenset({"msedge", "msedge.exe", "microsoft edge"}),
    "msedge-beta": frozenset({"msedge", "msedge.exe", "microsoft edge beta"}),
    "msedge-dev": frozenset({"msedge", "msedge.exe", "microsoft edge dev"}),
    "msedge-canary": frozenset({"msedge", "msedge.exe", "microsoft edge canary"}),
}
_IDENTITY_COMPONENTS: dict[WebBrowserChannel, frozenset[str]] = {
    "chromium": frozenset({"chromium", "chromium.app"}),
    "chrome": frozenset({"chrome", "google chrome", "google chrome.app", "google-chrome", "google-chrome-stable"}),
    "chrome-beta": frozenset({"chrome beta", "google chrome beta", "google chrome beta.app", "google-chrome-beta"}),
    "chrome-dev": frozenset({"chrome dev", "google chrome dev", "google chrome dev.app", "google-chrome-unstable", "chrome-unstable"}),
    "chrome-canary": frozenset({"chrome canary", "google chrome canary", "google chrome canary.app", "chrome sxs"}),
    "msedge": frozenset({"edge", "microsoft edge", "microsoft edge.app", "msedge"}),
    "msedge-beta": frozenset({"edge beta", "microsoft edge beta", "microsoft edge beta.app", "msedge beta"}),
    "msedge-dev": frozenset({"edge dev", "microsoft edge dev", "microsoft edge dev.app", "msedge dev"}),
    "msedge-canary": frozenset({"edge canary", "microsoft edge canary", "microsoft edge canary.app", "edge sxs", "msedge canary"}),
}
_ALL_IDENTITIES = frozenset().union(*_IDENTITY_COMPONENTS.values())


def web_executable_matches_channel(channel: WebBrowserChannel, executable: Path) -> bool:
    """Return whether path components prove the selected browser channel."""
    normalized = executable.expanduser().resolve()
    if normalized.name.casefold() not in _EXECUTABLE_NAMES[channel]:
        return False
    components = {part.casefold() for part in normalized.parts[:-1]}
    selected = _IDENTITY_COMPONENTS[channel]
    return bool(components & selected) and not bool(components & (_ALL_IDENTITIES - selected))


class PlatformRuntimeCheck(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    platform: Literal["android", "web", "windows", "macos"]
    status: Literal["ready", "missing", "unsupported"]
    ready: bool
    message: str
    action: str | None = None

    @model_validator(mode="after")
    def validate_status(self) -> "PlatformRuntimeCheck":
        if self.ready != (self.status == "ready"):
            raise ValueError("platform runtime status and readiness must agree")
        return self


class PlatformPrerequisiteCheck(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    identifier: str
    code: str | None = None
    target_id: str | None = Field(default=None, min_length=1, max_length=256, pattern=r"^[A-Za-z0-9_.:@-]+$")
    status: Literal["ready", "unavailable", "error", "not_applicable"]
    message: str
    action: str | None = None
    commands: tuple[Annotated[str, Field(min_length=1, max_length=2000)], ...] = Field(default=(), max_length=5)

    @model_validator(mode="after")
    def _selection_identity_only(self) -> "PlatformPrerequisiteCheck":
        if self.target_id is not None and self.identifier != "device_selection":
            raise ValueError("target_id belongs only to Android device selection.")
        return self
