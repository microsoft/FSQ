# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import asyncio
import builtins
import json
import time
from pathlib import Path

import httpx
import pytest
from openai import AsyncOpenAI

from fsq_agent.adapters.coding_agent import DefaultCodingAgentRuntime
from fsq_agent.agent_engine import AgentResult, EngineError, ModelRequest, ModelResult, OutputContract, _openai_backend
from fsq_agent.ai_services import AIAssertionEvaluator, CaseSuggestionAnalyzer, build_ai_assertion_evaluator, build_case_suggestion_analyzer
from fsq_agent.config import Settings, refresh_provider_settings, save_azure_openai_provider
from fsq_agent.core.evidence import EvidenceRecorder
from fsq_agent.models import AgentFinalOutput, AgentRuntimeSettings, AIAssertionRequest, ConfigurationError, GoalPrePlan, KnowledgeBundle, PlanningError, RunExecutionContext, Task
from fsq_agent.providers import build_model_provider_session, test_model_provider_connection


class _NoActionHarness:
    def __init__(self, evaluator=None):
        self.evaluator = evaluator

    def close(self):
        if self.evaluator is not None:
            self.evaluator.close()

    def action_space(self) -> list:
        return []


class _NoHelperTools:
    def build_tools(self, **kwargs) -> list:
        return []


async def _run_main(runtime, task, run_id):
    run_dir = runtime.settings.output.runs_dir / run_id
    return await runtime.run_task(
        task,
        KnowledgeBundle(),
        [],
        run_id,
        context=RunExecutionContext(run_id=run_id, run_dir=run_dir, platform=runtime.settings.harness.platform),
        evidence_sink=EvidenceRecorder(run_id=run_id, output_dir=run_dir),
    )


