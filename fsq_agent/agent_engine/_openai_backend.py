# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from __future__ import annotations

import asyncio
import logging
import sys
import threading
from contextlib import asynccontextmanager
from contextvars import ContextVar
from typing import TYPE_CHECKING

from agents import Agent, ModelSettings, OpenAIProvider, RunConfig, Runner, _debug
from agents.models.interface import ModelTracing
from agents.tracing.provider import TraceProvider
from agents.tracing.setup import get_trace_provider, set_trace_provider
from agents.tracing.spans import NoOpSpan, SpanImpl
from openai import AsyncOpenAI

from ._context import ModelInputFilter
from ._contracts import AgentResult, EngineError
from ._conversion import SDKOutputContract, engine_error, model_input, model_result, sdk_tool, semantic_event, token_usage

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Mapping

    from agents.models.interface import Model as SDKModel

    from ._contracts import AgentEventSink, AgentRequest, Model, ModelRequest, ModelResult, ToolCall


class _SafeModelLogFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = "Model backend diagnostic (%s)."
        record.args = (record.levelname,)
        record.exc_info = None
        record.exc_text = None
        return True


_debug.DONT_LOG_MODEL_DATA = True
_debug.DONT_LOG_TOOL_DATA = True
for _logger_name in ("openai.agents", "openai._base_client"):
    logging.getLogger(_logger_name).addFilter(_SafeModelLogFilter())

_logger = logging.getLogger("openai.agents")

_safe_trace_errors: ContextVar[bool] = ContextVar("agent_engine_safe_trace_errors", default=False)
_trace_provider_lock = threading.Lock()


class _SafeErrorSpan(SpanImpl):
    def set_error(self, error) -> None:
        super().set_error({"message": "Agent operation failed.", "data": None})


class _SafeTraceProvider(TraceProvider):
    def __init__(self, delegate: TraceProvider) -> None:
        self._delegate = delegate

    def register_processor(self, processor) -> None:
        self._delegate.register_processor(processor)

    def set_processors(self, processors) -> None:
        self._delegate.set_processors(processors)

    def get_current_trace(self):
        return self._delegate.get_current_trace()

    def get_current_span(self):
        return self._delegate.get_current_span()

    def set_disabled(self, disabled: bool) -> None:
        self._delegate.set_disabled(disabled)

    def time_iso(self) -> str:
        return self._delegate.time_iso()

    def gen_trace_id(self) -> str:
        return self._delegate.gen_trace_id()

    def gen_span_id(self) -> str:
        return self._delegate.gen_span_id()

    def gen_group_id(self) -> str:
        return self._delegate.gen_group_id()

    def create_trace(self, *args, **kwargs):
        return self._delegate.create_trace(*args, **kwargs)

    def create_span(self, *args, **kwargs):
        span = self._delegate.create_span(*args, **kwargs)
        if not _safe_trace_errors.get() or isinstance(span, NoOpSpan):
            return span
        if not isinstance(span, SpanImpl):
            raise EngineError("configuration", "The configured trace provider does not support safe agent spans.")
        return _SafeErrorSpan(
            trace_id=span.trace_id,
            span_id=span.span_id,
            parent_id=span.parent_id,
            processor=span._processor,
            span_data=span.span_data,
            tracing_api_key=span.tracing_api_key,
            trace_metadata=span.trace_metadata,
        )

    def force_flush(self) -> None:
        self._delegate.force_flush()

    def shutdown(self) -> None:
        self._delegate.shutdown()


def _install_trace_error_safety() -> None:
    with _trace_provider_lock:
        provider = get_trace_provider()
        if not isinstance(provider, _SafeTraceProvider):
            set_trace_provider(_SafeTraceProvider(provider))


class OpenAIModelProvider:
    def __init__(self, *, base_url: str, api_key: str, headers: Mapping[str, str] | None = None) -> None:
        if not base_url.strip() or not api_key.strip():
            raise EngineError("configuration", "Model endpoint and credential are required.")
        self._base_url = base_url
        self._api_key = api_key
        self._headers = dict(headers or {})
        self._client: AsyncOpenAI | None = None
        self._provider: OpenAIProvider | None = None
        self._models: dict[str, _OpenAIModel] = {}
        self._loop: asyncio.AbstractEventLoop | None = None
        self._busy = False
        self._closed = False

    def get_model(self, model_name: str) -> Model:
        self._check_open()
        if not model_name.strip():
            raise EngineError("configuration", "An explicit model name is required.")
        if model_name not in self._models:
            self._models[model_name] = _OpenAIModel(self, model_name)
        return self._models[model_name]

    def _check_open(self) -> None:
        if self._closed:
            raise EngineError("closed", "Model provider is closed.")

    def _check_loop(self) -> None:
        loop = asyncio.get_running_loop()
        if self._loop is not None and self._loop is not loop:
            raise EngineError("lifecycle", "Model provider belongs to another event loop.")
        self._loop = loop

    @asynccontextmanager
    async def _use_model(self, name: str) -> AsyncIterator[SDKModel]:
        self._check_open()
        self._check_loop()
        if self._busy:
            raise EngineError("lifecycle", "Model provider is already in use.")
        self._busy = True
        try:
            if self._client is None:
                self._client = AsyncOpenAI(api_key=self._api_key, base_url=self._base_url, default_headers=self._headers or None)
                self._provider = OpenAIProvider(openai_client=self._client, use_responses=True, use_responses_websocket=False)
            if self._provider is None:
                raise EngineError("configuration", "Model provider initialization failed.")
            yield self._provider.get_model(name)
        finally:
            self._busy = False

    async def aclose(self) -> None:
        if self._closed and self._provider is None and self._client is None:
            return
        if self._loop is not None:
            self._check_loop()
        if self._busy:
            raise EngineError("lifecycle", "Cannot close a model provider while it is in use.")
        self._closed = True
        provider, client = self._provider, self._client
        self._models.clear()
        self._api_key = ""
        self._headers.clear()
        failure: BaseException | None = None
        self._busy = True
        try:
            for attribute, close in (("_provider", provider.aclose if provider else None), ("_client", client.close if client else None)):
                if close is None:
                    continue
                try:
                    await close()
                except BaseException as error:
                    _logger.exception("Model provider cleanup failed.")
                    failure = failure or error
                else:
                    setattr(self, attribute, None)
        finally:
            self._busy = False
        if isinstance(failure, Exception):
            raise EngineError("cleanup", "Model provider cleanup failed.") from None
        if failure is not None:
            raise failure


