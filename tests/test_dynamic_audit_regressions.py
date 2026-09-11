# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import asyncio

import pytest

from fsq_agent.config import Settings
from fsq_agent.execution import DynamicExecutionRequest, DynamicExecutionService
from fsq_agent.models import ConfigurationError, DynamicAgentOutcome, Task, VerificationResult


class _ContextAgent:
    def __init__(self, callback):
        self.callback = callback

    async def run_in_context(self, task, context, event_sink=None, *, evidence_sink=None, cancellation_check=None):
        self.callback()
        return DynamicAgentOutcome(task=task, steps=[], verification=VerificationResult(status="success", summary="Done"), duration_ms=1)


@pytest.mark.asyncio
async def test_dynamic_cancel_after_runtime_cannot_freeze_success(tmp_path):
    settings = Settings()
    settings.workspace.root_dir = tmp_path
    settings.output.runs_dir = tmp_path / ".fsq/runs/web"
    settings.harness.platform = "web"
    cancelled = False

    def set_cancel():
        nonlocal cancelled
        cancelled = True

    def check():
        if cancelled:
            raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        await DynamicExecutionService(agent=_ContextAgent(set_cancel)).execute(DynamicExecutionRequest(task=Task(description="short"), settings=settings, cancellation_check=check))
    from fsq_agent.execution import RunLifecycleService

    run = next(settings.output.runs_dir.iterdir())
    assert RunLifecycleService.load_result(run).outcome == "cancelled"


@pytest.mark.asyncio
async def test_unscoped_legacy_agent_is_rejected_before_actions(tmp_path):
    invoked = []

    class UnsafeAgent:
        async def run(self, *args, **kwargs):
            invoked.append(True)

    with pytest.raises(ConfigurationError, match="run_in_context"):
        await DynamicExecutionService(agent=UnsafeAgent()).execute(DynamicExecutionRequest(task=Task(description="no action"), settings=Settings()))
    assert invoked == []


@pytest.mark.asyncio
async def test_dynamic_source_snapshot_failure_retains_allocated_identity(tmp_path, monkeypatch):
    from fsq_agent.execution import RunLifecycleService
    from fsq_agent.models import ToolExecutionError

    settings = Settings()
    settings.workspace.root_dir = tmp_path
    settings.output.runs_dir = tmp_path / ".fsq/runs/web"
    settings.harness.platform = "web"
    monkeypatch.setattr(RunLifecycleService, "snapshot_sources", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("disk failed")))
    with pytest.raises(ToolExecutionError) as error:
        await DynamicExecutionService(agent=_ContextAgent(lambda: None)).execute(DynamicExecutionRequest(task=Task(description="no UI"), settings=settings))
    assert error.value.context["run_id"]
    assert error.value.context["platform"] == "web"


@pytest.mark.asyncio
async def test_post_freeze_report_interruption_preserves_result_and_processing(tmp_path, monkeypatch):
    from fsq_agent.execution import RunLifecycleService, dynamic, load_run_metadata
    from fsq_agent.models import ExecutableStep, RunnerEvent, RunnerStepResult

    class CompleteAgent:
        async def run_in_context(self, task, context, event_sink=None, *, evidence_sink=None, **kwargs):
            step = evidence_sink.allocate_step_identity(ExecutableStep(step_id="one", kind="observation", action_name="observe", params={}))
            evidence_sink.record_event(
                RunnerEvent(
                    run_id=context.run_id,
                    event_type="step_start",
                    step_id=step.step_id,
                    source_step_id=step.source_step_id,
                    step_execution_id=step.step_execution_id,
                    invocation_path=step.invocation_path,
                )
            )
            evidence_sink.record_step_result(
                RunnerStepResult(step_id=step.step_id, source_step_id=step.source_step_id, step_execution_id=step.step_execution_id, invocation_path=step.invocation_path, status="passed")
            )
            return DynamicAgentOutcome(task=task, steps=[], verification=VerificationResult(status="success", summary="Observed"))

    settings = Settings()
    settings.workspace.root_dir = tmp_path
    settings.output.runs_dir = tmp_path / ".fsq/runs/web"
    settings.harness.platform = "web"
    monkeypatch.setattr(dynamic.ReportGenerator, "generate", lambda *args, **kwargs: (_ for _ in ()).throw(KeyboardInterrupt()))
    with pytest.raises(KeyboardInterrupt):
        await DynamicExecutionService(agent=CompleteAgent()).execute(DynamicExecutionRequest(task=Task(description="observed"), settings=settings))
    root = next(settings.output.runs_dir.iterdir())
    assert RunLifecycleService.load_result(root).outcome == "success"
    assert load_run_metadata(root).processing["report"]["status"] == "cancelled"
