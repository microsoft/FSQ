# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import asyncio
import importlib
import json
from copy import deepcopy

import httpx
import pytest
from google import genai

from fsq_agent.agent_engine import AgentRequest, EngineError, ModelRequest, OutputContract, ToolBinding, create_agent_engine_for_model, create_google_gemini_model_provider
from fsq_agent.models import AgentFinalOutput


def _interaction(steps, *, status="completed"):
    return {"id": "interaction_test", "status": status, "model": "gemini-3.8-flash", "steps": steps, "usage": {"total_input_tokens": 3, "total_output_tokens": 2, "total_tokens": 5}}


def _output(text="done"):
    return {"type": "model_output", "content": [{"type": "text", "text": text}]}


def _provider(monkeypatch, handler):
    backend = importlib.import_module("fsq_agent.agent_engine._google_gemini_backend")
    clients = []
    original = genai.Client

    def make_client(**kwargs):
        kwargs["http_options"]["async_client_args"]["transport"] = httpx.MockTransport(handler)
        kwargs["http_options"]["retry_options"] = {"attempts": 1}
        client = original(**kwargs)
        clients.append(client._api_client._async_httpx_client)
        return client

    monkeypatch.setattr(backend.genai, "Client", make_client)
    return create_google_gemini_model_provider(base_url="https://generativelanguage.googleapis.com/v1beta/", api_key="synthetic-key"), clients


def _http(payload, stream):
    if not stream:
        return httpx.Response(200, json=payload)
    event = {"event_type": "interaction.completed", "interaction": payload}
    return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=f"event: interaction.completed\ndata: {json.dumps(event)}\n\n")


@pytest.mark.parametrize("stream", [False, True])
async def test_gemini_shared_loop_preserves_private_steps_and_filters_results(monkeypatch, stream):
    payloads = []
    calls = []
    final = AgentFinalOutput(status="success", summary="Done.")
    thought = {"type": "thought", "signature": "b3BhcXVl", "summary": []}

    def respond(request):
        payload = json.loads(request.content)
        payloads.append(payload)
        if len(payloads) == 1:
            steps = [
                thought,
                {"type": "function_call", "id": "call_one", "name": "observe", "arguments": {"value": "one"}},
                {"type": "function_call", "id": "call_two", "name": "observe", "arguments": {"value": "two"}},
            ]
            return _http(_interaction(steps, status="requires_action"), stream)
        return _http(_interaction([_output(final.model_dump_json())]), stream)

    async def invoke(call):
        calls.append(call)
        return "raw-result"

    observed = []

    def filter_outputs(entries):
        observed.extend(entries)
        return {entry.entry_id: "bounded-result" for entry in entries}

    provider, transport = _provider(monkeypatch, respond)
    try:
        model = provider.get_model("gemini-3.8-flash")
        result = await create_agent_engine_for_model(model).run(
            model,
            AgentRequest(
                name="test",
                instructions="Inspect",
                input="Check",
                stream=stream,
                tools=(ToolBinding(name="observe", description="Inspect", parameters_schema={"type": "object", "properties": {"value": {"type": "string"}}, "required": ["value"]}, invoke=invoke),),
                output=OutputContract(name="final", schema=AgentFinalOutput.model_json_schema(), parse=AgentFinalOutput.model_validate_json),
                tool_output_filter=filter_outputs,
            ),
        )
        assert result.final_output == final
        assert [call.call_id for call in calls] == ["call_one", "call_two"]
        assert result.usage.requests == 2
        assert all(payload["store"] is False and "previous_interaction_id" not in payload for payload in payloads)
        history = payloads[1]["input"]
        assert thought in history
        outputs = [step for step in history if step["type"] == "function_result"]
        assert [step["call_id"] for step in outputs] == ["call_one", "call_two"]
        assert all(step["result"] == [{"type": "text", "text": "bounded-result"}] for step in outputs)
        assert [entry.output for entry in observed] == ["raw-result", "raw-result"]
    finally:
        await provider.aclose()
    assert transport
    assert all(client.is_closed for client in transport)


