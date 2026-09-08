# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from __future__ import annotations

import asyncio
import base64
import json
from copy import deepcopy
from typing import TYPE_CHECKING

from agents import FunctionTool
from agents.agent_output import AgentOutputSchemaBase
from agents.exceptions import ModelBehaviorError
from agents.strict_schema import ensure_strict_json_schema
from openai import APITimeoutError

from ._contracts import AgentEvent, EngineError, ImageContent, ModelResult, TextContent, TokenUsage, ToolCall, ToolInputFailure

if TYPE_CHECKING:
    from agents.items import ModelResponse, TResponseInputItem
    from agents.stream_events import StreamEvent
    from agents.tool_context import ToolContext
    from agents.usage import Usage

    from ._contracts import Message, OutputContract, ToolBinding


def model_input(value: str | tuple[Message, ...]) -> str | list[TResponseInputItem]:
    if isinstance(value, str):
        return value
    messages = []
    for message in value:
        content = []
        for part in message.content:
            if isinstance(part, TextContent):
                content.append({"type": "input_text", "text": part.text})
            elif isinstance(part, ImageContent):
                if not part.mime_type.startswith("image/"):
                    raise EngineError("configuration", "Image input requires an image MIME type.")
                image_url = f"data:{part.mime_type};base64,{base64.b64encode(part.data).decode('ascii')}"
                content.append({"type": "input_image", "image_url": image_url})
            else:
                raise EngineError("configuration", "Unsupported model input content.")
        messages.append({"role": message.role, "content": content})
    return messages


def token_usage(usage: Usage | None) -> TokenUsage | None:
    if usage is None or not usage.requests:
        return None
    return TokenUsage(input_tokens=usage.input_tokens, output_tokens=usage.output_tokens, total_tokens=usage.total_tokens, requests=usage.requests)


def model_result(response: ModelResponse) -> ModelResult:
    pieces = []
    refused = False
    incomplete = False
    for item in response.output:
        incomplete = incomplete or getattr(item, "status", None) == "incomplete"
        if item.type != "message":
            continue
        for part in item.content:
            if part.type == "output_text":
                pieces.append(part.text)
            elif part.type == "refusal":
                refused = True
    finish_reason = "refusal" if refused else "incomplete" if incomplete else "unknown"
    return ModelResult(text="\n".join(pieces), usage=token_usage(response.usage), finish_reason=finish_reason)


def sdk_tool(binding: ToolBinding, invocation_tasks: set[asyncio.Task]) -> FunctionTool:
    async def invoke(context: ToolContext, raw_arguments: str) -> str:
        task = asyncio.current_task()
        if task is not None:
            invocation_tasks.add(task)
            task.add_done_callback(invocation_tasks.discard)
        try:
            arguments = json.loads(raw_arguments)
        except json.JSONDecodeError:
            arguments = None
        if not isinstance(arguments, dict):
            failure = ToolInputFailure(name=binding.name, call_id=context.tool_call_id, message="Tool arguments must be a JSON object.")
            if binding.on_invalid_input is not None:
                return await binding.on_invalid_input(failure)
            return json.dumps({"error": failure.message})
        return await binding.invoke(ToolCall(name=binding.name, arguments=arguments, call_id=context.tool_call_id))

    return FunctionTool(
        name=binding.name,
        description=binding.description,
        params_json_schema=deepcopy(binding.parameters_schema),
        strict_json_schema=binding.strict,
        on_invoke_tool=invoke,
        _use_default_failure_error_function=False,
    )


class SDKOutputContract(AgentOutputSchemaBase):
    def __init__(self, contract: OutputContract) -> None:
        self._contract = contract
        schema = deepcopy(contract.schema)
        self._schema = ensure_strict_json_schema(schema) if contract.strict else schema

    def is_plain_text(self) -> bool:
        return False

    def name(self) -> str:
        return self._contract.name

    def json_schema(self) -> dict:
        return self._schema

    def is_strict_json_schema(self) -> bool:
        return self._contract.strict

    def validate_json(self, json_str: str):
        try:
            return self._contract.parse(json_str)
        except (ValueError, TypeError):
            raise ModelBehaviorError("Structured model output failed validation.") from None


