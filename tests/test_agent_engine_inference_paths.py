# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import builtins
import json
from pathlib import Path

import httpx
import pytest
from openai import AsyncOpenAI

from fsq_agent.adapters.coding_agent import DefaultCodingAgentRuntime
from fsq_agent.agent_engine import EngineError, _openai_backend
from fsq_agent.ai_services import AIAssertionEvaluator, CaseSuggestionAnalyzer
from fsq_agent.config import Settings
from fsq_agent.models import AgentFinalOutput, AgentRuntimeSettings, AIAssertionRequest, ConfigurationError, GoalPrePlan, KnowledgeBundle, PlanningError, Task
from fsq_agent.providers import build_model_provider_session, test_model_provider_connection


class _NoActionHarness:
    def action_space(self) -> list:
        return []


class _NoHelperTools:
    def build_tools(self, **kwargs) -> list:
        return []


@pytest.mark.parametrize("path", ["pre_plan", "main", "verification", "assertion", "connection", "suggestion"])
@pytest.mark.parametrize("failure_kind", [None, "incomplete", "failed", "refusal"])
@pytest.mark.parametrize("provider", ["azure_openai", "openai", "google_gemini"])
async def test_six_inference_paths_use_real_engine_without_sdk_import(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, path: str, failure_kind: str | None, provider: str) -> None:
    settings = Settings(agent_runtime=AgentRuntimeSettings(provider=provider, tracing_enabled=False))
    settings.agent_runtime.base_url = "https://api.openai.com/v1/" if provider == "openai" else "https://model.example.test/openai/v1/"
    if provider == "google_gemini":
        settings.agent_runtime.base_url = "https://generativelanguage.googleapis.com/v1beta/"
    settings.agent_runtime.api_key = "synthetic-model-key"
    settings.agent_runtime.model = "test-model"
    settings.output.runs_dir = tmp_path
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
            result = await runtime.run_task(task, KnowledgeBundle(), [], "integration-run")
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
        result = AIAssertionEvaluator(build_model_provider_session(settings)).evaluate(AIAssertionRequest(platform="web", prompt="Visible?", screenshot_path=screenshot))
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
            assert result.model == "test-model"
    else:
        analyzer = CaseSuggestionAnalyzer(build_model_provider_session(settings))
        if failure_kind is not None:
            with pytest.raises(EngineError):
                analyzer.analyze(parsed_case={"platform": "web"}, execution_report={"status": "success"})
        else:
            result = analyzer.analyze(parsed_case={"platform": "web"}, execution_report={"status": "success"})
            assert result.summary == "No change needed."
            assert result.candidate_case_yaml is None
    assert len(payloads) == 1
    assert payloads[0]["model"] == "test-model"
    if provider == "google_gemini":
        assert payloads[0]["store"] is False
    if path in {"assertion", "connection", "suggestion"}:
        assert not payloads[0].get("tools")
        assert not payloads[0].get("stream")
        assert not payloads[0].get("reasoning")
    assert clients
    assert all(client.is_closed for client in clients)
