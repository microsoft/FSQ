# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import ast
import asyncio
import builtins
import importlib
import json
from copy import deepcopy
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING

import httpx
import pytest
from openai import AsyncOpenAI
from pydantic import BaseModel

from fsq_agent.agent_engine import (
    AgentEvent,
    AgentRequest,
    EngineError,
    ImageContent,
    Message,
    ModelRequest,
    ModelResult,
    OutputContract,
    TextContent,
    ToolBinding,
    ToolCall,
    ToolOutputEntry,
    ToolOutputTrimSettings,
    create_agent_engine,
    create_model_provider,
)

if TYPE_CHECKING:
    from collections.abc import Callable


def _response(text: str = "done", *, usage: bool = True) -> dict:
    return {
        "id": "resp_test",
        "object": "response",
        "created_at": 1,
        "model": "test-model",
        "status": "completed",
        "output": [{"id": "msg_test", "type": "message", "role": "assistant", "status": "completed", "content": [{"type": "output_text", "text": text, "annotations": []}]}],
        "usage": {"input_tokens": 3, "output_tokens": 2, "total_tokens": 5, "input_tokens_details": {"cached_tokens": 0}, "output_tokens_details": {"reasoning_tokens": 0}} if usage else None,
    }


def _provider(monkeypatch: pytest.MonkeyPatch, handler: "Callable[[httpx.Request], httpx.Response]"):
    backend = importlib.import_module("fsq_agent.agent_engine._openai_backend")
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(backend, "AsyncOpenAI", partial(AsyncOpenAI, http_client=client, max_retries=0))
    return create_model_provider(base_url="https://model.example.test/v1/", api_key="test-credential", headers={"x-test-provider": "configured"}), client


def test_public_import_does_not_load_sdk_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    module = importlib.import_module("fsq_agent.agent_engine")
    root = importlib.import_module("fsq_agent")
    original_import = builtins.__import__

    def guarded_import(name, *args, **kwargs):
        if name.split(".")[0] in {"agents", "openai"} or name.endswith("_openai_backend"):
            raise AssertionError(f"Public import loaded backend: {name}")
        if name.startswith("fsq_agent.") and not name.startswith("fsq_agent.agent_engine"):
            raise AssertionError(f"Public import loaded FSQ business module: {name}")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    importlib.reload(root)
    importlib.reload(module)


def test_engine_dependency_boundary_has_no_business_or_external_sdk_consumers() -> None:
    package = Path(__file__).parents[1] / "fsq_agent"
    violations = []
    for path in package.rglob("*.py"):
        relative = path.relative_to(package)
        in_engine = relative.parts[0] == "agent_engine"
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            names = [node.module or ""] if isinstance(node, ast.ImportFrom) else [alias.name for alias in node.names] if isinstance(node, ast.Import) else []
            for name in names:
                if in_engine and name.startswith("fsq_agent.") and not name.startswith("fsq_agent.agent_engine"):
                    violations.append(f"{relative}:{node.lineno}: business dependency {name}")
                if name.split(".")[0] == "agents" or (not in_engine and name.split(".")[0] == "openai"):
                    violations.append(f"{relative}:{node.lineno}: external SDK dependency {name}")
    assert violations == []


async def test_complete_uses_one_tool_free_sdk_request_and_closes(monkeypatch: pytest.MonkeyPatch) -> None:
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=_response())

    provider, client = _provider(monkeypatch, respond)
    model = provider.get_model("test-model")
    try:
        result = await model.complete(ModelRequest(input="check"))
        assert result.text == "done"
        assert result.finish_reason == "unknown"
        assert result.usage is not None
        assert result.usage.input_tokens == 3
        assert len(requests) == 1
        payload = json.loads(requests[0].content)
        assert payload["model"] == "test-model"
        assert not payload.get("tools")
        assert not payload.get("stream")
        assert not payload.get("reasoning")
        assert not payload.get("conversation")
        assert not payload.get("previous_response_id")
        assert requests[0].headers["x-test-provider"] == "configured"
        assert not client.is_closed
    finally:
        await provider.aclose()
    await provider.aclose()
    assert client.is_closed
    with pytest.raises(EngineError, match="closed"):
        provider.get_model("test-model")
    with pytest.raises(EngineError, match="closed"):
        await model.complete(ModelRequest(input="again"))


async def test_complete_converts_neutral_image_and_missing_usage(monkeypatch: pytest.MonkeyPatch) -> None:
    payloads: list[dict] = []

    def respond(request: httpx.Request) -> httpx.Response:
        payloads.append(json.loads(request.content))
        return httpx.Response(200, json=_response(usage=False))

    provider, _client = _provider(monkeypatch, respond)
    try:
        result = await provider.get_model("test-model").complete(ModelRequest(input=(Message(content=(TextContent("inspect"), ImageContent(data=b"image", mime_type="image/png"))),)))
        assert result.usage is None
        assert payloads[0]["input"][0]["content"] == [{"type": "input_text", "text": "inspect"}, {"type": "input_image", "image_url": "data:image/png;base64,aW1hZ2U="}]
    finally:
        await provider.aclose()


async def test_complete_normalizes_provider_error_without_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    provider, client = _provider(monkeypatch, lambda request: httpx.Response(401, json={"error": {"message": "private-prompt test-credential", "type": "authentication_error"}}))
    try:
        with pytest.raises(EngineError) as failure:
            await provider.get_model("test-model").complete(ModelRequest(input="private-prompt"))
        assert failure.value.category == "authentication"
        assert failure.value.status_code == 401
        assert "private-prompt" not in str(failure.value)
        assert "test-credential" not in str(failure.value)
    finally:
        await provider.aclose()
    assert client.is_closed


