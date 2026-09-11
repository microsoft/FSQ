# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from pathlib import Path
from typing import Any

from fsq_agent.agent._pre_plan import (
    PRE_PLAN_AGENT_INSTRUCTIONS,
    ReadKnowledgeIndexArgs,
    ReadKnowledgePageArgs,
    build_pre_plan_input,
    page_file_from_index,
    safe_page_relative_path,
)
from fsq_agent.agent._prompt import PromptModelBuilder, PromptRenderer
from fsq_agent.agent._structured_output import coerce_agent_final_output, coerce_string_list, serialize_agent_final_output
from fsq_agent.agent._verification_task import VERIFICATION_AGENT_INSTRUCTIONS, VerificationEvidenceBuilder
from fsq_agent.models import AgentFinalOutput, AgentPromptConfig, KnowledgeBundle, SkillBundle, StepResult, Task


class CodingAgentPolicy:
    pre_plan_instructions = PRE_PLAN_AGENT_INSTRUCTIONS
    verification_instructions = VERIFICATION_AGENT_INSTRUCTIONS
    read_knowledge_index_schema = ReadKnowledgeIndexArgs
    read_knowledge_page_schema = ReadKnowledgePageArgs

    def build_pre_plan_input(self, reference_text: str, knowledge: KnowledgeBundle, skills: list[SkillBundle], **kwargs: Any) -> str:
        return build_pre_plan_input(reference_text, knowledge, skills, **kwargs)

    def page_file_from_index(self, index_text: str, page_id: str) -> str | None:
        return page_file_from_index(index_text, page_id)

    def safe_page_relative_path(self, value: str) -> Path | None:
        return safe_page_relative_path(value)

    def coerce_agent_final_output(self, output: Any) -> AgentFinalOutput | None:
        return coerce_agent_final_output(output)

    def serialize_agent_final_output(self, output: AgentFinalOutput | str) -> str:
        return serialize_agent_final_output(output)

    def coerce_string_list(self, value: Any) -> list[str]:
        return coerce_string_list(value)

    def build_agent_prompt(self, settings: AgentPromptConfig, knowledge: KnowledgeBundle, skills: list[SkillBundle]) -> str:
        return PromptRenderer(settings).render_agent_prompt(PromptModelBuilder(settings).build_agent_prompt(knowledge, skills))

    def build_task_prompt(
        self,
        settings: AgentPromptConfig,
        task: Task,
        runtime_policy: list[str] | None = None,
        runtime_secret_names: list[str] | None = None,
        runtime_secret_warnings: list[str] | None = None,
    ) -> str:
        model = PromptModelBuilder(settings).build_task_prompt(task, runtime_policy, runtime_secret_names, runtime_secret_warnings)
        return PromptRenderer(settings).render_task_prompt(model)

    def build_verification_input(self, task: Task, results: list[StepResult], events_path: Path | None = None) -> str:
        return VerificationEvidenceBuilder().build_model_input(task, results, events_path)
