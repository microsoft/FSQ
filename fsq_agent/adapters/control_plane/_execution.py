# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from __future__ import annotations

import asyncio
import json
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Never

from fsq_agent._capability_bootstrap import steps_require_provider
from fsq_agent.adapters.coding_agent import create_coding_agent_runtime
from fsq_agent.agent import FsqAgent
from fsq_agent.ai_services import build_ai_assertion_evaluator
from fsq_agent.case_dsl import FsqCaseLoader, FsqExecutableStepAdapter
from fsq_agent.config import Settings, validate_runtime_settings, validate_strict_core_settings, workspace_revision
from fsq_agent.core import ArtifactStore, EvidenceRecorder, HarnessFactory, RuntimeSecretStore
from fsq_agent.execution import (
    DynamicExecutionRequest,
    DynamicExecutionService,
    LifecycleExecutionRequest,
    LifecycleExecutionService,
    RecordingService,
    RunArtifactIndex,
    RunResultSummary,
    RunSource,
    RunStepCounts,
    allocate_run,
    collect_strict_lifecycle_cases,
    run_strict_lifecycle_case,
    transition_run,
)
from fsq_agent.models import ExecutableStep, FsqCase, RunnerEvent, RunnerStepResult, Task

from ._cases import build_strict_registry_context, resolve_case
from ._evidence import EvidenceProjection, configured_secret_values, safe_exception_message
from ._readiness import require_android_preflight, require_macos_preflight, require_provider
from ._state import ControlPlaneState, TaskCancelledError
from ._targets import validate_target


@dataclass
class PreparedRun:
    request_id: str
    settings: Settings
    workspace_name: str
    platform_revision: str
    mode: str
    target_id: str
    goal: str | None = None
    case_path: Path | None = None
    case: FsqCase | None = None
    registry: Any | None = None
    registry_snapshot: Any | None = None
    lifecycle_cases: list[tuple[Path, FsqCase]] = field(default_factory=list)
    resolved_steps_by_path: dict[Path, list[ExecutableStep]] = field(default_factory=dict)
    requires_ai_assertion: bool = False


@dataclass
class ExecutionHandle:
    request_id: str
    thread: threading.Thread | None = None
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _loop: asyncio.AbstractEventLoop | None = None
    _task: asyncio.Task[None] | None = None

    def attach(self, loop: asyncio.AbstractEventLoop, task: asyncio.Task[None]) -> None:
        with self._lock:
            self._loop = loop
            self._task = task

    def cancel(self) -> None:
        with self._lock:
            loop = self._loop
            task = self._task
        if loop is not None and task is not None and not task.done():
            loop.call_soon_threadsafe(task.cancel)


