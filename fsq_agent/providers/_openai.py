# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import json
import re
from dataclasses import dataclass

import httpx

from fsq_agent.config import Settings, validate_provider_settings
from fsq_agent.models import ConfigurationError
from fsq_agent.providers._azure_openai import ProviderClientConfig

_BASE_URL = "https://api.openai.com/v1/"
_MAX_RESPONSE_BYTES = 1024 * 1024
_SPECIALIZATIONS = {"mini", "nano", "codex", "embedding", "audio", "realtime", "image", "search", "transcribe", "transcription", "tts"}
_ERROR_MESSAGES = {
    "invalid_candidate": "OpenAI API key is missing or invalid.",
    "authentication": "OpenAI rejected the API key.",
    "access_denied": "OpenAI model access was denied.",
    "rate_limited": "OpenAI model discovery was rate limited.",
    "timeout": "OpenAI model discovery timed out.",
    "network": "OpenAI model discovery is unavailable.",
    "malformed_response": "OpenAI model discovery returned an invalid response.",
}


@dataclass(frozen=True)
class OpenAIModel:
    id: str
    name: str


def _failure(reason: str) -> ConfigurationError:
    return ConfigurationError(_ERROR_MESSAGES[reason], context={"provider": "openai", "reason": reason})


def _candidate_key(api_key: str) -> str:
    if not isinstance(api_key, str):
        raise _failure("invalid_candidate")
    normalized = api_key.strip()
    if not normalized or normalized.lower().startswith("replace-with") or any(ord(character) < 33 or ord(character) > 126 for character in normalized):
        raise _failure("invalid_candidate")
    return normalized


def build_openai_client_config(settings: Settings) -> ProviderClientConfig:
    validate_provider_settings(settings)
    runtime = settings.agent_runtime
    if runtime.provider != "openai":
        raise _failure("invalid_candidate")
    return ProviderClientConfig(
        provider="openai",
        model=runtime.model.strip(),
        api_key=_candidate_key(runtime.api_key),
        base_url=_BASE_URL,
        metadata={"endpoint_family": "openai"},
    )


def list_openai_models(*, api_key: str) -> tuple[OpenAIModel, ...]:
    normalized_key = _candidate_key(api_key)
    payload = bytearray()
    try:
        with (
            httpx.Client(timeout=httpx.Timeout(30.0, connect=10.0), follow_redirects=False) as client,
            client.stream("GET", f"{_BASE_URL}models", headers={"Authorization": f"Bearer {normalized_key}", "Accept": "application/json"}) as response,
        ):
            if response.status_code != 200:
                raise _failure({401: "authentication", 403: "access_denied", 429: "rate_limited"}.get(response.status_code, "network"))
            for chunk in response.iter_bytes(chunk_size=64 * 1024):
                if len(payload) + len(chunk) > _MAX_RESPONSE_BYTES:
                    raise _failure("malformed_response")
                payload.extend(chunk)
    except httpx.TimeoutException:
        raise _failure("timeout") from None
    except httpx.DecodingError:
        raise _failure("malformed_response") from None
    except httpx.HTTPError:
        raise _failure("network") from None
    try:
        data = json.loads(payload)
    except (ValueError, UnicodeError):
        raise _failure("malformed_response") from None
    if not isinstance(data, dict) or data.get("object") != "list" or not isinstance(data.get("data"), list):
        raise _failure("malformed_response")
    models: dict[str, OpenAIModel] = {}
    for candidate in data["data"]:
        model_id = candidate.get("id") if isinstance(candidate, dict) else None
        if not isinstance(model_id, str) or not model_id or model_id != model_id.strip() or len(model_id) > 256:
            raise _failure("malformed_response")
        version = re.match(r"^gpt-(\d+)(?:[.-]|$)", model_id, flags=re.IGNORECASE)
        if version is None or int(version.group(1)) < 5:
            continue
        if _SPECIALIZATIONS.intersection(re.findall(r"[a-z0-9]+", model_id.casefold())):
            continue
        models[model_id] = OpenAIModel(id=model_id, name=model_id)
    return tuple(sorted(models.values(), key=lambda model: (model.id.casefold(), model.id)))
