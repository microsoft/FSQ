# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from fsq_agent.config import Settings
from fsq_agent.models import ConfigurationError
from fsq_agent.providers._client_config import ProviderClientConfig


def build_azure_openai_client_config(settings: Settings) -> ProviderClientConfig:
    runtime_settings = settings.agent_runtime
    api_key = runtime_settings.api_key
    if not api_key:
        raise ConfigurationError("Azure OpenAI API key is not configured.")
    if api_key.lower().startswith("replace-with"):
        raise ConfigurationError("Azure OpenAI API key still contains a placeholder value.")
    base_url = runtime_settings.base_url.strip()
    if not base_url.endswith("/openai/v1/"):
        raise ConfigurationError(
            "Azure OpenAI base URL must use the /openai/v1/ form.",
            context={"base_url": base_url},
        )
    model = runtime_settings.model.strip()
    if not model:
        raise ConfigurationError("Azure OpenAI model deployment name is required.")
    return ProviderClientConfig(
        provider="azure_openai",
        model=model,
        api_key=api_key,
        base_url=base_url,
        metadata={"endpoint_family": "azure_openai"},
        model_name_is_deployment=True,
    )
