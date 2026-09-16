# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from fsq_agent.adapters.cli import main
from fsq_agent.application import ApplicationError, configure_deepseek
from fsq_agent.config import activate_github_copilot_provider, load_user_provider_config, save_azure_openai_provider, save_deepseek_provider, save_google_gemini_provider, save_openai_provider
from fsq_agent.control_plane import ControlPlaneServer, ControlPlaneServerOptions
from fsq_agent.models import ConfigurationError
from fsq_agent.providers import DeepSeekModel


@pytest.fixture
def discovery(monkeypatch):
    calls = []

    def discover(*, api_key):
        calls.append(api_key)
        return (DeepSeekModel(id="deepseek-flash", name="deepseek-flash"), DeepSeekModel(id="deepseek-v4-pro", name="deepseek-v4-pro"))

    monkeypatch.setattr("fsq_agent.application.providers._discover_deepseek_models", discover)
    return calls


def test_deepseek_http_and_cli_share_saved_configuration(tmp_path, monkeypatch, discovery):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    server = ControlPlaneServer(ControlPlaneServerOptions(user_config_root=tmp_path / ".fsq", static_path=tmp_path / "static"))

    status, models = server.handle_post("/api/control-plane/config/deepseek/models", {"apiKey": "candidate-key"})
    assert status == 200
    assert models == {"models": [{"id": "deepseek-flash", "name": "deepseek-flash"}, {"id": "deepseek-v4-pro", "name": "deepseek-v4-pro"}]}

    status, saved = server.handle_put("/api/control-plane/config/deepseek", {"apiKey": "candidate-key", "modelName": "deepseek-flash"})
    assert status == 200
    assert saved["provider"] == {"type": "deepseek", "apiKey": "candidate-key", "modelName": "deepseek-flash"}

    result = CliRunner().invoke(main, ["--output", "json", "--non-interactive", "providers", "configure", "deepseek", "--model", "deepseek-v4-pro", "--api-key", "cli-key"])
    assert result.exit_code == 0, result.output
    assert "cli-key" not in result.output
    assert load_user_provider_config(tmp_path / ".fsq").api_key == "cli-key"
    assert discovery == ["candidate-key", "candidate-key", "cli-key"]


def test_deepseek_application_revalidates_exact_model_membership(tmp_path, monkeypatch):
    monkeypatch.setattr("fsq_agent.application.providers._discover_deepseek_models", lambda **kwargs: (DeepSeekModel(id="deepseek-flash", name="deepseek-flash"),))
    with pytest.raises(ApplicationError) as caught:
        configure_deepseek(model="deepseek-v4-pro", api_key="candidate-key", user_config_root=tmp_path)
    assert caught.value.details == {"provider": "deepseek", "reason": "model_not_offered"}
    assert load_user_provider_config(tmp_path).provider is None


@pytest.mark.parametrize(
    "reason,status",
    [("invalid_candidate", 400), ("authentication", 401), ("access_denied", 403), ("rate_limited", 429), ("timeout", 504), ("network", 503), ("malformed_response", 502), ("internal", 500)],
)
def test_deepseek_errors_preserve_previous_provider_and_hide_details(tmp_path, monkeypatch, reason, status):
    def fail(**kwargs):
        raise ConfigurationError("secret raw detail", context={"provider": "deepseek", "reason": reason})

    monkeypatch.setattr("fsq_agent.application.providers._discover_deepseek_models", fail)
    save_openai_provider(model="gpt-5", api_key="old-key", user_config_root=tmp_path)
    server = ControlPlaneServer(ControlPlaneServerOptions(user_config_root=tmp_path))
    actual, body = server.handle_put("/api/control-plane/config/deepseek", {"apiKey": "secret", "modelName": "deepseek-flash"})
    assert actual == status
    assert "secret" not in str(body)
    assert "raw detail" not in str(body)
    assert load_user_provider_config(tmp_path).provider.type == "openai"


def test_deepseek_routes_reject_nonlocal_and_cross_origin(tmp_path, discovery):
    server = ControlPlaneServer(ControlPlaneServerOptions(user_config_root=tmp_path))
    path = "/api/control-plane/config/deepseek/models"
    body = {"apiKey": "candidate-key"}
    assert server.handle_post(path, body, peer_host="192.0.2.1")[0] == 403
    assert server.handle_post(path, body, origin="https://untrusted.test", host="127.0.0.1:8879")[0] == 403
    assert discovery == []


