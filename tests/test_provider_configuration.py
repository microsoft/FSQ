# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from fsq_agent.agent_engine import ModelRequest, ModelResult
from fsq_agent.config import (
    Settings,
    activate_github_copilot_provider,
    load_settings,
    load_user_provider_config,
    save_azure_openai_provider,
)
from fsq_agent.models import AgentRuntimeSettings, ConfigurationError
from fsq_agent.providers import (
    GitHubDeviceCode,
    activate_github_copilot_authorization,
    build_model_provider_session,
    complete_github_copilot_device_flow,
    request_github_copilot_device_code,
    test_model_provider_connection,
)
from fsq_agent.providers import _github_copilot as copilot


def _settings_path(tmp_path: Path) -> Path:
    path = tmp_path / "platform.yaml"
    path.write_text(
        f"""
workspace:
  root_dir: {tmp_path.as_posix()}/workspace
""",
        encoding="utf-8",
    )
    return path


def test_azure_client_uses_saved_private_key_instead_of_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_root = tmp_path / "user"
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "environment-key")
    save_azure_openai_provider(
        base_url="https://example.openai.azure.com",
        model="saved-model",
        api_key="saved-key",
        user_config_root=user_root,
    )
    settings = load_settings(_settings_path(tmp_path), user_config_root=user_root)

    session = build_model_provider_session(settings)

    assert session.client_config.api_key == "saved-key"
    assert session.model == "saved-model"


def test_github_client_uses_saved_custom_model_and_user_provider_token(tmp_path: Path) -> None:
    user_root = tmp_path / "user"
    activate_github_copilot_provider(
        model="custom-copilot-model",
        github_token={"access_token": "github-token"},
        provider_token={"token": "provider-token", "expires_at": time.time() + 3600, "plan": "business"},
        user_config_root=user_root,
    )
    settings = load_settings(_settings_path(tmp_path), user_config_root=user_root)

    session = build_model_provider_session(settings)

    assert session.model == "custom-copilot-model"
    assert session.client_config.api_key == "provider-token"
    assert session.client_config.base_url == "https://api.business.githubcopilot.com"


def test_runtime_missing_github_credentials_never_starts_device_flow(tmp_path: Path) -> None:
    settings = Settings(agent_runtime=AgentRuntimeSettings(provider="github_copilot"))
    settings.agent_runtime.model = "copilot-model"
    settings.agent_runtime.user_config_root = tmp_path

    with (
        patch.object(copilot, "request_github_copilot_device_code") as request_device_code,
        pytest.raises(ConfigurationError, match="Control Plane Config"),
    ):
        build_model_provider_session(settings)

    request_device_code.assert_not_called()


def test_request_github_device_code_returns_observable_values_without_printing() -> None:
    response = MagicMock()
    response.raise_for_status = MagicMock()
    response.json.return_value = {
        "device_code": "device-secret",
        "user_code": "ABCD-EFGH",
        "verification_uri": "https://github.com/login/device",
        "interval": 7,
        "expires_in": 600,
    }

    with patch.object(copilot.httpx, "post", return_value=response) as post, patch("builtins.print") as print_mock:
        device_code = request_github_copilot_device_code()

    assert device_code.user_code == "ABCD-EFGH"
    assert device_code.verification_uri == "https://github.com/login/device"
    assert device_code.poll_interval_seconds == 7
    assert device_code.expires_at > time.time()
    assert post.call_args.kwargs["data"]["scope"] == copilot.GITHUB_OAUTH_SCOPE
    print_mock.assert_not_called()


def test_cancelled_github_device_flow_preserves_active_provider(tmp_path: Path) -> None:
    user_root = tmp_path / "user"
    save_azure_openai_provider(
        base_url="https://example.openai.azure.com",
        model="azure-model",
        api_key="azure-key",
        user_config_root=user_root,
    )
    device_code = GitHubDeviceCode(
        device_code="device-secret",
        user_code="ABCD-EFGH",
        verification_uri="https://github.com/login/device",
        expires_at=time.time() + 600,
        poll_interval_seconds=1,
    )

    with pytest.raises(ConfigurationError, match="cancelled"):
        complete_github_copilot_device_flow(
            device_code,
            cancel_requested=lambda: True,
        )

    assert load_user_provider_config(user_root).provider.type == "azure_openai"  # type: ignore[union-attr]


