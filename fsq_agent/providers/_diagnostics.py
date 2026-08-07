# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from __future__ import annotations

import json
import os
from pathlib import Path
import time
from typing import Any, Callable
from urllib.parse import urlsplit, urlunsplit

import httpx

from fsq_agent.config import Settings
from fsq_agent.models import (
    DiagnosticProbeResult,
    DoctorCheckResult,
    DoctorFix,
    DoctorProgressEvent,
    DoctorProgressSink,
)
from fsq_agent.providers._factory import refresh_model_provider_session
from fsq_agent.providers._github_copilot import (
    COPILOT_BASE_URLS,
    PROVIDER_TOKEN_CACHE_RELATIVE_PATH,
    TOKEN_CACHE_RELATIVE_PATH,
)


class ProviderDiagnosticService:
    def __init__(
        self,
        *,
        environ: dict[str, str] | None = None,
        clock: Callable[[], float] = time.time,
        http_get: Callable[..., Any] | None = None,
    ) -> None:
        self.environ = dict(environ) if environ is not None else dict(os.environ)
        self.clock = clock
        self.http_get = http_get or httpx.get

    def probe(
        self,
        settings: Settings,
        timeout_seconds: float = 5.0,
        progress_sink: DoctorProgressSink | None = None,
    ) -> list[DiagnosticProbeResult]:
        if settings.openai_agents.provider == "azure_openai":
            return self._probe_azure(settings, timeout_seconds, progress_sink)
        return self._probe_copilot(settings, timeout_seconds, progress_sink)

    def refresh_cached_copilot_provider_token(
        self,
        settings: Settings,
        progress_sink: DoctorProgressSink | None = None,
    ) -> None:
        _emit(
            progress_sink,
            DoctorProgressEvent(
                event_type="repair_started",
                phase="Provider",
                repair_action="provider.refresh_copilot_token",
                summary="Refreshing the cached Copilot provider token...",
            ),
        )
        session = refresh_model_provider_session(settings)
        session.close_sync()

    def _probe_copilot(
        self,
        settings: Settings,
        timeout_seconds: float,
        sink: DoctorProgressSink | None,
    ) -> list[DiagnosticProbeResult]:
        _started(sink, "provider.github_copilot.credentials", "Checking cached GitHub Copilot credentials...")
        workspace = settings.workspace.root_dir
        if workspace is None:
            checks = [_provider_fail("provider.github_copilot.credentials", "Workspace is not resolved.", settings)]
            _completed(sink, checks[0])
            return checks
        provider_path = Path(workspace) / PROVIDER_TOKEN_CACHE_RELATIVE_PATH
        oauth_path = Path(workspace) / TOKEN_CACHE_RELATIVE_PATH
        provider = _read_json(provider_path)
        oauth = _read_json(oauth_path)
        provider_valid = _provider_token_valid(provider, self.clock())
        oauth_valid = _oauth_token_valid(oauth, self.clock())
        if not provider_valid:
            fixes = [
                DoctorFix(
                    description="Refresh the cached Copilot provider token from the existing GitHub login.",
                    repair_action="provider.refresh_copilot_token",
                )
            ] if oauth_valid else [
                DoctorFix(
                    description="Configure GitHub Copilot authentication.",
                    command=f"fsq-agent init --platform {settings.harness.platform} --provider github_copilot",
                    verification_command=f"fsq-agent doctor --platform {settings.harness.platform} --mode dynamic --non-interactive",
                )
            ]
            checks = [
                DiagnosticProbeResult(
                    id="provider.github_copilot.credentials",
                    category="Provider",
                    status="fail",
                    summary="GitHub Copilot cached credentials are not ready.",
                    affected_targets=["dynamic", "ai_assertion"],
                    fixes=fixes,
                    metadata={"provider_token": "missing_or_expired", "oauth_token": "ready" if oauth_valid else "missing_or_expired"},
                )
            ]
            _completed(sink, checks[0])
            return checks
        plan = provider.get("plan")
        base_url = COPILOT_BASE_URLS.get(plan, "https://api.githubcopilot.com")
        credential = DiagnosticProbeResult(
                id="provider.github_copilot.credentials",
                category="Provider",
                status="pass",
                summary="GitHub Copilot cached credentials are ready.",
                affected_targets=["dynamic", "ai_assertion"],
                metadata={"plan": plan},
            )
        _completed(sink, credential)
        _started(sink, "provider.github_copilot.endpoint", "Checking the GitHub Copilot endpoint...")
        endpoint = _reachability("provider.github_copilot.endpoint", base_url, timeout_seconds, settings, self.http_get)
        _completed(sink, endpoint)
        return [credential, endpoint]

    def _probe_azure(
        self,
        settings: Settings,
        timeout_seconds: float,
        sink: DoctorProgressSink | None,
    ) -> list[DiagnosticProbeResult]:
        checks: list[DiagnosticProbeResult] = []
        _started(sink, "provider.azure_openai.configuration", "Checking Azure OpenAI configuration...")
        base_url = settings.openai_agents.base_url.strip()
        model = settings.openai_agents.model.strip()
        key = self.environ.get("AZURE_OPENAI_API_KEY")
        missing: list[str] = []
        if not base_url.endswith("/openai/v1/"):
            missing.append("AZURE_OPENAI_BASE_URL")
        if not model:
            missing.append("AZURE_OPENAI_MODEL")
        if not key or key.lower().startswith("replace-with"):
            missing.append("AZURE_OPENAI_API_KEY")
        if missing:
            checks.append(
                DiagnosticProbeResult(
                    id="provider.azure_openai.configuration",
                    category="Provider",
                    status="fail",
                    summary="Azure OpenAI local configuration is incomplete or invalid.",
                    affected_targets=["dynamic", "ai_assertion"],
                    fixes=[
                        DoctorFix(
                            description="Configure Azure OpenAI values through init.",
                            command=f"fsq-agent init --platform {settings.harness.platform} --provider azure_openai",
                            environment_variable=name,
                        )
                        for name in missing
                    ],
                    metadata={"missing_or_invalid": missing},
                )
            )
            _completed(sink, checks[-1])
            return checks
        checks.append(
            DiagnosticProbeResult(
                id="provider.azure_openai.configuration",
                category="Provider",
                status="pass",
                summary="Azure OpenAI endpoint, model, and API key are configured.",
                affected_targets=["dynamic", "ai_assertion"],
                metadata={"api_key": "set", "inference_authorization": "not_tested"},
            )
        )
        _completed(sink, checks[-1])
        _started(sink, "provider.azure_openai.endpoint", "Checking the Azure OpenAI endpoint...")
        checks.append(_reachability("provider.azure_openai.endpoint", base_url, timeout_seconds, settings, self.http_get))
        _completed(sink, checks[-1])
        return checks


