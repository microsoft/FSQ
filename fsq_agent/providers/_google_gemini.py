# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import asyncio
import json
import re
import time
from dataclasses import dataclass

import httpx

from fsq_agent.config import Settings, validate_provider_settings
from fsq_agent.models import ConfigurationError
from fsq_agent.providers._client_config import ProviderClientConfig
from fsq_agent.providers._session import _run_async_sync

_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/"
_MAX_PAGES = 20
_MAX_BYTES = 4 * 1024 * 1024
_DEADLINE_SECONDS = 60.0
_MODEL_PATTERN = re.compile(r"gemini-([0-9]+)(?:\.([0-9]+))?-(flash|pro)")
_MESSAGES = {
    "invalid_candidate": "Google Gemini API key is missing or invalid.",
    "authentication": "Google Gemini rejected the API key.",
    "access_denied": "Google Gemini model access was denied.",
    "rate_limited": "Google Gemini model discovery was rate limited.",
    "timeout": "Google Gemini model discovery timed out.",
    "network": "Google Gemini model discovery is unavailable.",
    "malformed_response": "Google Gemini model discovery returned an invalid or incomplete collection.",
}


@dataclass(frozen=True)
class GoogleGeminiModel:
    id: str
    name: str


def _failure(reason):
    return ConfigurationError(_MESSAGES[reason], context={"provider": "google_gemini", "reason": reason})


def _candidate_key(value):
    if not isinstance(value, str):
        raise _failure("invalid_candidate")
    key = value.strip()
    if not key or len(key) > 1024 or key.lower().startswith("replace-with") or any(ord(character) < 33 or ord(character) > 126 for character in key):
        raise _failure("invalid_candidate")
    return key


def build_google_gemini_client_config(settings: Settings) -> ProviderClientConfig:
    validate_provider_settings(settings)
    runtime = settings.agent_runtime
    if runtime.provider != "google_gemini":
        raise _failure("invalid_candidate")
    return ProviderClientConfig(
        provider="google_gemini", model=runtime.model.strip(), api_key=_candidate_key(runtime.api_key), base_url=_BASE_URL, metadata={"endpoint_family": "google_gemini"}, backend="google_interactions"
    )


def _model(candidate):
    if not isinstance(candidate, dict):
        raise _failure("malformed_response")
    name = candidate.get("name")
    methods = candidate.get("supportedGenerationMethods")
    display = candidate.get("displayName")
    if (
        not isinstance(name, str)
        or len(name) > 256
        or not name.startswith("models/")
        or name != name.strip()
        or not isinstance(methods, list)
        or any(not isinstance(method, str) for method in methods)
    ):
        raise _failure("malformed_response")
    model_id = name.removeprefix("models/")
    if not model_id or "/" in model_id or any(ord(character) < 33 or ord(character) > 126 for character in model_id):
        raise _failure("malformed_response")
    if display is not None and (not isinstance(display, str) or not display.strip() or len(display) > 128 or any(ord(character) < 32 for character in display)):
        raise _failure("malformed_response")
    match = _MODEL_PATTERN.fullmatch(model_id)
    if "generateContent" not in methods or match is None or int(match[1]) < 3:
        return None
    # Google thinking docs: Pro >= 3.1 supplies medium; keep aligned with Agent Engine effort mapping.
    if match[3] == "pro" and (int(match[1]), int(match[2] or 0)) < (3, 1):
        return None
    return GoogleGeminiModel(id=model_id, name=display or model_id)


def _sort_key(model):
    match = _MODEL_PATTERN.fullmatch(model.id)
    return -int(match[1]), -int(match[2] or "0"), match[3] != "flash", model.id


def list_google_gemini_models(*, api_key: str) -> tuple[GoogleGeminiModel, ...]:
    key = _candidate_key(api_key)
    return _run_async_sync(_discover_models(key))


async def _discover_models(key: str) -> tuple[GoogleGeminiModel, ...]:
    deadline = time.monotonic() + _DEADLINE_SECONDS
    params = {"pageSize": "1000"}
    seen_tokens = set()
    models = {}
    total_bytes = 0
    try:
        async with asyncio.timeout(_DEADLINE_SECONDS), httpx.AsyncClient(follow_redirects=False) as client:
            for _page in range(_MAX_PAGES):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise _failure("timeout")
                payload = bytearray()
                async with client.stream(
                    "GET", f"{_BASE_URL}models", params=params, headers={"x-goog-api-key": key, "Accept": "application/json"}, timeout=httpx.Timeout(min(30.0, remaining), connect=min(10.0, remaining))
                ) as response:
                    if response.status_code != 200:
                        raise _failure({400: "invalid_candidate", 401: "authentication", 403: "access_denied", 429: "rate_limited"}.get(response.status_code, "network"))
                    async for chunk in response.aiter_bytes(chunk_size=64 * 1024):
                        total_bytes += len(chunk)
                        if time.monotonic() >= deadline:
                            raise _failure("timeout")
                        if total_bytes > _MAX_BYTES:
                            raise _failure("malformed_response")
                        payload.extend(chunk)
                try:
                    data = json.loads(payload)
                except (ValueError, UnicodeError):
                    raise _failure("malformed_response") from None
                if not isinstance(data, dict) or not isinstance(data.get("models", []), list):
                    raise _failure("malformed_response")
                for candidate in data.get("models", []):
                    model = _model(candidate)
                    if model is not None:
                        models.setdefault(model.id, model)
                if "nextPageToken" not in data:
                    return tuple(sorted(models.values(), key=_sort_key))
                token = data["nextPageToken"]
                if not isinstance(token, str) or not token.strip() or len(token) > 4096 or token in seen_tokens or any(ord(character) < 32 for character in token):
                    raise _failure("malformed_response")
                seen_tokens.add(token)
                params["pageToken"] = token
    except (TimeoutError, httpx.TimeoutException):
        raise _failure("timeout") from None
    except httpx.DecodingError:
        raise _failure("malformed_response") from None
    except httpx.HTTPError:
        raise _failure("network") from None
    raise _failure("malformed_response")
