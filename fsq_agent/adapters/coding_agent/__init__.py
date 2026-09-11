# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fsq_agent.adapters.coding_agent._runtime import DefaultCodingAgentRuntime
from fsq_agent.tools import AgentToolAdapter, AgentToolRegistry, DefaultAgentToolProvider, FileOps

if TYPE_CHECKING:
    from fsq_agent.agent import CodingAgentRuntime
    from fsq_agent.config import Settings


def create_coding_agent_runtime(settings: Settings, *, harness_factory: Any | None = None) -> CodingAgentRuntime:
    knowledge = settings.agent_context.knowledge
    file_ops = FileOps(
        read_roots=[settings.cases.dir, knowledge.root_dir, knowledge.skills.dir, knowledge.pre_plan.dir or knowledge.root_dir, settings.output.root_dir],
        write_root=settings.output.root_dir / "artifacts",
    )
    provider = DefaultAgentToolProvider(
        file_ops,
        runtime_secret_settings=settings.runtime_secrets,
        local_tool_output_settings=settings.agent_runtime.local_tool_output,
        runs_dir=settings.output.runs_dir,
    )
    adapter = AgentToolAdapter(
        AgentToolRegistry.from_providers([provider]),
        local_tool_output_settings=settings.agent_runtime.local_tool_output,
    )
    return DefaultCodingAgentRuntime(settings, adapter, harness_factory=harness_factory)


__all__ = ["DefaultCodingAgentRuntime", "create_coding_agent_runtime"]
