# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import asyncio
from typing import ClassVar

import pytest

from fsq_agent.agent_engine import EngineError, ModelRequest, ModelResult, OutputContract, TokenUsage
from fsq_agent.providers import ModelProviderSession
from fsq_agent.providers._azure_openai import ProviderClientConfig


class _LoopBoundProvider:
    instances: ClassVar[list["_LoopBoundProvider"]] = []

    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs
        self.requests: list[ModelRequest] = []
        self.create_loop: asyncio.AbstractEventLoop | None = None
        self.closed = False
        self.model_name = ""
        self.instances.append(self)

    def get_model(self, model_name: str):
        self.model_name = model_name
        return self

    async def complete(self, request: ModelRequest) -> ModelResult:
        self.create_loop = asyncio.get_running_loop()
        self.requests.append(request)
        return ModelResult(text='{"passed": true}')

    async def aclose(self) -> None:
        if asyncio.get_running_loop() is not self.create_loop:
            raise RuntimeError("Event loop is closed")
        self.closed = True


def _client_config() -> ProviderClientConfig:
    return ProviderClientConfig(
        provider="test",
        model="test-model",
        api_key="test-key",
        base_url="https://example.test/openai/v1/",
    )


@pytest.mark.parametrize("synchronous", [False, True])
@pytest.mark.parametrize("model_name", ["opaque-deployment", "gpt-5", "gpt-5.2-pro"])
async def test_azure_reasoning_effort_context_survives_session_construction(tmp_path, monkeypatch, synchronous, model_name):
    from fsq_agent.config import Settings, refresh_provider_settings, save_azure_openai_provider
    from fsq_agent.providers import _session as session_module
    from fsq_agent.providers import build_model_provider_session

    _LoopBoundProvider.instances = []
    monkeypatch.setattr(session_module, "create_model_provider", _LoopBoundProvider)
    save_azure_openai_provider(base_url="https://example.test/openai/v1/", model=model_name, api_key="test-key", user_config_root=tmp_path)
    settings = refresh_provider_settings(Settings(), tmp_path)
    settings.agent_runtime.reasoning_effort = "high"
    session = build_model_provider_session(settings)
    request = ModelRequest("check", reasoning_effort="low")
    try:
        if synchronous:
            session.complete_sync(request)
        else:
            await session.complete(request)
        provider = _LoopBoundProvider.instances[0]
        assert provider.kwargs["model_name_is_deployment"] is True
        assert provider.model_name == model_name
        assert provider.requests == [request]
        assert settings.agent_runtime.reasoning_effort == "high"
    finally:
        await session.close()
    assert provider.closed


async def test_complete_sync_from_running_loop_does_not_reuse_closed_loop() -> None:
    _LoopBoundProvider.instances = []
    session = ModelProviderSession(_client_config(), provider_factory=_LoopBoundProvider)

    response = session.complete_sync(ModelRequest(input="check"))
    session.close_sync()

    assert response.text == '{"passed": true}'
    assert len(_LoopBoundProvider.instances) == 1
    provider = _LoopBoundProvider.instances[0]
    assert provider.closed is True
    assert provider.model_name == "test-model"
    assert provider.requests == [ModelRequest(input="check")]


async def test_complete_async_reuses_provider_until_close() -> None:
    _LoopBoundProvider.instances = []
    session = ModelProviderSession(_client_config(), provider_factory=_LoopBoundProvider)

    response = await session.complete(ModelRequest(input="check"))

    assert response.text == '{"passed": true}'
    assert len(_LoopBoundProvider.instances) == 1
    provider = _LoopBoundProvider.instances[0]
    assert provider.closed is False
    assert session.get_model() is provider

    await session.close()

    assert provider.closed is True


def test_repeated_sync_requests_create_independent_providers() -> None:
    _LoopBoundProvider.instances = []
    session = ModelProviderSession(_client_config(), provider_factory=_LoopBoundProvider)
    session.complete_sync(ModelRequest(input="first"))
    session.complete_sync(ModelRequest(input="second"))
    assert len(_LoopBoundProvider.instances) == 2
    assert all(provider.closed for provider in _LoopBoundProvider.instances)
    assert _LoopBoundProvider.instances[0].create_loop is not _LoopBoundProvider.instances[1].create_loop


def test_cleanup_failure_does_not_replace_invocation_failure() -> None:
    class FailingProvider(_LoopBoundProvider):
        async def complete(self, request: ModelRequest) -> ModelResult:
            raise EngineError("timeout", "Primary model timeout.")

        async def aclose(self) -> None:
            self.closed = True
            raise RuntimeError("Secondary cleanup failure")

    session = ModelProviderSession(_client_config(), provider_factory=FailingProvider)
    with pytest.raises(EngineError, match="Primary model timeout"):
        session.complete_sync(ModelRequest(input="test"))