def prepare_run(*, request_id: str, settings: Settings, body: dict[str, Any]) -> PreparedRun:
    mode = body.get("mode")
    workspace_name = body.get("workspaceName")
    platform = body.get("platform")
    target_id = body.get("targetId")
    if mode not in {"explore", "strict"}:
        raise ValueError("mode must be explore or strict.")
    expected_fields = {"mode", "workspaceName", "platform", "targetId", "goal" if mode == "explore" else "casePath"}
    if set(body) != expected_fields:
        raise ValueError(f"{mode} run fields must be exactly {', '.join(sorted(expected_fields))}.")
    if not isinstance(workspace_name, str) or not workspace_name.strip():
        raise TypeError("workspaceName is required.")
    if not isinstance(platform, str) or platform != settings.harness.platform:
        raise ValueError("platform does not match the loaded platform preset.")
    if not isinstance(target_id, str):
        raise TypeError("targetId is required.")
    config_path = settings.workspace.config_path
    if not isinstance(config_path, Path):
        raise TypeError("Selected workspace platform configuration is unavailable.")
    platform_revision = workspace_revision(config_path)
    if platform not in {"macos", "android"}:
        validate_target(settings, target_id)
    elif platform == "macos" and target_id != "macos-app":
        raise ValueError("Select the configured macOS application target.")
    run_settings = settings.model_copy(deep=True)
    if platform == "android":
        from fsq_agent.models import AndroidDevice

        AndroidDevice(serial=target_id, state="device")
        run_settings.harness.android.serial = target_id

    if mode == "explore":
        goal = body.get("goal")
        if not isinstance(goal, str) or not " ".join(goal.split()):
            raise ValueError("Explore runs require a non-empty goal.")
        if body.get("casePath") is not None:
            raise ValueError("Explore runs must not include casePath.")
        require_macos_preflight(run_settings, "explore")
        require_android_preflight(run_settings, "explore")
        validate_runtime_settings(run_settings)
        require_provider(run_settings)
        return PreparedRun(
            request_id=request_id,
            settings=run_settings,
            workspace_name=workspace_name.strip(),
            platform_revision=platform_revision,
            mode=mode,
            target_id=target_id,
            goal=" ".join(goal.split()),
        )

    if body.get("goal") is not None:
        raise ValueError("Strict runs must not include goal.")
    case_path = resolve_case(run_settings, body.get("casePath"))
    case = FsqCaseLoader().load_case(case_path)
    registry, snapshot, provider_required = build_strict_registry_context(run_settings.harness.platform)
    lifecycle_cases = collect_strict_lifecycle_cases(
        case_path=case_path,
        case=case,
        settings=run_settings,
        validate_case_path=lambda candidate: _require_strict_lifecycle_containment(candidate, run_settings.cases.dir),
    )
    resolved_steps: dict[Path, list[ExecutableStep]] = {}
    requires_ai = False
    secret_store = RuntimeSecretStore.from_settings(run_settings.runtime_secrets)
    for lifecycle_path, lifecycle_case in lifecycle_cases:
        _require_strict_lifecycle_containment(lifecycle_path, run_settings.cases.dir)
        _validate_case_platform(run_settings, lifecycle_case)
        _validate_android_app_id(run_settings, root_case=case, case=lifecycle_case)
        steps = FsqExecutableStepAdapter(registry_snapshot=snapshot).to_executable_steps(lifecycle_case)
        _preflight_steps(steps, snapshot, secret_store)
        resolved_steps[lifecycle_path.resolve()] = steps
        requires_ai = requires_ai or steps_require_provider(steps, snapshot, provider_required)
    require_macos_preflight(run_settings, "strict", requires_provider=requires_ai)
    if platform == "android":
        run_settings.harness.android.app_id = _android_app_id(run_settings, case)
        require_android_preflight(run_settings, "strict", requires_provider=requires_ai)
    validate_strict_core_settings(run_settings, requires_ai_assertion=requires_ai)
    if requires_ai:
        require_provider(run_settings)
    return PreparedRun(
        request_id=request_id,
        settings=run_settings,
        workspace_name=workspace_name.strip(),
        platform_revision=platform_revision,
        mode=mode,
        target_id=target_id,
        case_path=case_path,
        case=case,
        registry=registry,
        registry_snapshot=snapshot,
        lifecycle_cases=lifecycle_cases,
        resolved_steps_by_path=resolved_steps,
        requires_ai_assertion=requires_ai,
    )


def _require_strict_lifecycle_containment(case_path: Path, cases_dir: Path) -> None:
    try:
        case_path.resolve().relative_to(cases_dir.resolve())
    except ValueError as exc:
        raise ValueError("Strict lifecycle dependency escapes the configured cases directory.") from exc


def start_execution(prepared: PreparedRun, state: ControlPlaneState) -> ExecutionHandle:
    handle = ExecutionHandle(request_id=prepared.request_id)
    thread = threading.Thread(target=_execution_thread, args=(prepared, state, handle), name=f"fsq-control-plane-{prepared.request_id}", daemon=True)
    handle.thread = thread
    thread.start()
    return handle


def _execution_thread(prepared: PreparedRun, state: ControlPlaneState, handle: ExecutionHandle) -> None:
    if prepared.mode == "strict":
        _run_strict(prepared, state)
        return
    with asyncio.Runner() as runner:
        loop = runner.get_loop()
        task = loop.create_task(_run_explore(prepared, state))
        handle.attach(loop, task)
        try:
            loop.run_until_complete(task)
        except asyncio.CancelledError:
            state.finish(prepared.request_id, status="cancelled", summary="Run cancelled.")


