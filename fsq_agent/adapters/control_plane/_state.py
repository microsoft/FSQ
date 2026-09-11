# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from threading import Condition
from typing import Any, Literal
from uuid import uuid4

TaskStatus = Literal["preparing", "running", "finalizing", "success", "failed", "inconclusive", "cancelled", "error"]
_TERMINAL_STATUSES = {"success", "failed", "inconclusive", "cancelled", "error"}
_ACTIVE_STATUSES = {"preparing", "running", "finalizing"}


class BusyError(RuntimeError):
    pass


class RequestNotFoundError(KeyError):
    pass


class TaskCancelledError(RuntimeError):
    pass


def _now() -> str:
    return datetime.now(UTC).isoformat()


@dataclass
class TaskRecord:
    request_id: str
    workspace_name: str
    platform: str
    target_id: str
    mode: str
    source: dict[str, Any]
    status: TaskStatus = "preparing"
    started_at: str = field(default_factory=_now)
    completed_at: str | None = None
    run_id: str | None = None
    run_dir: Any | None = None
    cases_dir: Any | None = None
    events: list[dict[str, Any]] = field(default_factory=list)
    active_step: dict[str, Any] | None = None
    result: dict[str, Any] | None = None
    summary: str = "Preparing run."
    cancel_requested: bool = False
    screenshot: dict[str, Any] | None = None
    ui_snapshot: dict[str, Any] | None = None
    screenshot_revision: int = 0
    ui_snapshot_revision: int = 0
    report_available: bool = False
    evidence_available: bool = False