@pytest.mark.parametrize("status,category", [("incomplete", "incomplete"), ("failed", "invalid_output"), ("in_progress", "invalid_output")])
async def test_gemini_direct_rejects_partial_result(monkeypatch, status, category):
    provider, transport = _provider(monkeypatch, lambda request: _http(_interaction([_output("private-partial")], status=status), False))
    try:
        with pytest.raises(EngineError) as failure:
            await provider.get_model("gemini-3.8-flash").complete(ModelRequest(input="private-input"))
        assert failure.value.category == category
        assert "private" not in str(failure.value)
    finally:
        await provider.aclose()
    assert transport
    assert all(client.is_closed for client in transport)


async def test_gemini_direct_request_and_closed_model(monkeypatch):
    provider, transport = _provider(monkeypatch, lambda request: _http(_interaction([_output()]), False))
    model = provider.get_model("gemini-3.8-flash")
    result = await model.complete(ModelRequest(input="Check"))
    assert result.text == "done"
    assert result.parsed_output is None
    assert result.usage.total_tokens == 5
    await provider.aclose()
    await provider.aclose()
    assert transport
    assert all(client.is_closed for client in transport)
    with pytest.raises(EngineError, match="closed"):
        await model.complete(ModelRequest(input="again"))


async def test_gemini_direct_structured_output_without_tools(monkeypatch):
    payloads, candidates = [], []
    text = ' {"value": null}\n'
    parsed = {"value": None}
    schema = {"type": "object", "properties": {"value": {"anyOf": [{"type": "string"}, {"type": "null"}]}}, "required": ["value"], "additionalProperties": False}
    original = deepcopy(schema)

    def parse(candidate):
        candidates.append(candidate)
        return parsed

    def respond(request):
        payloads.append(json.loads(request.content))
        return _http(_interaction([_output(text)]), False)

    provider, transport = _provider(monkeypatch, respond)
    try:
        result = await provider.get_model("gemini-3.8-flash").complete(ModelRequest("Check", output=OutputContract("single", schema, parse)))
        assert result.parsed_output is parsed
        assert result.text == text
        assert result.usage.total_tokens == 5
        assert candidates == [text]
        assert schema == original
        assert len(payloads) == 1
        payload = payloads[0]
        assert payload["store"] is False
        assert "tools" not in payload
        assert "previous_interaction_id" not in payload
        assert payload["response_format"]["type"] == "text"
        assert payload["response_format"]["mime_type"] == "application/json"
        assert payload["response_format"]["schema"]["required"] == ["value"]
    finally:
        await provider.aclose()
    assert all(client.is_closed for client in transport)


@pytest.mark.parametrize("terminal", [True, False])
async def test_gemini_real_sdk_aggregates_argument_deltas_before_effects(monkeypatch, terminal):
    payloads, called = [], []

    def respond(request):
        payloads.append(json.loads(request.content))
        if len(payloads) > 1:
            return _http(_interaction([_output()]), True)
        events = [
            {"event_type": "step.start", "index": 0, "step": {"type": "function_call", "id": "call_delta", "name": "inspect", "arguments": {}}},
            {"event_type": "step.delta", "index": 0, "delta": {"type": "arguments_delta", "arguments": '{"value":'}},
            {"event_type": "step.delta", "index": 0, "delta": {"type": "arguments_delta", "arguments": '"complete"}'}},
            {"event_type": "step.stop", "index": 0},
        ]
        if terminal:
            events.append({"event_type": "interaction.completed", "interaction": {"id": "interaction_test", "status": "requires_action"}})
        content = "".join(f"event: {event['event_type']}\ndata: {json.dumps(event)}\n\n" for event in events)
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=content)

    async def invoke(call):
        called.append(call.arguments)
        return "ok"

    provider, clients = _provider(monkeypatch, respond)
    model = provider.get_model("gemini-3.8-flash")
    request = AgentRequest(
        name="delta",
        instructions="Inspect",
        input="Check",
        stream=True,
        tools=(ToolBinding(name="inspect", description="Inspect", parameters_schema={"type": "object", "properties": {"value": {"type": "string"}}}, invoke=invoke),),
    )
    try:
        if terminal:
            assert (await create_agent_engine_for_model(model).run(model, request)).final_output == "done"
            assert called == [{"value": "complete"}]
        else:
            with pytest.raises(EngineError):
                await create_agent_engine_for_model(model).run(model, request)
            assert called == []
    finally:
        await provider.aclose()
    assert all(client.is_closed for client in clients)