def _field(value: object, name: str):
    return value.get(name) if isinstance(value, dict) else getattr(value, name, None)


def _item_text(value: object) -> str:
    content = _field(value, "content")
    if not isinstance(content, list):
        return ""
    return "\n".join(text for part in content if isinstance(text := _field(part, "text"), str))


def semantic_event(event: StreamEvent, calls: dict[str, ToolCall]) -> AgentEvent | None:
    if event.type == "agent_updated_stream_event":
        return AgentEvent(kind="agent_started", agent_name=event.new_agent.name)
    if event.type != "run_item_stream_event":
        return None
    raw = event.item.raw_item
    call_id = _field(raw, "call_id") or _field(event.item, "call_id")
    call_id = call_id if isinstance(call_id, str) else None
    name = _field(event.item, "tool_name") or _field(raw, "name")
    name = name if isinstance(name, str) else None
    if event.name == "tool_called":
        arguments = _field(raw, "arguments")
        if isinstance(arguments, str):
            try:
                decoded = json.loads(arguments)
            except json.JSONDecodeError:
                decoded = arguments
            arguments = decoded if isinstance(decoded, dict | str) else arguments
        elif not isinstance(arguments, dict):
            arguments = None
        if call_id and name and isinstance(arguments, dict):
            calls[call_id] = ToolCall(name=name, arguments=arguments, call_id=call_id)
        return AgentEvent(kind="tool_called", tool_name=name, call_id=call_id, arguments=arguments)
    if event.name == "tool_output":
        remembered = calls.pop(call_id, None) if call_id else None
        output = _field(event.item, "output")
        if not isinstance(output, str):
            output = json.dumps(output, ensure_ascii=False)
        return AgentEvent(kind="tool_output", tool_name=name or (remembered.name if remembered else None), call_id=call_id, output=output)
    if event.name == "message_output_created":
        return AgentEvent(kind="message", text=_item_text(raw))
    if event.name == "reasoning_item_created":
        summary = _field(raw, "summary")
        if isinstance(summary, list):
            summary = "\n".join(text for part in summary if isinstance(text := _field(part, "text"), str))
        if isinstance(summary, str) and summary:
            return AgentEvent(kind="reasoning_summary", text=summary)
    return None


def engine_error(error: Exception) -> EngineError:
    if isinstance(error, EngineError):
        return error
    status = getattr(error, "status_code", None)
    status_code = status if isinstance(status, int) else None
    if status_code in {401, 403}:
        return EngineError("authentication", "Model provider authentication failed.", status_code=status_code)
    if status_code == 429:
        return EngineError("rate_limit", "Model provider rate limit reached.", status_code=status_code)
    if status_code == 404 or (status_code is not None and status_code >= 500):
        return EngineError("unavailable", "Model provider or selected model is unavailable.", status_code=status_code)
    if isinstance(error, TimeoutError | APITimeoutError):
        return EngineError("timeout", "Model operation timed out.")
    name = type(error).__name__
    if name == "ModelRefusalError":
        return EngineError("refusal", "Model provider refused the request.")
    if name == "MaxTurnsExceeded":
        return EngineError("max_turns", "Agent exceeded the configured turn limit.")
    if name == "UserError":
        return EngineError("configuration", "Agent backend configuration is invalid.")
    if name == "ModelBehaviorError":
        message = str(error).lower()
        if "response.incomplete" in message or "status=incomplete" in message:
            reason = "content_filter" if "content_filter" in message else "incomplete"
            return EngineError("incomplete", "Model provider returned an incomplete response.", reason=reason)
        return EngineError("invalid_output", "Model provider returned an invalid response or structured output.")
    return EngineError("runtime", "Model backend operation failed.", status_code=status_code)
