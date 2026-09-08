# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, StrictUndefined, TemplateError

from fsq_agent.models import AgentPromptConfig, AgentTaskInput, ConfigurationError, KnowledgeBundle, SkillBundle, Task

_DEFAULT_TEMPLATE_DIR = Path(__file__).parent / "templates"
_DEFAULT_AGENT_TEMPLATE = _DEFAULT_TEMPLATE_DIR / "agent_instructions.j2"
_DEFAULT_TASK_TEMPLATE = _DEFAULT_TEMPLATE_DIR / "task_input.j2"


@dataclass(frozen=True)
class PromptKeyValue:
    key: str
    value: str


@dataclass(frozen=True)
class PromptSkill:
    name: str
    instructions: str


@dataclass(frozen=True)
class AgentPromptModel:
    private_knowledge: list[PromptKeyValue] = field(default_factory=list)
    skills: list[PromptSkill] = field(default_factory=list)
    variables: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TaskPromptModel:
    id: str
    name: str
    description: str
    acceptance_criteria: list[str] = field(default_factory=list)
    key_actions: list[str] = field(default_factory=list)
    verification_goal: str | None = None
    input_json: str = ""
    variables: dict[str, Any] = field(default_factory=dict)


class PromptModelBuilder:
    def __init__(self, settings: AgentPromptConfig) -> None:
        self.settings = settings

    def build_agent_prompt(self, knowledge: KnowledgeBundle, skills: list[SkillBundle]) -> AgentPromptModel:
        return AgentPromptModel(
            private_knowledge=[PromptKeyValue(key=key, value=str(value)) for key, value in knowledge.items.items()],
            skills=[
                PromptSkill(
                    name=skill.name,
                    instructions=skill.instructions or "",
                )
                for skill in skills
            ],
            variables=dict(self.settings.variables),
        )

    def build_task_prompt(
        self,
        task: Task,
        runtime_policy: list[str] | None = None,
        runtime_secret_names: list[str] | None = None,
        runtime_secret_warnings: list[str] | None = None,
    ) -> TaskPromptModel:
        acceptance_policy = (
            "Use the provided key actions as execution guidance and the verification_goal as the only final success target."
            if task.key_actions or task.verification_goal
            else "Use successful flow completion with enough evidence as the success standard."
        )
        task_input = AgentTaskInput(
            task=task,
            acceptance_criteria=list(task.acceptance_criteria),
            key_actions=list(task.key_actions),
            verification_goal=task.verification_goal,
            runtime_policy=list(runtime_policy or []),
            runtime_secret_names=list(runtime_secret_names or []),
            runtime_secret_warnings=list(runtime_secret_warnings or []),
            acceptance_policy=acceptance_policy,
        )
        return TaskPromptModel(
            id=task.id,
            name=task.name,
            description=task.description,
            acceptance_criteria=list(task.acceptance_criteria),
            key_actions=list(task.key_actions),
            verification_goal=task.verification_goal,
            input_json=task_input.model_dump_json(indent=2),
            variables=dict(self.settings.variables),
        )


class PromptRenderer:
    def __init__(self, settings: AgentPromptConfig) -> None:
        self.settings = settings

    def render_agent_prompt(self, model: AgentPromptModel) -> str:
        return self._render_template(self.settings.agent_template_path or _DEFAULT_AGENT_TEMPLATE, asdict(model))

    def render_task_prompt(self, model: TaskPromptModel) -> str:
        return self._render_template(self.settings.task_template_path or _DEFAULT_TASK_TEMPLATE, {"task": asdict(model)})

    def _render_template(self, path: Path, context: dict[str, Any]) -> str:
        template_path = path.expanduser().resolve()
        if not template_path.exists() or not template_path.is_file():
            raise ConfigurationError("Prompt template file does not exist.", context={"path": str(template_path)})
        environment = Environment(
            loader=FileSystemLoader(str(template_path.parent)),
            undefined=StrictUndefined,
            # Prompt templates render model input text, not HTML or other executable markup.
            autoescape=False,  # noqa: S701
            trim_blocks=True,
            lstrip_blocks=True,
            keep_trailing_newline=True,
        )
        try:
            return environment.get_template(template_path.name).render(**context).strip()
        except TemplateError as exc:
            raise ConfigurationError("Unable to render prompt template.", context={"path": str(template_path)}) from exc
