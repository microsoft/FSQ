# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from fsq_agent.agent._policy import CodingAgentPolicy
from fsq_agent.config import Settings, validate_runtime_settings
from fsq_agent.core import CapabilityDefinitionFactory, CapabilityRegistry, CommonPlatformTools
from fsq_agent.knowledge import PrivateKnowledgeLoader
from fsq_agent.models import Task
from fsq_agent.skills import SkillLoader
from fsq_agent.tools import AgentToolRegistry, DefaultAgentToolProvider, FileOps


def check_dynamic_agent_readiness(settings: Settings) -> tuple[bool, str, str]:
    try:
        validate_runtime_settings(settings)
        knowledge_settings = settings.agent_context.knowledge
        skills = SkillLoader(knowledge_settings.skills.dir).load(settings.skills)
        knowledge = PrivateKnowledgeLoader(knowledge_settings.root_dir).load_for_task(Task(description="Doctor readiness"))
        policy = CodingAgentPolicy()
        policy.build_agent_prompt(settings.agent_runtime.prompt, knowledge, skills)
        policy.build_task_prompt(settings.agent_runtime.prompt, Task(description="Doctor readiness"))
        definitions = [
            *CommonPlatformTools.capability_definitions(),
            *CapabilityDefinitionFactory().platform_definitions(platform=settings.harness.platform),
        ]
        CapabilityRegistry.from_definitions(definitions)
        file_ops = FileOps(
            read_roots=[settings.cases.dir, knowledge_settings.root_dir, knowledge_settings.skills.dir, knowledge_settings.pre_plan.dir or knowledge_settings.root_dir, settings.output.root_dir],
            write_root=settings.output.root_dir / "artifacts",
        )
        provider = DefaultAgentToolProvider(
            file_ops,
            runtime_secret_settings=settings.runtime_secrets,
            local_tool_output_settings=settings.agent_runtime.local_tool_output,
            runs_dir=settings.output.runs_dir,
        )
        AgentToolRegistry.from_providers([provider])
    except Exception:  # noqa: BLE001 - public readiness returns only safe normalized diagnostics.
        return False, "Dynamic Agent prerequisites are unavailable.", "Repair Agent, prompt, knowledge, Skill, or AgentTool configuration."
    return True, "Dynamic Agent prerequisites are ready.", ""
