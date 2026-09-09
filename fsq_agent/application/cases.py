# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import re
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from fsq_agent.ai_services import build_case_suggestion_analyzer
from fsq_agent.application.contracts import (
    ApplicationError,
    ApplicationErrorCategory,
    ApplicationErrorCode,
    CaseCreateEventSink,
    CaseCreateRequest,
    CaseCreateResult,
    CaseTestRequest,
    CaseTestResult,
    WorkspaceRequest,
)
from fsq_agent.application.workspace import require_initialized_workspace
from fsq_agent.config import Settings, load_workspace_platform_settings
from fsq_agent.execution import DynamicExecutionRequest, DynamicExecutionService, RecordingService
from fsq_agent.models import ConfigurationError, Task, TaskResult


class _Agent(Protocol):
    async def run(self, task: Task, event_sink: CaseCreateEventSink | None = None, *, run_id: str) -> TaskResult: ...


SettingsLoader = Callable[[Path, str], Settings]
AgentFactory = Callable[[Settings], _Agent]


async def create_case(
    request: CaseCreateRequest,
    *,
    event_sink: CaseCreateEventSink | None = None,
    settings_loader: SettingsLoader = load_workspace_platform_settings,
    agent_factory: AgentFactory | None = None,
) -> CaseCreateResult:
    normalized_goal = " ".join(request.goal.split())
    if request.case_name is not None:
        try:
            RecordingService.validate_case_name(request.case_name)
        except ConfigurationError as exc:
            raise ApplicationError(
                code=ApplicationErrorCode.CASE_INVALID, category=ApplicationErrorCategory.REQUEST_VALIDATION, message="Invalid Case name.", action="Use a safe suffix-free Case name."
            ) from exc
    if not normalized_goal:
        raise ApplicationError(
            code=ApplicationErrorCode.CASE_GOAL_INVALID,
            category=ApplicationErrorCategory.REQUEST_VALIDATION,
            message="Goal cannot be empty.",
            action="Provide a non-empty natural-language Goal.",
        )

    workspace = require_initialized_workspace(WorkspaceRequest(current_directory=request.current_directory))
    settings = settings_loader(workspace.workspace, request.platform)
    if agent_factory is None:
        raise ApplicationError(
            code=ApplicationErrorCode.CONFIGURATION_INVALID,
            category=ApplicationErrorCategory.CONFIGURATION,
            message="A Coding Agent runtime is not configured.",
            action="Start this operation through a supported FSQ adapter.",
        )
    task = _task_from_goal(normalized_goal)
    execution = await DynamicExecutionService(agent=agent_factory(settings)).execute(
        DynamicExecutionRequest(
            task=task,
            settings=settings,
            event_sink=event_sink,
            record=True,
            case_name=request.case_name,
            publication_directory=getattr(getattr(settings, "cases", None), "dir", None),
        )
    )
    result = execution.task_result
    candidate_case_path = execution.recording.recorded_case_path if execution.recording is not None and execution.recording.status == "recorded" else None
    return CaseCreateResult(
        run_id=result.report.run_id,
        task_id=result.task_id,
        status=result.status,
        summary=result.verification.summary,
        report_path=result.report.path,
        candidate_case_path=candidate_case_path,
        case_name=execution.recording.case_name if execution.recording else None,
        published_case_path=execution.recording.published_case_path if execution.recording else None,
        publication_outcome=execution.recording.publication_outcome if execution.recording else "failed",
        warnings=list(execution.recording.warnings) if execution.recording else ["case.recording_failed"],
    )


def _task_from_goal(goal: str) -> Task:
    return Task(
        id=_goal_task_id(goal),
        name=goal,
        description=(
            "Run this natural-language goal as a goal-driven automation task. "
            "First derive ordered key actions from page knowledge, then execute them while adapting to live UI state. "
            "Final verification should judge whether the goal is complete.\n\n"
            f"Goal: {goal}"
        ),
        planning_reference_kind="goal",
        planning_reference_text=goal,
    )


def _goal_task_id(goal: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", goal.casefold()).strip("-")
    return slug[:80] or "goal-task"


from fsq_agent.application._case_test import execute_case_test


def test_case(request: CaseTestRequest) -> CaseTestResult:
    """Execute the canonical Case testing use case through its private strict helper."""
    return execute_case_test(request, suggestion_analyzer_factory=build_case_suggestion_analyzer)


__all__ = ["create_case", "test_case"]

from fsq_agent.application._case_format import format_case
from fsq_agent.application.contracts.cases import CaseFormatDiagnostic, CaseFormatRequest, CaseFormatResult, CaseSaveRequest, CaseSaveResult


def save_recorded_case(request: CaseSaveRequest) -> CaseSaveResult:
    import json

    from fsq_agent.execution import publish_recorded_case

    draft = False
    try:
        metadata = json.loads(request.candidate_path.with_name("recording.json").read_text())
        if not isinstance(metadata, dict) or not isinstance(metadata.get("draft"), bool):
            raise ConfigurationError("Invalid recording metadata.")
        draft = metadata["draft"]
    except FileNotFoundError:
        pass
    except (OSError, ValueError) as exc:
        raise ConfigurationError("Unable to read recording metadata.") from exc
    path, outcome = publish_recorded_case(candidate_path=request.candidate_path, destination_directory=request.destination_directory, platform=request.platform, case_name=request.case_name)
    return CaseSaveResult(path=path, outcome=outcome, draft=draft)


__all__ += ["CaseFormatDiagnostic", "CaseFormatRequest", "CaseFormatResult", "CaseSaveRequest", "CaseSaveResult", "format_case", "save_recorded_case"]
