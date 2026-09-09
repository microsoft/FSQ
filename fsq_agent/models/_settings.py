# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import os
import re
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, field_validator, model_validator

from fsq_agent.models._skills import SkillConfig


class AgentSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = "fsq-agent"
    step_timeout_seconds: int = Field(default=60, ge=1)


class LocalToolOutputSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    artifact_enabled: bool = True
    always_write_artifact: bool = True
    artifact_subdir: str = "artifacts/tools"
    recent_inline_output_count: int = Field(default=3, ge=0)
    full_output_max_chars: int = Field(default=30000, ge=1)
    total_inline_output_max_chars: int = Field(default=60000, ge=1)
    historical_output_mode: Literal["artifact_reference"] = "artifact_reference"
    historical_preview_chars: int = Field(default=1000, ge=0)
    model_response_max_chars: int = Field(default=4000, ge=500)

    @field_validator("artifact_subdir")
    @classmethod
    def validate_artifact_subdir(cls, value: str) -> str:
        path = Path(value)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError("artifact_subdir must be a relative path inside the run directory")
        return value


class RuntimeSecretSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    allowed_env_names: list[str] = Field(default_factory=list)
    _values: dict[str, str] = PrivateAttr(default_factory=dict)

    @property
    def allowed_names(self) -> list[str]:
        return list(self.allowed_env_names)

    def set_values(self, values: dict[str, str]) -> None:
        self.allowed_env_names = list(values)
        self._values = dict(values)

    def resolve(self, name: str) -> str:
        return self._values[name]

    def private_values(self) -> dict[str, str]:
        return dict(self._values)


class PrePlanKnowledgeSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dir: Path | None = None


class KnowledgeSkillSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dir: Path = Path("skills")
    items: list[SkillConfig] = Field(default_factory=list)


class AgentKnowledgeSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    root_dir: Path = Path("./knowledge")
    skills: KnowledgeSkillSettings = Field(default_factory=KnowledgeSkillSettings)
    pre_plan: PrePlanKnowledgeSettings = Field(default_factory=PrePlanKnowledgeSettings)


class AgentContextSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    knowledge: AgentKnowledgeSettings = Field(default_factory=AgentKnowledgeSettings)


class AndroidHarnessSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    backend: Literal["uiautomator2"] = "uiautomator2"
    _app_id: str | None = PrivateAttr(default=None)
    _serial: str | None = PrivateAttr(default=None)

    @property
    def app_id(self) -> str | None:
        return self._app_id

    @app_id.setter
    def app_id(self, value: str | None) -> None:
        self._app_id = value

    @property
    def serial(self) -> str | None:
        return self._serial

    @serial.setter
    def serial(self, value: str | None) -> None:
        self._serial = value


class WebHarnessSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    backend: Literal["playwright"] = "playwright"
    channel: Literal["chromium", "chrome", "chrome-beta", "chrome-dev", "chrome-canary", "msedge", "msedge-beta", "msedge-dev", "msedge-canary"] = "chrome"
    headless: bool = True
    base_url: str | None = None
    viewport_width: int | None = Field(default=None, ge=1)
    viewport_height: int | None = Field(default=None, ge=1)
    _browser_executable_path: Path | None = PrivateAttr(default=None)

    @property
    def browser_executable_path(self) -> Path | None:
        return self._browser_executable_path

    @browser_executable_path.setter
    def browser_executable_path(self, value: str | Path | None) -> None:
        self._browser_executable_path = Path(value) if value else None

    @field_validator("base_url")
    @classmethod
    def normalize_base_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @model_validator(mode="after")
    def _validate_viewport_pair(self) -> "WebHarnessSettings":
        if (self.viewport_width is None) == (self.viewport_height is None):
            return self
        raise ValueError("viewport_width and viewport_height must be configured together")


class WindowsHarnessSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    backend: Literal["pywinauto"] = "pywinauto"
    backend_kind: Literal["uia", "win32"] = "uia"
    launch_args: list[str] = Field(default_factory=list)
    app_path: Path | None = None
    window_title_re: str | None = None

    @field_validator("window_title_re")
    @classmethod
    def _normalize_window_title_re(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None


class MacOSHarnessSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    backend: Literal["appium_mac2"] = "appium_mac2"
    appium_server_url: str | None = None
    page_source_max_depth: int = Field(default=12, ge=1)
    action_timeout_seconds: int = Field(default=10, ge=1)
    new_command_timeout_seconds: int = Field(default=300, ge=1)
    _bundle_id: str | None = PrivateAttr(default=None)
    _app_path: Path | None = PrivateAttr(default=None)

    @field_validator("appium_server_url")
    @classmethod
    def _normalize_appium_server_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @property
    def bundle_id(self) -> str | None:
        return self._bundle_id

    @bundle_id.setter
    def bundle_id(self, value: str | None) -> None:
        self._bundle_id = value.strip() if isinstance(value, str) and value.strip() else None

    @property
    def app_path(self) -> Path | None:
        return self._app_path

    @app_path.setter
    def app_path(self, value: str | Path | None) -> None:
        self._app_path = Path(value) if value else None


class HarnessSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    platform: Literal["android", "web", "windows", "macos"] = "android"
    android: AndroidHarnessSettings = Field(default_factory=AndroidHarnessSettings)
    web: WebHarnessSettings = Field(default_factory=WebHarnessSettings)
    windows: WindowsHarnessSettings = Field(default_factory=WindowsHarnessSettings)
    macos: MacOSHarnessSettings = Field(default_factory=MacOSHarnessSettings)


class PostActionDelaySettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    platform: float = Field(default=1.0, ge=0)
    common: float = Field(default=0.0, ge=0)


class ExecutionSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    post_action_delay_seconds: PostActionDelaySettings = Field(default_factory=PostActionDelaySettings)


class AgentPromptConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent_template_path: Path | None = None
    task_template_path: Path | None = None
    variables: dict[str, Any] = Field(default_factory=dict)


class AgentRuntimeSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: Literal["openai", "azure_openai", "github_copilot"] | None = None
    max_turns: int = Field(default=50, ge=1)
    tracing_enabled: bool = True
    prompt: AgentPromptConfig = Field(default_factory=AgentPromptConfig)
    _base_url: str = PrivateAttr(default="")
    _model: str = PrivateAttr(default="")
    _api_key: str = PrivateAttr(default="")
    _github_token: dict[str, Any] | None = PrivateAttr(default=None)
    _provider_token: dict[str, Any] | None = PrivateAttr(default=None)
    _user_config_root: Path | None = PrivateAttr(default=None)
    _local_tool_output: LocalToolOutputSettings = PrivateAttr(default_factory=LocalToolOutputSettings)

    @property
    def base_url(self) -> str:
        return self._base_url

    @base_url.setter
    def base_url(self, value: str) -> None:
        self._base_url = value

    @property
    def api_key(self) -> str:
        return self._api_key

    @api_key.setter
    def api_key(self, value: str) -> None:
        self._api_key = value

    @property
    def model(self) -> str:
        return self._model

    @model.setter
    def model(self, value: str) -> None:
        self._model = value

    @property
    def github_token(self) -> dict[str, Any] | None:
        return dict(self._github_token) if self._github_token is not None else None

    @github_token.setter
    def github_token(self, value: dict[str, Any] | None) -> None:
        self._github_token = dict(value) if value is not None else None

    @property
    def provider_token(self) -> dict[str, Any] | None:
        return dict(self._provider_token) if self._provider_token is not None else None

    @provider_token.setter
    def provider_token(self, value: dict[str, Any] | None) -> None:
        self._provider_token = dict(value) if value is not None else None

    @property
    def user_config_root(self) -> Path | None:
        return self._user_config_root

    @user_config_root.setter
    def user_config_root(self, value: Path | None) -> None:
        self._user_config_root = value

    @property
    def local_tool_output(self) -> LocalToolOutputSettings:
        return self._local_tool_output

    @local_tool_output.setter
    def local_tool_output(self, value: LocalToolOutputSettings | dict[str, Any]) -> None:
        self._local_tool_output = LocalToolOutputSettings.model_validate(value)


class WorkspaceSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    root_dir: Path | None = None
    config_path: Path | None = None


_WORKSPACE_ENV_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}


