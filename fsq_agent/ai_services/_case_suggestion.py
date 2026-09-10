# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from __future__ import annotations

import json
from dataclasses import dataclass
from types import MappingProxyType
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field

from fsq_agent.agent_engine import EngineError, ModelRequest, OutputContract

if TYPE_CHECKING:
    from collections.abc import Mapping

    from fsq_agent.agent_engine import ModelResult
    from fsq_agent.providers import ModelProviderSession


class _SuggestionEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)

    kind: str = Field(min_length=1)
    message: str = Field(min_length=1)


class _SuggestionOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)

    summary: str = Field(min_length=1)
    suggestions: list[_SuggestionEntry]
    candidate_case_yaml: str | None = Field(min_length=1)


def _suggestion_output(response: ModelResult) -> _SuggestionOutput:
    if not isinstance(response.parsed_output, _SuggestionOutput):
        raise EngineError("invalid_output", "Model provider returned an invalid suggestion output.")
    return response.parsed_output


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
        failed = False
        try:
            response = self._session.complete_sync(
                ModelRequest(
                    input=_analysis_input(parsed_case, execution_report),
                    output=OutputContract(name="case_suggestion", schema=_SuggestionOutput.model_json_schema(), parse=_SuggestionOutput.model_validate_json),
                )
            )
            payload = _suggestion_output(response)
            return CaseSuggestionAnalysis(
                summary=payload.summary,
                suggestions=tuple({"kind": item.kind, "message": item.message} for item in payload.suggestions),
                candidate_case_yaml=payload.candidate_case_yaml,
            )
        except BaseException:
            failed = True
            raise
        finally:
            try:
                self._session.close_sync()
            except BaseException:
                if not failed:
                    raise


def _analysis_input(parsed_case: dict[str, Any], execution_report: dict[str, Any]) -> str:
    return (
        "Analyze one completed deterministic FSQ Case run. You are read-only: do not request or describe another UI run, "
        "and do not change the reported execution status or facts. Return JSON only with: summary (short string), "
        "suggestions (array of objects with kind and message strings), and candidate_case_yaml (a complete FSQ YAML string or null). "
        "A candidate must preserve the source platform and must be null unless the supplied facts justify a concrete improvement.\n\n"
        f"PARSED_CASE:\n{json.dumps(parsed_case, ensure_ascii=False, default=str)}"
        f"\n\nEXECUTION_REPORT:\n{json.dumps(execution_report, ensure_ascii=False, default=str)}"
    )
