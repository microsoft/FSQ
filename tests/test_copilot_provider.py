# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import json
import time
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from fsq_agent.ai_services import build_ai_assertion_evaluator
from fsq_agent.config import Settings
from fsq_agent.models import ConfigurationError, OpenAIAgentsSettings
from fsq_agent.providers import _github_copilot as copilot
from fsq_agent.providers import (
    build_model_provider_session,
    prepare_model_provider_session,
    refresh_model_provider_session,
)


def _fake_copilot_token(prefix: str = "copilot") -> str:
    return f"{prefix}-token"


def _fake_github_token(suffix: str) -> str:
    return f"ghu_{suffix}"


def _authorization() -> copilot.GitHubCopilotAuthorization:
    return copilot.GitHubCopilotAuthorization(
        github_access_token=_fake_github_token("pending"),
        github_expires_at=time.time() + 3600,
        copilot_token=_fake_copilot_token("pending"),
        copilot_expires_at=time.time() + 1800,
        plan="business",
    )


def _github_settings(
    tmp_path,
    *,
    github_token: dict[str, object] | None = None,
    provider_token: dict[str, object] | None = None,
) -> Settings:
    settings = Settings(openai_agents=OpenAIAgentsSettings(provider="github_copilot"))
    settings.openai_agents.model = "gpt-5.5"
    settings.openai_agents.github_token = github_token
    settings.openai_agents.provider_token = provider_token
    settings.openai_agents.user_config_root = tmp_path
    return settings


class _AsyncOpenAI:
    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs


def test_build_github_copilot_client_config_uses_plan_endpoint(tmp_path) -> None:
    settings = _github_settings(
        tmp_path,
        provider_token={
            "token": _fake_copilot_token(),
            "expires_at": time.time() + 3600,
            "plan": "business",
        },
    )
    with patch.object(copilot, "_get_copilot_plan") as get_plan, patch.object(copilot, "_get_copilot_token") as get_token:
        config = copilot.build_github_copilot_client_config(settings)

    get_plan.assert_not_called()
    get_token.assert_not_called()
    assert config.api_key == _fake_copilot_token()
    assert config.base_url == "https://api.business.githubcopilot.com"
    assert config.default_headers["copilot-integration-id"] == "vscode-chat"
    assert config.model == "gpt-5.5"


def test_prepare_model_provider_session_exchanges_and_caches_copilot_provider_token(tmp_path) -> None:
    provider_cache_path = tmp_path / "auth" / "github-copilot-provider-token.json"
    settings = _github_settings(
        tmp_path,
        github_token={"access_token": _fake_github_token("test"), "expires_at": time.time() + 3600},
    )

    with (
        patch.object(copilot, "_get_copilot_plan", return_value="business") as get_plan,
        patch.object(
            copilot,
            "_get_copilot_token",
            return_value=copilot.CopilotToken(token=_fake_copilot_token(), expires_at=time.time() + 3600),
        ) as get_token,
    ):
        session = prepare_model_provider_session(settings)

    get_plan.assert_called_once_with(_fake_github_token("test"))
    get_token.assert_called_once_with(_fake_github_token("test"))
    assert session.client_config.api_key == _fake_copilot_token()
    data = json.loads(provider_cache_path.read_text(encoding="utf-8"))
    assert data["token"] == _fake_copilot_token()
    assert data["plan"] == "business"


def test_runtime_copilot_provider_session_requires_cached_provider_or_github_token(tmp_path) -> None:
    settings = _github_settings(tmp_path)

    with (
        patch.object(copilot, "request_github_copilot_device_code") as request_device_code,
        patch.object(copilot, "_get_copilot_token") as get_token,
        pytest.raises(ConfigurationError, match="Control Plane Config"),
    ):
        build_model_provider_session(settings)

    request_device_code.assert_not_called()
    get_token.assert_not_called()


def test_runtime_copilot_provider_session_refreshes_expired_provider_token_from_cached_github_token(tmp_path) -> None:
    provider_cache_path = tmp_path / "auth" / "github-copilot-provider-token.json"
    settings = _github_settings(
        tmp_path,
        github_token={"access_token": _fake_github_token("test"), "expires_at": time.time() + 3600},
        provider_token={"token": _fake_copilot_token("old-provider"), "expires_at": time.time() - 1, "plan": "enterprise"},
    )

    with (
        patch.object(
            copilot,
            "_get_copilot_plan",
            return_value="enterprise",
        ) as get_plan,
        patch.object(
            copilot,
            "_get_copilot_token",
            return_value=copilot.CopilotToken(token=_fake_copilot_token("new-provider"), expires_at=time.time() + 3600),
        ) as get_token,
    ):
        session = build_model_provider_session(settings)

    get_plan.assert_called_once_with(_fake_github_token("test"))
    get_token.assert_called_once_with(_fake_github_token("test"))
    assert session.client_config.api_key == _fake_copilot_token("new-provider")
    data = json.loads(provider_cache_path.read_text(encoding="utf-8"))
    assert data["token"] == _fake_copilot_token("new-provider")


