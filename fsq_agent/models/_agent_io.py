# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from fsq_agent.models._task import StepStatus, Task, VerificationStatus

AGENT_FINAL_OUTPUT_SCHEMA_VERSION = "task_run_v1"
AGENT_TASK_INPUT_SCHEMA_VERSION = "task_input_v1"


class AgentTaskInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["task_input_v1"] = AGENT_TASK_INPUT_SCHEMA_VERSION
    task: Task
    acceptance_criteria: list[str] = Field(default_factory=list)
    key_actions: list[str] = Field(default_factory=list)
    verification_goal: str | None = None
    runtime_policy: list[str] = Field(default_factory=list)
    runtime_secret_names: list[str] = Field(default_factory=list)
    runtime_secret_warnings: list[str] = Field(default_factory=list)
    acceptance_policy: str
    output_contract: Literal["task_run_v1"] = AGENT_FINAL_OUTPUT_SCHEMA_VERSION


class AgentPlanItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    step_id: int = Field(ge=1)
    action: str
    success_criteria: list[str] = Field(default_factory=list)
    status: StepStatus


class AgentFinalOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["task_run_v1"] = AGENT_FINAL_OUTPUT_SCHEMA_VERSION
    status: VerificationStatus
    summary: str
    pre_plan: list[AgentPlanItem] = Field(default_factory=list)
    plan_updates: list[str] = Field(default_factory=list)
    satisfied_criteria: list[str] = Field(default_factory=list)
    unmet_criteria: list[str] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


class ToolCallRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool_call_id: str | None = None
    tool_name: str
    tool_origin: Literal["agent_tool", "common", "platform", "harness", "runtime", "unknown"] = "unknown"
    status: Literal["completed", "failed"]
    transport_status: Literal["completed", "failed"] | None = None
    execution_status: Literal["passed", "failed", "success", "skipped", "cancelled", "incomplete", "error"] | None = None
    arguments: dict[str, Any] | str | None = None
    output_preview: str | None = None
    artifact_path: str | None = None
    error: str | None = None
    started_sequence: int | None = None
    completed_sequence: int | None = None
    started_at: datetime | str | None = None
    completed_at: datetime | str | None = None
    duration_ms: int | None = Field(default=None, ge=0)
