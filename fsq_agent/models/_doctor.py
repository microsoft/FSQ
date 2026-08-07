# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from __future__ import annotations

from pathlib import Path
from collections.abc import Callable
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


DoctorMode = Literal["dynamic", "strict", "all"]
DoctorOutputFormat = Literal["text", "json"]
DoctorStatus = Literal["pass", "warn", "fail", "skip"]
DoctorTarget = Literal["dynamic", "strict", "ai_assertion"]
DoctorReadinessState = Literal["ready", "blocked", "not_checked"]
DoctorRepairStatus = Literal["applied", "declined", "skipped", "failed"]
DoctorRepairAction = Literal[
    "workspace.initialize",
    "environment.update",
    "provider.refresh_copilot_token",
]


class DoctorRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    platform: Literal["android", "web", "windows", "macos"] | None = None
    mode: DoctorMode = "all"
    output_format: DoctorOutputFormat = "text"
    interactive: bool = False
    repair: bool = False
    working_directory: Path = Field(default_factory=Path.cwd)
    timeout_seconds: float = Field(default=5.0, gt=0, le=60)

    @model_validator(mode="after")
    def validate_format_and_repair(self) -> "DoctorRequest":
        if self.output_format == "json" and self.repair:
            raise ValueError("JSON doctor output cannot be combined with repair.")
        if self.output_format == "json" and self.interactive:
            raise ValueError("JSON doctor output must be non-interactive.")
        return self


class DoctorFix(BaseModel):
    model_config = ConfigDict(extra="forbid")

    description: str
    command: str | None = None
    verification_command: str | None = None
    environment_variable: str | None = None
    documentation_url: str | None = None
    repair_action: DoctorRepairAction | None = None


class DiagnosticProbeResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    category: str
    status: DoctorStatus
    summary: str
    affected_targets: list[DoctorTarget] = Field(default_factory=list)
    prerequisite_ids: list[str] = Field(default_factory=list)
    fixes: list[DoctorFix] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class DoctorCheckResult(DiagnosticProbeResult):
    pass


class DoctorRepairResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action_id: DoctorRepairAction
    target: str
    status: DoctorRepairStatus
    backup_path: Path | None = None
    rerun_check_ids: list[str] = Field(default_factory=list)


class DoctorReadinessItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: DoctorReadinessState
    blocking_check_ids: list[str] = Field(default_factory=list)


class DoctorReadiness(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dynamic_llm: DoctorReadinessItem
    strict_core: DoctorReadinessItem
    ai_assertion: DoctorReadinessItem


class DoctorReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = 1
    platform: Literal["android", "web", "windows", "macos"] | None = None
    platform_source: Literal["explicit", "environment", "interactive", "unresolved"] = "unresolved"
    requested_mode: DoctorMode = "all"
    status: Literal["ready", "blocked", "usage_error", "cancelled"]
    exit_code: Literal[0, 1, 2, 130]
    checks: list[DoctorCheckResult] = Field(default_factory=list)
    repairs: list[DoctorRepairResult] = Field(default_factory=list)
    readiness: DoctorReadiness
    summary: dict[str, int] = Field(default_factory=dict)


class DoctorProgressEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_type: Literal[
        "phase_started",
        "check_started",
        "check_completed",
        "repair_started",
        "repair_completed",
        "summary_ready",
    ]
    phase: str | None = None
    check_id: str | None = None
    repair_action: DoctorRepairAction | None = None
    status: str | None = None
    summary: str = ""
    check: DoctorCheckResult | None = None
    repair: DoctorRepairResult | None = None
    report: DoctorReport | None = None


DoctorProgressSink = Callable[[DoctorProgressEvent], None]


class EnvironmentFileUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: Path
    keys: tuple[str, ...]
    backup_path: Path | None = None
    line_ending: str = "\n"
