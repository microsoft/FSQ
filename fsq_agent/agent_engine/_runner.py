# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from __future__ import annotations

import asyncio
import json
import sys
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, TypeVar

from openai.types.responses import ResponseStreamEvent

from ._context import ModelInputData, ModelInputFilter
from ._contracts import AgentEvent, AgentResult, EngineError, ToolCall, ToolInputFailure
from ._conversion import _item_text, add_usage, check_response, decode_tool_arguments, model_input, response_items, response_parameters, semantic_event, token_usage
from ._tracing import TraceSession, span

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable

    from openai import AsyncOpenAI, AsyncStream
    from openai.types.responses import Response

    from ._contracts import AgentEventSink, AgentRequest, ToolBinding


_ResultT = TypeVar("_ResultT")


@dataclass(frozen=True)
class _TaskInterruption:
    error: BaseException = field(repr=False)


async def _capture_interruption(operation: Awaitable[_ResultT]) -> _ResultT | _TaskInterruption:
    try:
        return await operation
    except (KeyboardInterrupt, SystemExit) as error:
        return _TaskInterruption(error)


@asynccontextmanager
async def _tool_invocations() -> AsyncIterator[set[asyncio.Task]]:
    tasks: set[asyncio.Task] = set()
    try:
        yield tasks
    finally:
        primary = sys.exception()
        pending = tuple(task for task in tasks if not task.done())
        if primary is not None:
            for task in pending:
                if not task.cancelling():
                    task.cancel()
        if pending:
            joined = asyncio.gather(*pending, return_exceptions=True)
            interruption: asyncio.CancelledError | None = None
            while not joined.done():
                try:
                    await asyncio.shield(joined)
                except asyncio.CancelledError as error:
                    interruption = error
                    for task in pending:
                        if not task.done() and not task.cancelling():
                            task.cancel()
            outcomes = joined.result()
            if primary is None:
                if interruption is not None:
                    raise interruption
                for outcome in outcomes:
                    if isinstance(outcome, _TaskInterruption):
                        raise outcome.error
                    if isinstance(outcome, BaseException):
                        raise outcome


async def _complete_cleanup(operation: Awaitable[None], primary: BaseException | None) -> None:
    joined = asyncio.gather(_capture_interruption(operation), return_exceptions=True)
    interruption = None
    while not joined.done():
        try:
            await asyncio.shield(joined)
        except asyncio.CancelledError as error:
            interruption = error
    outcome = joined.result()[0]
    if primary is None:
        if interruption is not None:
            raise interruption
        if isinstance(outcome, _TaskInterruption):
            raise outcome.error
        if isinstance(outcome, Exception):
            raise EngineError("cleanup", "Agent stream cleanup failed.") from None
        if isinstance(outcome, BaseException):
            raise outcome


async def _close_stream(stream: AsyncStream[ResponseStreamEvent], primary: BaseException | None) -> None:
    await _complete_cleanup(stream.close(), primary)


@asynccontextmanager
async def _response_stream(client: AsyncOpenAI, parameters: dict) -> AsyncIterator[AsyncStream[ResponseStreamEvent]]:
    response_stream = await client.responses.create(**parameters, stream=True)
    try:
        yield response_stream
    finally:
        await _close_stream(response_stream, sys.exception())


async def _read_response(client: AsyncOpenAI, parameters: dict, *, stream: bool) -> Response:
    if not stream:
        response = await client.responses.create(**parameters)
        check_response(response)
        return response
    async with _response_stream(client, parameters) as response_stream:
        content = await response_stream.response.aread()
        completed_response = None
        failed_response = None
        stream_error = False
        for sse in response_stream._decoder.iter_bytes(iter((content,))):
            if sse.data.startswith("[DONE]"):
                break
            data = sse.json()
            if isinstance(data, dict) and data.get("error"):
                stream_error = True
                continue
            event = client._process_response_data(data=data, cast_to=ResponseStreamEvent, response=response_stream.response)
            if event.type == "response.completed":
                completed_response = event.response
            elif event.type in {"response.failed", "response.incomplete"}:
                failed_response = event.response
            elif event.type in {"error", "response.error"}:
                stream_error = True
        if failed_response is not None:
            check_response(failed_response)
            raise EngineError("invalid_output", "Model provider returned an invalid response or structured output.")
        if stream_error or completed_response is None:
            raise EngineError("invalid_output", "Model stream ended without a completed response.")
        check_response(completed_response)
        return completed_response


