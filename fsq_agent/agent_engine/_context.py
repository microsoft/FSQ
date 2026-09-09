# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ._contracts import EngineError, ToolOutputEntry

if TYPE_CHECKING:
    from ._contracts import ToolOutputFilter


@dataclass(frozen=True)
class ModelInputData:
    input: list[dict]
    instructions: str | None


class ModelInputFilter:
    def __init__(self, output_filter: ToolOutputFilter | None) -> None:
        self._filter = output_filter

    def __call__(self, model_data: ModelInputData) -> ModelInputData:
        if self._filter is None:
            return model_data
        names = {str(item.get("call_id") or ""): str(item.get("name") or "") for item in model_data.input if isinstance(item, dict) and item.get("type") == "function_call"}
        entries = []
        for index, item in enumerate(model_data.input):
            if not isinstance(item, dict):
                continue
            if item.get("type") != "function_call_output":
                continue
            call_id = str(item.get("call_id") or item.get("id") or "") or None
            output = item.get("output", "")
            entries.append(ToolOutputEntry(entry_id=index, call_id=call_id, tool_name=names.get(call_id or "") or None, output=output if isinstance(output, str) else str(output)))
        replacements = self._filter(tuple(entries))
        known = {entry.entry_id for entry in entries}
        for entry_id, output in replacements.items():
            if type(entry_id) is not int or entry_id not in known or not isinstance(output, str):
                raise EngineError("configuration", "Tool output replacements must reference known entries and contain text.")
        items = [{**item, "output": replacements[index]} if index in replacements else item for index, item in enumerate(model_data.input)]
        return ModelInputData(input=items, instructions=model_data.instructions)
