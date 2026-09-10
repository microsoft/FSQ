# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from __future__ import annotations

from typing import TYPE_CHECKING

from ._contracts import EngineError

if TYPE_CHECKING:
    from ._contracts import OutputContract, OutputT


def parse_output(text: str, contract: OutputContract[OutputT]) -> OutputT:
    if not text.strip():
        raise EngineError("invalid_output", "Model provider returned an invalid response or structured output.")
    try:
        return contract.parse(text)
    except (ValueError, TypeError):
        raise EngineError("invalid_output", "Model provider returned an invalid response or structured output.") from None