class _FinalOutput(BaseModel):
    value: str


def _http_response(payload: dict, *, stream: bool) -> httpx.Response:
    if not stream:
        return httpx.Response(200, json=payload)
    events = [
        {"type": "response.created", "sequence_number": 0, "response": {**payload, "output": [], "status": "in_progress"}},
        {"type": "response.completed", "sequence_number": 1, "response": payload},
    ]
    return _sse_response(events)


def _sse_response(events: list[dict]) -> httpx.Response:
    content = "".join(f"event: {event['type']}\ndata: {json.dumps(event)}\n\n" for event in events)
    return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=content)


@pytest.mark.parametrize("stream", [False, True])
async def test_agent_tool_loop_output_contract_events_and_context(monkeypatch: pytest.MonkeyPatch, stream: bool) -> None:
    payloads: list[dict] = []
    calls: list[ToolCall] = []
    entries: list[ToolOutputEntry] = []
    events: list[AgentEvent] = []

    def respond(request: httpx.Request) -> httpx.Response:
        payloads.append(json.loads(request.content))
        response = _response('{"value":"done"}')
        if len(payloads) == 1:
            response["output"] = [{"id": "fc_test", "type": "function_call", "name": "echo", "call_id": "call_test", "arguments": '{"text":"hello"}', "status": "completed"}]
        return _http_response(response, stream=stream)

    async def invoke(call: ToolCall) -> str:
        calls.append(call)
        return "original tool output"

    def trim(outputs: tuple[ToolOutputEntry, ...]) -> dict[int, str]:
        entries.extend(outputs)
        return {entry.entry_id: "bounded preview" for entry in outputs}

    async def on_event(event: AgentEvent) -> None:
        events.append(event)

    provider, client = _provider(monkeypatch, respond)
    tool = ToolBinding(
        name="echo",
        description="Echo text",
        parameters_schema={"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"], "additionalProperties": False},
        invoke=invoke,
    )
    request = AgentRequest(
        name="test",
        instructions="Do the task.",
        input="run",
        tools=(tool,),
        output=OutputContract(name="FinalOutput", schema=_FinalOutput.model_json_schema(), parse=_FinalOutput.model_validate_json),
        stream=stream,
        trimming=ToolOutputTrimSettings(max_output_chars=10, preview_chars=4),
        tool_output_filter=trim,
    )
    try:
        result = await create_agent_engine().run(provider.get_model("test-model"), request, on_event=on_event)
        assert isinstance(result.final_output, _FinalOutput)
        assert result.final_output.value == "done"
        assert calls == [ToolCall(name="echo", arguments={"text": "hello"}, call_id="call_test")]
        assert len(payloads) == 2
        for payload in payloads:
            assert payload["reasoning"]["effort"] == "medium"
            assert payload["text"]["verbosity"] == "medium"
            assert payload["text"]["format"]["type"] == "json_schema"
            assert payload["text"]["format"]["name"] == "final_output"
            assert payload["include"] == []
        assert entries[0].call_id == "call_test"
        assert entries[0].user_turn == 1
        assert entries[0].output == "original tool output"
        output = next(item for item in payloads[1]["input"] if item.get("type") == "function_call_output")
        assert output["call_id"] == "call_test"
        assert output["output"] == "bounded preview"
        if stream:
            tool_events = [event for event in events if event.kind in {"tool_called", "tool_output"}]
            assert [event.kind for event in tool_events] == ["tool_called", "tool_output"]
            assert all(event.call_id == "call_test" and event.tool_name == "echo" for event in tool_events)
            assert tool_events[0].arguments == {"text": "hello"}
        assert not client.is_closed
    finally:
        await provider.aclose()
    assert client.is_closed


