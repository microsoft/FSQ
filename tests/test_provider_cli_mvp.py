# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from fsq_agent.adapters.cli._main import main
from fsq_agent.application import ProviderConfigurationResult, ProviderStatusResult
from fsq_agent.application.providers import complete_github_configuration, configure_azure_openai, provider_status
from fsq_agent.config import activate_github_copilot_provider, load_user_provider_config
from fsq_agent.providers import GitHubCopilotModel


def test_azure_application_configuration_is_visible_to_ui_config_api(tmp_path: Path) -> None:
    configure_azure_openai(base_url="https://sample.openai.azure.com", model="gpt-5", api_key="top-secret", user_config_root=tmp_path)

    saved = load_user_provider_config(tmp_path)
    assert saved.provider is not None
    assert saved.provider.type == "azure_openai"
    assert saved.provider.model == "gpt-5"
    assert saved.api_key == "top-secret"


@pytest.mark.parametrize("output", ["human", "json", "jsonl"])
def test_openai_cli_non_interactive_configures_shared_user_store(tmp_path, monkeypatch, output):
    from fsq_agent.providers import OpenAIModel

    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr("fsq_agent.application.providers._discover_openai_models", lambda **kwargs: (OpenAIModel(id="gpt-5", name="gpt-5"),))
    result = CliRunner().invoke(main, ["--output", output, "--non-interactive", "providers", "configure", "openai", "--model", "gpt-5", "--api-key", "cli-secret"])
    assert result.exit_code == 0, result.output
    saved = load_user_provider_config(tmp_path / ".fsq")
    assert saved.provider.type == "openai"
    assert saved.api_key == "cli-secret"
    assert "cli-secret" not in result.output
    status = provider_status(user_config_root=tmp_path / ".fsq")
    assert status.status == "ready"
    assert status.provider == "openai"


def test_openai_cli_interactive_requires_explicit_model_choice_and_hides_key(tmp_path, monkeypatch):
    from fsq_agent.providers import OpenAIModel

    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr("fsq_agent.application.providers._discover_openai_models", lambda **kwargs: (OpenAIModel(id="gpt-5", name="gpt-5"),))
    result = CliRunner().invoke(main, ["providers", "configure", "openai"], input="cli-secret\n1\n")
    assert result.exit_code == 0, result.output
    assert "Select model" in result.output
    assert "cli-secret" not in result.output
    assert load_user_provider_config(tmp_path / ".fsq").provider.model == "gpt-5"


@pytest.mark.parametrize("options", [[], ["--model", "gpt-5"], ["--api-key", "cli-secret"], ["--base-url", "https://untrusted.example", "--model", "gpt-5", "--api-key", "cli-secret"]])
def test_openai_cli_rejects_missing_or_custom_endpoint_options(options):
    result = CliRunner().invoke(main, ["--output", "json", "--non-interactive", "providers", "configure", "openai", *options])
    assert result.exit_code == 2
    assert "cli-secret" not in result.output


def test_openai_cli_empty_models_does_not_save_or_prompt_for_selection(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr("fsq_agent.application.providers._discover_openai_models", lambda **kwargs: ())
    result = CliRunner().invoke(main, ["providers", "configure", "openai", "--api-key", "cli-secret"])
    assert result.exit_code == 4
    assert "Select model" not in result.output
    assert "cli-secret" not in result.output
    assert load_user_provider_config(tmp_path / ".fsq").provider is None


def test_status_reads_github_configuration_written_by_shared_ui_api(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    activate_github_copilot_provider(
        model="gpt-5", github_token={"access_token": "oauth-secret"}, provider_token={"token": "provider-secret", "expires_at": 9999999999, "plan": "individual"}, user_config_root=tmp_path
    )
    monkeypatch.setattr("fsq_agent.application.providers.check_provider_readiness", lambda _settings: (True, "Provider is ready.", ""))

    result = provider_status(user_config_root=tmp_path)

    assert result.status == "ready"
    assert result.provider == "github_copilot"
    assert result.model == "gpt-5"
    assert "secret" not in result.model_dump_json()


def test_failed_github_replacement_preserves_existing_azure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    configure_azure_openai(base_url="https://sample.openai.azure.com", model="old-model", api_key="old-secret", user_config_root=tmp_path)
    monkeypatch.setattr("fsq_agent.application.providers.complete_github_copilot_device_flow", lambda *_args, **_kwargs: object())
    monkeypatch.setattr("fsq_agent.application.providers.list_github_copilot_models", lambda _authorization: (GitHubCopilotModel(id="gpt-5", name="GPT-5"),))
    monkeypatch.setattr("fsq_agent.application.providers.activate_github_copilot_authorization", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("secret failure")))

    with pytest.raises(Exception, match="GitHub Copilot configuration failed"):
        complete_github_configuration(object(), model="gpt-5", select_model=lambda _models: "gpt-5", cancel_requested=lambda: False, user_config_root=tmp_path)

    saved = load_user_provider_config(tmp_path)
    assert saved.provider is not None
    assert saved.provider.type == "azure_openai"
    assert saved.provider.model == "old-model"
    assert saved.api_key == "old-secret"


def test_azure_cli_non_interactive_maps_complete_request_without_workspace(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = {}

    def configure(**kwargs):
        captured.update(kwargs)
        return ProviderConfigurationResult(provider="azure_openai", model=kwargs["model"])

    monkeypatch.setattr("fsq_agent.adapters.cli._main.configure_azure_openai", configure)
    result = CliRunner().invoke(
        main, ["--output", "json", "--non-interactive", "providers", "configure", "azure_openai", "--base-url", "https://sample.openai.azure.com", "--model", "gpt-5", "--api-key", "cli-secret"]
    )

    assert result.exit_code == 0
    assert captured["api_key"] == "cli-secret"
    assert "cli-secret" not in result.output


@pytest.mark.parametrize("output", ["json", "jsonl"])
def test_provider_status_machine_modes_emit_one_safe_terminal_result(output: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "fsq_agent.adapters.cli._main.provider_status", lambda: ProviderStatusResult(status="ready", configured=True, provider="azure_openai", model="gpt-5", authenticated=True, message="Ready.")
    )

    result = CliRunner().invoke(main, ["--output", output, "providers", "status"])

    assert result.exit_code == 0
    records = [json.loads(line) for line in result.output.splitlines()]
    assert len(records) == 1
    assert records[0]["result"]["provider"] == "azure_openai"


def test_provider_status_unavailable_is_result_with_exit_four(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "fsq_agent.adapters.cli._main.provider_status",
        lambda: ProviderStatusResult(status="unavailable", configured=False, authenticated=False, message="Not configured.", action="Configure a Provider."),
    )

    result = CliRunner().invoke(main, ["--output", "json", "providers", "status"])

    assert result.exit_code == 4
    assert json.loads(result.output)["status"] == "unavailable"
