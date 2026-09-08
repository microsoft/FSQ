# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import asyncio
import sys
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

from fsq_agent.agent_engine import EngineError, Model, ModelProvider, ModelRequest, ModelResult, create_model_provider
from fsq_agent.models import ConfigurationError
from fsq_agent.providers._azure_openai import ProviderClientConfig


class ModelProviderSession:
    def __init__(self, client_config: ProviderClientConfig, *, provider_factory: Callable[..., ModelProvider] | None = None) -> None:
        self.client_config = client_config
        self.provider = client_config.provider
        self.model = client_config.model
        self.metadata = dict(client_config.metadata)
        self._provider_factory = provider_factory or create_model_provider
        self._model_provider: ModelProvider | None = None

    def get_model(self) -> Model:
        if self._model_provider is None:
            self._model_provider = self._new_provider()
        return self._model_provider.get_model(self.model)

    async def complete(self, request: ModelRequest) -> ModelResult:
        return await self.get_model().complete(request)

    def complete_sync(self, request: ModelRequest) -> ModelResult:
        return _run_async_sync(self._complete_once(request))

    async def close(self) -> None:
        provider = self._model_provider
        if provider is not None and await _close_provider(provider, suppress_errors=sys.exception() is not None):
            if self._model_provider is provider:
                self._model_provider = None

    def close_sync(self) -> None:
        if self._model_provider is not None:
            _run_async_sync(self.close())

    async def _complete_once(self, request: ModelRequest) -> ModelResult:
        provider = self._new_provider()
        try:
            return await provider.get_model(self.model).complete(request)
        finally:
            await _close_provider(provider, suppress_errors=sys.exception() is not None)

    def _new_provider(self) -> ModelProvider:
        try:
            return self._provider_factory(api_key=self.client_config.api_key, base_url=self.client_config.base_url, headers=self.client_config.default_headers or None)
        except EngineError as error:
            if error.category == "configuration":
                raise ConfigurationError(str(error)) from None
            raise


async def _close_provider(provider: ModelProvider, *, suppress_errors: bool) -> bool:
    try:
        await provider.aclose()
    except BaseException:
        if not suppress_errors:
            raise
        return False
    return True


def _run_async_sync(coro):
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    with ThreadPoolExecutor(max_workers=1) as executor:
        return executor.submit(lambda: asyncio.run(coro)).result()