@pytest.mark.parametrize("stream", [False, True])
async def test_parallel_tools_keep_model_order_and_private_history(monkeypatch: pytest.MonkeyPatch, stream: bool) -> None:
    payloads = []
    events = []
    completed = []
    first_started = asyncio.Event()
    second_finished = asyncio.Event()
    reasoning = {"type": "reasoning", "id": "reason_test", "summary": [{"type": "summary_text", "text": "Public summary."}], "encrypted_content": "opaque-private-state"}

    def respond(request: httpx.Request) -> httpx.Response:
        payloads.append(json.loads(request.content))
        response = _response('{"value":"done"}')
        if len(payloads) == 1:
            response["output"] = [
                reasoning,
                {"id": "fc_first", "type": "function_call", "name": "echo", "call_id": "call_first", "arguments": "{}", "status": "completed"},
                {"id": "fc_second", "type": "function_call", "name": "echo", "call_id": "call_second", "arguments": "{}", "status": "completed"},
                _response('{"value":"not final"}')["output"][0],
            ]
        return _http_response(response, stream=stream)

    async def invoke(call: ToolCall) -> str:
        if call.call_id == "call_first":
            first_started.set()
            await second_finished.wait()
        else:
            await first_started.wait()
            second_finished.set()
        completed.append(call.call_id)
        return call.call_id

    async def on_event(event: AgentEvent) -> None:
        events.append(event)

    provider, _client = _provider(monkeypatch, respond)
    request = AgentRequest(
        name="parallel",
        instructions="Execute tools.",
        input="run",
        tools=(ToolBinding(name="echo", description="Echo", parameters_schema={"type": "object", "properties": {}}, invoke=invoke),),
        output=OutputContract(name="Final", schema=_FinalOutput.model_json_schema(), parse=_FinalOutput.model_validate_json),
        stream=stream,
    )
    try:
        result = await asyncio.wait_for(create_agent_engine().run(provider.get_model("test-model"), request, on_event=on_event), timeout=5)
        assert result.final_output.value == "done"
        assert completed == ["call_second", "call_first"]
        assert len(payloads) == 2
        outputs = [item for item in payloads[1]["input"] if item.get("type") == "function_call_output"]
        assert [(item["call_id"], item["output"]) for item in outputs] == [("call_first", "call_first"), ("call_second", "call_second")]
        retained = next(item for item in payloads[1]["input"] if item.get("type") == "reasoning")
        assert retained["encrypted_content"] == "opaque-private-state"
        assert result.usage is not None
        assert result.usage.requests == 2
        assert result.usage.total_tokens == 10
        if stream:
            assert [(event.kind, event.call_id) for event in events if event.kind in {"tool_called", "tool_output"}] == [
                ("tool_called", "call_first"),
                ("tool_called", "call_second"),
                ("tool_output", "call_first"),
                ("tool_output", "call_second"),
            ]
            assert [event.text for event in events if event.kind == "reasoning_summary"] == ["Public summary."]
        else:
            assert events == []
    finally:
        await provider.aclose()


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("tool_only", [False, True])
async def test_turn_limit_counts_model_turns(monkeypatch: pytest.MonkeyPatch, stream: bool, tool_only: bool) -> None:
    payloads = []
    calls = []

    def respond(request: httpx.Request) -> httpx.Response:
        payloads.append(json.loads(request.content))
        response = _response("")
        if tool_only:
            response["output"] = [{"id": f"fc_{len(payloads)}", "type": "function_call", "name": "echo", "call_id": f"call_{len(payloads)}", "arguments": "{}", "status": "completed"}]
        return _http_response(response, stream=stream)

    async def invoke(call: ToolCall) -> str:
        calls.append(call.call_id)
        return "continue"

    provider, _client = _provider(monkeypatch, respond)
    request = AgentRequest(
        name="limited",
        instructions="Return structured output.",
        input="run",
        tools=(ToolBinding(name="echo", description="Echo", parameters_schema={"type": "object", "properties": {}}, invoke=invoke),),
        output=OutputContract(name="Final", schema=_FinalOutput.model_json_schema(), parse=_FinalOutput.model_validate_json),
        max_turns=2,
        stream=stream,
    )
    try:
        with pytest.raises(EngineError) as failure:
            await create_agent_engine().run(provider.get_model("test-model"), request)
        assert failure.value.category == "max_turns"
        assert len(payloads) == 2
        assert calls == (["call_1", "call_2"] if tool_only else [])
    finally:
        await provider.aclose()


@pytest.mark.parametrize("terminal", ["response.failed", "response.incomplete", "error", "missing"])
async def test_stream_terminal_failures_do_not_complete(monkeypatch: pytest.MonkeyPatch, terminal: str) -> None:
    response = _response("private terminal output")
    response["status"] = "in_progress"
    events = [{"type": "response.created", "sequence_number": 0, "response": response}]
    if terminal == "error":
        events.append({"type": "error", "sequence_number": 1, "code": "server_error", "message": "private terminal detail", "param": None})
    elif terminal != "missing":
        response = {**response, "status": terminal.removeprefix("response.")}
        if terminal == "response.incomplete":
            response["incomplete_details"] = {"reason": "content_filter"}
        else:
            response["error"] = {"code": "server_error", "message": "private terminal detail"}
        events.append({"type": terminal, "sequence_number": 1, "response": response})
    requests = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return _sse_response(events)

    provider, client = _provider(monkeypatch, respond)
    try:
        with pytest.raises(EngineError) as failure:
            await create_agent_engine().run(provider.get_model("test-model"), AgentRequest(name="stream", instructions="test", input="test", stream=True))
        assert failure.value.category == ("incomplete" if terminal == "response.incomplete" else "invalid_output")
        if terminal == "response.incomplete":
            assert failure.value.reason == "content_filter"
        assert "private terminal" not in str(failure.value)
        assert len(requests) == 1
    finally:
        await provider.aclose()
    assert client.is_closed


async def test_stream_item_events_do_not_duplicate_tool_execution(monkeypatch: pytest.MonkeyPatch) -> None:
    requests = []
    calls = []
    events = []
    tool_item = {"id": "fc_incremental", "type": "function_call", "name": "echo", "call_id": "call_incremental", "arguments": "{}", "status": "completed"}

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        response = _response()
        if len(requests) != 1:
            return _http_response(response, stream=True)
        response["output"] = [tool_item]
        return _sse_response(
            [
                {"type": "response.created", "sequence_number": 0, "response": {**response, "output": [], "status": "in_progress"}},
                {"type": "response.output_item.added", "sequence_number": 1, "output_index": 0, "item": {**tool_item, "arguments": "", "status": "in_progress"}},
                {"type": "response.function_call_arguments.delta", "sequence_number": 2, "output_index": 0, "item_id": "fc_incremental", "delta": "{"},
                {"type": "response.function_call_arguments.done", "sequence_number": 3, "output_index": 0, "item_id": "fc_incremental", "arguments": "{}"},
                {"type": "response.output_item.done", "sequence_number": 4, "output_index": 0, "item": tool_item},
                {"type": "response.completed", "sequence_number": 5, "response": response},
            ]
        )

    async def invoke(call: ToolCall) -> str:
        calls.append(call)
        return "done"

    async def on_event(event: AgentEvent) -> None:
        events.append(event)

    provider, _client = _provider(monkeypatch, respond)
    request = AgentRequest(name="stream", instructions="test", input="test", tools=(ToolBinding(name="echo", description="Echo", parameters_schema={}, invoke=invoke),), stream=True)
    try:
        result = await create_agent_engine().run(provider.get_model("test-model"), request, on_event=on_event)
        assert result.final_output == "done"
        assert calls == [ToolCall(name="echo", call_id="call_incremental", arguments={})]
        assert [event.kind for event in events if event.kind.startswith("tool_")] == ["tool_called", "tool_output"]
        assert len(requests) == 2
    finally:
        await provider.aclose()


