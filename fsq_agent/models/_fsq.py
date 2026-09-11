# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictStr, field_validator, model_validator

FsqPlatform = Literal["android", "ios", "macos", "windows", "web"]
FsqCaseHookActionName = Literal["runCase", "runShell"]


class FsqCaseHookAction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action_name: FsqCaseHookActionName
    value: StrictStr

    @model_validator(mode="after")
    def _require_value(self) -> "FsqCaseHookAction":
        if self.value.strip():
            return self
        raise ValueError("requires non-empty hook action value")


class FsqCaseHook(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actions: list[FsqCaseHookAction]

    @model_validator(mode="before")
    @classmethod
    def _normalize_hook_entry(cls, value: Any) -> Any:
        if isinstance(value, cls):
            return value
        if not isinstance(value, dict):
            return value
        if set(value) == {"actions"}:
            return value

        actions: list[dict[str, Any]] = []
        unknown_actions: list[str] = []
        for action_name, action_value in value.items():
            if action_name not in {"runCase", "runShell"}:
                unknown_actions.append(str(action_name))
                continue
            actions.append({"action_name": action_name, "value": action_value})
        if unknown_actions:
            raise ValueError(f"unsupported hook action: {', '.join(unknown_actions)}")
        return {"actions": actions}

    @model_validator(mode="after")
    def _require_actions(self) -> "FsqCaseHook":
        if self.actions:
            return self
        raise ValueError("requires runCase or runShell")


class FsqCaseConfig(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)

    schema_version: str = Field(alias="schemaVersion")
    name: str
    description: str = ""
    platform: FsqPlatform
    app_id: str | None = Field(default=None, alias="appId")
    url: str | None = None
    tags: list[str] = Field(default_factory=list)
    env: dict[str, str | int | float | bool] = Field(default_factory=dict)
    properties: dict[str, Any] = Field(default_factory=dict)
    on_case_start: list[FsqCaseHook] = Field(default_factory=list, alias="onCaseStart")
    on_case_complete: list[FsqCaseHook] = Field(default_factory=list, alias="onCaseComplete")

    @field_validator("on_case_start", "on_case_complete", mode="before")
    @classmethod
    def _normalize_hook_field(cls, value: Any) -> Any:
        return _normalize_lifecycle_hook_field(value)


class CaseLifecycleSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    on_case_start: list[FsqCaseHook] = Field(default_factory=list, alias="onCaseStart")
    on_case_complete: list[FsqCaseHook] = Field(default_factory=list, alias="onCaseComplete")

    @field_validator("on_case_start", "on_case_complete", mode="before")
    @classmethod
    def _normalize_hook_field(cls, value: Any) -> Any:
        return _normalize_lifecycle_hook_field(value)


class FsqCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: Path
    config: FsqCaseConfig
    commands: list[Any]
    source_text: str | None = Field(default=None, exclude=True, repr=False)

    @property
    def id(self) -> str:
        return self.path.name.removesuffix(".fsq.yaml")


def _normalize_lifecycle_hook_field(value: Any) -> Any:
    if value is None:
        return []
    if isinstance(value, list):
        hooks = value
    elif isinstance(value, dict):
        hooks = [value]
    else:
        # Pydantic field validators require ValueError for normalized validation failures.
        raise ValueError("hook field must be a mapping or list of mappings")  # noqa: TRY004
    for hook in hooks:
        if isinstance(hook, dict) and "actions" in hook:
            raise ValueError("unsupported hook action: actions")
    return hooks