@pytest.mark.parametrize(
    "schema",
    [
        {"type": "object", "properties": {"value": {"$ref": "#/$defs/Missing"}}},
        {"$ref": "https://untrusted.test/schema"},
        {"type": "not-a-type"},
        {"required": "name"},
        {"required": ["name", "name"]},
        {"type": "string", "maxLength": -1},
        {"enum": []},
        {"items": False},
        {"type": "integer", "minimum": True},
        {"properties": {"value": False}},
        {"anyOf": []},
        {"additionalProperties": "false"},
        {"pattern": "["},
        {"anyOf": [{"type": "string"}, {"type": "integer"}], "$ref": "#/anyOf/01"},
        {"anyOf": [{"type": "string"}, {"type": "integer"}], "$ref": "#/anyOf/00"},
        {"anyOf": [{"type": "string"}, {"type": "integer"}], "$ref": "#/anyOf/+1"},
    ],
)
@pytest.mark.parametrize("contract", ["output", "tool"])
async def test_invalid_google_schema_fails_before_request(monkeypatch, schema, contract):
    requests = []

    async def forbidden(call):
        pytest.fail("Invalid schema executed a tool")

    def respond(request):
        requests.append(request)
        return _http(_interaction([_output()]), False)

    provider, _clients = _provider(monkeypatch, respond)
    request = AgentRequest(
        name="schema",
        input="check",
        instructions="check",
        output=OutputContract(name="test", schema=schema, parse=json.loads) if contract == "output" else None,
        tools=(ToolBinding(name="test", description="test", parameters_schema=schema, invoke=forbidden),) if contract == "tool" else (),
    )
    try:
        model = provider.get_model("gemini-3.8-flash")
        with pytest.raises(EngineError) as caught:
            await create_agent_engine_for_model(model).run(model, request)
        assert caught.value.category == "configuration"
        assert requests == []
    finally:
        await provider.aclose()


@pytest.mark.parametrize("agent", [False, True])
@pytest.mark.parametrize("status,category", [(401, "authentication"), (403, "authentication"), (404, "unavailable"), (429, "rate_limit"), (503, "unavailable"), (None, "timeout")])
async def test_native_errors_preserve_categories_and_connection_guidance(monkeypatch, agent, status, category):
    from fsq_agent.providers._connection_test import _connection_error

    def respond(request):
        if status is None:
            raise httpx.ReadTimeout("private-timeout synthetic-key", request=request)
        return httpx.Response(status, json={"error": {"code": status, "message": "private-error synthetic-key"}})

    provider, clients = _provider(monkeypatch, respond)
    model = provider.get_model("gemini-3.8-flash")
    try:
        operation = (
            create_agent_engine_for_model(model).run(model, AgentRequest(name="errors", instructions="Check", input="private-input")) if agent else model.complete(ModelRequest(input="private-input"))
        )
        with pytest.raises(EngineError) as caught:
            await operation
        assert caught.value.category == category
        assert caught.value.status_code == status
        assert "private" not in str(caught.value)
        assert "synthetic-key" not in str(caught.value)
        connection = _connection_error(caught.value)
        guidance = {401: "authentication failed", 403: "authentication failed", 404: "not found", 429: "rate limit", 503: "temporarily unavailable", None: "timed out"}
        assert guidance[status] in str(connection)
    finally:
        await provider.aclose()
    assert all(client.is_closed for client in clients)


