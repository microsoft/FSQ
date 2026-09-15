# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from collections.abc import Callable

import httpx
import pytest

from fsq_agent import providers
from fsq_agent.config import Settings, refresh_provider_settings, save_deepseek_provider
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


def test_deepseek_models_use_official_endpoint_and_forward_compatible_family_filter(monkeypatch):
    eligible = ["deepseek-flash", "deepseek-v4.1-flash", "deepseek-v4.2-flash", "deepseek-v4-pro", "deepseek-v5-flash", "deepseek-v5-pro"]
    excluded = [
        "deepseek-v4-flash",
        "deepseek-v3.9-pro",
        "deepseek-v4.1-flash-vision-exp",
        "deepseek-v4-pro-preview",
        "deepseek-v5-coder",
        "DeepSeek-v5-flash",
        "deepseek-reasoner",
    ]

    def respond(request):
        assert str(request.url) == "https://api.deepseek.com/models"
        assert request.headers["authorization"] == "Bearer candidate-key"
        assert request.headers["accept"] == "application/json"
        return httpx.Response(200, json={"object": "list", "data": [{"id": model_id, "owned_by": "deepseek"} for model_id in eligible + excluded + eligible]})

    clients = _mock_client(monkeypatch, respond)
    models = providers.list_deepseek_models(api_key=" candidate-key ")

    assert [(model.id, model.name) for model in models] == [
        ("deepseek-flash", "deepseek-flash"),
        ("deepseek-v5-flash", "deepseek-v5-flash"),
        ("deepseek-v5-pro", "deepseek-v5-pro"),
        ("deepseek-v4.2-flash", "deepseek-v4.2-flash"),
        ("deepseek-v4.1-flash", "deepseek-v4.1-flash"),
        ("deepseek-v4-pro", "deepseek-v4-pro"),
    ]
    assert all(client.is_closed for client in clients)
    with pytest.raises(AttributeError):
        models[0].name = "changed"


@pytest.mark.parametrize("model_ids", [[], ["deepseek-v4-flash", "deepseek-v3.9-pro", "deepseek-coder"]])
def test_deepseek_empty_eligible_set_is_success(monkeypatch, model_ids):
    _mock_client(monkeypatch, lambda request: httpx.Response(200, json={"object": "list", "data": [{"id": model_id} for model_id in model_ids]}))
    assert providers.list_deepseek_models(api_key="candidate-key") == ()


@pytest.mark.parametrize("status,reason", [(401, "authentication"), (403, "access_denied"), (429, "rate_limited"), (503, "network"), (302, "network")])
def test_deepseek_model_http_errors_are_safe_and_close_client(monkeypatch, status, reason):
    clients = _mock_client(monkeypatch, lambda request: httpx.Response(status, text="private-upstream candidate-key", headers={"location": "https://untrusted.example/models"}))
    with pytest.raises(ConfigurationError) as caught:
        providers.list_deepseek_models(api_key="candidate-key")
    assert caught.value.context == {"provider": "deepseek", "reason": reason}
    assert "private-upstream" not in str(caught.value)
    assert "candidate-key" not in str(caught.value)
    assert all(client.is_closed for client in clients)


@pytest.mark.parametrize("exception_type,reason", [(httpx.ReadTimeout, "timeout"), (httpx.ConnectError, "network")])
def test_deepseek_model_network_errors_are_classified(monkeypatch, exception_type, reason):
    def respond(request):
        raise exception_type("private-upstream candidate-key", request=request)

    clients = _mock_client(monkeypatch, respond)
    with pytest.raises(ConfigurationError) as caught:
        providers.list_deepseek_models(api_key="candidate-key")
    assert caught.value.context == {"provider": "deepseek", "reason": reason}
    assert "candidate-key" not in str(caught.value)
    assert all(client.is_closed for client in clients)


@pytest.mark.parametrize(
    "payload", [None, {}, {"object": "list", "data": None}, {"object": "list", "data": [None]}, {"object": "list", "data": [{"id": " "}]}, {"object": "list", "data": [{"id": 5}]}]
)
def test_deepseek_invalid_model_response_does_not_become_empty_success(monkeypatch, payload):
    _mock_client(monkeypatch, lambda request: httpx.Response(200, json=payload))
    with pytest.raises(ConfigurationError) as caught:
        providers.list_deepseek_models(api_key="candidate-key")
    assert caught.value.context == {"provider": "deepseek", "reason": "malformed_response"}


def test_deepseek_oversized_model_response_is_rejected(monkeypatch):
    clients = _mock_client(monkeypatch, lambda request: httpx.Response(200, content=b"x" * (1024 * 1024 + 1)))
    with pytest.raises(ConfigurationError) as caught:
        providers.list_deepseek_models(api_key="candidate-key")
    assert caught.value.context == {"provider": "deepseek", "reason": "malformed_response"}
    assert all(client.is_closed for client in clients)


@pytest.mark.parametrize("api_key", ["", " ", "replace-with-key", "line\nbreak"])
def test_deepseek_invalid_key_fails_before_network(monkeypatch, api_key):
    clients = _mock_client(monkeypatch, lambda request: pytest.fail("Invalid key must not contact DeepSeek"))
    with pytest.raises(ConfigurationError) as caught:
        providers.list_deepseek_models(api_key=api_key)
    assert caught.value.context == {"provider": "deepseek", "reason": "invalid_candidate"}
    assert clients == []


@pytest.mark.parametrize("factory_name", ["build_model_provider_session", "prepare_model_provider_session", "refresh_model_provider_session"])
def test_deepseek_session_factories_use_saved_responses_configuration_without_discovery(tmp_path, monkeypatch, factory_name):
    save_deepseek_provider(model="deepseek-flash", api_key="saved-key", user_config_root=tmp_path)
    settings = refresh_provider_settings(Settings(), tmp_path)
    clients = _mock_client(monkeypatch, lambda request: pytest.fail("Readiness and construction must not call model discovery"))
    session = getattr(providers, factory_name)(settings)
    assert session.provider == "deepseek"
    assert session.model == "deepseek-flash"
    assert session.client_config.api_key == "saved-key"
    assert session.client_config.base_url == "https://api.deepseek.com/"
    assert session.client_config.backend == "openai_responses"
    assert session.client_config.default_headers == {}
    assert session.metadata == {"endpoint_family": "deepseek"}
    assert "saved-key" not in repr(session.client_config)
    session.close_sync()
    assert clients == []
