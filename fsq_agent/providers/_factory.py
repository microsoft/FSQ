# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from fsq_agent.config import Settings
from fsq_agent.providers._azure_openai import build_azure_openai_client_config
from fsq_agent.providers._github_copilot import build_github_copilot_client_config, refresh_github_copilot_client_config
from fsq_agent.providers._google_gemini import build_google_gemini_client_config
from fsq_agent.providers._openai import build_openai_client_config
from fsq_agent.providers._session import ModelProviderSession


class ModelProviderFactory:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def build_session(self) -> ModelProviderSession:
        provider = self.settings.agent_runtime.provider
        if provider == "google_gemini":
            return ModelProviderSession(build_google_gemini_client_config(self.settings))
        if provider == "github_copilot":
            return ModelProviderSession(build_github_copilot_client_config(self.settings))
        if provider == "azure_openai":
            return ModelProviderSession(build_azure_openai_client_config(self.settings))
        if provider == "openai":
            return ModelProviderSession(build_openai_client_config(self.settings))
        from fsq_agent.models import ConfigurationError

        raise ConfigurationError("Model Provider is not configured. Add a Provider in Control Plane Config.")

    def refresh_session(self) -> ModelProviderSession:
        provider = self.settings.agent_runtime.provider
        if provider == "github_copilot":
            return ModelProviderSession(refresh_github_copilot_client_config(self.settings))
        return self.build_session()


def build_model_provider_session(settings: Settings) -> ModelProviderSession:
    return ModelProviderFactory(settings).build_session()


def prepare_model_provider_session(settings: Settings) -> ModelProviderSession:
    return ModelProviderFactory(settings).build_session()


def refresh_model_provider_session(settings: Settings) -> ModelProviderSession:
    return ModelProviderFactory(settings).refresh_session()


def check_provider_readiness(settings: Settings) -> tuple[bool, str, str]:
    session = None
    try:
        session = prepare_model_provider_session(settings)
    except Exception:  # noqa: BLE001 - readiness returns a safe unavailable result.
        return False, "Model Provider is unavailable for non-interactive use.", "Run fsq providers configure for the selected Provider."
    finally:
        if session is not None:
            session.close_sync()
    return True, "Model Provider is ready for non-interactive use.", ""
