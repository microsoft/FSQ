# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from __future__ import annotations

from fsq_agent.models import (
    DiagnosticProbeResult,
    DoctorCheckResult,
    DoctorProgressEvent,
    DoctorProgressSink,
)


def emit_started(
    sink: DoctorProgressSink | None,
    *,
    phase: str,
    check_id: str,
    summary: str,
) -> None:
    emit(
        sink,
        DoctorProgressEvent(
            event_type="check_started",
            phase=phase,
            check_id=check_id,
            summary=summary,
        ),
    )


def emit_completed(
    sink: DoctorProgressSink | None,
    result: DiagnosticProbeResult,
) -> None:
    check = DoctorCheckResult.model_validate(result.model_dump())
    emit(
        sink,
        DoctorProgressEvent(
            event_type="check_completed",
            phase=check.category,
            check_id=check.id,
            status=check.status,
            summary=check.summary,
            check=check,
        ),
    )


def emit(sink: DoctorProgressSink | None, event: DoctorProgressEvent) -> None:
    if sink is None:
        return
    try:
        sink(event)
    except Exception:
        pass
