# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import json
from copy import deepcopy

import httpx
import pytest
from openai import AsyncOpenAI

from fsq_agent.adapters.coding_agent._harness_tools import HarnessToolAdapter
from fsq_agent.agent_engine import AgentRequest, create_agent_engine_for_model, create_google_gemini_model_provider, create_model_provider
from fsq_agent.agent_engine._google_gemini_schema import google_schema
from fsq_agent.agent_engine._schema import ensure_strict_json_schema
from fsq_agent.drivers.web._playwright import PlaywrightWebDriver
from fsq_agent.harnesses._web import WebHarness
from fsq_agent.models import RunnerStepResult, StepPhaseReport, WebClickOnParams, WebPressKeyParams


def _schema_nodes(value):
    if isinstance(value, dict):
        yield value
        for item in value.values():
            yield from _schema_nodes(item)
    elif isinstance(value, list):
        for item in value:
            yield from _schema_nodes(item)


def _tags(schema):
    tags = set()
    for node in _schema_nodes(schema):
        kind = node.get("properties", {}).get("kind", {})
        if "const" in kind:
            tags.add(kind["const"])
        else:
            tags.update(kind.get("enum", []))
    return tags


def _provider(monkeypatch, provider_name, respond):
    clients = []
    if provider_name == "openai":
        from fsq_agent.agent_engine import _openai_backend

        def make_client(**kwargs):
            client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
            clients.append(client)
            return AsyncOpenAI(http_client=client, max_retries=0, **kwargs)

        monkeypatch.setattr(_openai_backend, "AsyncOpenAI", make_client)
        provider = create_model_provider(base_url="https://model.example.test/v1/", api_key="synthetic-key")
    else:
        from fsq_agent.agent_engine import _google_gemini_backend

        original = _google_gemini_backend.genai.Client

        def make_client(**kwargs):
            kwargs["http_options"]["async_client_args"]["transport"] = httpx.MockTransport(respond)
            kwargs["http_options"]["retry_options"] = {"attempts": 1}
            client = original(**kwargs)
            clients.append(client._api_client._async_httpx_client)
            return client

        monkeypatch.setattr(_google_gemini_backend.genai, "Client", make_client)
        provider = create_google_gemini_model_provider(base_url="https://generativelanguage.googleapis.com/v1beta/", api_key="synthetic-key")
    return provider, clients


def _response(provider_name, stream, call):
    if provider_name == "openai":
        output = (
            [{"id": f"fn-{call['id']}", "type": "function_call", "call_id": call["id"], "name": call["name"], "arguments": json.dumps(call["arguments"]), "status": "completed"}]
            if call
            else [{"id": "message", "type": "message", "role": "assistant", "status": "completed", "content": [{"type": "output_text", "text": "Done.", "annotations": []}]}]
        )
        response = {"id": "response", "object": "response", "created_at": 1, "model": "test-model", "status": "completed", "output": output}
        event_name = "response.completed"
        event = {"type": event_name, "sequence_number": 0, "response": response}
    else:
        steps = [{"type": "function_call", **call}] if call else [{"type": "model_output", "content": [{"type": "text", "text": "Done."}]}]
        response = {"id": "interaction", "model": "test-model", "status": "requires_action" if call else "completed", "steps": steps}
        event_name = "interaction.completed"
        event = {"event_type": event_name, "interaction": response}
    if stream:
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=f"event: {event_name}\ndata: {json.dumps(event)}\n\n")
    return httpx.Response(200, json=response)


