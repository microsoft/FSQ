# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from __future__ import annotations

import asyncio
import logging
import sys
from contextlib import asynccontextmanager
from contextvars import ContextVar
from copy import deepcopy
from typing import TYPE_CHECKING

from google import genai
from google.genai.client import DebugConfig

from ._backend import BackendModel
from ._contracts import EngineError, ToolOutputEntry
from ._google_gemini_conversion import GoogleStream, google_error, google_parameters, google_result, google_turn
from ._runner import _close_stream, run_agent

if TYPE_CHECKING:
    from ._contracts import AgentEventSink, AgentRequest, Model, ModelRequest

_safe_logging: ContextVar[bool] = ContextVar("google_model_safe_logging", default=False)


class _SafeLogFilter(logging.Filter):
    def filter(self, record):
        if _safe_logging.get() or record.name == __name__:
            record.msg = "Google model diagnostic (%s)."
            record.args = (record.levelname,)
            record.exc_info = None
            record.exc_text = None
        return True


_log_filter = _SafeLogFilter()
_logger = logging.getLogger(__name__)
_logger.addFilter(_log_filter)


class GoogleGeminiModelProvider:
    def __init__(self, *, base_url: str, api_key: str):
        if base_url != "https://generativelanguage.googleapis.com/v1beta/" or not api_key.strip():
            raise EngineError("configuration", "Google model endpoint and credential are invalid.")
        self._api_key = api_key
        self._client = None
        self._models = {}
        self._loop = None
        self._busy = False
        self._closed = False

    def _check_open(self):
        if self._closed:
            raise EngineError("closed", "Model provider is closed.")

    def _check_loop(self):
        loop = asyncio.get_running_loop()
        if self._loop is not None and self._loop is not loop:
            raise EngineError("lifecycle", "Model provider belongs to another event loop.")
        self._loop = loop

    def get_model(self, model_name: str):
        self._check_open()
        if not model_name.strip():
            raise EngineError("configuration", "An explicit model name is required.")
        if model_name not in self._models:
            self._models[model_name] = _GoogleGeminiModel(self, model_name)
        return self._models[model_name]

    @asynccontextmanager
    async def _use_client(self):
        self._check_open()
        self._check_loop()
        if self._busy:
            raise EngineError("lifecycle", "Model provider is already in use.")
        self._busy = True
        log_scope = _safe_logging.set(True)
        try:
            if self._client is None:
                self._client = genai.Client(
                    api_key=self._api_key,
                    enterprise=False,
                    vertexai=False,
                    debug_config=DebugConfig(client_mode=None, replays_directory=None, replay_id=None),
                    http_options={
                        "base_url": "https://generativelanguage.googleapis.com",
                        "api_version": "v1beta",
                        "async_client_args": {"follow_redirects": False},
                        "client_args": {"follow_redirects": False},
                    },
                )
            resource = self._client.aio.interactions
            for name in tuple(logging.Logger.manager.loggerDict):
                if name.startswith(("google.genai", "google_genai", "httpx", "httpcore")):
                    logging.getLogger(name).addFilter(_log_filter)
            yield resource
        except EngineError:
            raise
        except Exception as error:
            _logger.exception("Google model operation failed.")
            raise google_error(error) from None
        finally:
            _safe_logging.reset(log_scope)
            self._busy = False

    async def aclose(self):
        if self._closed and self._client is None:
            return
        if self._loop is not None:
            self._check_loop()
        if self._busy:
            raise EngineError("lifecycle", "Cannot close a model provider while it is in use.")
        self._closed = True
        self._api_key = ""
        self._models.clear()
        self._busy = True
        try:
            if self._client is not None:
                await self._client.aio.aclose()
                self._client.close()
                self._client = None
        except Exception:
            _logger.exception("Google model cleanup failed.")
            raise EngineError("cleanup", "Model provider cleanup failed.") from None
        finally:
            self._busy = False


class _GoogleGeminiModel(BackendModel):
    def __init__(self, provider, name):
        self._provider = provider
        self._name = name

    def create_engine(self):
        return GoogleGeminiAgentEngine()

    async def complete(self, request: ModelRequest):
        parameters = google_parameters(self._name, request)
        async with self._provider._use_client() as resource:
            response = await resource.create(**parameters)
            return google_result(response.model_dump(mode="json", by_alias=True, exclude_none=True))


class GoogleGeminiAgentEngine:
    def __init__(self):
        self._loop = None
        self._busy = False

    async def run(self, model: Model, request: AgentRequest, *, on_event: AgentEventSink | None = None):
        if not isinstance(model, _GoogleGeminiModel):
            raise EngineError("incompatible_model", "The selected model is incompatible with this agent backend.")
        loop = asyncio.get_running_loop()
        if self._busy or (self._loop is not None and self._loop is not loop):
            raise EngineError("lifecycle", "Agent engine is busy or belongs to another event loop.")
        self._loop = loop
        if request.max_turns < 1 or not request.name.strip() or len({tool.name for tool in request.tools}) != len(request.tools):
            raise EngineError("configuration", "Agent name, positive turn limit, and unique tool names are required.")
        self._busy = True
        try:
            parameters = google_parameters(model._name, request)
            async with model._provider._use_client() as resource:
                return await run_agent(_GoogleConversation(resource, parameters, request.stream), request, on_event)
        finally:
            self._busy = False


class _GoogleConversation:
    def __init__(self, resource, parameters, stream):
        self.resource = resource
        self.parameters = parameters
        self.history = parameters["input"]
        self.stream = stream
        self.outputs = []

    def tool_outputs(self):
        return tuple(self.outputs)

    def add_tool_output(self, call, output):
        self.outputs.append(ToolOutputEntry(entry_id=len(self.history), call_id=call["call_id"], tool_name=call["name"], output=output))
        self.history.append({"type": "function_result", "call_id": call["call_id"], "name": call["name"], "result": [{"type": "text", "text": output}]})

    async def request(self, replacements):
        history = deepcopy(self.history)
        for index, replacement in replacements.items():
            history[index]["result"] = [{"type": "text", "text": replacement}]
        parameters = {**self.parameters, "input": history}
        if self.stream:
            stream = await self.resource.create(**parameters, stream=True)
            collector = GoogleStream()
            try:
                async for event in stream:
                    collector.add(event.model_dump(mode="json", by_alias=True, exclude_none=True))
                response = collector.finish()
            finally:
                await _close_stream(stream, sys.exception())
        else:
            result = await self.resource.create(**parameters)
            response = result.model_dump(mode="json", by_alias=True, exclude_none=True)
        turn = google_turn(response, allow_tools=True)
        self.history.extend(deepcopy(response["steps"]))
        return turn
