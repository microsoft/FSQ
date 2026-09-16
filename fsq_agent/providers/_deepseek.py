# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import json
import re
from dataclasses import dataclass

import httpx

from fsq_agent.config import Settings, validate_provider_settings
from fsq_agent.models import ConfigurationError
from fsq_agent.providers._client_config import ProviderClientConfig

_BASE_URL = "https://api.deepseek.com/"
_MAX_RESPONSE_BYTES = 1024 * 1024
_VERSIONED_MODEL = re.compile(r"^deepseek-v(\d+)(?:\.(\d+))?-(flash|pro)$")
_ERROR_MESSAGES = {
    "invalid_candidate": "DeepSeek API key is missing or invalid.",
    "authentication": "DeepSeek rejected the API key.",
    "access_denied": "DeepSeek model access was denied.",
    "rate_limited": "DeepSeek model discovery was rate limited.",
    "timeout": "DeepSeek model discovery timed out.",
    "network": "DeepSeek model discovery is unavailable.",
    "malformed_response": "DeepSeek model discovery returned an invalid response.",
}


@dataclass(frozen=True)
class DeepSeekModel:
    id: str
    name: str


def _failure(reason: str) -> ConfigurationError:
    return ConfigurationError(_ERROR_MESSAGES[reason], context={"provider": "deepseek", "reason": reason})


def _candidate_key(api_key: str) -> str:
    if not isinstance(api_key, str):
        raise _failure("invalid_candidate")
    normalized = api_key.strip()
    if not normalized or normalized.lower().startswith("replace-with") or len(normalized) > 1024 or any(ord(character) < 33 or ord(character) > 126 for character in normalized):
        raise _failure("invalid_candidate")
    return normalized


def build_deepseek_client_config(settings: Settings) -> ProviderClientConfig:
    validate_provider_settings(settings)
    runtime = settings.agent_runtime
    if runtime.provider != "deepseek":
        raise _failure("invalid_candidate")
    return ProviderClientConfig(
        provider="deepseek",
        model=runtime.model.strip(),
        api_key=_candidate_key(runtime.api_key),
        base_url=_BASE_URL,
        metadata={"endpoint_family": "deepseek"},
    )


def list_deepseek_models(*, api_key: str) -> tuple[DeepSeekModel, ...]:
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
    models: dict[str, DeepSeekModel] = {}
    for candidate in data["data"]:
        model_id = candidate.get("id") if isinstance(candidate, dict) else None
        if not isinstance(model_id, str) or not model_id or model_id != model_id.strip() or len(model_id) > 256:
            raise _failure("malformed_response")
        if _eligible_version(model_id) is not None:
            models[model_id] = DeepSeekModel(id=model_id, name=model_id)
    return tuple(sorted(models.values(), key=_model_order))


def _eligible_version(model_id: str) -> tuple[int, int, str] | None:
    if model_id == "deepseek-flash":
        return (0, 0, "alias")
    match = _VERSIONED_MODEL.fullmatch(model_id)
    if match is None:
        return None
    major, minor, family = int(match[1]), int(match[2] or 0), match[3]
    minimum = (4, 1) if family == "flash" else (4, 0)
    return (major, minor, family) if (major, minor) >= minimum else None


def _model_order(model: DeepSeekModel) -> tuple[int, int, int, int, str]:
    version = _eligible_version(model.id)
    if version is None or version[2] == "alias":
        return (0, 0, 0, 0, model.id)
    major, minor, family = version
    return (1, -major, -minor, 0 if family == "flash" else 1, model.id)
