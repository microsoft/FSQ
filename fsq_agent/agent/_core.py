# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import inspect
import time
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path
from typing import Any

from fsq_agent.agent._events import RunEventEmitter
from fsq_agent.agent._runtime import CodingAgentRuntime, CodingAgentRuntimeFactory
from fsq_agent.agent._verifier import Verifier
from fsq_agent.config import Settings
from fsq_agent.core.interfaces import EvidenceJournalSink
from fsq_agent.knowledge import PrivateKnowledgeLoader
from fsq_agent.models import DynamicAgentOutcome, KnowledgeBundle, PlanningError, RunEvent, RunEventSink, RunExecutionContext, Task
from fsq_agent.observation import ExecutionLogger
from fsq_agent.providers import refresh_model_provider_session
from fsq_agent.skills import SkillLoader


class FsqAgent:
    def __init__(
        self,
        settings: Settings,
        verifier: Verifier,
        reporter: Any,
        knowledge_loader: PrivateKnowledgeLoader,
        skill_loader: SkillLoader,
        runtime: CodingAgentRuntime,
        event_logger: ExecutionLogger | None = None,
    ) -> None:
        self.settings = settings
        self.verifier = verifier
        self.reporter = reporter
        self.knowledge_loader = knowledge_loader
        self.skill_loader = skill_loader
        self.runtime = runtime
        self.event_logger = event_logger

    @classmethod
    def from_settings(
        cls,
        settings: Settings,
        runtime_factory: CodingAgentRuntimeFactory,
        harness_factory: Callable[[str], Any] | None = None,
    ) -> "FsqAgent":
        knowledge = settings.agent_context.knowledge
        knowledge_root = knowledge.root_dir
        skills_dir = knowledge.skills.dir
        knowledge_loader = PrivateKnowledgeLoader(knowledge_root)
        skill_loader = SkillLoader(skills_dir)
        reporter = None
        event_logger = ExecutionLogger(settings.output.runs_dir)
        return cls(
            settings,
            Verifier(),
            reporter,
            knowledge_loader,
            skill_loader,
            runtime_factory(settings, harness_factory=harness_factory),
            event_logger,
        )

    @staticmethod
    def _runtime_secret_values(settings: Settings) -> tuple[str, ...]:
        return tuple(sorted(set(settings.runtime_secrets.private_values().values()), key=len, reverse=True))

    async def run_in_context(
        self,
        task: Task,
        context: RunExecutionContext,
        event_sink: RunEventSink | None = None,
        *,
        evidence_sink: EvidenceJournalSink | None = None,
        cancellation_check: Callable[[], None] | None = None,
    ) -> DynamicAgentOutcome:
        run_id = context.run_id
        if not run_id.strip() or run_id in {".", ".."} or any(character in run_id for character in "/\\:\x00") or Path(run_id).name != run_id:
            raise ValueError("Run ID must be a nonempty single path component.")
        started = time.perf_counter()
        emitter = RunEventEmitter(self.event_logger, event_sink, secret_values=self._runtime_secret_values(self.settings))
        try:
            await emitter.emit(RunEvent(run_id=run_id, task_id=task.id, type="run_started", title="Run started", message=task.name))
            knowledge = self.knowledge_loader.load_for_task(task)
            skills = self.skill_loader.load(self.settings.skills)
            await emitter.emit(
                RunEvent(
                    run_id=run_id,
                    task_id=task.id,
                    type="agent_started",
                    title="Agent context loaded",
                    message=f"Loaded {len(skills)} skills and {len(knowledge.items)} knowledge items.",
                    payload={"skill_count": len(skills), "knowledge_item_count": len(knowledge.items)},
                )
            )
            provider_refresh_session = refresh_model_provider_session(self.settings)
            provider_refresh_session.close_sync()
            task = await self._augment_goal_only_task_with_pre_plan(task, skills, run_id, emitter)
            if cancellation_check is not None:
                cancellation_check()
            results = await self.runtime.run_task(task, knowledge, skills, run_id, emitter.emit, context=context, evidence_sink=evidence_sink, cancellation_check=cancellation_check)
            if cancellation_check is not None:
                cancellation_check()
            events_path = context.run_dir / "events.jsonl" if self.event_logger else None
            results.extend(await self.runtime.run_verification(task, results, run_id, events_path, emitter.emit))
            if cancellation_check is not None:
                cancellation_check()
            verification = await self.verifier.verify(task, results, events_path=events_path)
            await emitter.emit(RunEvent(run_id=run_id, task_id=task.id, type="run_completed", title="Execution completed", message=verification.summary, payload={"status": verification.status}))
            return DynamicAgentOutcome(
                task=task,
                steps=results,
                verification=verification,
                duration_ms=int((time.perf_counter() - started) * 1000),
                errors=[
                    {"category": "agent_runtime_error", "message": step.error or "Runtime failed."}
                    for step in results
                    if step.status == "failed" and step.tool_name in {"agent_runtime.runner", "runtime"}
                ],
            )
        except BaseException as exc:
            with suppress(BaseException):
                await emitter.emit(
                    RunEvent(run_id=run_id, task_id=task.id, type="run_failed", title="Execution interrupted", message=type(exc).__name__, payload={"exception_type": type(exc).__name__})
                )
            raise

    def _load_pre_plan_knowledge(self) -> KnowledgeBundle:
        items: dict[str, str] = {}
        knowledge = self.settings.agent_context.knowledge
        project_path = knowledge.root_dir / "project.md"
        if project_path.exists():
            project_knowledge = project_path.read_text(encoding="utf-8")
            if project_knowledge.strip():
                items["project.md"] = project_knowledge

        pre_plan_dir = knowledge.pre_plan.dir or knowledge.root_dir
        index_path = pre_plan_dir / "index.md"
        if index_path.exists():
            items["index.md"] = index_path.read_text(encoding="utf-8")
        return KnowledgeBundle(items=items)

    async def _augment_goal_only_task_with_pre_plan(
        self,
        task: Task,
        skills: list[object],
        run_id: str,
        emitter: RunEventEmitter,
    ) -> Task:
        if task.key_actions and self._usable_text(task.verification_goal):
            return task

        reference_type, reference_text = self._planning_reference_for_task(task)
        pre_plan = await self._run_pre_plan(reference_type, reference_text, skills, run_id, emitter)
        generated_key_actions = [f"Key action {index}: {action.action.strip()}" for index, action in enumerate(pre_plan.key_actions, start=1) if action.action.strip()]
        key_actions = list(task.key_actions) or generated_key_actions
        verification_goal = pre_plan.verification_goal.strip()
        payload = {
            "key_action_count": len(key_actions),
            "verification_goal_present": bool(verification_goal),
            "relevant_page_ids": pre_plan.relevant_page_ids,
            "warnings": pre_plan.warnings,
        }
        if not key_actions:
            await emitter.emit(
                RunEvent(
                    run_id=run_id,
                    task_id=task.id,
                    type="planning_update",
                    title="Goal pre-plan produced no key actions",
                    message="Pre-plan did not produce a useful key-action chain.",
                    payload=payload,
                )
            )
            raise PlanningError("Goal pre-plan did not produce useful key actions.", context=payload)
        if not verification_goal:
            await emitter.emit(
                RunEvent(
                    run_id=run_id,
                    task_id=task.id,
                    type="planning_update",
                    title="Goal pre-plan produced no verification goal",
                    message="Pre-plan did not produce a usable final verification goal.",
                    payload=payload,
                )
            )
            raise PlanningError("Goal pre-plan did not produce a usable verification goal.", context=payload)

        await emitter.emit(
            RunEvent(
                run_id=run_id,
                task_id=task.id,
                type="planning_update",
                title="Goal pre-plan injected",
                message=pre_plan.summary or f"Generated {len(key_actions)} key actions and one verification goal.",
                payload=payload,
            )
        )
        return task.model_copy(update={"key_actions": key_actions, "verification_goal": verification_goal})

    async def _run_pre_plan(
        self,
        reference_type: str,
        reference_text: str,
        skills: list[object],
        run_id: str,
        emitter: RunEventEmitter,
    ):
        knowledge = self._load_pre_plan_knowledge()
        signature = inspect.signature(self.runtime.run_pre_plan)
        accepts_reference_type = "reference_type" in signature.parameters or any(parameter.kind == inspect.Parameter.VAR_KEYWORD for parameter in signature.parameters.values())
        if accepts_reference_type:
            return await self.runtime.run_pre_plan(
                reference_text,
                knowledge,
                skills,
                run_id,
                emitter.emit,
                reference_type=reference_type,
            )
        return await self.runtime.run_pre_plan(reference_text, knowledge, skills, run_id, emitter.emit)

    def _planning_reference_for_task(self, task: Task) -> tuple[str, str]:
        if task.planning_reference_text and task.planning_reference_text.strip():
            return task.planning_reference_kind or "unknown", task.planning_reference_text.strip()
        return "unknown", self._goal_text_for_task(task)

    def _goal_text_for_task(self, task: Task) -> str:
        if task.verification_goal and task.verification_goal.startswith("Goal completed: "):
            return task.verification_goal.removeprefix("Goal completed: ").strip()
        return task.name if task.name and task.name != "Task" else task.description

    def _usable_text(self, value: str | None) -> bool:
        return bool(value and value.strip())