async def test_google_environment_cannot_select_debug_or_vertex_client(monkeypatch, tmp_path):
    from google.genai._api_client import BaseApiClient

    for name, value in {
        "GOOGLE_GENAI_CLIENT_MODE": "auto",
        "GOOGLE_GENAI_REPLAY_ID": "",
        "GOOGLE_GENAI_REPLAYS_DIRECTORY": str(tmp_path),
        "GOOGLE_GENAI_USE_VERTEXAI": "true",
        "GOOGLE_GENAI_USE_ENTERPRISE": "true",
        "GOOGLE_API_KEY": "wrong-key",
        "GEMINI_API_KEY": "wrong-key",
        "GOOGLE_GEMINI_BASE_URL": "https://untrusted.test",
        "GOOGLE_CLOUD_PROJECT": "other",
        "GOOGLE_CLOUD_LOCATION": "other",
    }.items():
        monkeypatch.setenv(name, value)

    def respond(request):
        assert str(request.url) == "https://generativelanguage.googleapis.com/v1beta/interactions"
        assert request.headers["x-goog-api-key"] == "synthetic-key"
        return _http(_interaction([_output()]), False)

    provider, clients = _provider(monkeypatch, respond)
    try:
        await provider.get_model("gemini-3.8-flash").complete(ModelRequest(input="check"))
        api_client = provider._client._api_client
        assert type(api_client) is BaseApiClient
        sync_client = api_client._httpx_client
    finally:
        await provider.aclose()
    assert sync_client.is_closed
    assert all(client.is_closed for client in clients)


@pytest.mark.parametrize(
    "case,category",
    [("safety", "refusal"), ("tokens", "incomplete"), ("budget", "incomplete"), ("failed_then_complete", "invalid_output"), ("unfinished", "invalid_output"), ("mismatched", "invalid_output")],
)
async def test_stream_failure_facts_prevent_output_and_tools(monkeypatch, case, category):
    calls, emitted = [], []
    call = {"type": "function_call", "name": "inspect", "id": "call_test", "arguments": {}}
    complete = {"event_type": "interaction.completed", "interaction": _interaction([call])}
    events = {
        "safety": [{"event_type": "error", "error": {"code": "SAFETY", "message": "private-detail"}}],
        "tokens": [{"event_type": "error", "error": {"code": "MAX_TOKENS", "message": "private-detail"}}],
        "budget": [{"event_type": "interaction.status_update", "interaction_id": "interaction_test", "status": "budget_exceeded"}],
        "failed_then_complete": [{"event_type": "interaction.status_update", "interaction_id": "interaction_test", "status": "failed"}, complete],
        "unfinished": [{"event_type": "step.start", "index": 0, "step": call}, complete],
        "mismatched": [
            {"event_type": "step.start", "index": 0, "step": call},
            {"event_type": "step.stop", "index": 0},
            {"event_type": "interaction.completed", "interaction": _interaction([{**call, "id": "changed"}])},
        ],
    }[case]

    def respond(request):
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content="".join(f"event: {event['event_type']}\ndata: {json.dumps(event)}\n\n" for event in events))

    async def invoke(tool_call):
        calls.append(tool_call)
        return "done"

    async def emit(event):
        emitted.append(event.kind)

    provider, clients = _provider(monkeypatch, respond)
    model = provider.get_model("gemini-3.8-flash")
    try:
        with pytest.raises(EngineError) as caught:
            await create_agent_engine_for_model(model).run(
                model,
                AgentRequest(
                    name="stream", instructions="Check", input="Check", stream=True, tools=(ToolBinding(name="inspect", description="Inspect", parameters_schema={"type": "object"}, invoke=invoke),)
                ),
                on_event=emit,
            )
        assert caught.value.category == category
        assert "private-detail" not in str(caught.value)
        assert calls == []
        assert emitted == ["agent_started"]
    finally:
        await provider.aclose()
    assert all(client.is_closed for client in clients)


