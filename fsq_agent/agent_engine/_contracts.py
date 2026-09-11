# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Generic, Literal, Protocol, TypeAlias, TypeVar

JsonValue: TypeAlias = str | int | float | bool | list["JsonValue"] | dict[str, "JsonValue"] | None
ReasoningEffort: TypeAlias = Literal["low", "mid", "high"]
OutputT = TypeVar("OutputT")


@dataclass(frozen=True)
class TextContent:
    text: str


@dataclass(frozen=True)
class ImageContent:
    data: bytes = field(repr=False)
    mime_type: str = "image/png"


@dataclass(frozen=True)
class Message:
    content: tuple[TextContent | ImageContent, ...]
    role: Literal["user", "assistant"] = "user"


@dataclass(frozen=True)
class ModelRequest(Generic[OutputT]):
    input: str | tuple[Message, ...]
    instructions: str | None = None
    output: OutputContract[OutputT] | None = None
    reasoning_effort: ReasoningEffort = "mid"


@dataclass(frozen=True)
class TokenUsage:
    input_tokens: int
    output_tokens: int
    total_tokens: int
    requests: int = 1
    cached_input_tokens: int | None = None
    reasoning_tokens: int | None = None


@dataclass(frozen=True)
class ModelResult(Generic[OutputT]):
    text: str
    usage: TokenUsage | None = None
    finish_reason: str = "unknown"
    parsed_output: OutputT | None = None


@dataclass(frozen=True)
class ToolCall:
    name: str
    arguments: dict[str, JsonValue]
    call_id: str


@dataclass(frozen=True)
class ToolInputFailure:
    name: str
    call_id: str
    message: str


@dataclass(frozen=True)
class ToolBinding:
    name: str
    description: str
    parameters_schema: dict[str, JsonValue]
    invoke: Callable[[ToolCall], Awaitable[str]] = field(repr=False)
    strict: bool = True
    on_invalid_input: Callable[[ToolInputFailure], Awaitable[str]] | None = field(default=None, repr=False)


@dataclass(frozen=True)
class OutputContract(Generic[OutputT]):
    name: str
    schema: dict[str, JsonValue]
    parse: Callable[[str], OutputT] = field(repr=False)
    strict: bool = True


@dataclass(frozen=True)
class ToolOutputEntry:
    entry_id: int
    call_id: str | None
    tool_name: str | None
    output: str


ToolOutputFilter: TypeAlias = Callable[[tuple[ToolOutputEntry, ...]], Mapping[int, str]]


@dataclass(frozen=True)
class AgentRequest(Generic[OutputT]):
    name: str
    instructions: str
    input: str | tuple[Message, ...]
    tools: tuple[ToolBinding, ...] = ()
    output: OutputContract[OutputT] | None = None
    max_turns: int = 10
    stream: bool = False
    tracing_enabled: bool = False
    tool_output_filter: ToolOutputFilter | None = field(default=None, repr=False)
    reasoning_effort: ReasoningEffort = "mid"


@dataclass(frozen=True)
class AgentResult(Generic[OutputT]):
    final_output: OutputT | str
    usage: TokenUsage | None = None


@dataclass(frozen=True)
class AgentEvent:
    kind: Literal["agent_started", "tool_called", "tool_output", "message", "reasoning_summary"]
    text: str = ""
    agent_name: str | None = None
    tool_name: str | None = None
    call_id: str | None = None
    arguments: dict[str, JsonValue] | str | None = None
    output: str | None = None


AgentEventSink: TypeAlias = Callable[[AgentEvent], Awaitable[None]]


class EngineError(Exception):
    def __init__(self, category: str, message: str, *, status_code: int | None = None, reason: str | None = None) -> None:
        super().__init__(message)
        self.category = category
        self.status_code = status_code
        self.reason = reason


class Model(Protocol):
    async def complete(self, request: ModelRequest[OutputT]) -> ModelResult[OutputT]: ...


class ModelProvider(Protocol):
    def get_model(self, model_name: str) -> Model: ...

    async def aclose(self) -> None: ...


class AgentEngine(Protocol):
    async def run(self, model: Model, request: AgentRequest[OutputT], *, on_event: AgentEventSink | None = None) -> AgentResult[OutputT]: ...
