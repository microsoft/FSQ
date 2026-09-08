# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from fsq_agent.models import (
    AgentContextSettings,
    AgentRuntimeSettings,
    AgentSettings,
    CaseLifecycleSettings,
    CaseSettings,
    ExecutionSettings,
    HarnessSettings,
    OutputSettings,
    RuntimeSecretSettings,
    SkillConfig,
    WorkspaceSettings,
)


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    agent: AgentSettings = Field(default_factory=AgentSettings)
    agent_runtime: AgentRuntimeSettings = Field(default_factory=AgentRuntimeSettings)
    harness: HarnessSettings = Field(default_factory=HarnessSettings)
    execution: ExecutionSettings = Field(default_factory=ExecutionSettings)
    case_lifecycle: CaseLifecycleSettings = Field(default_factory=CaseLifecycleSettings, alias="caseLifecycle")
    runtime_secrets: RuntimeSecretSettings = Field(default_factory=RuntimeSecretSettings)
    workspace: WorkspaceSettings = Field(default_factory=WorkspaceSettings)
    cases: CaseSettings = Field(default_factory=CaseSettings)
    output: OutputSettings = Field(default_factory=OutputSettings)
    agent_context: AgentContextSettings = Field(default_factory=AgentContextSettings)

    @property
    def skills(self) -> list[SkillConfig]:
        return self.agent_context.knowledge.skills.items

    @skills.setter
    def skills(self, value: list[SkillConfig]) -> None:
        self.agent_context.knowledge.skills.items = value

    @property
    def knowledge_dir(self) -> Path:
        return self.agent_context.knowledge.root_dir

    @knowledge_dir.setter
    def knowledge_dir(self, value: str | Path) -> None:
        self.agent_context.knowledge.root_dir = Path(value)