@pytest.mark.parametrize("path", ["pre_plan", "main", "verification", "assertion", "connection", "suggestion"])
@pytest.mark.parametrize("failure_kind", [None, "incomplete", "failed", "refusal"])
@pytest.mark.parametrize("provider", ["azure_openai", "openai", "google_gemini", "github_copilot"])
@pytest.mark.parametrize("effort", ["low", "high"])
async def test_six_inference_paths_use_real_engine_without_sdk_import(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, path: str, failure_kind: str | None, provider: str, effort: str) -> None:
    settings = Settings(agent_runtime=AgentRuntimeSettings(provider=provider, tracing_enabled=False, reasoning_effort=effort))
    settings.agent_runtime.base_url = "https://api.openai.com/v1/" if provider == "openai" else "https://model.example.test/openai/v1/"
    if provider == "google_gemini":
        settings.agent_runtime.base_url = "https://generativelanguage.googleapis.com/v1beta/"
    settings.agent_runtime.api_key = "synthetic-model-key"
    model_name = "gemini-3.6-flash" if provider == "google_gemini" else "gpt-5.2-pro"
    settings.agent_runtime.model = model_name
    settings.output.runs_dir = tmp_path
    if provider == "github_copilot":
        settings.agent_runtime.base_url = "https://api.enterprise.githubcopilot.com/"
        settings.agent_runtime.provider_token = {"token": "synthetic-model-key", "expires_at": time.time() + 3600, "plan": "enterprise"}
        settings.agent_runtime.github_token = None
        settings.agent_runtime.user_config_root = tmp_path / "isolated-user"
    payloads = []
    clients = []
    results = {
        "pre_plan": GoalPrePlan(goal="Open the app.", verification_goal="The app is open.").model_dump_json(),
        "main": AgentFinalOutput(status="success", summary="Done.").model_dump_json(),
        "verification": AgentFinalOutput(status="success", summary="Verified.").model_dump_json(),
        "assertion": '{"passed":true,"explanation":"Visible.","confidence":0.9}',
        "connection": "FSQ_OK",
        "suggestion": '{"summary":"No change needed.","suggestions":[],"candidate_case_yaml":null}',
    }

    def respond(request: httpx.Request) -> httpx.Response:
        if provider == "google_gemini":
            assert str(request.url) == settings.agent_runtime.base_url + "interactions"
            assert request.headers["x-goog-api-key"] == "synthetic-model-key"
            payload = json.loads(request.content)
            payloads.append(payload)
            response = {
                "id": "interaction_path",
                "model": "test-model",
                "status": failure_kind if failure_kind in {"failed", "incomplete"} else "completed",
                "steps": [{"type": "model_output", "content": [{"type": "text", "text": results[path]}]}],
            }
            if failure_kind == "refusal":
                response["status"] = "failed"
                response["errors"] = [{"code": "SAFETY", "message": "private-response"}]
            if payload.get("stream"):
                event = {"event_type": "interaction.completed", "interaction": response}
                return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=f"event: interaction.completed\ndata: {json.dumps(event)}\n\n")
            return httpx.Response(200, json=response)
        assert str(request.url) == settings.agent_runtime.base_url + "responses"
        assert request.headers["authorization"] == "Bearer synthetic-model-key"
        if provider == "github_copilot":
            assert request.headers["copilot-integration-id"] == "vscode-chat"
            assert request.headers["openai-intent"] == "conversation-agent"
            assert request.headers["editor-version"].startswith("vscode/")
            assert request.headers["editor-plugin-version"].startswith("copilot-chat/")
        payload = json.loads(request.content)
        payloads.append(payload)
        response = {
            "id": "resp_path",
            "object": "response",
            "created_at": 1,
            "model": "test-model",
            "status": "completed",
            "output": [{"id": "msg_path", "type": "message", "role": "assistant", "status": "completed", "content": [{"type": "output_text", "text": results[path], "annotations": []}]}],
        }
        if failure_kind in {"incomplete", "failed"}:
            response["status"] = failure_kind
            if failure_kind == "incomplete":
                response["incomplete_details"] = {"reason": "content_filter"}
            else:
                response["error"] = {"code": "server_error", "message": "private error detail"}
        elif failure_kind == "refusal":
            response["output"][0]["content"].append({"type": "refusal", "refusal": ""})
        if not payload.get("stream"):
            return httpx.Response(200, json=response)
        event = {"type": "response.completed", "sequence_number": 0, "response": response}
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=f"event: response.completed\ndata: {json.dumps(event)}\n\n")

    def make_client(**kwargs):
        transport = httpx.AsyncClient(transport=httpx.MockTransport(respond))
        clients.append(transport)
        return AsyncOpenAI(http_client=transport, max_retries=0, **kwargs)

    original_import = builtins.__import__

    def without_sdk(name, *args, **kwargs):
        level = kwargs.get("level", args[3] if len(args) > 3 else 0)
        if level == 0 and name.split(".")[0] == "agents":
            raise AssertionError("Inference must not import OpenAI Agents SDK")
        return original_import(name, *args, **kwargs)

    if provider == "google_gemini":
        from fsq_agent.agent_engine import _google_gemini_backend

        original_client = _google_gemini_backend.genai.Client

        def make_google_client(**kwargs):
            kwargs["http_options"]["async_client_args"]["transport"] = httpx.MockTransport(respond)
            kwargs["http_options"]["retry_options"] = {"attempts": 1}
            client = original_client(**kwargs)
            clients.append(client._api_client._async_httpx_client)
            return client

        monkeypatch.setattr(_google_gemini_backend.genai, "Client", make_google_client)
    else:
        monkeypatch.setattr(_openai_backend, "AsyncOpenAI", make_client)
    monkeypatch.setattr(builtins, "__import__", without_sdk)
    if path in {"pre_plan", "main", "verification"}:
        runtime = DefaultCodingAgentRuntime(settings, _NoHelperTools(), lambda run_id: _NoActionHarness())
        task = Task(id="integration", name="Integration", description="Open the app.", verification_goal="The app is open.")
        if path == "pre_plan":
            if failure_kind is not None:
                with pytest.raises(PlanningError):
                    await runtime.run_pre_plan("Open the app.", KnowledgeBundle(), [], "integration-run")
            else:
                result = await runtime.run_pre_plan("Open the app.", KnowledgeBundle(), [], "integration-run")
                assert result.verification_goal == "The app is open."
        elif path == "main":
            result = await _run_main(runtime, task, "integration-run")
            assert result[-1].status == ("failed" if failure_kind else "success")
            assert result[-1].tool_name == "agent_runtime.runner"
        else:
            result = await runtime.run_verification(task, [], "integration-run", None)
            assert result[-1].status == ("failed" if failure_kind else "success")
            assert result[-1].tool_name == "agent_runtime.verifier"
        assert payloads[0]["stream"] is True
        if provider == "google_gemini":
            assert payloads[0]["response_format"]["mime_type"] == "application/json"
        else:
            assert payloads[0]["text"]["format"]["strict"] is True
    elif path == "assertion":
        screenshot = tmp_path / "sample.png"
        screenshot.write_bytes(b"synthetic-image")
        result = build_ai_assertion_evaluator(settings).evaluate(AIAssertionRequest(platform="web", prompt="Visible?", screenshot_path=screenshot))
        assert result.passed is (failure_kind is None)
        assert result.status == ("error" if failure_kind else "passed")
        assert payloads[0]["input"][0]["content"][1]["type"] == ("image" if provider == "google_gemini" else "input_image")
    elif path == "connection":
        monkeypatch.setattr("fsq_agent.providers._connection_test.refresh_provider_settings", lambda *args: settings)
        if failure_kind is not None:
            with pytest.raises(ConfigurationError):
                test_model_provider_connection()
        else:
            result = test_model_provider_connection()
            assert result.model == model_name
    else:
        analyzer = build_case_suggestion_analyzer(settings)
        if failure_kind is not None:
            with pytest.raises(EngineError):
                analyzer.analyze(parsed_case={"platform": "web"}, execution_report={"status": "success"})
        else:
            result = analyzer.analyze(parsed_case={"platform": "web"}, execution_report={"status": "success"})
            assert result.summary == "No change needed."
            assert result.candidate_case_yaml is None
    assert len(payloads) == 1
    assert payloads[0]["model"] == model_name
    effective_effort = "low" if path == "connection" else effort
    minimum = "minimal" if provider == "google_gemini" else "low" if provider == "azure_openai" else "medium"
    native_effort = minimum if effective_effort == "low" else "high"
    if provider == "google_gemini":
        assert payloads[0]["store"] is False
        assert payloads[0]["generation_config"] == {"thinking_level": native_effort}
    else:
        assert payloads[0]["reasoning"] == {"effort": native_effort}
    if path in {"assertion", "connection", "suggestion"}:
        assert not payloads[0].get("tools")
        assert not payloads[0].get("stream")
        output_format = payloads[0].get("response_format") if provider == "google_gemini" else payloads[0].get("text", {}).get("format")
        if path == "connection":
            assert output_format is None
        else:
            assert output_format is not None
            schema = output_format["schema"]
            expected = {"passed", "explanation", "confidence"} if path == "assertion" else {"summary", "suggestions", "candidate_case_yaml"}
            assert set(schema["properties"]) == expected
            assert set(schema["required"]) == expected
            assert schema["additionalProperties"] is False
    assert clients
    assert all(client.is_closed for client in clients)
    if provider == "github_copilot":
        assert not settings.agent_runtime.user_config_root.exists()


