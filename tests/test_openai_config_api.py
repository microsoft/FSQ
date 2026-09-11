# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import pytest

from fsq_agent import providers
from fsq_agent.config import load_user_provider_config, save_azure_openai_provider
from fsq_agent.control_plane import ControlPlaneServer, ControlPlaneServerOptions
from fsq_agent.models import ConfigurationError


@pytest.fixture
def server(tmp_path):
    return ControlPlaneServer(ControlPlaneServerOptions(host="127.0.0.1", static_path=tmp_path / "static", user_config_root=tmp_path / "user", open_browser=False))


def _discover(monkeypatch, models):
    calls = []

    def discover(*, api_key):
        calls.append(api_key)
        return tuple(providers.OpenAIModel(id=model, name=model) for model in models)

    monkeypatch.setattr("fsq_agent.application.providers._discover_openai_models", discover)
    return calls


def test_openai_discovery_save_and_config_use_shared_application(server, tmp_path, monkeypatch):
    calls = _discover(monkeypatch, ["gpt-5", "gpt-5.4"])
    status, discovered = server.handle_post("/api/control-plane/config/openai/models", {"apiKey": "candidate-key"})
    assert status == 200
    assert discovered == {"models": [{"id": model, "name": model} for model in ("gpt-5", "gpt-5.4")]}
    assert load_user_provider_config(tmp_path / "user").provider is None
    status, saved = server.handle_put("/api/control-plane/config/openai", {"apiKey": "candidate-key", "modelName": "gpt-5.4"})
    assert status == 200
    assert saved == {"configured": True, "provider": {"type": "openai", "modelName": "gpt-5.4", "apiKey": "candidate-key"}}
    read_status, current, headers = server.handle_get("/api/control-plane/config")
    assert read_status == 200
    assert current == saved
    assert headers["Cache-Control"] == "no-store"
    assert calls == ["candidate-key", "candidate-key"]


def test_openai_empty_discovery_succeeds_but_cannot_replace_provider(server, tmp_path, monkeypatch):
    _discover(monkeypatch, [])
    save_azure_openai_provider(base_url="https://example.openai.azure.com", model="deployment", api_key="old-key", user_config_root=tmp_path / "user")
    status, result = server.handle_post("/api/control-plane/config/openai/models", {"apiKey": "candidate-key"})
    assert status == 200
    assert result == {"models": []}
    status, result = server.handle_put("/api/control-plane/config/openai", {"apiKey": "candidate-key", "modelName": "gpt-5"})
    assert status == 400
    assert result["code"] == "provider_model_not_offered"
    saved = load_user_provider_config(tmp_path / "user")
    assert saved.provider.type == "azure_openai"
    assert saved.api_key == "old-key"


@pytest.mark.parametrize(
    "reason,status,code",
    [
        ("invalid_candidate", 400, "invalid_provider_config"),
        ("authentication", 401, "provider_authorization_failed"),
        ("access_denied", 403, "provider_access_denied"),
        ("rate_limited", 429, "provider_rate_limited"),
        ("timeout", 504, "provider_timeout"),
        ("network", 503, "provider_unavailable"),
        ("malformed_response", 502, "provider_response_invalid"),
        ("unexpected_reason", 500, "config_internal_error"),
    ],
)
@pytest.mark.parametrize("operation", ["models", "save"])
def test_openai_failure_facts_cross_application_and_http_safely(server, monkeypatch, reason, status, code, operation):
    def fail(**kwargs):
        raise ConfigurationError("private-upstream candidate-key", context={"provider": "openai", "reason": reason, "raw": "candidate-key"})

    monkeypatch.setattr("fsq_agent.application.providers._discover_openai_models", fail)
    if operation == "models":
        actual_status, error = server.handle_post("/api/control-plane/config/openai/models", {"apiKey": "candidate-key"})
    else:
        actual_status, error = server.handle_put("/api/control-plane/config/openai", {"apiKey": "candidate-key", "modelName": "gpt-5"})
    assert actual_status == status
    assert error["code"] == code
    assert error["message"]
    assert error["action"]
    assert "candidate-key" not in str(error)
    assert "private-upstream" not in str(error)


