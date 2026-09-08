# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from __future__ import annotations

from typing import TYPE_CHECKING

from agents.extensions import ToolOutputTrimmer
from agents.run_config import ModelInputData

from ._contracts import EngineError, ToolOutputEntry

if TYPE_CHECKING:
    from agents.run_config import CallModelData

    from ._contracts import ToolOutputFilter, ToolOutputTrimSettings


class ModelInputFilter:
    def __init__(self, trimming: ToolOutputTrimSettings | None, output_filter: ToolOutputFilter | None) -> None:
        self._trimmer = (
            ToolOutputTrimmer(recent_turns=trimming.recent_turns, max_output_chars=trimming.max_output_chars, preview_chars=trimming.preview_chars, trimmable_tools=trimming.trimmable_tools)
            if trimming is not None
            else None
        )
        self._filter = output_filter

    def __call__(self, data: CallModelData) -> ModelInputData:
        model_data = self._trimmer(data) if self._trimmer is not None else data.model_data
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