def test_refresh_model_provider_session_always_refreshes_from_cached_github_token(tmp_path) -> None:
    provider_cache_path = tmp_path / "auth" / "github-copilot-provider-token.json"
    settings = _github_settings(
        tmp_path,
        github_token={"access_token": _fake_github_token("test"), "expires_at": time.time() + 3600},
        provider_token={"token": _fake_copilot_token("still-valid-provider"), "expires_at": time.time() + 3600, "plan": "business"},
    )

    with (
        patch.object(
            copilot,
            "_get_copilot_plan",
            return_value="enterprise",
        ) as get_plan,
        patch.object(
            copilot,
            "_get_copilot_token",
            return_value=copilot.CopilotToken(token=_fake_copilot_token("fresh-provider"), expires_at=time.time() + 3600),
        ) as get_token,
    ):
        session = refresh_model_provider_session(settings)

    get_plan.assert_called_once_with(_fake_github_token("test"))
    get_token.assert_called_once_with(_fake_github_token("test"))
    assert session.client_config.api_key == _fake_copilot_token("fresh-provider")
    data = json.loads(provider_cache_path.read_text(encoding="utf-8"))
    assert data["token"] == _fake_copilot_token("fresh-provider")
    assert data["plan"] == "enterprise"


def test_runtime_copilot_provider_session_and_ai_assertion_use_cached_provider_token(tmp_path) -> None:
    settings = _github_settings(
        tmp_path,
        provider_token={
            "token": _fake_copilot_token(),
            "expires_at": time.time() + 3600,
            "plan": "enterprise",
        },
    )

    with patch.object(copilot, "_get_copilot_token") as get_token:
        session = build_model_provider_session(settings)
        evaluator = build_ai_assertion_evaluator(settings)

    get_token.assert_not_called()
    assert session.client_config.api_key == _fake_copilot_token()
    assert session.client_config.base_url == "https://api.enterprise.githubcopilot.com"
    assert evaluator.session.client_config.api_key == _fake_copilot_token()


def test_load_cached_token_returns_none_when_expired(tmp_path) -> None:
    assert copilot._load_github_token({"access_token": _fake_github_token("old"), "expires_at": time.time() - 1}) is None


def test_load_github_token_returns_saved_nonexpired_token() -> None:
    token = copilot._load_github_token({"access_token": _fake_github_token("new")})

    assert token == _fake_github_token("new")


def test_request_device_code_uses_explicit_copilot_scope() -> None:
    response = MagicMock()
    response.json.return_value = {
        "device_code": "device",
        "user_code": "user",
        "verification_uri": "https://github.com/login/device",
        "expires_in": 600,
        "interval": 5,
    }
    response.raise_for_status = MagicMock()

    with patch.object(copilot.httpx, "post", return_value=response) as post:
        copilot.request_github_copilot_device_code()

    assert post.call_args.kwargs["data"]["scope"] == copilot.GITHUB_OAUTH_SCOPE


def test_complete_device_flow_returns_pending_authorization_without_activating_config() -> None:
    response = MagicMock()
    response.json.return_value = {"access_token": _fake_github_token("pending"), "expires_in": 3600}
    response.raise_for_status = MagicMock()
    device_code = copilot.GitHubDeviceCode(
        device_code="device",
        user_code="user",
        verification_uri="https://github.com/login/device",
        expires_at=time.time() + 600,
        poll_interval_seconds=1,
    )

    with (
        patch.object(copilot.time, "sleep"),
        patch.object(copilot.httpx, "post", return_value=response),
        patch.object(copilot, "_get_copilot_plan", return_value="business"),
        patch.object(
            copilot,
            "_get_copilot_token",
            return_value=copilot.CopilotToken(token=_fake_copilot_token("pending"), expires_at=time.time() + 1800),
        ),
        patch.object(copilot, "activate_github_copilot_provider") as activate,
    ):
        authorization = copilot.complete_github_copilot_device_flow(device_code, cancel_requested=lambda: False)

    activate.assert_not_called()
    assert authorization.plan == "business"
    assert authorization.github_access_token == _fake_github_token("pending")
    assert authorization.copilot_token == _fake_copilot_token("pending")
    assert "pending-token" not in repr(authorization)