def test_github_device_flow_cancelled_during_poll_response_does_not_commit(tmp_path: Path) -> None:
    user_root = tmp_path / "user"
    save_azure_openai_provider(
        base_url="https://example.openai.azure.com",
        model="azure-model",
        api_key="azure-key",
        user_config_root=user_root,
    )
    device_code = GitHubDeviceCode(
        device_code="device-secret",
        user_code="ABCD-EFGH",
        verification_uri="https://github.com/login/device",
        expires_at=time.time() + 600,
        poll_interval_seconds=1,
    )
    cancelled = False
    response = MagicMock()
    response.raise_for_status = MagicMock()
    response.json.return_value = {"access_token": "github-token"}

    def poll(*args, **kwargs):
        nonlocal cancelled
        cancelled = True
        return response

    with patch.object(copilot.time, "sleep"), patch.object(copilot.httpx, "post", side_effect=poll), pytest.raises(ConfigurationError, match="cancelled"):
        complete_github_copilot_device_flow(
            device_code,
            cancel_requested=lambda: cancelled,
        )

    assert load_user_provider_config(user_root).provider.type == "azure_openai"  # type: ignore[union-attr]


def test_completed_github_device_flow_requires_explicit_activation_to_commit(tmp_path: Path) -> None:
    user_root = tmp_path / "user"
    device_code = GitHubDeviceCode(
        device_code="device-secret",
        user_code="ABCD-EFGH",
        verification_uri="https://github.com/login/device",
        expires_at=time.time() + 600,
        poll_interval_seconds=1,
    )
    response = MagicMock()
    response.raise_for_status = MagicMock()
    response.json.return_value = {"access_token": "github-token"}

    with (
        patch.object(copilot.time, "sleep"),
        patch.object(copilot.httpx, "post", return_value=response),
        patch.object(copilot, "_get_copilot_plan", return_value="enterprise"),
        patch.object(
            copilot,
            "_get_copilot_token",
            return_value=copilot.CopilotToken(token="provider-token", expires_at=time.time() + 3600),  # noqa: S106 - synthetic test credential.
        ),
    ):
        authorization = complete_github_copilot_device_flow(
            device_code,
            cancel_requested=lambda: False,
        )
    assert load_user_provider_config(user_root).provider is None

    saved = activate_github_copilot_authorization(
        authorization,
        model="copilot-model",
        user_config_root=user_root,
    )

    assert saved.provider is not None
    assert saved.provider.type == "github_copilot"
    assert saved.provider.model == "copilot-model"
    assert saved.github_token == {"access_token": "github-token"}
    assert saved.provider_token is not None
    assert saved.provider_token["token"] == "provider-token"  # noqa: S105 - synthetic test credential.
    assert saved.provider_token["plan"] == "enterprise"


def test_connection_test_uses_saved_provider_and_always_closes_session(tmp_path: Path) -> None:
    from fsq_agent.config import Settings

    user_root = tmp_path / "user"
    save_azure_openai_provider(
        base_url="https://example.openai.azure.com",
        model="saved-model",
        api_key="saved-key",
        user_config_root=user_root,
    )
    session = MagicMock()
    session.provider = "azure_openai"
    session.model = "saved-model"
    session.complete_sync.return_value = ModelResult(text="FSQ_OK")

    settings = Settings()
    settings.agent_runtime.reasoning_effort = "high"
    with (
        patch("fsq_agent.providers._connection_test.build_model_provider_session", return_value=session),
        patch("fsq_agent.providers._connection_test.Settings", return_value=settings),
    ):
        result = test_model_provider_connection(user_config_root=user_root)

    assert result.provider == "azure_openai"
    assert result.model == "saved-model"
    assert result.duration_seconds >= 0
    session.complete_sync.assert_called_once_with(ModelRequest(input="Reply with FSQ_OK.", reasoning_effort="low"))
    session.close_sync.assert_called_once_with()
