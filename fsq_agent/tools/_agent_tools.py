# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import json
import time
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, Field

from fsq_agent.models import (
    AgentToolCall,
    AgentToolDefinition,
    AgentToolResult,
    LocalToolOutputSettings,
    RuntimeSecretSettings,
    ToolExecutionError,
)
from fsq_agent.tools._file_ops import FileOps
from fsq_agent.tools._tool_artifacts import ToolArtifactStore

_AGENT_TOOL_ORDER = (
    "read_file",
    "write_file",
    "search_artifact",
    "read_artifact_slice",
)


class _ReadFileArgs(BaseModel):
    path: str = Field(description="Workspace-relative path to read.")


class _WriteFileArgs(BaseModel):
    path: str = Field(description="Workspace-relative path to write.")
    content: str = Field(description="Text content to write.")


class _SearchArtifactArgs(BaseModel):
    artifact_path: str = Field(description="Artifact path returned by a previous tool response.")
    query: str = Field(description="Text to search for inside the artifact.")
    case_sensitive: bool = Field(default=False, description="Whether the search is case-sensitive.")
    max_matches: int = Field(default=20, ge=1, le=100, description="Maximum number of matches to return.")
    context_chars: int = Field(default=300, ge=0, le=2000, description="Characters of context around each match.")


class _ReadArtifactSliceArgs(BaseModel):
    artifact_path: str = Field(description="Artifact path returned by a previous tool response.")
    offset: int = Field(default=0, ge=0, description="Character offset to start reading from.")
    length: int = Field(default=12000, ge=1, le=30000, description="Maximum characters to read.")


_AGENT_TOOL_MODELS: dict[str, tuple[str, type[BaseModel]]] = {
    "read_file": ("Read a scoped workspace file.", _ReadFileArgs),
    "write_file": ("Write a scoped output file.", _WriteFileArgs),
    "search_artifact": ("Search a large AgentTool-output artifact by text and return offsets with local context.", _SearchArtifactArgs),
    "read_artifact_slice": ("Read a bounded character slice from a large AgentTool-output artifact by offset and length.", _ReadArtifactSliceArgs),
}


@runtime_checkable
class AgentToolProvider(Protocol):
    def list_capabilities(self) -> list[AgentToolDefinition]: ...

    async def invoke(self, call: AgentToolCall) -> AgentToolResult: ...


class AgentToolRegistry:
    def __init__(self) -> None:
        self._definitions: dict[str, AgentToolDefinition] = {}
        self._providers: dict[str, AgentToolProvider] = {}

    @classmethod
    def from_providers(cls, providers: list[AgentToolProvider]) -> "AgentToolRegistry":
        registry = cls()
        for provider in providers:
            registry.register_provider(provider)
        return registry

    def register_provider(self, provider: AgentToolProvider) -> None:
        for definition in provider.list_capabilities():
            if definition.name in self._definitions:
                raise ToolExecutionError("Duplicate AgentTool name.", context={"tool_name": definition.name})
            self._definitions[definition.name] = definition
            self._providers[definition.name] = provider

    def list_tools(self) -> list[AgentToolDefinition]:
        return list(self._definitions.values())

    def get(self, name: str) -> AgentToolDefinition | None:
        return self._definitions.get(name)

    def provider_for(self, name: str) -> AgentToolProvider | None:
        return self._providers.get(name)

    def list_providers(self) -> list[AgentToolProvider]:
        providers: list[AgentToolProvider] = []
        seen: set[int] = set()
        for provider in self._providers.values():
            marker = id(provider)
            if marker in seen:
                continue
            seen.add(marker)
            providers.append(provider)
        return providers

    def capability_for(self, name: str) -> None:
        return None

    def list_capability_definitions(self) -> list[Any]:
        return []


class AgentToolExecutor:
    def __init__(self, registry: AgentToolRegistry) -> None:
        self.registry = registry

    async def execute(self, call: AgentToolCall) -> AgentToolResult:
        provider = self.registry.provider_for(call.tool_name)
        if provider is None:
            raise ToolExecutionError("Unknown AgentTool.", context={"tool_name": call.tool_name})
        return await provider.invoke(call)