@pytest.mark.parametrize("observed,final,valid", [(False, 0, False), (True, 1, False), ({"nested": [False]}, {"nested": [0]}, False), (0, False, False), (1, 1.0, True), (False, False, True)])
async def test_stream_consistency_uses_json_types_before_effects(monkeypatch, observed, final, valid):
    calls, emitted, requests = [], [], []
    original = {"type": "function_call", "name": "inspect", "id": "call_json", "arguments": {"value": observed}}
    terminal = {**original, "arguments": {"value": final}}
    events = [
        {"event_type": "step.start", "index": 0, "step": original},
        {"event_type": "step.stop", "index": 0},
        {"event_type": "interaction.completed", "interaction": _interaction([terminal], status="requires_action")},
    ]

    def respond(request):
        requests.append(request)
        if len(requests) > 1:
            return _http(_interaction([_output()]), True)
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content="".join(f"event: {event['event_type']}\ndata: {json.dumps(event)}\n\n" for event in events))

    async def invoke(call):
        calls.append(call.arguments)
        return "done"

    async def emit(event):
        emitted.append(event.kind)

    provider, clients = _provider(monkeypatch, respond)
    model = provider.get_model("gemini-3.8-flash")
    request = AgentRequest(
        name="json", instructions="Check", input="Check", stream=True, tools=(ToolBinding(name="inspect", description="Inspect", parameters_schema={"type": "object"}, invoke=invoke),)
    )
    try:
        if valid:
            result = await create_agent_engine_for_model(model).run(model, request, on_event=emit)
            assert result.final_output == "done"
            assert calls == [{"value": final}]
            assert len(requests) == 2
        else:
            with pytest.raises(EngineError) as caught:
                await create_agent_engine_for_model(model).run(model, request, on_event=emit)
            assert caught.value.category == "invalid_output"
            assert calls == []
            assert emitted == ["agent_started"]
            assert len(requests) == 1
    finally:
        await provider.aclose()
    assert all(client.is_closed for client in clients)


@pytest.mark.parametrize("contract", ["tool", "output"])
async def test_canonical_array_schema_reference_reaches_native_client(monkeypatch, contract):
    schema = {"anyOf": [{"type": "string"}, {"type": "integer"}], "$ref": "#/anyOf/1"}
    payloads = []

    async def forbidden(call):
        pytest.fail("Schema check must not execute tools")

    def respond(request):
        payloads.append(json.loads(request.content))
        return _http(_interaction([_output("1")]), False)

    provider, clients = _provider(monkeypatch, respond)
    model = provider.get_model("gemini-3.8-flash")
    request = AgentRequest(
        name="reference",
        input="Check",
        instructions="Check",
        tools=(ToolBinding(name="test", description="Test", parameters_schema=schema, invoke=forbidden),) if contract == "tool" else (),
        output=OutputContract(name="test", schema=schema, parse=json.loads) if contract == "output" else None,
    )
    try:
        await create_agent_engine_for_model(model).run(model, request)
        assert len(payloads) == 1
        transmitted = payloads[0]["tools"][0]["parameters"] if contract == "tool" else payloads[0]["response_format"]["schema"]
        assert transmitted == schema
    finally:
        await provider.aclose()
    assert all(client.is_closed for client in clients)


@pytest.mark.parametrize("platform", ["android", "web", "windows", "macos"])
async def test_all_platform_tool_schemas_pass_through_engine_and_native_client(monkeypatch, platform):
    from fsq_agent._capability_bootstrap import build_capability_registry
    from fsq_agent.agent_engine._google_gemini_schema import google_schema

    definitions = build_capability_registry(platform=platform).snapshot().capabilities
    schemas = {item.name: item.params_json_schema for item in definitions}
    original = deepcopy(schemas)
    payloads = []

    async def forbidden(call):
        pytest.fail("Schema verification must not execute UI tools")

    def respond(request):
        payloads.append(json.loads(request.content))
        return _http(_interaction([_output()]), False)

    provider, clients = _provider(monkeypatch, respond)
    model = provider.get_model("gemini-3.8-flash")
    bindings = tuple(ToolBinding(name=name, description=name, parameters_schema=schema, invoke=forbidden) for name, schema in schemas.items())
    try:
        await create_agent_engine_for_model(model).run(model, AgentRequest(name="schemas", instructions="Check", input="Check", tools=bindings))
        assert {item["name"]: item["parameters"] for item in payloads[0]["tools"]} == {name: google_schema(schema) for name, schema in schemas.items()}
        assert schemas == original
    finally:
        await provider.aclose()
    assert all(client.is_closed for client in clients)


