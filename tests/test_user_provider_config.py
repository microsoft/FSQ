# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import json
import multiprocessing
from pathlib import Path
from typing import Any

import pytest
import yaml

from fsq_agent.config import (
    activate_github_copilot_provider,
    list_workspace_registry,
    load_settings,
    load_user_provider_config,
    refresh_provider_settings,
    save_azure_openai_provider,
    validate_provider_settings,
)
from fsq_agent.models import ConfigurationError


def _save_azure_repeatedly(user_root: Path, start: Any) -> None:
    start.wait()
    for index in range(20):
        save_azure_openai_provider(
            base_url="https://example.openai.azure.com",
            model=f"azure-{index}",
            api_key=f"azure-key-{index}",
            user_config_root=user_root,
        )


def _save_github_repeatedly(user_root: Path, start: Any) -> None:
    start.wait()
    for index in range(20):
        activate_github_copilot_provider(
            model=f"github-{index}",
            github_token={"access_token": f"github-token-{index}"},
            provider_token={"token": f"provider-token-{index}", "plan": "individual"},
            user_config_root=user_root,
        )


def _runtime_config(tmp_path: Path) -> Path:
    config_path = tmp_path / "platform.yaml"
    config_path.write_text(
        f"""
workspace:
  root_dir: {tmp_path.as_posix()}/workspace
agent_runtime:
  max_turns: 40
""",
        encoding="utf-8",
    )
    return config_path


def test_load_user_provider_config_initializes_explicit_unconfigured_state(tmp_path: Path) -> None:
    user_root = tmp_path / "user"

    config = load_user_provider_config(user_root)

    assert config.version == 3
    assert config.provider is None
    assert config.workspaces == []
    assert yaml.safe_load((user_root / "config.yaml").read_text(encoding="utf-8")) == {
        "version": 3,
        "provider": None,
        "workspaces": [],
    }
    assert (user_root / "auth").is_dir()


def test_load_user_provider_config_upgrades_valid_v2_without_changing_provider_or_credentials(tmp_path: Path) -> None:
    user_root = tmp_path / "user"
    user_root.mkdir()
    workspace_root = (tmp_path / "checkout").resolve()
    (user_root / "config.yaml").write_text(
        f"""
version: 2
provider:
  type: github_copilot
  model: copilot-model
workspaces:
  - name: checkout
    config_path: {(workspace_root / ".fsq" / "config.yaml").as_posix()}
""",
        encoding="utf-8",
    )
    auth_dir = user_root / "auth"
    auth_dir.mkdir()
    (auth_dir / "github-copilot-token.json").write_text('{"access_token":"github-token"}', encoding="utf-8")
    (auth_dir / "github-copilot-provider-token.json").write_text(
        '{"token":"provider-token","plan":"individual"}',
        encoding="utf-8",
    )

    config = load_user_provider_config(user_root)

    assert config.version == 3
    assert config.provider is not None
    assert config.provider.type == "github_copilot"
    assert [(entry.name, entry.root_path) for entry in config.workspaces] == [("checkout", workspace_root)]
    assert config.github_token == {"access_token": "github-token"}
    assert config.provider_token == {"token": "provider-token", "plan": "individual"}
    persisted = yaml.safe_load((user_root / "config.yaml").read_text(encoding="utf-8"))
    assert persisted == {
        "version": 3,
        "provider": {"type": "github_copilot", "model": "copilot-model"},
        "workspaces": [{"name": "checkout", "root_path": str(workspace_root)}],
    }


def test_provider_activation_preserves_workspace_registry(tmp_path: Path) -> None:
    user_root = tmp_path / "user"
    user_root.mkdir()
    workspace_root = (tmp_path / "checkout").resolve()
    (user_root / "config.yaml").write_text(
        f"""
version: 3
provider: null
workspaces:
  - name: checkout
    root_path: {workspace_root.as_posix()}
""",
        encoding="utf-8",
    )

    save_azure_openai_provider(
        base_url="https://example.openai.azure.com",
        model="azure-model",
        api_key="azure-key",
        user_config_root=user_root,
    )

    registry = list_workspace_registry(user_root)
    assert [(entry.name, entry.root_path) for entry in registry] == [("checkout", workspace_root)]


