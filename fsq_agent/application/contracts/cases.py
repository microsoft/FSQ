# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from fsq_agent.models import RunEventSink


class CaseCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    current_directory: Path
    platform: Literal["android", "web", "windows", "macos"]
    goal: str
    case_name: str | None = None


class CaseCreateResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    run_id: str
    task_id: str
    status: str
    summary: str
    report_path: Path
    candidate_case_path: Path | None = None
    case_name: str | None = None
    published_case_path: Path | None = None
    publication_outcome: str = "not_requested"
    warnings: list[str] = Field(default_factory=list)


CaseCreateEventSink = RunEventSink


class CaseTestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    current_directory: Path
    platform: Literal["android", "web", "windows", "macos"]
    case_path: Path
    suggest: bool = False


class CaseTestResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    run_id: str
    status: Literal["success", "failed", "inconclusive", "cancelled", "error"]
    summary: str
    report_path: Path
    evidence_manifest_path: Path | None = None
    suggestion_path: Path | None = None
    candidate_case_path: Path | None = None
    warnings: list[str] = Field(default_factory=list)
    processing: dict[str, dict[str, str]] = Field(default_factory=dict)


class CaseFormatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    current_directory: Path
    case_path: Path
    mode: Literal["check", "diff", "write"] = "check"


class CaseFormatDiagnostic(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    code: str
    message: str
    file: str
    step_index: int | None = None
    field_path: list[str | int] = Field(default_factory=list)


class CaseFormatResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    path: Path
    mode: Literal["check", "diff", "write"]
    valid: bool
    formatted: bool = False
    changed: bool = False
    needs_formatting: bool = False
    diagnostics: list[CaseFormatDiagnostic] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    diff: str | None = None
    validation_scope: str = "document-local static validation; referenced files and runtime readiness are not checked"


class CaseSaveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    candidate_path: Path
    destination_directory: Path
    platform: Literal["android", "web", "windows", "macos"]
    case_name: str


class CaseSaveResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    path: Path | None
    outcome: Literal["created", "unchanged", "conflict", "failed"]
    draft: bool = False
