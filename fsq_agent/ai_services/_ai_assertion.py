# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from __future__ import annotations

import json
import mimetypes
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field

from fsq_agent.agent_engine import EngineError, ImageContent, Message, ModelRequest, OutputContract, TextContent
from fsq_agent.models import AIAssertionRequest, AIAssertionResult, ConfigurationError

if TYPE_CHECKING:
    from fsq_agent.agent_engine import ModelResult
    from fsq_agent.providers import ModelProviderSession


class _AssertionOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True, allow_inf_nan=False)

    passed: bool
    explanation: str = Field(min_length=1)
    confidence: float | None = Field(ge=0, le=1)


def _assertion_output(response: ModelResult) -> _AssertionOutput:
    if not isinstance(response.parsed_output, _AssertionOutput):
        raise EngineError("invalid_output", "Model provider returned an invalid assertion output.")
    return response.parsed_output


class AIAssertionEvaluator:
    def __init__(self, session: ModelProviderSession) -> None:
        self.session = session

    def evaluate(self, request: AIAssertionRequest) -> AIAssertionResult:
        started = time.perf_counter()
        failed = False
        try:
            response = self.session.complete_sync(self._build_input(request))
            payload = _assertion_output(response)
            passed = payload.passed
            status = "passed" if passed else "failed"
            return AIAssertionResult(
                status=status,
                passed=passed,
                explanation=payload.explanation,
                confidence=payload.confidence,
                provider=self.session.provider,
                model=self.session.model,
                latency_ms=int((time.perf_counter() - started) * 1000),
                token_usage=self._token_usage(response),
                artifact_refs=[request.screenshot_artifact_ref] if request.screenshot_artifact_ref else [],
                metadata={"provider_metadata": self.session.metadata},
            )
        except Exception as exc:
            failed = True
            if isinstance(exc, ConfigurationError):
                raise
            metadata: dict[str, Any] = {"provider_metadata": self.session.metadata}
            if isinstance(exc, EngineError):
                metadata["engine_error"] = {"category": exc.category}
                if exc.reason is not None:
                    metadata["engine_error"]["reason"] = exc.reason
            return AIAssertionResult(
                status="error",
                passed=False,
                explanation="AI assertion evaluation failed.",
                provider=self.session.provider,
                model=self.session.model,
                latency_ms=int((time.perf_counter() - started) * 1000),
                artifact_refs=[request.screenshot_artifact_ref] if request.screenshot_artifact_ref else [],
                error="AI assertion evaluation failed.",
                metadata=metadata,
            )
        except BaseException:
            failed = True
            raise
        finally:
            try:
                self.session.close_sync()
            except BaseException:
                if not failed:
                    raise

    def close(self) -> None:
        self.session.close_sync()

    def _build_input(self, request: AIAssertionRequest) -> ModelRequest[_AssertionOutput]:
        text = (
            "Evaluate this explicit platform visual assertion. "
            "Return JSON only with keys passed (boolean), explanation (nonblank string), and confidence (0 to 1 or null).\n\n"
            f"Platform: {request.platform}\n"
            f"Prompt: {request.prompt}\n"
            f"Context: {json.dumps(request.ui_context, ensure_ascii=False, default=str)}"
        )
        content: list[TextContent | ImageContent] = [TextContent(text)]
        screenshot_path = Path(request.screenshot_path) if request.screenshot_path else None
        if screenshot_path and screenshot_path.exists():
            mime_type = mimetypes.guess_type(str(screenshot_path))[0] or "image/png"
            content.append(ImageContent(data=screenshot_path.read_bytes(), mime_type=mime_type))
        return ModelRequest(
            input=(Message(content=tuple(content)),),
            output=OutputContract(name="visual_assertion", schema=_AssertionOutput.model_json_schema(), parse=_AssertionOutput.model_validate_json),
        )

    def _token_usage(self, response: ModelResult) -> dict[str, int]:
        usage = response.usage
        if usage is None:
            return {}
        return {"input_tokens": usage.input_tokens, "output_tokens": usage.output_tokens, "total_tokens": usage.total_tokens}
