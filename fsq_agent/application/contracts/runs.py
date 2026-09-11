# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from fsq_agent.models import PublicRunReport, RunArtifactIndex, RunMetadata, RunResultSummary, RunRuntime, RunSource, RunStepCounts

Platform = Literal["android", "web", "windows", "macos"]
RunMode = Literal["strict", "explore"]
RunStatus = Literal["preparing", "running", "finalizing", "success", "failed", "inconclusive", "cancelled", "error", "interrupted"]


class RunSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    run_id: str
    platform: Platform
    mode: RunMode | None = None
    status: RunStatus | None = None
    started_at: datetime | None = None
    duration_ms: int | None = None
    source: RunSource | None = None
    result: RunResultSummary | None = None
    evidence: dict[str, Any] | None = None
    warnings: tuple[str, ...] = ()
    liveness: str | None = None
    persisted_status: str | None = None


class _RunScope(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    current_directory: Path | None = None
    workspace_name: str | None = None
    user_config_root: Path | None = None

    @model_validator(mode="after")
    def validate_scope(self):
        if (self.current_directory is None) == (self.workspace_name is None):
            raise ValueError("Supply exactly one current_directory or workspace_name.")
        return self


class ListRunsRequest(_RunScope):
    platform: Platform | None = None
    statuses: tuple[RunStatus, ...] = ()
    mode: RunMode | None = None
    since: str | None = None
    case_id: str | None = None
    limit: int = Field(default=20, ge=1, le=200)


class ListRunsResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    workspace: str
    platforms: tuple[Platform, ...]
    filters: dict[str, Any]
    matched_count: int
    returned_count: int
    truncated: bool
    runs: tuple[RunSummary, ...]
    warnings: tuple[str, ...] = ()


class ShowRunRequest(_RunScope):
    run_id: str
    platform: Platform | None = None


class GenerateRunHtmlRequest(ShowRunRequest):
    pass


class RunDetail(RunMetadata):
    mode: RunMode | None = None
    source: RunSource | None = None
    status: RunStatus | None = None
    availability: dict[str, str] = Field(default_factory=dict)
    liveness: str | None = None
    persisted_status: str | None = None


class ShowRunResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    workspace: str
    run: RunDetail
    html_path: str | None = None
    warnings: tuple[str, ...] = ()


class RunLogEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    sequence: int | None = None
    step_id: str | None = None
    event_type: str | None = None
    duration_ms: int | None = None
    time: str | None = Field(default=None, max_length=100)
    level: str | None = Field(default=None, max_length=50)
    phase: str | None = Field(default=None, max_length=100)
    tool: str | None = Field(default=None, max_length=200)
    label: str | None = Field(default=None, max_length=500)
    status: str | None = Field(default=None, max_length=100)
    message: str | None = Field(default=None, max_length=4000)


class ReadRunLogsRequest(_RunScope):
    run_id: str
    platform: Platform | None = None
    levels: tuple[str, ...] = ()
    phases: tuple[str, ...] = ()
    limit: int = Field(default=200, ge=1, le=5000)


class ReadRunLogsResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    run_id: str
    platform: Platform
    filters: dict[str, Any]
    matched_count: int
    returned_count: int
    truncated: bool
    events: tuple[RunLogEvent, ...]
    warnings: tuple[str, ...] = ()


class GenerateRunHtmlResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    run_id: str
    platform: Platform
    html_path: str


class GetRunReportRequest(ShowRunRequest):
    baseline_run_id: str | None = None
    related_run_ids: tuple[str, ...] = Field(default=(), max_length=8)


class GetRunReportResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    workspace: str
    run_id: str
    platform: Platform
    report: PublicRunReport
    warnings: tuple[str, ...] = ()


class ExportRunReportRequest(GetRunReportRequest):
    format: Literal["json", "junit", "html", "bundle"]
    output_path: Path | None = None
    share_profile: Path | None = None


class ExportRunReportResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    run_id: str
    platform: Platform
    export_id: str
    format: str
    output_path: Path
    execution_status: str | None = None
    report_gate: str = "incomplete"
    files: tuple[dict[str, Any], ...]
    warnings: tuple[str, ...] = ()


class _SourceArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    kind: Literal["source"]
    artifact_id: str


class _ExportArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    kind: Literal["export"]
    export_id: str
    file_id: str


class ResolveRunArtifactRequest(ShowRunRequest):
    reference: _SourceArtifact | _ExportArtifact = Field(discriminator="kind")


class ResolvedRunArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    path: Path = Field(exclude=True)
    size: int
    mime_type: str
    filename: str
    sha256: str | None = Field(default=None, exclude=True)


__all__ = [
    "ExportRunReportRequest",
    "ExportRunReportResult",
    "GenerateRunHtmlRequest",
    "GenerateRunHtmlResult",
    "GetRunReportRequest",
    "GetRunReportResult",
    "ListRunsRequest",
    "ListRunsResult",
    "ReadRunLogsRequest",
    "ReadRunLogsResult",
    "ResolveRunArtifactRequest",
    "ResolvedRunArtifact",
    "RunArtifactIndex",
    "RunDetail",
    "RunLogEvent",
    "RunMetadata",
    "RunResultSummary",
    "RunRuntime",
    "RunSource",
    "RunStepCounts",
    "RunSummary",
    "ShowRunRequest",
    "ShowRunResult",
]
