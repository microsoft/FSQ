# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from __future__ import annotations

import asyncio
import logging
import sys
from contextlib import asynccontextmanager
from contextvars import ContextVar
from typing import TYPE_CHECKING

from openai import AsyncOpenAI
from openai.types.responses import ResponseStreamEvent

from ._backend import BackendModel, BackendTurn
from ._contracts import AgentResult, EngineError, ToolOutputEntry
from ._conversion import _item_text, check_response, engine_error, model_input, model_result, response_items, response_parameters, semantic_event, token_usage
from ._runner import _close_stream, run_agent

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Mapping

    from ._contracts import AgentEventSink, AgentRequest, Model, ModelRequest, ModelResult, OutputT

_safe_model_logging: ContextVar[bool] = ContextVar("agent_engine_safe_model_logging", default=False)


class _SafeModelLogFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if record.name != __name__ and not _safe_model_logging.get():
            return True
        record.msg = "Model backend diagnostic (%s)."
        record.args = (record.levelname,)
        record.exc_info = None
        record.exc_text = None
        return True


_logger = logging.getLogger(__name__)
_logger.addFilter(_SafeModelLogFilter())
logging.getLogger("openai._base_client").addFilter(_SafeModelLogFilter())


class OpenAIModelProvider:
    def __init__(self, *, base_url: str, api_key: str, headers: Mapping[str, str] | None = None) -> None:
        if not base_url.strip() or not api_key.strip():
            raise EngineError("configuration", "Model endpoint and credential are required.")
        self._base_url = base_url
        self._api_key = api_key
        self._headers = dict(headers or {})
        self._client: AsyncOpenAI | None = None
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
    async def _use_client(self) -> AsyncIterator[AsyncOpenAI]:
        self._check_open()
        self._check_loop()
        if self._busy:
            raise EngineError("lifecycle", "Model provider is already in use.")
        self._busy = True
        log_scope = _safe_model_logging.set(True)
        try:
            if self._client is None:
                self._client = AsyncOpenAI(api_key=self._api_key, base_url=self._base_url, default_headers=self._headers or None)
            yield self._client
        finally:
            _safe_model_logging.reset(log_scope)
            self._busy = False

    async def aclose(self) -> None:
        if self._closed and self._client is None:
            return
        if self._loop is not None:
            self._check_loop()
        if self._busy:
            raise EngineError("lifecycle", "Cannot close a model provider while it is in use.")
        self._closed = True
        self._models.clear()
        self._api_key = ""
        self._headers.clear()
        self._busy = True
        try:
            if self._client is not None:
                await self._client.close()
                self._client = None
        except Exception:
            _logger.exception("Model provider cleanup failed.")
            raise EngineError("cleanup", "Model provider cleanup failed.") from None
        finally:
            self._busy = False


class _OpenAIModel(BackendModel):
    def __init__(self, provider: OpenAIModelProvider, name: str) -> None:
        self._provider = provider
        self._name = name

    def create_engine(self):
        return OpenAIAgentEngine()

    async def complete(self, request: ModelRequest[OutputT]) -> ModelResult[OutputT]:
        try:
            async with self._provider._use_client() as client:
                response = await client.responses.create(**response_parameters(self._name, model_input(request.input), request.instructions, output=request.output))
                return model_result(response, request.output)
        except EngineError:
            raise
        except Exception as error:
            _logger.exception("Model operation failed.")
            raise engine_error(error) from None


class OpenAIAgentEngine:
    def __init__(self) -> None:
        self._busy = False
        self._loop: asyncio.AbstractEventLoop | None = None

    async def run(self, model: Model, request: AgentRequest, *, on_event: AgentEventSink | None = None) -> AgentResult:
        if not isinstance(model, _OpenAIModel):
            raise EngineError("incompatible_model", "The selected model is incompatible with this agent backend.")
        if self._busy:
            raise EngineError("lifecycle", "Agent engine is already running.")
        loop = asyncio.get_running_loop()
        if self._loop is not None and self._loop is not loop:
            raise EngineError("lifecycle", "Agent engine belongs to another event loop.")
        self._loop = loop
        if request.max_turns < 1 or not request.name.strip():
            raise EngineError("configuration", "Agent name and positive turn limit are required.")
        if len({tool.name for tool in request.tools}) != len(request.tools):
            raise EngineError("configuration", "Agent tool names must be unique.")
        self._busy = True
        try:
            async with model._provider._use_client() as client:
                return await run_agent(OpenAIConversation(client, model._name, request), request, on_event)
        except EngineError:
            raise
        except Exception as error:
            _logger.exception("Agent operation failed.")
            raise engine_error(error) from None
        finally:
            self._busy = False


class OpenAIConversation:
    def __init__(self, client: AsyncOpenAI, model_name: str, request: AgentRequest) -> None:
        self.client = client
        self.history = model_input(request.input)
        self.stream = request.stream
        self.parameters = response_parameters(model_name, self.history, request.instructions, tools=request.tools, output=request.output, agent=True)

    def tool_outputs(self) -> tuple[ToolOutputEntry, ...]:
        names = {item["call_id"]: item.get("name") for item in self.history if item.get("type") == "function_call"}
        return tuple(
            ToolOutputEntry(entry_id=index, call_id=item.get("call_id"), tool_name=names.get(item.get("call_id")), output=item["output"])
            for index, item in enumerate(self.history)
            if item.get("type") == "function_call_output"
        )

    def filtered_input(self, replacements: Mapping[int, str]) -> list[dict]:
        return [{**item, "output": replacements[index]} if index in replacements else item for index, item in enumerate(self.history)]

    async def request(self, replacements: Mapping[int, str]) -> BackendTurn:
        parameters = {**self.parameters, "input": self.filtered_input(replacements)}
        response = await _read_response(self.client, parameters, stream=self.stream)
        items = response_items(response)
        self.history.extend(items)
        messages = [item for item in items if item["type"] == "message"]
        return BackendTurn(
            text=_item_text(messages[-1]) if messages else "",
            calls=tuple(item for item in items if item["type"] == "function_call"),
            events=tuple(event for item in items if (event := semantic_event(item)) is not None),
            usage=token_usage(response.usage),
        )

    def add_tool_output(self, call: dict, output: str) -> None:
        self.history.append({"type": "function_call_output", "call_id": call["call_id"], "output": output})


async def _read_response(client: AsyncOpenAI, parameters: dict, *, stream: bool):
    if not stream:
        response = await client.responses.create(**parameters)
        check_response(response)
        return response
    response_stream = await client.responses.create(**parameters, stream=True)
    try:
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
    finally:
        await _close_stream(response_stream, sys.exception())
