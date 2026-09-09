# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import asyncio
import gzip
import json

import httpx
import pytest

from fsq_agent import providers
from fsq_agent.config import Settings, load_user_provider_config, refresh_provider_settings, save_azure_openai_provider, save_google_gemini_provider, save_openai_provider
from fsq_agent.models import ConfigurationError


def _model(name):
    return {"name": f"models/{name}", "displayName": name, "supportedGenerationMethods": ["generateContent"]}


def _transport(monkeypatch, handler):
    from fsq_agent.providers import _google_gemini

    original = httpx.AsyncClient
    monkeypatch.setattr(_google_gemini.httpx, "AsyncClient", lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs))


def test_gemini_discovers_all_pages_and_filters_stable_general_models(monkeypatch):
    requests = []

    def respond(request):
        requests.append(request)
        assert request.headers["x-goog-api-key"] == "candidate-key"
        assert "key" not in request.url.params
        if "pageToken" not in request.url.params:
            return httpx.Response(
                200, json={"models": [_model(name) for name in ("gemini-2.5-pro", "gemini-3.1-pro-preview", "gemini-3.8-flash-lite", "gemini-3.8-flash-image")], "nextPageToken": "next"}
            )
        return httpx.Response(200, json={"models": [_model(name) for name in ("gemini-3.8-flash", "gemini-3.10-pro", "gemini-3.8-flash", "gemini-3-pro")]})

    _transport(monkeypatch, respond)
    models = providers.list_google_gemini_models(api_key=" candidate-key ")
    assert [model.id for model in models] == ["gemini-3.10-pro", "gemini-3.8-flash", "gemini-3-pro"]
    assert len(requests) == 2
    assert all(request.url.params["pageSize"] == "1000" for request in requests)


@pytest.mark.parametrize("status,reason", [(401, "authentication"), (403, "access_denied"), (429, "rate_limited"), (503, "network")])
def test_gemini_later_page_failure_does_not_return_partial_models(monkeypatch, status, reason):
    def respond(request):
        if "pageToken" not in request.url.params:
            return httpx.Response(200, json={"models": [_model("gemini-3.8-flash")], "nextPageToken": "next"})
        return httpx.Response(status, json={"error": {"message": "secret-key private-detail"}})

    _transport(monkeypatch, respond)
    with pytest.raises(ConfigurationError) as failure:
        providers.list_google_gemini_models(api_key="secret-key")
    assert failure.value.context == {"provider": "google_gemini", "reason": reason}
    assert "secret-key" not in str(failure.value)
    assert "private-detail" not in str(failure.value)


def test_gemini_repeated_token_is_failure(monkeypatch):
    _transport(monkeypatch, lambda request: httpx.Response(200, json={"models": [], "nextPageToken": "loop"}))
    with pytest.raises(ConfigurationError) as failure:
        providers.list_google_gemini_models(api_key="candidate-key")
    assert failure.value.context["reason"] == "malformed_response"


@pytest.mark.parametrize("replacement", ["openai", "azure_openai"])
def test_gemini_saved_snapshot_and_reverse_replacement(tmp_path, replacement):
    save_openai_provider(model="gpt-5", api_key="old-key", user_config_root=tmp_path)
    saved = save_google_gemini_provider(model=" gemini-3.8-flash ", api_key=" google-key ", user_config_root=tmp_path)
    assert saved.provider.type == "google_gemini"
    assert not (tmp_path / "auth/openai.json").exists()
    assert json.loads((tmp_path / "auth/google-gemini.json").read_text())["api_key"] == "google-key"
    assert "google-key" not in saved.model_dump_json()
    settings = refresh_provider_settings(Settings(), tmp_path)
    assert settings.agent_runtime.base_url == "https://generativelanguage.googleapis.com/v1beta/"
    assert settings.agent_runtime.api_key == "google-key"
    if replacement == "openai":
        save_openai_provider(model="gpt-5", api_key="new-key", user_config_root=tmp_path)
    else:
        save_azure_openai_provider(base_url="https://example.openai.azure.com", model="deployment", api_key="new-key", user_config_root=tmp_path)
    assert not (tmp_path / "auth/google-gemini.json").exists()
    assert load_user_provider_config(tmp_path).provider.type == replacement


