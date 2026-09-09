# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from __future__ import annotations

import base64
import json
from copy import deepcopy
from typing import TYPE_CHECKING

import httpx
from google.genai._gaos.lib.compat_errors import APIConnectionError, APIResponseValidationError, APITimeoutError

from ._backend import BackendTurn, decode_tool_arguments
from ._contracts import AgentEvent, EngineError, ImageContent, ModelResult, TextContent, TokenUsage
from ._google_gemini_schema import google_schema

if TYPE_CHECKING:
    from ._contracts import AgentRequest, Message, ModelRequest


def google_input(value: str | tuple[Message, ...]) -> list[dict]:
    if isinstance(value, str):
        return [{"type": "user_input", "content": [{"type": "text", "text": value}]}]
    history = []
    for message in value:
        content = []
        for part in message.content:
            if isinstance(part, TextContent):
                content.append({"type": "text", "text": part.text})
            elif isinstance(part, ImageContent) and part.mime_type.startswith("image/"):
                content.append({"type": "image", "mime_type": part.mime_type, "data": base64.b64encode(part.data).decode("ascii")})
            else:
                raise EngineError("configuration", "Unsupported model input content.")
        history.append({"type": "user_input" if message.role == "user" else "model_output", "content": content})
    return history


def google_parameters(model: str, request: ModelRequest | AgentRequest) -> dict:
    result = {"model": model, "input": google_input(request.input), "store": False}
    if request.instructions is not None:
        result["system_instruction"] = request.instructions
    if hasattr(request, "tools"):
        result["tools"] = [{"type": "function", "name": tool.name, "description": tool.description, "parameters": google_schema(tool.parameters_schema)} for tool in request.tools]
        if request.output is not None:
            result["response_format"] = {"type": "text", "mime_type": "application/json", "schema": google_schema(request.output.schema)}
    return result


def google_usage(value: dict | None) -> TokenUsage | None:
    if not isinstance(value, dict):
        return None
    counts = [value.get(name) for name in ("total_input_tokens", "total_output_tokens", "total_tokens")]
    if any(type(count) is not int or count < 0 for count in counts):
        return None
    cached = value.get("total_cached_tokens")
    thoughts = value.get("total_thought_tokens")
    return TokenUsage(
        input_tokens=counts[0],
        output_tokens=counts[1],
        total_tokens=counts[2],
        cached_input_tokens=cached if type(cached) is int and cached >= 0 else None,
        reasoning_tokens=thoughts if type(thoughts) is int and thoughts >= 0 else None,
    )


def _check_failure(response: dict) -> None:
    errors = response.get("errors") or []
    if not isinstance(errors, list):
        raise EngineError("invalid_output", "Model provider returned invalid error information.")
    error_codes = {str(item.get("code", "")).upper() for item in errors if isinstance(item, dict)}
    if error_codes.intersection({"SAFETY", "BLOCKED", "REFUSAL"}):
        raise EngineError("refusal", "Model provider refused the request.")
    if response.get("status") in {"incomplete", "budget_exceeded"} or "MAX_TOKENS" in error_codes:
        raise EngineError("incomplete", "Model provider returned an incomplete response.", reason="incomplete")
    if errors or response.get("status") in {"failed", "cancelled"}:
        raise EngineError("invalid_output", "Model provider returned an invalid terminal response.")


def google_turn(response: dict, *, allow_tools: bool) -> BackendTurn:
    _check_failure(response)
    if response.get("status") not in {"completed", "requires_action"}:
        raise EngineError("invalid_output", "Model provider returned an invalid terminal response.")
    steps = response.get("steps")
    if not isinstance(steps, list):
        raise EngineError("invalid_output", "Model provider returned an invalid response.")
    calls, events, messages = [], [], []
    for step in steps:
        if not isinstance(step, dict):
            raise EngineError("invalid_output", "Model provider returned an invalid step.")
        kind = step.get("type")
        if kind == "thought":
            summary = step.get("summary") or []
            text = "\n".join(part["text"] for part in summary if isinstance(part, dict) and part.get("type") == "text" and isinstance(part.get("text"), str))
            if text:
                events.append(AgentEvent(kind="reasoning_summary", text=text))
            continue
        if kind == "function_call":
            if not allow_tools or not isinstance(step.get("id"), str) or not step["id"] or not isinstance(step.get("name"), str) or not step["name"]:
                raise EngineError("invalid_output", "Model provider returned an invalid function call.")
            arguments = step.get("arguments")
            try:
                encoded = json.dumps(arguments, allow_nan=False) if isinstance(arguments, dict) else arguments
            except (TypeError, ValueError):
                encoded = None
            call = {"call_id": step["id"], "name": step["name"], "arguments": encoded}
            calls.append(call)
            events.append(AgentEvent(kind="tool_called", tool_name=step["name"], call_id=step["id"], arguments=arguments if isinstance(arguments, dict | str) else None))
        elif kind == "model_output":
            if step.get("error"):
                raise EngineError("invalid_output", "Model provider returned an invalid output step.")
            content = step.get("content")
            if not isinstance(content, list) or any(not isinstance(part, dict) or part.get("type") != "text" or not isinstance(part.get("text"), str) for part in content):
                raise EngineError("invalid_output", "Model provider returned unsupported output content.")
            text = "\n".join(part["text"] for part in content)
            messages.append(text)
            events.append(AgentEvent(kind="message", text=text))
        else:
            raise EngineError("invalid_output", "Model provider returned an unsupported step.")
    if len({call["call_id"] for call in calls}) != len(calls) or (response["status"] == "requires_action" and not calls):
        raise EngineError("invalid_output", "Model provider returned invalid continuation state.")
    return BackendTurn(text=messages[-1] if messages else "", calls=tuple(calls), events=tuple(events), usage=google_usage(response.get("usage")))


