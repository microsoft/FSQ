# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from __future__ import annotations

import json
from dataclasses import dataclass
from types import MappingProxyType
from typing import TYPE_CHECKING, Any

from fsq_agent.agent_engine import ModelRequest
from fsq_agent.models import ConfigurationError

if TYPE_CHECKING:
    from collections.abc import Mapping

    from fsq_agent.providers import ModelProviderSession


@dataclass(frozen=True)
class CaseSuggestionAnalysis:
    summary: str
    suggestions: tuple[Mapping[str, str], ...]
    candidate_case_yaml: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "suggestions", tuple(MappingProxyType(dict(item)) for item in self.suggestions))


class CaseSuggestionAnalyzer:
    """Analyze completed deterministic execution facts without action tools."""

    def __init__(self, session: ModelProviderSession) -> None:
        self._session = session

    def analyze(self, *, parsed_case: dict[str, Any], execution_report: dict[str, Any]) -> CaseSuggestionAnalysis:
        try:
            response = self._session.complete_sync(ModelRequest(input=_analysis_input(parsed_case, execution_report)))
            payload = _parse_json_object(response.text)
            return _validate_analysis(payload)
        finally:
            self._session.close_sync()


def _analysis_input(parsed_case: dict[str, Any], execution_report: dict[str, Any]) -> str:
    return (
        "Analyze one completed deterministic FSQ Case run. You are read-only: do not request or describe another UI run, "
        "and do not change the reported execution status or facts. Return JSON only with: summary (short string), "
        "suggestions (array of objects with kind and message strings), and candidate_case_yaml (a complete FSQ YAML string or null). "
        "A candidate must preserve the source platform and should be omitted unless the supplied facts justify a concrete improvement.\n\n"
        f"PARSED_CASE:\n{json.dumps(parsed_case, ensure_ascii=False, default=str)}"
        f"\n\nEXECUTION_REPORT:\n{json.dumps(execution_report, ensure_ascii=False, default=str)}"
    )


def _parse_json_object(text: str) -> dict[str, Any]:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ConfigurationError("Case suggestion analysis returned invalid JSON.") from exc
    if not isinstance(payload, dict):
        raise ConfigurationError("Case suggestion analysis returned an invalid result.")
    return payload


def _validate_analysis(payload: dict[str, Any]) -> CaseSuggestionAnalysis:
    summary = payload.get("summary")
    suggestions = payload.get("suggestions")
    candidate = payload.get("candidate_case_yaml")
    if not isinstance(summary, str) or not summary.strip() or not isinstance(suggestions, list):
        raise ConfigurationError("Case suggestion analysis returned an invalid result.")
    normalized: list[dict[str, str]] = []
    for item in suggestions:
        if not isinstance(item, dict) or not isinstance(item.get("kind"), str) or not isinstance(item.get("message"), str):
            raise ConfigurationError("Case suggestion analysis returned an invalid suggestion.")
        kind = item["kind"].strip()
        message = item["message"].strip()
        if not kind or not message:
            raise ConfigurationError("Case suggestion analysis returned an invalid suggestion.")
        normalized.append({"kind": kind, "message": message})
    if candidate is not None and (not isinstance(candidate, str) or not candidate.strip()):
        raise ConfigurationError("Case suggestion analysis returned an invalid candidate Case.")
    return CaseSuggestionAnalysis(summary=summary.strip(), suggestions=tuple(normalized), candidate_case_yaml=candidate.strip() if candidate else None)
