# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import ast
import asyncio
import json
import threading
import time
from pathlib import Path
from typing import Any, ClassVar

import pytest

from fsq_agent.adapters.coding_agent import DefaultCodingAgentRuntime
from fsq_agent.adapters.coding_agent._harness_tools import HarnessToolAdapter
from fsq_agent.agent._pre_plan import build_pre_plan_input
from fsq_agent.agent._prompt import PromptModelBuilder, PromptRenderer
from fsq_agent.agent._verification_task import VerificationEvidenceBuilder
from fsq_agent.agent_engine import AgentEvent, AgentRequest, AgentResult, EngineError, ModelRequest, ModelResult, TokenUsage, ToolCall, ToolInputFailure, ToolOutputEntry
from fsq_agent.config import Settings
from fsq_agent.models import (
    AgentFinalOutput,
    AgentRuntimeSettings,
    GoalPrePlan,
    HarnessActionResult,
    HarnessArtifactRef,
    HarnessContext,
    HarnessFunctionSchema,
    KnowledgeBundle,
    LocalToolOutputSettings,
    OutputSettings,
    RunnerStepResult,
    RuntimeSecretSettings,
    SkillBundle,
    StepPhase,
    StepPhaseReport,
    StepResult,
    Task,
)
from fsq_agent.providers import build_model_provider_session


class _EmptyToolFactory:
    def build_tools(self, *_args: Any, **_kwargs: Any) -> list[Any]:
        return []


class _CapturingToolFactory:
    def __init__(self) -> None:
        self.kwargs: dict[str, Any] | None = None

    def build_tools(self, *_args: Any, **kwargs: Any) -> list[Any]:
        self.kwargs = kwargs
        return []


class _FakeHarness:
    def close(self) -> None:
        return None

    def __init__(
        self,
        *,
        tool_name: str = "tap_on",
        driver_method: str = "tap_on",
        fsq_action_name: str = "tapOn",
        screen_size: tuple[int, int] | None = None,
    ) -> None:
        self.tool_name = tool_name
        self.driver_method = driver_method
        self.fsq_action_name = fsq_action_name
        self.screen_size = screen_size
        self.steps: list[Any] = []
        self.calls: list[str] = []

    def action_space(self) -> list[HarnessFunctionSchema]:
        return [
            HarnessFunctionSchema(
                name=self.tool_name,
                description=f"Run {self.fsq_action_name}.",
                params_json_schema={"type": "object", "properties": {"target": {"type": "string"}}, "required": ["target"]},
                platform="android",
                driver_method=self.driver_method,
                fsq_action_name=self.fsq_action_name,
            )
        ]

    def get_context(self) -> HarnessContext:
        self.calls.append("get_context")
        return HarnessContext(platform="android", session_id="session-1", screen_size=self.screen_size)

    def before_action(self, step: Any, context: HarnessContext) -> None:
        self.calls.append(f"before:{step.action_name}:{context.session_id}")

    def invoke_action(self, step: Any, context: HarnessContext) -> HarnessActionResult:
        self.calls.append(f"invoke:{step.action_name}:{context.session_id}")
        self.steps.append(step)
        return HarnessActionResult(
            status="passed",
            action_name=step.action_name,
            output={"context_session_id": context.session_id, "params": step.params},
            metadata={"harness": "fake"},
        )

    def after_action(
        self,
        step: Any,
        context: HarnessContext,
        action_result: HarnessActionResult | None,
    ) -> None:
        status = action_result.status if action_result else "none"
        self.calls.append(f"after:{step.action_name}:{status}")

    def capture_artifact(
        self,
        kind: str,
        reason: str,
        context: HarnessContext,
        step_id: str,
        phase: StepPhase,
    ) -> HarnessArtifactRef:
        self.calls.append(f"capture:{kind}:{reason}:{step_id}:{phase}:{context.session_id}")
        return HarnessArtifactRef(
            artifact_id=f"{step_id}-{phase}-{reason}-{kind}",
            kind=kind,
            path=Path(f"artifacts/{kind}/{step_id}-{phase}-{reason}.{kind}"),
        )

    def classify_error(self, _error: BaseException, _phase: StepPhase, _step: Any) -> str:
        return "unknown"


class _FailingHarness(_FakeHarness):
    def action_space(self) -> list[HarnessFunctionSchema]:
        raise RuntimeError("Harness action-space failed")


class _FailingCaptureHarness(_FakeHarness):
    def capture_artifact(
        self,
        kind: str,
        reason: str,
        context: HarnessContext,
        step_id: str,
        phase: StepPhase,
    ) -> HarnessArtifactRef:
        self.calls.append(f"capture:{kind}:{reason}:{step_id}:{phase}:{context.session_id}")
        raise RuntimeError("capture failed")


class _DirectInvokeForbiddenHarness(_FakeHarness):
    def invoke_action(self, step: Any, context: HarnessContext) -> HarnessActionResult:
        raise AssertionError("adapter must not directly invoke the harness")


class _FakeWebHarness(_FakeHarness):
    def __init__(self) -> None:
        super().__init__(tool_name="click_on", driver_method="click_on", fsq_action_name="clickOn")

    def action_space(self) -> list[HarnessFunctionSchema]:
        schemas = super().action_space()
        return [schemas[0].model_copy(update={"platform": "web"})]

    def get_context(self) -> HarnessContext:
        self.calls.append("get_context")
        return HarnessContext(platform="web", session_id="session-1")


def _fake_harness_factory(_run_id: str) -> _FakeHarness:
    return _FakeHarness()


class _FakeProviderSession:
    def get_model(self):
        return _FakeModel()

    async def close(self) -> None:
        return None


class _FakeModel:
    async def complete(self, request: ModelRequest) -> ModelResult:
        raise AssertionError("Agent runtime must not bypass the engine")


class _FakeEngine:
    requests: ClassVar[list[AgentRequest]] = []

    async def run(self, model, request: AgentRequest, *, on_event=None) -> AgentResult:
        self.requests.append(request)
        if request.output is not None and request.output.name == "GoalPrePlan":
            return AgentResult(final_output=GoalPrePlan(goal="Open the app.", verification_goal="The app is open."))
        return AgentResult(final_output=AgentFinalOutput(status="success", summary="Done."))


@pytest.mark.parametrize("outcome", ["success", "startup", "model", "cancelled"])
@pytest.mark.parametrize("close_fails", [False, True])
async def test_owned_harness_and_provider_are_disposed_for_every_exit(tmp_path, monkeypatch, outcome, close_fails):
    closed = []
    primary = asyncio.CancelledError("primary") if outcome == "cancelled" else EngineError("model", "primary")
    cleanup_error = OSError("cleanup")

    class Harness(_FakeHarness):
        def action_space(self):
            if outcome == "startup":
                raise primary
            return super().action_space()

        def close(self):
            closed.append("harness")
            if close_fails:
                raise cleanup_error

    class Session(_FakeProviderSession):
        async def close(self):
            closed.append("provider")

    class Engine(_FakeEngine):
        async def run(self, *args, **kwargs):
            if outcome in {"model", "cancelled"}:
                raise primary
            return await super().run(*args, **kwargs)

    settings = Settings(agent_runtime=_azure_openai_settings())
    settings.output.runs_dir = tmp_path
    monkeypatch.setattr("fsq_agent.adapters.coding_agent._runtime.build_model_provider_session", lambda _: Session())
    runtime = DefaultCodingAgentRuntime(settings, _EmptyToolFactory(), lambda _: Harness(), engine=Engine())
    operation = _run_in_context(runtime, Task(description="Test"), KnowledgeBundle(), [], "owned")
    if outcome == "cancelled" or (outcome == "success" and close_fails):
        with pytest.raises(asyncio.CancelledError if outcome == "cancelled" else OSError) as raised:
            await operation
        assert raised.value is (primary if outcome == "cancelled" else cleanup_error)
    else:
        result = await operation
        assert result[-1].status == ("success" if outcome == "success" else "failed")
        if outcome != "success":
            assert result[-1].error == ("Harness action-space discovery failed." if outcome == "startup" else "primary")
    assert closed == ["harness", "provider"]


@pytest.mark.parametrize("interruption", ["timeout", "cancelled"])
async def test_late_harness_construction_is_disposed_in_worker(tmp_path, interruption):
    started, release, closed = threading.Event(), threading.Event(), threading.Event()

    class Harness(_FakeHarness):
        def close(self):
            closed.set()

    def build(run_id):
        started.set()
        assert release.wait(5)
        return Harness()

    settings = Settings(agent_runtime=_azure_openai_settings())
    settings.output.runs_dir = tmp_path
    settings.agent.step_timeout_seconds = 0.02
    runtime = DefaultCodingAgentRuntime(settings, _EmptyToolFactory(), build)
    operation = asyncio.create_task(runtime._build_harness_with_timeout("late"))
    try:
        assert await asyncio.to_thread(started.wait, 2)
        if interruption == "cancelled":
            operation.cancel()
        with pytest.raises(TimeoutError if interruption == "timeout" else asyncio.CancelledError):
            await operation
    finally:
        release.set()
        assert await asyncio.to_thread(closed.wait, 2)


async def test_cancellation_during_disposal_waits_for_owned_cleanup(tmp_path, monkeypatch):
    started, release = threading.Event(), threading.Event()
    closed = []

    class Harness(_FakeHarness):
        def close(self):
            started.set()
            assert release.wait(5)
            closed.append("harness")

    class Session(_FakeProviderSession):
        async def close(self):
            closed.append("provider")

    settings = Settings(agent_runtime=_azure_openai_settings())
    settings.output.runs_dir = tmp_path
    monkeypatch.setattr("fsq_agent.adapters.coding_agent._runtime.build_model_provider_session", lambda _: Session())
    runtime = DefaultCodingAgentRuntime(settings, _EmptyToolFactory(), lambda _: Harness(), engine=_FakeEngine())
    operation = asyncio.create_task(_run_in_context(runtime, Task(description="Test"), KnowledgeBundle(), [], "during-close"))
    try:
        assert await asyncio.to_thread(started.wait, 2)
        operation.cancel()
        await asyncio.sleep(0)
        operation.cancel()
        await asyncio.sleep(0)
        assert not operation.done()
    finally:
        release.set()
    with pytest.raises(asyncio.CancelledError):
        await operation
    assert closed == ["harness", "provider"]


