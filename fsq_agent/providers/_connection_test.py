# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from __future__ import annotations

from dataclasses import dataclass
from time import perf_counter
from typing import TYPE_CHECKING, Any

from fsq_agent.agent_engine import EngineError, ModelRequest, ModelResult
from fsq_agent.config import Settings, refresh_provider_settings, validate_provider_settings
from fsq_agent.models import ConfigurationError
from fsq_agent.providers._factory import build_model_provider_session

if TYPE_CHECKING:
    from pathlib import Path

CONNECTION_TEST_PROMPT = "Reply with FSQ_OK."


@dataclass(frozen=True)
class ProviderConnectionTestResult:
    provider: str
    model: str
    duration_seconds: float


def test_model_provider_connection(
    user_config_root: str | Path | None = None,  # noqa: PT028 - public operation, not a pytest test.
) -> ProviderConnectionTestResult:
    settings = refresh_provider_settings(Settings(), user_config_root)
    validate_provider_settings(settings)
    session = build_model_provider_session(settings)
    started_at = perf_counter()
    try:
        response = session.complete_sync(ModelRequest(input=CONNECTION_TEST_PROMPT, reasoning_effort="low"))
        _require_output_text(response)
        return ProviderConnectionTestResult(
            provider=session.provider,
            model=session.model,
            duration_seconds=perf_counter() - started_at,
        )
    except ConfigurationError:
        raise
    except Exception as exc:
        raise _connection_error(exc) from exc
    finally:
        session.close_sync()


def _require_output_text(response: ModelResult) -> None:
    output_text = response.text
    if not isinstance(output_text, str) or not output_text.strip():
        raise ConfigurationError("Provider connection test returned an empty model response.")


def _connection_error(exc: Exception) -> ConfigurationError:
    status_code = exc.status_code if isinstance(exc, EngineError) else None
    if status_code in {401, 403}:
        message = "Provider authentication failed. Check the saved credentials."
    elif status_code == 404:
        message = "The saved model or deployment was not found."
    elif status_code == 429:
        message = "The provider rate limit was reached. Try again later."
    elif isinstance(status_code, int) and status_code >= 500:
        message = "The provider is temporarily unavailable. Try again later."
    elif isinstance(exc, TimeoutError) or (isinstance(exc, EngineError) and exc.category == "timeout"):
        message = "The provider connection test timed out."
    else:
        message = "The provider connection test failed."
    context: dict[str, Any] = {"error_type": type(exc).__name__}
    if isinstance(status_code, int):
        context["status_code"] = status_code
    return ConfigurationError(message, context=context)


test_model_provider_connection.__test__ = False