def test_list_copilot_models_requests_plan_endpoint_and_filters_general_gpt_five_plus() -> None:
    response = MagicMock()
    response.json.return_value = {
        "data": [
            {"id": "gpt-4o", "name": "GPT 4o", "capabilities": {"type": "chat"}},
            {"id": "gpt-5", "name": "GPT 5", "capabilities": {"type": "chat"}},
            {"id": "gpt-5.1", "name": "GPT 5.1", "capabilities": {"type": "chat"}},
            {"id": "gpt-5-mini", "name": "GPT 5 Mini", "capabilities": {"type": "chat"}},
            {"id": "gpt-5.1-codex", "name": "GPT 5.1 Codex", "capabilities": {"type": "chat"}},
            {"id": "gpt-6", "name": "GPT 6", "capabilities": {"type": "chat"}},
            {"id": "GPT-5", "name": "duplicate", "capabilities": {"type": "chat"}},
            {"id": "gpt-5.2", "name": "disabled", "model_picker_enabled": False, "capabilities": {"type": "chat"}},
            {"id": "gpt-5.3", "name": "completion", "capabilities": {"type": "completion"}},
        ]
    }
    response.raise_for_status = MagicMock()

    with patch.object(copilot.httpx, "get", return_value=response) as get:
        models = copilot.list_github_copilot_models(_authorization())

    assert models == (
        copilot.GitHubCopilotModel(id="gpt-5", name="GPT 5"),
        copilot.GitHubCopilotModel(id="gpt-5.1", name="GPT 5.1"),
        copilot.GitHubCopilotModel(id="gpt-6", name="GPT 6"),
    )
    assert get.call_args.args[0] == "https://api.business.githubcopilot.com/models"
    assert get.call_args.kwargs["headers"]["authorization"] == "Bearer pending-token"
    assert get.call_args.kwargs["timeout"] == copilot.COPILOT_AUTH_TIMEOUT_SECONDS


def test_list_copilot_models_rejects_malformed_envelope_without_exposing_token() -> None:
    response = MagicMock()
    response.json.return_value = {"data": "not-a-list", "token": _fake_copilot_token("pending")}
    response.raise_for_status = MagicMock()

    with (
        patch.object(copilot.httpx, "get", return_value=response),
        pytest.raises(ConfigurationError, match="model response was invalid") as captured,
    ):
        copilot.list_github_copilot_models(_authorization())

    assert _fake_copilot_token("pending") not in str(captured.value)


def test_activate_copilot_authorization_commits_exact_credentials_and_model(tmp_path) -> None:
    authorization = _authorization()

    with patch.object(copilot, "activate_github_copilot_provider", return_value=MagicMock()) as activate:
        result = copilot.activate_github_copilot_authorization(
            authorization,
            model="gpt-5.1",
            user_config_root=tmp_path,
        )

    assert result is activate.return_value
    activate.assert_called_once_with(
        model="gpt-5.1",
        github_token={"access_token": authorization.github_access_token, "expires_at": authorization.github_expires_at},
        provider_token={
            "token": authorization.copilot_token,
            "expires_at": authorization.copilot_expires_at,
            "plan": authorization.plan,
        },
        user_config_root=tmp_path,
    )


def test_get_copilot_token_uses_copilot_exchange_headers() -> None:
    response = MagicMock()
    response.json.return_value = {
        "token": _fake_copilot_token(),
        "expires_at": 9999999999,
    }
    response.raise_for_status = MagicMock()

    with patch.object(copilot.httpx, "get", return_value=response) as get:
        token = copilot._get_copilot_token(_fake_github_token("test"))

    headers = get.call_args.kwargs["headers"]
    assert headers["authorization"] == "token ghu_test"
    assert headers["x-github-api-version"] == copilot.GITHUB_API_VERSION
    assert get.call_args.kwargs["timeout"] == copilot.COPILOT_AUTH_TIMEOUT_SECONDS
    assert token.token == _fake_copilot_token()


def test_prepare_model_provider_session_noninteractive_rejects_missing_provider_token(tmp_path) -> None:
    settings = _github_settings(tmp_path)

    with (
        patch.object(copilot, "request_github_copilot_device_code") as request_device_code,
        patch.object(copilot, "_get_copilot_token") as get_token,
        pytest.raises(ConfigurationError, match="Control Plane Config"),
    ):
        prepare_model_provider_session(settings)

    request_device_code.assert_not_called()
    get_token.assert_not_called()


def test_get_copilot_plan_rejects_unknown_plan() -> None:
    response = MagicMock()
    response.json.return_value = {"copilot_plan": "unknown"}
    response.raise_for_status = MagicMock()

    with patch.object(copilot.httpx, "get", return_value=response), pytest.raises(ConfigurationError, match="Unknown GitHub Copilot plan"):
        copilot._get_copilot_plan(_fake_github_token("test"))


def test_get_copilot_token_requires_token_field() -> None:
    response = MagicMock()
    response.json.return_value = {}
    response.raise_for_status = MagicMock()

    with patch.object(copilot.httpx, "get", return_value=response), pytest.raises(ConfigurationError, match="did not include a token"):
        copilot._get_copilot_token(_fake_github_token("test"))