async def test_agent_invalid_output_is_neutral_error(monkeypatch: pytest.MonkeyPatch) -> None:
    provider, _client = _provider(monkeypatch, lambda request: httpx.Response(200, json=_response('{"value": []}')))
    request = AgentRequest(name="test", instructions="Return JSON.", input="run", output=OutputContract(name="Final", schema=_FinalOutput.model_json_schema(), parse=_FinalOutput.model_validate_json))
    try:
        with pytest.raises(EngineError) as failure:
            await create_agent_engine().run(provider.get_model("test-model"), request)
        assert failure.value.category == "invalid_output"
    finally:
        await provider.aclose()


async def test_agent_rejects_incompatible_model_before_invoking_it() -> None:
    class OtherModel:
        async def complete(self, request: ModelRequest) -> ModelResult:
            raise AssertionError("Incompatible model must not be invoked")

    with pytest.raises(EngineError) as failure:
        await create_agent_engine().run(OtherModel(), AgentRequest(name="test", instructions="test", input="test"))
    assert failure.value.category == "incompatible_model"


async def test_cancelled_single_request_releases_provider_for_close(monkeypatch: pytest.MonkeyPatch) -> None:
    entered = asyncio.Event()
    release = asyncio.Event()

    async def respond(request: httpx.Request) -> httpx.Response:
        entered.set()
        await release.wait()
        return httpx.Response(200, json=_response())

    provider, client = _provider(monkeypatch, respond)
    task = asyncio.create_task(provider.get_model("test-model").complete(ModelRequest(input="wait")))
    await asyncio.wait_for(entered.wait(), timeout=5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await provider.aclose()
    assert client.is_closed


async def test_cancelled_streamed_agent_closes_model_work_before_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    entered = asyncio.Event()
    release = asyncio.Event()
    cancelled = asyncio.Event()

    async def respond(request: httpx.Request) -> httpx.Response:
        entered.set()
        try:
            await release.wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise
        return _http_response(_response(), stream=True)

    provider, client = _provider(monkeypatch, respond)
    task = asyncio.create_task(create_agent_engine().run(provider.get_model("test-model"), AgentRequest(name="test", instructions="wait", input="wait", stream=True)))
    await asyncio.wait_for(entered.wait(), timeout=5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=5)
    assert cancelled.is_set()
    await provider.aclose()
    assert client.is_closed


async def test_stream_consumer_failure_cleans_up_without_restarting(monkeypatch: pytest.MonkeyPatch) -> None:
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return _http_response(_response(), stream=True)

    async def on_event(event: AgentEvent) -> None:
        if event.kind == "message":
            raise ValueError("private consumer detail")

    provider, client = _provider(monkeypatch, respond)
    try:
        with pytest.raises(EngineError) as failure:
            await create_agent_engine().run(provider.get_model("test-model"), AgentRequest(name="test", instructions="test", input="test", stream=True), on_event=on_event)
        assert "private consumer detail" not in str(failure.value)
        assert len(requests) == 1
    finally:
        await provider.aclose()
    assert client.is_closed


async def test_provider_rejects_foreign_event_loop_and_concurrent_use(monkeypatch: pytest.MonkeyPatch) -> None:
    entered = asyncio.Event()
    release = asyncio.Event()

    async def respond(request: httpx.Request) -> httpx.Response:
        entered.set()
        await release.wait()
        return httpx.Response(200, json=_response())

    provider, client = _provider(monkeypatch, respond)
    model = provider.get_model("test-model")
    task = asyncio.create_task(model.complete(ModelRequest(input="first")))
    await asyncio.wait_for(entered.wait(), timeout=5)
    try:
        with pytest.raises(EngineError, match="already in use"):
            await model.complete(ModelRequest(input="concurrent"))

        def foreign_call() -> None:
            with pytest.raises(EngineError, match="another event loop"):
                asyncio.run(model.complete(ModelRequest(input="foreign")))

        await asyncio.to_thread(foreign_call)
    finally:
        release.set()
        await task
        await provider.aclose()
    assert client.is_closed


def test_context_bridge_preserves_stage_order_ids_and_private_items() -> None:
    from fsq_agent.agent_engine._context import ModelInputData, ModelInputFilter

    items = [
        {"role": "user", "content": "first"},
        {"type": "function_call", "name": "observe", "call_id": "old-call", "arguments": "{}"},
        {"type": "function_call_output", "call_id": "old-call", "output": "old output " * 100},
        {"role": "user", "content": "second"},
        {"type": "reasoning", "id": "private-reasoning", "encrypted_content": "opaque-state", "summary": []},
        {"type": "function_call", "name": "observe", "call_id": "new-call", "arguments": "{}"},
        {"type": "function_call_output", "call_id": "new-call", "output": "new output " * 100},
    ]
    original = deepcopy(items)
    observed = []

    def filter_outputs(entries: tuple[ToolOutputEntry, ...]) -> dict[int, str]:
        observed.extend(entries)
        return {entries[-1].entry_id: "new bounded output"}

    bridge = ModelInputFilter(ToolOutputTrimSettings(recent_turns=1, max_output_chars=100, preview_chars=10), filter_outputs)
    result = bridge(ModelInputData(input=items, instructions="instructions"))

    assert observed[0].output.startswith("[Trimmed:")
    assert observed[1].output == original[6]["output"]
    assert [entry.user_turn for entry in observed] == [1, 2]
    assert [entry.call_id for entry in observed] == ["old-call", "new-call"]
    assert result.input[4] == original[4]
    assert result.input[6] == {**original[6], "output": "new bounded output"}
    assert result.instructions == "instructions"
    assert len(result.input) == len(original)
    assert items == original


def test_context_bridge_rejects_replacing_non_tool_input() -> None:
    from fsq_agent.agent_engine._context import ModelInputData, ModelInputFilter

    bridge = ModelInputFilter(None, lambda entries: {0: "replaced user content"})
    with pytest.raises(EngineError, match="known entries"):
        bridge(ModelInputData(input=[{"role": "user", "content": "test"}], instructions=None))


async def test_backend_accepts_real_macos_tool_schema_without_mutation(monkeypatch: pytest.MonkeyPatch) -> None:
    from fsq_agent.models import MacOSLaunchAppParams

    captured: list[dict] = []
    schema = MacOSLaunchAppParams.model_json_schema()
    original = deepcopy(schema)

    async def forbidden_tool(call: ToolCall) -> str:
        raise AssertionError("Schema-only test must not execute a tool")

    def respond(request: httpx.Request) -> httpx.Response:
        captured.append(json.loads(request.content))
        return httpx.Response(200, json=_response())

    provider, _client = _provider(monkeypatch, respond)
    try:
        await create_agent_engine().run(
            provider.get_model("test-model"),
            AgentRequest(name="test", instructions="test", input="test", tools=(ToolBinding(name="launch_app", description="Launch app", parameters_schema=schema, invoke=forbidden_tool),)),
        )
        assert captured[0]["tools"][0]["strict"] is True
        assert "new_session" in captured[0]["tools"][0]["parameters"]["properties"]
        assert schema == original
    finally:
        await provider.aclose()


def _capture_tracing(monkeypatch: pytest.MonkeyPatch) -> tuple[list[dict], list[dict]]:
    from fsq_agent.agent_engine import _tracing

    traces = []
    spans = []

    async def capture(items):
        traces.extend(item for item in items if item["object"] == "trace")
        spans.extend(item for item in items if item["object"] == "trace.span")

    monkeypatch.setattr(_tracing, "_export", capture)
    return traces, spans


@pytest.mark.parametrize("stream", [False, True])
async def test_tracing_excludes_model_and_tool_payloads(monkeypatch: pytest.MonkeyPatch, stream: bool) -> None:
    traces, spans = _capture_tracing(monkeypatch)
    requests = []
    private_values = ["private instructions", "private model input", "private arguments", "private tool output", "private model output"]

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        response = _response(private_values[-1])
        if len(requests) == 1:
            response["output"] = [{"id": "fc_trace", "type": "function_call", "name": "echo", "call_id": "call_trace", "arguments": json.dumps({"text": private_values[2]}), "status": "completed"}]
        return _http_response(response, stream=stream)

    async def invoke(call: ToolCall) -> str:
        return private_values[3]

    tool = ToolBinding(
        name="echo", description="Echo", parameters_schema={"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"], "additionalProperties": False}, invoke=invoke
    )
    provider, _client = _provider(monkeypatch, respond)
    try:
        result = await create_agent_engine().run(
            provider.get_model("test-model"),
            AgentRequest(name="traced", instructions=private_values[0], input=private_values[1], tools=(tool,), tracing_enabled=True, stream=stream),
        )
        assert result.final_output == private_values[-1]
        assert traces
        assert spans
        assert len([span for span in spans if span["span_data"]["type"] == "response"]) == 2
        assert any(span["span_data"]["type"] == "function" for span in spans)
        serialized = json.dumps({"traces": traces, "spans": spans})
        assert all(value not in serialized for value in private_values)
    finally:
        await provider.aclose()


