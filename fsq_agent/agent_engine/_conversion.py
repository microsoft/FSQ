# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from __future__ import annotations

import base64
from copy import deepcopy
from typing import TYPE_CHECKING

from openai import APITimeoutError

from ._backend import decode_tool_arguments
from ._contracts import AgentEvent, EngineError, ImageContent, ModelResult, TextContent, TokenUsage
from ._schema import ensure_strict_json_schema

if TYPE_CHECKING:
    from openai.types.responses import Response

    from ._contracts import Message, OutputContract, ToolBinding


def model_input(value: str | tuple[Message, ...]) -> list[dict]:
    if isinstance(value, str):
        return [{"content": value, "role": "user"}]
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


def token_usage(usage: object) -> TokenUsage | None:
    values = [getattr(usage, name, None) for name in ("input_tokens", "output_tokens", "total_tokens")]
    if not all(type(value) is int and value >= 0 for value in values):
        return None
    input_details = getattr(usage, "input_tokens_details", None)
    output_details = getattr(usage, "output_tokens_details", None)
    cached_input_tokens = getattr(input_details, "cached_tokens", None)
    reasoning_tokens = getattr(output_details, "reasoning_tokens", None)
    if type(cached_input_tokens) is not int or cached_input_tokens < 0:
        cached_input_tokens = None
    if type(reasoning_tokens) is not int or reasoning_tokens < 0:
        reasoning_tokens = None
    return TokenUsage(
        input_tokens=values[0],
        output_tokens=values[1],
        total_tokens=values[2],
        requests=1,
        cached_input_tokens=cached_input_tokens,
        reasoning_tokens=reasoning_tokens,
    )


def _incomplete_response(response: Response) -> EngineError:
    reason = "content_filter" if _field(_field(response, "incomplete_details"), "reason") == "content_filter" else "incomplete"
    return EngineError("incomplete", "Model provider returned an incomplete response.", reason=reason)


def check_response(response: Response) -> None:
    status = _field(response, "status")
    if status == "incomplete":
        raise _incomplete_response(response)
    if status != "completed" or _field(response, "error") is not None:
        raise EngineError("invalid_output", "Model provider returned an invalid response or structured output.")
    output = _field(response, "output")
    if not isinstance(output, list):
        raise EngineError("invalid_output", "Model provider returned an invalid response.")
    for item in output:
        if _field(item, "status") == "incomplete":
            raise _incomplete_response(response)
        if _field(item, "type") == "message":
            content = _field(item, "content")
            if not isinstance(content, list):
                raise EngineError("invalid_output", "Model provider returned an invalid message.")
            if any(_field(part, "type") == "refusal" for part in content):
                raise EngineError("refusal", "Model provider refused the request.")


def model_result(response: Response) -> ModelResult:
    check_response(response)
    pieces = [part.text for item in response.output if item.type == "message" for part in item.content if part.type == "output_text"]
    return ModelResult(text="\n".join(pieces), usage=token_usage(response.usage))


def response_parameters(
    model_name: str,
    input_items: list[dict],
    instructions: str | None,
    *,
    tools: tuple[ToolBinding, ...] = (),
    output: OutputContract | None = None,
    agent: bool = False,
) -> dict:
    parameters = {
        "model": model_name,
        "input": input_items,
        "include": [],
        "tools": [
            {
                "type": "function",
                "name": tool.name,
                "description": tool.description,
                "parameters": ensure_strict_json_schema(tool.parameters_schema) if tool.strict else deepcopy(tool.parameters_schema),
                "strict": tool.strict,
            }
            for tool in tools
        ],
    }
    if instructions is not None:
        parameters["instructions"] = instructions
    if agent:
        parameters["reasoning"] = {"effort": "medium"}
        text = {"verbosity": "medium"}
        if output is not None:
            text["format"] = {
                "type": "json_schema",
                "name": "final_output",
                "schema": ensure_strict_json_schema(output.schema) if output.strict else deepcopy(output.schema),
                "strict": output.strict,
            }
        parameters["text"] = text
    return parameters


def response_items(response: Response) -> list[dict]:
    if not isinstance(response.output, list):
        raise EngineError("invalid_output", "Model provider returned an invalid response.")
    items = [item.model_dump(exclude_unset=True) for item in response.output]
    if any(item.get("type") not in {"message", "function_call", "reasoning"} for item in items):
        raise EngineError("invalid_output", "Model provider returned an unsupported response item.")
    return items


def _field(value: object, name: str):
    return value.get(name) if isinstance(value, dict) else getattr(value, name, None)


def _item_text(value: object) -> str:
    content = _field(value, "content")
    if not isinstance(content, list):
        return ""
    return "\n".join(text for part in content if isinstance(text := _field(part, "text"), str))


def semantic_event(item: dict) -> AgentEvent | None:
    if item["type"] == "function_call":
        raw_arguments = item.get("arguments")
        arguments = decode_tool_arguments(raw_arguments)
        if arguments is None:
            arguments = raw_arguments if isinstance(raw_arguments, str) else None
        return AgentEvent(kind="tool_called", tool_name=item.get("name"), call_id=item.get("call_id"), arguments=arguments)
    if item["type"] == "message":
        return AgentEvent(kind="message", text=_item_text(item))
    if item["type"] == "reasoning":
        summary = item.get("summary")
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
    return EngineError("runtime", "Model backend operation failed.", status_code=status_code)
