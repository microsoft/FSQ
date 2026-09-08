# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from __future__ import annotations

import json
import mimetypes
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fsq_agent.agent_engine import ImageContent, Message, ModelRequest, TextContent
from fsq_agent.models import AIAssertionRequest, AIAssertionResult, ConfigurationError

if TYPE_CHECKING:
    from fsq_agent.agent_engine import ModelResult
    from fsq_agent.providers import ModelProviderSession


class AIAssertionEvaluator:
    def __init__(self, session: ModelProviderSession) -> None:
        self.session = session

    def evaluate(self, request: AIAssertionRequest) -> AIAssertionResult:
        started = time.perf_counter()
        try:
            response = self.session.complete_sync(self._build_input(request))
            payload = self._parse_response(response)
            passed = payload["passed"]
            status = "passed" if passed else "failed"
            explanation = str(payload.get("explanation") or payload.get("summary") or "AI assertion evaluated.")
            confidence = payload.get("confidence")
            return AIAssertionResult(
                status=status,
                passed=passed,
                explanation=explanation,
                confidence=confidence if isinstance(confidence, int | float) else None,
                provider=self.session.provider,
                model=self.session.model,
                latency_ms=int((time.perf_counter() - started) * 1000),
                token_usage=self._token_usage(response),
                artifact_refs=[request.screenshot_artifact_ref] if request.screenshot_artifact_ref else [],
                metadata={"provider_metadata": self.session.metadata},
            )
        except Exception as exc:
            if isinstance(exc, ConfigurationError):
                raise
            return AIAssertionResult(
                status="error",
                passed=False,
                explanation="AI assertion evaluation failed.",
                provider=self.session.provider,
                model=self.session.model,
                latency_ms=int((time.perf_counter() - started) * 1000),
                artifact_refs=[request.screenshot_artifact_ref] if request.screenshot_artifact_ref else [],
                error=str(exc) or exc.__class__.__name__,
                metadata={"provider_metadata": self.session.metadata},
            )
        finally:
            self.session.close_sync()

    def close(self) -> None:
        self.session.close_sync()

    def _build_input(self, request: AIAssertionRequest) -> ModelRequest:
        text = (
            "Evaluate this explicit platform visual assertion. "
            "Return JSON only with keys passed (boolean), explanation (short string), and confidence (0 to 1).\n\n"
            f"Platform: {request.platform}\n"
            f"Prompt: {request.prompt}\n"
            f"Context: {json.dumps(request.ui_context, ensure_ascii=False, default=str)}"
        )
        content: list[TextContent | ImageContent] = [TextContent(text)]
        screenshot_path = Path(request.screenshot_path) if request.screenshot_path else None
        if screenshot_path and screenshot_path.exists():
            mime_type = mimetypes.guess_type(str(screenshot_path))[0] or "image/png"
            content.append(ImageContent(data=screenshot_path.read_bytes(), mime_type=mime_type))
        return ModelRequest(input=(Message(content=tuple(content)),))

    def _parse_response(self, response: ModelResult) -> dict[str, Any]:
        text = response.text
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            payload = self._json_object_from_text(text)
        if not isinstance(payload, dict):
            return {"passed": False, "explanation": text[:1000] or "AI assertion returned no parseable verdict."}
        if not isinstance(payload.get("passed"), bool):
            raise TypeError("AI assertion returned an invalid boolean verdict.")
        return payload

    def _json_object_from_text(self, text: str) -> dict[str, Any]:
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            return {"passed": False, "explanation": text[:1000] or "AI assertion returned no parseable verdict."}
        try:
            payload = json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            return {"passed": False, "explanation": text[:1000] or "AI assertion returned no parseable verdict."}
        return payload if isinstance(payload, dict) else {"passed": False, "explanation": text[:1000]}

    def _token_usage(self, response: ModelResult) -> dict[str, int]:
        usage = response.usage
        if usage is None:
            return {}
        return {"input_tokens": usage.input_tokens, "output_tokens": usage.output_tokens, "total_tokens": usage.total_tokens}