async def test_independent_engines_preserve_per_run_tracing_choice(monkeypatch: pytest.MonkeyPatch) -> None:
    traces, spans = _capture_tracing(monkeypatch)
    entered = asyncio.Event()
    release = asyncio.Event()
    calls = []

    async def traced_response(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        response = _response()
        if len(calls) == 1:
            entered.set()
            await release.wait()
            response["output"] = [{"id": "fc_interleaved", "type": "function_call", "name": "echo", "call_id": "call_interleaved", "arguments": "{}", "status": "completed"}]
        return httpx.Response(200, json=response)

    async def invoke(call: ToolCall) -> str:
        return "done"

    traced_provider, _traced_client = _provider(monkeypatch, traced_response)
    tool = ToolBinding(name="echo", description="Echo", parameters_schema={"type": "object", "properties": {}, "additionalProperties": False}, invoke=invoke)
    traced_task = asyncio.create_task(
        create_agent_engine().run(traced_provider.get_model("test-model"), AgentRequest(name="traced", instructions="test", input="test", tools=(tool,), tracing_enabled=True))
    )
    await asyncio.wait_for(entered.wait(), timeout=5)
    silent_provider, _silent_client = _provider(monkeypatch, lambda request: httpx.Response(200, json=_response()))
    try:
        await create_agent_engine().run(silent_provider.get_model("test-model"), AgentRequest(name="silent", instructions="test", input="test", tracing_enabled=False))
        release.set()
        await asyncio.wait_for(traced_task, timeout=5)
        assert len(traces) == 1
        assert len([span for span in spans if span["span_data"]["type"] == "response"]) == 2
        assert len([span for span in spans if span["span_data"]["type"] == "function"]) == 1
    finally:
        release.set()
        await asyncio.gather(traced_task, return_exceptions=True)
        await traced_provider.aclose()
        await silent_provider.aclose()


@pytest.mark.parametrize("entry", ["complete", "agent", "streamed_agent"])
@pytest.mark.parametrize("failure_kind", ["incomplete", "incomplete_empty", "incomplete_item", "failed", "error", "refusal", "refusal_empty"])
async def test_shared_response_failures_stop_before_output_or_tools(monkeypatch: pytest.MonkeyPatch, entry: str, failure_kind: str) -> None:
    response = _response('{"value":"private partial output"}')
    requests = []
    events = []
    if failure_kind.startswith("incomplete"):
        response["incomplete_details"] = {"reason": "content_filter"}
        if failure_kind == "incomplete_item":
            response["output"][0]["status"] = "incomplete"
        else:
            response["status"] = "incomplete"
        if failure_kind == "incomplete_empty":
            response["output"] = []
        expected_category = "incomplete"
    elif failure_kind in {"failed", "error"}:
        if failure_kind == "failed":
            response["status"] = "failed"
        response["error"] = {"code": "server_error", "message": "private provider error"}
        expected_category = "invalid_output"
    else:
        response["output"][0]["content"].append({"type": "refusal", "refusal": "" if failure_kind == "refusal_empty" else "private refusal"})
        expected_category = "refusal"
    if entry != "complete":
        response["output"].append({"id": "fc_forbidden", "type": "function_call", "name": "forbidden", "call_id": "call_forbidden", "arguments": "{}", "status": "completed"})

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return _http_response(response, stream=entry == "streamed_agent")

    async def forbidden(call: ToolCall) -> str:
        raise AssertionError("A failed model response must not execute tools")

    async def on_event(event: AgentEvent) -> None:
        events.append(event)

    provider, client = _provider(monkeypatch, respond)
    try:
        model = provider.get_model("test-model")
        if entry == "complete":
            operation = model.complete(ModelRequest(input="test"))
        else:
            request = AgentRequest(
                name="test",
                instructions="test",
                input="test",
                stream=entry == "streamed_agent",
                tools=(ToolBinding(name="forbidden", description="Forbidden", parameters_schema={}, invoke=forbidden),),
            )
            operation = create_agent_engine().run(model, request, on_event=on_event)
        with pytest.raises(EngineError) as failure:
            await operation
        assert failure.value.category == expected_category
        if expected_category == "incomplete":
            assert failure.value.reason == "content_filter"
        assert "private" not in str(failure.value)
        assert len(requests) == 1
        assert all(event.kind == "agent_started" for event in events)
    finally:
        await provider.aclose()
    assert client.is_closed


@pytest.mark.parametrize("configuration", ["tool_ref", "output_node", "recent_turns", "max_output_chars", "preview_chars"])
async def test_invalid_preflight_is_configuration_without_model_or_tool_effects(monkeypatch: pytest.MonkeyPatch, configuration: str) -> None:
    requests = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=_response())

    async def forbidden(call: ToolCall) -> str:
        raise AssertionError("Invalid configuration must not invoke a tool")

    schema = {"type": "object", "properties": {"field": {"$ref": "#/private_missing", "description": "private schema"}}} if configuration == "tool_ref" else {}
    output = OutputContract(name="Invalid", schema={"type": "object", "properties": {"private_field": False}}, parse=json.loads) if configuration == "output_node" else None
    trimming = ToolOutputTrimSettings(**{configuration: -1}) if configuration in {"recent_turns", "max_output_chars", "preview_chars"} else None
    request = AgentRequest(
        name="test", instructions="test", input="test", tools=(ToolBinding(name="unused", description="Unused", parameters_schema=schema, invoke=forbidden),), output=output, trimming=trimming
    )
    provider, client = _provider(monkeypatch, respond)
    try:
        with pytest.raises(EngineError) as failure:
            await create_agent_engine().run(provider.get_model("test-model"), request)
        assert failure.value.category == "configuration"
        assert "private" not in str(failure.value)
        assert requests == []
    finally:
        await provider.aclose()
    assert client.is_closed