async def _run_explore(prepared: PreparedRun, state: ControlPlaneState) -> None:
    settings = prepared.settings
    request_id = prepared.request_id
    projection = _projection(settings, state, request_id)
    try:
        state.raise_if_cancelled(request_id)
        state.transition(request_id, "running", summary="Explore run is executing.")
        task = _task_from_goal(prepared.goal or "", request_id)
        execution = await DynamicExecutionService(
            agent=FsqAgent.from_settings(settings, create_coding_agent_runtime),
            recording_service=RecordingService(),
        ).execute(
            DynamicExecutionRequest(
                task=task,
                settings=settings,
                event_sink=projection.project_run_event,
                record=True,
                allow_recording_failure=True,
                cancellation_check=lambda: state.raise_if_cancelled(request_id),
                recording_error_sink=lambda exc: state.add_event(
                    request_id,
                    {
                        "time": None,
                        "phase": "finalizing",
                        "label": "Dynamic recording",
                        "tool": None,
                        "status": "failed",
                        "durationMs": None,
                        "message": projection.safe_text(f"Recording failed: {exc}"),
                        "level": "warning",
                    },
                ),
            )
        )
        result = execution.task_result
        if state.is_cancel_requested(request_id):
            _raise_async_cancelled()
        projection.bind_run(result.report.run_id)
        state.transition(request_id, "finalizing", summary="Finalizing evidence, report, and recording.")
        projection.load_persisted_manifest()
        projection.load_persisted_step_ids()
        state.finish(
            request_id,
            status=result.status,
            summary=projection.safe_text(result.verification.summary),
            result={
                "status": result.status,
                "durationMs": result.duration_ms,
                "suggestedCaseName": execution.recording.case_name if execution.recording else None,
                "recordingDraft": execution.recording.draft if execution.recording else None,
            },
            report_available=result.report.path.exists(),
        )
    except asyncio.CancelledError:
        state.finish(request_id, status="cancelled", summary="Run cancelled.")
        raise
    except Exception as exc:  # noqa: BLE001 - background failures are normalized into task state.
        state.finish(request_id, status="error", summary=safe_exception_message(exc, settings=settings, unexpected=True))


