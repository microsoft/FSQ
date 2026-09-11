# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from collections.abc import Callable
from pathlib import Path

from fsq_agent.application.contracts import ApplicationError, ApplicationErrorCategory, ApplicationErrorCode, ProviderConfigurationResult, ProviderStatusResult
from fsq_agent.config import Settings, load_user_provider_config, refresh_provider_settings, save_azure_openai_provider, save_google_gemini_provider, save_openai_provider
from fsq_agent.models import ConfigurationError
from fsq_agent.providers import (
    GitHubCopilotModel,
    GitHubDeviceCode,
    GoogleGeminiModel,
    OpenAIModel,
    activate_github_copilot_authorization,
    check_provider_readiness,
    complete_github_copilot_device_flow,
    list_github_copilot_models,
    request_github_copilot_device_code,
)
from fsq_agent.providers import list_google_gemini_models as _discover_google_gemini_models
from fsq_agent.providers import list_openai_models as _discover_openai_models

_OPENAI_FAILURES = {
    "invalid_candidate": ("OpenAI model or API key is invalid.", "Complete the model and API key fields and retry."),
    "model_not_offered": ("The selected OpenAI model is not available.", "Reload models for this API key and select an offered model."),
    "authentication": ("OpenAI rejected the API key.", "Check the candidate API key and retry."),
    "access_denied": ("OpenAI model access was denied.", "Check the key's project permissions and model access."),
    "rate_limited": ("OpenAI model discovery was rate limited.", "Check quota or wait before retrying model discovery."),
    "timeout": ("OpenAI model discovery timed out.", "Check network access and retry loading or saving the model."),
    "network": ("OpenAI model discovery is unavailable.", "Check network access and retry."),
    "malformed_response": ("OpenAI returned an invalid model response.", "Retry model discovery later."),
    "storage": ("OpenAI configuration could not be stored.", "Check local configuration permissions and retry."),
    "internal": ("OpenAI configuration could not be completed.", "Reload the current configuration before retrying."),
}


def _openai_error(reason: object) -> ApplicationError:
    reason = reason if isinstance(reason, str) and reason in _OPENAI_FAILURES else "internal"
    if reason in {"invalid_candidate", "model_not_offered", "storage"}:
        code, category = ApplicationErrorCode.CONFIGURATION_INVALID, ApplicationErrorCategory.CONFIGURATION
    elif reason == "internal":
        code, category = ApplicationErrorCode.INTERNAL_ERROR, ApplicationErrorCategory.INTERNAL
    else:
        code, category = ApplicationErrorCode.PROVIDER_UNAVAILABLE, ApplicationErrorCategory.UNAVAILABLE
    message, action = _OPENAI_FAILURES[reason]
    return ApplicationError(code=code, category=category, message=message, action=action, details={"provider": "openai", "reason": reason})


def list_openai_models(*, api_key: str) -> tuple[OpenAIModel, ...]:
    try:
        return _discover_openai_models(api_key=api_key)
    except ConfigurationError as error:
        raise _openai_error(error.context.get("reason") if error.context.get("provider") == "openai" else None) from None
    except Exception as error:
        if isinstance(error, ApplicationError):
            raise
        raise _openai_error("internal") from None


def configure_openai(*, model: str, api_key: str, user_config_root: str | Path | None = None) -> ProviderConfigurationResult:
    if not isinstance(model, str) or not model.strip():
        raise _openai_error("invalid_candidate")
    normalized_model = model.strip()
    models = list_openai_models(api_key=api_key)
    if normalized_model not in {item.id for item in models}:
        raise _openai_error("model_not_offered")
    try:
        save_openai_provider(model=normalized_model, api_key=api_key, user_config_root=user_config_root)
    except ConfigurationError as error:
        raise _openai_error(error.context.get("reason") if error.context.get("provider") == "openai" else None) from None
    except Exception as error:
        if isinstance(error, ApplicationError):
            raise
        raise _openai_error("internal") from None
    return ProviderConfigurationResult(provider="openai", model=normalized_model)


def configure_azure_openai(*, base_url: str, model: str, api_key: str, user_config_root: str | Path | None = None) -> ProviderConfigurationResult:
    try:
        saved = save_azure_openai_provider(base_url=base_url, model=model, api_key=api_key, user_config_root=user_config_root)
    except Exception as exc:
        raise _provider_error("Azure OpenAI configuration failed.", "Check the endpoint, model, and API key, then retry.") from exc
    return ProviderConfigurationResult(provider="azure_openai", model=saved.provider.model if saved.provider is not None else "")


