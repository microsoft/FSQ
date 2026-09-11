# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any


class ApplicationErrorCode(StrEnum):
    WORKSPACE_NOT_INITIALIZED = "workspace.not_initialized"
    CASE_GOAL_INVALID = "case.goal_invalid"
    CASE_NOT_FOUND = "case.not_found"
    CASE_INVALID = "case.invalid"
    CASE_SUGGESTION_FAILED = "case.suggestion_failed"
    CONFIGURATION_INVALID = "configuration.invalid"
    PROVIDER_UNAVAILABLE = "provider.unavailable"
    ENVIRONMENT_UNAVAILABLE = "environment.unavailable"
    RUN_SOURCE_CHANGED = "run.source_changed"
    RUN_EXPORT_INVALID = "run.export_invalid"
    RUN_COMPARISON_INVALID = "run.comparison_invalid"
    RUN_EXECUTION_FAILED = "run.execution_failed"
    RUN_CANCELLED = "run.cancelled"
    RUN_NOT_FOUND = "run.not_found"
    RUN_ID_CONFLICT = "run.id_conflict"
    RUN_METADATA_INVALID = "run.metadata_invalid"
    RUN_LOGS_UNAVAILABLE = "run.logs_unavailable"
    RUN_LOGS_INVALID = "run.logs_invalid"
    RUN_REPORT_UNAVAILABLE = "run.report_unavailable"
    RUN_REPORT_GENERATION_FAILED = "run.report_generation_failed"
    RUN_REPORT_OPEN_FAILED = "run.report_open_failed"
    RUN_ID_ALLOCATION_FAILED = "run.id_allocation_failed"
    INTERNAL_ERROR = "internal.error"


class ApplicationErrorCategory(StrEnum):
    WORKSPACE_CONFIGURATION = "workspace_configuration"
    REQUEST_VALIDATION = "request_validation"
    CONFIGURATION = "configuration"
    UNAVAILABLE = "unavailable"
    INTERNAL = "internal"


class ApplicationRecordType(StrEnum):
    EVENT = "event"
    RESULT = "result"
    ERROR = "error"


def _record_metadata(*, operation: str, status: str) -> dict[str, object]:
    return {"schema_version": "fsq.machine/v1", "operation": operation, "status": status, "timestamp": datetime.now(UTC).isoformat()}


class ApplicationError(Exception):
    def __init__(self, *, code: ApplicationErrorCode, category: ApplicationErrorCategory, message: str, action: str | None = None, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code, self.category, self.message, self.action, self.details = code, category, message, action, details or {}

    def to_record(self, *, operation: str = "unknown") -> dict[str, object]:
        return {
            **_record_metadata(operation=operation, status="error"),
            "type": ApplicationRecordType.ERROR.value,
            "error": {"code": self.code.value, "category": self.category.value, "message": self.message, "action": self.action, "details": self.details},
        }


def result_record(result: object, *, operation: str = "unknown", status: str = "success", warnings: list[str] | None = None) -> dict[str, object]:
    return {**_record_metadata(operation=operation, status=status), "type": ApplicationRecordType.RESULT.value, "warnings": warnings or [], "result": result}


def event_record(event: object, *, operation: str = "unknown") -> dict[str, object]:
    return {**_record_metadata(operation=operation, status="running"), "type": ApplicationRecordType.EVENT.value, "event": event}