@pytest.mark.parametrize("effort,next_effort", [("low", "high"), ("high", "low")])
async def test_reasoning_effort_task_snapshot_and_injected_evaluator_survive_provider_refresh(monkeypatch, tmp_path, effort, next_effort):
    agent_requests, model_requests, evaluators, provider_instances = [], [], [], []

    class CapturingProvider:
        def __init__(self, **kwargs):
            self.model_name = ""
            self.closed = False
            provider_instances.append(self)

        def get_model(self, name):
            self.model_name = name
            return self

        async def complete(self, request):
            model_requests.append((self.model_name, request))
            text = '{"passed":true,"explanation":"Visible.","confidence":null}'
            return ModelResult(text=text, parsed_output=request.output.parse(text))

        async def aclose(self):
            self.closed = True

    class CapturingEngine:
        async def run(self, model, request, *, on_event=None):
            agent_requests.append((model.model_name, request))
            if request.name.endswith("pre-planner"):
                text = GoalPrePlan(goal="Inspect.", verification_goal="Visible.").model_dump_json()
            else:
                text = AgentFinalOutput(status="success", summary="Done.").model_dump_json()
                if request.name == "snapshot-agent":
                    result = evaluators[-1].evaluate(AIAssertionRequest(platform="android", prompt="Visible?"))
                    assert result.status == "passed"
            return AgentResult(final_output=request.output.parse(text))

    def create_harness(factory, **kwargs):
        evaluator = kwargs["ai_assertion_evaluator"]
        assert isinstance(evaluator, AIAssertionEvaluator)
        evaluators.append(evaluator)
        return _NoActionHarness(evaluator)

    monkeypatch.setattr("fsq_agent.providers._session.create_model_provider", CapturingProvider)
    monkeypatch.setattr("fsq_agent.adapters.coding_agent._runtime.HarnessFactory.create_harness", create_harness)
    user_root = tmp_path / "isolated-user"
    save_azure_openai_provider(base_url="https://model.example.test/openai/v1/", model="first-deployment", api_key="synthetic-key", user_config_root=user_root)
    source = Settings(agent_runtime=AgentRuntimeSettings(reasoning_effort=effort, tracing_enabled=False))
    source.agent.name = "snapshot-agent"
    source.output.runs_dir = tmp_path / "runs"
    original = source.model_dump()
    resolved = refresh_provider_settings(source, user_root)
    assert source.model_dump() == original
    assert source.agent_runtime.provider is None
    assert source.agent_runtime.model == ""
    assert resolved is not source
    assert resolved.agent_runtime is not source.agent_runtime
    assert resolved.agent_runtime.reasoning_effort == effort
    assert resolved.agent_runtime.model == "first-deployment"

    runtime = DefaultCodingAgentRuntime(resolved, _NoHelperTools(), engine=CapturingEngine())
    task = Task(id="snapshot-task", description="Inspect.", verification_goal="Visible.")
    plan = await runtime.run_pre_plan("Inspect.", KnowledgeBundle(), [], "snapshot-run")
    assert plan.verification_goal == "Visible."
    assert len(agent_requests) == 1
    assert model_requests == []

    save_azure_openai_provider(base_url="https://model.example.test/openai/v1/", model="next-deployment", api_key="next-synthetic-key", user_config_root=user_root)
    source.agent_runtime.reasoning_effort = next_effort
    next_settings = refresh_provider_settings(source, user_root)
    assert next_settings.agent_runtime.model == "next-deployment"
    assert next_settings.agent_runtime.reasoning_effort == next_effort
    assert source.agent_runtime.provider is None
    assert source.agent_runtime.model == ""
    assert resolved.agent_runtime.reasoning_effort == effort
    assert resolved.agent_runtime.model == "first-deployment"

    assert (await _run_main(runtime, task, "snapshot-run"))[-1].status == "success"
    assert (await runtime.run_verification(task, [], "snapshot-run", None))[-1].status == "success"
    assert len(evaluators) == 1
    assert [request.name for _, request in agent_requests] == ["snapshot-agent pre-planner", "snapshot-agent", "snapshot-agent verifier"]
    assert [(name, request.reasoning_effort) for name, request in agent_requests] == [("first-deployment", effort)] * 3
    assert [(name, request.reasoning_effort) for name, request in model_requests] == [("first-deployment", effort)]

    next_runtime = DefaultCodingAgentRuntime(next_settings, _NoHelperTools(), engine=CapturingEngine())
    await next_runtime.run_pre_plan("Inspect.", KnowledgeBundle(), [], "next-run")
    assert (await _run_main(next_runtime, task, "next-run"))[-1].status == "success"
    assert (await next_runtime.run_verification(task, [], "next-run", None))[-1].status == "success"
    assert [(name, request.reasoning_effort) for name, request in agent_requests] == [("first-deployment", effort)] * 3 + [("next-deployment", next_effort)] * 3
    assert [(name, request.reasoning_effort) for name, request in model_requests] == [("first-deployment", effort), ("next-deployment", next_effort)]
    assert len(evaluators) == 2
    assert len(provider_instances) == 8
    assert all(provider.closed for provider in provider_instances)