@pytest.mark.parametrize("source", ["provider", "tool", "filter", "unknown_tool"])
@pytest.mark.parametrize("stream", [False, True])
async def test_trace_errors_never_export_private_exception_details(monkeypatch: pytest.MonkeyPatch, source: str, stream: bool) -> None:
    traces, spans = _capture_tracing(monkeypatch)
    private_detail = "private-error-detail-92837"
    requests = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if source == "provider":
            return httpx.Response(400, json={"error": {"message": private_detail, "type": "invalid_request_error"}})
        response = _response()
        response["output"] = [
            {"id": "fc_failure", "type": "function_call", "name": private_detail if source == "unknown_tool" else "echo", "call_id": "call_failure", "arguments": "{}", "status": "completed"}
        ]
        return _http_response(response, stream=stream)

    async def invoke(call: ToolCall) -> str:
        if source == "tool":
            raise RuntimeError(private_detail)
        return "tool result"

    def filter_output(entries):
        if source == "filter" and entries:
            raise RuntimeError(private_detail)
        return {}

    provider, _client = _provider(monkeypatch, respond)
    request = AgentRequest(
        name="error-test",
        instructions="test",
        input="test",
        tools=(ToolBinding(name="echo", description="Echo", parameters_schema={"type": "object", "properties": {}, "additionalProperties": False}, invoke=invoke),),
        tool_output_filter=filter_output,
        tracing_enabled=True,
        stream=stream,
    )
    try:
        with pytest.raises(EngineError):
            await create_agent_engine().run(provider.get_model("test-model"), request)
        assert any(span["error"] for span in spans)
        assert private_detail not in json.dumps({"traces": traces, "spans": spans})
    finally:
        await provider.aclose()


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("arguments", ["", " ", "{", "[]", "null", '{"value":NaN}', '{"value":[Infinity]}', '{"value":{"nested":-Infinity}}'])
async def test_invalid_tool_input_returns_formatted_failure_without_execution(monkeypatch: pytest.MonkeyPatch, arguments: str, stream: bool) -> None:
    payloads = []
    failures = []
    events = []

    def respond(request: httpx.Request) -> httpx.Response:
        payloads.append(json.loads(request.content))
        response = _response("Handled invalid input.")
        if len(payloads) == 1:
            response["output"] = [{"id": "fc_invalid", "type": "function_call", "name": "echo", "call_id": "call_invalid", "arguments": arguments, "status": "completed"}]
        return _http_response(response, stream=stream)

    async def forbidden_invocation(call: ToolCall) -> str:
        raise AssertionError("Invalid input must not execute tools")

    async def on_invalid_input(failure) -> str:
        failures.append(failure)
        return json.dumps({"status": "failed", "tool_name": failure.name, "error": failure.message})

    async def on_event(event: AgentEvent) -> None:
        events.append(event)

    provider, _client = _provider(monkeypatch, respond)
    try:
        tool = ToolBinding(name="echo", description="Echo", parameters_schema={"type": "object", "properties": {}}, invoke=forbidden_invocation, on_invalid_input=on_invalid_input)
        result = await create_agent_engine().run(provider.get_model("test-model"), AgentRequest(name="test", instructions="test", input="test", tools=(tool,), stream=stream), on_event=on_event)
        assert result.final_output == "Handled invalid input."
        assert len(payloads) == 2
        assert len(failures) == 1
        assert failures[0].name == "echo"
        assert failures[0].call_id == "call_invalid"
        output = next(item for item in payloads[1]["input"] if item.get("type") == "function_call_output")
        assert output["call_id"] == "call_invalid"
        assert json.loads(output["output"])["status"] == "failed"
        if stream:
            assert next(event.arguments for event in events if event.kind == "tool_called") == arguments
    finally:
        await provider.aclose()


