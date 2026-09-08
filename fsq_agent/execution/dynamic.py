# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from fsq_agent.config import list_workspace_registry

from .recording import RecordingResult, RecordingService
from .runs import RunArtifactIndex, RunResultSummary, RunRuntime, RunSource, allocate_run, transition_run

if TYPE_CHECKING:
    from collections.abc import Callable

    from fsq_agent.config import Settings
    from fsq_agent.models import RunEventSink, Task, TaskResult


class DynamicAgent(Protocol):
    async def run(self, task: Task, event_sink: RunEventSink | None = None, *, run_id: str) -> TaskResult: ...


@dataclass(frozen=True)
class DynamicExecutionRequest:
    task: Task
    settings: Settings
    event_sink: RunEventSink | None = None
    record: bool = False
    allow_recording_failure: bool = False
    publication_directory: Path | None = None
    cancellation_check: Callable[[], None] | None = None
    report_coordinator: Callable[[TaskResult], None] | None = None
    recording_error_sink: Callable[[Exception], None] | None = None


@dataclass(frozen=True)
class DynamicExecutionResult:
    task_result: TaskResult
    recording: RecordingResult | None = None


class DynamicExecutionService:
    def __init__(self, *, agent: DynamicAgent, recording_service: RecordingService | None = None) -> None:
        self._agent = agent
        self._recording_service = recording_service or RecordingService()

    async def execute(self, request: DynamicExecutionRequest) -> DynamicExecutionResult:
        if request.cancellation_check is not None:
            request.cancellation_check()
        settings = request.settings
        workspace = Path(settings.workspace.root_dir or Path(settings.output.runs_dir).parent.parent.parent)
        private_values = tuple(sorted(set(settings.runtime_secrets.private_values().values()), key=len, reverse=True))
        safe_source_id = _redact_metadata_text(request.task.id, private_values, workspace)
        for value in private_values:
            slug_value = re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")
            if slug_value:
                safe_source_id = re.sub(re.escape(slug_value), "***", safe_source_id, flags=re.IGNORECASE)
        metadata = allocate_run(
            workspace=workspace,
            workspace_name=_workspace_name(workspace, settings.agent_runtime.user_config_root),
            platform=settings.harness.platform,
            source_id=safe_source_id,
            mode="explore",
            source=RunSource(kind="goal", goal_summary=_redact_metadata_text(request.task.name, private_values, workspace)[:200]),
            platform_runs_dir=Path(settings.output.runs_dir),
        )
        run_dir = Path(settings.output.runs_dir) / metadata.run_id
        checking_cancellation = False
        try:
            metadata = transition_run(run_dir, metadata, "running")
            result = await self._agent.run(request.task, event_sink=request.event_sink, run_id=metadata.run_id)
            _validate_result_identity(result, metadata.run_id)
            if request.cancellation_check is not None:
                checking_cancellation = True
                request.cancellation_check()
                checking_cancellation = False
            metadata = transition_run(run_dir, metadata, "finalizing")
            if request.report_coordinator is not None:
                request.report_coordinator(result)
            transition_run(
                run_dir,
                metadata,
                result.status if result.status in {"success", "failed", "inconclusive"} else "error",
                result=RunResultSummary(summary=_redact_metadata_text(result.verification.summary, private_values, workspace)[:2000]),
                runtime=RunRuntime(provider=settings.agent_runtime.provider, model=settings.agent_runtime.model),
                artifacts=RunArtifactIndex(
                    report=result.report.path.name if result.report.format == "json" else result.report.path.with_suffix(".json").name if result.report.format == "markdown" else None,
                    report_markdown=result.report.path.name if result.report.format == "markdown" else None,
                    html_report=result.report.path.name if result.report.format == "html" else None,
                    events="events.jsonl",
                    evidence_manifest=result.report.evidence_manifest_path.name if result.report.evidence_manifest_path else None,
                ),
            )
        except BaseException as error:
            status = "cancelled" if checking_cancellation or isinstance(error, asyncio.CancelledError | KeyboardInterrupt) else "error"
            _best_effort_fail_run(run_dir, metadata, status)
            raise
        recording = None
        if request.record:
            try:
                recording = self._recording_service.record(
                    run_dir=run_dir,
                    task=request.task,
                    result=result,
                    settings=request.settings,
                    allow_failure=request.allow_recording_failure,
                    publication_directory=request.publication_directory,
                )
            except Exception as exc:  # noqa: BLE001 - recording never changes completed execution status.
                if request.recording_error_sink is not None:
                    request.recording_error_sink(exc)
                recording = None
        return DynamicExecutionResult(task_result=result, recording=recording)


def _redact_metadata_text(text: str, private_values: tuple[str, ...], workspace: Path) -> str:
    roots = {str(workspace.resolve()), workspace.resolve().as_posix()}
    if workspace.is_absolute():
        roots.update((str(workspace), workspace.as_posix()))
    roots.update(root.replace("\\", "\\\\") for root in tuple(roots))
    for root in sorted(roots, key=len, reverse=True):
        text = re.sub(re.escape(root), "<workspace>", text, flags=re.IGNORECASE if workspace.drive else 0)
    for value in private_values:
        if value:
            text = text.replace(value, "***")
    return text


def _validate_result_identity(result: TaskResult, run_id: str) -> None:
    if result.report.run_id != run_id:
        raise ValueError("Agent report Run ID does not match the allocated Run ID.")


def _best_effort_fail_run(run_dir, metadata, status: str) -> None:
    try:
        transition_run(run_dir, metadata, status)
    except Exception:  # noqa: BLE001, S110 - preserve the original execution failure.
        pass


def _workspace_name(workspace_root: Path, user_config_root: Path | None) -> str:
    resolved = workspace_root.resolve()
    entry = next((item for item in list_workspace_registry(user_config_root=user_config_root) if item.root_path.resolve() == resolved), None)
    return entry.name if entry is not None else workspace_root.name