@pytest.fixture(params=["openai", "google_gemini", "github_copilot"])
def structured_session(request, monkeypatch, tmp_path):
    provider = request.param
    settings = Settings(agent_runtime=AgentRuntimeSettings(provider=provider, tracing_enabled=False))
    settings.agent_runtime.base_url = "https://api.openai.com/v1/" if provider == "openai" else "https://generativelanguage.googleapis.com/v1beta/"
    settings.agent_runtime.api_key = "synthetic-model-key"
    settings.agent_runtime.model = "test-model"
    if provider == "github_copilot":
        settings.agent_runtime.base_url = "https://api.enterprise.githubcopilot.com/"
        settings.agent_runtime.provider_token = {"token": "synthetic-model-key", "expires_at": time.time() + 3600, "plan": "enterprise"}
        settings.agent_runtime.github_token = None
        settings.agent_runtime.user_config_root = tmp_path / "isolated-user"
    control = {"text": '{"value":"private-candidate"}', "failure": None}
    payloads, clients = [], []

    def respond(http_request):
        if provider == "github_copilot":
            assert str(http_request.url) == "https://api.enterprise.githubcopilot.com/responses"
            assert http_request.headers["authorization"] == "Bearer synthetic-model-key"
            assert http_request.headers["copilot-integration-id"] == "vscode-chat"
            assert http_request.headers["openai-intent"] == "conversation-agent"
        payloads.append(json.loads(http_request.content))
        failure = control["failure"]
        if provider == "google_gemini":
            response = {"id": "single", "model": "test-model", "status": "completed", "steps": [{"type": "model_output", "content": [{"type": "text", "text": control["text"]}]}]}
            if failure == "refusal":
                response.update(status="failed", errors=[{"code": "SAFETY", "message": "private-refusal"}])
            elif failure == "incomplete":
                response["status"] = "incomplete"
        else:
            response = {
                "id": "single",
                "object": "response",
                "created_at": 1,
                "model": "test-model",
                "status": "completed",
                "output": [{"id": "message", "type": "message", "role": "assistant", "status": "completed", "content": [{"type": "output_text", "text": control["text"], "annotations": []}]}],
            }
            if failure == "refusal":
                response["output"][0]["content"].append({"type": "refusal", "refusal": "private-refusal"})
            elif failure == "incomplete":
                response.update(status="incomplete", incomplete_details={"reason": "content_filter"})
        return httpx.Response(200, json=response)

    if provider == "google_gemini":
        from fsq_agent.agent_engine import _google_gemini_backend

        original = _google_gemini_backend.genai.Client

        def make_client(**kwargs):
            kwargs["http_options"]["async_client_args"]["transport"] = httpx.MockTransport(respond)
            kwargs["http_options"]["retry_options"] = {"attempts": 1}
            client = original(**kwargs)
            clients.append(client._api_client._async_httpx_client)
            return client

        monkeypatch.setattr(_google_gemini_backend.genai, "Client", make_client)
    else:

        def make_client(**kwargs):
            client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
            clients.append(client)
            return AsyncOpenAI(http_client=client, max_retries=0, **kwargs)

        monkeypatch.setattr(_openai_backend, "AsyncOpenAI", make_client)
    session = build_model_provider_session(settings)
    yield session, control, payloads
    session.close_sync()
    assert all(client.is_closed for client in clients)
    if provider == "github_copilot":
        assert not settings.agent_runtime.user_config_root.exists()