def _patch_runtime_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    import fsq_agent.adapters.coding_agent._runtime as runtime_module

    _FakeEngine.requests = []
    monkeypatch.setattr(runtime_module, "build_model_provider_session", lambda _settings: _FakeProviderSession())
    monkeypatch.setattr(runtime_module, "create_agent_engine_for_model", lambda model: _FakeEngine())


def _test_request(runtime: DefaultCodingAgentRuntime, run_id: str = "") -> AgentRequest:
    return runtime._build_request(name="test", instructions="instructions", model_input="test", tools=[], output_type=AgentFinalOutput, run_id=run_id)


def _azure_openai_settings(*, api_key: str = "dummy") -> AgentRuntimeSettings:
    settings = AgentRuntimeSettings(provider="azure_openai")
    settings.base_url = "https://edgeqa-resource.cognitiveservices.azure.com/openai/v1/"
    settings.model = "gpt-5.4"
    settings.api_key = api_key
    return settings


async def _run_in_context(runtime, task, knowledge, skills, run_id, event_sink=None):
    from fsq_agent.core.evidence import EvidenceRecorder
    from fsq_agent.models import RunExecutionContext

    run_dir = runtime.settings.output.runs_dir / run_id
    return await runtime.run_task(
        task,
        knowledge,
        skills,
        run_id,
        event_sink,
        context=RunExecutionContext(run_id=run_id, run_dir=run_dir, platform=runtime.settings.harness.platform),
        evidence_sink=EvidenceRecorder(run_id=run_id, output_dir=run_dir),
    )


@pytest.mark.asyncio
async def test_runtime_failure_returns_failed_step() -> None:
    settings = Settings(agent_runtime=_azure_openai_settings())
    runtime = DefaultCodingAgentRuntime(settings, _EmptyToolFactory(), lambda _run_id: _FailingHarness())
    task = Task(
        id="runtime-failure",
        name="Runtime Failure",
        description="Trigger harness failure.",
        acceptance_criteria=["A failed step is returned."],
    )

    results = await _run_in_context(runtime, task, KnowledgeBundle(), [], "runtime-failure-2026-05-09_00-00-00")

    assert results[0].status == "failed"
    assert results[0].tool_name == "agent_runtime.runner"
    assert results[0].tool_output["failure_category"] == "agent_runtime_error"
    assert results[0].tool_output["failure_reason"] == "agent_runtime_error"
    assert results[0].actual_outcome == "Agent runtime execution failed before producing structured verification output."
    assert "Harness action-space discovery failed" in str(results[0].error)


