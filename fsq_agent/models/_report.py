# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, PrivateAttr, field_validator, model_validator


class ReportArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    path: Path
    format: Literal["markdown", "json", "html"] = "markdown"
    evidence_manifest_path: Path | None = None
    evidence_bundle_path: Path | None = None
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


def _identifier(value: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 500 or value in {".", ".."} or any(char in value for char in ("/", chr(92), chr(0))):
        raise ValueError("Expected a bounded local identity, not a path.")
    return value


class PublicRunReport(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    schema_version: Literal["fsq.report/v1"] = "fsq.report/v1"
    run: dict[str, JsonValue]
    source: dict[str, JsonValue] = Field(default_factory=dict)
    execution: dict[str, JsonValue]
    verification: dict[str, JsonValue]
    evidence: dict[str, JsonValue]
    processing: dict[str, JsonValue] = Field(default_factory=dict)
    steps: list[dict[str, JsonValue]] = Field(default_factory=list)
    tool_calls: list[dict[str, JsonValue]] = Field(default_factory=list)
    logs: list[dict[str, JsonValue]] = Field(default_factory=list)
    artifacts: list[dict[str, JsonValue]] = Field(default_factory=list)
    metrics: dict[str, JsonValue] = Field(default_factory=dict)
    lineage: dict[str, JsonValue] = Field(default_factory=dict)
    comparison: dict[str, JsonValue] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    _run_dirs: dict[str, Path] = PrivateAttr(default_factory=dict)
    _fingerprints: dict[str, dict[str, str | None]] = PrivateAttr(default_factory=dict)
    _related: list[Any] = PrivateAttr(default_factory=list)

    @field_validator("run")
    @classmethod
    def valid_run(cls, value):
        _identifier(value.get("run_id"))
        if value.get("platform") not in {None, "android", "web", "windows", "macos"}:
            raise ValueError("Unsupported report platform.")
        gate = value.get("gate")
        if not isinstance(gate, dict) or gate.get("status") not in {"passed", "failed", "error", "incomplete"}:
            raise ValueError("Report gate requires a supported disposition.")
        if not isinstance(gate.get("reasons"), list) or any(not isinstance(item, str) for item in gate["reasons"]):
            raise ValueError("Report gate requires ordered reason codes.")
        return value

    @field_validator("execution", "verification", "evidence")
    @classmethod
    def valid_outcome_sections(cls, value, info):
        allowed = {
            "execution": ("outcome", {"success", "failed", "error", "cancelled", "interrupted", "inconclusive", "preparing", "running", "finalizing"}),
            "verification": ("status", {"success", "passed", "failed", "inconclusive", "unknown", "pending", "not_requested", "not_applicable", "error", "cancelled"}),
            "evidence": ("status", {"complete", "partial", "unavailable", "not_applicable"}),
        }
        key, statuses = allowed[info.field_name]
        if key not in value or value[key] not in statuses:
            raise ValueError("Unsupported public report outcome.")
        return value

    @field_validator("artifacts")
    @classmethod
    def valid_artifact_refs(cls, value):
        for artifact in value:
            _identifier(artifact.get("run_id"))
            _identifier(artifact.get("artifact_id"))
            path = artifact.get("path")
            if path is not None and (not isinstance(path, str) or Path(path).is_absolute() or ".." in Path(path).parts or chr(92) in path):
                raise ValueError("Artifact paths must be contained relative paths.")
        return value

    @model_validator(mode="after")
    def consistent_gate(self):
        counts = self.execution.get("counts")
        if not isinstance(counts, dict):
            raise ValueError("Public report requires explicit count availability.")  # noqa: TRY004 - Pydantic validators normalize ValueError as field validation.
        count_keys = ("passed", "failed", "skipped", "cancelled", "incomplete")
        total = counts.get("total")
        if total is not None:
            if (
                not isinstance(total, int)
                or isinstance(total, bool)
                or total < 0
                or any(not isinstance(counts.get(key), int) or isinstance(counts.get(key), bool) or counts[key] < 0 for key in count_keys)
            ):
                raise ValueError("Public report counts must be nonnegative measured integers or unavailable.")
            if total != sum(counts[key] for key in count_keys):
                raise ValueError("Public report logical counts do not balance.")
        if self.run["gate"]["status"] != "passed":
            return self
        if not isinstance(counts, dict) or self.execution.get("outcome") != "success" or self.execution.get("trustworthy") is not True or self.execution.get("accounting_complete") is not True:
            raise ValueError("Passing report requires trustworthy completed execution and accounting.")
        if self.verification.get("status") not in {"success", "passed", "not_requested", "not_applicable"} or self.evidence.get("status") not in {"complete", "not_applicable"}:
            raise ValueError("Passing report cannot contain unresolved verification or evidence.")
        if self.evidence.get("required_missing") or self.evidence.get("errors"):
            raise ValueError("Passing report has missing required evidence.")
        if self.run.get("platform") not in {"web", "android", "windows", "macos"} or self.run.get("mode") not in {"strict", "explore"}:
            raise ValueError("Passing report requires established platform and execution mode.")
        if not isinstance(total, int) or isinstance(total, bool) or total < 0:
            raise ValueError("Passing report requires known logical counts.")
        if self.run.get("mode") == "strict" and (counts.get("failed") or counts.get("cancelled") or counts.get("incomplete") or counts.get("skipped") or not counts.get("attempt_count")):
            raise ValueError("Unfinished Strict accounting cannot pass.")
        if total == 0 and self.verification.get("status") not in {"success", "passed"}:
            raise ValueError("Unexecuted and unverified Run cannot pass.")
        if self.execution.get("history_conflict"):
            raise ValueError("Conflicting historical facts cannot pass.")
        observed_attempts = self.execution.get("observed_attempt_count")
        if observed_attempts is not None and observed_attempts != counts.get("attempt_count"):
            raise ValueError("Observed attempts disagree with the report count.")
        return self


class RunArtifactSelection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    run_id: str
    artifact_id: str

    _validate_ids = field_validator("run_id", "artifact_id")(_identifier)


class RunTextReplacement(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    text: str = Field(min_length=1, max_length=4000)
    replacement: str = Field(default="[REDACTED]", max_length=4000)


class RunScreenshotMask(RunArtifactSelection):
    x: int = Field(ge=0, strict=True)
    y: int = Field(ge=0, strict=True)
    width: int = Field(gt=0, strict=True)
    height: int = Field(gt=0, strict=True)


class RunCaseReviewDeclaration(RunArtifactSelection):
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    attribution: str = Field(min_length=1, max_length=200)


class RunShareProfile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal["fsq.share/v1"] = "fsq.share/v1"
    artifacts: tuple[RunArtifactSelection, ...] | None = None
    replacements: tuple[RunTextReplacement, ...] = ()
    remove_fields: tuple[str, ...] = ()
    masks: tuple[RunScreenshotMask, ...] = ()
    case_review_declaration: RunCaseReviewDeclaration | None = None

    @field_validator("remove_fields")
    @classmethod
    def display_fields_only(cls, value):
        allowed = {"source.description", "source.goal_summary", "execution.summary", "verification.summary", "logs.message", "logs.label", "steps.error_message", "artifacts.content"}
        if any(item not in allowed for item in value):
            raise ValueError("Only allowlisted display fields can be removed.")
        return value

    @model_validator(mode="after")
    def bounded(self):
        if sum(map(len, (self.replacements, self.remove_fields, self.masks, self.artifacts or ()))) > 256 or len(self.model_dump_json().encode()) > 256 * 1024:
            raise ValueError("Share profile exceeds the fixed resource limit.")
        return self


class RunReportExportOptions(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    format: Literal["json", "junit", "html", "bundle"]
    destination: Path
    export_id: str = "export"
    run_dirs: dict[str, Path] = Field(default_factory=dict)
    share_profile: RunShareProfile | None = None

    _validate_id = field_validator("export_id")(_identifier)


class RunReportExportResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    export_id: str
    format: Literal["json", "junit", "html", "bundle"]
    path: Path
    files: tuple[dict[str, JsonValue], ...] = ()
    warnings: tuple[str, ...] = ()