@pytest.mark.parametrize("body", [{}, {"apiKey": ""}, {"apiKey": 42}, {"apiKey": "candidate-key", "baseUrl": "https://untrusted.example"}])
def test_openai_discovery_rejects_invalid_body_before_provider(server, monkeypatch, body):
    calls = _discover(monkeypatch, ["gpt-5"])
    status, _error = server.handle_post("/api/control-plane/config/openai/models", body)
    assert status == 400
    assert calls == []


@pytest.mark.parametrize("entry", ["config", "application", "http", "cli"])
def test_openai_lock_entry_failure_is_safe_storage_failure(tmp_path, monkeypatch, entry):
    from pathlib import Path

    from click.testing import CliRunner

    from fsq_agent.adapters.cli import main
    from fsq_agent.application import ApplicationError, configure_openai
    from fsq_agent.config import _user_provider, save_openai_provider

    user_root = tmp_path / ".fsq"
    original = save_azure_openai_provider(base_url="https://example.openai.azure.com", model="deployment", api_key="old-key", user_config_root=user_root)
    _discover(monkeypatch, ["gpt-5"])

    def fail_lock(lock_file):
        raise OSError("private-path candidate-key")

    with monkeypatch.context() as fault:
        fault.setattr(_user_provider, "_acquire_process_lock", fail_lock)
        if entry == "config":
            with pytest.raises(ConfigurationError) as caught:
                save_openai_provider(model="gpt-5", api_key="candidate-key", user_config_root=user_root)
            assert caught.value.context == {"provider": "openai", "reason": "storage"}
            output = str(caught.value)
        elif entry == "application":
            with pytest.raises(ApplicationError) as caught:
                configure_openai(model="gpt-5", api_key="candidate-key", user_config_root=user_root)
            assert caught.value.category.value == "configuration"
            assert caught.value.details == {"provider": "openai", "reason": "storage"}
            output = str(caught.value.to_record())
        elif entry == "http":
            server = ControlPlaneServer(ControlPlaneServerOptions(user_config_root=user_root))
            status, error = server.handle_put("/api/control-plane/config/openai", {"modelName": "gpt-5", "apiKey": "candidate-key"})
            assert status == 503
            assert error["code"] == "provider_storage_unavailable"
            output = str(error)
        else:
            fault.setattr(Path, "home", classmethod(lambda cls: tmp_path))
            result = CliRunner().invoke(main, ["--output", "json", "--non-interactive", "providers", "configure", "openai", "--model", "gpt-5", "--api-key", "candidate-key"])
            assert result.exit_code == 3
            output = result.output
        assert "candidate-key" not in output
        assert "private-path" not in output
    current = load_user_provider_config(user_root)
    assert current.provider == original.provider
    assert current.api_key == "old-key"


def test_openai_post_commit_unlock_failure_remains_internal(tmp_path, monkeypatch):
    from fsq_agent.application import ApplicationError, configure_openai
    from fsq_agent.config import _user_provider

    _discover(monkeypatch, ["gpt-5"])
    release = _user_provider._release_process_lock

    def fail_after_unlock(lock_file):
        release(lock_file)
        raise OSError("private-path candidate-key")

    with monkeypatch.context() as fault:
        fault.setattr(_user_provider, "_release_process_lock", fail_after_unlock)
        with pytest.raises(ApplicationError) as caught:
            configure_openai(model="gpt-5", api_key="candidate-key", user_config_root=tmp_path)
        assert caught.value.details == {"provider": "openai", "reason": "internal"}
        assert "candidate-key" not in str(caught.value.to_record())
    assert load_user_provider_config(tmp_path).provider.type == "openai"


@pytest.mark.parametrize("operation", ["models", "save"])
def test_openai_routes_enforce_local_same_origin_access(server, monkeypatch, operation):
    calls = _discover(monkeypatch, ["gpt-5"])
    handler = server.handle_post if operation == "models" else server.handle_put
    path = "/api/control-plane/config/openai/models" if operation == "models" else "/api/control-plane/config/openai"
    body = {"apiKey": "candidate-key"} if operation == "models" else {"apiKey": "candidate-key", "modelName": "gpt-5"}
    status, error = handler(path, body, peer_host="192.0.2.10")
    assert status == 403
    assert error["code"] == "config_unavailable"
    status, error = handler(path, body, origin="https://untrusted.example", host="127.0.0.1:8879")
    assert status == 403
    assert error["code"] == "cross_origin_forbidden"
    assert calls == []