class ControlPlaneState:
    def __init__(self) -> None:
        self._condition = Condition()
        self._revision = 0
        self._current_request_id: str | None = None
        self._tasks: dict[str, TaskRecord] = {}

    def reserve(self, *, workspace_name: str, platform: str, target_id: str, mode: str, source: dict[str, Any]) -> str:
        with self._condition:
            if self._current_request_id is not None:
                raise BusyError("Another Control Plane task is active.")
            request_id = str(uuid4())
            self._tasks[request_id] = TaskRecord(
                request_id=request_id,
                workspace_name=workspace_name,
                platform=platform,
                target_id=target_id,
                mode=mode,
                source=dict(source),
            )
            self._current_request_id = request_id
            self._notify()
            return request_id

    def update_source(self, request_id: str, values: dict[str, Any]) -> None:
        with self._condition:
            task = self._require(request_id)
            task.source.update(values)
            self._notify()

    def update_case_step_result(self, request_id: str, step_id: str, result: dict[str, Any], *, source_index: int | None = None) -> None:
        if not step_id:
            return
        with self._condition:
            task = self._require(request_id)
            steps = task.source.get("caseSteps")
            if not isinstance(steps, list):
                return
            for step in steps:
                if isinstance(step, dict) and (step.get("stepId") == step_id or step.get("sourceStepId") == step_id or (source_index is not None and step.get("index") == source_index)):
                    execution_id = result.get("stepExecutionId")
                    if execution_id:
                        attempts = [item for item in step.get("attempts", []) if item.get("stepExecutionId") != execution_id]
                        attempts.append(dict(result))
                        step["attempts"] = attempts
                    step.update(result)
                    self._notify()
                    return

    def abandon_preparation(self, request_id: str) -> None:
        with self._condition:
            task = self._require(request_id)
            if task.status != "preparing":
                return
            del self._tasks[request_id]
            if self._current_request_id == request_id:
                self._current_request_id = None
            self._notify()

    def transition(self, request_id: str, status: TaskStatus, *, summary: str | None = None) -> None:
        with self._condition:
            task = self._require(request_id)
            if task.status in _TERMINAL_STATUSES:
                return
            task.status = status
            if summary is not None:
                task.summary = summary
            if status in _TERMINAL_STATUSES:
                self._mark_unfinished_strict_steps_incomplete(task)
                task.completed_at = _now()
                if self._current_request_id == request_id:
                    self._current_request_id = None
            self._notify()

    def bind_run(self, request_id: str, run_id: str, run_dir: Any | None = None) -> None:
        with self._condition:
            task = self._require(request_id)
            task.run_id = run_id
            if run_dir is not None:
                task.run_dir = run_dir
            self._notify()

    def bind_cases_dir(self, request_id: str, cases_dir: Any) -> None:
        with self._condition:
            task = self._require(request_id)
            task.cases_dir = cases_dir
            self._notify()

    def add_event(self, request_id: str, event: dict[str, Any]) -> None:
        with self._condition:
            task = self._require(request_id)
            if task.status in _TERMINAL_STATUSES:
                return
            normalized = dict(event)
            normalized["sequence"] = len(task.events) + 1
            task.events.append(normalized)
            if normalized.get("stepId"):
                task.active_step = {"stepId": normalized["stepId"], "label": normalized.get("label")}
            self._notify()

    def annotate_event_step(self, request_id: str, sequence: int, step_id: str) -> None:
        if sequence <= 0 or not step_id:
            return
        with self._condition:
            task = self._require(request_id)
            for event in task.events:
                if event.get("sequence") == sequence and event.get("stepId") != step_id:
                    event["stepId"] = step_id
                    self._notify()
                    return

    def set_artifact(self, request_id: str, kind: str, artifact: dict[str, Any]) -> None:
        with self._condition:
            task = self._require(request_id)
            if kind == "screenshot":
                if task.screenshot and task.screenshot.get("path") == artifact.get("path"):
                    return
                task.screenshot_revision += 1
                task.screenshot = {**artifact, "revision": task.screenshot_revision}
            elif kind == "ui_snapshot":
                if task.ui_snapshot and task.ui_snapshot.get("path") == artifact.get("path"):
                    return
                task.ui_snapshot_revision += 1
                task.ui_snapshot = {**artifact, "revision": task.ui_snapshot_revision}
            else:
                return
            task.evidence_available = True
            self._notify()

    def finish(self, request_id: str, *, status: TaskStatus, summary: str, result: dict[str, Any] | None = None, report_available: bool = False) -> None:
        with self._condition:
            task = self._require(request_id)
            if task.status in _TERMINAL_STATUSES:
                return
            task.result = result
            task.report_available = report_available
            self._mark_unfinished_strict_steps_incomplete(task)
            task.status = status
            task.summary = summary
            task.completed_at = _now()
            if self._current_request_id == request_id:
                self._current_request_id = None
            self._notify()

    def request_cancel(self, request_id: str) -> dict[str, Any]:
        with self._condition:
            task = self._require(request_id)
            if task.status in _TERMINAL_STATUSES:
                return self._snapshot(task)
            task.cancel_requested = True
            task.summary = "Cancellation requested."
            self._notify()
            return self._snapshot(task)

    def raise_if_cancelled(self, request_id: str) -> None:
        with self._condition:
            if self._require(request_id).cancel_requested:
                raise TaskCancelledError("Cancelled by user.")

    def is_cancel_requested(self, request_id: str) -> bool:
        with self._condition:
            return self._require(request_id).cancel_requested

    def snapshot(self, request_id: str, *, after_sequence: int = 0) -> dict[str, Any]:
        with self._condition:
            return self._snapshot(self._require(request_id), after_sequence=after_sequence)

    def bootstrap(self) -> dict[str, Any]:
        with self._condition:
            active = self._tasks.get(self._current_request_id or "")
            return {
                "apiVersion": "1.0",
                "platforms": [
                    {"id": "android", "label": "Android"},
                    {"id": "web", "label": "Web"},
                    {"id": "windows", "label": "Windows"},
                    {"id": "macos", "label": "macOS"},
                ],
                "busy": active is not None,
                "activeTask": self._summary(active) if active else None,
            }

    def wait_for_update(self, request_id: str, *, after_sequence: int, revision: int, timeout: float) -> tuple[dict[str, Any], int]:
        with self._condition:
            if self._revision == revision:
                self._condition.wait(timeout)
            return self._snapshot(self._require(request_id), after_sequence=after_sequence), self._revision

    def revision(self) -> int:
        with self._condition:
            return self._revision

    def artifact(self, request_id: str, kind: str) -> tuple[dict[str, Any] | None, str | None]:
        with self._condition:
            task = self._require(request_id)
            value = task.screenshot if kind == "screenshot" else task.ui_snapshot
            return (dict(value) if value else None), task.run_id

    def run_directory(self, request_id: str):
        with self._condition:
            return self._require(request_id).run_dir

    def cases_directory(self, request_id: str):
        with self._condition:
            return self._require(request_id).cases_dir

    def _snapshot(self, task: TaskRecord, *, after_sequence: int = 0) -> dict[str, Any]:
        events_after = 0 if task.status in _TERMINAL_STATUSES else after_sequence
        return {
            **self._summary(task),
            "source": dict(task.source),
            "startedAt": task.started_at,
            "completedAt": task.completed_at,
            "cancelRequested": task.cancel_requested,
            "events": [dict(event) for event in task.events if event["sequence"] > events_after],
            "activeStep": dict(task.active_step) if task.active_step else None,
            "result": dict(task.result) if task.result else None,
            "suggestedCaseName": (task.result or {}).get("suggestedCaseName"),
            "recordingDraft": (task.result or {}).get("recordingDraft"),
            "summary": task.summary,
            "screenshotRevision": task.screenshot_revision,
            "uiSnapshotRevision": task.ui_snapshot_revision,
            "evidenceAvailable": task.evidence_available,
            "reportAvailable": task.report_available,
            "terminal": task.status in _TERMINAL_STATUSES,
        }

    def _summary(self, task: TaskRecord) -> dict[str, Any]:
        return {
            "requestId": task.request_id,
            "runId": task.run_id,
            "workspaceName": task.workspace_name,
            "platform": task.platform,
            "targetId": task.target_id,
            "mode": task.mode,
            "status": task.status,
        }

    def _mark_unfinished_strict_steps_incomplete(self, task: TaskRecord) -> None:
        if task.mode != "strict":
            return
        steps = task.source.get("caseSteps")
        if not isinstance(steps, list):
            return
        for step in steps:
            if isinstance(step, dict) and not step.get("status"):
                step["status"] = "incomplete"
                step["message"] = "No authoritative final result is available for this action."

    def _require(self, request_id: str) -> TaskRecord:
        task = self._tasks.get(request_id)
        if task is None:
            raise RequestNotFoundError(request_id)
        return task

    def _notify(self) -> None:
        self._revision += 1
        self._condition.notify_all()


__all__ = ["BusyError", "ControlPlaneState", "RequestNotFoundError", "TaskCancelledError"]