@pytest.mark.parametrize("service", ["assertion", "suggestion"])
@pytest.mark.parametrize("effort,next_effort", [("low", "high"), ("high", "low")])
def test_reasoning_effort_service_factory_captures_value_before_source_settings_change(structured_session, monkeypatch, service, effort, next_effort):
    session, control, payloads = structured_session
    settings = Settings(agent_runtime=AgentRuntimeSettings(reasoning_effort=effort))
    monkeypatch.setattr("fsq_agent.ai_services._factory.build_model_provider_session", lambda configured: session)
    if service == "assertion":
        service_instance = build_ai_assertion_evaluator(settings)
        control["text"] = '{"passed":true,"explanation":"Visible.","confidence":null}'
    else:
        service_instance = build_case_suggestion_analyzer(settings)
        control["text"] = '{"summary":"No changes.","suggestions":[],"candidate_case_yaml":null}'
    settings.agent_runtime.reasoning_effort = next_effort
    if service == "assertion":
        assert service_instance.evaluate(AIAssertionRequest(platform="web", prompt="Visible?")).status == "passed"
    else:
        assert service_instance.analyze(parsed_case={}, execution_report={}).summary == "No changes."
    assert len(payloads) == 1
    if session.provider == "google_gemini":
        assert payloads[0]["generation_config"] == {"thinking_level": effort}
    else:
        assert payloads[0]["reasoning"] == {"effort": effort}