@pytest.mark.asyncio
async def test_runtime_emits_startup_events_before_main_planning(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_runtime_engine(monkeypatch)
    runtime = DefaultCodingAgentRuntime(Settings(agent_runtime=_azure_openai_settings()), _EmptyToolFactory(), _fake_harness_factory)
    task = Task(id="startup", name="Startup", description="Run startup.")
    events: list[Any] = []

    results = await _run_in_context(runtime, task, KnowledgeBundle(), [], "startup-run", event_sink=events.append)

    assert results[-1].status == "success"
    titles = [event.title for event in events]
    expected_titles = [
        "Runtime startup started",
        "Provider setup started",
        "Provider setup completed",
        "Harness setup started",
        "Harness setup completed",
        "Tool setup started",
        "Tool setup completed",
        "Agent runtime ready",
        "Planning started",
    ]
    for title in expected_titles:
        assert title in titles
    assert [titles.index(title) for title in expected_titles] == sorted(titles.index(title) for title in expected_titles)
    harness_started = events[titles.index("Harness setup started")]
    assert harness_started.payload["timeout_seconds"] == 60
    assert harness_started.payload["app_id_configured"] is False
    assert harness_started.payload["serial_selected"] is False
    assert "serial_configured" not in harness_started.payload
    harness_completed = events[titles.index("Harness setup completed")]
    assert harness_completed.payload["harness_class"] == "_FakeHarness"
    assert "driver_class" not in harness_completed.payload


@pytest.mark.asyncio
async def test_runtime_routes_three_agent_flows_through_neutral_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_runtime_engine(monkeypatch)
    runtime = DefaultCodingAgentRuntime(Settings(agent_runtime=_azure_openai_settings()), _EmptyToolFactory(), _fake_harness_factory)
    task = Task(id="explicit-model-settings", name="Model Settings", description="Run with stable runtime settings.")

    await runtime.run_pre_plan("Open the app.", KnowledgeBundle(), [], "explicit-model-settings-run")
    await _run_in_context(runtime, task, KnowledgeBundle(), [], "explicit-model-settings-run")
    await runtime.run_verification(task, [], "explicit-model-settings-run", None)

    assert [request.name for request in _FakeEngine.requests] == [
        "fsq-agent pre-planner",
        "fsq-agent",
        "fsq-agent verifier",
    ]
    assert [request.output.name for request in _FakeEngine.requests] == ["GoalPrePlan", "AgentFinalOutput", "AgentFinalOutput"]
    assert all(request.stream for request in _FakeEngine.requests)
    assert len(_FakeEngine.requests[0].tools) == 2
    assert _FakeEngine.requests[-1].tools == ()


@pytest.mark.asyncio
async def test_runtime_emits_one_dynamic_agent_token_usage_event_from_engine_result(monkeypatch: pytest.MonkeyPatch) -> None:
    class UsageEngine(_FakeEngine):
        async def run(self, model, request, *, on_event=None):
            result = await super().run(model, request, on_event=on_event)
            if request.output is not None and request.output.name == "AgentFinalOutput":
                return AgentResult(
                    final_output=result.final_output,
                    usage=TokenUsage(1200, 80, 1280, requests=3, cached_input_tokens=900, reasoning_tokens=25),
                )
            return result

    _patch_runtime_engine(monkeypatch)
    runtime = DefaultCodingAgentRuntime(
        Settings(agent_runtime=_azure_openai_settings()),
        _EmptyToolFactory(),
        _fake_harness_factory,
        engine=UsageEngine(),
    )
    events: list[Any] = []

    results = await _run_in_context(runtime, Task(id="usage", description="Measure usage."), KnowledgeBundle(), [], "usage-run", events.append)

    assert results[-1].status == "success"
    usage_events = [event for event in events if event.type == "dynamic_agent_token_usage"]
    assert len(usage_events) == 1
    assert usage_events[0].payload == {
        "provider": "azure_openai",
        "model": "gpt-5.4",
        "requests": 3,
        "input_tokens": 1200,
        "output_tokens": 80,
        "total_tokens": 1280,
        "cached_input_tokens": 900,
        "reasoning_tokens": 25,
    }


@pytest.mark.asyncio
async def test_runtime_does_not_emit_token_usage_without_engine_usage(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_runtime_engine(monkeypatch)
    runtime = DefaultCodingAgentRuntime(Settings(agent_runtime=_azure_openai_settings()), _EmptyToolFactory(), _fake_harness_factory)
    events: list[Any] = []

    await _run_in_context(runtime, Task(id="no-usage", description="No usage."), KnowledgeBundle(), [], "no-usage-run", events.append)

    assert all(event.type != "dynamic_agent_token_usage" for event in events)


@pytest.mark.asyncio
async def test_runtime_does_not_fabricate_usage_when_engine_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []

    class FailingEngine:
        async def run(self, model, request, *, on_event=None):
            calls.append(request)
            raise EngineError("incomplete", "Model stream failed.")

    _patch_runtime_engine(monkeypatch)
    runtime = DefaultCodingAgentRuntime(Settings(agent_runtime=_azure_openai_settings()), _EmptyToolFactory(), _fake_harness_factory, engine=FailingEngine())
    events: list[Any] = []

    results = await _run_in_context(runtime, Task(id="failed-usage", description="Fail after usage."), KnowledgeBundle(), [], "failed-usage-run", events.append)

    assert results[0].status == "failed"
    usage_events = [event for event in events if event.type == "dynamic_agent_token_usage"]
    assert usage_events == []
    assert len(calls) == 1
    assert events[-1].type == "run_failed"


@pytest.mark.asyncio
async def test_runtime_does_not_retry_usage_event_when_sink_fails_after_receiving_it(monkeypatch: pytest.MonkeyPatch) -> None:
    class UsageEngine(_FakeEngine):
        async def run(self, model, request, *, on_event=None):
            result = await super().run(model, request, on_event=on_event)
            return AgentResult(final_output=result.final_output, usage=TokenUsage(100, 10, 110))

    _patch_runtime_engine(monkeypatch)
    runtime = DefaultCodingAgentRuntime(
        Settings(agent_runtime=_azure_openai_settings()),
        _EmptyToolFactory(),
        _fake_harness_factory,
        engine=UsageEngine(),
    )
    events: list[Any] = []

    def failing_usage_sink(event: Any) -> None:
        events.append(event)
        if event.type == "dynamic_agent_token_usage":
            raise RuntimeError("downstream sink failed after persistence")

    results = await _run_in_context(
        runtime,
        Task(id="usage-sink-failure", description="Fail the usage sink."),
        KnowledgeBundle(),
        [],
        "usage-sink-failure-run",
        failing_usage_sink,
    )

    assert results[0].status == "failed"
    assert len([event for event in events if event.type == "dynamic_agent_token_usage"]) == 1
    assert events[-1].type == "run_failed"


@pytest.mark.asyncio
@pytest.mark.parametrize("effort", ["low", "high"])
async def test_runtime_preserves_effort_with_durable_context(monkeypatch: pytest.MonkeyPatch, effort: str) -> None:
    _patch_runtime_engine(monkeypatch)
    settings = Settings(agent_runtime=_azure_openai_settings())
    settings.agent_runtime.reasoning_effort = effort
    runtime = DefaultCodingAgentRuntime(settings, _EmptyToolFactory(), _fake_harness_factory)
    task = Task(id="explicit-model-settings", name="Model Settings", description="Run with configured effort.")

    await runtime.run_pre_plan("Open the app.", KnowledgeBundle(), [], "explicit-model-settings-run")
    await _run_in_context(runtime, task, KnowledgeBundle(), [], "explicit-model-settings-run")
    await runtime.run_verification(task, [], "explicit-model-settings-run", None)

    assert [request.name for request in _FakeEngine.requests] == [
        "fsq-agent pre-planner",
        "fsq-agent",
        "fsq-agent verifier",
    ]
    assert all(request.reasoning_effort == effort for request in _FakeEngine.requests)


@pytest.mark.asyncio
async def test_runtime_harness_construction_failure_is_visible(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_runtime_engine(monkeypatch)

    def fail_harness(_run_id: str) -> _FakeHarness:
        raise RuntimeError("device connect failed")

    runtime = DefaultCodingAgentRuntime(Settings(agent_runtime=_azure_openai_settings()), _EmptyToolFactory(), fail_harness)
    events: list[Any] = []

    results = await _run_in_context(runtime, Task(id="failure", description="Fail startup."), KnowledgeBundle(), [], "failure-run", events.append)

    assert results[0].status == "failed"
    assert "device connect failed" in str(results[0].error)
    titles = [event.title for event in events]
    assert "Harness setup started" in titles
    assert "Harness setup completed" not in titles
    assert titles[-1] == "Agent run failed"


@pytest.mark.asyncio
async def test_runtime_cleanup_failure_preserves_original_failed_result(monkeypatch: pytest.MonkeyPatch) -> None:
    class FailedCloseSession(_FakeProviderSession):
        async def close(self) -> None:
            raise EngineError("cleanup", "Secondary cleanup failure")

    class FailedEngine:
        async def run(self, model, request, *, on_event=None):
            raise EngineError("incomplete", "Primary model failure", reason="content_filter")

    _patch_runtime_engine(monkeypatch)
    monkeypatch.setattr("fsq_agent.adapters.coding_agent._runtime.build_model_provider_session", lambda settings: FailedCloseSession())
    runtime = DefaultCodingAgentRuntime(Settings(agent_runtime=_azure_openai_settings()), _EmptyToolFactory(), _fake_harness_factory, engine=FailedEngine())

    results = await _run_in_context(runtime, Task(id="failure", description="Fail run"), KnowledgeBundle(), [], "failed-cleanup-run")

    assert results[0].status == "failed"
    assert results[0].error == "Primary model failure"
    assert results[0].tool_output["failure_category"] == "provider_content_filter"


@pytest.mark.parametrize("operation", ["run_task", "run_pre_plan", "run_verification"])
@pytest.mark.parametrize("outcome", ["failure", "cancelled", "success"])
@pytest.mark.parametrize("cleanup_cancelled", [False, True])
async def test_runtime_cleanup_preserves_primary_outcome_with_real_session(monkeypatch: pytest.MonkeyPatch, operation: str, outcome: str, cleanup_cancelled: bool) -> None:
    from fsq_agent.models import PlanningError

    primary = asyncio.CancelledError("Primary cancellation") if outcome == "cancelled" else EngineError("incomplete", "Primary model failure", reason="content_filter")
    cleanup_error = asyncio.CancelledError("Secondary cleanup cancellation") if cleanup_cancelled else EngineError("cleanup", "Secondary cleanup failure")
    closed = []

    class ControlledProvider:
        def get_model(self, model_name: str):
            return _FakeModel()

        async def aclose(self) -> None:
            closed.append(True)
            raise cleanup_error

    class ControlledEngine(_FakeEngine):
        async def run(self, model, request, *, on_event=None):
            if outcome != "success":
                raise primary
            return await super().run(model, request, on_event=on_event)

    settings = Settings(agent_runtime=_azure_openai_settings())
    monkeypatch.setattr("fsq_agent.providers._session.create_model_provider", lambda **kwargs: ControlledProvider())
    session = build_model_provider_session(settings)
    monkeypatch.setattr("fsq_agent.adapters.coding_agent._runtime.build_model_provider_session", lambda configured: session)
    runtime = DefaultCodingAgentRuntime(settings, _EmptyToolFactory(), _fake_harness_factory, engine=ControlledEngine())
    task = Task(id="failure-precedence", description="Verify failure precedence")

    async def invoke_operation():
        if operation == "run_pre_plan":
            return await runtime.run_pre_plan("Plan task", KnowledgeBundle(), [], "precedence-run")
        if operation == "run_verification":
            return await runtime.run_verification(task, [], "precedence-run", None)
        return await _run_in_context(runtime, task, KnowledgeBundle(), [], "precedence-run")

    if outcome == "cancelled":
        with pytest.raises(asyncio.CancelledError) as failure:
            await invoke_operation()
        assert failure.value is primary
    elif outcome == "success":
        with pytest.raises(type(cleanup_error)) as failure:
            await invoke_operation()
        assert failure.value is cleanup_error
    elif operation == "run_pre_plan":
        with pytest.raises(PlanningError) as failure:
            await invoke_operation()
        assert failure.value.__cause__ is primary
    else:
        results = await invoke_operation()
        assert results[0].status == "failed"
        assert results[0].error == "Primary model failure"
    assert closed == [True]


@pytest.mark.asyncio
async def test_runtime_harness_construction_timeout_is_visible(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_runtime_engine(monkeypatch)

    def slow_harness(_run_id: str) -> _FakeHarness:
        time.sleep(2)
        return _FakeHarness()

    settings = Settings(agent={"step_timeout_seconds": 1}, agent_runtime=_azure_openai_settings())
    runtime = DefaultCodingAgentRuntime(settings, _EmptyToolFactory(), slow_harness)
    events: list[Any] = []

    results = await _run_in_context(runtime, Task(id="timeout", description="Timeout startup."), KnowledgeBundle(), [], "timeout-run", events.append)

    assert results[0].status == "failed"
    assert "Harness setup timed out after 1 seconds" in str(results[0].error)
    titles = [event.title for event in events]
    assert "Harness setup started" in titles
    assert "Harness setup completed" not in titles
    assert titles[-1] == "Agent run failed"


def test_runtime_harness_timeout_does_not_wait_for_worker_shutdown() -> None:
    def slow_harness(_run_id: str) -> _FakeHarness:
        time.sleep(3)
        return _FakeHarness()

    settings = Settings(agent={"step_timeout_seconds": 1}, agent_runtime=AgentRuntimeSettings())
    runtime = DefaultCodingAgentRuntime(settings, _EmptyToolFactory(), slow_harness)

    async def run_timeout() -> None:
        with pytest.raises(TimeoutError, match="Harness setup timed out after 1 seconds"):
            await runtime._build_harness_with_timeout("shutdown-run")

    started = time.perf_counter()
    asyncio.run(run_timeout())

    assert time.perf_counter() - started < 1.8


@pytest.mark.asyncio
async def test_runtime_classifies_engine_content_filter_incomplete(monkeypatch: pytest.MonkeyPatch) -> None:
    class _ContentFilterEngine:
        async def run(self, model, request, *, on_event=None):
            raise EngineError("incomplete", "Model response incomplete.", reason="content_filter")

    _patch_runtime_engine(monkeypatch)
    monkeypatch.setattr("fsq_agent.adapters.coding_agent._runtime.create_agent_engine_for_model", lambda model: _ContentFilterEngine())
    runtime = DefaultCodingAgentRuntime(Settings(agent_runtime=_azure_openai_settings()), _EmptyToolFactory(), _fake_harness_factory)
    events: list[Any] = []

    results = await _run_in_context(runtime, Task(id="content-filter", description="Trigger content filter."), KnowledgeBundle(), [], "content-filter-run", events.append)

    assert results[0].status == "failed"
    assert results[0].actual_outcome == "Agent runtime execution ended with an incomplete provider response due to content filtering."
    assert results[0].tool_output["failure_category"] == "provider_content_filter"
    assert events[-1].type == "run_failed"
    assert events[-1].payload["failure_category"] == "provider_content_filter"
    assert events[-1].payload["failure_reason"] == "content_filter"


def test_runtime_builds_configured_web_harness(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls: dict[str, Any] = {}

    class _FakeWebDriver:
        def __init__(self, **kwargs: Any) -> None:
            calls["driver"] = kwargs

    import fsq_agent.adapters.coding_agent._runtime as runtime_module

    monkeypatch.setattr("fsq_agent.drivers._factory.PlaywrightWebDriver", _FakeWebDriver)
    monkeypatch.setattr(runtime_module, "build_ai_assertion_evaluator", lambda _settings: "ai-evaluator")
    chrome_path = tmp_path / "chrome.exe"
    chrome_path.write_text("", encoding="utf-8")
    settings = Settings(
        harness={
            "platform": "web",
            "web": {
                "backend": "playwright",
                "channel": "chrome",
                "headless": False,
                "base_url": "https://example.test",
                "viewport_width": 390,
                "viewport_height": 844,
            },
        },
        output={"root_dir": tmp_path / "output"},
        agent_runtime=AgentRuntimeSettings(),
    )
    settings.harness.web.browser_executable_path = chrome_path
    settings.output.runs_dir = tmp_path / "runs"
    runtime = DefaultCodingAgentRuntime(settings, _EmptyToolFactory())

    payload = runtime._harness_setup_payload()
    harness = runtime._build_harness("web-run")
    completed_payload = runtime._harness_setup_payload(harness)

    assert calls["driver"] == {
        "channel": "chrome",
        "executable_path": chrome_path,
        "headless": False,
        "base_url": "https://example.test",
        "viewport": (390, 844),
    }
    assert payload == {
        "platform": "web",
        "timeout_seconds": 60,
        "backend": "playwright",
        "channel": "chrome",
        "browser_executable_configured": True,
        "headless": False,
        "base_url_configured": True,
        "viewport_configured": True,
    }
    assert completed_payload["harness_class"] == "WebHarness"
    assert completed_payload["driver_class"] == "_FakeWebDriver"


def test_runtime_builds_configured_macos_harness(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls: dict[str, Any] = {}

    class _FakeMacOSDriver:
        def __init__(self, **kwargs: Any) -> None:
            calls["driver"] = kwargs

    import fsq_agent.adapters.coding_agent._runtime as runtime_module

    monkeypatch.setattr("fsq_agent.drivers._factory.AppiumMac2Driver", _FakeMacOSDriver)
    monkeypatch.setattr(runtime_module, "build_ai_assertion_evaluator", lambda _settings: "ai-evaluator")
    settings = Settings(
        harness={
            "platform": "macos",
            "macos": {
                "backend": "appium_mac2",
                "page_source_max_depth": 7,
                "action_timeout_seconds": 11,
                "new_command_timeout_seconds": 420,
            },
        },
        output={"root_dir": tmp_path / "output"},
        agent_runtime=AgentRuntimeSettings(),
    )
    settings.harness.macos.appium_server_url = "http://127.0.0.1:4723"
    settings.harness.macos.bundle_id = "com.example.MacApp"
    settings.output.runs_dir = tmp_path / "runs"
    runtime = DefaultCodingAgentRuntime(settings, _EmptyToolFactory())

    payload = runtime._harness_setup_payload()
    harness = runtime._build_harness("mac-run")
    completed_payload = runtime._harness_setup_payload(harness)

    assert calls["driver"] == {
        "server_url": "http://127.0.0.1:4723",
        "bundle_id": "com.example.MacApp",
        "app_path": None,
        "page_source_max_depth": 7,
        "action_timeout_seconds": 11,
        "new_command_timeout_seconds": 420,
    }
    assert payload == {
        "platform": "macos",
        "timeout_seconds": 60,
        "backend": "appium_mac2",
        "appium_server_configured": True,
        "bundle_id_configured": True,
        "app_path_configured": False,
        "action_timeout_seconds": 11,
        "new_command_timeout_seconds": 420,
        "configured_skill_names": [],
    }
    assert completed_payload["harness_class"] == "MacOSHarness"
    assert completed_payload["driver_class"] == "_FakeMacOSDriver"


def test_runtime_builds_step_results_from_structured_pre_plan() -> None:
    settings = Settings(agent_runtime=AgentRuntimeSettings())
    runtime = DefaultCodingAgentRuntime(settings, _EmptyToolFactory())
    final_output = """
{
    "status": "failed",
    "summary": "Could not finish.",
    "pre_plan": [
        {
            "step_id": 1,
            "action": "Open browser",
            "success_criteria": ["Browser is open"],
            "status": "success"
        },
        {
            "step_id": 2,
            "action": "Add page to favorites",
            "success_criteria": ["Page is favorited"],
            "status": "adjusted"
        }
    ],
    "plan_updates": ["Used keyboard shortcut after toolbar button was unavailable."],
    "satisfied_criteria": ["Browser is open"],
    "unmet_criteria": ["Page is favorited"],
    "evidence": [],
    "errors": []
}
"""

    steps = runtime._build_pre_plan_step_results(final_output)

    assert [step.step_id for step in steps] == [1, 2]
    assert [step.status for step in steps] == ["success", "adjusted"]
    assert [step.duration_ms for step in steps] == [0, 0]
    assert steps[0].tool_name == "pre_plan"
    assert "Browser is open" in steps[0].actual_outcome
    assert "Used keyboard shortcut" in steps[1].actual_outcome


def test_runtime_task_input_uses_goal_only_verification_contract() -> None:
    settings = Settings(agent_runtime=AgentRuntimeSettings())
    runtime = DefaultCodingAgentRuntime(settings, _EmptyToolFactory())
    task = Task(id="derive", name="Derive", description="Open the page and verify it loads.")

    task_input = runtime._build_task_input(task)

    assert "Structured task input:" in task_input
    assert '"schema_version": "task_input_v1"' in task_input
    assert "Final verification goal: none provided" in task_input
    assert "verification_goal" in task_input


def test_runtime_task_input_includes_runtime_secret_names_and_warnings() -> None:
    runtime_secrets = RuntimeSecretSettings()
    runtime_secrets.set_values({"TEST_ACCOUNT_EMAIL": "user@example.com"})
    runtime_secrets.allowed_env_names.append("TEST_ACCOUNT_PASSWORD")
    settings = Settings(
        agent_runtime=AgentRuntimeSettings(),
        runtime_secrets=runtime_secrets,
    )
    runtime = DefaultCodingAgentRuntime(settings, _EmptyToolFactory())
    task = Task(id="login", name="Login", description="Sign in.")

    task_input = runtime._build_task_input(task)

    assert '"runtime_secret_names": [\n    "TEST_ACCOUNT_EMAIL"\n  ]' in task_input
    assert "Runtime secret TEST_ACCOUNT_PASSWORD is configured but not set." in task_input
    assert "user@example.com" not in task_input


def test_pre_plan_input_includes_available_platform_tools() -> None:
    payload = json.loads(
        build_pre_plan_input(
            "Open downloads.",
            KnowledgeBundle(),
            [],
            available_platform_tools=[
                {
                    "name": "tap_on",
                    "alias": "tapOn",
                    "aliases": [],
                    "executor_kind": "driver",
                    "step_kind": "action",
                    "platform": "android",
                }
            ],
        )
    )

    assert payload["available_platform_tools"] == [
        {
            "name": "tap_on",
            "alias": "tapOn",
            "aliases": [],
            "executor_kind": "driver",
            "step_kind": "action",
            "platform": "android",
        }
    ]


def test_pre_plan_input_includes_runtime_secret_names_without_values() -> None:
    payload = json.loads(
        build_pre_plan_input(
            "Sign in.",
            KnowledgeBundle(),
            [],
            runtime_secret_names=["TEST_ACCOUNT_EMAIL"],
            runtime_secret_warnings=["Runtime secret TEST_ACCOUNT_PASSWORD is configured but not set."],
        )
    )

    assert payload["runtime_secret_names"] == ["TEST_ACCOUNT_EMAIL"]
    assert payload["runtime_secret_warnings"] == ["Runtime secret TEST_ACCOUNT_PASSWORD is configured but not set."]


def test_pre_plan_input_includes_loaded_skills() -> None:
    payload = json.loads(
        build_pre_plan_input(
            "Open downloads.",
            KnowledgeBundle(),
            [SkillBundle(name="automation-basics", description="Use semantic actions.", kind="markdown", instructions="Prefer semantic actions.")],
        )
    )

    assert payload["skills"] == [
        {
            "name": "automation-basics",
            "description": "Use semantic actions.",
            "instructions": "Prefer semantic actions.",
        }
    ]


def test_runtime_pre_plan_tool_summary_uses_active_platform_registry() -> None:
    settings = Settings(harness={"platform": "android"}, agent_runtime=AgentRuntimeSettings())
    runtime = DefaultCodingAgentRuntime(settings, _EmptyToolFactory())

    tools = runtime._pre_plan_tool_summary()

    by_name = {tool["name"]: tool for tool in tools}
    assert by_name["tap_on"]["alias"] == "tapOn"
    assert by_name["tap_at"]["alias"] == "tapAt"
    assert by_name["ui_snapshot"]["alias"] == "uiTree"
    assert by_name["assert_visible"]["step_kind"] == "assertion"
    assert "get_runtime_secret" not in by_name


def test_runtime_instructions_exclude_loader_diagnostics() -> None:
    settings = Settings(agent_runtime=AgentRuntimeSettings())
    runtime = DefaultCodingAgentRuntime(settings, _EmptyToolFactory())
    knowledge = KnowledgeBundle(items={"project.md": "Use Edge account guidance."}, warnings=["missing optional knowledge"])
    skills = [SkillBundle(name="automation-basics", kind="markdown", instructions="Use semantic actions.")]

    instructions = runtime._build_instructions(knowledge, skills)

    assert "Custom operator instructions:" not in instructions
    assert "Knowledge warnings:" not in instructions
    assert "Skill warnings:" not in instructions
    assert "missing optional knowledge" not in instructions
    assert "Use Edge account guidance." in instructions
    assert "Use semantic actions." in instructions
    assert "Final output JSON Schema:" not in instructions
    assert "AgentFinalOutput structured output required by the agent runtime" in instructions


def test_runtime_instructions_use_configured_prompt_templates(tmp_path: Path) -> None:
    agent_template = tmp_path / "agent.j2"
    task_template = tmp_path / "task.j2"
    agent_template.write_text(
        "Configured base instruction.\nConfigured knowledge:\n{% for item in private_knowledge %}- {{ item.key }}={{ item.value }}\n{% endfor %}",
        encoding="utf-8",
    )
    task_template.write_text(
        "Task {{ task.id }}: {{ task.description }}\n{% if task.acceptance_criteria %}{{ task.acceptance_criteria | join(', ') }}{% else %}Configured no criteria text.{% endif %}\n",
        encoding="utf-8",
    )
    settings = Settings(
        agent_runtime=AgentRuntimeSettings(
            prompt={
                "agent_template_path": agent_template,
                "task_template_path": task_template,
            },
        )
    )
    runtime = DefaultCodingAgentRuntime(settings, _EmptyToolFactory())
    knowledge = KnowledgeBundle(items={"k": "v"})

    instructions = runtime._build_instructions(knowledge, [])
    task_input = runtime._build_task_input(Task(id="t1", description="Do it."))

    assert instructions.startswith("Configured base instruction.")
    assert "Configured knowledge:" in instructions
    assert task_input == "Task t1: Do it.\nConfigured no criteria text."


def test_runtime_instructions_include_knowledge_index_content() -> None:
    settings = Settings(agent_runtime=AgentRuntimeSettings())
    runtime = DefaultCodingAgentRuntime(settings, _EmptyToolFactory())
    knowledge = KnowledgeBundle(items={"project.md": "Use Other ways to sign in, then choose password sign-in."})

    instructions = runtime._build_instructions(knowledge, [])

    assert "Private knowledge:" in instructions
    assert "project.md" in instructions
    assert "choose password sign-in" in instructions


def test_prompt_model_builder_and_renderer_use_templates() -> None:
    settings = AgentRuntimeSettings().prompt
    builder = PromptModelBuilder(settings)
    renderer = PromptRenderer(settings)

    agent_model = builder.build_agent_prompt(KnowledgeBundle(), [])
    task_model = builder.build_task_prompt(Task(id="task-1", description="Do it.", verification_goal="Done."))

    assert "Custom operator instructions:" not in renderer.render_agent_prompt(agent_model)
    assert "Preserve ordered key-action semantic fidelity." in renderer.render_agent_prompt(agent_model)
    assert "launch_app harness tool" not in renderer.render_agent_prompt(agent_model)
    assert "kill_app harness tool" not in renderer.render_agent_prompt(agent_model)
    assert "tool usage error" in renderer.render_agent_prompt(agent_model)
    rendered_task = renderer.render_task_prompt(task_model)
    assert "Structured task input:" in rendered_task
    assert '"id": "task-1"' in rendered_task
    assert "Verification goal:" in rendered_task
    assert "Done." in rendered_task


def test_agent_runtime_settings_rejects_obsolete_custom_instruction_fields(tmp_path: Path) -> None:
    custom_instructions = tmp_path / "custom-instructions.md"

    with pytest.raises(ValueError, match="custom_instructions"):
        AgentRuntimeSettings(prompt={"custom_instructions": ["Custom."]})

    with pytest.raises(ValueError, match="custom_instructions_path"):
        AgentRuntimeSettings(prompt={"custom_instructions_path": custom_instructions})


def test_prompt_renderer_injects_model_into_configured_jinja_templates(tmp_path: Path) -> None:
    agent_template = tmp_path / "agent.j2"
    task_template = tmp_path / "task.j2"
    agent_template.write_text("{{ variables.prefix }}{% for skill in skills %} {{ skill.name }}={{ skill.instructions }}{% endfor %}", encoding="utf-8")
    task_template.write_text("Task {{ task.id }} {{ task.variables.prefix }}", encoding="utf-8")
    settings = AgentRuntimeSettings(
        prompt={
            "agent_template_path": agent_template,
            "task_template_path": task_template,
            "variables": {"prefix": "Base."},
        },
    ).prompt
    builder = PromptModelBuilder(settings)
    renderer = PromptRenderer(settings)

    agent_model = builder.build_agent_prompt(KnowledgeBundle(), [SkillBundle(name="s", kind="markdown", instructions="Skill.")])
    task_model = builder.build_task_prompt(Task(id="task-1", description="Do it.", acceptance_criteria=["Done."]))

    assert renderer.render_agent_prompt(agent_model) == "Base. s=Skill."
    assert renderer.render_task_prompt(task_model) == "Task task-1 Base."


@pytest.mark.asyncio
async def test_harness_tool_adapter_delegates_to_step_runner(monkeypatch: pytest.MonkeyPatch) -> None:
    import fsq_agent.adapters.coding_agent._harness_tools as harness_tools_module

    runner_calls: list[tuple[Any, str, Any]] = []

    class _FakeStepRunner:
        def __init__(self, harness: Any, **_: Any) -> None:
            self.harness = harness

        def run_step(self, run_id: str, step: Any) -> RunnerStepResult:
            runner_calls.append((self.harness, run_id, step))
            return RunnerStepResult(
                step_id=step.step_id,
                status="passed",
                duration_ms=7,
                phase_reports=[
                    StepPhaseReport(
                        step_id=step.step_id,
                        phase="invoke",
                        status="passed",
                        metadata={
                            "harness_output": {"params": step.params},
                            "harness_metadata": {"runner": "fake"},
                        },
                    )
                ],
            )

    monkeypatch.setattr(harness_tools_module, "StepRunner", _FakeStepRunner)
    harness = _DirectInvokeForbiddenHarness()
    adapter = HarnessToolAdapter(harness, run_id="run-1")

    tools = adapter.build_tools()
    output = await tools[0].invoke(ToolCall(name=tools[0].name, arguments={"target": "Downloads"}, call_id="call-1"))

    payload = json.loads(output)
    assert payload["status"] == "passed"
    assert payload["duration_ms"] == 7
    assert payload["runner_step_id"] == "agent-tap_on-1"
    assert payload["runner_result"]["status"] == "passed"
    assert payload["result"]["output"] == {"params": {"target": "Downloads"}}
    assert runner_calls[0][0] is harness
    assert runner_calls[0][1] == "run-1"
    assert runner_calls[0][2].action_name == "tap_on"
    assert runner_calls[0][2].metadata["authored_action_name"] == "tapOn"
    assert runner_calls[0][2].evidence_policy.capture_before is False
    assert runner_calls[0][2].evidence_policy.artifact_kinds == []
    assert harness.steps == []


@pytest.mark.asyncio
async def test_harness_tool_adapter_applies_evidence_policy_to_mutating_action() -> None:
    harness = _FakeHarness()
    adapter = HarnessToolAdapter(harness, run_id="run-1")

    tools = adapter.build_tools()
    output = await tools[0].invoke(ToolCall(name=tools[0].name, arguments={"target": "Downloads"}, call_id="call-1"))

    payload = json.loads(output)
    assert tools[0].name == "tap_on"
    assert payload["tool_origin"] == "platform"
    assert payload["status"] == "passed"
    assert payload["driver_method"] == "tap_on"
    assert payload["fsq_action_name"] == "tapOn"
    assert payload["result"]["output"]["params"] == {"target": "Downloads"}
    assert [ref["kind"] for ref in payload["artifact_refs"]] == ["screenshot", "ui_snapshot", "screenshot", "ui_snapshot"]
    assert payload["result"]["artifact_refs"] == payload["artifact_refs"]
    assert payload["runner_result"]["phase_reports"][0]["phase"] == "prepare"
    assert payload["runner_result"]["phase_reports"][-1]["phase"] == "finalize"
    assert harness.steps[0].action_name == "tap_on"
    assert harness.steps[0].metadata["authored_action_name"] == "tapOn"
    assert harness.steps[0].kind == "action"
    assert harness.steps[0].evidence_policy.capture_before is True
    assert harness.steps[0].evidence_policy.capture_after is True
    assert harness.steps[0].evidence_policy.capture_on_failure is False
    assert harness.steps[0].evidence_policy.artifact_kinds == ["screenshot", "ui_snapshot"]
    assert [call for call in harness.calls if call.startswith("capture:")] == [
        "capture:screenshot:before-action:agent-tap_on-1:prepare:session-1",
        "capture:ui_snapshot:before-action:agent-tap_on-1:prepare:session-1",
        "capture:screenshot:after-action:agent-tap_on-1:finalize:session-1",
        "capture:ui_snapshot:after-action:agent-tap_on-1:finalize:session-1",
    ]


@pytest.mark.asyncio
async def test_harness_tool_adapter_outputs_tap_at_safe_replay_params() -> None:
    harness = _FakeHarness(tool_name="tap_at", driver_method="tap_at", fsq_action_name="tapAt", screen_size=(1080, 2400))
    adapter = HarnessToolAdapter(harness, run_id="run-1")

    tools = adapter.build_tools()
    output = await tools[0].invoke(ToolCall(name=tools[0].name, arguments={"point": {"x": 100, "y": 200}}, call_id="call-1"))

    payload = json.loads(output)
    expected = {"point": {"x": 100, "y": 200}, "reference_screen_size": {"width": 1080, "height": 2400}}
    assert payload["tool_name"] == "tap_at"
    assert payload["safe_replay_params"] == expected
    assert payload["runner_result"]["phase_reports"][1]["metadata"]["safe_replay_params"] == expected


def test_harness_tool_adapter_uses_default_strict_schema_for_capability_tools() -> None:
    harness = _FakeHarness(tool_name="perform_actions", driver_method="perform_actions", fsq_action_name="performActions")
    adapter = HarnessToolAdapter(harness, run_id="run-1")

    tools = adapter.build_tools()

    assert tools[0].name == "perform_actions"
    assert tools[0].strict is True


def test_harness_tool_adapter_builds_macos_bindings_with_strict_schema_requirement() -> None:
    from fsq_agent.core.harness._appium_mac2_driver import AppiumMac2Driver
    from fsq_agent.core.harness._macos import MacOSHarness

    class SchemaOnlyEvaluator:
        def evaluate(self, request: Any) -> Any:
            raise AssertionError(f"Schema construction must not invoke the evaluator: {request!r}")

    harness = MacOSHarness(
        driver=AppiumMac2Driver(bundle_id="com.microsoft.edgemac"),
        ai_assertion_evaluator=SchemaOnlyEvaluator(),
    )
    adapter = HarnessToolAdapter(harness, run_id="macos-schema-run", platform="macos")

    tools = adapter.build_tools()

    assert {tool.name for tool in tools} == {schema.name for schema in adapter.schemas}
    assert "assert_with_ai" in {tool.name for tool in tools}
    assert all(tool.strict for tool in tools)


def test_harness_tool_adapter_preserves_capability_parameter_schema() -> None:
    harness = _FakeHarness()
    adapter = HarnessToolAdapter(harness, run_id="schema-run", platform="android")
    schema = adapter.schemas[0].params_json_schema
    schema["description"] = "Tool parameter description."
    schema["properties"]["target"]["description"] = "Target description."

    [tool] = adapter.build_tools()

    assert tool.parameters_schema is schema
    assert tool.parameters_schema["description"] == "Tool parameter description."
    assert tool.parameters_schema["properties"]["target"]["description"] == "Target description."
    assert tool.strict is True


async def test_invalid_harness_input_keeps_failure_provenance_without_actions() -> None:
    harness = _FakeHarness()
    adapter = HarnessToolAdapter(harness, run_id="run-1")
    tool = adapter.build_tools()[0]
    output = await tool.on_invalid_input(ToolInputFailure(name=tool.name, call_id="invalid-action", message="Tool arguments must be a JSON object."))
    payload = json.loads(output)
    assert payload["status"] == "failed"
    assert payload["capability_name"] == "tap_on"
    assert payload["platform"] == "android"
    assert payload["artifact_refs"] == []
    assert payload["error_message"] == "Tool arguments must be a JSON object."
    assert harness.calls == []


@pytest.mark.asyncio
async def test_harness_tool_adapter_keeps_default_evidence_policy_for_assertion_actions() -> None:
    harness = _FakeHarness(
        tool_name="assert_visible",
        driver_method="assert_visible",
        fsq_action_name="assertVisible",
    )
    adapter = HarnessToolAdapter(harness, run_id="run-1")

    tools = adapter.build_tools()
    output = await tools[0].invoke(ToolCall(name=tools[0].name, arguments={"target": "Downloads"}, call_id="call-1"))

    payload = json.loads(output)
    assert payload["status"] == "passed"
    assert [ref["kind"] for ref in payload["artifact_refs"]] == ["screenshot", "ui_snapshot"]
    assert payload["result"]["artifact_refs"] == payload["artifact_refs"]
    assert harness.steps[0].action_name == "assert_visible"
    assert harness.steps[0].metadata["authored_action_name"] == "assertVisible"
    assert harness.steps[0].kind == "assertion"
    assert harness.steps[0].evidence_policy.capture_before is True
    assert harness.steps[0].evidence_policy.capture_after is False
    assert harness.steps[0].evidence_policy.artifact_kinds == ["screenshot", "ui_snapshot"]
    assert [call for call in harness.calls if call.startswith("capture:")] == [
        "capture:screenshot:before-action:agent-assert_visible-1:prepare:session-1",
        "capture:ui_snapshot:before-action:agent-assert_visible-1:prepare:session-1",
    ]


@pytest.mark.asyncio
async def test_harness_tool_adapter_uses_step_kind_for_effective_evidence_policy() -> None:
    harness = _FakeHarness()
    adapter = HarnessToolAdapter(harness, run_id="run-1")

    tools = adapter.build_tools()
    output = await tools[0].invoke(ToolCall(name=tools[0].name, arguments={"target": "Downloads"}, call_id="call-1"))

    payload = json.loads(output)
    assert payload["status"] == "passed"
    assert payload["fsq_action_name"] == "tapOn"
    assert [ref["kind"] for ref in payload["artifact_refs"]] == ["screenshot", "ui_snapshot", "screenshot", "ui_snapshot"]
    assert harness.steps[0].action_name == "tap_on"
    assert harness.steps[0].metadata["authored_action_name"] == "tapOn"
    assert harness.steps[0].evidence_policy.capture_before is True
    assert harness.steps[0].evidence_policy.artifact_kinds == ["screenshot", "ui_snapshot"]
    assert [call for call in harness.calls if call.startswith("capture:")] == [
        "capture:screenshot:before-action:agent-tap_on-1:prepare:session-1",
        "capture:ui_snapshot:before-action:agent-tap_on-1:prepare:session-1",
        "capture:screenshot:after-action:agent-tap_on-1:finalize:session-1",
        "capture:ui_snapshot:after-action:agent-tap_on-1:finalize:session-1",
    ]


@pytest.mark.asyncio
async def test_harness_tool_adapter_uses_web_platform_registry_for_evidence_policy() -> None:
    harness = _FakeWebHarness()
    adapter = HarnessToolAdapter(harness, run_id="run-1", platform="web")

    tools = adapter.build_tools()
    output = await tools[0].invoke(ToolCall(name=tools[0].name, arguments={"target": "Search"}, call_id="call-1"))

    payload = json.loads(output)
    assert payload["status"] == "passed"
    assert payload["fsq_action_name"] == "clickOn"
    assert [ref["kind"] for ref in payload["artifact_refs"]] == [
        "screenshot",
        "ui_snapshot",
        "screenshot",
        "ui_snapshot",
    ]
    assert harness.steps[0].action_name == "click_on"
    assert harness.steps[0].metadata["authored_action_name"] == "clickOn"
    assert harness.steps[0].evidence_policy.artifact_kinds == ["screenshot", "ui_snapshot"]


@pytest.mark.asyncio
async def test_harness_tool_adapter_surfaces_artifact_capture_failure() -> None:
    harness = _FailingCaptureHarness()
    adapter = HarnessToolAdapter(harness, run_id="run-1")

    tools = adapter.build_tools()
    output = await tools[0].invoke(ToolCall(name=tools[0].name, arguments={"target": "Downloads"}, call_id="call-1"))

    payload = json.loads(output)
    assert payload["status"] == "failed"
    assert payload["failure_category"] == "artifact_error"
    assert payload["result"]["failure_category"] == "artifact_error"
    assert payload["runner_result"]["status"] == "failed"
    assert payload["runner_result"]["failure_category"] == "artifact_error"
    assert "capture failed" in payload["error_message"]


def test_runtime_tool_origin_recognizes_platform_tools() -> None:
    runtime = DefaultCodingAgentRuntime(Settings(agent_runtime=AgentRuntimeSettings()), _EmptyToolFactory(), _fake_harness_factory)
    runtime._agent_tool_names = {"read_file"}
    runtime._harness_tool_names = {"tap_on"}

    assert runtime._tool_origin("tap_on") == "platform"
    assert runtime._tool_origin("read_file") == "agent_tool"
    assert runtime._tool_origin("read_knowledge_index") == "runtime"
    assert runtime._tool_origin("unexpected_tool") == "unknown"


def test_runtime_tool_output_payload_preserves_runner_evidence_fields() -> None:
    runtime = DefaultCodingAgentRuntime(Settings(agent_runtime=AgentRuntimeSettings()), _EmptyToolFactory())
    output = json.dumps(
        {
            "tool_name": "tap_on",
            "tool_origin": "harness",
            "status": "passed",
            "replay": {"kind": "fsq_command", "alias": "tapAt"},
            "safe_replay_params": {"point": {"x": 100, "y": 200}, "reference_screen_size": {"width": 1080, "height": 2400}},
            "runner_step_id": "agent-tap_on-1",
            "runner_result": {"step_id": "agent-tap_on-1", "status": "passed"},
            "artifact_refs": [{"kind": "screenshot", "path": "artifacts/screenshots/before.png"}],
            "result": {
                "artifact_refs": [{"kind": "ui_tree", "path": "artifacts/ui-trees/after.json"}],
            },
        }
    )

    payload = runtime._tool_output_payload(output)

    assert payload["runner_step_id"] == "agent-tap_on-1"
    assert payload["replay"] == {"kind": "fsq_command", "alias": "tapAt"}
    assert payload["safe_replay_params"] == {"point": {"x": 100, "y": 200}, "reference_screen_size": {"width": 1080, "height": 2400}}
    assert payload["runner_result"] == {"step_id": "agent-tap_on-1", "status": "passed"}
    assert payload["artifact_refs"] == [{"kind": "screenshot", "path": "artifacts/screenshots/before.png"}]


def test_runtime_tool_output_payload_adds_agent_tool_fields() -> None:
    runtime = DefaultCodingAgentRuntime(Settings(agent_runtime=AgentRuntimeSettings()), _EmptyToolFactory())
    runtime._agent_tool_names = {"search_artifact"}
    output = json.dumps(
        {
            "tool_name": "search_artifact",
            "model_output": "full",
            "artifact": {"path": None, "content_chars": None},
            "status": "passed",
            "result": {
                "tool_name": "search_artifact",
                "status": "success",
                "output": {"matches": []},
                "duration_ms": 12,
            },
        }
    )

    payload = runtime._tool_output_payload(output)

    assert payload["tool_name"] == "search_artifact"
    assert payload["tool_origin"] == "agent_tool"
    assert payload["executor_kind"] == "agent_tool"
    assert payload["status"] == "passed"
    assert payload["duration_ms"] == 12


@pytest.mark.asyncio
async def test_runtime_uses_engine_events_for_agent_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_runtime_engine(monkeypatch)
    tool_factory = _CapturingToolFactory()
    runtime = DefaultCodingAgentRuntime(Settings(agent_runtime=_azure_openai_settings()), tool_factory, _fake_harness_factory)
    task = Task(id="agent-tools", name="Agent Tools", description="Run with AgentTools.")

    await _run_in_context(runtime, task, KnowledgeBundle(), [], "agent-tools-run", event_sink=lambda _event: None)

    assert tool_factory.kwargs is not None
    assert tool_factory.kwargs["event_sink"] is None
    assert callable(tool_factory.kwargs["runner_invoker"])


def test_runtime_stream_tool_output_preserves_tool_name_from_started_event() -> None:
    runtime = DefaultCodingAgentRuntime(Settings(agent_runtime=AgentRuntimeSettings()), _EmptyToolFactory())
    started = AgentEvent(kind="tool_called", tool_name="read_knowledge_page", call_id="call-1", arguments={"page_id": "edge_android_new_tab_page", "file": None})
    completed = AgentEvent(kind="tool_output", call_id="call-1", output='{"ok":true,"page_id":"edge_android_new_tab_page","duration_ms":123}')

    start_event = runtime._map_stream_event(started, "run-1", "pre-plan")
    completed_event = runtime._map_stream_event(completed, "run-1", "pre-plan")

    assert start_event is not None
    assert completed_event is not None
    assert completed_event.tool_name == "read_knowledge_page"
    assert completed_event.tool_call_id == "call-1"
    assert completed_event.duration_ms == 123


def test_runtime_stream_message_output_uses_text_not_engine_object_repr() -> None:
    runtime = DefaultCodingAgentRuntime(Settings(agent_runtime=AgentRuntimeSettings()), _EmptyToolFactory())
    event = AgentEvent(kind="message", text='{"schema_version":"task_run_v1","status":"success"}')

    run_event = runtime._map_stream_event(event, "run-1", "task")

    assert run_event is not None
    assert run_event.message == '{"schema_version":"task_run_v1","status":"success"}'


def test_runtime_stream_omits_empty_reasoning_summary() -> None:
    runtime = DefaultCodingAgentRuntime(Settings(agent_runtime=AgentRuntimeSettings()), _EmptyToolFactory())
    event = AgentEvent(kind="reasoning_summary")

    assert runtime._map_stream_event(event, "run-1", "task") is None


def test_verification_evidence_builder_uses_text_only_after_runner_visual_assertion(tmp_path: Path) -> None:
    output_root = tmp_path / "output"
    screenshots_dir = output_root / "harness-screenshots"
    screenshots_dir.mkdir(parents=True)
    screenshot_path = screenshots_dir / "screenshot.png"
    screenshot_path.write_bytes(b"\x89PNG\r\n\x1a\nimage")
    task = Task(
        id="visual",
        description="Verify the page visually.",
        verification_goal="Verify the logo is visible.",
    )
    results = [
        StepResult(
            step_id=1,
            status="success",
            actual_outcome=json.dumps(
                {
                    "schema_version": "task_run_v1",
                    "status": "success",
                    "summary": "Visual assertion passed.",
                    "pre_plan": [],
                    "plan_updates": [],
                    "satisfied_criteria": ["Key action 1: assertWithAI Verify the logo is visible."],
                    "unmet_criteria": [],
                    "evidence": [f"Runner inspected submitted screenshot {screenshot_path} and verified the logo."],
                    "errors": [],
                }
            ),
            tool_name="agent_runtime.runner",
        )
    ]

    model_input = VerificationEvidenceBuilder().build_model_input(task, results, image_root=output_root)

    assert isinstance(model_input, str)
    evidence = json.loads(model_input)
    assert evidence["verification_goal"] == "Verify the logo is visible."
    assert "verification_mode" not in evidence
    assert "blocking_criteria" not in evidence
    assert "visual_artifacts" not in evidence
    assert evidence["agent_claims"]["status"] == "success"
    assert "Runner inspected submitted screenshot" in evidence["agent_claims"]["evidence"][0]
    assert "input_image" not in model_input


def test_verification_evidence_builder_does_not_attach_images_from_paths(tmp_path: Path) -> None:
    output_root = tmp_path / "output"
    output_root.mkdir()
    outside_root = tmp_path / "outside"
    outside_root.mkdir()
    screenshot_path = outside_root / "screenshot.png"
    screenshot_path.write_bytes(b"\x89PNG\r\n\x1a\nimage")
    task = Task(id="visual", description="Verify the page visually.")
    results = [
        StepResult(
            step_id=1,
            status="success",
            actual_outcome=f"Screenshot outside output root: {screenshot_path}",
        )
    ]

    model_input = VerificationEvidenceBuilder().build_model_input(task, results, image_root=output_root)

    assert isinstance(model_input, str)
    assert "input_image" not in model_input
    assert "visual_artifacts" not in model_input


def test_runtime_builds_neutral_request_with_one_tool_output_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    settings = Settings(agent_runtime=AgentRuntimeSettings())
    runtime = DefaultCodingAgentRuntime(settings, _EmptyToolFactory())

    request = _test_request(runtime)

    assert request.tracing_enabled is False
    input_filter = request.tool_output_filter
    assert input_filter.recent_inline_outputs == 3
    assert input_filter.max_output_chars == 30000
    assert input_filter.max_total_inline_chars == 60000
    assert not hasattr(request, "trimming")


def test_runtime_builds_run_config_enables_sdk_tracing_with_openai_export_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "trace-key")
    settings = Settings(agent_runtime=AgentRuntimeSettings())
    runtime = DefaultCodingAgentRuntime(settings, _EmptyToolFactory())

    assert _test_request(runtime).tracing_enabled is True


def test_runtime_builds_run_config_respects_explicit_tracing_disable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "trace-key")
    settings = Settings(agent_runtime=AgentRuntimeSettings(tracing_enabled=False))
    runtime = DefaultCodingAgentRuntime(settings, _EmptyToolFactory())

    assert _test_request(runtime).tracing_enabled is False


def test_provider_session_preserves_azure_connection_values_for_neutral_access() -> None:
    settings = Settings(agent_runtime=_azure_openai_settings(api_key="azure-key"))

    session = build_model_provider_session(settings)
    assert session.client_config.api_key == "azure-key"
    assert session.client_config.base_url == "https://edgeqa-resource.cognitiveservices.azure.com/openai/v1/"
    assert session.client_config.default_headers == {}
    assert not hasattr(session, "create_agents_provider")


def test_runtime_tool_count_filter_keeps_only_three_small_recent_outputs(tmp_path: Path) -> None:
    output_settings = OutputSettings()
    output_settings.runs_dir = tmp_path / "runs"
    settings = Settings(agent_runtime=AgentRuntimeSettings(), output=output_settings)
    runtime = DefaultCodingAgentRuntime(settings, _EmptyToolFactory())
    input_filter = _test_request(runtime, run_id="run-1").tool_output_filter
    entries = (
        ToolOutputEntry(1, "1", "read_file", "old output"),
        ToolOutputEntry(3, "2", "read_file", "recent 1"),
        ToolOutputEntry(5, "3", "read_file", "recent 2"),
        ToolOutputEntry(7, "4", "read_file", "recent 3"),
    )

    filtered = input_filter(entries)

    assert filtered[1].startswith("[Historical read_file output stored as artifact.")
    assert "old output" not in filtered[1]
    assert 3 not in filtered
    assert 5 not in filtered
    assert 7 not in filtered


def test_runtime_tool_count_filter_enforces_total_inline_budget(tmp_path: Path) -> None:
    runtime_settings = AgentRuntimeSettings()
    runtime_settings.local_tool_output = LocalToolOutputSettings(recent_inline_output_count=3, total_inline_output_max_chars=60000)
    output_settings = OutputSettings()
    output_settings.runs_dir = tmp_path / "runs"
    runtime = DefaultCodingAgentRuntime(Settings(agent_runtime=runtime_settings, output=output_settings), _EmptyToolFactory())
    input_filter = _test_request(runtime, run_id="run-1").tool_output_filter
    entries = tuple(ToolOutputEntry(index, str(index), "ui_snapshot", character * 25000) for index, character in enumerate("abc", start=1))

    filtered = input_filter(entries)

    assert filtered[1].startswith("[Historical ui_snapshot output stored as artifact.")
    assert 2 not in filtered
    assert 3 not in filtered


@pytest.mark.parametrize(("tool_name", "artifact_label"), [("harness_source", "harness_source"), ("", "runtime_tool")])
def test_runtime_tool_count_filter_writes_artifact_for_trimmed_history(tmp_path: Path, tool_name: str, artifact_label: str) -> None:
    runtime_settings = AgentRuntimeSettings()
    runtime_settings.local_tool_output = LocalToolOutputSettings(recent_inline_output_count=0)
    output_settings = OutputSettings()
    output_settings.runs_dir = tmp_path / "runs"
    settings = Settings(agent_runtime=runtime_settings, output=output_settings)
    runtime = DefaultCodingAgentRuntime(settings, _EmptyToolFactory())
    input_filter = _test_request(runtime, run_id="run-1").tool_output_filter
    entries = (ToolOutputEntry(1, "1", tool_name, "<node>" * 7000),)

    filtered = input_filter(entries)

    assert "Artifact path:" in filtered[1]
    artifacts = list((tmp_path / "runs" / "run-1" / "artifacts" / "tools").glob("*.json"))
    assert [artifact.name for artifact in artifacts] == [f"000001-{artifact_label}.json"]
    payload = json.loads(artifacts[0].read_text(encoding="utf-8"))
    assert payload["tool_name"] == artifact_label
    assert payload["content"] == entries[0].output


async def test_file_helper_context_filter_preserves_complete_artifact(tmp_path: Path) -> None:
    from fsq_agent.adapters.coding_agent import create_coding_agent_runtime
    from fsq_agent.agent_engine import ToolCall

    settings = Settings()
    settings.output.runs_dir = tmp_path / "runs"
    settings.agent_context.knowledge.root_dir = tmp_path / "knowledge"
    settings.agent_context.knowledge.root_dir.mkdir()
    content = "x" * 40000 + "TAIL"
    (settings.agent_context.knowledge.root_dir / "large.txt").write_text(content, encoding="utf-8")
    runtime = create_coding_agent_runtime(settings)
    tools = runtime.tool_factory.build_tools(run_id="run-1", task_id="task-1")
    read_tool = next(tool for tool in tools if tool.name == "read_file")
    search_tool = next(tool for tool in tools if tool.name == "search_artifact")
    output = await read_tool.invoke(ToolCall(name="read_file", arguments={"path": "large.txt"}, call_id="large-read"))
    payload = json.loads(output)
    artifact_path = Path(payload["artifact"]["path"])
    original_artifact = await asyncio.to_thread(artifact_path.read_text, encoding="utf-8")
    assert payload["model_output"] == "artifact_reference"
    assert json.loads(json.loads(original_artifact)["content"])["output"] == content
    input_filter = _test_request(runtime, run_id="run-1").tool_output_filter

    entries = (
        ToolOutputEntry(1, "large-read", "read_file", output),
        ToolOutputEntry(2, "recent-1", "read_file", "recent 1"),
        ToolOutputEntry(3, "recent-2", "read_file", "recent 2"),
        ToolOutputEntry(4, "recent-3", "read_file", "recent 3"),
    )
    filtered = input_filter(entries)

    assert str(artifact_path) in filtered[1]
    assert await asyncio.to_thread(artifact_path.read_text, encoding="utf-8") == original_artifact
    recovery = json.loads(await search_tool.invoke(ToolCall(name="search_artifact", arguments={"artifact_path": str(artifact_path), "query": "TAIL", "max_matches": 1}, call_id="recover-tail")))
    assert recovery["result"]["output"]["matches"]
    assert "TAIL" in recovery["result"]["output"]["matches"][0]["preview"]


def test_runtime_input_filter_trims_recent_large_ui_snapshot_to_artifact(tmp_path: Path) -> None:
    output_settings = OutputSettings()
    output_settings.runs_dir = tmp_path / "runs"
    settings = Settings(agent_runtime=AgentRuntimeSettings(), output=output_settings)
    runtime = DefaultCodingAgentRuntime(settings, _EmptyToolFactory())
    input_filter = _test_request(runtime, run_id="run-1").tool_output_filter
    snapshot_output = json.dumps(
        {
            "tool_name": "ui_snapshot",
            "status": "passed",
            "result": {"output": {"xml": '<node password="false">' + ("visible text " * 5000) + "</node>"}},
        }
    )
    entries = (ToolOutputEntry(1, "snapshot", "ui_snapshot", snapshot_output),)

    filtered = input_filter(entries)

    assert filtered[1].startswith("[Historical ui_snapshot output stored as artifact.")
    assert "Artifact path:" in filtered[1]
    assert len(filtered[1]) < len(snapshot_output)
    assert list((tmp_path / "runs" / "run-1" / "artifacts" / "tools").glob("*.json"))


def test_runtime_preview_redacts_wrapped_sensitive_tool_output() -> None:
    runtime = DefaultCodingAgentRuntime(Settings(agent_runtime=AgentRuntimeSettings()), _EmptyToolFactory())
    output = json.dumps(
        {
            "tool_name": "secret_debug_tool",
            "model_output": "full",
            "result": {
                "tool_name": "secret_debug_tool",
                "status": "success",
                "output": {
                    "type": "runtime_secret",
                    "name": "TEST_ACCOUNT_PASSWORD",
                    "value": "super-secret",
                    "sensitive": True,
                },
                "sensitive": True,
            },
        }
    )

    preview = runtime._preview(output)

    assert "super-secret" not in preview
    assert '"value": "***"' in preview


def test_runtime_redacts_configured_secret_values_from_final_output() -> None:
    runtime_secrets = RuntimeSecretSettings()
    runtime_secrets.set_values({"TEST_ACCOUNT_PASSWORD": "super-secret"})
    runtime = DefaultCodingAgentRuntime(
        Settings(
            agent_runtime=AgentRuntimeSettings(),
            runtime_secrets=runtime_secrets,
        ),
        _EmptyToolFactory(),
    )
    final_output = AgentFinalOutput(
        status="success",
        summary="Logged in with super-secret.",
        evidence=["The password super-secret was entered."],
    )

    redacted = runtime._redact_runtime_secrets(final_output)

    assert isinstance(redacted, AgentFinalOutput)
    assert redacted.summary == "Logged in with ***."
    assert redacted.evidence == ["The password *** was entered."]


def test_runtime_redacts_configured_secret_values_from_tool_arguments() -> None:
    runtime_secrets = RuntimeSecretSettings()
    runtime_secrets.set_values({"TEST_ACCOUNT_PASSWORD": "super-secret"})
    runtime = DefaultCodingAgentRuntime(
        Settings(
            agent_runtime=AgentRuntimeSettings(),
            runtime_secrets=runtime_secrets,
        ),
        _EmptyToolFactory(),
    )
    event = AgentEvent(kind="tool_called", tool_name="text_type", call_id="call-1", arguments={"text": "super-secret", "target": "Password"})

    run_event = runtime._map_stream_event(event, "run-1", "task-1")

    assert run_event.tool_arguments == {"text": "***", "target": "Password"}


def test_runtime_preserves_keyboard_key_arguments_for_recording() -> None:
    runtime = DefaultCodingAgentRuntime(Settings(), _EmptyToolFactory())
    event = AgentEvent(kind="tool_called", tool_name="press_key", call_id="keyboard-1", arguments={"key": "Enter", "modifiers": ["COMMAND"]})
    recorded = runtime._map_stream_event(event, "run-1", "task-1")
    assert recorded.tool_arguments == {"key": "Enter", "modifiers": ["COMMAND"]}
    assert runtime._redact({"apiKey": "credential-value", "private_key": "private-value", "authorization": "bearer-value", "key": "Escape"}) == {
        "apiKey": "***",
        "private_key": "***",
        "authorization": "***",
        "key": "Escape",
    }


def test_runtime_input_filter_leaves_plain_screenshot_outputs_text_only(tmp_path: Path) -> None:
    output_root = tmp_path / "output"
    screenshots_dir = output_root / "harness-screenshots"
    screenshots_dir.mkdir(parents=True)
    screenshot_path = screenshots_dir / "screenshot.png"
    screenshot_path.write_bytes(b"\x89PNG\r\n\x1a\nimage")
    output_settings = OutputSettings(root_dir=output_root)
    output_settings.runs_dir = output_root / "runs"
    settings = Settings(agent_runtime=AgentRuntimeSettings(), output=output_settings)
    runtime = DefaultCodingAgentRuntime(settings, _EmptyToolFactory())
    input_filter = _test_request(runtime, run_id="run-1").tool_output_filter
    entries = (ToolOutputEntry(1, "img", "harness_screenshot", f"Screenshot saved successfully to: {screenshot_path}"),)

    filtered = input_filter(entries)

    assert filtered == {}


def test_runtime_input_filter_does_not_attach_submitted_visual_assertion_image(tmp_path: Path) -> None:
    output_root = tmp_path / "output"
    screenshots_dir = output_root / "harness-screenshots"
    screenshots_dir.mkdir(parents=True)
    screenshot_path = screenshots_dir / "screenshot.png"
    screenshot_path.write_bytes(b"\x89PNG\r\n\x1a\nimage")
    output_settings = OutputSettings(root_dir=output_root)
    output_settings.runs_dir = output_root / "runs"
    settings = Settings(agent_runtime=AgentRuntimeSettings(), output=output_settings)
    runtime = DefaultCodingAgentRuntime(settings, _EmptyToolFactory())
    input_filter = _test_request(runtime, run_id="run-1").tool_output_filter
    output = json.dumps(
        {
            "type": "visual_assertion_submission",
            "assertion_id": "key-action-7",
            "prompt": "Verify the logo is visible.",
            "screenshot_path": str(screenshot_path),
        }
    )
    entries = (ToolOutputEntry(1, "visual", "submit_visual_assertion", output),)

    filtered = input_filter(entries)

    assert filtered == {}


def test_runtime_input_filter_rejects_screenshot_images_outside_output_root(tmp_path: Path) -> None:
    output_root = tmp_path / "output"
    output_root.mkdir()
    outside_root = tmp_path / "outside"
    outside_root.mkdir()
    screenshot_path = outside_root / "screenshot.png"
    screenshot_path.write_bytes(b"\x89PNG\r\n\x1a\nimage")
    output_settings = OutputSettings(root_dir=output_root)
    output_settings.runs_dir = output_root / "runs"
    settings = Settings(agent_runtime=AgentRuntimeSettings(), output=output_settings)
    runtime = DefaultCodingAgentRuntime(settings, _EmptyToolFactory())
    input_filter = _test_request(runtime, run_id="run-1").tool_output_filter
    output = json.dumps({"type": "visual_assertion_submission", "assertion_id": "key-action-7", "prompt": "Verify the logo is visible.", "screenshot_path": str(screenshot_path)})
    entries = (ToolOutputEntry(1, "visual", "submit_visual_assertion", output),)

    filtered = input_filter(entries)

    assert filtered == {}


def test_coding_agent_adapter_does_not_import_agent_private_modules() -> None:
    adapter_root = Path(__file__).parents[1] / "fsq_agent" / "adapters" / "coding_agent"
    private_imports: list[str] = []
    for source_path in adapter_root.glob("*.py"):
        for node in ast.walk(ast.parse(source_path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("fsq_agent.agent._"):
                private_imports.append(f"{source_path.name}:{node.lineno}:{node.module}")
            if isinstance(node, ast.Import):
                private_imports.extend(f"{source_path.name}:{node.lineno}:{alias.name}" for alias in node.names if alias.name.startswith("fsq_agent.agent._"))
    assert private_imports == []


@pytest.mark.asyncio
async def test_cancelled_main_engine_does_not_fabricate_unreturned_usage(monkeypatch):
    class CancelledEngine:
        async def run(self, model, request, *, on_event=None):
            raise asyncio.CancelledError()

    _patch_runtime_engine(monkeypatch)
    runtime = DefaultCodingAgentRuntime(Settings(agent_runtime=_azure_openai_settings()), _EmptyToolFactory(), _fake_harness_factory, engine=CancelledEngine())
    events = []
    with pytest.raises(asyncio.CancelledError):
        await _run_in_context(runtime, Task(id="cancel-usage", description="cancel"), KnowledgeBundle(), [], "cancel-usage", events.append)
    usage = [event for event in events if event.type == "dynamic_agent_token_usage"]
    assert usage == []


@pytest.mark.parametrize("as_json", [False, True])
def test_neutral_argument_redaction_preserves_shape_and_removes_credentials(as_json):
    runtime = DefaultCodingAgentRuntime(Settings(), _EmptyToolFactory(), _fake_harness_factory)
    arguments = {"password": "CANARY_PASSWORD", "authorization": "Bearer CANARY_AUTH", "nested": {"token": "CANARY_TOKEN"}, "key": "Enter", "textType": "runtimeSecret", "text": "PASSWORD_REFERENCE"}
    value = json.dumps(arguments) if as_json else arguments
    redacted = runtime._redact(value)
    parsed = json.loads(redacted) if as_json else redacted
    assert "CANARY" not in json.dumps(parsed)
    assert parsed["key"] == "Enter"
    assert parsed["text"] == "PASSWORD_REFERENCE"
