# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from fsq_agent.models._report import ReportArtifact

StepStatus = Literal["success", "failed", "skipped", "adjusted"]
VerificationStatus = Literal["success", "failed", "inconclusive"]
PlanningReferenceKind = Literal["goal", "raw_case", "unknown"]


class Task(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = "task"
    name: str = "Task"
    description: str
    acceptance_criteria: list[str] = Field(default_factory=list)
    planning_reference_kind: PlanningReferenceKind | None = None
    planning_reference_text: str | None = None
    key_actions: list[str] = Field(default_factory=list)
    verification_goal: str | None = None
    timeout_seconds: int = Field(default=300, ge=1)
    max_retries: int = Field(default=3, ge=0)
    knowledge_refs: list[str] = Field(default_factory=list)


class ExecutionStep(BaseModel):
    model_config = ConfigDict(extra="forbid")

    step_id: int = Field(ge=1)
    action: str
    tool: str
    tool_input: dict[str, Any] = Field(default_factory=dict)
    expected_outcome: str
    timeout_seconds: int | None = Field(default=None, ge=1)
    max_attempts: int = Field(default=1, ge=1)


class ExecutionPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: str
    steps: list[ExecutionStep]
    rationale: str = ""
    warnings: list[str] = Field(default_factory=list)


class StepResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    step_id: int = Field(ge=1)
    status: StepStatus
    actual_outcome: str
    screenshot_path: Path | None = None
    ui_tree_snapshot: dict[str, Any] | None = None
    duration_ms: int = Field(default=0, ge=0)
    error: str | None = None
    tool_name: str | None = None
    tool_output: Any = None


class VerificationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: VerificationStatus
    summary: str
    satisfied_criteria: list[str] = Field(default_factory=list)
    unmet_criteria: list[str] = Field(default_factory=list)
    diagnostics: list[str] = Field(default_factory=list)


class TaskResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: str
    status: Literal["success", "failed", "inconclusive", "cancelled", "error"]
    steps: list[StepResult]
    verification: VerificationResult
    report: ReportArtifact
    duration_ms: int | None = Field(default=None, ge=0)
