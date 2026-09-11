# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ._task import StepResult, Task, VerificationResult

SafeText = str | None


class RunSource(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    kind: Literal["case", "goal"]
    case_id: str | None = None
    case_path: str | None = None
    goal_summary: str | None = None
    snapshot_path: str | None = None
    digest: str | None = None

    @field_validator("case_id", "case_path", "goal_summary")
    @classmethod
    def bound_source_text(cls, value: SafeText) -> SafeText:
        if value is not None and (not value.strip() or len(value) > 500):
            raise ValueError("Run source text must be non-blank and at most 500 characters.")
        return value


class RunStepCounts(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    total: int | None = Field(default=0, ge=0)
    passed: int | None = Field(default=0, ge=0)
    failed: int | None = Field(default=0, ge=0)
    skipped: int | None = Field(default=0, ge=0)
    cancelled: int | None = Field(default=0, ge=0)
    incomplete: int | None = Field(default=0, ge=0)
    attempt_count: int | None = Field(default=0, ge=0)
    unavailable_reason: str | None = None

    @model_validator(mode="after")
    def validate_counts(self):
        outcomes = (self.passed, self.failed, self.skipped, self.cancelled, self.incomplete)
        if self.total is None or any(value is None for value in outcomes):
            if not self.unavailable_reason or self.total is not None or any(value is not None for value in outcomes):
                raise ValueError("Unknown counts require null values and a reason.")
        elif self.total != sum(outcomes):
            raise ValueError("Run counts must account for every logical leaf exactly once.")
        return self


class RunResultSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    summary: str = Field(default="", max_length=2000)
    steps: RunStepCounts | None = Field(default_factory=RunStepCounts)
    failed_step: str | None = Field(default=None, max_length=500)


class RunRuntime(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    provider: str | None = Field(default=None, max_length=200)
    model: str | None = Field(default=None, max_length=200)


class RunArtifactIndex(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    report: str | None = None
    report_markdown: str | None = None
    events: str | None = None
    evidence_manifest: str | None = None
    suggestions: str | None = None
    candidate_case: str | None = None
    html_report: str | None = None
    execution_result: str | None = None
    evidence_journal: str | None = None


class RunMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal["fsq.run/v1", "fsq.run/v2"] = "fsq.run/v2"
    revision: int = Field(default=0, ge=0)
    run_id: str
    workspace: dict[Literal["name"], str]
    platform: Literal["android", "web", "windows", "macos"]
    mode: Literal["strict", "explore"]
    status: Literal["preparing", "running", "finalizing", "success", "failed", "inconclusive", "cancelled", "error"]
    started_at: datetime | None
    completed_at: datetime | None = None
    duration_ms: int | None = Field(default=None, ge=0)
    duration_unavailable_reason: str | None = "unmeasured"
    source: RunSource
    result: RunResultSummary = Field(default_factory=RunResultSummary)
    runtime: RunRuntime = Field(default_factory=RunRuntime)
    artifacts: RunArtifactIndex = Field(default_factory=RunArtifactIndex)

    execution_result: str | None = None
    evidence: dict[str, Any] = Field(default_factory=dict)
    processing: dict[str, dict[str, Any]] = Field(default_factory=dict)
    provenance: dict[str, Any] = Field(default_factory=dict)
    lineage: list[dict[str, Any]] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def preserve_legacy_unknowns(cls, value):
        if isinstance(value, dict) and value.get("schema_version") == "fsq.run/v1":
            value = dict(value)
            supplied_result = value.get("result")
            result = supplied_result.model_dump() if isinstance(supplied_result, RunResultSummary) else dict(supplied_result or {})
            if isinstance(result.get("steps"), RunStepCounts):
                result["steps"] = result["steps"].model_dump()
            if not isinstance(result.get("steps"), dict) or not result["steps"]:
                result["steps"] = None
                value["result"] = result
                return value
            result["steps"] = {**result["steps"], "attempt_count": result["steps"].get("attempt_count")}
            try:
                RunStepCounts.model_validate(result["steps"])
            except ValueError:
                result["steps"] = None
            value["result"] = result
        return value

    @model_validator(mode="after")
    def validate_relative_paths(self) -> "RunMetadata":
        paths = [self.source.case_path if self.source else None, self.source.snapshot_path if self.source else None, self.execution_result, *self.artifacts.model_dump().values()]
        for value in paths:
            if value is None:
                continue
            path = Path(value)
            if path.is_absolute() or ".." in path.parts:
                raise ValueError("Run paths must be contained relative paths.")
        if self.source and self.source.kind == "case" and not self.source.case_id:
            raise ValueError("Case Run source requires case_id.")
        if self.source and self.source.kind == "goal" and self.source.case_id is not None:
            raise ValueError("Goal Run source cannot contain case_id.")
        for moment in (self.started_at, self.completed_at):
            if moment is not None and (moment.tzinfo is None or moment.utcoffset() != UTC.utcoffset(moment)):
                raise ValueError("Run timestamps must be UTC.")
        if self.completed_at and self.started_at and self.completed_at < self.started_at:
            raise ValueError("Run completion cannot precede its start.")
        _validate_evidence(self.evidence, required=False)
        for value in self.processing.values():
            if value.get("status") not in {"not_requested", "pending", "success", "failed", "cancelled"}:
                raise ValueError("Invalid processing status.")
        return self


class RunExecutionContext(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    run_id: str
    platform: str
    run_dir: Path = Field(exclude=True)
    workspace_name: str = ""
    provenance: dict[str, Any] = Field(default_factory=dict)


class DynamicAgentOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    task: Task
    steps: list[StepResult]
    verification: VerificationResult
    duration_ms: int | None = None
    errors: list[dict[str, Any]] = Field(default_factory=list)


class RunExecutionResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal["fsq.execution-result/v1"] = "fsq.execution-result/v1"
    run_id: str
    platform: Literal["android", "web", "windows", "macos"]
    mode: Literal["strict", "explore"]
    outcome: Literal["success", "failed", "inconclusive", "cancelled", "error"]
    completed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    summary: str
    counts: RunStepCounts
    failed_step: str | None = None
    verification: dict[str, Any]
    evidence: dict[str, Any]
    primary_failure: dict[str, Any] | None = None
    duration_ms: int | None = Field(default=None, ge=0)
    duration_unavailable_reason: str | None = "unmeasured"
    steps: list[dict[str, Any]] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_conclusions(self):
        if self.counts.total is None:
            raise ValueError("Frozen execution requires complete logical counts.")
        if self.verification.get("status") not in {"success", "passed", "failed", "inconclusive", "cancelled", "error", "not_requested", "not_applicable"}:
            raise ValueError("Invalid verification status.")
        _validate_evidence(self.evidence, required=True)
        if self.outcome == "success":
            if self.verification["status"] in {"failed", "error", "cancelled", "inconclusive"}:
                raise ValueError("Execution success conflicts with verification outcome.")
            if self.counts.cancelled or self.counts.incomplete or self.evidence["status"] in {"partial", "unavailable"}:
                raise ValueError("Execution success cannot contain unfinished or missing required evidence.")
            if self.mode == "strict" and (self.counts.failed or self.counts.skipped or self.verification["status"] == "failed"):
                raise ValueError("Strict success cannot contain blocked logical leaves or failed verification.")
            if not self.counts.attempt_count and self.verification["status"] not in {"success", "passed"}:
                raise ValueError("Execution success requires an actual attempt or valid verification.")
        if self.completed_at.tzinfo is None or self.completed_at.utcoffset() != UTC.utcoffset(self.completed_at):
            raise ValueError("Run execution timestamps must be UTC.")
        return self


def _validate_evidence(value, *, required):
    if not value and not required:
        return
    if value.get("status") not in {"complete", "partial", "unavailable", "not_applicable"}:
        raise ValueError("Invalid evidence status.")
    if value.get("status") in {"complete", "not_applicable"} and (value.get("required_missing") or value.get("errors")):
        raise ValueError("Complete evidence cannot contain missing required evidence or errors.")
