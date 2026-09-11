# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from fsq_agent import FsqAgent, Task
from fsq_agent.adapters.coding_agent._runtime import DefaultCodingAgentRuntime, _ToolOutputBudgetFilter
from fsq_agent.agent import Verifier
from fsq_agent.agent_engine import ToolCall, ToolOutputEntry
from fsq_agent.config import Settings
from fsq_agent.execution import DynamicExecutionRequest, DynamicExecutionService
from fsq_agent.models import GoalPrePlan, KnowledgeBundle, ReportArtifact, RunEvent, RunExecutionContext, SkillBundle, StepResult, ToolExecutionError
from fsq_agent.observation import ExecutionLogger


def _coordinate(agent):
    async def coordinator(task, event_sink=None):
        result = await DynamicExecutionService(agent=agent).execute(DynamicExecutionRequest(task=task, settings=agent.settings, event_sink=event_sink))
        return result.task_result

    return SimpleNamespace(run=coordinator)


async def _run_in_context(agent, task, run_id, event_sink=None):
    from fsq_agent.core.evidence import EvidenceRecorder

    run_dir = agent.settings.output.runs_dir / run_id
    return await agent.run_in_context(
        task,
        RunExecutionContext(run_id=run_id, run_dir=run_dir, platform=agent.settings.harness.platform),
        event_sink,
        evidence_sink=EvidenceRecorder(run_id=run_id, output_dir=run_dir),
    )