def _normalize_workspace_name(value: str) -> str:
    normalized = value.strip()
    if not normalized or normalized in {".", ".."}:
        raise ValueError("workspace name must be a non-empty directory name")
    if any(ord(character) < 32 or character in "/\\" for character in normalized):
        raise ValueError("workspace name cannot contain path separators or control characters")
    if os.name == "nt":
        if any(character in '<>:"|?*' for character in normalized) or normalized.endswith((".", " ")):
            raise ValueError("workspace name contains characters invalid on Windows")
        if normalized.split(".", 1)[0].upper() in _WINDOWS_RESERVED_NAMES:
            raise ValueError("workspace name is reserved on Windows")
    return normalized


class AndroidWorkspaceTarget(BaseModel):
    model_config = ConfigDict(extra="forbid")

    app_id: str = Field(min_length=1)

    @field_validator("app_id")
    @classmethod
    def normalize_app_id(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("app_id cannot be blank")
        return normalized


class WebWorkspaceTarget(BaseModel):
    model_config = ConfigDict(extra="forbid")

    browser_channel: Literal["chromium", "chrome", "chrome-beta", "chrome-dev", "chrome-canary", "msedge", "msedge-beta", "msedge-dev", "msedge-canary"] = "chrome"
    browser_executable_path: Path


class WindowsWorkspaceTarget(BaseModel):
    model_config = ConfigDict(extra="forbid")

    app_path: Path
    window_title_re: str | None = None
    launch_args: str = ""

    @field_validator("window_title_re")
    @classmethod
    def normalize_window_title_re(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None


class MacOSWorkspaceTarget(BaseModel):
    model_config = ConfigDict(extra="forbid")

    bundle_id: str | None = None
    app_path: Path | None = None

    @field_validator("bundle_id")
    @classmethod
    def normalize_bundle_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @model_validator(mode="after")
    def validate_identity(self) -> "MacOSWorkspaceTarget":
        if self.bundle_id is None and self.app_path is None:
            raise ValueError("macOS target requires bundle_id or app_path")
        return self


WorkspaceTarget = AndroidWorkspaceTarget | WebWorkspaceTarget | WindowsWorkspaceTarget | MacOSWorkspaceTarget


class WorkspacePlatformCreateInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    platform: Literal["android", "web", "windows", "macos"]
    target: WorkspaceTarget
    env: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def parse_platform_target(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        platform = data.get("platform")
        target = data.get("target")
        target_models = {
            "android": AndroidWorkspaceTarget,
            "web": WebWorkspaceTarget,
            "windows": WindowsWorkspaceTarget,
            "macos": MacOSWorkspaceTarget,
        }
        target_model = target_models.get(platform)
        if target_model is not None and not isinstance(target, BaseModel):
            updated = dict(data)
            updated["target"] = target_model.model_validate(target)
            return updated
        return data

    @field_validator("env")
    @classmethod
    def validate_env(cls, value: dict[str, str]) -> dict[str, str]:
        normalized: dict[str, str] = {}
        for name, secret in value.items():
            if not _WORKSPACE_ENV_NAME_RE.fullmatch(name):
                raise ValueError(f"invalid workspace env name: {name}")
            if not isinstance(secret, str) or not secret.strip():
                raise ValueError(f"workspace env value cannot be blank: {name}")
            normalized[name] = secret
        return normalized

    @model_validator(mode="after")
    def validate_target_matches_platform(self) -> "WorkspacePlatformCreateInput":
        expected_types = {
            "android": AndroidWorkspaceTarget,
            "web": WebWorkspaceTarget,
            "windows": WindowsWorkspaceTarget,
            "macos": MacOSWorkspaceTarget,
        }
        if not isinstance(self.target, expected_types[self.platform]):
            raise ValueError("workspace target does not match platform")  # noqa: TRY004 - Pydantic validators require ValueError.
        return self


class WorkspaceConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal[2]
    name: str = Field(min_length=1, max_length=128)
    root_path: Path
    platform: Literal["android", "web", "windows", "macos"]
    target: WorkspaceTarget
    env: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def parse_platform_target(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        platform = data.get("platform")
        target = data.get("target")
        target_models = {
            "android": AndroidWorkspaceTarget,
            "web": WebWorkspaceTarget,
            "windows": WindowsWorkspaceTarget,
            "macos": MacOSWorkspaceTarget,
        }
        target_model = target_models.get(platform)
        if target_model is not None and not isinstance(target, BaseModel):
            updated = dict(data)
            updated["target"] = target_model.model_validate(target)
            return updated
        return data

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        return _normalize_workspace_name(value)

    @field_validator("root_path")
    @classmethod
    def validate_root_path(cls, value: Path) -> Path:
        if not value.expanduser().is_absolute():
            raise ValueError("root_path must be absolute")
        return value

    @field_validator("env")
    @classmethod
    def validate_env(cls, value: dict[str, str]) -> dict[str, str]:
        normalized: dict[str, str] = {}
        for name, secret in value.items():
            if not _WORKSPACE_ENV_NAME_RE.fullmatch(name):
                raise ValueError(f"invalid workspace env name: {name}")
            if not isinstance(secret, str) or not secret.strip():
                raise ValueError(f"workspace env value cannot be blank: {name}")
            normalized[name] = secret
        return normalized

    @model_validator(mode="after")
    def validate_target_matches_platform(self) -> "WorkspaceConfig":
        expected_types = {
            "android": AndroidWorkspaceTarget,
            "web": WebWorkspaceTarget,
            "windows": WindowsWorkspaceTarget,
            "macos": MacOSWorkspaceTarget,
        }
        if not isinstance(self.target, expected_types[self.platform]):
            raise ValueError("workspace target does not match platform")  # noqa: TRY004 - Pydantic validators require ValueError.
        return self


class WorkspaceRegistryEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    root_path: Path

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        return _normalize_workspace_name(value)

    @field_validator("root_path")
    @classmethod
    def validate_root_path(cls, value: Path) -> Path:
        expanded = value.expanduser()
        if not expanded.is_absolute():
            raise ValueError("workspace root_path must be absolute")
        if expanded.is_symlink():
            raise ValueError("workspace root_path must not be a symbolic link")
        return expanded.resolve()


class WorkspacePlatformStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")

    platform: Literal["android", "web", "windows", "macos"]
    config_path: Path
    status: Literal["available", "unavailable"]
    message: str
    action: str | None = None


class WorkspaceStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    root_path: Path
    status: Literal["available", "partial", "unavailable"]
    message: str
    action: str | None = None
    platforms: list[WorkspacePlatformStatus] = Field(default_factory=list)


class WorkspaceInitResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["initialized", "platform_added", "unchanged", "updated"]
    name: str = Field(min_length=1, max_length=128)
    root_path: Path
    platform: Literal["android", "web", "windows", "macos"]

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        return _normalize_workspace_name(value)

    @field_validator("root_path")
    @classmethod
    def validate_root_path(cls, value: Path) -> Path:
        expanded = value.expanduser()
        if not expanded.is_absolute():
            raise ValueError("workspace root_path must be absolute")
        if expanded.is_symlink():
            raise ValueError("workspace root_path must not be a symbolic link")
        return expanded.resolve()


class CaseSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dir: Path = Path("./cases")


class OutputSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    root_dir: Path = Path("output")
    _runs_dir: Path = PrivateAttr(default=Path("runs"))

    @property
    def runs_dir(self) -> Path:
        return self._runs_dir

    @runs_dir.setter
    def runs_dir(self, value: str | Path) -> None:
        self._runs_dir = Path(value)