def google_result(response: dict) -> ModelResult:
    turn = google_turn(response, allow_tools=False)
    return ModelResult(text=turn.text, usage=turn.usage)


def google_error(error: Exception) -> EngineError:
    if isinstance(error, EngineError):
        return error
    status = getattr(error, "status_code", getattr(error, "code", None))
    status = status if type(status) is int else None
    if isinstance(error, TimeoutError | httpx.TimeoutException | APITimeoutError):
        return EngineError("timeout", "Model operation timed out.")
    if isinstance(error, APIResponseValidationError):
        return EngineError("invalid_output", "Google model response is invalid.")
    if isinstance(error, APIConnectionError | httpx.NetworkError):
        return EngineError("unavailable", "Google model service is unavailable.")
    category = {401: "authentication", 403: "authentication", 429: "rate_limit", 404: "unavailable"}.get(status)
    if status is not None and status >= 500:
        category = "unavailable"
    return EngineError(category or "runtime", "Google model operation failed.", status_code=status)


def _same_json_value(left: object, right: object) -> bool:
    if type(left) in {int, float} and type(right) in {int, float}:
        return left == right
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        return left.keys() == right.keys() and all(_same_json_value(value, right[key]) for key, value in left.items())
    if isinstance(left, list):
        return len(left) == len(right) and all(_same_json_value(first, second) for first, second in zip(left, right, strict=True))
    return left == right


class GoogleStream:
    def __init__(self):
        self.steps: dict[int, dict] = {}
        self.stopped: set[int] = set()
        self.arguments: dict[int, str] = {}
        self.completed: dict | None = None

    def add(self, event: dict) -> None:
        kind = event.get("event_type")
        if self.completed is not None:
            raise EngineError("invalid_output", "Model stream continued after its terminal event.")
        if kind == "error":
            _check_failure({"errors": [event.get("error")]})
            raise EngineError("invalid_output", "Model stream failed.")
        if kind == "interaction.completed":
            self.completed = deepcopy(event.get("interaction"))
            if not isinstance(self.completed, dict):
                raise EngineError("invalid_output", "Model stream has invalid terminal information.")
            _check_failure(self.completed)
            return
        if kind == "interaction.status_update":
            _check_failure(event)
            if event.get("status") not in {"in_progress", "queued", "requires_action", "completed"}:
                raise EngineError("invalid_output", "Model stream has an unsupported status.")
            return
        if kind == "interaction.created":
            if isinstance(event.get("interaction"), dict):
                _check_failure(event["interaction"])
            return
        index = event.get("index")
        if type(index) is not int or index < 0:
            raise EngineError("invalid_output", "Model stream has an invalid step index.")
        if kind == "step.start":
            if index in self.steps or not isinstance(event.get("step"), dict):
                raise EngineError("invalid_output", "Model stream has an invalid step start.")
            self.steps[index] = deepcopy(event["step"])
        elif kind == "step.stop":
            if index not in self.steps or index in self.stopped:
                raise EngineError("invalid_output", "Model stream has an invalid step stop.")
            self.stopped.add(index)
        elif kind == "step.delta":
            if index not in self.steps or index in self.stopped:
                raise EngineError("invalid_output", "Model stream has an invalid delta.")
            delta = event.get("delta") or {}
            if delta.get("type") == "arguments_delta":
                self.arguments[index] = self.arguments.get(index, "") + delta.get("arguments", "")
            elif delta.get("type") == "text":
                content = self.steps[index].setdefault("content", [])
                if not content:
                    content.append({"type": "text", "text": ""})
                content[-1]["text"] += delta.get("text", "")
            elif delta.get("type") == "thought_signature":
                self.steps[index]["signature"] = delta.get("signature")
            elif delta.get("type") == "thought_summary":
                content = delta.get("content")
                if not isinstance(content, dict):
                    raise EngineError("invalid_output", "Model stream has an invalid summary.")
                self.steps[index].setdefault("summary", []).append(deepcopy(content))
            else:
                raise EngineError("invalid_output", "Model stream has an unsupported delta.")
        else:
            raise EngineError("invalid_output", "Model stream has an unsupported event.")

    def finish(self) -> dict:
        if not isinstance(self.completed, dict):
            raise EngineError("invalid_output", "Model stream ended without a terminal event.")
        if set(self.steps) != self.stopped or sorted(self.steps) != list(range(len(self.steps))):
            raise EngineError("invalid_output", "Model stream ended with unfinished steps.")
        for index, arguments in self.arguments.items():
            decoded = decode_tool_arguments(arguments)
            self.steps[index]["arguments"] = decoded if decoded is not None else arguments
        if self.completed.get("steps") is None:
            self.completed["steps"] = [self.steps[index] for index in sorted(self.steps)]
        elif self.steps:
            final_steps = self.completed["steps"]
            if not isinstance(final_steps, list) or len(final_steps) != len(self.steps):
                raise EngineError("invalid_output", "Model stream terminal steps do not match its increments.")
            for index, observed in self.steps.items():
                if not _same_json_value(observed, final_steps[index]):
                    raise EngineError("invalid_output", "Model stream terminal steps do not match its increments.")
        return self.completed