@pytest.mark.parametrize("service", ["assertion", "suggestion"])
@pytest.mark.parametrize("effort", [None, "low", "high", "auto"])
def test_services_reasoning_effort_constructor_compatibility(structured_session, service, effort):
    session, control, payloads = structured_session
    options = {} if effort is None else {"reasoning_effort": effort}
    if service == "assertion":
        control["text"] = '{"passed":true,"explanation":"Visible.","confidence":null}'
        result = AIAssertionEvaluator(session, **options).evaluate(AIAssertionRequest(platform="web", prompt="Visible?"))
        if effort == "auto":
            assert result.status == "error"
            assert result.metadata["engine_error"]["category"] == "configuration"
        else:
            assert result.status == "passed"
    else:
        control["text"] = '{"summary":"No changes.","suggestions":[],"candidate_case_yaml":null}'
        analyzer = CaseSuggestionAnalyzer(session, **options)
        if effort == "auto":
            with pytest.raises(EngineError) as failure:
                analyzer.analyze(parsed_case={}, execution_report={})
            assert failure.value.category == "configuration"
        else:
            assert analyzer.analyze(parsed_case={}, execution_report={}).summary == "No changes."
    if effort == "auto":
        assert payloads == []
    else:
        assert len(payloads) == 1
        native = "medium" if effort is None else effort
        if session.provider == "google_gemini":
            assert payloads[0]["generation_config"] == {"thinking_level": native}
        else:
            assert payloads[0]["reasoning"] == {"effort": native}


@pytest.mark.parametrize("failure_kind", [None, "refusal", "incomplete"])
def test_single_parser_runs_only_after_protocol_checks(structured_session, failure_kind):
    session, control, payloads = structured_session
    control["failure"] = failure_kind
    candidates = []
    parsed = object()

    def parse(text):
        candidates.append(text)
        return parsed

    request = ModelRequest("Check", output=OutputContract("output", {"type": "object", "properties": {"value": {"type": "string"}}}, parse))
    if failure_kind is None:
        result = session.complete_sync(request)
        assert result.parsed_output is parsed
        assert result.text == control["text"]
        assert candidates == [control["text"]]
    else:
        with pytest.raises(EngineError) as failure:
            session.complete_sync(request)
        assert failure.value.category == failure_kind
        assert candidates == []
        assert "private" not in str(failure.value)
    assert len(payloads) == 1


@pytest.mark.parametrize(
    "parser_error,category", [(ValueError("private-validator"), "invalid_output"), (TypeError("private-validator"), "invalid_output"), (RuntimeError("private-programming"), "runtime")]
)
def test_single_parser_failures_are_safe_and_not_retried(structured_session, parser_error, category):
    session, _control, payloads = structured_session
    candidates = []

    def parse(text):
        candidates.append(text)
        raise parser_error

    with pytest.raises(EngineError) as failure:
        session.complete_sync(ModelRequest("Check", output=OutputContract("output", {"type": "object"}, parse)))
    assert failure.value.category == category
    assert "private" not in str(failure.value)
    assert failure.value.__cause__ is None
    assert len(candidates) == len(payloads) == 1


