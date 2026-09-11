# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from collections.abc import Callable

import httpx
import pytest

from fsq_agent import providers
from fsq_agent.config import Settings, refresh_provider_settings, save_openai_provider
from fsq_agent.models import ConfigurationError


def _mock_client(monkeypatch: pytest.MonkeyPatch, handler: Callable[[httpx.Request], httpx.Response]) -> list[httpx.Client]:
    original_client = httpx.Client
    clients = []

    def create_client(**kwargs):
        client = original_client(transport=httpx.MockTransport(handler), **kwargs)
        clients.append(client)
        return client

    monkeypatch.setattr(httpx, "Client", create_client)
    return clients


def test_openai_models_use_official_endpoint_and_filter_general_gpt_models(monkeypatch):
    model_ids = ["gpt-5.4", "gpt-5", "gpt-10", "gpt-4.1", "gpt-5.4", "o3", "gpt-5foo"]
    model_ids += [f"gpt-5-{suffix}" for suffix in ("mini", "nano", "codex", "embedding", "audio", "realtime", "image", "search", "transcribe", "transcription", "tts")]

    def respond(request):
        assert str(request.url) == "https://api.openai.com/v1/models"
        assert request.headers["authorization"] == "Bearer candidate-key"
        return httpx.Response(200, json={"object": "list", "data": [{"id": model_id} for model_id in model_ids]})

    clients = _mock_client(monkeypatch, respond)
    models = providers.list_openai_models(api_key=" candidate-key ")
    assert [(model.id, model.name) for model in models] == [(model_id, model_id) for model_id in ("gpt-10", "gpt-5", "gpt-5.4")]
    assert all(client.is_closed for client in clients)
    with pytest.raises(AttributeError):
        models[0].name = "changed"


def test_openai_reasoning_effort_eligibility_preserves_snapshots_and_future_versions(monkeypatch):
    eligible = ["gpt-5", "gpt-5-2025-08-07", "gpt-5.1", "gpt-5.2-pro", "gpt-5.4-pro-2026-03-05", "gpt-5.7", "gpt-5.10-pro", "gpt-6", "gpt-7", "gpt-7-pro"]
    excluded = ["gpt-5-pro", "gpt-5-pro-2025-10-06", "gpt-5.1-pro", "gpt-5-chat-latest", "gpt-5.2-chat", "gpt-7-chat", "gpt-7-mini", "gpt-5.10-codex"]
    _mock_client(monkeypatch, lambda request: httpx.Response(200, json={"object": "list", "data": [{"id": name} for name in eligible + excluded + eligible]}))

    assert [model.id for model in providers.list_openai_models(api_key="candidate-key")] == sorted(eligible)


@pytest.mark.parametrize("model_ids", [[], ["gpt-4.1", "gpt-5-mini"]])
def test_openai_empty_eligible_set_is_success(monkeypatch, model_ids):
    _mock_client(monkeypatch, lambda request: httpx.Response(200, json={"object": "list", "data": [{"id": model_id} for model_id in model_ids]}))
    assert providers.list_openai_models(api_key="candidate-key") == ()


@pytest.mark.parametrize("status,reason", [(401, "authentication"), (403, "access_denied"), (429, "rate_limited"), (503, "network"), (302, "network")])
def test_openai_model_http_errors_are_safe_and_close_client(monkeypatch, status, reason):
    clients = _mock_client(monkeypatch, lambda request: httpx.Response(status, text="private-upstream candidate-key", headers={"location": "https://untrusted.example/models"}))
    with pytest.raises(ConfigurationError) as caught:
        providers.list_openai_models(api_key="candidate-key")
    assert caught.value.context == {"provider": "openai", "reason": reason}
    assert "private-upstream" not in str(caught.value)
    assert "candidate-key" not in str(caught.value)
    assert all(client.is_closed for client in clients)


@pytest.mark.parametrize("exception_type,reason", [(httpx.ReadTimeout, "timeout"), (httpx.ConnectError, "network")])
def test_openai_model_network_errors_are_classified(monkeypatch, exception_type, reason):
    def respond(request):
        raise exception_type("private-upstream candidate-key", request=request)

    clients = _mock_client(monkeypatch, respond)
    with pytest.raises(ConfigurationError) as caught:
        providers.list_openai_models(api_key="candidate-key")
    assert caught.value.context == {"provider": "openai", "reason": reason}
    assert "candidate-key" not in str(caught.value)
    assert all(client.is_closed for client in clients)


@pytest.mark.parametrize(
    "payload", [None, {}, {"object": "list", "data": None}, {"object": "list", "data": [None]}, {"object": "list", "data": [{"id": " "}]}, {"object": "list", "data": [{"id": 5}]}]
)
def test_openai_invalid_model_response_does_not_become_empty_success(monkeypatch, payload):
    _mock_client(monkeypatch, lambda request: httpx.Response(200, json=payload))
    with pytest.raises(ConfigurationError) as caught:
        providers.list_openai_models(api_key="candidate-key")
    assert caught.value.context["reason"] == "malformed_response"


def test_openai_oversized_model_response_is_rejected(monkeypatch):
    clients = _mock_client(monkeypatch, lambda request: httpx.Response(200, content=b"x" * (1024 * 1024 + 1)))
    with pytest.raises(ConfigurationError) as caught:
        providers.list_openai_models(api_key="candidate-key")
    assert caught.value.context["reason"] == "malformed_response"
    assert all(client.is_closed for client in clients)


@pytest.mark.parametrize("api_key", ["", " ", "replace-with-key"])
def test_openai_invalid_key_fails_before_network(monkeypatch, api_key):
    clients = _mock_client(monkeypatch, lambda request: pytest.fail("Invalid key must not contact OpenAI"))
    with pytest.raises(ConfigurationError) as caught:
        providers.list_openai_models(api_key=api_key)
    assert caught.value.context["reason"] == "invalid_candidate"
    assert clients == []


@pytest.mark.parametrize("factory_name", ["build_model_provider_session", "prepare_model_provider_session", "refresh_model_provider_session"])
def test_openai_session_factories_use_saved_official_configuration_without_discovery(tmp_path, monkeypatch, factory_name):
    save_openai_provider(model="gpt-5", api_key="saved-key", user_config_root=tmp_path)
    settings = refresh_provider_settings(Settings(), tmp_path)
    clients = _mock_client(monkeypatch, lambda request: pytest.fail("Readiness and construction must not call model discovery"))
    session = getattr(providers, factory_name)(settings)
    assert session.provider == "openai"
    assert session.model == "gpt-5"
    assert session.client_config.api_key == "saved-key"
    assert session.client_config.base_url == "https://api.openai.com/v1/"
    assert session.client_config.default_headers == {}
    assert session.metadata == {"endpoint_family": "openai"}
    assert "saved-key" not in repr(session.client_config)
    session.close_sync()
    assert clients == []