def _started(sink: DoctorProgressSink | None, check_id: str, summary: str) -> None:
    _emit(
        sink,
        DoctorProgressEvent(
            event_type="check_started",
            phase="Provider",
            check_id=check_id,
            summary=summary,
        ),
    )


def _completed(sink: DoctorProgressSink | None, result: DiagnosticProbeResult) -> None:
    check = DoctorCheckResult.model_validate(result.model_dump())
    _emit(
        sink,
        DoctorProgressEvent(
            event_type="check_completed",
            phase="Provider",
            check_id=check.id,
            status=check.status,
            summary=check.summary,
            check=check,
        ),
    )


def _emit(sink: DoctorProgressSink | None, event: DoctorProgressEvent) -> None:
    if sink is None:
        return
    try:
        sink(event)
    except Exception:
        pass


def _reachability(
    check_id: str,
    url: str,
    timeout: float,
    settings: Settings,
    http_get: Callable[..., Any],
) -> DiagnosticProbeResult:
    sanitized = _sanitize_url(url)
    try:
        response = http_get(url, timeout=timeout, follow_redirects=False)
        status_class = response.status_code // 100
        if status_class >= 5:
            raise httpx.HTTPStatusError("server error", request=response.request, response=response)
    except httpx.HTTPError as exc:
        return DiagnosticProbeResult(
            id=check_id,
            category="Provider",
            status="fail",
            summary=f"Provider endpoint is not reachable ({type(exc).__name__}).",
            affected_targets=["dynamic", "ai_assertion"],
            fixes=[DoctorFix(description="Check DNS, proxy, firewall, TLS, and the configured provider endpoint.")],
            metadata={"endpoint": sanitized},
        )
    return DiagnosticProbeResult(
        id=check_id,
        category="Provider",
        status="pass",
        summary="Provider endpoint is reachable without an inference request.",
        affected_targets=["dynamic", "ai_assertion"],
        metadata={"endpoint": sanitized, "http_status_class": f"{status_class}xx"},
    )


def _read_json(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _provider_token_valid(value: dict[str, object], now: float) -> bool:
    token = value.get("token")
    expires = value.get("expires_at")
    plan = value.get("plan")
    return bool(
        isinstance(token, str)
        and token
        and isinstance(expires, int | float)
        and expires > now + 60
        and plan in COPILOT_BASE_URLS
    )


def _oauth_token_valid(value: dict[str, object], now: float) -> bool:
    token = value.get("access_token")
    expires = value.get("expires_at")
    return bool(
        isinstance(token, str)
        and token
        and (expires is None or isinstance(expires, int | float) and expires > now + 60)
    )


def _sanitize_url(url: str) -> str:
    parsed = urlsplit(url)
    host = parsed.hostname or ""
    if parsed.port:
        host = f"{host}:{parsed.port}"
    return urlunsplit((parsed.scheme, host, parsed.path, "", ""))


def _provider_fail(check_id: str, summary: str, settings: Settings) -> DiagnosticProbeResult:
    return DiagnosticProbeResult(
        id=check_id,
        category="Provider",
        status="fail",
        summary=summary,
        affected_targets=["dynamic", "ai_assertion"],
        fixes=[
            DoctorFix(
                description="Initialize provider authentication.",
                command=f"fsq-agent init --platform {settings.harness.platform} --provider github_copilot",
            )
        ],
    )