def _run_strict(prepared: PreparedRun, state: ControlPlaneState) -> None:
    settings = prepared.settings
    request_id = prepared.request_id
    metadata = None
    run_dir = None
    evaluator = None
    try:
        workspace_root = settings.workspace.root_dir
        if workspace_root is None:
            _raise_workspace_root_unavailable()
        case_id = prepared.case.id if prepared.case else "strict"
        metadata = allocate_run(
            workspace=workspace_root,
            workspace_name=prepared.workspace_name,
            platform=settings.harness.platform,
            source_id=case_id,
            mode="strict",
            source=RunSource(
                kind="case",
                case_id=case_id,
                case_path=str(prepared.case_path.relative_to(workspace_root))
                if prepared.case_path and prepared.case_path.is_relative_to(workspace_root)
                else prepared.case_path.name
                if prepared.case_path
                else None,
            ),
            platform_runs_dir=Path(settings.output.runs_dir),
        )
        run_id = metadata.run_id
        run_dir = Path(settings.output.runs_dir) / run_id
        projection = _projection(settings, state, request_id)
        projection.bind_run(run_id)
        state.raise_if_cancelled(request_id)
        state.transition(request_id, "running", summary="Strict replay is executing.")
        metadata = transition_run(run_dir, metadata, "running")
        evaluator = build_ai_assertion_evaluator(settings) if prepared.requires_ai_assertion else None
        harness = HarnessFactory().create_harness(
            platform=settings.harness.platform,
            harness_settings=settings.harness,
            artifact_store=ArtifactStore(run_dir=run_dir),
            ai_assertion_evaluator=evaluator,
            runtime_secret_settings=settings.runtime_secrets,
            app_id=_android_app_id(settings, prepared.case) if settings.harness.platform == "android" else None,
            serial=prepared.target_id if settings.harness.platform == "android" else None,
        )
        recorder = _ProjectionEvidenceRecorder(run_id=run_id, output_dir=run_dir, projection=projection)
        artifact = (
            LifecycleExecutionService(runner=run_strict_lifecycle_case)
            .execute(
                LifecycleExecutionRequest(
                    case_path=prepared.case_path,
                    case=prepared.case,
                    settings=settings,
                    harness=_CancellableHarness(harness, state, request_id),
                    output_dir=run_dir,
                    run_id=run_id,
                    registry=prepared.registry,
                    registry_snapshot=prepared.registry_snapshot,
                    resolve_steps=lambda steps, _case: _preflight_steps(steps, prepared.registry_snapshot, RuntimeSecretStore.from_settings(settings.runtime_secrets)),
                    post_action_delay_seconds=settings.execution.post_action_delay_seconds,
                    runtime_secret_store=RuntimeSecretStore.from_settings(settings.runtime_secrets),
                    recorder=recorder,
                    resolved_steps_by_path=prepared.resolved_steps_by_path,
                    cases_by_path={path.resolve(): case for path, case in prepared.lifecycle_cases},
                    cancellation_check=lambda: state.raise_if_cancelled(request_id),
                )
            )
            .report
        )
        state.raise_if_cancelled(request_id)
        state.transition(request_id, "finalizing", summary="Finalizing strict evidence and report.")
        metadata = transition_run(run_dir, metadata, "finalizing")
        projection.load_persisted_manifest()
        status, summary = _strict_report_status(artifact.path)
        result_status = "success" if status == "passed" else "failed"
        transition_run(
            run_dir,
            metadata,
            result_status,
            result=RunResultSummary(summary=summary, steps=_strict_report_step_counts(artifact.path)),
            artifacts=RunArtifactIndex(
                report=artifact.path.with_suffix(".json").name,
                report_markdown=artifact.path.name,
                events="events.jsonl" if (run_dir / "events.jsonl").is_file() else None,
                evidence_manifest=artifact.evidence_manifest_path.name if artifact.evidence_manifest_path else None,
            ),
        )
        state.finish(
            request_id,
            status=result_status,
            summary=projection.safe_text(summary),
            result={"status": status},
            report_available=artifact.path.exists(),
        )
    except TaskCancelledError:
        if run_dir is not None and metadata is not None:
            _best_effort_terminal_run(run_dir, metadata, "cancelled")
        state.finish(request_id, status="cancelled", summary="Run cancelled.")
    except Exception as exc:  # noqa: BLE001
        if run_dir is not None and metadata is not None:
            _best_effort_terminal_run(run_dir, metadata, "error")
        state.finish(request_id, status="error", summary=safe_exception_message(exc, settings=settings, unexpected=True))
    finally:
        if evaluator is not None:
            evaluator.close()


class _ProjectionEvidenceRecorder(EvidenceRecorder):
    def __init__(self, *, run_id: str, output_dir: Path, projection: EvidenceProjection) -> None:
        super().__init__(run_id=run_id, output_dir=output_dir)
        self.projection = projection

    def record_event(self, event: RunnerEvent) -> None:
        super().record_event(event)
        self.projection.project_runner_event(event)

    def record_step_result(self, result: RunnerStepResult) -> None:
        super().record_step_result(result)
        self.projection.project_step_result(result)


class _CancellableHarness:
    def __init__(self, harness: Any, state: ControlPlaneState, request_id: str) -> None:
        self._harness = harness
        self._state = state
        self._request_id = request_id

    def _check(self) -> None:
        self._state.raise_if_cancelled(self._request_id)

    def get_context(self):
        self._check()
        return self._harness.get_context()

    def action_space(self):
        return self._harness.action_space()

    def before_action(self, step, context) -> None:
        self._check()
        return self._harness.before_action(step, context)

    def invoke_action(self, step, context):
        self._check()
        return self._harness.invoke_action(step, context)

    def after_action(self, step, context, action_result) -> None:
        self._check()
        return self._harness.after_action(step, context, action_result)

    def capture_artifact(self, *args, **kwargs):
        self._check()
        return self._harness.capture_artifact(*args, **kwargs)

    def classify_error(self, error, phase, step):
        return self._harness.classify_error(error, phase, step)


