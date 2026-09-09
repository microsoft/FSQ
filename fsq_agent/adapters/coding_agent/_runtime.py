# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from __future__ import annotations

import asyncio
import inspect
import json
import os
import threading
import time
from typing import TYPE_CHECKING, Any

from fsq_agent._capability_bootstrap import build_capability_registry
from fsq_agent.adapters.coding_agent._harness_tools import HarnessToolAdapter
from fsq_agent.agent import CodingAgentPolicy
from fsq_agent.agent_engine import (
    AgentEngine,
    AgentEvent,
    AgentRequest,
    AgentResult,
    EngineError,
    Model,
    OutputContract,
    ToolBinding,
    ToolCall,
    ToolOutputEntry,
    create_agent_engine_for_model,
)
from fsq_agent.ai_services import build_ai_assertion_evaluator
from fsq_agent.config import Settings, validate_runtime_settings
from fsq_agent.core import (
    ArtifactStore,
    HarnessFactory,
    HarnessInterface,
    RuntimeSecretStore,
)
from fsq_agent.models import AgentFinalOutput, GoalPrePlan, KnowledgeBundle, PlanningError, RunEvent, RunEventSink, SkillBundle, StepResult, Task
from fsq_agent.providers import build_model_provider_session
from fsq_agent.tools import AgentToolAdapter, ToolArtifactStore

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from pydantic import BaseModel

    from fsq_agent.providers import ModelProviderSession

_RUNTIME_TOOL_NAMES = {
    "read_knowledge_index",
    "read_knowledge_page",
}


def _runtime_failure_metadata(exc: BaseException) -> dict[str, str]:
    if isinstance(exc, EngineError) and exc.category == "incomplete" and exc.reason == "content_filter":
        return {
            "failure_category": "provider_content_filter",
            "failure_reason": "content_filter",
            "failure_summary": "Agent runtime execution ended with an incomplete provider response due to content filtering.",
        }
    if isinstance(exc, EngineError) and exc.category == "incomplete":
        return {
            "failure_category": "provider_response_incomplete",
            "failure_reason": "incomplete",
            "failure_summary": "Agent runtime execution ended with an incomplete provider response.",
        }
    return {
        "failure_category": "agent_runtime_error",
        "failure_reason": "agent_runtime_error",
        "failure_summary": "Agent runtime execution failed before producing structured verification output.",
    }


class _ToolOutputBudgetFilter:
    def __init__(
        self,
        recent_inline_outputs: int,
        max_output_chars: int,
        max_total_inline_chars: int,
        artifact_store: ToolArtifactStore | None,
    ) -> None:
        self.recent_inline_outputs = recent_inline_outputs
        self.max_output_chars = max_output_chars
        self.max_total_inline_chars = max_total_inline_chars
        self.artifact_store = artifact_store
        self.artifact_paths_by_call_id: dict[str, str] = {}

    def __call__(self, entries: tuple[ToolOutputEntry, ...]) -> dict[int, str]:
        if not entries:
            return {}
        remaining_inline_chars = self.max_total_inline_chars
        inline_entry_ids: set[int] = set()
        recent_entries = entries[-self.recent_inline_outputs :] if self.recent_inline_outputs else ()
        for entry in reversed(recent_entries):
            output_chars = len(entry.output)
            if output_chars <= self.max_output_chars and output_chars <= remaining_inline_chars:
                inline_entry_ids.add(entry.entry_id)
                remaining_inline_chars -= output_chars

        replacements: dict[int, str] = {}
        for entry in entries:
            if entry.entry_id in inline_entry_ids:
                continue
            display_name = entry.tool_name or "tool"
            artifact_path = self._artifact_path_for(entry)
            if artifact_path:
                replacements[entry.entry_id] = f"[Historical {display_name} output stored as artifact. Artifact path: {artifact_path}. Content chars: {len(entry.output)}.]"
            else:
                replacements[entry.entry_id] = f"[Historical {display_name} output omitted because artifact storage is unavailable. Content chars: {len(entry.output)}.]"
        return replacements

    def _artifact_path_for(self, entry: ToolOutputEntry) -> str | None:
        call_id = entry.call_id or ""
        if call_id in self.artifact_paths_by_call_id:
            return self.artifact_paths_by_call_id[call_id]
        existing_path = self._existing_artifact_path(entry.output)
        if existing_path:
            if call_id:
                self.artifact_paths_by_call_id[call_id] = existing_path
            return existing_path
        if not self.artifact_store:
            return None
        tool_name = entry.tool_name or "runtime_tool"
        path = self.artifact_store.write(tool_name, entry.output, {"source": "model_input_filter", "call_id": call_id})
        if not path:
            return None
        artifact_path = str(path)
        if call_id:
            self.artifact_paths_by_call_id[call_id] = artifact_path
        return artifact_path

    def _existing_artifact_path(self, output_text: str) -> str | None:
        try:
            payload = json.loads(output_text)
        except json.JSONDecodeError:
            return None
        if not isinstance(payload, dict):
            return None
        artifact = payload.get("artifact")
        if not isinstance(artifact, dict):
            return None
        path = artifact.get("path")
        return path if isinstance(path, str) and path else None