def test_deepseek_and_openai_replacement_removes_inactive_credentials(tmp_path):
    save_openai_provider(model="gpt-5", api_key="openai-key", user_config_root=tmp_path)
    save_deepseek_provider(model="deepseek-flash", api_key="deepseek-key", user_config_root=tmp_path)
    assert not (tmp_path / "auth" / "openai.json").exists()
    assert json.loads((tmp_path / "auth" / "deepseek.json").read_text(encoding="utf-8")) == {"api_key": "deepseek-key"}

    save_openai_provider(model="gpt-5", api_key="new-openai-key", user_config_root=tmp_path)
    assert not (tmp_path / "auth" / "deepseek.json").exists()
    assert load_user_provider_config(tmp_path).provider.type == "openai"


@pytest.mark.parametrize("replacement", ["azure_openai", "google_gemini", "github_copilot"])
def test_replacing_deepseek_removes_its_credentials(tmp_path, replacement):
    save_deepseek_provider(model="deepseek-flash", api_key="deepseek-key", user_config_root=tmp_path)
    if replacement == "azure_openai":
        save_azure_openai_provider(base_url="https://example.openai.azure.com", model="deployment", api_key="azure-key", user_config_root=tmp_path)
    elif replacement == "google_gemini":
        save_google_gemini_provider(model="gemini-3.8-flash", api_key="gemini-key", user_config_root=tmp_path)
    else:
        activate_github_copilot_provider(model="gpt-5", github_token={"access_token": "oauth"}, provider_token={"token": "token", "plan": "individual"}, user_config_root=tmp_path)
    assert not (tmp_path / "auth" / "deepseek.json").exists()
    assert load_user_provider_config(tmp_path).provider.type == replacement


@pytest.mark.parametrize("rollback_succeeds", [True, False])
def test_deepseek_failed_transaction_preserves_previous_provider(tmp_path, monkeypatch, rollback_succeeds):
    from fsq_agent.config import _user_provider

    original = save_openai_provider(model="gpt-5", api_key="old-key", user_config_root=tmp_path)
    original_stage = _user_provider._stage_write
    attempts = 0

    def fail_candidate_once(path, payload):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise OSError("synthetic storage failure")
        return original_stage(path, payload)

    monkeypatch.setattr(_user_provider, "_stage_write", fail_candidate_once)
    if not rollback_succeeds:
        monkeypatch.setattr(_user_provider, "_restore_snapshots", lambda snapshots: False)
    with pytest.raises(ConfigurationError) as caught:
        save_deepseek_provider(model="deepseek-flash", api_key="candidate-key", user_config_root=tmp_path)
    assert caught.value.context == {"provider": "deepseek", "reason": "storage" if rollback_succeeds else "internal"}
    loaded = load_user_provider_config(tmp_path)
    assert loaded.provider == original.provider
    assert loaded.api_key == "old-key"
    assert "candidate-key" not in str(caught.value)


def test_deepseek_cli_status_uses_saved_configuration_without_discovery(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    save_deepseek_provider(model="deepseek-flash", api_key="hidden-key", user_config_root=tmp_path / ".fsq")
    monkeypatch.setattr("fsq_agent.application.providers._discover_deepseek_models", lambda **kwargs: pytest.fail("Status must not discover models"))
    result = CliRunner().invoke(main, ["--output", "json", "providers", "status"])
    assert result.exit_code == 0, result.output
    record = json.loads(result.output)
    assert record["result"]["provider"] == "deepseek"
    assert record["result"]["model"] == "deepseek-flash"
    assert record["result"]["status"] == "ready"
    assert "hidden-key" not in result.output


@pytest.mark.parametrize("output", ["human", "json", "jsonl"])
def test_deepseek_cli_modes_are_safe_and_explicit(tmp_path, monkeypatch, discovery, output):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    arguments = ["--output", output, "providers", "configure", "deepseek"]
    if output != "human":
        arguments += ["--model", "deepseek-flash", "--api-key", "hidden-deepseek-key"]
    result = CliRunner().invoke(main, arguments, input="hidden-deepseek-key\n1\n")
    assert result.exit_code == 0, result.output
    assert "hidden-deepseek-key" not in result.output
    assert "deepseek" in result.output.casefold()
    if output == "human":
        assert "DeepSeek API key" in result.output
        assert "Select model" in result.output
        assert len(discovery) == 2
    else:
        assert len(discovery) == 1
        record = json.loads(result.output)
        assert record["result"]["provider"] == "deepseek"
        assert record["result"]["model"] == "deepseek-flash"
        assert record["result"]["configured"] is True
        assert record["result"]["status"] == "success"