class _OpenAIModel:
    def __init__(self, provider: OpenAIModelProvider, name: str) -> None:
        self._provider = provider
        self._name = name

    async def complete(self, request: ModelRequest) -> ModelResult:
        try:
            async with self._provider._use_model(self._name) as model:
                response = await model.get_response(
                    system_instructions=request.instructions,
                    input=model_input(request.input),
                    model_settings=ModelSettings(),
                    tools=[],
                    output_schema=None,
                    handoffs=[],
                    tracing=ModelTracing.DISABLED,
                    previous_response_id=None,
                    conversation_id=None,
                    prompt=None,
                )
                return model_result(response)
        except EngineError:
            raise
        except Exception as error:
            _logger.exception("Model operation failed.")
            raise engine_error(error) from None


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
                    if isinstance(outcome, BaseException):
                        raise outcome


class OpenAIAgentEngine:
    def __init__(self) -> None:
        self._busy = False

    async def run(self, model: Model, request: AgentRequest, *, on_event: AgentEventSink | None = None) -> AgentResult:
        if not isinstance(model, _OpenAIModel):
            raise EngineError("incompatible_model", "The selected model is incompatible with this agent backend.")
        if self._busy:
            raise EngineError("lifecycle", "Agent engine is already running.")
        if request.max_turns < 1 or not request.name.strip():
            raise EngineError("configuration", "Agent name and positive turn limit are required.")
        if len({tool.name for tool in request.tools}) != len(request.tools):
            raise EngineError("configuration", "Agent tool names must be unique.")
        self._busy = True
        trace_scope = _safe_trace_errors.set(request.tracing_enabled)
        try:
            if request.tracing_enabled:
                _install_trace_error_safety()
            async with model._provider._use_model(model._name) as sdk_model, _tool_invocations() as invocation_tasks:
                agent = Agent(
                    name=request.name,
                    instructions=request.instructions,
                    model=sdk_model,
                    model_settings=ModelSettings(reasoning={"effort": "medium"}, verbosity="medium"),
                    tools=[sdk_tool(tool, invocation_tasks) for tool in request.tools],
                    output_type=SDKOutputContract(request.output) if request.output is not None else None,
                )
                run_config = RunConfig(
                    tracing_disabled=not request.tracing_enabled,
                    trace_include_sensitive_data=False,
                    call_model_input_filter=ModelInputFilter(request.trimming, request.tool_output_filter) if request.trimming is not None or request.tool_output_filter is not None else None,
                )
                if request.stream:
                    result = await self._run_streamed(agent, request, run_config, on_event)
                else:
                    result = await Runner.run(agent, input=model_input(request.input), max_turns=request.max_turns, run_config=run_config)
                return AgentResult(final_output=result.final_output, usage=token_usage(result.context_wrapper.usage))
        except EngineError:
            raise
        except Exception as error:
            _logger.exception("Agent operation failed.")
            raise engine_error(error) from None
        finally:
            _safe_trace_errors.reset(trace_scope)
            self._busy = False

    async def _run_streamed(self, agent: Agent, request: AgentRequest, run_config: RunConfig, on_event: AgentEventSink | None):
        result = Runner.run_streamed(agent, input=model_input(request.input), max_turns=request.max_turns, run_config=run_config)
        events = result.stream_events()
        calls: dict[str, ToolCall] = {}
        failure: BaseException | None = None
        try:
            async for sdk_event in events:
                event = semantic_event(sdk_event, calls)
                if event is not None and on_event is not None:
                    await on_event(event)
        except BaseException as error:
            failure = error
            result.cancel()
            raise
        else:
            return result
        finally:
            if not result.is_complete:
                result.cancel()
            cleanup_error: BaseException | None = None
            try:
                await events.aclose()
            except BaseException as error:
                _logger.exception("Agent stream cleanup failed.")
                cleanup_error = error
            if result.run_loop_task is not None:
                outcomes = await asyncio.gather(result.run_loop_task, return_exceptions=True)
                if failure is None and isinstance(outcomes[0], BaseException):
                    raise outcomes[0]
            if cleanup_error is not None and failure is None:
                if not isinstance(cleanup_error, Exception):
                    raise cleanup_error
                raise EngineError("cleanup", "Agent stream cleanup failed.") from None
