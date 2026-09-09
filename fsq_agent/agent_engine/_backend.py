# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from ._contracts import AgentEvent, TokenUsage, ToolOutputEntry

if TYPE_CHECKING:
    from collections.abc import Mapping

    from ._contracts import AgentEngine


class BackendModel:
    def create_engine(self) -> AgentEngine:
        raise NotImplementedError


@dataclass(frozen=True)
class BackendTurn:
    text: str
    calls: tuple[dict, ...]
    events: tuple[AgentEvent, ...]
    usage: TokenUsage | None


class BackendConversation(Protocol):
    def tool_outputs(self) -> tuple[ToolOutputEntry, ...]: ...

    async def request(self, replacements: Mapping[int, str]) -> BackendTurn: ...

    def add_tool_output(self, call: dict, output: str) -> None: ...


def _reject_json_constant(value: str) -> None:
    raise ValueError("Tool arguments must contain valid JSON values.")


def decode_tool_arguments(raw_arguments: object) -> dict | None:
    if not isinstance(raw_arguments, str):
        return None
    try:
        arguments = json.loads(raw_arguments, parse_constant=_reject_json_constant)
    except ValueError:
        return None
    return arguments if isinstance(arguments, dict) else None


def add_usage(total: TokenUsage | None, measured: TokenUsage | None) -> TokenUsage | None:
    if total is None:
        return measured
    if measured is None:
        return total
    return TokenUsage(
        input_tokens=total.input_tokens + measured.input_tokens,
        output_tokens=total.output_tokens + measured.output_tokens,
        total_tokens=total.total_tokens + measured.total_tokens,
        requests=total.requests + measured.requests,
        cached_input_tokens=total.cached_input_tokens + measured.cached_input_tokens if total.cached_input_tokens is not None and measured.cached_input_tokens is not None else None,
        reasoning_tokens=total.reasoning_tokens + measured.reasoning_tokens if total.reasoning_tokens is not None and measured.reasoning_tokens is not None else None,
    )