async def test_gemini_rejects_concurrent_use_and_cancels_pending_request(monkeypatch):
    started, cancelled = asyncio.Event(), asyncio.Event()

    async def respond(request):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    provider, clients = _provider(monkeypatch, respond)
    model = provider.get_model("gemini-3.8-flash")
    task = asyncio.create_task(model.complete(ModelRequest(input="wait")))
    try:
        await asyncio.wait_for(started.wait(), 3)
        with pytest.raises(EngineError, match="already in use"):
            await model.complete(ModelRequest(input="second"))
        with pytest.raises(EngineError, match="in use"):
            await provider.aclose()
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await provider.aclose()
    assert cancelled.is_set()
    assert all(client.is_closed for client in clients)


async def test_gemini_model_and_engine_are_loop_bound(monkeypatch):
    provider, clients = _provider(monkeypatch, lambda request: _http(_interaction([_output()]), False))
    model = provider.get_model("gemini-3.8-flash")
    engine = create_agent_engine_for_model(model)
    request = AgentRequest(name="loop", instructions="Check", input="Check")
    await engine.run(model, request)

    async def foreign_loop():
        with pytest.raises(EngineError, match="another event loop"):
            await engine.run(model, request)
        with pytest.raises(EngineError, match="another event loop"):
            await model.complete(ModelRequest(input="foreign"))

    try:
        await asyncio.to_thread(lambda: asyncio.run(foreign_loop()))
    finally:
        await provider.aclose()
    assert all(client.is_closed for client in clients)


async def test_gemini_failed_close_remains_retryable_without_model_recreation(monkeypatch):
    provider, clients = _provider(monkeypatch, lambda request: _http(_interaction([_output()]), False))
    model = provider.get_model("gemini-3.8-flash")
    await model.complete(ModelRequest(input="Check"))
    client = provider._client
    original_close = client.aio.aclose

    async def fail_close():
        raise RuntimeError("private-close")

    monkeypatch.setattr(client.aio, "aclose", fail_close)
    with pytest.raises(EngineError, match="cleanup"):
        await provider.aclose()
    assert provider._client is client
    with pytest.raises(EngineError, match="closed"):
        await model.complete(ModelRequest(input="again"))
    monkeypatch.setattr(client.aio, "aclose", original_close)
    await provider.aclose()
    assert provider._client is None
    assert all(client.is_closed for client in clients)


@pytest.mark.parametrize("failure", ["cancel", "event"])
async def test_gemini_run_cleanup_covers_callbacks_and_event_consumer(monkeypatch, failure):
    started, cleaned = asyncio.Event(), asyncio.Event()
    call = {"type": "function_call", "name": "inspect", "id": "call_test", "arguments": {}}
    provider, clients = _provider(monkeypatch, lambda request: _http(_interaction([call], status="requires_action"), True))

    async def invoke(tool_call):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleaned.set()

    async def emit(event):
        if failure == "event" and event.kind == "tool_called":
            raise ValueError("private-consumer")

    model = provider.get_model("gemini-3.8-flash")
    engine = create_agent_engine_for_model(model)
    request = AgentRequest(
        name="cleanup", instructions="Check", input="Check", stream=True, tools=(ToolBinding(name="inspect", description="Inspect", parameters_schema={"type": "object"}, invoke=invoke),)
    )
    task = asyncio.create_task(engine.run(model, request, on_event=emit))
    try:
        if failure == "cancel":
            await asyncio.wait_for(started.wait(), 3)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert cleaned.is_set()
        else:
            with pytest.raises(EngineError) as caught:
                await task
            assert "private" not in str(caught.value)
            assert not started.is_set()
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await provider.aclose()
    assert all(client.is_closed for client in clients)
