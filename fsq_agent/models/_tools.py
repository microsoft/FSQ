# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from fsq_agent.models._core import ExecutableStepKind, FailureCategory, HarnessArtifactRef, HarnessPlatform, RunnerStatus

AgentToolStatus = Literal["success", "failed", "skipped"]
CommonToolStatus = AgentToolStatus
CapabilityExecutorKind = Literal["common", "driver"]
ReplayKind = Literal["fsq_command"]
ToolKind = Literal["common", "cli", "file", "harness", "shell"]
ToolStatus = Literal["success", "failed", "skipped"]


class ReplayPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: ReplayKind
    alias: str


class CapabilityDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    name: str
    executor_kind: CapabilityExecutorKind
    params_model: type[BaseModel]
    step_kind: ExecutableStepKind = "action"
    description: str = ""
    platform: HarnessPlatform | None = None
    backend: str | None = None
    owner: str | None = None
    post_action_delay_seconds: float | None = Field(default=None, ge=0)
    sensitivity: bool = False
    replay: ReplayPolicy | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def params_json_schema(self) -> dict[str, Any]:
        return self.params_model.model_json_schema()

    @property
    def fsq_command_alias(self) -> str | None:
        if self.replay is not None and self.replay.kind == "fsq_command":
            return self.replay.alias
        return None

    def safe_metadata(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "capability_name": self.name,
            "executor_kind": self.executor_kind,
            "step_kind": self.step_kind,
            "platform": self.platform,
            "backend": self.backend,
            "owner": self.owner,
            "post_action_delay_seconds": self.post_action_delay_seconds,
            "sensitivity": self.sensitivity,
            "replay": self.replay.model_dump(mode="json") if self.replay else None,
        }
        payload.update(self.metadata)
        return payload


class CapabilityRegistrySnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    capabilities: list[CapabilityDefinition] = Field(default_factory=list)

    def by_name(self) -> dict[str, CapabilityDefinition]:
        return {capability.name: capability for capability in self.capabilities}

    def resolve(self, name_or_alias: str) -> CapabilityDefinition | None:
        for capability in self.capabilities:
            if capability.name == name_or_alias or capability.fsq_command_alias == name_or_alias:
                return capability
        return None


class CapabilityInvocation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    capability_name: str
    params: dict[str, Any] = Field(default_factory=dict)
    run_id: str | None = None
    step_id: str | None = None
    source_ref: dict[str, Any] | None = None
    authored_action_name: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class CapabilityExecutionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    capability_name: str
    executor_kind: CapabilityExecutorKind
    status: RunnerStatus
    output: Any = None
    artifact_refs: list[HarnessArtifactRef] = Field(default_factory=list)
    error_message: str | None = None
    failure_category: FailureCategory | None = None
    duration_ms: int | None = Field(default=None, ge=0)
    unavailable_reason: str | None = "unmeasured"
    replay: ReplayPolicy | None = None
    sensitivity: bool = False
    safe_replay_params: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class AgentToolDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    description: str = ""
    params_json_schema: dict[str, Any] = Field(default_factory=dict)
    strict: bool = True
    metadata: dict[str, Any] = Field(default_factory=dict)


class AgentToolCall(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool_name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    timeout_seconds: int | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class AgentToolResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool_name: str
    status: AgentToolStatus
    output: Any = None
    artifact_path: Path | str | None = None
    artifact_content_chars: int | None = Field(default=None, ge=0)
    model_output: Literal["full", "artifact_reference"] = "full"
    sensitive: bool = False
    error: str | None = None
    duration_ms: int = Field(default=0, ge=0)
    metadata: dict[str, Any] = Field(default_factory=dict)
    raw: Any = None


class CommonToolDefinition(AgentToolDefinition):
    model_config = ConfigDict(extra="forbid")


class CommonToolCall(AgentToolCall):
    model_config = ConfigDict(extra="forbid")


class CommonToolResult(AgentToolResult):
    model_config = ConfigDict(extra="forbid")


class ToolDefinition(CommonToolDefinition):
    model_config = ConfigDict(extra="forbid")

    kind: ToolKind = "common"
    input_schema: dict[str, Any] = Field(default_factory=dict)
    server_name: str | None = None
    command: str | None = None


class ToolCall(CommonToolCall):
    model_config = ConfigDict(extra="forbid")

    kind: ToolKind | None = None


class ToolResult(CommonToolResult):
    model_config = ConfigDict(extra="forbid")