@pytest.mark.parametrize("provider_name", ["openai", "google_gemini"])
@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("operation", ["click_on", "press_key"])
async def test_real_clients_convert_all_web_tools_and_continue_after_invalid_input(monkeypatch, provider_name, stream, operation):
    target = {
        "page": "main",
        "steps": [
            {"kind": "role", "role": "listitem"},
            {"kind": "filter", "has": {"steps": [{"kind": "role", "role": "button", "name": "Save", "disabled": False}, {"kind": "filter", "visible": True}]}},
            {"kind": "first"},
            {"kind": "role", "role": "button", "name": "Save"},
        ],
    }
    params = (
        WebClickOnParams(target=target, expect={"kind": "popup", "page": "receipt"})
        if operation == "click_on"
        else WebPressKeyParams(
            key="Control+Enter",
            scope={"kind": "element", "target": target},
            expect={"kind": "dialog", "dialog_type": "prompt", "action": "accept", "prompt_text": {"text": "approval_code", "textType": "runtimeSecret"}},
        )
    )
    arguments = params.model_dump(mode="json")
    full_results, invoked, requests, events = [], [], [], []
    harness = WebHarness(PlaywrightWebDriver())
    adapter = HarnessToolAdapter(harness, run_id="schema-run", platform="web", on_full_result=lambda call_id, payload: full_results.append((call_id, payload)))
    tools = tuple(adapter.build_tools())
    original_schemas = {tool.name: deepcopy(tool.parameters_schema) for tool in tools}

    def run_step(*, run_id, step):
        parsed = type(params).model_validate(step.params)
        invoked.append(parsed)
        assert run_id == "schema-run"
        return RunnerStepResult(
            step_id="schema-execution",
            status="passed",
            action_status="passed",
            duration_ms=0,
            metadata={"action_effect": "completed"},
            phase_reports=[
                StepPhaseReport(
                    step_id="schema-execution",
                    phase="invoke",
                    status="passed",
                    metadata={"safe_replay_params": parsed.model_dump(mode="json"), "harness_output": {"page": "main", "completed": True}},
                ),
            ],
        )

    monkeypatch.setattr(adapter.runner, "run_step", run_step)

    def respond(request):
        assert request.url.path.endswith("responses" if provider_name == "openai" else "interactions")
        requests.append(json.loads(request.content))
        if len(requests) == 1:
            call = {"id": "invalid-web", "name": operation, "arguments": []}
        elif len(requests) == 2:
            call = {"id": "valid-web", "name": operation, "arguments": arguments}
        else:
            call = None
        return _response(provider_name, stream, call)

    async def on_event(event):
        events.append(event)

    provider, clients = _provider(monkeypatch, provider_name, respond)
    try:
        model = provider.get_model("test-model")
        result = await create_agent_engine_for_model(model).run(model, AgentRequest("Web schemas", "Use declared Web tools.", "Inspect.", tools=tools, stream=stream), on_event=on_event)
        assert result.final_output == "Done."
        assert len(requests) == 3
        assert invoked == [params]
        assert all(tool.strict for tool in tools)
        assert {"click_on", "press_key", "select_option", "wait_for", "ui_snapshot", "find_elements", "inspect_element"} <= original_schemas.keys()
        assert "upload_files" not in original_schemas
        for request in requests:
            assert len(request["tools"]) == len(tools)
            for tool in request["tools"]:
                converted = tool["parameters"]
                expected = ensure_strict_json_schema(original_schemas[tool["name"]]) if provider_name == "openai" else google_schema(original_schemas[tool["name"]])
                assert converted == expected
                assert _tags(converted) == _tags(original_schemas[tool["name"]])
                assert converted["description"] == original_schemas[tool["name"]]["description"]
                for node in _schema_nodes(converted):
                    assert "oneOf" not in node
                    assert "discriminator" not in node
                    if node.get("type") == "object":
                        assert node["additionalProperties"] is False
                        if provider_name == "openai":
                            assert set(node["required"]) == set(node["properties"])
                if provider_name == "openai":
                    assert tool["strict"] is True
        assert {tool.name: tool.parameters_schema for tool in tools} == original_schemas
        history = requests[-1]["input"]
        output_texts = (
            [item["output"] for item in history if item.get("type") == "function_call_output"]
            if provider_name == "openai"
            else [item["result"][0]["text"] for item in history if item["type"] == "function_result"]
        )
        outputs = [json.loads(text) for text in output_texts]
        assert len(outputs) == 2
        assert outputs[0]["status"] == "failed"
        assert outputs[0]["action_effect"] == "not_started"
        assert outputs[1]["status"] == "passed"
        assert all(len(text) <= 16000 for text in output_texts)
        if stream:
            assert [event.call_id for event in events if event.kind == "tool_output"] == ["invalid-web", "valid-web"]
        assert [call_id for call_id, _ in full_results] == ["invalid-web", "valid-web"]
        assert full_results[-1][1]["safe_replay_params"] == arguments
    finally:
        await provider.aclose()
        harness.close()
    assert clients
    assert all(client.is_closed for client in clients)
