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
    ReasoningEffort,
    TextContent,
    TokenUsage,
    ToolBinding,
    ToolCall,
    ToolInputFailure,
    ToolOutputEntry,
    ToolOutputFilter,
)


def create_model_provider(*, base_url: str, api_key: str, headers: Mapping[str, str] | None = None, model_name_is_deployment: bool = False) -> ModelProvider:
    try:
        from ._openai_backend import OpenAIModelProvider
    except ImportError:
        raise EngineError("configuration", "The model backend dependencies are unavailable.") from None
    return OpenAIModelProvider(base_url=base_url, api_key=api_key, headers=headers, model_name_is_deployment=model_name_is_deployment)


def create_agent_engine() -> AgentEngine:
    try:
        from ._openai_backend import OpenAIAgentEngine
    except ImportError:
        raise EngineError("configuration", "The agent backend dependencies are unavailable.") from None
    return OpenAIAgentEngine()


def create_google_gemini_model_provider(*, base_url: str, api_key: str) -> ModelProvider:
    try:
        from ._google_gemini_backend import GoogleGeminiModelProvider
    except ImportError:
        raise EngineError("configuration", "The Google model backend dependencies are unavailable.") from None
    return GoogleGeminiModelProvider(base_url=base_url, api_key=api_key)


def create_agent_engine_for_model(model: Model) -> AgentEngine:
    from ._backend import BackendModel

    if not isinstance(model, BackendModel):
        raise EngineError("incompatible_model", "The selected model is incompatible with this agent backend.")
    return model.create_engine()


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
    "ReasoningEffort",
    "TextContent",
    "TokenUsage",
    "ToolBinding",
    "ToolCall",
    "ToolInputFailure",
    "ToolOutputEntry",
    "ToolOutputFilter",
    "create_agent_engine",
    "create_agent_engine_for_model",
    "create_google_gemini_model_provider",
    "create_model_provider",
]
