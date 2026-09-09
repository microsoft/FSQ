# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from __future__ import annotations

import asyncio
import json
import sys
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, TypeVar

from ._backend import add_usage, decode_tool_arguments
from ._context import ModelInputFilter
from ._contracts import AgentEvent, AgentResult, EngineError, ToolCall, ToolInputFailure
from ._tracing import TraceSession, span

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable

    from ._backend import BackendConversation
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


async def _close_stream(stream, primary: BaseException | None) -> None:
    await _complete_cleanup(stream.close(), primary)


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


async def run_agent(backend: BackendConversation, request: AgentRequest, on_event: AgentEventSink | None) -> AgentResult:
    bindings = {tool.name: tool for tool in request.tools}
    input_filter = ModelInputFilter(request.tool_output_filter)
    usage = None

    async def emit(event: AgentEvent | None) -> None:
        if request.stream and event is not None and on_event is not None:
            await on_event(event)

    async with TraceSession("Agent workflow", enabled=request.tracing_enabled), _tool_invocations() as invocation_tasks:
        with span("agent", name=request.name, handoffs=[], tools=list(bindings), output_type=request.output.name if request.output is not None else "str"):
            await emit(AgentEvent(kind="agent_started", agent_name=request.name))
            for _turn in range(request.max_turns):
                replacements = input_filter(backend.tool_outputs())
                with span("response", response_id=None, usage=None) as response_span:
                    response = await backend.request(replacements)
                    measured = response.usage
                    usage = add_usage(usage, measured)
                    if response_span is not None and measured is not None:
                        response_span["span_data"]["usage"] = {"input_tokens": measured.input_tokens, "output_tokens": measured.output_tokens}
                calls = response.calls
                for item in calls:
                    if item.get("name") not in bindings or not isinstance(item.get("call_id"), str) or not item["call_id"]:
                        raise EngineError("invalid_output", "Model provider returned an invalid function call.")
                for event in response.events:
                    await emit(event)
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
                        backend.add_tool_output(item, output)
                        await emit(AgentEvent(kind="tool_output", tool_name=item["name"], call_id=item["call_id"], output=output))
                    continue
                text = response.text
                if request.output is None:
                    return AgentResult(final_output=text, usage=usage)
                if text:
                    try:
                        final_output = request.output.parse(text)
                    except (ValueError, TypeError):
                        raise EngineError("invalid_output", "Model provider returned an invalid response or structured output.") from None
                    return AgentResult(final_output=final_output, usage=usage)
            raise EngineError("max_turns", "Agent exceeded the configured turn limit.")
