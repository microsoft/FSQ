# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from __future__ import annotations

from typing import TYPE_CHECKING

from ._contracts import EngineError, ToolOutputEntry

if TYPE_CHECKING:
    from ._contracts import ToolOutputFilter


class ModelInputFilter:
    def __init__(self, output_filter: ToolOutputFilter | None) -> None:
        self._filter = output_filter

    def __call__(self, entries: tuple[ToolOutputEntry, ...]) -> dict[int, str]:
        if self._filter is None:
            return {}
        replacements = self._filter(entries)
        known = {entry.entry_id for entry in entries}
        for entry_id, output in replacements.items():
            if type(entry_id) is not int or entry_id not in known or not isinstance(output, str):
                raise EngineError("configuration", "Tool output replacements must reference known entries and contain text.")
        return dict(replacements)
