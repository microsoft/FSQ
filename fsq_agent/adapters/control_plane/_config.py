# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from fsq_agent.application import ApplicationError, configure_openai, list_openai_models
from fsq_agent.config import load_user_provider_config, save_azure_openai_provider
from fsq_agent.models import ConfigurationError
from fsq_agent.providers import test_model_provider_connection

if TYPE_CHECKING:
    from pathlib import Path


@dataclass(frozen=True)
class ConfigAPIError(Exception):
    status: int
    code: str
    message: str
    action: str


def require_config_access(bind_host: str, peer_host: str | None) -> None:
    if not _host_resolves_exclusively_to_loopback(bind_host):
        raise ConfigAPIError(403, "config_unavailable", "Provider configuration is available only on a loopback server.", "Restart Control Plane with a loopback bind host.")
    if not _is_loopback_address(peer_host):
        raise ConfigAPIError(403, "config_unavailable", "Provider configuration is available only to loopback clients.", "Open Control Plane from this computer.")


def require_same_origin_write(origin: str | None, host: str | None) -> None:
    if not origin:
        return
    parsed = urlsplit(origin)
    if parsed.scheme != "http" or not host or parsed.netloc.casefold() != host.casefold():
        raise ConfigAPIError(403, "cross_origin_forbidden", "Cross-origin Provider configuration writes are forbidden.", "Use the local Control Plane page to change Provider configuration.")


def get_config(user_config_root: Path | None) -> dict[str, Any]:
    config = load_user_provider_config(user_config_root)
    provider = config.provider
    if provider is None:
        return {"configured": False, "provider": None}
    if provider.type == "openai":
        return {"configured": True, "provider": {"type": "openai", "modelName": provider.model, "apiKey": config.api_key}}
    if provider.type == "azure_openai":
        presentation = {
            "type": "azure_openai",
            "modelName": provider.model,
            "baseUrl": provider.base_url,
            "apiKey": config.api_key,
        }
    else:
        presentation = {
            "type": "github_copilot",
            "modelName": provider.model,
            "authenticated": True,
        }
    return {"configured": True, "provider": presentation}


def list_openai_config_models(body: dict[str, Any]) -> dict[str, Any]:
    _require_openai_fields(body, {"apiKey"})
    models = list_openai_models(api_key=body["apiKey"])
    return {"models": [{"id": model.id, "name": model.name} for model in models]}


def save_openai_config(body: dict[str, Any], user_config_root: Path | None) -> dict[str, Any]:
    _require_openai_fields(body, {"modelName", "apiKey"})
    configure_openai(model=body["modelName"], api_key=body["apiKey"], user_config_root=user_config_root)
    return get_config(user_config_root)


def _require_openai_fields(body: dict[str, Any], fields: set[str]) -> None:
    _require_exact_fields(body, fields)
    if not all(isinstance(body[name], str) and body[name].strip() for name in fields):
        raise ConfigAPIError(400, "invalid_provider_config", "OpenAI configuration fields must be non-empty strings.", "Complete the required OpenAI fields and retry.")


def save_azure_config(body: dict[str, Any], user_config_root: Path | None) -> dict[str, Any]:
    _require_exact_fields(body, {"baseUrl", "modelName", "apiKey"})
    values = {name: body[name] for name in ("baseUrl", "modelName", "apiKey")}
    if not all(isinstance(value, str) and value.strip() for value in values.values()):
        raise ConfigAPIError(400, "invalid_provider_config", "baseUrl, modelName, and apiKey must be non-empty strings.", "Complete every Azure Provider field and retry.")
    save_azure_openai_provider(
        base_url=values["baseUrl"],
        model=values["modelName"],
        api_key=values["apiKey"],
        user_config_root=user_config_root,
    )
    return get_config(user_config_root)


def test_saved_connection(body: dict[str, Any], user_config_root: Path | None) -> dict[str, Any]:
    _require_exact_fields(body, set())
    result = test_model_provider_connection(user_config_root=user_config_root)
    return {
        "success": True,
        "provider": result.provider,
        "modelName": result.model,
        "durationMs": round(result.duration_seconds * 1000),
    }


def map_config_exception(exc: BaseException) -> ConfigAPIError:
    if isinstance(exc, ConfigAPIError):
        return exc
    if isinstance(exc, ApplicationError) and exc.details.get("provider") == "openai":
        mappings = {
            "invalid_candidate": (400, "invalid_provider_config"),
            "model_not_offered": (400, "provider_model_not_offered"),
            "authentication": (401, "provider_authorization_failed"),
            "access_denied": (403, "provider_access_denied"),
            "rate_limited": (429, "provider_rate_limited"),
            "timeout": (504, "provider_timeout"),
            "network": (503, "provider_unavailable"),
            "malformed_response": (502, "provider_response_invalid"),
            "storage": (503, "provider_storage_unavailable"),
        }
        reason = exc.details.get("reason")
        if isinstance(reason, str) and reason in mappings:
            status, code = mappings[reason]
            return ConfigAPIError(status, code, exc.message, exc.action or "Retry the OpenAI operation.")
        return ConfigAPIError(500, "config_internal_error", "OpenAI configuration could not be completed.", "Reload the current configuration before retrying.")
    if isinstance(exc, ConfigurationError):
        message = str(exc).splitlines()[0]
        lowered = message.casefold()
        if "not configured" in lowered or "authentication is not configured" in lowered:
            return ConfigAPIError(409, "provider_unconfigured", message, "Save a Provider configuration and retry.")
        if "rate limit" in lowered:
            return ConfigAPIError(429, "provider_rate_limited", message, "Wait and retry the connection test.")
        if "timed out" in lowered:
            return ConfigAPIError(504, "provider_timeout", message, "Check network access and retry.")
        if "authentication failed" in lowered:
            return ConfigAPIError(401, "provider_authorization_failed", message, "Check the saved Provider credentials.")
        if "not found" in lowered:
            return ConfigAPIError(404, "provider_model_unavailable", message, "Check the saved model or deployment name.")
        return ConfigAPIError(400, "provider_configuration_failed", message, "Correct the Provider configuration and retry.")
    if isinstance(exc, OSError):
        return ConfigAPIError(503, "provider_storage_unavailable", "Unable to access local Provider configuration.", "Check local file permissions and retry.")
    return ConfigAPIError(500, "config_internal_error", "An unexpected Provider configuration error occurred.", "Retry or inspect the local server logs.")


def _require_exact_fields(body: dict[str, Any], expected: set[str]) -> None:
    if set(body) != expected:
        names = ", ".join(sorted(expected)) or "no fields"
        raise ConfigAPIError(400, "invalid_request", f"Request body must contain exactly {names}.", "Correct the request body and retry.")


def _host_resolves_exclusively_to_loopback(host: str) -> bool:
    try:
        addresses = {item[4][0] for item in socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)}
    except socket.gaierror:
        return False
    return bool(addresses) and all(_is_loopback_address(address) for address in addresses)


def _is_loopback_address(value: str | None) -> bool:
    if not value:
        return False
    try:
        return ipaddress.ip_address(value.split("%", 1)[0]).is_loopback
    except ValueError:
        return False
