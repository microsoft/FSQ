# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from collections.abc import Mapping

from ._contracts import (
    AgentEngine,
    AgentEvent,
    AgentEventSink,
    AgentRequest,
    AgentResult,
    EngineError,
    ImageContent,
    JsonValue,
    Message,
    Model,
    ModelProvider,
    ModelRequest,
    ModelResult,
    OutputContract,
    TextContent,
    TokenUsage,
    ToolBinding,
    ToolCall,
    ToolInputFailure,
    ToolOutputEntry,
    ToolOutputFilter,
)


def create_model_provider(*, base_url: str, api_key: str, headers: Mapping[str, str] | None = None) -> ModelProvider:
    try:
        from ._openai_backend import OpenAIModelProvider
    except ImportError:
        raise EngineError("configuration", "The model backend dependencies are unavailable.") from None
    return OpenAIModelProvider(base_url=base_url, api_key=api_key, headers=headers)


def create_agent_engine() -> AgentEngine:
    try:
        from ._openai_backend import OpenAIAgentEngine
    except ImportError:
        raise EngineError("configuration", "The agent backend dependencies are unavailable.") from None
    return OpenAIAgentEngine()


__all__ = [
    "AgentEngine",
    "AgentEvent",
    "AgentEventSink",
    "AgentRequest",
    "AgentResult",
    "EngineError",
    "ImageContent",
    "JsonValue",
    "Message",
    "Model",
    "ModelProvider",
    "ModelRequest",
    "ModelResult",
    "OutputContract",
    "TextContent",
    "TokenUsage",
    "ToolBinding",
    "ToolCall",
    "ToolInputFailure",
    "ToolOutputEntry",
    "ToolOutputFilter",
    "create_agent_engine",
    "create_model_provider",
]
