# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ._contracts import EngineError, ToolOutputEntry

if TYPE_CHECKING:
    from ._contracts import ToolOutputFilter, ToolOutputTrimSettings


@dataclass(frozen=True)
class ModelInputData:
    input: list[dict]
    instructions: str | None


class ModelInputFilter:
    def __init__(self, trimming: ToolOutputTrimSettings | None, output_filter: ToolOutputFilter | None) -> None:
        if trimming is not None:
            if trimming.recent_turns < 1:
                raise EngineError("configuration", "recent_turns must be >= 1")
            if trimming.max_output_chars < 1:
                raise EngineError("configuration", "max_output_chars must be >= 1")
            if trimming.preview_chars < 0:
                raise EngineError("configuration", "preview_chars must be >= 0")
        self._trimming = trimming
        self._filter = output_filter

    def __call__(self, model_data: ModelInputData) -> ModelInputData:
        model_data = _trim_model_data(model_data, self._trimming) if self._trimming is not None else model_data
        if self._filter is None:
            return model_data
        names = {str(item.get("call_id") or ""): str(item.get("name") or "") for item in model_data.input if isinstance(item, dict) and item.get("type") == "function_call"}
        entries = []
        user_turn = 0
        for index, item in enumerate(model_data.input):
            if not isinstance(item, dict):
                continue
            if item.get("role") == "user":
                user_turn += 1
            if item.get("type") != "function_call_output":
                continue
            call_id = str(item.get("call_id") or item.get("id") or "") or None
            output = item.get("output", "")
            entries.append(ToolOutputEntry(entry_id=index, call_id=call_id, tool_name=names.get(call_id or "") or None, user_turn=user_turn, output=output if isinstance(output, str) else str(output)))
        replacements = self._filter(tuple(entries))
        known = {entry.entry_id for entry in entries}
        for entry_id, output in replacements.items():
            if type(entry_id) is not int or entry_id not in known or not isinstance(output, str):
                raise EngineError("configuration", "Tool output replacements must reference known entries and contain text.")
        items = [{**item, "output": replacements[index]} if index in replacements else item for index, item in enumerate(model_data.input)]
        return ModelInputData(input=items, instructions=model_data.instructions)


def _trim_model_data(model_data: ModelInputData, settings: ToolOutputTrimSettings) -> ModelInputData:
    items = model_data.input
    user_message_count = 0
    boundary = 0
    for index in range(len(items) - 1, -1, -1):
        item = items[index]
        if isinstance(item, dict) and item.get("role") == "user":
            user_message_count += 1
            if user_message_count >= settings.recent_turns:
                boundary = index
                break
    if boundary == 0:
        return model_data

    names = {str(item.get("call_id") or ""): str(item.get("name") or "") for item in items if isinstance(item, dict) and item.get("type") == "function_call"}
    trimmed = []
    for index, item in enumerate(items):
        if index >= boundary or not isinstance(item, dict) or item.get("type") != "function_call_output":
            trimmed.append(item)
            continue
        call_id = str(item.get("call_id") or item.get("id") or "")
        name = names.get(call_id, "")
        if settings.trimmable_tools is not None and name not in settings.trimmable_tools:
            trimmed.append(item)
            continue
        output = item.get("output", "")
        output = output if isinstance(output, str) else str(output)
        if len(output) <= settings.max_output_chars:
            trimmed.append(item)
            continue
        summary = f"[Trimmed: {name or 'unknown_tool'} output \u2014 {len(output)} chars \u2192 {settings.preview_chars} char preview]\n{output[: settings.preview_chars]}..."
        trimmed.append({**item, "output": summary} if len(summary) < len(output) else item)
    return ModelInputData(input=trimmed, instructions=model_data.instructions)
