# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from fsq_agent.adapters.cli import main
from fsq_agent.config import load_user_provider_config, save_openai_provider
from fsq_agent.control_plane import ControlPlaneServer, ControlPlaneServerOptions
from fsq_agent.models import ConfigurationError
from fsq_agent.providers import GoogleGeminiModel


@pytest.fixture
def discovery(monkeypatch):
    calls = []

    def discover(*, api_key):
        calls.append(api_key)
        return (GoogleGeminiModel(id="gemini-3.8-flash", name="Gemini Flash"),)

    monkeypatch.setattr("fsq_agent.application.providers._discover_google_gemini_models", discover)
    return calls


def test_gemini_http_and_cli_share_saved_configuration(tmp_path, monkeypatch, discovery):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    server = ControlPlaneServer(ControlPlaneServerOptions(user_config_root=tmp_path / ".fsq", static_path=tmp_path / "static"))
    status, models = server.handle_post("/api/control-plane/config/google-gemini/models", {"apiKey": "candidate-key"})
    assert status == 200
    assert models == {"models": [{"id": "gemini-3.8-flash", "name": "Gemini Flash"}]}
    status, saved = server.handle_put("/api/control-plane/config/google-gemini", {"apiKey": "candidate-key", "modelName": "gemini-3.8-flash"})
    assert status == 200
    assert saved["provider"] == {"type": "google_gemini", "apiKey": "candidate-key", "modelName": "gemini-3.8-flash"}
    result = CliRunner().invoke(main, ["--output", "json", "--non-interactive", "providers", "configure", "google_gemini", "--model", "gemini-3.8-flash", "--api-key", "cli-key"])
    assert result.exit_code == 0, result.output
    assert "cli-key" not in result.output
    assert load_user_provider_config(tmp_path / ".fsq").api_key == "cli-key"
    assert discovery == ["candidate-key", "candidate-key", "cli-key"]


@pytest.mark.parametrize(
    "reason,status",
    [("invalid_candidate", 400), ("authentication", 401), ("access_denied", 403), ("rate_limited", 429), ("timeout", 504), ("network", 503), ("malformed_response", 502), ("internal", 500)],
)
def test_gemini_errors_preserve_previous_provider_and_hide_details(tmp_path, monkeypatch, reason, status):
    def fail(**kwargs):
        raise ConfigurationError("secret raw detail", context={"provider": "google_gemini", "reason": reason})

    monkeypatch.setattr("fsq_agent.application.providers._discover_google_gemini_models", fail)
    save_openai_provider(model="gpt-5", api_key="old-key", user_config_root=tmp_path)
    server = ControlPlaneServer(ControlPlaneServerOptions(user_config_root=tmp_path))
    actual, body = server.handle_put("/api/control-plane/config/google-gemini", {"apiKey": "secret", "modelName": "gemini-3.8-flash"})
    assert actual == status
    assert "secret" not in str(body)
    assert "raw detail" not in str(body)
    assert load_user_provider_config(tmp_path).provider.type == "openai"


def test_gemini_routes_reject_nonlocal_and_cross_origin(tmp_path, discovery):
    server = ControlPlaneServer(ControlPlaneServerOptions(user_config_root=tmp_path))
    path = "/api/control-plane/config/google-gemini/models"
    body = {"apiKey": "candidate-key"}
    assert server.handle_post(path, body, peer_host="192.0.2.1")[0] == 403
    assert server.handle_post(path, body, origin="https://untrusted.test", host="127.0.0.1:8879")[0] == 403
    assert discovery == []


@pytest.mark.parametrize("output", ["human", "json", "jsonl"])
def test_gemini_cli_modes_are_safe_and_explicit(tmp_path, monkeypatch, discovery, output):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    arguments = ["--output", output, "providers", "configure", "google_gemini"]
    if output != "human":
        arguments += ["--model", "gemini-3.8-flash", "--api-key", "hidden-google-key"]
    result = CliRunner().invoke(main, arguments, input="hidden-google-key\n1\n")
    assert result.exit_code == 0, result.output
    assert "hidden-google-key" not in result.output
    assert "google_gemini" in result.output
    if output == "human":
        assert "Google Gemini API key" in result.output
        assert "Select model" in result.output
        assert len(discovery) == 2
    else:
        assert len(discovery) == 1
        records = [json.loads(line) for line in result.output.splitlines()]
        assert len(records) == 1
        record = records[0]
        assert record["schema_version"] == "fsq.machine/v1"
        assert record["type"] == "result"
        assert record["operation"] == "providers.configure"
        assert record["status"] == "success"
        assert record["result"]["provider"] == "google_gemini"
        assert record["result"]["model"] == "gemini-3.8-flash"


@pytest.mark.parametrize("options", [["--output", "json"], ["--output", "jsonl"], ["--non-interactive"]])
@pytest.mark.parametrize("provided", [[], ["--model", "gemini-3.8-flash"], ["--api-key", "key"]])
def test_gemini_cli_missing_machine_options_do_not_prompt(tmp_path, monkeypatch, discovery, options, provided):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    result = CliRunner().invoke(main, [*options, "providers", "configure", "google_gemini", *provided])
    assert result.exit_code == 2
    assert discovery == []


