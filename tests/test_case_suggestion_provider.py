# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import pytest

from fsq_agent.agent_engine import ModelRequest, ModelResult
from fsq_agent.ai_services import CaseSuggestionAnalysis, CaseSuggestionAnalyzer
from fsq_agent.models import ConfigurationError


class _Session:
    provider = "test"
    model = "test-model"

    def __init__(self, output: str) -> None:
        self.output = output
        self.calls = []
        self.closed = False

    def complete_sync(self, request: ModelRequest) -> ModelResult:
        self.calls.append(request)
        return ModelResult(text=self.output)

    def close_sync(self) -> None:
        self.closed = True


def test_case_suggestion_analyzer_makes_one_tool_free_request_and_closes_session() -> None:
    session = _Session('{"summary":"Improve target","suggestions":[{"kind":"target","message":"Use semantic text"}],"candidate_case_yaml":null}')
    analyzer = CaseSuggestionAnalyzer(session)

    result = analyzer.analyze(
        parsed_case={"config": {"schemaVersion": "fsq.ai-test/v1"}, "commands": []},
        execution_report={"summary": {"status": "failed"}},
    )

    assert result.summary == "Improve target"
    assert result.suggestions == ({"kind": "target", "message": "Use semantic text"},)
    assert len(session.calls) == 1
    assert isinstance(session.calls[0], ModelRequest)
    assert session.calls[0].instructions is None
    assert session.closed is True


def test_case_suggestion_analyzer_rejects_invalid_response_and_closes_session() -> None:
    session = _Session("not json")

    with pytest.raises(ConfigurationError, match="invalid JSON"):
        CaseSuggestionAnalyzer(session).analyze(parsed_case={"config": {}, "commands": []}, execution_report={})

    assert session.closed is True


@pytest.mark.parametrize(
    "suggestion",
    [
        {"kind": " ", "message": "Useful"},
        {"kind": "target", "message": " "},
    ],
)
def test_case_suggestion_analyzer_rejects_blank_suggestion_fields(suggestion: dict[str, str]) -> None:
    import json

    output = json.dumps({"summary": "Result", "suggestions": [suggestion], "candidate_case_yaml": None})
    session = _Session(output)

    with pytest.raises(ConfigurationError, match="invalid suggestion"):
        CaseSuggestionAnalyzer(session).analyze(parsed_case={"config": {}, "commands": []}, execution_report={})

    assert session.closed is True


def test_suggestion_result_snapshots_and_freezes_entries() -> None:
    source = {"kind": "target", "message": "Use the search field"}
    result = CaseSuggestionAnalysis(summary="Improve target", suggestions=(source,))
    source["message"] = "Changed after construction"
    assert result.suggestions[0]["message"] == "Use the search field"
    with pytest.raises(TypeError):
        result.suggestions[0]["message"] = "Caller mutation"
