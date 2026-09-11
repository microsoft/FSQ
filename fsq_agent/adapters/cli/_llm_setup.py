# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from __future__ import annotations

import logging
import os
from pathlib import Path

import click

from fsq_agent.adapters.cli._env_file import read_env_values, upsert_env_values
from fsq_agent.config import load_settings, validate_provider_settings
from fsq_agent.models import ConfigurationError
from fsq_agent.providers import prepare_model_provider_session

logger = logging.getLogger("fsq_agent.adapters.cli._llm_setup")

LLM_PROVIDER_ENV = "FSQ_LLM_PROVIDER"
AZURE_OPENAI_BASE_URL_ENV = "AZURE_OPENAI_BASE_URL"
AZURE_OPENAI_MODEL_ENV = "AZURE_OPENAI_MODEL"
AZURE_OPENAI_API_KEY_ENV = "AZURE_OPENAI_API_KEY"
COPILOT_PROVIDER = "github_copilot"
AZURE_PROVIDER = "azure_openai"
MANAGED_PROVIDER_KEYS = (
    LLM_PROVIDER_ENV,
    AZURE_OPENAI_BASE_URL_ENV,
    AZURE_OPENAI_MODEL_ENV,
    AZURE_OPENAI_API_KEY_ENV,
)


def setup_llm_provider(*, provider: str) -> None:
    setup_root = Path.cwd()
    env_path = setup_root / ".env"
    workspace_path = setup_root / ".fsq-agent-workspace"

    env_values = read_env_values(env_path)
    write_values = _setup_values(provider, env_values)
    upsert_env_values(env_path, write_values)

    _log_process_env_conflicts(write_values, env_values)
    settings = load_settings(workspace=workspace_path)
    validate_provider_settings(settings)
    session = prepare_model_provider_session(settings, interactive_auth=True)
    try:
        logger.info("LLM provider readiness: ready")
        logger.info("Provider: %s", settings.agent_runtime.provider)
        logger.info("Setup root: %s", setup_root)
        logger.info("Environment file: %s", env_path)
        logger.info("Workspace: %s", settings.workspace.root_dir)
        logger.info("Mode: init provider setup")
    finally:
        session.close_sync()


def _setup_values(provider: str, env_values: dict[str, str]) -> dict[str, str]:
    if provider == COPILOT_PROVIDER:
        return {LLM_PROVIDER_ENV: COPILOT_PROVIDER}
    if provider == AZURE_PROVIDER:
        return {
            LLM_PROVIDER_ENV: AZURE_PROVIDER,
            AZURE_OPENAI_BASE_URL_ENV: _prompt_visible_env(AZURE_OPENAI_BASE_URL_ENV, env_values),
            AZURE_OPENAI_MODEL_ENV: _prompt_visible_env(AZURE_OPENAI_MODEL_ENV, env_values),
            AZURE_OPENAI_API_KEY_ENV: _prompt_secret_env(AZURE_OPENAI_API_KEY_ENV, env_values),
        }
    raise ConfigurationError(
        "Unsupported LLM provider.",
        context={"provider": provider, "supported": [COPILOT_PROVIDER, AZURE_PROVIDER]},
    )


def _prompt_visible_env(name: str, env_values: dict[str, str]) -> str:
    existing = _effective_env_value(name, env_values)
    if existing:
        return click.prompt(name, default=existing, show_default=True).strip()
    return click.prompt(name).strip()


def _prompt_secret_env(name: str, env_values: dict[str, str]) -> str:
    existing = _effective_env_value(name, env_values)
    if existing and not _is_placeholder_secret(existing):
        if click.confirm(f"Keep existing {name}?", default=True):
            return existing
    return click.prompt(name, hide_input=True, confirmation_prompt=False).strip()


def _effective_env_value(name: str, env_values: dict[str, str]) -> str | None:
    process_value = os.getenv(name)
    if process_value is not None and process_value.strip():
        return process_value.strip()
    value = env_values.get(name)
    return value.strip() if value and value.strip() else None


def _is_placeholder_secret(value: str) -> bool:
    return value.lower().startswith("replace-with")


def _log_process_env_conflicts(written_values: dict[str, str], env_values: dict[str, str]) -> None:
    for key, written_value in written_values.items():
        process_value = os.getenv(key)
        if process_value is not None and process_value.strip() and process_value.strip() != written_value:
            logger.warning(
                "%s is set in the process environment; that value takes precedence over .env.",
                key,
            )
            continue
        env_value = env_values.get(key)
        if key not in written_values and env_value and os.getenv(key) and os.getenv(key) != env_value:
            logger.warning(
                "%s is set in the process environment; that value takes precedence over .env.",
                key,
            )