@pytest.mark.parametrize("duplicate", ["name", "path"])
def test_load_user_provider_config_rejects_duplicate_workspace_identity(tmp_path: Path, duplicate: str) -> None:
    user_root = tmp_path / "user"
    user_root.mkdir()
    first_path = (tmp_path / "checkout").resolve()
    second_path = first_path if duplicate == "path" else (tmp_path / "search").resolve()
    second_name = "search" if duplicate == "path" else "CHECKOUT"
    (user_root / "config.yaml").write_text(
        yaml.safe_dump(
            {
                "version": 3,
                "provider": None,
                "workspaces": [
                    {"name": "checkout", "root_path": str(first_path)},
                    {"name": second_name, "root_path": str(second_path)},
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    with pytest.raises(ConfigurationError, match="Invalid user Provider configuration"):
        load_user_provider_config(user_root)


def test_load_user_provider_config_rejects_symlinked_workspace_registry_root(tmp_path: Path) -> None:
    real_root = tmp_path / "checkout"
    real_root.mkdir()
    linked_root = tmp_path / "checkout-link"
    try:
        linked_root.symlink_to(real_root, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"Symlink creation is unavailable: {exc}")
    user_root = tmp_path / "user"
    user_root.mkdir()
    (user_root / "config.yaml").write_text(
        yaml.safe_dump(
            {
                "version": 3,
                "provider": None,
                "workspaces": [
                    {"name": "checkout", "root_path": str(linked_root)},
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    with pytest.raises(ConfigurationError, match="Invalid user Provider configuration"):
        load_user_provider_config(user_root)


@pytest.mark.parametrize("previous", [None, "azure_openai", "github_copilot"])
def test_save_openai_provider_replaces_credentials_and_resolves_saved_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, previous: str | None) -> None:
    from fsq_agent.config import save_openai_provider

    user_root = tmp_path / "user"
    if previous == "azure_openai":
        save_azure_openai_provider(base_url="https://example.openai.azure.com", model="deployment", api_key="azure-key", user_config_root=user_root)
    elif previous == "github_copilot":
        activate_github_copilot_provider(model="gpt-5", github_token={"access_token": "github-token"}, provider_token={"token": "copilot-token", "plan": "individual"}, user_config_root=user_root)
    monkeypatch.setenv("OPENAI_API_KEY", "environment-key")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://other.example/v1/")
    saved = save_openai_provider(model=" gpt-5 ", api_key=" saved-openai-key ", user_config_root=user_root)
    loaded = load_user_provider_config(user_root)
    assert saved.provider is not None
    assert saved.provider.model_dump() == {"type": "openai", "model": "gpt-5"}
    assert loaded.provider == saved.provider
    assert loaded.api_key == "saved-openai-key"
    assert "saved-openai-key" not in loaded.model_dump_json()
    assert json.loads((user_root / "auth" / "openai.json").read_text(encoding="utf-8")) == {"api_key": "saved-openai-key"}
    assert {path.name for path in (user_root / "auth").iterdir()} == {"openai.json"}
    settings = load_settings(_runtime_config(tmp_path), user_config_root=user_root)
    validate_provider_settings(settings)
    assert settings.agent_runtime.provider == "openai"
    assert settings.agent_runtime.base_url == "https://api.openai.com/v1/"
    assert settings.agent_runtime.model == "gpt-5"
    assert settings.agent_runtime.api_key == "saved-openai-key"
    assert settings.agent_runtime.github_token is None
    assert settings.agent_runtime.provider_token is None
    assert "saved-openai-key" not in settings.model_dump_json()


@pytest.mark.parametrize("replacement", ["azure_openai", "github_copilot"])
def test_replacing_openai_removes_its_credentials(tmp_path: Path, replacement: str) -> None:
    from fsq_agent.config import save_openai_provider

    save_openai_provider(model="gpt-5", api_key="openai-key", user_config_root=tmp_path)
    if replacement == "azure_openai":
        save_azure_openai_provider(base_url="https://example.openai.azure.com", model="deployment", api_key="azure-key", user_config_root=tmp_path)
    else:
        activate_github_copilot_provider(model="gpt-5", github_token={"access_token": "github-token"}, provider_token={"token": "copilot-token", "plan": "individual"}, user_config_root=tmp_path)
    assert not (tmp_path / "auth" / "openai.json").exists()
    assert load_user_provider_config(tmp_path).provider.type == replacement


@pytest.mark.parametrize("api_key", ["", " ", "replace-with-key"])
def test_invalid_openai_candidate_preserves_active_provider(tmp_path: Path, api_key: str) -> None:
    from fsq_agent.config import save_openai_provider

    original = save_azure_openai_provider(base_url="https://example.openai.azure.com", model="deployment", api_key="azure-key", user_config_root=tmp_path)
    with pytest.raises(ConfigurationError) as caught:
        save_openai_provider(model="gpt-5", api_key=api_key, user_config_root=tmp_path)
    assert caught.value.context == {"provider": "openai", "reason": "invalid_candidate"}
    assert load_user_provider_config(tmp_path).provider == original.provider
    assert load_user_provider_config(tmp_path).api_key == "azure-key"


def test_save_openai_preserves_workspace_registry(tmp_path: Path) -> None:
    from fsq_agent.config import save_openai_provider

    workspace_root = (tmp_path / "workspace").resolve()
    (tmp_path / "config.yaml").write_text(yaml.safe_dump({"version": 3, "provider": None, "workspaces": [{"name": "workspace", "root_path": str(workspace_root)}]}), encoding="utf-8")
    save_openai_provider(model="gpt-5", api_key="openai-key", user_config_root=tmp_path)
    assert [(entry.name, entry.root_path) for entry in list_workspace_registry(tmp_path)] == [("workspace", workspace_root)]


@pytest.mark.parametrize("rollback_succeeds", [True, False])
def test_openai_failed_transaction_reports_only_confirmed_rollback_as_storage(tmp_path, monkeypatch, rollback_succeeds):
    from fsq_agent.config import _user_provider, save_openai_provider

    original = save_azure_openai_provider(base_url="https://example.openai.azure.com", model="deployment", api_key="old-key", user_config_root=tmp_path)
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
        save_openai_provider(model="gpt-5", api_key="candidate-key", user_config_root=tmp_path)
    assert caught.value.context == {"provider": "openai", "reason": "storage" if rollback_succeeds else "internal"}
    assert load_user_provider_config(tmp_path).provider == original.provider
    assert load_user_provider_config(tmp_path).api_key == "old-key"
    assert "candidate-key" not in str(caught.value)


def test_save_azure_provider_normalizes_and_keeps_secret_out_of_serialization(tmp_path: Path) -> None:
    user_root = tmp_path / "user"

    saved = save_azure_openai_provider(
        base_url="https://example.openai.azure.com/openai/responses?api-version=preview",
        model="  gpt-5.4  ",
        api_key="complete-local-key",
        user_config_root=user_root,
    )
    loaded = load_user_provider_config(user_root)

    assert saved.provider is not None
    assert saved.provider.type == "azure_openai"
    assert saved.provider.model == "gpt-5.4"
    assert saved.provider.base_url == "https://example.openai.azure.com/openai/v1/"
    assert loaded.provider == saved.provider
    assert loaded.api_key == "complete-local-key"
    assert "api_key" not in loaded.model_dump()
    assert json.loads((user_root / "auth" / "azure-openai.json").read_text(encoding="utf-8")) == {"api_key": "complete-local-key"}


def test_activate_github_provider_replaces_azure_only_after_complete_auth(tmp_path: Path) -> None:
    user_root = tmp_path / "user"
    save_azure_openai_provider(
        base_url="https://example.openai.azure.com",
        model="azure-model",
        api_key="azure-key",
        user_config_root=user_root,
    )
    github_token = {"access_token": "github-token", "expires_at": 12345}
    provider_token = {"token": "provider-token", "expires_at": 67890, "plan": "individual"}

    saved = activate_github_copilot_provider(
        model="copilot-model",
        github_token=github_token,
        provider_token=provider_token,
        user_config_root=user_root,
    )

    assert saved.provider is not None
    assert saved.provider.type == "github_copilot"
    assert saved.provider.model == "copilot-model"
    assert saved.github_token == github_token
    assert saved.provider_token == provider_token
    assert not (user_root / "auth" / "azure-openai.json").exists()


def test_invalid_replacement_preserves_active_provider(tmp_path: Path) -> None:
    user_root = tmp_path / "user"
    active = activate_github_copilot_provider(
        model="copilot-model",
        github_token={"access_token": "github-token"},
        provider_token={"token": "provider-token", "plan": "individual"},
        user_config_root=user_root,
    )

    with pytest.raises(ConfigurationError, match="API key"):
        save_azure_openai_provider(
            base_url="https://example.openai.azure.com",
            model="azure-model",
            api_key=" ",
            user_config_root=user_root,
        )

    assert load_user_provider_config(user_root).provider == active.provider
    assert (user_root / "auth" / "github-copilot-token.json").exists()
    assert (user_root / "auth" / "github-copilot-provider-token.json").exists()


def test_provider_replacement_is_serialized_across_processes(tmp_path: Path) -> None:
    user_root = tmp_path / "user"
    context = multiprocessing.get_context("spawn")
    start = context.Event()
    processes = [
        context.Process(target=_save_azure_repeatedly, args=(user_root, start)),
        context.Process(target=_save_github_repeatedly, args=(user_root, start)),
    ]
    for process in processes:
        process.start()
    start.set()
    for process in processes:
        process.join(timeout=30)
        assert process.exitcode == 0

    saved = load_user_provider_config(user_root)
    assert saved.provider is not None
    if saved.provider.type == "azure_openai":
        assert saved.api_key.startswith("azure-key-")
        assert not (user_root / "auth" / "github-copilot-token.json").exists()
        assert not (user_root / "auth" / "github-copilot-provider-token.json").exists()
    else:
        assert saved.github_token is not None
        assert saved.provider_token is not None
        assert not (user_root / "auth" / "azure-openai.json").exists()


def test_runtime_load_ignores_provider_environment_and_refreshes_only_provider(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_root = tmp_path / "user"
    config_path = _runtime_config(tmp_path)
    monkeypatch.setenv("FSQ_LLM_PROVIDER", "azure_openai")
    monkeypatch.setenv("AZURE_OPENAI_BASE_URL", "https://environment.example/openai/v1/")
    monkeypatch.setenv("AZURE_OPENAI_MODEL", "environment-model")
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "environment-key")

    settings = load_settings(config_path, user_config_root=user_root)

    assert settings.agent_runtime.provider is None
    assert settings.agent_runtime.model == ""
    assert settings.agent_runtime.base_url == ""
    assert settings.agent_runtime.api_key == ""
    with pytest.raises(ConfigurationError, match="not configured"):
        validate_provider_settings(settings)

    save_azure_openai_provider(
        base_url="https://saved.example.openai.azure.com",
        model="saved-model",
        api_key="saved-key",
        user_config_root=user_root,
    )
    refreshed = refresh_provider_settings(settings, user_config_root=user_root)

    assert refreshed is not settings
    assert refreshed.agent_runtime.provider == "azure_openai"
    assert refreshed.agent_runtime.model == "saved-model"
    assert refreshed.agent_runtime.base_url == "https://saved.example.openai.azure.com/openai/v1/"
    assert refreshed.agent_runtime.api_key == "saved-key"
    assert refreshed.agent_runtime.max_turns == 40
    assert refreshed.workspace.root_dir == settings.workspace.root_dir