@pytest.mark.parametrize("interrupt_type", [KeyboardInterrupt, SystemExit])
@pytest.mark.parametrize("stream", [False, True])
def test_process_interruption_waits_for_sibling_cleanup(monkeypatch: pytest.MonkeyPatch, interrupt_type: type[BaseException], stream: bool) -> None:
    loop = asyncio.new_event_loop()
    started = asyncio.Event()
    blocked = asyncio.Event()
    cleanup_release = asyncio.Event()
    cleaned = asyncio.Event()
    interruption = interrupt_type("synthetic interrupt")
    response = _response()
    response["output"] = [{"id": f"fc_{name}", "type": "function_call", "name": name, "call_id": f"call_{name}", "arguments": "{}", "status": "completed"} for name in ("wait", "interrupt")]

    async def wait(call: ToolCall) -> str:
        started.set()
        try:
            await blocked.wait()
        finally:
            loop.call_soon(cleanup_release.set)
            await cleanup_release.wait()
            cleaned.set()
        return "unused"

    async def interrupt(call: ToolCall) -> str:
        await started.wait()
        raise interruption

    provider, client = _provider(monkeypatch, lambda request: _http_response(response, stream=stream))
    request = AgentRequest(
        name="interrupt",
        instructions="test",
        input="test",
        stream=stream,
        tools=tuple(ToolBinding(name=name, description=name, parameters_schema={}, invoke=invoke) for name, invoke in (("wait", wait), ("interrupt", interrupt))),
    )

    async def observe():
        try:
            await create_agent_engine().run(provider.get_model("test-model"), request)
        except (KeyboardInterrupt, SystemExit, asyncio.CancelledError) as error:
            return error
        return None

    execution = loop.create_task(observe())
    try:
        try:
            outcome = loop.run_until_complete(execution)
        except (KeyboardInterrupt, SystemExit):
            outcome = None
        assert outcome is interruption
        assert execution.done()
        assert cleaned.is_set()
        assert not asyncio.all_tasks(loop)
    finally:
        pending = asyncio.all_tasks(loop)
        for task in pending:
            task.cancel()
        if pending:
            loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
        loop.run_until_complete(provider.aclose())
        loop.close()
    assert client.is_closed


@pytest.mark.parametrize("interrupt_type", [KeyboardInterrupt, SystemExit])
@pytest.mark.parametrize("has_primary", [False, True])
def test_stream_close_interruption_is_delivered_to_owner(interrupt_type: type[BaseException], has_primary: bool) -> None:
    from fsq_agent.agent_engine._runner import _close_stream

    loop = asyncio.new_event_loop()
    interruption = interrupt_type("synthetic close interrupt")
    primary = RuntimeError("primary error") if has_primary else None

    class InterruptedStream:
        async def close(self) -> None:
            raise interruption

    async def observe():
        try:
            await _close_stream(InterruptedStream(), primary)
        except (KeyboardInterrupt, SystemExit) as error:
            return error
        return None

    execution = loop.create_task(observe())
    try:
        escaped = False
        try:
            outcome = loop.run_until_complete(execution)
        except (KeyboardInterrupt, SystemExit):
            escaped = True
            outcome = None
        assert not escaped
        assert outcome is (None if has_primary else interruption)
        assert execution.done()
        assert not asyncio.all_tasks(loop)
    finally:
        pending = asyncio.all_tasks(loop)
        for task in pending:
            task.cancel()
        if pending:
            loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
        loop.close()


