# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from fsq_agent.config import Settings
    from fsq_agent.core.interfaces import EvidenceJournalSink
    from fsq_agent.models import GoalPrePlan, KnowledgeBundle, RunEvent, RunExecutionContext, SkillBundle, StepResult, Task


class CodingAgentRuntime(Protocol):
    async def run_task(
        self,
        task: Task,
        knowledge: KnowledgeBundle,
        skills: list[SkillBundle],
        run_id: str,
        event_sink: Callable[[RunEvent], Awaitable[None]] | None = None,
        *,
        context: RunExecutionContext | None = None,
        evidence_sink: EvidenceJournalSink | None = None,
        cancellation_check: Callable[[], None] | None = None,
    ) -> list[StepResult]: ...

    async def run_pre_plan(
        self,
        reference_text: str,
        knowledge: KnowledgeBundle,
        skills: list[SkillBundle],
        run_id: str,
        event_sink: Callable[[RunEvent], Awaitable[None]] | None = None,
        *,
        reference_type: str = "unknown",
    ) -> GoalPrePlan: ...

    async def run_verification(self, task: Task, results: list[StepResult], run_id: str, events_path: Any, event_sink: Callable[[RunEvent], Awaitable[None]] | None = None) -> list[StepResult]: ...


class CodingAgentRuntimeFactory(Protocol):
    def __call__(self, settings: Settings, *, harness_factory: Any | None = None) -> CodingAgentRuntime: ...