def _projection(settings: Settings, state: ControlPlaneState, request_id: str) -> EvidenceProjection:
    return EvidenceProjection(state, request_id, Path(settings.output.runs_dir), secret_values=configured_secret_values(settings))


def _raise_async_cancelled() -> None:
    raise asyncio.CancelledError


def _task_from_goal(goal: str, request_id: str) -> Task:
    slug = re.sub(r"[^a-z0-9]+", "-", goal.lower()).strip("-") or "control-plane-goal"
    return Task(
        id=f"{slug[:60]}-{request_id[:8]}",
        name=goal,
        description=goal,
        planning_reference_kind="goal",
        planning_reference_text=goal,
    )


def _preflight_steps(steps: list[ExecutableStep], snapshot: Any, secret_store: RuntimeSecretStore) -> list[ExecutableStep]:
    for step in steps:
        capability = snapshot.resolve(step.action_name)
        if capability is None:
            raise ValueError(f"Unsupported strict action: {step.action_name}")
        capability.params_model.model_validate(step.params)
        for name in _runtime_secret_names(step.params):
            secret_store.resolve(name)
    return steps


def _runtime_secret_names(value: Any) -> set[str]:
    names: set[str] = set()
    if isinstance(value, dict):
        if value.get("textType") == "runtimeSecret" and isinstance(value.get("text"), str):
            names.add(value["text"].strip())
        else:
            for child in value.values():
                names.update(_runtime_secret_names(child))
    elif isinstance(value, list):
        for child in value:
            names.update(_runtime_secret_names(child))
    return {name for name in names if name}


def _validate_case_platform(settings: Settings, case: FsqCase) -> None:
    if case.config.platform != settings.harness.platform:
        raise ValueError("Strict lifecycle child platform does not match the selected platform.")


def _android_app_id(settings: Settings, case: FsqCase | None) -> str:
    return settings.harness.android.app_id or (case.config.app_id if case else None) or ""


def _validate_android_app_id(settings: Settings, *, root_case: FsqCase, case: FsqCase) -> None:
    if settings.harness.platform == "android" and not (settings.harness.android.app_id or root_case.config.app_id or case.config.app_id):
        raise ValueError("Android app id is required for strict execution.")


def _strict_report_status(path: Path) -> tuple[str, str]:
    json_path = path.with_suffix(".json")
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    summary = payload.get("summary") if isinstance(payload, dict) else None
    status = str(summary.get("status") if isinstance(summary, dict) else "failed")
    failed = summary.get("failed_steps", 0) if isinstance(summary, dict) else 0
    return status, "Strict replay passed." if status == "passed" else f"Strict replay failed with {failed} failed step(s)."


def _strict_report_step_counts(path: Path) -> RunStepCounts:
    payload = json.loads(path.with_suffix(".json").read_text(encoding="utf-8"))
    summary = payload.get("summary") if isinstance(payload, dict) else None
    total = int(summary.get("total_steps", 0) or 0) if isinstance(summary, dict) else 0
    failed = int(summary.get("failed_steps", 0) or 0) if isinstance(summary, dict) else 0
    return RunStepCounts(total=total, passed=max(0, total - failed), failed=failed)


def _best_effort_terminal_run(run_dir: Path, metadata, status: str) -> None:
    try:
        transition_run(run_dir, metadata, status)
    except Exception:  # noqa: BLE001, S110 - preserve the original execution failure.
        pass


def _raise_workspace_root_unavailable() -> Never:
    raise RuntimeError("Workspace root is unavailable.")


__all__ = ["ExecutionHandle", "PreparedRun", "prepare_run", "start_execution"]