async def test_client_cleanup_failure_retains_resources_for_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    provider, client = _provider(monkeypatch, lambda request: httpx.Response(200, json=_response()))
    await provider.get_model("test-model").complete(ModelRequest(input="test"))
    close = client.aclose
    attempts = []

    async def close_once_rejected() -> None:
        attempts.append(True)
        if len(attempts) == 1:
            raise RuntimeError("Client cleanup rejected")
        await close()

    monkeypatch.setattr(client, "aclose", close_once_rejected)
    try:
        with pytest.raises(EngineError, match="cleanup"):
            await provider.aclose()
        with pytest.raises(EngineError, match="closed"):
            provider.get_model("test-model")
        await provider.aclose()
        assert client.is_closed
        assert len(attempts) == 2
    finally:
        await close()


async def test_trace_error_safety_leaves_unrelated_spans_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    from fsq_agent.agent_engine._tracing import TraceSession, span

    _traces, spans = _capture_tracing(monkeypatch)
    provider, _client = _provider(monkeypatch, lambda request: httpx.Response(200, json=_response()))
    try:
        async with TraceSession("unrelated", enabled=True):
            with span("custom", name="unrelated") as unrelated:
                unrelated["error"] = {"message": "caller-owned error", "data": {"category": "caller"}}
                await create_agent_engine().run(provider.get_model("test-model"), AgentRequest(name="test", instructions="test", input="test", tracing_enabled=True))
        assert spans[-1]["error"] == {"message": "caller-owned error", "data": {"category": "caller"}}
    finally:
        await provider.aclose()


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("cancel_source", ["tool", "filter"])
async def test_internal_cancellation_propagates_without_another_model_request(monkeypatch: pytest.MonkeyPatch, stream: bool, cancel_source: str) -> None:
    requests = []
    cancellations = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        response = _response('{"value":"unexpected completion"}')
        if len(requests) == 1:
            response["output"] = [{"id": "fc_cancel", "type": "function_call", "name": "cancel_tool", "call_id": "call_cancel", "arguments": "{}", "status": "completed"}]
        return _http_response(response, stream=stream)

    async def invoke(call: ToolCall) -> str:
        if cancel_source == "tool":
            cancellations.append("tool")
            raise asyncio.CancelledError("tool cancelled")
        return "tool result"

    def filter_outputs(entries):
        if entries and cancel_source == "filter":
            cancellations.append("filter")
            raise asyncio.CancelledError("filter cancelled")
        return {}

    provider, client = _provider(monkeypatch, respond)
    tool = ToolBinding(name="cancel_tool", description="A cancellable tool", parameters_schema={"type": "object", "properties": {}, "additionalProperties": False}, invoke=invoke)
    request = AgentRequest(
        name="cancellation",
        instructions="test",
        input="test",
        tools=(tool,),
        tool_output_filter=filter_outputs,
        output=OutputContract(name="Final", schema=_FinalOutput.model_json_schema(), parse=_FinalOutput.model_validate_json),
        stream=stream,
    )
    try:
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(create_agent_engine().run(provider.get_model("test-model"), request), timeout=5)
        assert cancellations == [cancel_source]
        assert len(requests) == 1
    finally:
        await provider.aclose()
    assert client.is_closed


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("cleanup_raises", [False, True])
async def test_engine_cancellation_waits_for_run_owned_tool_cleanup(monkeypatch: pytest.MonkeyPatch, stream: bool, cleanup_raises: bool) -> None:
    entered = asyncio.Event()
    cleanup_entered = asyncio.Event()
    release_cleanup = asyncio.Event()
    cleaned = asyncio.Event()
    blocked = asyncio.Event()
    tool_tasks = []
    response = _response()
    response["output"] = [{"id": "fc_cleanup", "type": "function_call", "name": "resource_tool", "call_id": "call_cleanup", "arguments": "{}", "status": "completed"}]

    async def invoke(call: ToolCall) -> str:
        tool_tasks.append(asyncio.current_task())
        entered.set()
        try:
            await blocked.wait()
            return "unused"
        finally:
            cleanup_entered.set()
            await release_cleanup.wait()
            cleaned.set()
            if cleanup_raises:
                raise OSError("secondary tool cleanup failure")

    provider, client = _provider(monkeypatch, lambda request: _http_response(response, stream=stream))
    tool = ToolBinding(name="resource_tool", description="Own an async resource", parameters_schema={"type": "object", "properties": {}, "additionalProperties": False}, invoke=invoke)
    execution = asyncio.create_task(create_agent_engine().run(provider.get_model("test-model"), AgentRequest(name="test", instructions="test", input="test", tools=(tool,), stream=stream)))
    try:
        await asyncio.wait_for(entered.wait(), timeout=5)
        execution.cancel("primary cancellation")
        await asyncio.wait_for(cleanup_entered.wait(), timeout=5)
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(asyncio.shield(execution), timeout=0.05)
        assert not cleaned.is_set()
        assert not client.is_closed
        with pytest.raises(EngineError, match="in use"):
            await provider.aclose()
        release_cleanup.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(execution, timeout=5)
        assert cleaned.is_set()
        assert all(task.done() for task in tool_tasks)
    finally:
        release_cleanup.set()
        if not execution.done():
            execution.cancel()
        await asyncio.gather(execution, *tool_tasks, return_exceptions=True)
        await provider.aclose()
    assert client.is_closed