def test_provider_exports_resolve_and_exclude_business_services() -> None:
    from fsq_agent import ai_services, providers

    assert all(hasattr(providers, name) for name in providers.__all__)
    for name in ("AIAssertionEvaluator", "CaseSuggestionAnalyzer", "CaseSuggestionAnalysis", "build_ai_assertion_evaluator", "build_case_suggestion_analyzer"):
        assert name not in providers.__all__
        assert hasattr(ai_services, name)


async def test_wrong_loop_close_keeps_provider_owned_for_correct_loop_cleanup() -> None:
    session = ModelProviderSession(_client_config(), provider_factory=_LoopBoundProvider)
    await session.complete(ModelRequest(input="test"))
    provider = session.get_model()
    with pytest.raises(RuntimeError, match="Event loop is closed"):
        session.close_sync()
    assert session.get_model() is provider
    assert not provider.closed
    await session.close()
    assert provider.closed


async def test_rejected_close_keeps_provider_owned_until_success() -> None:
    class RejectOnceProvider(_LoopBoundProvider):
        close_attempts = 0

        async def aclose(self) -> None:
            self.close_attempts += 1
            if self.close_attempts == 1:
                raise EngineError("lifecycle", "Provider is still in use.")
            await super().aclose()

    session = ModelProviderSession(_client_config(), provider_factory=RejectOnceProvider)
    await session.complete(ModelRequest(input="test"))
    provider = session.get_model()
    with pytest.raises(EngineError, match="still in use"):
        await session.close()
    assert session.get_model() is provider
    await session.close()
    assert provider.closed
    assert provider.close_attempts == 2


@pytest.mark.parametrize("synchronous", [False, True])
async def test_cleanup_cancellation_does_not_replace_primary_invocation_failure(synchronous: bool) -> None:
    primary = EngineError("timeout", "Primary model timeout.")
    providers = []

    class CancelledCloseProvider(_LoopBoundProvider):
        def __init__(self, **kwargs) -> None:
            super().__init__(**kwargs)
            providers.append(self)

        async def complete(self, request: ModelRequest) -> ModelResult:
            raise primary

        async def aclose(self) -> None:
            self.closed = True
            raise asyncio.CancelledError("secondary cleanup cancellation")

    session = ModelProviderSession(_client_config(), provider_factory=CancelledCloseProvider)

    async def invoke_session() -> None:
        if synchronous:
            session.complete_sync(ModelRequest(input="test"))
        else:
            try:
                await session.complete(ModelRequest(input="test"))
            finally:
                await session.close()

    with pytest.raises(EngineError) as failure:
        await invoke_session()
    assert failure.value is primary
    assert providers[0].closed


async def test_cleanup_cancellation_without_primary_error_propagates() -> None:
    class CancelledCloseProvider(_LoopBoundProvider):
        async def aclose(self) -> None:
            raise asyncio.CancelledError("cleanup cancelled")

    session = ModelProviderSession(_client_config(), provider_factory=CancelledCloseProvider)
    await session.complete(ModelRequest(input="test"))
    provider = session.get_model()
    with pytest.raises(asyncio.CancelledError):
        await session.close()
    assert session.get_model() is provider


@pytest.mark.parametrize("synchronous", [False, True])
async def test_session_forwards_contract_and_result_without_parsing(synchronous):
    parsed = object()
    usage = TokenUsage(1, 2, 3)
    expected = ModelResult("original text", usage, "stop", parsed)
    requests = []

    def forbidden_parser(text):
        raise AssertionError("Provider sessions must not invoke the output parser")

    contract = OutputContract("contract", {"type": "object"}, forbidden_parser)
    request = ModelRequest("Check", "Instructions", contract)

    class ParsedProvider(_LoopBoundProvider):
        async def complete(self, actual):
            await super().complete(actual)
            requests.append(actual)
            return expected

    session = ModelProviderSession(_client_config(), provider_factory=ParsedProvider)
    try:
        result = session.complete_sync(request) if synchronous else await session.complete(request)
        assert result is expected
        assert result.parsed_output is parsed
        assert result.usage is usage
        assert requests == [request]
        assert requests[0] is request
        assert requests[0].output is contract
    finally:
        await session.close()


def test_neutral_positional_constructors_remain_compatible():
    usage = TokenUsage(1, 2, 3)
    request = ModelRequest("Check", "Instructions")
    result = ModelResult("original", usage, "stop")
    assert request.input == "Check"
    assert request.instructions == "Instructions"
    assert request.output is None
    assert result.text == "original"
    assert result.usage is usage
    assert result.finish_reason == "stop"
    assert result.parsed_output is None
