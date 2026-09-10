# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import asyncio
import json
from dataclasses import replace
from pathlib import Path

import pytest

from fsq_agent.agent_engine import EngineError, ImageContent, ModelRequest, ModelResult, TextContent, TokenUsage
from fsq_agent.ai_services import AIAssertionEvaluator
from fsq_agent.models import AIAssertionRequest, ConfigurationError, HarnessArtifactRef


class _Session:
    provider = "azure_openai"
    model = "selected-model"

    def __init__(self, response: ModelResult | BaseException, cleanup_error: BaseException | None = None) -> None:
        self.response = response
        self.cleanup_error = cleanup_error
        self.metadata = {"endpoint_family": "azure_openai"}
        self.requests: list[ModelRequest] = []
        self.closed = False

    def complete_sync(self, request: ModelRequest) -> ModelResult:
        self.requests.append(request)
        if isinstance(self.response, BaseException):
            raise self.response
        assert request.output is not None
        try:
            parsed = request.output.parse(self.response.text)
        except (ValueError, TypeError):
            raise EngineError("invalid_output", "Invalid model output.") from None
        return replace(self.response, parsed_output=parsed)

    def close_sync(self) -> None:
        self.closed = True
        if self.cleanup_error is not None:
            raise self.cleanup_error


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


@pytest.mark.parametrize("text", ["", " \n", "not JSON", "[]", "null", '{"passed":false,"explanation":"Absent"}'])
def test_unparseable_assertion_is_error_not_negative_verdict(text: str, tmp_path: Path) -> None:
    session = _Session(ModelResult(text=text))
    result = AIAssertionEvaluator(session).evaluate(AIAssertionRequest(platform="web", prompt="Check", screenshot_path=tmp_path / "absent.png"))
    assert result.status == "error"
    assert result.metadata["engine_error"]["category"] == "invalid_output"
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
    session = _Session(ModelResult(text=json.dumps({"passed": verdict, "explanation": "Model supplied an invalid verdict.", "confidence": None})))
    result = AIAssertionEvaluator(session).evaluate(AIAssertionRequest(platform="web", prompt="Check"))
    assert result.status == "error"
    assert result.passed is False
    assert result.error == "AI assertion evaluation failed."
    assert result.metadata["engine_error"] == {"category": "invalid_output"}
    assert len(session.requests) == 1
    assert session.closed


@pytest.mark.parametrize("verdict", [False, True])
@pytest.mark.parametrize("wrapped", [False, True])
def test_assertion_accepts_real_booleans_but_rejects_markdown(verdict: bool, wrapped: bool) -> None:
    text = json.dumps({"passed": verdict, "explanation": "Visible" if verdict else "Absent", "confidence": 0.8})
    session = _Session(ModelResult(text=f"```json\n{text}\n```" if wrapped else text))
    result = AIAssertionEvaluator(session).evaluate(AIAssertionRequest(platform="web", prompt="Check"))
    assert result.status == ("error" if wrapped else "passed" if verdict else "failed")
    assert result.passed is (verdict and not wrapped)
    if not wrapped:
        assert result.confidence == 0.8
        assert result.error is None
    assert session.closed


@pytest.mark.parametrize("confidence", [None, 0, 1, 0.5])
def test_assertion_normalizes_text_and_accepts_nullable_finite_confidence(confidence):
    session = _Session(ModelResult(json.dumps({"passed": False, "explanation": " Absent \n", "confidence": confidence})))
    result = AIAssertionEvaluator(session).evaluate(AIAssertionRequest(platform="web", prompt="Check"))
    assert result.status == "failed"
    assert result.explanation == "Absent"
    assert result.confidence == confidence


@pytest.mark.parametrize(
    "field,value",
    [
        ("explanation", " "),
        ("explanation", 1),
        ("explanation", None),
        ("confidence", True),
        ("confidence", "0.5"),
        ("confidence", -0.1),
        ("confidence", 1.1),
        ("confidence", float("nan")),
        ("confidence", float("inf")),
        ("confidence", float("-inf")),
        ("extra", "private-content"),
    ],
)
def test_assertion_rejects_invalid_fields(field, value):
    payload = {"passed": True, "explanation": "Visible", "confidence": None, field: value}
    result = AIAssertionEvaluator(_Session(ModelResult(json.dumps(payload)))).evaluate(AIAssertionRequest(platform="web", prompt="Check"))
    assert result.status == "error"
    assert result.metadata["engine_error"] == {"category": "invalid_output"}
    assert "private-content" not in result.model_dump_json()


