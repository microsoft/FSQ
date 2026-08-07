# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from fsq_agent.models import (
    DiagnosticProbeResult,
    DoctorCheckResult,
    DoctorReadiness,
    DoctorReadinessItem,
)


def normalize_probes(probes: list[DiagnosticProbeResult]) -> list[DoctorCheckResult]:
    return [DoctorCheckResult.model_validate(probe.model_dump()) for probe in probes]


def compute_readiness(mode: str, checks: list[DoctorCheckResult]) -> DoctorReadiness:
    def item(target: str, checked: bool) -> DoctorReadinessItem:
        if not checked:
            return DoctorReadinessItem(status="not_checked")
        blocking = [check.id for check in checks if check.status == "fail" and target in check.affected_targets]
        return DoctorReadinessItem(status="blocked" if blocking else "ready", blocking_check_ids=blocking)

    return DoctorReadiness(
        dynamic_llm=item("dynamic", mode in {"dynamic", "all"}),
        strict_core=item("strict", mode in {"strict", "all"}),
        ai_assertion=item("ai_assertion", mode == "all"),
    )


def sanitize_checks(checks: list[DoctorCheckResult]) -> list[DoctorCheckResult]:
    return [
        check.model_copy(
            update={
                "summary": _sanitize_text(check.summary),
                "metadata": _sanitize_value(check.metadata),
                "fixes": [
                    fix.model_copy(
                        update={
                            "description": _sanitize_text(fix.description),
                            "command": _sanitize_text(fix.command) if fix.command else None,
                            "verification_command": _sanitize_text(fix.verification_command)
                            if fix.verification_command
                            else None,
                            "documentation_url": _sanitize_url(fix.documentation_url)
                            if fix.documentation_url
                            else None,
                        }
                    )
                    for fix in check.fixes
                ],
            }
        )
        for check in checks
    ]


def _sanitize_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): "[redacted]"
            if any(word in str(key).casefold() for word in ("token", "key", "secret", "authorization", "cookie"))
            else _sanitize_value(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_sanitize_value(item) for item in value]
    if isinstance(value, str):
        return _sanitize_text(value)
    return value


def _sanitize_text(value: str) -> str:
    value = re.sub(r"(?i)(bearer|token|api[_ -]?key|authorization|cookie)\s*[:=]\s*\S+", r"\1=[redacted]", value)
    return re.sub(r"https?://[^\s]+", lambda match: _sanitize_url(match.group(0)), value)


def _sanitize_url(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"}:
        return value
    host = parsed.hostname or ""
    if parsed.port:
        host = f"{host}:{parsed.port}"
    return urlunsplit((parsed.scheme, host, parsed.path, "", ""))