@pytest.mark.asyncio
async def test_agent_run_requires_configured_model_provider_auth(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
    task = Task(
        id="smoke",
        name="Smoke",
        description="Record a smoke test task.",
        acceptance_criteria=["A report exists."],
    )

    settings = Settings.model_validate(
        {
            "agent_runtime": {"provider": "azure_openai"},
            "output": {"root_dir": tmp_path / "output"},
        }
    )
    settings.output.runs_dir = tmp_path / "runs"
    with pytest.raises(ToolExecutionError, match="Run execution failed") as error:
        await _coordinate(FsqAgent.from_settings(settings, lambda configured, *, harness_factory=None: DefaultCodingAgentRuntime(configured, object()))).run(task)
    assert error.value.context["run_id"]
    assert error.value.context["exception_type"] == "ConfigurationError"


class _KnowledgeLoader:
    def load_for_task(self, task: Task) -> KnowledgeBundle:
        return KnowledgeBundle()


class _SkillLoader:
    def load(self, skills: list[object]) -> list[object]:
        return []


class _ConfiguredSkillLoader:
    def __init__(self, bundles: list[SkillBundle]) -> None:
        self.bundles = bundles

    def load(self, skills: list[object]) -> list[object]:
        return list(self.bundles)


def _settings_with_knowledge(
    knowledge_dir: Path,
    pre_plan_dir: Path | None = None,
    runs_dir: Path | None = None,
) -> Settings:
    knowledge: dict[str, object] = {"root_dir": knowledge_dir}
    if pre_plan_dir is not None:
        knowledge["pre_plan"] = {"dir": pre_plan_dir}
    settings = Settings(agent_context={"knowledge": knowledge})
    if runs_dir is not None:
        settings.output.runs_dir = runs_dir
    return settings


def _record_test_evidence(context):
    from fsq_agent.models import ExecutableStep, RunnerEvent, RunnerStepResult

    sink = context.get("evidence_sink")
    allocated = context.get("context")
    if sink is not None and allocated is not None:
        step = sink.allocate_step_identity(ExecutableStep(step_id="observed", action_name="observe", kind="observation", params={}))
        sink.record_event(
            RunnerEvent(
                run_id=allocated.run_id,
                event_type="step_start",
                step_id=step.step_id,
                source_step_id=step.source_step_id,
                step_execution_id=step.step_execution_id,
                invocation_path=step.invocation_path,
            )
        )
        sink.record_step_result(
            RunnerStepResult(step_id=step.step_id, source_step_id=step.source_step_id, step_execution_id=step.step_execution_id, invocation_path=step.invocation_path, status="passed")
        )


class _Runtime:
    def __init__(self) -> None:
        self.last_task: Task | None = None

    async def run_task(
        self,
        task: Task,
        knowledge: KnowledgeBundle,
        skills: list[object],
        run_id: str,
        event_sink: object | None = None,
        **execution_context,
    ) -> list[StepResult]:
        self.last_task = task
        _record_test_evidence(execution_context)
        return [
            StepResult(
                step_id=1,
                status="success",
                actual_outcome='{"status":"success","summary":"Done","pre_plan":[],"plan_updates":[],"satisfied_criteria":["A report exists."],"unmet_criteria":[],"evidence":["Report generated"],"errors":[]}',
                tool_name="agent_runtime.runner",
            )
        ]

    async def run_verification(
        self,
        task: Task,
        results: list[StepResult],
        run_id: str,
        events_path: Path | None,
        event_sink: object | None = None,
        **execution_context,
    ) -> list[StepResult]:
        return []


class _GoalRunRuntime(_Runtime):
    def __init__(self) -> None:
        super().__init__()
        self.pre_plan_goal: str | None = None
        self.pre_plan_reference_type: str | None = None
        self.pre_plan_knowledge: KnowledgeBundle | None = None
        self.pre_plan_skills: list[object] | None = None

    async def run_pre_plan(
        self,
        reference_text: str,
        knowledge: KnowledgeBundle,
        skills: list[object],
        run_id: str,
        event_sink: object | None = None,
        reference_type: str = "goal",
    ) -> GoalPrePlan:
        self.pre_plan_goal = reference_text
        self.pre_plan_reference_type = reference_type
        self.pre_plan_knowledge = knowledge
        self.pre_plan_skills = skills
        return GoalPrePlan(
            goal=reference_text,
            verification_goal=f"Verify planned outcome for {reference_type}: {reference_text.splitlines()[0]}",
            summary="Generated execution actions.",
            relevant_page_ids=["edge_android_new_tab_page"],
            key_actions=[
                {"step_id": 1, "action": "Open the overflow menu."},
                {"step_id": 2, "action": "Tap Downloads."},
            ],
        )

    async def run_task(
        self,
        task: Task,
        knowledge: KnowledgeBundle,
        skills: list[object],
        run_id: str,
        event_sink: object | None = None,
        **execution_context,
    ) -> list[StepResult]:
        self.last_task = task
        _record_test_evidence(execution_context)
        satisfied = task.verification_goal or "Verification goal missing."
        return [
            StepResult(
                step_id=1,
                status="success",
                actual_outcome=(
                    f'{{"status":"success","summary":"Goal done","pre_plan":[],"plan_updates":[],"satisfied_criteria":["{satisfied}"],"unmet_criteria":[],"evidence":["Goal observed"],"errors":[]}}'
                ),
                tool_name="agent_runtime.runner",
            )
        ]


class _CancelledRuntime:
    async def run_task(
        self,
        task: Task,
        knowledge: KnowledgeBundle,
        skills: list[object],
        run_id: str,
        event_sink: object | None = None,
        **execution_context,
    ) -> list[StepResult]:
        raise asyncio.CancelledError()

    async def run_verification(
        self,
        task: Task,
        results: list[StepResult],
        run_id: str,
        events_path: Path | None,
        event_sink: object | None = None,
        **execution_context,
    ) -> list[StepResult]:
        return []


class _FakeArtifactStore:
    def __init__(self) -> None:
        self.writes: list[tuple[str, str, dict[str, Any]]] = []

    def write(self, tool_name: str, output_text: str, metadata: dict[str, Any]) -> Path:
        self.writes.append((tool_name, output_text, metadata))
        return Path("artifact.json")


class _Reporter:
    def __init__(self) -> None:
        self.run_ids: list[str] = []

    def generate(self, run_id: str, task: Task, steps: list[StepResult], verification: object) -> ReportArtifact:
        self.run_ids.append(run_id)
        return ReportArtifact(run_id=run_id, path=Path("report.md"))


class _RefreshSession:
    def close_sync(self) -> None:
        pass


def _stub_provider_refresh(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("fsq_agent.agent._core.refresh_model_provider_session", lambda settings: _RefreshSession())


@pytest.mark.asyncio
async def test_agent_uses_supplied_run_ids_without_managing_metadata(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_provider_refresh(monkeypatch)
    reporter = _Reporter()
    settings = Settings()
    settings.output.runs_dir = tmp_path / "runs"
    agent = FsqAgent(
        settings,
        verifier=Verifier(),
        reporter=reporter,  # type: ignore[arg-type]
        knowledge_loader=_KnowledgeLoader(),  # type: ignore[arg-type]
        skill_loader=_SkillLoader(),  # type: ignore[arg-type]
        runtime=_Runtime(),  # type: ignore[arg-type]
    )
    task = Task(
        id="smoke",
        name="Smoke",
        description="Record a smoke test task.",
        acceptance_criteria=["A report exists."],
        key_actions=["Key action 1: Record report existence."],
        verification_goal="A report exists.",
    )

    outcome = await _run_in_context(agent, task, "supplied-run-1")
    second_outcome = await _run_in_context(agent, task, "supplied-run-2")
    assert outcome.task.id == second_outcome.task.id == task.id
    assert reporter.run_ids == []
    assert not (settings.output.runs_dir / "supplied-run-1" / "run.json").exists()
    assert not (settings.output.runs_dir / "supplied-run-2" / "run.json").exists()
    result = await _coordinate(agent).run(task)
    second_result = await _coordinate(agent).run(task)

    assert result.report.path.is_file()
    assert second_result.report.path.is_file()
    assert result.report.run_id != second_result.report.run_id
    assert result.report.run_id.startswith("smoke-")
    assert (settings.output.runs_dir / result.report.run_id / "run.json").is_file()
    assert (settings.output.runs_dir / second_result.report.run_id / "run.json").is_file()


@pytest.mark.parametrize("run_id", ["", " ", ".", "..", "../outside", "nested/run", "nested\\run", "C:relative", "bad\x00name"])
async def test_agent_rejects_invalid_run_identity_before_side_effects(run_id: str) -> None:
    agent = FsqAgent(Settings(), Verifier(), _Reporter(), _KnowledgeLoader(), _SkillLoader(), _Runtime())
    context = RunExecutionContext(run_id=run_id, run_dir=Path("unused"), platform="android")
    with pytest.raises(ValueError, match="Run ID"):
        await agent.run_in_context(Task(description="test"), context)


def test_tool_output_budget_filter_artifacts_ordinary_sensitive_markers() -> None:
    artifact_store = _FakeArtifactStore()
    input_filter = _ToolOutputBudgetFilter(
        recent_inline_outputs=0,
        max_output_chars=1,
        max_total_inline_chars=1,
        artifact_store=artifact_store,  # type: ignore[arg-type]
    )

    entry = ToolOutputEntry(1, "call-1", "secret_debug_tool", '{"type":"runtime_secret","name":"TEST_ACCOUNT_PASSWORD","value":"secret","sensitive":true}')
    path = input_filter._artifact_path_for(entry)

    assert path == "artifact.json"
    assert artifact_store.writes == [("secret_debug_tool", entry.output, {"source": "model_input_filter", "call_id": "call-1"})]


def test_tool_output_budget_filter_references_sensitive_history_without_preview() -> None:
    artifact_store = _FakeArtifactStore()
    input_filter = _ToolOutputBudgetFilter(
        recent_inline_outputs=0,
        max_output_chars=1,
        max_total_inline_chars=1,
        artifact_store=artifact_store,  # type: ignore[arg-type]
    )
    output = (
        '{"tool_name":"secret_debug_tool","model_output":"full","result":'
        '{"tool_name":"secret_debug_tool","status":"success","output":'
        '{"type":"runtime_secret","name":"TEST_ACCOUNT_PASSWORD","value":"secret-password","sensitive":true},'
        '"sensitive":true}}'
    )
    filtered = input_filter((ToolOutputEntry(1, "call-1", "secret_debug_tool", output),))

    assert artifact_store.writes == [("secret_debug_tool", output, {"source": "model_input_filter", "call_id": "call-1"})]
    assert "secret-password" not in filtered[1]
    assert filtered[1] == f"[Historical secret_debug_tool output stored as artifact. Artifact path: artifact.json. Content chars: {len(output)}.]"


def test_tool_output_budget_filter_keeps_recent_sensitive_marker_inline() -> None:
    artifact_store = _FakeArtifactStore()
    input_filter = _ToolOutputBudgetFilter(
        recent_inline_outputs=1,
        max_output_chars=100000,
        max_total_inline_chars=100000,
        artifact_store=artifact_store,  # type: ignore[arg-type]
    )
    output = (
        '{"tool_name":"secret_debug_tool","model_output":"full","result":'
        '{"tool_name":"secret_debug_tool","status":"success","output":'
        '{"type":"runtime_secret","name":"TEST_ACCOUNT_PASSWORD","value":"secret-password","sensitive":true},'
        '"sensitive":true}}'
    )
    filtered = input_filter((ToolOutputEntry(1, "call-1", "secret_debug_tool", output),))

    assert artifact_store.writes == []
    assert filtered == {}


@pytest.mark.asyncio
async def test_agent_run_emits_and_persists_live_events(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_provider_refresh(monkeypatch)
    reporter = _Reporter()
    events: list[RunEvent] = []
    settings = Settings()
    settings.output.runs_dir = tmp_path / "runs"
    agent = FsqAgent(
        settings,
        verifier=Verifier(),
        reporter=reporter,  # type: ignore[arg-type]
        knowledge_loader=_KnowledgeLoader(),  # type: ignore[arg-type]
        skill_loader=_SkillLoader(),  # type: ignore[arg-type]
        runtime=_Runtime(),  # type: ignore[arg-type]
        event_logger=ExecutionLogger(tmp_path),
    )
    task = Task(
        id="smoke",
        name="Smoke",
        description="Record a smoke test task.",
        acceptance_criteria=["A report exists."],
        key_actions=["Key action 1: Record report existence."],
        verification_goal="A report exists.",
    )

    result = await _coordinate(agent).run(task, event_sink=events.append)

    assert [event.type for event in events] == ["run_started", "agent_started", "run_completed"]
    assert [event.sequence for event in events] == [1, 2, 3]
    timeline_path = tmp_path / result.report.run_id / "events.jsonl"
    assert timeline_path.exists()
    assert "agent_started" in timeline_path.read_text(encoding="utf-8")


async def test_agent_events_redact_configured_values_before_persistence_and_dispatch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_provider_refresh(monkeypatch)
    settings = Settings()
    settings.output.runs_dir = tmp_path / "runs"
    private_value = "private-value-2617"
    settings.runtime_secrets.set_values({"TEST_ACCOUNT_PASSWORD": private_value})
    runtime = _Runtime()
    events: list[RunEvent] = []
    agent = FsqAgent(settings, Verifier(), _Reporter(), _KnowledgeLoader(), _SkillLoader(), runtime, ExecutionLogger(tmp_path))
    task = Task(id=f"task-{private_value}", name=f"Check {private_value}", description="Task", key_actions=["Inspect"], verification_goal="Inspection complete.")

    await _run_in_context(agent, task, "private-events-run", events.append)

    assert runtime.last_task.name == f"Check {private_value}"
    assert private_value not in (tmp_path / "private-events-run" / "events.jsonl").read_text(encoding="utf-8")
    assert all(private_value not in event.model_dump_json() for event in events)
    assert events[0].message == "Check [REDACTED]"


@pytest.mark.parametrize("notification", ["logger", "sink"])
@pytest.mark.parametrize("cancelled", [False, True])
async def test_failure_notification_cannot_replace_primary_exception_or_run_status(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, notification: str, cancelled: bool) -> None:
    from fsq_agent.execution import DynamicExecutionRequest, DynamicExecutionService, load_run_metadata

    _stub_provider_refresh(monkeypatch)
    primary = asyncio.CancelledError("primary cancellation") if cancelled else RuntimeError("primary execution failure")
    run_ids = []

    class FailedRuntime(_Runtime):
        async def run_task(self, task, knowledge, skills, run_id, event_sink=None, **execution_context):
            run_ids.append(run_id)
            raise primary

    class FailedLogger:
        def write_run_event(self, event: RunEvent) -> None:
            if notification == "logger" and event.type == "run_failed":
                raise OSError("secondary log failure")

    def event_sink(event: RunEvent) -> None:
        if notification == "sink" and event.type == "run_failed":
            raise OSError("secondary sink failure")

    settings = Settings()
    settings.workspace.root_dir = tmp_path
    settings.output.runs_dir = tmp_path / "runs"
    agent = FsqAgent(settings, Verifier(), _Reporter(), _KnowledgeLoader(), _SkillLoader(), FailedRuntime(), FailedLogger())
    task = Task(description="Task", key_actions=["Inspect"], verification_goal="Inspection complete.")
    with pytest.raises(asyncio.CancelledError if cancelled else ToolExecutionError) as failure:
        await DynamicExecutionService(agent=agent).execute(DynamicExecutionRequest(task=task, settings=settings, event_sink=event_sink))
    assert (failure.value if cancelled else failure.value.__cause__) is primary
    assert load_run_metadata(settings.output.runs_dir / run_ids[0]).status == ("cancelled" if cancelled else "error")


@pytest.mark.parametrize("cancelled", [False, True])
async def test_initial_event_failure_is_recorded_and_execution_keeps_original_status(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cancelled: bool) -> None:
    from fsq_agent.execution import DynamicExecutionRequest, DynamicExecutionService, load_run_metadata

    entered = asyncio.Event()
    wait_forever = asyncio.Event()
    events: list[RunEvent] = []
    refresh_calls = []
    primary = RuntimeError("Initial event dispatch failed")

    async def event_sink(event: RunEvent) -> None:
        events.append(event)
        if event.type == "run_started":
            entered.set()
            if cancelled:
                await wait_forever.wait()
            else:
                raise primary

    monkeypatch.setattr("fsq_agent.agent._core.refresh_model_provider_session", refresh_calls.append)
    settings = Settings()
    settings.workspace.root_dir = tmp_path
    settings.output.runs_dir = tmp_path / "runs"
    runtime = _Runtime()
    agent = FsqAgent(settings, Verifier(), _Reporter(), _KnowledgeLoader(), _SkillLoader(), runtime, ExecutionLogger(settings.output.runs_dir))
    task = Task(description="Task", key_actions=["Inspect"], verification_goal="Inspection complete.")
    execution = asyncio.create_task(DynamicExecutionService(agent=agent).execute(DynamicExecutionRequest(task=task, settings=settings, event_sink=event_sink)))
    try:
        await asyncio.wait_for(entered.wait(), timeout=5)
        if cancelled:
            execution.cancel("Initial event dispatch cancelled")
        with pytest.raises(asyncio.CancelledError if cancelled else ToolExecutionError) as failure:
            await asyncio.wait_for(execution, timeout=5)
        if not cancelled:
            assert failure.value.__cause__ is primary
            assert failure.value.context["run_id"] == events[0].run_id
    finally:
        if not execution.done():
            execution.cancel()
        await asyncio.gather(execution, return_exceptions=True)

    assert [event.type for event in events] == ["run_started", "run_failed"]
    assert [event.sequence for event in events] == [1, 2]
    directory = settings.output.runs_dir / events[0].run_id
    timeline = [json.loads(line) for line in (directory / "events.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [event["type"] for event in timeline] == ["run_started", "run_failed"]
    assert load_run_metadata(directory).status == ("cancelled" if cancelled else "error")
    assert runtime.last_task is None
    assert refresh_calls == []


@pytest.mark.asyncio
async def test_agent_run_persists_run_failed_for_cancellation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_provider_refresh(monkeypatch)
    events: list[RunEvent] = []
    settings = Settings()
    settings.output.runs_dir = tmp_path / "runs"
    agent = FsqAgent(
        settings,
        verifier=Verifier(),
        reporter=_Reporter(),  # type: ignore[arg-type]
        knowledge_loader=_KnowledgeLoader(),  # type: ignore[arg-type]
        skill_loader=_SkillLoader(),  # type: ignore[arg-type]
        runtime=_CancelledRuntime(),  # type: ignore[arg-type]
        event_logger=ExecutionLogger(tmp_path),
    )
    task = Task(
        id="smoke",
        name="Smoke",
        description="Record a smoke test task.",
        key_actions=["Key action 1: Start smoke task."],
        verification_goal="Start smoke task.",
    )

    with pytest.raises(asyncio.CancelledError):
        await _coordinate(agent).run(task, event_sink=events.append)

    assert [event.type for event in events] == ["run_started", "agent_started", "run_failed"]
    run_files = list(settings.output.runs_dir.glob("*/run.json"))
    assert json.loads(run_files[0].read_text())["status"] == "cancelled"
    assert json.loads((run_files[0].parent / "execution-result.json").read_text())["outcome"] == "cancelled"
    timeline_paths = await asyncio.to_thread(lambda: list(tmp_path.glob("smoke-*/events.jsonl")))
    assert len(timeline_paths) == 1
    timeline = await asyncio.to_thread(timeline_paths[0].read_text, encoding="utf-8")
    assert "run_failed" in timeline
    assert "CancelledError" in timeline


@pytest.mark.asyncio
async def test_agent_run_preplans_goal_only_task_before_execution(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_provider_refresh(monkeypatch)
    knowledge_dir = tmp_path / "knowledge"
    knowledge_dir.mkdir(parents=True)
    (knowledge_dir / "project.md").write_text("# Project Knowledge", encoding="utf-8")
    runtime = _GoalRunRuntime()
    events: list[RunEvent] = []
    agent = FsqAgent(
        _settings_with_knowledge(knowledge_dir, runs_dir=tmp_path / "runs"),
        verifier=Verifier(),
        reporter=_Reporter(),  # type: ignore[arg-type]
        knowledge_loader=_KnowledgeLoader(),  # type: ignore[arg-type]
        skill_loader=_SkillLoader(),  # type: ignore[arg-type]
        runtime=runtime,  # type: ignore[arg-type]
    )
    task = Task(
        id="downloads",
        name="Access Downloads",
        description="Access Downloads through the overflow menu.",
        verification_goal="Goal completed: Access Downloads",
        acceptance_criteria=["Goal completed: Access Downloads"],
    )

    result = await _coordinate(agent).run(task, event_sink=events.append)

    assert result.status == "success"
    assert runtime.pre_plan_goal == "Access Downloads"
    assert runtime.pre_plan_reference_type == "unknown"
    assert runtime.pre_plan_knowledge is not None
    assert runtime.pre_plan_knowledge.items == {"project.md": "# Project Knowledge"}
    assert runtime.pre_plan_knowledge.warnings == []
    assert runtime.pre_plan_skills == []
    assert runtime.last_task is not None
    assert runtime.last_task.key_actions == [
        "Key action 1: Open the overflow menu.",
        "Key action 2: Tap Downloads.",
    ]
    assert runtime.last_task.verification_goal == "Verify planned outcome for unknown: Access Downloads"
    assert any(event.title == "Goal pre-plan injected" for event in events)


@pytest.mark.asyncio
async def test_agent_run_refreshes_provider_before_pre_plan(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    knowledge_dir = tmp_path / "knowledge"
    knowledge_dir.mkdir(parents=True)
    (knowledge_dir / "project.md").write_text("# Project Knowledge", encoding="utf-8")
    runtime = _GoalRunRuntime()
    calls: list[str] = []

    def fake_refresh_model_provider_session(settings: Settings) -> _RefreshSession:
        calls.append("refresh")
        session = _RefreshSession()
        session.close_sync = lambda: calls.append("refresh_closed")  # type: ignore[method-assign]
        return session

    original_run_pre_plan = runtime.run_pre_plan

    async def recording_run_pre_plan(*args, **kwargs):
        calls.append("pre_plan")
        return await original_run_pre_plan(*args, **kwargs)

    runtime.run_pre_plan = recording_run_pre_plan  # type: ignore[method-assign]
    monkeypatch.setattr("fsq_agent.agent._core.refresh_model_provider_session", fake_refresh_model_provider_session)
    agent = FsqAgent(
        _settings_with_knowledge(knowledge_dir, runs_dir=tmp_path / "runs"),
        verifier=Verifier(),
        reporter=_Reporter(),  # type: ignore[arg-type]
        knowledge_loader=_KnowledgeLoader(),  # type: ignore[arg-type]
        skill_loader=_SkillLoader(),  # type: ignore[arg-type]
        runtime=runtime,  # type: ignore[arg-type]
    )
    task = Task(
        id="downloads",
        name="Access Downloads",
        description="Access Downloads through the overflow menu.",
        verification_goal="Goal completed: Access Downloads",
        acceptance_criteria=["Goal completed: Access Downloads"],
    )

    result = await _coordinate(agent).run(task)

    assert result.status == "success"
    assert calls[:3] == ["refresh", "refresh_closed", "pre_plan"]
    assert calls.count("refresh") == 1


@pytest.mark.asyncio
async def test_agent_pre_plan_receives_loaded_configured_skills(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_provider_refresh(monkeypatch)
    knowledge_dir = tmp_path / "knowledge"
    knowledge_dir.mkdir(parents=True)
    runtime = _GoalRunRuntime()
    skill = SkillBundle(
        name="automation-basics",
        description="Semantic action guidance.",
        kind="markdown",
        instructions="Prefer semantic actions.",
    )
    agent = FsqAgent(
        _settings_with_knowledge(knowledge_dir, runs_dir=tmp_path / "runs"),
        verifier=Verifier(),
        reporter=_Reporter(),  # type: ignore[arg-type]
        knowledge_loader=_KnowledgeLoader(),  # type: ignore[arg-type]
        skill_loader=_ConfiguredSkillLoader([skill]),  # type: ignore[arg-type]
        runtime=runtime,  # type: ignore[arg-type]
    )
    task = Task(
        id="downloads",
        name="Access Downloads",
        description="Access Downloads through the overflow menu.",
        verification_goal="Goal completed: Access Downloads",
    )

    result = await _coordinate(agent).run(task)

    assert result.status == "success"
    assert runtime.pre_plan_skills == [skill]


@pytest.mark.asyncio
async def test_agent_pre_plan_uses_explicit_raw_case_planning_reference(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_provider_refresh(monkeypatch)
    knowledge_dir = tmp_path / "knowledge"
    knowledge_dir.mkdir(parents=True)
    (knowledge_dir / "project.md").write_text("# Project Knowledge", encoding="utf-8")
    runtime = _GoalRunRuntime()
    agent = FsqAgent(
        _settings_with_knowledge(knowledge_dir, runs_dir=tmp_path / "runs"),
        verifier=Verifier(),
        reporter=_Reporter(),  # type: ignore[arg-type]
        knowledge_loader=_KnowledgeLoader(),  # type: ignore[arg-type]
        skill_loader=_SkillLoader(),  # type: ignore[arg-type]
        runtime=runtime,  # type: ignore[arg-type]
    )
    raw_reference = """Source path: cases/settings.fsq.yaml

Raw case content:
```yaml
- tapOn: Privacy and security
- tapOn: Microsoft services
```
"""
    task = Task(
        id="settings",
        name="Case reference: settings.fsq.yaml",
        description="Run raw case content.",
        planning_reference_kind="raw_case",
        planning_reference_text=raw_reference,
        verification_goal="Goal completed: Execute the referenced case content from settings.fsq.yaml.",
        acceptance_criteria=["Goal completed: Execute the referenced case content from settings.fsq.yaml."],
    )

    result = await _coordinate(agent).run(task)

    assert result.status == "success"
    assert runtime.pre_plan_reference_type == "raw_case"
    assert runtime.pre_plan_goal == raw_reference.strip()
    assert runtime.pre_plan_goal is not None
    assert "Microsoft services" in runtime.pre_plan_goal
    assert "Execute the referenced case content" not in runtime.pre_plan_goal
    assert runtime.last_task is not None
    assert runtime.last_task.verification_goal.startswith("Verify planned outcome for raw_case:")


@pytest.mark.asyncio
async def test_pre_plan_runtime_reads_page_by_index_page_id(tmp_path: Path) -> None:
    knowledge_dir = tmp_path / "knowledge"
    pages_dir = knowledge_dir / "pages"
    pages_dir.mkdir(parents=True)
    (knowledge_dir / "index.md").write_text(
        """
# Knowledge Index

```json
{
    "schema_version": "page_knowledge_index_v1",
    "product": "Microsoft Edge",
    "platform": "Android",
    "pages": [
        {
            "page_id": "edge_android_new_tab_page",
            "file": "pages/edge_android_new_tab_page.md",
            "name": "New Tab Page",
            "intents": ["new tab"]
        }
    ]
}
```
""",
        encoding="utf-8",
    )
    (pages_dir / "edge_android_new_tab_page.md").write_text("# New Tab Page", encoding="utf-8")
    runtime = DefaultCodingAgentRuntime(_settings_with_knowledge(knowledge_dir), object())  # type: ignore[arg-type]

    output = await runtime._read_knowledge_page_tool(ToolCall(name="read_knowledge_page", arguments={"page_id": "edge_android_new_tab_page"}, call_id="call-page"))

    assert '"ok": true' in output
    assert "# New Tab Page" in output
    assert "pages/edge_android_new_tab_page.md" in output


@pytest.mark.asyncio
async def test_pre_plan_runtime_reads_from_pre_plan_knowledge_dir(tmp_path: Path) -> None:
    private_knowledge_dir = tmp_path / "knowledge"
    page_knowledge_dir = tmp_path / "knowledge" / "project_android_v1"
    private_knowledge_dir.mkdir(parents=True)
    (private_knowledge_dir / "project.md").write_text("# Project Knowledge", encoding="utf-8")
    pages_dir = page_knowledge_dir / "pages"
    pages_dir.mkdir(parents=True)
    (page_knowledge_dir / "index.md").write_text("# Page Graph Index", encoding="utf-8")
    (pages_dir / "edge_android_new_tab_page.md").write_text("# New Tab Page", encoding="utf-8")
    runtime = DefaultCodingAgentRuntime(
        _settings_with_knowledge(private_knowledge_dir, page_knowledge_dir),
        object(),
    )  # type: ignore[arg-type]

    index_output = await runtime._read_knowledge_index_tool(ToolCall(name="read_knowledge_index", arguments={}, call_id="call-index"))
    page_output = await runtime._read_knowledge_page_tool(ToolCall(name="read_knowledge_page", arguments={"file": "pages/edge_android_new_tab_page.md"}, call_id="call-page"))

    assert "# Project Knowledge" in index_output
    assert "# Page Graph Index" in index_output
    assert "# New Tab Page" in page_output


@pytest.mark.asyncio
async def test_pre_plan_runtime_reads_project_knowledge_without_index(tmp_path: Path) -> None:
    knowledge_dir = tmp_path / "knowledge"
    knowledge_dir.mkdir(parents=True)
    (knowledge_dir / "project.md").write_text("# Project Knowledge", encoding="utf-8")
    runtime = DefaultCodingAgentRuntime(_settings_with_knowledge(knowledge_dir), object())  # type: ignore[arg-type]

    payload = json.loads(await runtime._read_knowledge_index_tool(ToolCall(name="read_knowledge_index", arguments={}, call_id="call-index")))

    assert payload["ok"] is True
    assert payload["entries"] == [{"path": "project.md", "content": "# Project Knowledge"}]
    assert "Knowledge index not found" not in payload["content"]


@pytest.mark.asyncio
async def test_pre_plan_runtime_returns_structured_page_read_failures(tmp_path: Path) -> None:
    knowledge_dir = tmp_path / "knowledge"
    knowledge_dir.mkdir(parents=True)
    runtime = DefaultCodingAgentRuntime(_settings_with_knowledge(knowledge_dir), object())  # type: ignore[arg-type]

    missing_payload = json.loads(await runtime._read_knowledge_page_tool(ToolCall(name="read_knowledge_page", arguments={"page_id": "missing_page"}, call_id="call-missing")))
    unsafe_payload = json.loads(await runtime._read_knowledge_page_tool(ToolCall(name="read_knowledge_page", arguments={"file": "../secret.md"}, call_id="call-unsafe")))

    assert missing_payload == {
        "ok": False,
        "error": "Knowledge page not found.",
        "page_id": "missing_page",
        "path": "pages/missing_page.md",
    }
    assert unsafe_payload == {
        "ok": False,
        "error": "A safe page_id or relative page file is required.",
        "page_id": None,
        "file": "../secret.md",
    }


@pytest.mark.asyncio
async def test_pre_plan_runtime_returns_empty_knowledge_when_no_project_or_index(tmp_path: Path) -> None:
    knowledge_dir = tmp_path / "knowledge"
    knowledge_dir.mkdir(parents=True)
    runtime = DefaultCodingAgentRuntime(_settings_with_knowledge(knowledge_dir), object())  # type: ignore[arg-type]

    payload = json.loads(await runtime._read_knowledge_index_tool(ToolCall(name="read_knowledge_index", arguments={}, call_id="call-index")))

    assert payload == {"ok": True, "path": None, "content": "", "entries": []}


@pytest.mark.parametrize(
    ("tool_name", "relative_file"),
    [("read_knowledge_index", "project.md"), ("read_knowledge_index", "index.md"), ("read_knowledge_page", "index.md"), ("read_knowledge_page", "pages/page.md")],
)
@pytest.mark.parametrize("failure_kind", ["decode", "read"])
async def test_optional_knowledge_read_errors_are_safe_and_recoverable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, tool_name: str, relative_file: str, failure_kind: str) -> None:
    root = tmp_path / "knowledge"
    (root / "pages").mkdir(parents=True)
    (root / "project.md").write_text("project guidance", encoding="utf-8")
    (root / "index.md").write_text('{"pages":[{"page_id":"page","file":"pages/page.md"}]}', encoding="utf-8")
    (root / "pages" / "page.md").write_text("page guidance", encoding="utf-8")
    target = root / relative_file
    original_read = Path.read_text

    def unreadable(path: Path, *args, **kwargs):
        if path == target:
            raise OSError(f"private-read-detail {target}")
        return original_read(path, *args, **kwargs)

    if failure_kind == "decode":
        target.write_bytes(b"\xffprivate-read-detail")
    else:
        monkeypatch.setattr(Path, "read_text", unreadable)
    runtime = DefaultCodingAgentRuntime(_settings_with_knowledge(root), object())
    tool = next(binding for binding in runtime._build_pre_plan_tools() if binding.name == tool_name)
    call = ToolCall(name=tool_name, arguments={"page_id": "page"} if tool_name == "read_knowledge_page" else {}, call_id="knowledge-call")
    output = await tool.invoke(call)
    assert json.loads(output)["ok"] is False
    assert "private-read-detail" not in output
    assert str(root) not in output
    monkeypatch.setattr(Path, "read_text", original_read)
    target.write_text("repaired guidance", encoding="utf-8")
    assert json.loads(await tool.invoke(call))["ok"] is True


async def test_optional_knowledge_invalid_parameters_return_safe_failure(tmp_path: Path) -> None:
    runtime = DefaultCodingAgentRuntime(_settings_with_knowledge(tmp_path), object())
    output = await runtime._read_knowledge_page_tool(ToolCall(name="read_knowledge_page", arguments={"page_id": ["private-invalid-value"]}, call_id="invalid-knowledge"))
    assert json.loads(output)["ok"] is False
    assert "private-invalid-value" not in output


@pytest.mark.parametrize("tool_name", ["read_knowledge_index", "read_knowledge_page"])
async def test_optional_knowledge_read_cancellation_propagates(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, tool_name: str) -> None:
    (tmp_path / "index.md").write_text("index", encoding="utf-8")
    cancellation = asyncio.CancelledError("knowledge cancelled")

    def cancelled_read(path: Path, *args, **kwargs):
        raise cancellation

    runtime = DefaultCodingAgentRuntime(_settings_with_knowledge(tmp_path), object())
    tool = next(binding for binding in runtime._build_pre_plan_tools() if binding.name == tool_name)
    call = ToolCall(name=tool_name, arguments={"page_id": "page"} if tool_name == "read_knowledge_page" else {}, call_id="cancelled-knowledge")
    monkeypatch.setattr(Path, "read_text", cancelled_read)
    with pytest.raises(asyncio.CancelledError) as failure:
        await tool.invoke(call)
    assert failure.value is cancellation


def test_complete_run_entry_remains_owned_by_execution(tmp_path):
    agent = FsqAgent(_settings_with_knowledge(tmp_path), Verifier(), None, _KnowledgeLoader(), _SkillLoader(), _Runtime())
    assert not hasattr(agent, "run")


@pytest.mark.asyncio
@pytest.mark.parametrize("as_json", [False, True])
async def test_event_persistence_redacts_structured_and_json_string_arguments(as_json):
    from fsq_agent.agent._events import RunEventEmitter

    events = []
    values = {"password": "ARG_SECRET", "authorization": "Bearer HEADER_SECRET", "nested": {"access_token": "TOKEN_SECRET"}, "key": "Enter"}
    await RunEventEmitter(sink=events.append).emit(RunEvent(run_id="run", task_id="task", type="tool_call_started", title="Call", tool_arguments=json.dumps(values) if as_json else values))
    assert "ARG_SECRET" not in events[0].model_dump_json()
    assert "HEADER_SECRET" not in events[0].model_dump_json()
    assert "TOKEN_SECRET" not in events[0].model_dump_json()
    args = json.loads(events[0].tool_arguments) if as_json else events[0].tool_arguments
    assert args["key"] == "Enter"


@pytest.mark.asyncio
@pytest.mark.parametrize("value", [{"id_token": "CANARY_ID"}, "https://example.test/?refresh_token=CANARY_QUERY", "Bearer CANARY_BARE"])
async def test_event_credential_family_stays_redacted(value):
    from fsq_agent.agent._events import RunEventEmitter

    events = []
    await RunEventEmitter(sink=events.append).emit(RunEvent(run_id="run", task_id="task", type="planning_update", title="progress", payload={"value": value}))
    assert "CANARY" not in events[0].model_dump_json()