def test_gemini_cli_rejects_base_url_before_discovery(discovery):
    result = CliRunner().invoke(main, ["providers", "configure", "google_gemini", "--base-url", "https://untrusted.test"])
    assert result.exit_code == 2
    assert discovery == []


@pytest.mark.parametrize("mode,exit_code", [("empty", 4), ("network", 4), ("selection", 3)])
def test_gemini_cli_failed_discovery_or_selection_preserves_provider(tmp_path, monkeypatch, mode, exit_code):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    save_openai_provider(model="gpt-5", api_key="old-key", user_config_root=tmp_path / ".fsq")

    def discover(**kwargs):
        if mode == "network":
            raise ConfigurationError("hidden-google-key", context={"provider": "google_gemini", "reason": "network"})
        return ()

    monkeypatch.setattr("fsq_agent.application.providers._discover_google_gemini_models", discover)
    arguments = ["providers", "configure", "google_gemini"]
    if mode == "selection":
        arguments += ["--model", "gemini-3.8-flash", "--api-key", "hidden-google-key"]
    result = CliRunner().invoke(main, arguments, input="hidden-google-key\n")
    assert result.exit_code == exit_code
    assert "hidden-google-key" not in result.output
    assert "Select model" not in result.output
    assert load_user_provider_config(tmp_path / ".fsq").provider.type == "openai"


@pytest.mark.parametrize("output", ["human", "json", "jsonl"])
def test_gemini_status_does_not_discover_or_infer(tmp_path, monkeypatch, output):
    from fsq_agent.config import save_google_gemini_provider

    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    save_google_gemini_provider(model="gemini-3.8-flash", api_key="hidden-key", user_config_root=tmp_path / ".fsq")

    def forbidden(**kwargs):
        pytest.fail("Readiness must not discover or infer")

    monkeypatch.setattr("fsq_agent.application.providers._discover_google_gemini_models", forbidden)
    monkeypatch.setattr("fsq_agent.providers._session.create_google_gemini_model_provider", forbidden)
    result = CliRunner().invoke(main, ["--output", output, "providers", "status"])
    assert result.exit_code == 0, result.output
    assert "google_gemini" in result.output
    assert "hidden-key" not in result.output
    if output != "human":
        records = [json.loads(line) for line in result.output.splitlines()]
        assert len(records) == 1
        record = records[0]
        assert record["schema_version"] == "fsq.machine/v1"
        assert record["type"] == "result"
        assert record["operation"] == "providers.status"
        assert record["status"] == "ready"
        assert record["result"]["provider"] == "google_gemini"
        assert record["result"]["configured"] is True
        assert record["result"]["model"] == "gemini-3.8-flash"


@pytest.mark.parametrize("output", ["json", "jsonl"])
@pytest.mark.parametrize(
    "reason,exit_code,code,category",
    [
        ("model_not_offered", 3, "configuration.invalid", "configuration"),
        ("network", 4, "provider.unavailable", "unavailable"),
        ("malformed_response", 4, "provider.unavailable", "unavailable"),
        ("storage", 3, "configuration.invalid", "configuration"),
        ("internal", 5, "internal.error", "internal"),
    ],
)
def test_gemini_machine_failures_emit_one_safe_terminal_record(tmp_path, monkeypatch, output, reason, exit_code, code, category):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    save_openai_provider(model="gpt-5", api_key="old-key", user_config_root=tmp_path / ".fsq")

    def discover(**kwargs):
        if reason == "model_not_offered":
            return ()
        raise ConfigurationError("hidden-google-key private-upstream", context={"provider": "google_gemini", "reason": reason, "raw": "private-upstream"})

    monkeypatch.setattr("fsq_agent.application.providers._discover_google_gemini_models", discover)
    result = CliRunner().invoke(main, ["--output", output, "--non-interactive", "providers", "configure", "google_gemini", "--model", "gemini-3.8-flash", "--api-key", "hidden-google-key"])
    assert result.exit_code == exit_code
    assert result.stderr == ""
    records = [json.loads(line) for line in result.stdout.splitlines()]
    assert len(records) == 1
    record = records[0]
    assert record["schema_version"] == "fsq.machine/v1"
    assert record["type"] == "error"
    assert record["operation"] == "providers.configure"
    assert record["status"] == "error"
    assert record["error"]["code"] == code
    assert record["error"]["category"] == category
    assert record["error"]["details"] == {"provider": "google_gemini", "reason": reason}
    assert record["error"]["message"]
    assert record["error"]["action"]
    assert "hidden-google-key" not in result.output
    assert "private-upstream" not in result.output
    assert "Select model" not in result.output
    assert load_user_provider_config(tmp_path / ".fsq").provider.type == "openai"