def _gemini_error(reason: object) -> ApplicationError:
    error = _openai_error(reason)
    return ApplicationError(
        code=error.code,
        category=error.category,
        message=error.message.replace("OpenAI", "Google Gemini"),
        action=(error.action or "").replace("OpenAI", "Google Gemini"),
        details={"provider": "google_gemini", "reason": error.details["reason"]},
    )


def list_google_gemini_models(*, api_key: str) -> tuple[GoogleGeminiModel, ...]:
    try:
        return _discover_google_gemini_models(api_key=api_key)
    except ConfigurationError as error:
        raise _gemini_error(error.context.get("reason") if error.context.get("provider") == "google_gemini" else None) from None
    except Exception as error:
        if isinstance(error, ApplicationError):
            raise
        raise _gemini_error("internal") from None


def configure_google_gemini(*, model: str, api_key: str, user_config_root: str | Path | None = None) -> ProviderConfigurationResult:
    if not isinstance(model, str) or not model.strip():
        raise _gemini_error("invalid_candidate")
    normalized_model = model.strip()
    models = list_google_gemini_models(api_key=api_key)
    if normalized_model not in {item.id for item in models}:
        raise _gemini_error("model_not_offered")
    try:
        save_google_gemini_provider(model=normalized_model, api_key=api_key, user_config_root=user_config_root)
    except ConfigurationError as error:
        raise _gemini_error(error.context.get("reason") if error.context.get("provider") == "google_gemini" else None) from None
    except Exception as error:
        if isinstance(error, ApplicationError):
            raise
        raise _gemini_error("internal") from None
    return ProviderConfigurationResult(provider="google_gemini", model=normalized_model)


def request_github_device_code() -> GitHubDeviceCode:
    try:
        return request_github_copilot_device_code()
    except Exception as exc:
        raise _provider_error("GitHub Copilot sign-in could not be started.", "Check network access and retry.") from exc


def complete_github_configuration(
    device_code: GitHubDeviceCode, *, model: str | None, select_model: Callable[[tuple[GitHubCopilotModel, ...]], str], cancel_requested: Callable[[], bool], user_config_root: str | Path | None = None
) -> ProviderConfigurationResult:
    try:
        authorization = complete_github_copilot_device_flow(device_code, cancel_requested=cancel_requested)
        models = list_github_copilot_models(authorization)
        selected = model.strip() if model else select_model(models).strip()
        _validate_selected_model(selected, models)
        saved = activate_github_copilot_authorization(authorization, model=selected, user_config_root=user_config_root)
    except Exception as exc:
        raise _provider_error("GitHub Copilot configuration failed.", "Retry sign-in and select an available model.") from exc
    return ProviderConfigurationResult(provider="github_copilot", model=saved.provider.model if saved.provider is not None else selected)


def provider_status(*, user_config_root: str | Path | None = None) -> ProviderStatusResult:
    try:
        configured = load_user_provider_config(user_config_root)
        if configured.provider is None:
            return ProviderStatusResult(status="unavailable", configured=False, authenticated=False, message="No model Provider is configured.", action="Run fsq providers configure.")
        ready, message, action = check_provider_readiness(refresh_provider_settings(Settings(), user_config_root))
        return ProviderStatusResult(
            status="ready" if ready else "unavailable", configured=True, provider=configured.provider.type, model=configured.provider.model, authenticated=ready, message=message, action=action or None
        )
    except Exception as exc:
        raise _provider_error("Provider status could not be read.", "Repair the user Provider configuration and retry.", configuration=True) from exc


def _provider_error(message: str, action: str, *, configuration: bool = False) -> ApplicationError:
    return ApplicationError(
        code=ApplicationErrorCode.CONFIGURATION_INVALID if configuration else ApplicationErrorCode.PROVIDER_UNAVAILABLE,
        category=ApplicationErrorCategory.CONFIGURATION if configuration else ApplicationErrorCategory.UNAVAILABLE,
        message=message,
        action=action,
    )


def _validate_selected_model(selected: str, models: tuple[GitHubCopilotModel, ...]) -> None:
    if selected not in {item.id for item in models}:
        raise ValueError("selected model is not offered")


__all__ = [
    "complete_github_configuration",
    "configure_azure_openai",
    "configure_google_gemini",
    "configure_openai",
    "list_google_gemini_models",
    "list_openai_models",
    "provider_status",
    "request_github_device_code",
]
