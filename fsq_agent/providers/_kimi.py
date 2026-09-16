# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from itertools import pairwise
from typing import Literal

import httpx

from fsq_agent.config import Settings, validate_provider_settings
from fsq_agent.models import ConfigurationError
from fsq_agent.providers._client_config import ProviderClientConfig

_BASE_URLS: dict[str, str] = {
    "cn": "https://api.moonshot.cn/v1",
    "global": "https://api.moonshot.ai/v1",
}
_MAX_RESPONSE_BYTES = 1024 * 1024
_MODEL_ID = re.compile(r"^kimi-k(?P<major>[1-9]\d*)(?:\.(?P<minor>0|[1-9]\d*))?(?P<suffix>(?:-[a-z0-9]+)*)$")
_UNSTABLE_MARKERS = ("preview", "beta", "alpha", "experimental", "rc", "latest")
_COMPACT_DATE = re.compile(r"^(?:19|20)\d{6}$")
_DASHED_DATE = re.compile(r"-(?:19|20)\d{2}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12]\d|3[01])(?:-|$)")
_ERROR_MESSAGES = {
    "invalid_candidate": "Kimi region or API key is missing or invalid.",
    "authentication": "Kimi rejected the API key for the selected region.",
    "access_denied": "Kimi model access was denied.",
    "rate_limited": "Kimi model discovery was rate limited.",
    "timeout": "Kimi model discovery timed out.",
    "network": "Kimi model discovery is unavailable.",
    "malformed_response": "Kimi model discovery returned an invalid response.",
}


@dataclass(frozen=True)
class KimiModel:
    id: str
    name: str


def _failure(reason: str, *, region: str | None = None) -> ConfigurationError:
    context = {"provider": "kimi", "reason": reason}
    if region in _BASE_URLS:
        context["region"] = region
    return ConfigurationError(_ERROR_MESSAGES[reason], context=context)


def _candidate(region: object, api_key: object) -> tuple[Literal["cn", "global"], str]:
    if not isinstance(region, str) or region not in _BASE_URLS:
        raise _failure("invalid_candidate")
    if not isinstance(api_key, str):
        raise _failure("invalid_candidate", region=region)
    normalized_key = api_key.strip()
    if not normalized_key or normalized_key.lower().startswith("replace-with") or len(normalized_key) > 1024 or any(ord(character) < 33 or ord(character) > 126 for character in normalized_key):
        raise _failure("invalid_candidate", region=region)
    return region, normalized_key


def _eligible_model(model_id: str) -> tuple[int, int, str] | None:
    match = _MODEL_ID.fullmatch(model_id)
    if match is None:
        return None
    major = int(match["major"])
    minor = int(match["minor"] or 0)
    if (major, minor) < (3, 0):
        return None
    suffix = match["suffix"]
    suffix_tokens = suffix.removeprefix("-").split("-") if suffix else []
    release_candidate = any(left == "release" and right == "candidate" for left, right in pairwise(suffix_tokens))
    if release_candidate or any(token.startswith(marker) for token in suffix_tokens for marker in _UNSTABLE_MARKERS):
        return None
    if any(_COMPACT_DATE.fullmatch(token) for token in suffix_tokens) or _DASHED_DATE.search(suffix):
        return None
    return major, minor, suffix


def build_kimi_client_config(settings: Settings) -> ProviderClientConfig:
    validate_provider_settings(settings)
    runtime = settings.agent_runtime
    if runtime.provider != "kimi" or runtime.provider_region not in _BASE_URLS:
        raise _failure("invalid_candidate")
    region, api_key = _candidate(runtime.provider_region, runtime.api_key)
    return ProviderClientConfig(
        provider="kimi",
        model=runtime.model.strip(),
        api_key=api_key,
        base_url=_BASE_URLS[region],
        metadata={"endpoint_family": "kimi", "region": region},
        backend="kimi_responses",
    )


def list_kimi_models(*, region: Literal["cn", "global"], api_key: str) -> tuple[KimiModel, ...]:
    normalized_region, normalized_key = _candidate(region, api_key)
    payload = bytearray()
    try:
        with (
            httpx.Client(timeout=httpx.Timeout(30.0, connect=10.0), follow_redirects=False) as client,
            client.stream(
                "GET",
                f"{_BASE_URLS[normalized_region]}/models",
                headers={"Authorization": "Bearer " + normalized_key, "Accept": "application/json"},
            ) as response,
        ):
            if response.status_code != 200:
                reason = {401: "authentication", 403: "access_denied", 429: "rate_limited"}.get(response.status_code, "network")
                raise _failure(reason, region=normalized_region)
            for chunk in response.iter_bytes(chunk_size=64 * 1024):
                if len(payload) + len(chunk) > _MAX_RESPONSE_BYTES:
                    raise _failure("malformed_response", region=normalized_region)
                payload.extend(chunk)
    except httpx.TimeoutException:
        raise _failure("timeout", region=normalized_region) from None
    except httpx.DecodingError:
        raise _failure("malformed_response", region=normalized_region) from None
    except httpx.HTTPError:
        raise _failure("network", region=normalized_region) from None
    try:
        data = json.loads(payload)
    except (ValueError, UnicodeError):
        raise _failure("malformed_response", region=normalized_region) from None
    if not isinstance(data, dict) or data.get("object") != "list" or not isinstance(data.get("data"), list):
        raise _failure("malformed_response", region=normalized_region)
    models: dict[str, tuple[KimiModel, tuple[int, int, str]]] = {}
    for candidate in data["data"]:
        model_id = candidate.get("id") if isinstance(candidate, dict) else None
        if not isinstance(model_id, str) or not model_id or model_id != model_id.strip() or len(model_id) > 256:
            raise _failure("malformed_response", region=normalized_region)
        parsed = _eligible_model(model_id)
        if parsed is not None:
            models[model_id] = (KimiModel(id=model_id, name=model_id), parsed)
    return tuple(
        item[0]
        for item in sorted(
            models.values(),
            key=lambda item: (-item[1][0], -item[1][1], 0 if not item[1][2] else 1, item[0].id),
        )
    )