@pytest.mark.parametrize("failure,reason", [("timeout", "timeout"), ("network", "network"), ("malformed", "malformed_response")])
def test_later_page_transport_failure_is_not_empty_success(monkeypatch, failure, reason):
    def respond(request):
        if "pageToken" not in request.url.params:
            return httpx.Response(200, json={"models": [], "nextPageToken": "next"})
        if failure == "timeout":
            raise httpx.ReadTimeout("private", request=request)
        if failure == "network":
            raise httpx.ConnectError("private", request=request)
        return httpx.Response(200, content=b"not-json")

    _transport(monkeypatch, respond)
    with pytest.raises(ConfigurationError) as caught:
        providers.list_google_gemini_models(api_key="key")
    assert caught.value.context["reason"] == reason


def test_empty_page_with_continuation_is_not_final(monkeypatch):
    calls = []

    def respond(request):
        calls.append(request)
        return httpx.Response(200, json={"models": [], "nextPageToken": "next"} if len(calls) == 1 else {"models": [_model("gemini-3.8-flash")]})

    _transport(monkeypatch, respond)
    assert len(providers.list_google_gemini_models(api_key="key")) == 1
    assert len(calls) == 2


def test_discovery_page_and_decoded_byte_budgets(monkeypatch):
    calls = []

    def respond(request):
        calls.append(request)
        return httpx.Response(200, json={"models": [_model("gemini-3.8-flash")], "nextPageToken": str(len(calls))})

    _transport(monkeypatch, respond)
    with pytest.raises(ConfigurationError) as caught:
        providers.list_google_gemini_models(api_key="key")
    assert len(calls) == 20
    assert caught.value.context["reason"] == "malformed_response"


def test_discovery_decoded_bytes_are_cumulative_across_pages(monkeypatch):
    from fsq_agent.providers import _google_gemini

    assert _google_gemini._MAX_BYTES == 4 * 1024 * 1024
    calls = []
    first = json.dumps({"models": [_model("gemini-3.8-flash")], "padding": "x" * (3 * 1024 * 1024), "nextPageToken": "next"}).encode()
    last = json.dumps({"models": [_model("gemini-3.7-flash")], "padding": "x" * (3 * 1024 * 1024)}).encode()
    assert max(len(first), len(last)) < _google_gemini._MAX_BYTES

    def respond(request):
        calls.append(request)
        payload = last if "pageToken" in request.url.params else first
        return httpx.Response(200, headers={"content-encoding": "gzip"}, content=gzip.compress(payload))

    _transport(monkeypatch, respond)
    with pytest.raises(ConfigurationError) as caught:
        providers.list_google_gemini_models(api_key="key")
    assert caught.value.context["reason"] == "malformed_response"
    assert len(calls) == 2
    calls.clear()
    monkeypatch.setattr(_google_gemini, "_MAX_BYTES", 8 * 1024 * 1024)
    assert [model.id for model in providers.list_google_gemini_models(api_key="key")] == ["gemini-3.8-flash", "gemini-3.7-flash"]
    assert len(calls) == 2


def test_discovery_overall_deadline_cancels_pending_transport(monkeypatch):
    from fsq_agent.providers import _google_gemini

    assert _google_gemini._DEADLINE_SECONDS == 60
    monkeypatch.setattr(_google_gemini, "_DEADLINE_SECONDS", 0.02)
    cancelled = []

    async def respond(request):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.append(True)

    _transport(monkeypatch, respond)
    with pytest.raises(ConfigurationError) as caught:
        providers.list_google_gemini_models(api_key="key")
    assert caught.value.context["reason"] == "timeout"
    assert cancelled == [True]


def test_gemini_and_github_replacements_preserve_frozen_readiness(tmp_path):
    from fsq_agent.adapters.control_plane._readiness import provider_readiness
    from fsq_agent.config import activate_github_copilot_provider

    activate_github_copilot_provider(model="gpt-5", github_token={"access_token": "oauth"}, provider_token={"token": "token", "plan": "individual"}, user_config_root=tmp_path)
    save_google_gemini_provider(model="gemini-3.8-flash", api_key="key", user_config_root=tmp_path)
    frozen = refresh_provider_settings(Settings(), tmp_path)
    assert provider_readiness(frozen)["status"] == "ready"
    assert not (tmp_path / "auth/github-copilot-token.json").exists()
    assert not (tmp_path / "auth/github-copilot-provider-token.json").exists()
    activate_github_copilot_provider(model="gpt-5", github_token={"access_token": "oauth"}, provider_token={"token": "token", "plan": "individual"}, user_config_root=tmp_path)
    assert not (tmp_path / "auth/google-gemini.json").exists()
    assert frozen.agent_runtime.provider == "google_gemini"
    assert provider_readiness(frozen)["status"] == "ready"
    session = providers.build_model_provider_session(frozen)
    assert session.provider == "google_gemini"
    assert session.model == "gemini-3.8-flash"
    session.close_sync()
