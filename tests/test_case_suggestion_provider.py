# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import asyncio
import json

import pytest

from fsq_agent.agent_engine import EngineError, ModelRequest, ModelResult
from fsq_agent.ai_services import CaseSuggestionAnalysis, CaseSuggestionAnalyzer


class _Session:
    provider = "test"
    model = "test-model"

    def __init__(self, output: str) -> None:
        self.output = output
        self.calls = []
        self.closed = False

    def complete_sync(self, request: ModelRequest) -> ModelResult:
        self.calls.append(request)
        assert request.output is not None
        try:
            parsed = request.output.parse(self.output)
        except (ValueError, TypeError):
            raise EngineError("invalid_output", "Invalid model output.") from None
        return ModelResult(text=self.output, parsed_output=parsed)

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

    with pytest.raises(EngineError) as failure:
        CaseSuggestionAnalyzer(session).analyze(parsed_case={"config": {}, "commands": []}, execution_report={})

    assert failure.value.category == "invalid_output"
    assert session.closed is True


@pytest.mark.parametrize(
    "suggestion",
    [
        {"kind": " ", "message": "Useful"},
        {"kind": "target", "message": " "},
    ],
)
def test_case_suggestion_analyzer_rejects_blank_suggestion_fields(suggestion: dict[str, str]) -> None:
    output = json.dumps({"summary": "Result", "suggestions": [suggestion], "candidate_case_yaml": None})
    session = _Session(output)

    with pytest.raises(EngineError) as failure:
        CaseSuggestionAnalyzer(session).analyze(parsed_case={"config": {}, "commands": []}, execution_report={})

    assert failure.value.category == "invalid_output"
    assert session.closed is True


def test_suggestion_result_snapshots_and_freezes_entries() -> None:
    source = {"kind": "target", "message": "Use the search field"}
    result = CaseSuggestionAnalysis(summary="Improve target", suggestions=(source,))
    source["message"] = "Changed after construction"
    assert result.suggestions[0]["message"] == "Use the search field"
    with pytest.raises(TypeError):
        result.suggestions[0]["message"] = "Caller mutation"


@pytest.mark.parametrize(
    "output",
    [
        "",
        " \n",
        "null",
        "[]",
        "{}",
        "```json\n{}\n```",
        '{"summary":"Good","suggestions":[]}',
        '{"summary":1,"suggestions":[],"candidate_case_yaml":null}',
        '{"summary":" ","suggestions":[],"candidate_case_yaml":null}',
        '{"summary":"Good","suggestions":{},"candidate_case_yaml":null}',
        '{"summary":"Good","suggestions":[],"candidate_case_yaml":" "}',
        '{"summary":"Good","suggestions":[],"candidate_case_yaml":1}',
        '{"summary":"Good","suggestions":[],"candidate_case_yaml":null,"extra":true}',
        '{"summary":"Good","suggestions":[{"kind":1,"message":"x"}],"candidate_case_yaml":null}',
        '{"summary":"Good","suggestions":[{"kind":"x","message":"x","extra":1}],"candidate_case_yaml":null}',
    ],
)
def test_suggestion_rejects_invalid_contract(output):
    session = _Session(output)
    with pytest.raises(EngineError) as failure:
        CaseSuggestionAnalyzer(session).analyze(parsed_case={}, execution_report={})
    assert failure.value.category == "invalid_output"
    assert session.closed
    assert len(session.calls) == 1


@pytest.mark.parametrize("candidate", [None, "  not-yaml-yet \n"])
def test_suggestion_normalization_and_empty_suggestions(candidate):
    session = _Session(json.dumps({"summary": " Good \n", "suggestions": [], "candidate_case_yaml": candidate}))
    result = CaseSuggestionAnalyzer(session).analyze(parsed_case={}, execution_report={})
    assert result.summary == "Good"
    assert result.suggestions == ()
    assert result.candidate_case_yaml == (candidate.strip() if candidate else None)


@pytest.mark.parametrize("primary", [EngineError("invalid_output", "Invalid output."), EngineError("timeout", "Timeout."), asyncio.CancelledError(), KeyboardInterrupt()])
def test_suggestion_primary_failure_wins_over_cleanup(primary):
    class FailingSession(_Session):
        def complete_sync(self, request):
            raise primary

        def close_sync(self):
            self.closed = True
            raise RuntimeError("cleanup failure")

    session = FailingSession("")
    with pytest.raises(type(primary)) as failure:
        CaseSuggestionAnalyzer(session).analyze(parsed_case={}, execution_report={})
    assert failure.value is primary
    assert session.closed


@pytest.mark.parametrize("cleanup", [EngineError("cleanup", "Cleanup failed."), asyncio.CancelledError(), KeyboardInterrupt()])
def test_suggestion_cleanup_failure_propagates_inside_unrelated_except(cleanup):
    class FailingCloseSession(_Session):
        def close_sync(self):
            self.closed = True
            raise cleanup

    session = FailingCloseSession('{"summary":"Good","suggestions":[],"candidate_case_yaml":null}')
    try:
        int("unrelated handled error")
    except ValueError:
        with pytest.raises(type(cleanup)) as failure:
            CaseSuggestionAnalyzer(session).analyze(parsed_case={}, execution_report={})
        assert failure.value is cleanup
    assert session.closed


def test_suggestion_requires_parsed_object():
    class UnparsedSession(_Session):
        def complete_sync(self, request):
            return ModelResult(self.output)

    session = UnparsedSession('{"summary":"Good","suggestions":[],"candidate_case_yaml":null}')
    with pytest.raises(EngineError) as failure:
        CaseSuggestionAnalyzer(session).analyze(parsed_case={}, execution_report={})
    assert failure.value.category == "invalid_output"
