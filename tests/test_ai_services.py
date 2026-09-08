# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import json
from pathlib import Path

import pytest

from fsq_agent.agent_engine import EngineError, ImageContent, ModelRequest, ModelResult, TextContent, TokenUsage
from fsq_agent.ai_services import AIAssertionEvaluator
from fsq_agent.models import AIAssertionRequest, ConfigurationError


class _Session:
    provider = "azure_openai"
    model = "selected-model"

    def __init__(self, response: ModelResult | Exception) -> None:
        self.response = response
        self.metadata = {"endpoint_family": "azure_openai"}
        self.requests: list[ModelRequest] = []
        self.closed = False

    def complete_sync(self, request: ModelRequest) -> ModelResult:
        self.requests.append(request)
        if isinstance(self.response, Exception):
            raise self.response
        return self.response

    def close_sync(self) -> None:
        self.closed = True


def test_visual_assertion_uses_neutral_image_and_preserves_verdict(tmp_path: Path) -> None:
    screenshot = tmp_path / "screen.png"
    screenshot.write_bytes(b"screenshot pixels")
    session = _Session(ModelResult(text='{"passed":true,"explanation":"Visible","confidence":0.8}', usage=TokenUsage(2, 3, 5)))

    result = AIAssertionEvaluator(session).evaluate(AIAssertionRequest(platform="web", prompt="The banner is visible", screenshot_path=screenshot))

    assert result.status == "passed"
    assert result.passed
    assert result.explanation == "Visible"
    assert result.model == "selected-model"
    assert result.token_usage == {"input_tokens": 2, "output_tokens": 3, "total_tokens": 5}
    assert len(session.requests) == 1
    assert not isinstance(session.requests[0].input, str)
    content = session.requests[0].input[0].content
    assert isinstance(content[0], TextContent)
    assert "The banner is visible" in content[0].text
    assert content[1] == ImageContent(data=b"screenshot pixels", mime_type="image/png")
    assert session.closed


@pytest.mark.parametrize("text", ["not JSON", "[]", '{"passed":false,"explanation":"Absent"}'])
def test_unparseable_or_negative_assertion_never_passes(text: str, tmp_path: Path) -> None:
    session = _Session(ModelResult(text=text))
    result = AIAssertionEvaluator(session).evaluate(AIAssertionRequest(platform="web", prompt="Check", screenshot_path=tmp_path / "absent.png"))
    assert result.status == "failed"
    assert not result.passed
    assert result.token_usage == {}
    assert not isinstance(session.requests[0].input, str)
    assert len(session.requests[0].input[0].content) == 1
    assert session.closed


def test_assertion_model_failure_remains_error() -> None:
    session = _Session(EngineError("timeout", "Model operation timed out."))
    result = AIAssertionEvaluator(session).evaluate(AIAssertionRequest(platform="web", prompt="Check"))
    assert result.status == "error"
    assert not result.passed
    assert session.closed


def test_assertion_configuration_error_propagates_and_closes() -> None:
    session = _Session(ConfigurationError("Missing provider"))
    with pytest.raises(ConfigurationError):
        AIAssertionEvaluator(session).evaluate(AIAssertionRequest(platform="web", prompt="Check"))
    assert session.closed


@pytest.mark.parametrize("verdict", ["false", "true", "", 0, 1, 1.0, None, [], [False], {}, {"verdict": False}])
def test_assertion_rejects_non_boolean_verdicts(verdict: object) -> None:
    session = _Session(ModelResult(text=json.dumps({"passed": verdict, "explanation": "Model supplied an invalid verdict."})))
    result = AIAssertionEvaluator(session).evaluate(AIAssertionRequest(platform="web", prompt="Check"))
    assert result.status == "error"
    assert result.passed is False
    assert result.error == "AI assertion returned an invalid boolean verdict."
    assert len(session.requests) == 1
    assert session.closed


@pytest.mark.parametrize("verdict", [False, True])
@pytest.mark.parametrize("wrapped", [False, True])
def test_assertion_preserves_real_booleans_and_existing_json_extraction(verdict: bool, wrapped: bool) -> None:
    text = json.dumps({"passed": verdict, "explanation": "Visible" if verdict else "Absent", "confidence": 0.8})
    session = _Session(ModelResult(text=f"```json\n{text}\n```" if wrapped else text))
    result = AIAssertionEvaluator(session).evaluate(AIAssertionRequest(platform="web", prompt="Check"))
    assert result.status == ("passed" if verdict else "failed")
    assert result.passed is verdict
    assert result.confidence == 0.8
    assert result.error is None
    assert session.closed