async def _invoke_tool(binding: ToolBinding, item: dict) -> str:
    with span("function", name=binding.name, input=None, output=None, mcp_data=None):
        arguments = decode_tool_arguments(item.get("arguments"))
        if arguments is None:
            failure = ToolInputFailure(name=binding.name, call_id=item["call_id"], message="Tool arguments must be a JSON object.")
            output = await binding.on_invalid_input(failure) if binding.on_invalid_input is not None else json.dumps({"error": failure.message})
        else:
            output = await binding.invoke(ToolCall(name=binding.name, arguments=arguments, call_id=item["call_id"]))
        if not isinstance(output, str):
            raise EngineError("runtime", "Agent tool callback must return text.")
        return output


def _observe_tool_task(task: asyncio.Task) -> None:
    if not task.cancelled():
        task.exception()


async def _tool_results(tasks: list[asyncio.Task]) -> list[str]:
    pending = set(tasks)
    while pending:
        completed, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
        for task in tasks:
            if task in completed:
                outcome = task.result()
                if isinstance(outcome, _TaskInterruption):
                    raise outcome.error
    return [task.result() for task in tasks]


async def run_agent(client: AsyncOpenAI, model_name: str, request: AgentRequest, on_event: AgentEventSink | None) -> AgentResult:
    history = model_input(request.input)
    bindings = {tool.name: tool for tool in request.tools}
    input_filter = ModelInputFilter(request.tool_output_filter)
    parameters = response_parameters(model_name, history, request.instructions, tools=request.tools, output=request.output, agent=True)
    usage = None

    async def emit(event: AgentEvent | None) -> None:
        if request.stream and event is not None and on_event is not None:
            await on_event(event)

    async with TraceSession("Agent workflow", enabled=request.tracing_enabled), _tool_invocations() as invocation_tasks:
        with span("agent", name=request.name, handoffs=[], tools=list(bindings), output_type=request.output.name if request.output is not None else "str"):
            await emit(AgentEvent(kind="agent_started", agent_name=request.name))
            for _turn in range(request.max_turns):
                filtered = input_filter(ModelInputData(input=history, instructions=request.instructions))
                parameters["input"] = filtered.input
                with span("response", response_id=None, usage=None) as response_span:
                    response = await _read_response(client, parameters, stream=request.stream)
                    measured = token_usage(response.usage)
                    usage = add_usage(usage, measured)
                    if response_span is not None and measured is not None:
                        response_span["span_data"]["usage"] = {"input_tokens": measured.input_tokens, "output_tokens": measured.output_tokens}
                items = response_items(response)
                calls = [item for item in items if item["type"] == "function_call"]
                for item in calls:
                    if item.get("name") not in bindings or not isinstance(item.get("call_id"), str) or not item["call_id"]:
                        raise EngineError("invalid_output", "Model provider returned an invalid function call.")
                for item in items:
                    await emit(semantic_event(item))
                history.extend(items)
                if calls:
                    tasks = []
                    for item in calls:
                        task = asyncio.create_task(_capture_interruption(_invoke_tool(bindings[item["name"]], item)))
                        invocation_tasks.add(task)
                        task.add_done_callback(invocation_tasks.discard)
                        task.add_done_callback(_observe_tool_task)
                        tasks.append(task)
                    outputs = await _tool_results(tasks)
                    for item, output in zip(calls, outputs, strict=True):
                        history.append({"type": "function_call_output", "call_id": item["call_id"], "output": output})
                        await emit(AgentEvent(kind="tool_output", tool_name=item["name"], call_id=item["call_id"], output=output))
                    continue
                messages = [item for item in items if item["type"] == "message"]
                last_message = messages[-1] if messages else {"content": []}
                text = _item_text(last_message)
                if request.output is None:
                    return AgentResult(final_output=text, usage=usage)
                if text:
                    try:
                        final_output = request.output.parse(text)
                    except (ValueError, TypeError):
                        raise EngineError("invalid_output", "Model provider returned an invalid response or structured output.") from None
                    return AgentResult(final_output=final_output, usage=usage)
            raise EngineError("max_turns", "Agent exceeded the configured turn limit.")