class DefaultAgentToolProvider:
    def __init__(
        self,
        file_ops: FileOps,
        *,
        runtime_secret_settings: RuntimeSecretSettings | None = None,
        local_tool_output_settings: LocalToolOutputSettings | None = None,
        runs_dir: Path | None = None,
        run_id: str = "",
        **_: Any,
    ) -> None:
        self.file_ops = file_ops
        self.runtime_secret_settings = runtime_secret_settings or RuntimeSecretSettings()
        self.local_tool_output_settings = local_tool_output_settings or LocalToolOutputSettings()
        self.runs_dir = runs_dir
        self.run_id = run_id
        self.artifact_store = self._build_artifact_store()

    def configure_run(self, run_id: str) -> None:
        self.run_id = run_id
        if self.runs_dir is not None:
            self.file_ops.configure_write_root(self.runs_dir / run_id / "artifacts")
        self.artifact_store = self._build_artifact_store()

    def list_capabilities(self) -> list[AgentToolDefinition]:
        return type(self).agent_tool_definitions()

    @classmethod
    def agent_tool_definitions(cls) -> list[AgentToolDefinition]:
        definitions: list[AgentToolDefinition] = []
        for name in _AGENT_TOOL_ORDER:
            description, params_model = _AGENT_TOOL_MODELS[name]
            definitions.append(
                AgentToolDefinition(
                    name=name,
                    description=description,
                    params_json_schema=params_model.model_json_schema(),
                    strict=True,
                    metadata={"tool_origin": "agent_tool"},
                )
            )
        return definitions

    @classmethod
    def capability_definitions(cls) -> list[Any]:
        return []

    async def invoke(self, call: AgentToolCall) -> AgentToolResult:
        handler = getattr(self, f"_{call.tool_name}", None)
        if callable(handler):
            return await handler(call.arguments)
        raise ToolExecutionError("Unknown AgentTool.", context={"tool_name": call.tool_name})

    async def _read_file(self, arguments: dict[str, Any]) -> AgentToolResult:
        parsed = _ReadFileArgs.model_validate(arguments)
        started = time.perf_counter()
        result = await self.file_ops.read_text({"path": parsed.path})
        return self._from_file_result("read_file", result, started, {"path": parsed.path})

    async def _write_file(self, arguments: dict[str, Any]) -> AgentToolResult:
        parsed = _WriteFileArgs.model_validate(arguments)
        started = time.perf_counter()
        result = await self.file_ops.write_text({"path": parsed.path, "content": parsed.content})
        return self._from_file_result("write_file", result, started, {"path": parsed.path})

    async def _search_artifact(self, arguments: dict[str, Any]) -> AgentToolResult:
        parsed = _SearchArtifactArgs.model_validate(arguments)
        started = time.perf_counter()
        if not self.artifact_store:
            raise ToolExecutionError("Tool artifact storage is not enabled for this run.")
        result = self.artifact_store.search(
            parsed.artifact_path,
            parsed.query,
            parsed.case_sensitive,
            parsed.max_matches,
            parsed.context_chars,
        )
        return AgentToolResult(
            tool_name="search_artifact",
            status="success",
            output=self._redact(result),
            duration_ms=int((time.perf_counter() - started) * 1000),
        )

    async def _read_artifact_slice(self, arguments: dict[str, Any]) -> AgentToolResult:
        parsed = _ReadArtifactSliceArgs.model_validate(arguments)
        started = time.perf_counter()
        if not self.artifact_store:
            raise ToolExecutionError("Tool artifact storage is not enabled for this run.")
        result = self.artifact_store.read_slice(parsed.artifact_path, parsed.offset, parsed.length)
        return AgentToolResult(
            tool_name="read_artifact_slice",
            status="success",
            output=self._redact(result),
            duration_ms=int((time.perf_counter() - started) * 1000),
        )

    def _from_file_result(self, tool_name: str, result: AgentToolResult, started: float, metadata: dict[str, Any]) -> AgentToolResult:
        payload = self._redact(result.model_dump(mode="json"))
        metadata = self._redact(metadata)
        if result.status == "failed":
            return AgentToolResult(
                tool_name=tool_name,
                status="failed",
                output=payload,
                error=payload["error"],
                duration_ms=result.duration_ms,
                metadata=metadata,
            )
        return self._with_optional_artifact(tool_name, payload, started, metadata)

    def _redact(self, value: Any) -> Any:
        if isinstance(value, str):
            for private_value in sorted(self.runtime_secret_settings.private_values().values(), key=len, reverse=True):
                if private_value:
                    value = value.replace(private_value, "***")
            return value
        if isinstance(value, dict):
            return {key: self._redact(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self._redact(item) for item in value]
        return value

    def _with_optional_artifact(
        self,
        tool_name: str,
        output: Any,
        started: float,
        metadata: dict[str, Any] | None = None,
    ) -> AgentToolResult:
        full_output = json.dumps(output, ensure_ascii=False, default=str)
        artifact_path = self.artifact_store.write(tool_name, full_output, metadata) if self.artifact_store else None
        return AgentToolResult(
            tool_name=tool_name,
            status="success",
            output=output,
            artifact_path=artifact_path,
            artifact_content_chars=len(full_output) if artifact_path else None,
            duration_ms=int((time.perf_counter() - started) * 1000),
            metadata=metadata or {},
        )

    def _build_artifact_store(self) -> ToolArtifactStore | None:
        if self.runs_dir and self.run_id and self.local_tool_output_settings.artifact_enabled:
            return ToolArtifactStore(self.runs_dir, self.run_id, self.local_tool_output_settings)
        return None


CommonToolProvider = AgentToolProvider
CommonToolRegistry = AgentToolRegistry
CommonToolExecutor = AgentToolExecutor
DefaultCommonToolProvider = DefaultAgentToolProvider