class DefaultCodingAgentRuntime:
    def __init__(
        self,
        settings: Settings,
        tool_factory: AgentToolAdapter,
        harness_factory: Callable[[str], HarnessInterface] | None = None,
        policy: CodingAgentPolicy | None = None,
        *,
        engine: AgentEngine | None = None,
    ) -> None:
        self.settings = settings
        self.policy = policy or CodingAgentPolicy()
        self.tool_factory = tool_factory
        self.harness_factory = harness_factory
        self._engine = engine
        self._agent_tool_names = self._discover_agent_tool_names(tool_factory)
        self._harness_tool_names: set[str] = set()
        self._harness_tool_schemas: dict[str, Any] = {}
        self._stream_tool_calls: dict[tuple[str, str, str], dict[str, Any]] = {}

    def _discover_agent_tool_names(self, tool_factory: AgentToolAdapter) -> set[str]:
        registry = getattr(tool_factory, "registry", None)
        list_tools = getattr(registry, "list_tools", None)
        if callable(list_tools):
            return {definition.name for definition in list_tools()}
        return set()

    def _agent_tool_providers(self) -> list[Any] | None:
        registry = getattr(self.tool_factory, "registry", None)
        list_providers = getattr(registry, "list_providers", None)
        if callable(list_providers):
            return list(list_providers())
        return None

    async def run_task(
        self,
        task: Task,
        knowledge: KnowledgeBundle,
        skills: list[SkillBundle],
        run_id: str,
        event_sink: RunEventSink | None = None,
    ) -> list[StepResult]:
        validate_runtime_settings(self.settings)
        started = time.perf_counter()
        provider_session = None
        result = None
        usage_event_emitted = False
        try:
            await self._emit(
                event_sink,
                RunEvent(
                    run_id=run_id,
                    task_id=task.id,
                    type="planning_update",
                    title="Runtime startup started",
                    message="Preparing provider, harness, tools, and agent runtime for main execution.",
                    payload={"platform": self.settings.harness.platform},
                ),
            )
            await self._emit(
                event_sink,
                RunEvent(
                    run_id=run_id,
                    task_id=task.id,
                    type="planning_update",
                    title="Provider setup started",
                    message="Creating the configured model provider session.",
                    payload={"provider": self.settings.agent_runtime.provider, "model": self.settings.agent_runtime.model},
                ),
            )
            provider_session = build_model_provider_session(self.settings)
            model = provider_session.get_model()
            await self._emit(
                event_sink,
                RunEvent(
                    run_id=run_id,
                    task_id=task.id,
                    type="planning_update",
                    title="Provider setup completed",
                    message="Model provider session is ready for the main execution agent.",
                    payload={"provider": self.settings.agent_runtime.provider, "model": self.settings.agent_runtime.model},
                ),
            )
            try:
                await self._emit(
                    event_sink,
                    RunEvent(
                        run_id=run_id,
                        task_id=task.id,
                        type="planning_update",
                        title="Harness setup started",
                        message="Constructing the platform harness for main execution.",
                        payload=self._harness_setup_payload(),
                    ),
                )
                harness = await self._build_harness_with_timeout(run_id)
                await self._emit(
                    event_sink,
                    RunEvent(
                        run_id=run_id,
                        task_id=task.id,
                        type="planning_update",
                        title="Harness setup completed",
                        message="Platform harness is ready for tool schema discovery.",
                        payload=self._harness_setup_payload(harness),
                    ),
                )
                await self._emit(
                    event_sink,
                    RunEvent(
                        run_id=run_id,
                        task_id=task.id,
                        type="planning_update",
                        title="Tool setup started",
                        message="Building AgentTools and platform tools for the agent runtime.",
                    ),
                )
                harness_adapter = HarnessToolAdapter(
                    harness,
                    run_id=run_id,
                    reserved_tool_names={*self._agent_tool_names, *_RUNTIME_TOOL_NAMES},
                    post_action_delay_seconds=self.settings.execution.post_action_delay_seconds,
                    runtime_secret_store=self._runtime_secret_store(),
                    platform=self.settings.harness.platform,
                )
                self._harness_tool_names = harness_adapter.tool_names
                self._harness_tool_schemas = harness_adapter.schemas_by_name
                agent_tools = self.tool_factory.build_tools(
                    run_id=run_id,
                    task_id=task.id,
                    event_sink=None,
                    runner_invoker=harness_adapter.run_step_with_capability_result,
                )
                harness_tools = harness_adapter.build_tools()
                await self._emit(
                    event_sink,
                    RunEvent(
                        run_id=run_id,
                        task_id=task.id,
                        type="planning_update",
                        title="Tool setup completed",
                        message="AgentTools and platform tools are ready for main execution.",
                        payload={"agent_tool_count": len(agent_tools), "platform_tool_count": len(harness_tools)},
                    ),
                )
                request = self._build_request(
                    name=self.settings.agent.name,
                    instructions=self._build_instructions(knowledge, skills),
                    model_input=self._build_task_input(task),
                    tools=[*agent_tools, *harness_tools],
                    output_type=AgentFinalOutput,
                    run_id=run_id,
                )
                await self._emit(
                    event_sink,
                    RunEvent(
                        run_id=run_id,
                        task_id=task.id,
                        type="planning_update",
                        title="Agent runtime ready",
                        message="Main execution agent is ready to start streamed planning.",
                        payload={"tool_count": len(agent_tools) + len(harness_tools)},
                    ),
                )
                await self._emit(
                    event_sink,
                    RunEvent(
                        run_id=run_id,
                        task_id=task.id,
                        type="planning_started",
                        title="Planning started",
                        message="The agent is deriving success criteria and preparing the first actions.",
                    ),
                )
                result = await self._run_agent(model, request, run_id, task.id, event_sink)
                usage_event = self._dynamic_agent_token_usage_event(result, run_id, task.id)
                if usage_event is not None:
                    usage_event_emitted = True
                    await self._emit(event_sink, usage_event)
            # Engine and provider packages raise implementation-specific exceptions that become failed steps.
            except Exception as exc:  # noqa: BLE001
                duration_ms = int((time.perf_counter() - started) * 1000)
                failure_metadata = _runtime_failure_metadata(exc)
                error_message = self._replace_secret_values(str(exc), self._runtime_secret_values())
                if result is not None and not usage_event_emitted:
                    usage_event = self._dynamic_agent_token_usage_event(result, run_id, task.id)
                    if usage_event is not None:
                        usage_event_emitted = True
                        await self._emit(event_sink, usage_event)
                await self._emit(
                    event_sink,
                    RunEvent(
                        run_id=run_id,
                        task_id=task.id,
                        type="run_failed",
                        title="Agent run failed",
                        message=error_message,
                        duration_ms=duration_ms,
                        payload=failure_metadata,
                    ),
                )
                return [
                    StepResult(
                        step_id=1,
                        status="failed",
                        actual_outcome=failure_metadata["failure_summary"],
                        duration_ms=duration_ms,
                        error=error_message,
                        tool_name="agent_runtime.runner",
                        tool_output=failure_metadata,
                    )
                ]
        # Runtime startup dependencies may fail with package-specific exceptions that become failed steps.
        except Exception as exc:  # noqa: BLE001
            duration_ms = int((time.perf_counter() - started) * 1000)
            failure_metadata = _runtime_failure_metadata(exc)
            error_message = self._replace_secret_values(str(exc), self._runtime_secret_values())
            await self._emit(
                event_sink,
                RunEvent(
                    run_id=run_id,
                    task_id=task.id,
                    type="run_failed",
                    title="Agent run failed",
                    message=error_message,
                    duration_ms=duration_ms,
                    payload=failure_metadata,
                ),
            )
            return [
                StepResult(
                    step_id=1,
                    status="failed",
                    actual_outcome=failure_metadata["failure_summary"],
                    duration_ms=duration_ms,
                    error=error_message,
                    tool_name="agent_runtime.runner",
                    tool_output=failure_metadata,
                )
            ]
        finally:
            if provider_session is not None:
                await self._close_provider_session(provider_session, failed=result is None)

        duration_ms = int((time.perf_counter() - started) * 1000)
        if result is None:
            return [
                StepResult(
                    step_id=1,
                    status="failed",
                    actual_outcome="Agent runtime execution ended before producing a streamed result.",
                    duration_ms=duration_ms,
                    error="Agent runtime execution ended before producing a streamed result.",
                    tool_name="agent_runtime.runner",
                )
            ]
        final_output = self.policy.coerce_agent_final_output(result.final_output) or str(result.final_output)
        final_output = self._redact_runtime_secrets(final_output)
        serialized_final_output = self.policy.serialize_agent_final_output(final_output)
        pre_plan_steps = self._build_pre_plan_step_results(final_output)
        structured_steps = pre_plan_steps
        return [
            *structured_steps,
            StepResult(
                step_id=len(structured_steps) + 1,
                status="success",
                actual_outcome=serialized_final_output,
                duration_ms=duration_ms,
                tool_name="agent_runtime.runner",
                tool_output=final_output.model_dump(mode="json") if isinstance(final_output, AgentFinalOutput) else serialized_final_output,
            ),
        ]

    async def _build_harness_with_timeout(self, run_id: str) -> HarnessInterface:
        timeout_seconds = self.settings.agent.step_timeout_seconds

        loop = asyncio.get_running_loop()
        future: asyncio.Future[HarnessInterface] = loop.create_future()

        def set_result(harness: HarnessInterface) -> None:
            if not future.done():
                future.set_result(harness)

        def set_exception(exc: Exception) -> None:
            if not future.done():
                future.set_exception(exc)

        def build_harness() -> None:
            try:
                harness = self._build_harness(run_id)
            # Backend construction failures must be forwarded from the worker thread to the event loop.
            except Exception as exc:  # noqa: BLE001
                try:
                    loop.call_soon_threadsafe(set_exception, exc)
                except RuntimeError:
                    pass
            else:
                try:
                    loop.call_soon_threadsafe(set_result, harness)
                except RuntimeError:
                    pass

        threading.Thread(target=build_harness, name=f"fsq-harness-setup-{run_id}", daemon=True).start()
        try:
            return await asyncio.wait_for(future, timeout=timeout_seconds)
        except TimeoutError as exc:
            if future.done() and not future.cancelled():
                raise
            future.cancel()
            raise TimeoutError(f"Harness setup timed out after {timeout_seconds} seconds.") from exc

    def _harness_setup_payload(self, harness: HarnessInterface | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "platform": self.settings.harness.platform,
            "timeout_seconds": self.settings.agent.step_timeout_seconds,
        }
        if self.settings.harness.platform == "android":
            android = self.settings.harness.android
            payload.update(
                {
                    "backend": android.backend,
                    "app_id_configured": bool(android.app_id),
                    "serial_selected": bool(android.serial),
                }
            )
        if self.settings.harness.platform == "web":
            web = self.settings.harness.web
            payload.update(
                {
                    "backend": web.backend,
                    "channel": web.channel,
                    "browser_executable_configured": web.browser_executable_path is not None,
                    "headless": web.headless,
                    "base_url_configured": bool(web.base_url),
                    "viewport_configured": web.viewport_width is not None and web.viewport_height is not None,
                }
            )
        if self.settings.harness.platform == "windows":
            windows = self.settings.harness.windows
            payload.update(
                {
                    "backend": windows.backend,
                    "backend_kind": windows.backend_kind,
                    "app_path_configured": windows.app_path is not None,
                    "window_title_re_configured": windows.window_title_re is not None,
                }
            )
        if self.settings.harness.platform == "macos":
            macos = self.settings.harness.macos
            payload.update(
                {
                    "backend": macos.backend,
                    "appium_server_configured": macos.appium_server_url is not None,
                    "bundle_id_configured": macos.bundle_id is not None,
                    "app_path_configured": macos.app_path is not None,
                    "action_timeout_seconds": macos.action_timeout_seconds,
                    "new_command_timeout_seconds": macos.new_command_timeout_seconds,
                    "configured_skill_names": [skill.name for skill in self.settings.skills],
                }
            )
        if harness is not None:
            payload["harness_class"] = type(harness).__name__
            driver = getattr(harness, "driver", None)
            if driver is not None:
                payload["driver_class"] = type(driver).__name__
        return payload

    async def run_pre_plan(
        self,
        reference_text: str,
        knowledge: KnowledgeBundle,
        skills: list[SkillBundle],
        run_id: str,
        event_sink: RunEventSink | None = None,
        reference_type: str = "goal",
    ) -> GoalPrePlan:
        validate_runtime_settings(self.settings)
        task_id = "pre-plan"
        started = time.perf_counter()
        await self._emit(
            event_sink,
            RunEvent(
                run_id=run_id,
                task_id=task_id,
                type="planning_started",
                title="Pre-plan started",
                message="Generating key actions from the planning reference and page knowledge.",
            ),
        )
        provider_session = build_model_provider_session(self.settings)
        result = None
        try:
            model = provider_session.get_model()
            request = self._build_request(
                name=f"{self.settings.agent.name} pre-planner",
                instructions=self.policy.pre_plan_instructions,
                tools=self._build_pre_plan_tools(),
                output_type=GoalPrePlan,
                model_input=self.policy.build_pre_plan_input(
                    reference_text,
                    knowledge,
                    skills,
                    reference_type=reference_type,
                    available_platform_tools=self._pre_plan_tool_summary(),
                    runtime_secret_names=list(self._runtime_secret_store().available_names()),
                    runtime_secret_warnings=list(self._runtime_secret_store().warnings()),
                ),
                run_id=run_id,
            )
            result = await self._run_agent(model, request, run_id, task_id, event_sink)
        except Exception as exc:
            duration_ms = int((time.perf_counter() - started) * 1000)
            await self._emit(
                event_sink,
                RunEvent(
                    run_id=run_id,
                    task_id=task_id,
                    type="run_failed",
                    title="Pre-plan failed",
                    message=self._replace_secret_values(str(exc), self._runtime_secret_values()),
                    duration_ms=duration_ms,
                ),
            )
            raise PlanningError("Goal pre-plan failed before producing structured output.", context={"error": str(exc)}) from exc
        finally:
            await self._close_provider_session(provider_session, failed=result is None)

        pre_plan = result.final_output
        if isinstance(pre_plan, GoalPrePlan):
            return pre_plan
        try:
            if isinstance(pre_plan, str):
                return GoalPrePlan.model_validate_json(pre_plan)
            return GoalPrePlan.model_validate(pre_plan)
        except Exception as exc:
            raise PlanningError("Goal pre-plan output did not match the expected schema.") from exc

    def _build_pre_plan_tools(self) -> list[ToolBinding]:
        return [
            ToolBinding(
                name="read_knowledge_index",
                description=(
                    "Read available pre-plan knowledge entries, including project.md and the page index when present. "
                    "Use this to reload project guidance or resolve page ids before loading page details."
                ),
                parameters_schema=self.policy.read_knowledge_index_schema.model_json_schema(),
                invoke=self._read_knowledge_index_tool,
            ),
            ToolBinding(
                name="read_knowledge_page",
                description=(
                    "Read one optional page knowledge node from the pre-plan knowledge directory by page_id or relative file path. Use this only for pages needed to continue the goal action chain."
                ),
                parameters_schema=self.policy.read_knowledge_page_schema.model_json_schema(),
                invoke=self._read_knowledge_page_tool,
            ),
        ]

    def _pre_plan_tool_summary(self) -> list[dict[str, Any]]:
        registry = build_capability_registry(platform=self.settings.harness.platform)
        return [
            {
                "name": capability.name,
                "alias": capability.replay.alias if capability.replay else None,
                "description": capability.description,
                "executor_kind": capability.executor_kind,
                "step_kind": capability.step_kind,
                "platform": capability.platform,
            }
            for capability in registry.snapshot().capabilities
        ]

    def _pre_plan_knowledge_dir(self) -> Path:
        knowledge = self.settings.agent_context.knowledge
        return knowledge.pre_plan.dir or knowledge.root_dir

    def _pre_plan_entry_paths(self) -> list[tuple[str, Path]]:
        knowledge = self.settings.agent_context.knowledge
        return [
            ("project.md", knowledge.root_dir / "project.md"),
            ("index.md", self._pre_plan_knowledge_dir() / "index.md"),
        ]

    async def _read_knowledge_index_tool(self, call: ToolCall) -> str:
        try:
            self.policy.read_knowledge_index_schema.model_validate(call.arguments)
        except ValueError:
            return json.dumps({"ok": False, "error": "Invalid knowledge index arguments."}, ensure_ascii=False)
        entries: list[dict[str, str]] = []
        for entry_path, path in self._pre_plan_entry_paths():
            try:
                if not path.exists():
                    continue
                entries.append({"path": entry_path, "content": path.read_text(encoding="utf-8")})
            except (OSError, ValueError):
                return json.dumps({"ok": False, "error": "Knowledge entry could not be read.", "path": entry_path}, ensure_ascii=False)
        content = "\n\n".join(f"--- {entry['path']} ---\n{entry['content']}" for entry in entries)
        path = entries[0]["path"] if entries else None
        return json.dumps({"ok": True, "path": path, "content": content, "entries": entries}, ensure_ascii=False)

    async def _read_knowledge_page_tool(self, call: ToolCall) -> str:
        try:
            parsed = self.policy.read_knowledge_page_schema.model_validate(call.arguments)
        except ValueError:
            return json.dumps({"ok": False, "error": "Invalid knowledge page arguments."}, ensure_ascii=False)
        relative_path = None
        knowledge_dir = self._pre_plan_knowledge_dir()
        try:
            if parsed.file:
                relative_path = self.policy.safe_page_relative_path(parsed.file)
            elif parsed.page_id:
                index_path = knowledge_dir / "index.md"
                index_text = index_path.read_text(encoding="utf-8") if index_path.exists() else ""
                indexed_file = self.policy.page_file_from_index(index_text, parsed.page_id)
                relative_path = self.policy.safe_page_relative_path(indexed_file or f"{parsed.page_id}.md")
            if relative_path is None:
                return json.dumps(
                    {"ok": False, "error": "A safe page_id or relative page file is required.", "page_id": parsed.page_id, "file": parsed.file},
                    ensure_ascii=False,
                )
            path = (knowledge_dir / relative_path).resolve()
            try:
                path.relative_to(knowledge_dir.resolve())
            except ValueError:
                return json.dumps({"ok": False, "error": "Resolved page path escaped the knowledge directory."}, ensure_ascii=False)
            if not path.exists() or not path.is_file():
                return json.dumps(
                    {"ok": False, "error": "Knowledge page not found.", "page_id": parsed.page_id, "path": str(relative_path).replace("\\", "/")},
                    ensure_ascii=False,
                )
            content = path.read_text(encoding="utf-8")
        except (OSError, ValueError):
            return json.dumps(
                {"ok": False, "error": "Knowledge page could not be read.", "path": str(relative_path).replace("\\", "/") if relative_path is not None else "index.md"},
                ensure_ascii=False,
            )
        return json.dumps(
            {"ok": True, "page_id": parsed.page_id, "path": str(relative_path).replace("\\", "/"), "content": content},
            ensure_ascii=False,
        )

    async def run_verification(
        self,
        task: Task,
        execution_results: list[StepResult],
        run_id: str,
        events_path: Any = None,
        event_sink: RunEventSink | None = None,
    ) -> list[StepResult]:
        validate_runtime_settings(self.settings)
        started = time.perf_counter()
        await self._emit(
            event_sink,
            RunEvent(
                run_id=run_id,
                task_id=task.id,
                type="planning_update",
                title="Verification started",
                message="Running evidence-based verifier agent over execution records and artifacts.",
            ),
        )
        evidence_input = self.policy.build_verification_input(task, execution_results, events_path)
        evidence_input = self._replace_secret_values(evidence_input, self._runtime_secret_values())
        provider_session = build_model_provider_session(self.settings)
        result = None
        try:
            model = provider_session.get_model()
            request = self._build_request(
                name=f"{self.settings.agent.name} verifier",
                instructions=self.policy.verification_instructions,
                tools=[],
                output_type=AgentFinalOutput,
                model_input=evidence_input,
                run_id=run_id,
            )
            result = await self._run_agent(model, request, run_id, task.id, event_sink)
        # Verifier engine/provider failures must become reportable verification step failures.
        except Exception as exc:  # noqa: BLE001
            duration_ms = int((time.perf_counter() - started) * 1000)
            return [
                StepResult(
                    step_id=len(execution_results) + 1,
                    status="failed",
                    actual_outcome="Evidence-based verifier agent failed before producing structured output.",
                    duration_ms=duration_ms,
                    error=self._replace_secret_values(str(exc), self._runtime_secret_values()),
                    tool_name="agent_runtime.verifier",
                )
            ]
        finally:
            await self._close_provider_session(provider_session, failed=result is None)

        duration_ms = int((time.perf_counter() - started) * 1000)
        final_output = self.policy.coerce_agent_final_output(result.final_output) or str(result.final_output)
        final_output = self._redact_runtime_secrets(final_output)
        serialized_final_output = self.policy.serialize_agent_final_output(final_output)
        return [
            StepResult(
                step_id=len(execution_results) + 1,
                status="success",
                actual_outcome=serialized_final_output,
                duration_ms=duration_ms,
                tool_name="agent_runtime.verifier",
                tool_output=final_output.model_dump(mode="json") if isinstance(final_output, AgentFinalOutput) else serialized_final_output,
            )
        ]

    async def _close_provider_session(self, session: ModelProviderSession, *, failed: bool) -> None:
        try:
            await session.close()
        except BaseException:
            if not failed:
                raise

    async def _emit(self, event_sink: RunEventSink | None, event: RunEvent) -> None:
        if not event_sink:
            return
        result = event_sink(event)
        if inspect.isawaitable(result):
            await result

    def _dynamic_agent_token_usage_event(self, result: Any, run_id: str, task_id: str) -> RunEvent | None:
        usage = getattr(result, "usage", None)
        if usage is None:
            return None
        payload = {
            "provider": self.settings.agent_runtime.provider,
            "model": self.settings.agent_runtime.model,
            "requests": getattr(usage, "requests", None),
            "input_tokens": getattr(usage, "input_tokens", None),
            "output_tokens": getattr(usage, "output_tokens", None),
            "total_tokens": getattr(usage, "total_tokens", None),
            "cached_input_tokens": getattr(usage, "cached_input_tokens", None),
            "reasoning_tokens": getattr(usage, "reasoning_tokens", None),
        }
        reported = {key: value for key, value in payload.items() if value is not None}
        if not any(key in reported for key in ("requests", "input_tokens", "output_tokens", "total_tokens")):
            return None
        return RunEvent(
            run_id=run_id,
            task_id=task_id,
            type="dynamic_agent_token_usage",
            title="Dynamic Agent token usage",
            message="Provider usage for the Dynamic Agent main execution.",
            payload=reported,
        )

    def _tracing_disabled(self) -> bool:
        if not self.settings.agent_runtime.tracing_enabled:
            return True
        export_api_key = os.getenv("OPENAI_API_KEY")
        return not bool(export_api_key and export_api_key.strip())

    def _build_request(self, *, name: str, instructions: str, model_input: str, tools: list[ToolBinding], output_type: type[BaseModel], run_id: str = "") -> AgentRequest:
        local_output = self.settings.agent_runtime.local_tool_output
        artifact_store = ToolArtifactStore(self.settings.output.runs_dir, run_id, local_output) if run_id and local_output.artifact_enabled else None
        input_filter = _ToolOutputBudgetFilter(
            local_output.recent_inline_output_count,
            local_output.full_output_max_chars,
            local_output.total_inline_output_max_chars,
            artifact_store,
        )
        return AgentRequest(
            name=name,
            instructions=instructions,
            input=model_input,
            tools=tuple(tools),
            output=OutputContract(name=output_type.__name__, schema=output_type.model_json_schema(), parse=output_type.model_validate_json),
            max_turns=self.settings.agent_runtime.max_turns,
            stream=True,
            tool_output_filter=input_filter,
            tracing_enabled=not self._tracing_disabled(),
        )

    async def _run_agent(self, model: Model, request: AgentRequest, run_id: str, task_id: str, event_sink: RunEventSink | None) -> AgentResult:
        engine = self._engine if self._engine is not None else create_agent_engine_for_model(model)

        async def on_event(event: AgentEvent) -> None:
            run_event = self._map_stream_event(event, run_id, task_id)
            if run_event is not None:
                await self._emit(event_sink, run_event)

        try:
            return await engine.run(model, request, on_event=on_event)
        finally:
            for key in list(self._stream_tool_calls):
                if key[:2] == (run_id, task_id):
                    del self._stream_tool_calls[key]

    def _build_harness(self, run_id: str) -> HarnessInterface:
        if self.harness_factory is not None:
            return self.harness_factory(run_id)
        return HarnessFactory().create_harness(
            platform=self.settings.harness.platform,
            harness_settings=self.settings.harness,
            artifact_store=ArtifactStore(self.settings.output.runs_dir / run_id),
            ai_assertion_evaluator=build_ai_assertion_evaluator(self.settings),
            runtime_secret_settings=self.settings.runtime_secrets,
            app_id=self.settings.harness.android.app_id,
        )

    def _map_stream_event(self, event: AgentEvent, run_id: str, task_id: str) -> RunEvent | None:
        if event.kind == "agent_started":
            return RunEvent(run_id=run_id, task_id=task_id, type="agent_started", title="Agent updated", message=event.agent_name or "agent")
        if event.kind == "tool_called":
            tool_name = event.tool_name
            tool_call_id = event.call_id
            tool_arguments = self._redact(event.arguments)
            if tool_call_id:
                self._stream_tool_calls[(run_id, task_id, tool_call_id)] = {
                    "tool_name": tool_name,
                    "tool_arguments": tool_arguments,
                }
            payload = {"tool_origin": self._tool_origin(tool_name)}
            schema = self._harness_tool_schemas.get(tool_name or "")
            if schema is not None:
                payload.update(
                    {
                        "platform": schema.platform,
                        "driver_method": schema.driver_method,
                        "fsq_action_name": schema.fsq_action_name,
                        "step_kind": schema.metadata.get("step_kind"),
                        "metadata": schema.metadata,
                    }
                )
            return RunEvent(
                run_id=run_id,
                task_id=task_id,
                type="tool_call_started",
                title="Tool call started",
                message=f"Calling {tool_name or 'tool'}.",
                tool_name=tool_name,
                tool_call_id=tool_call_id,
                tool_arguments=tool_arguments,
                payload=payload,
            )
        if event.kind == "tool_output":
            output = event.output
            payload = self._tool_output_payload(output)
            tool_call_id = event.call_id
            remembered = self._stream_tool_calls.pop((run_id, task_id, tool_call_id or ""), {})
            tool_name = payload.get("tool_name") or remembered.get("tool_name") or event.tool_name
            duration_ms = payload.get("duration_ms")
            return RunEvent(
                run_id=run_id,
                task_id=task_id,
                type="tool_call_completed",
                title="Tool call completed",
                message="Tool returned output.",
                tool_name=str(tool_name) if tool_name else None,
                tool_call_id=tool_call_id,
                duration_ms=duration_ms if isinstance(duration_ms, int) and duration_ms >= 0 else None,
                tool_output_preview=self._preview(output),
                payload=payload,
            )
        if event.kind == "reasoning_summary":
            summary = self._preview(event.text) if event.text else None
            if not summary:
                return None
            return RunEvent(run_id=run_id, task_id=task_id, type="reasoning_summary", title="Reasoning summary", message=summary)
        if event.kind == "message":
            return RunEvent(
                run_id=run_id,
                task_id=task_id,
                type="planning_update",
                title="Agent message",
                message=self._preview(event.text),
            )
        return None

    def _preview(self, value: Any, limit: int = 1000) -> str:
        text = value if isinstance(value, str) else repr(value)
        text = self._redact_sensitive_tool_output(text)
        text = self._replace_secret_values(text, self._runtime_secret_values())
        text = text.replace("\r", " ").replace("\n", " ")
        return text if len(text) <= limit else f"{text[:limit]}..."

    def _redact_sensitive_tool_output(self, text: str) -> str:
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            return text
        redacted, changed = self._redact_sensitive_payload(payload)
        if changed:
            return json.dumps(redacted, ensure_ascii=False)
        return text

    def _redact_sensitive_payload(self, value: Any) -> tuple[Any, bool]:
        if isinstance(value, dict):
            changed = False
            redacted: dict[str, Any] = {}
            is_sensitive = value.get("sensitive") is True
            for key, item in value.items():
                if is_sensitive and key == "value":
                    redacted[key] = "***"
                    changed = changed or item != "***"
                    continue
                redacted_item, item_changed = self._redact_sensitive_payload(item)
                redacted[key] = redacted_item
                changed = changed or item_changed
            return redacted, changed
        if isinstance(value, list):
            redacted_items: list[Any] = []
            changed = False
            for item in value:
                redacted_item, item_changed = self._redact_sensitive_payload(item)
                redacted_items.append(redacted_item)
                changed = changed or item_changed
            return redacted_items, changed
        return value, False

    def _redact_runtime_secrets(self, value: Any) -> Any:
        secret_values = self._runtime_secret_values()
        if not secret_values:
            return value
        redacted = self._replace_secret_values(value, secret_values)
        if isinstance(value, AgentFinalOutput) and isinstance(redacted, dict):
            return AgentFinalOutput.model_validate(redacted)
        return redacted

    def _runtime_secret_values(self) -> tuple[str, ...]:
        values = self.settings.runtime_secrets.private_values().values()
        return tuple(sorted(set(values), key=len, reverse=True))

    def _replace_secret_values(self, value: Any, secret_values: tuple[str, ...]) -> Any:
        if isinstance(value, AgentFinalOutput):
            return self._replace_secret_values(value.model_dump(mode="json"), secret_values)
        if isinstance(value, dict):
            return {key: self._replace_secret_values(item, secret_values) for key, item in value.items()}
        if isinstance(value, list):
            return [self._replace_secret_values(item, secret_values) for item in value]
        if isinstance(value, str):
            redacted = value
            for secret_value in secret_values:
                redacted = redacted.replace(secret_value, "***")
            return redacted
        return value

    def _redact(self, value: Any) -> Any:
        sensitive = ("token", "api_key", "apikey", "private_key", "privatekey", "secret", "password", "authorization", "cookie")
        secret_values = self._runtime_secret_values()
        if isinstance(value, dict):
            return {key: "***" if any(part in str(key).lower() for part in sensitive) else self._redact(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self._redact(item) for item in value]
        if isinstance(value, str):
            return self._replace_secret_values(value, secret_values)
        return value

    def _offset_step_ids(self, steps: list[StepResult], offset: int) -> list[StepResult]:
        if offset <= 0:
            return steps
        return [step.model_copy(update={"step_id": step.step_id + offset}) for step in steps]

    def _build_pre_plan_step_results(self, final_output: AgentFinalOutput | str) -> list[StepResult]:
        payload = self.policy.coerce_agent_final_output(final_output)
        if not payload:
            return []
        pre_plan = payload.pre_plan
        plan_updates = payload.plan_updates
        steps: list[StepResult] = []
        for index, item in enumerate(pre_plan, start=1):
            steps.append(self._build_pre_plan_step(index, item.model_dump(mode="json"), plan_updates))
        return steps

    def _build_pre_plan_step(
        self,
        fallback_step_id: int,
        item: dict[str, Any],
        plan_updates: list[str],
    ) -> StepResult:
        raw_step_id = item.get("step_id", fallback_step_id)
        step_id = raw_step_id if isinstance(raw_step_id, int) and raw_step_id >= 1 else fallback_step_id
        raw_status = str(item.get("status", "skipped")).lower()
        status = raw_status if raw_status in {"success", "failed", "skipped", "adjusted"} else "skipped"
        action = str(item.get("action") or "Pre-plan step")
        success_criteria = self.policy.coerce_string_list(item.get("success_criteria"))
        lines = [f"Action: {action}"]
        if success_criteria:
            lines.extend(["Success criteria:", *[f"- {criterion}" for criterion in success_criteria]])
        if status == "adjusted" and plan_updates:
            lines.extend(["Plan updates:", *[f"- {update}" for update in plan_updates]])
        return StepResult(
            step_id=step_id,
            status=status,
            actual_outcome="\n".join(lines),
            tool_name="pre_plan",
            tool_output=item,
        )

    def _build_instructions(self, knowledge: KnowledgeBundle, skills: list[SkillBundle]) -> str:
        prompt = self.settings.agent_runtime.prompt
        return self.policy.build_agent_prompt(prompt, knowledge, skills)

    def _build_task_input(self, task: Task, runtime_policy: list[str] | None = None) -> str:
        prompt = self.settings.agent_runtime.prompt
        store = self._runtime_secret_store()
        return self.policy.build_task_prompt(
            prompt,
            task,
            runtime_policy,
            list(store.available_names()),
            list(store.warnings()),
        )

    def _runtime_secret_store(self) -> RuntimeSecretStore:
        return RuntimeSecretStore.from_settings(self.settings.runtime_secrets)

    def _tool_origin(self, tool_name: str | None) -> str:
        if not tool_name:
            return "unknown"
        if tool_name in self._agent_tool_names:
            return "agent_tool"
        if tool_name in _RUNTIME_TOOL_NAMES:
            return "runtime"
        if tool_name in self._harness_tool_names:
            schema = self._harness_tool_schemas.get(tool_name)
            if schema is not None and schema.metadata.get("executor_kind") == "common":
                return "common"
            return "platform"
        return "unknown"

    def _artifact_path_from_output(self, output: Any) -> str | None:
        if isinstance(output, str):
            text = output
        else:
            text = str(output) if output is not None else ""
        if not text:
            return None
        try:
            payload = json.loads(text)
        # Arbitrary malformed tool output is treated as having no artifact reference.
        except json.JSONDecodeError:
            return None
        if not isinstance(payload, dict):
            return None
        artifact = payload.get("artifact")
        if isinstance(artifact, dict) and artifact.get("path"):
            return str(artifact["path"])
        result = payload.get("result")
        if isinstance(result, dict):
            artifact_refs = result.get("artifact_refs")
            if isinstance(artifact_refs, list) and artifact_refs:
                first_ref = artifact_refs[0]
                if isinstance(first_ref, dict) and first_ref.get("path"):
                    return str(first_ref["path"])
        return None

    def _tool_output_payload(self, output: Any) -> dict[str, Any]:
        payload: dict[str, Any] = {"artifact_path": self._artifact_path_from_output(output)}
        parsed = self._json_payload(output)
        if not isinstance(parsed, dict):
            return payload
        parsed_tool_name = parsed.get("tool_name")
        if isinstance(parsed_tool_name, str) and parsed_tool_name:
            payload["tool_name"] = parsed_tool_name
            payload["tool_origin"] = self._tool_origin(parsed_tool_name)
            if payload["tool_origin"] == "agent_tool":
                payload["executor_kind"] = "agent_tool"
        safe_keys = {
            "tool_name",
            "tool_origin",
            "platform",
            "driver_method",
            "fsq_action_name",
            "status",
            "failure_category",
            "error_message",
            "duration_ms",
            "step_kind",
            "replay",
            "safe_replay_params",
            "runner_step_id",
        }
        for key in safe_keys:
            if key in parsed:
                payload[key] = parsed[key]
        runner_result = parsed.get("runner_result")
        if isinstance(runner_result, dict):
            payload["runner_result"] = runner_result
        artifact_refs = parsed.get("artifact_refs")
        if isinstance(artifact_refs, list):
            payload["artifact_refs"] = artifact_refs
        metadata = parsed.get("metadata")
        if isinstance(metadata, dict):
            payload["metadata"] = metadata
        result = parsed.get("result")
        if isinstance(result, dict):
            if "tool_name" not in payload and isinstance(result.get("tool_name"), str):
                result_tool_name = str(result["tool_name"])
                payload["tool_name"] = result_tool_name
                payload["tool_origin"] = self._tool_origin(result_tool_name)
                if payload["tool_origin"] == "agent_tool":
                    payload["executor_kind"] = "agent_tool"
            if "duration_ms" not in payload and isinstance(result.get("duration_ms"), int):
                payload["duration_ms"] = result["duration_ms"]
            if "status" not in payload and result.get("status") is not None:
                payload["status"] = result.get("status")
            if "failure_category" not in payload and result.get("failure_category") is not None:
                payload["failure_category"] = result.get("failure_category")
            if "error_message" not in payload and result.get("error_message") is not None:
                payload["error_message"] = result.get("error_message")
            result_artifact_refs = result.get("artifact_refs")
            if "artifact_refs" not in payload and isinstance(result_artifact_refs, list) and result_artifact_refs:
                payload["artifact_refs"] = result_artifact_refs
        return payload

    def _json_payload(self, output: Any) -> Any:
        if isinstance(output, str):
            text = output
        else:
            text = str(output) if output is not None else ""
        if not text:
            return None
        try:
            return json.loads(text)
        # Arbitrary malformed tool output is treated as an absent JSON payload.
        except json.JSONDecodeError:
            return None