def test_single_parser_cancellation_propagates_and_closes(structured_session):
    session, _control, payloads = structured_session
    interruption = asyncio.CancelledError()

    def parse(text):
        raise interruption

    with pytest.raises(asyncio.CancelledError) as failure:
        session.complete_sync(ModelRequest("Check", output=OutputContract("output", {"type": "object"}, parse)))
    assert failure.value is interruption
    assert len(payloads) == 1


def test_single_unsupported_schema_fails_before_request(structured_session):
    session, _control, payloads = structured_session
    schema = {"type": "object", "properties": {"value": False}}
    with pytest.raises(EngineError) as failure:
        session.complete_sync(ModelRequest("Check", output=OutputContract("output", schema, json.loads)))
    assert failure.value.category == "configuration"
    assert payloads == []
    assert schema == {"type": "object", "properties": {"value": False}}


@pytest.mark.parametrize(
    "text",
    [
        "",
        " \n",
        "null",
        "[]",
        "not JSON",
        '{"passed":false,"explanation":"Absent"}',
        '{"passed":"false","explanation":"Absent","confidence":null}',
        '{"passed":1,"explanation":"Absent","confidence":null}',
        '{"passed":true,"explanation":1,"confidence":null}',
        '{"passed":true,"explanation":" ","confidence":null}',
        '{"passed":true,"explanation":"Visible","confidence":true}',
        '{"passed":true,"explanation":"Visible","confidence":"0.9"}',
        '{"passed":true,"explanation":"Visible","confidence":1.1}',
        '{"passed":true,"explanation":"Visible","confidence":NaN}',
        '{"passed":true,"explanation":"Visible","confidence":Infinity}',
        '{"passed":true,"explanation":"Visible","confidence":null,"extra":true}',
        '```json\n{"passed":true,"explanation":"Visible","confidence":null}\n```',
        'prose {"passed":true,"explanation":"Visible","confidence":null}',
    ],
)
def test_assertion_invalid_output_through_real_clients(structured_session, text):
    session, control, payloads = structured_session
    control["text"] = text
    result = AIAssertionEvaluator(session).evaluate(AIAssertionRequest(platform="web", prompt="Check"))
    assert result.status == "error"
    assert result.passed is False
    assert result.metadata["engine_error"]["category"] == "invalid_output"
    assert len(payloads) == 1


@pytest.mark.parametrize(
    "text",
    [
        "",
        " \n",
        "null",
        "[]",
        "not JSON",
        '{"summary":"Good","suggestions":[]}',
        '{"summary":1,"suggestions":[],"candidate_case_yaml":null}',
        '{"summary":" ","suggestions":[],"candidate_case_yaml":null}',
        '{"summary":"Good","suggestions":[{"kind":"custom","message":" "}],"candidate_case_yaml":null}',
        '{"summary":"Good","suggestions":[{"kind":"custom","message":"Good","extra":1}],"candidate_case_yaml":null}',
        '{"summary":"Good","suggestions":[],"candidate_case_yaml":1}',
        '{"summary":"Good","suggestions":[],"candidate_case_yaml":" "}',
        '{"summary":"Good","suggestions":[],"candidate_case_yaml":null,"extra":1}',
        '```json\n{"summary":"Good","suggestions":[],"candidate_case_yaml":null}\n```',
    ],
)
def test_suggestion_invalid_output_through_real_clients(structured_session, text):
    session, control, payloads = structured_session
    control["text"] = text
    with pytest.raises(EngineError) as failure:
        CaseSuggestionAnalyzer(session).analyze(parsed_case={}, execution_report={})
    assert failure.value.category == "invalid_output"
    assert len(payloads) == 1