@pytest.mark.parametrize("field", ["passed", "explanation", "confidence"])
def test_assertion_requires_every_field(field):
    payload = {"passed": True, "explanation": "Visible", "confidence": None}
    del payload[field]
    result = AIAssertionEvaluator(_Session(ModelResult(json.dumps(payload)))).evaluate(AIAssertionRequest(platform="web", prompt="Check"))
    assert result.status == "error"
    assert result.metadata["engine_error"]["category"] == "invalid_output"


@pytest.mark.parametrize("category,reason", [("refusal", None), ("incomplete", "content_filter"), ("configuration", None), ("authentication", None), ("rate_limit", None), ("timeout", None)])
def test_assertion_preserves_safe_engine_failure_metadata(category, reason):
    session = _Session(EngineError(category, "private-model-content", reason=reason))
    artifact = HarnessArtifactRef(artifact_id="screen", kind="screenshot", path=Path("artifacts/screen.png"))
    result = AIAssertionEvaluator(session).evaluate(AIAssertionRequest(platform="web", prompt="Check", screenshot_artifact_ref=artifact))
    assert result.status == "error"
    assert result.metadata["engine_error"] == {"category": category, **({"reason": reason} if reason is not None else {})}
    assert result.metadata["provider_metadata"] == session.metadata
    assert result.artifact_refs == [artifact]
    assert result.latency_ms >= 0
    assert "private-model-content" not in result.model_dump_json()


@pytest.mark.parametrize("primary", [EngineError("invalid_output", "private-output"), RuntimeError("private-runtime")])
@pytest.mark.parametrize("cleanup", [RuntimeError("cleanup failure"), asyncio.CancelledError("cleanup cancellation")])
def test_assertion_converted_error_wins_over_cleanup(primary, cleanup):
    session = _Session(primary, cleanup)
    result = AIAssertionEvaluator(session).evaluate(AIAssertionRequest(platform="web", prompt="Check"))
    assert result.status == "error"
    assert result.error == "AI assertion evaluation failed."
    assert session.closed


@pytest.mark.parametrize("primary", [asyncio.CancelledError(), KeyboardInterrupt(), ConfigurationError("Missing provider")])
def test_assertion_primary_interruption_or_configuration_wins_over_cleanup(primary):
    session = _Session(primary, RuntimeError("cleanup failure"))
    with pytest.raises(type(primary)) as failure:
        AIAssertionEvaluator(session).evaluate(AIAssertionRequest(platform="web", prompt="Check"))
    assert failure.value is primary
    assert session.closed


def test_assertion_cleanup_only_failure_is_not_success():
    session = _Session(ModelResult('{"passed":true,"explanation":"Visible","confidence":null}'), EngineError("cleanup", "Cleanup failed."))
    with pytest.raises(EngineError) as failure:
        AIAssertionEvaluator(session).evaluate(AIAssertionRequest(platform="web", prompt="Check"))
    assert failure.value.category == "cleanup"


@pytest.mark.parametrize("passed", [False, True])
@pytest.mark.parametrize("cleanup", [EngineError("cleanup", "Cleanup failed."), asyncio.CancelledError(), KeyboardInterrupt()])
def test_assertion_cleanup_failure_propagates_inside_unrelated_except(passed, cleanup):
    session = _Session(ModelResult(json.dumps({"passed": passed, "explanation": "Checked", "confidence": None})), cleanup)
    try:
        int("unrelated handled error")
    except ValueError:
        with pytest.raises(type(cleanup)) as failure:
            AIAssertionEvaluator(session).evaluate(AIAssertionRequest(platform="web", prompt="Check"))
        assert failure.value is cleanup
    assert session.closed


def test_assertion_requires_parsed_object_even_when_text_is_valid():
    class UnparsedSession(_Session):
        def complete_sync(self, request):
            return self.response

    session = UnparsedSession(ModelResult('{"passed":true,"explanation":"Visible","confidence":null}'))
    result = AIAssertionEvaluator(session).evaluate(AIAssertionRequest(platform="web", prompt="Check"))
    assert result.status == "error"
    assert result.metadata["engine_error"]["category"] == "invalid_output"
