# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import subprocess
import sys
import time
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from threading import Event, Thread
from typing import TYPE_CHECKING, Literal

from fsq_agent._workspace_paths import resolve_workspace_cases_path
from fsq_agent.case_dsl import FsqCaseLoader, FsqExecutableStepAdapter
from fsq_agent.core import CapabilityRegistry, EvidenceRecorder, HarnessInterface, RuntimeSecretStore, StepRunner, StepSequenceRunner
from fsq_agent.models import (
    CapabilityRegistrySnapshot,
    ConfigurationError,
    ExecutableStep,
    FsqCase,
    FsqCaseHook,
    FsqCaseHookAction,
    PostActionDelaySettings,
    ReportArtifact,
    RunnerEvent,
    RunnerStepResult,
    SourceRef,
    StepPhaseReport,
)
from fsq_agent.report import CoreEvidenceReportGenerator

from .runs import RunLifecycleService, RunSource, transition_run

if TYPE_CHECKING:
    from fsq_agent.config import Settings

LifecyclePhase = Literal["onCaseStart", "case", "onCaseComplete"]
ResolveSteps = Callable[[list[ExecutableStep], FsqCase], list[ExecutableStep]]
CancellationCheck = Callable[[], None]
CasePathValidator = Callable[[Path], None]
logger = logging.getLogger(__name__)
_PHASE_LABELS = {
    "onCaseStart": "before case",
    "case": "main case",
    "onCaseComplete": "after case",
}


@dataclass(frozen=True)
class LifecycleExecutionRequest:
    case_path: Path
    case: FsqCase
    settings: Settings
    harness: HarnessInterface
    output_dir: Path
    run_id: str
    registry: CapabilityRegistry
    registry_snapshot: CapabilityRegistrySnapshot
    resolve_steps: ResolveSteps
    post_action_delay_seconds: PostActionDelaySettings | None = None
    runtime_secret_store: RuntimeSecretStore | None = None
    recorder: EvidenceRecorder | None = None
    resolved_steps_by_path: dict[Path, list[ExecutableStep]] | None = None
    cases_by_path: dict[Path, FsqCase] | None = None
    cancellation_check: CancellationCheck | None = None


@dataclass(frozen=True)
class LifecycleExecutionResult:
    report: ReportArtifact


class LifecycleExecutionService:
    def __init__(self, *, runner: Callable[..., ReportArtifact] | None = None) -> None:
        self._runner = runner or run_strict_lifecycle_case

    def collect_cases(self, *, case_path: Path, case: FsqCase, settings: Settings, validate_case_path: CasePathValidator | None = None) -> list[tuple[Path, FsqCase]]:
        return collect_strict_lifecycle_cases(case_path=case_path, case=case, settings=settings, validate_case_path=validate_case_path)

    def execute(self, request: LifecycleExecutionRequest) -> LifecycleExecutionResult:
        return LifecycleExecutionResult(report=self._runner(**request.__dict__))


def run_strict_lifecycle_case(
    *,
    case_path: Path,
    case: FsqCase,
    settings: Settings,
    harness: HarnessInterface,
    output_dir: Path,
    run_id: str,
    registry: CapabilityRegistry,
    registry_snapshot: CapabilityRegistrySnapshot,
    resolve_steps: ResolveSteps,
    post_action_delay_seconds: PostActionDelaySettings | None = None,
    runtime_secret_store: RuntimeSecretStore | None = None,
    recorder: EvidenceRecorder | None = None,
    resolved_steps_by_path: dict[Path, list[ExecutableStep]] | None = None,
    cases_by_path: dict[Path, FsqCase] | None = None,
    cancellation_check: CancellationCheck | None = None,
) -> ReportArtifact:
    lifecycle_recorder = recorder or EvidenceRecorder(
        run_id=run_id,
        output_dir=output_dir,
        metadata={"root_case_path": case_path.name, "root_case_id": case.id},
        secret_values=tuple(settings.runtime_secrets.private_values().values()),
    )
    executor = _StrictLifecycleExecutor(
        case_path=case_path.resolve(),
        case=case,
        settings=settings,
        harness=harness,
        output_dir=output_dir,
        run_id=run_id,
        registry=registry,
        registry_snapshot=registry_snapshot,
        post_action_delay_seconds=post_action_delay_seconds,
        runtime_secret_store=runtime_secret_store,
        recorder=lifecycle_recorder,
        resolve_steps=resolve_steps,
        resolved_steps_by_path=resolved_steps_by_path or {},
        cases_by_path=cases_by_path or {case_path.resolve(): case},
        cancellation_check=cancellation_check or _no_op,
    )
    return executor.run()


def collect_strict_lifecycle_cases(
    *,
    case_path: Path,
    case: FsqCase,
    settings: Settings,
    validate_case_path: CasePathValidator | None = None,
) -> list[tuple[Path, FsqCase]]:
    cases: list[tuple[Path, FsqCase]] = []
    loader = FsqCaseLoader()

    def collect(current_path: Path, current_case: FsqCase, stack: tuple[Path, ...], *, include_config: bool) -> None:
        resolved_path = current_path.resolve()
        if validate_case_path is not None:
            validate_case_path(resolved_path)
        if resolved_path in stack:
            raise ConfigurationError(
                "Recursive lifecycle hook runCase detected.",
                context={"case_path": str(resolved_path), "chain": [str(path) for path in (*stack, resolved_path)]},
            )
        stack = (*stack, resolved_path)
        cases.append((resolved_path, current_case))
        hooks = [*current_case.config.on_case_start, *current_case.config.on_case_complete]
        if include_config:
            hooks = [
                *settings.case_lifecycle.on_case_start,
                *hooks,
                *settings.case_lifecycle.on_case_complete,
            ]
        for hook in hooks:
            for action in hook.actions:
                if action.action_name != "runCase":
                    continue
                child_path = _resolve_case_yaml_path(action.value, settings.cases.dir)
                if validate_case_path is not None:
                    validate_case_path(child_path)
                collect(child_path, loader.load_case(child_path), stack, include_config=False)

    collect(case_path, case, (), include_config=True)
    return cases


class _StrictLifecycleExecutor:
    def __init__(
        self,
        *,
        case_path: Path,
        case: FsqCase,
        settings: Settings,
        harness: HarnessInterface,
        output_dir: Path,
        run_id: str,
        registry: CapabilityRegistry,
        registry_snapshot: CapabilityRegistrySnapshot,
        post_action_delay_seconds: PostActionDelaySettings | None,
        runtime_secret_store: RuntimeSecretStore | None,
        recorder: EvidenceRecorder,
        resolve_steps: ResolveSteps,
        resolved_steps_by_path: dict[Path, list[ExecutableStep]],
        cases_by_path: dict[Path, FsqCase],
        cancellation_check: CancellationCheck,
    ) -> None:
        self.case_path = case_path
        self.case = case
        self.settings = settings
        self.output_dir = output_dir
        self.run_id = run_id
        self.registry_snapshot = registry_snapshot
        self.step_runner = StepRunner(
            harness=harness,
            capability_registry=registry,
            post_action_delay_seconds=post_action_delay_seconds,
            runtime_secret_store=runtime_secret_store,
        )
        self.recorder = _LifecycleRecorder(recorder)
        self.resolve_steps = resolve_steps
        self.resolved_steps_by_path = {path.resolve(): list(steps) for path, steps in resolved_steps_by_path.items()}
        self.cases_by_path = {path.resolve(): loaded_case for path, loaded_case in cases_by_path.items()}
        self.cancellation_check = cancellation_check
        self.loader = FsqCaseLoader()
        self._shell_step_index = 0
        self._run_case_step_index = 0
        self._logged_phase_starts: set[LifecyclePhase] = set()
        self._invocation_counter = 0
        self._invocation_path: tuple[str, ...] = ("root",)
        self._planned_case_steps: dict[tuple[str, ...], list[ExecutableStep]] = {}
        self._planned_hook_steps: dict[tuple[str, ...], ExecutableStep] = {}
        self._completion_depth = 0

    def run(self) -> ReportArtifact:
        lifecycle = RunLifecycleService()
        metadata = lifecycle.ensure_run(
            self.output_dir,
            run_id=self.run_id,
            platform=self.settings.harness.platform,
            mode="strict",
            source=RunSource(kind="case", case_id=self.case.id, case_path=self._safe_case_path(self.case_path)),
        )
        stop = Event()

        def heartbeat():
            while not stop.wait(5):
                lifecycle.heartbeat(self.output_dir)

        thread = Thread(target=heartbeat, daemon=True)
        try:
            try:
                self._plan_case(self.case_path, self.case, (), None, ("root",))
                lifecycle.preflight_steps([step for steps in self.resolved_steps_by_path.values() for step in steps], self.step_runner.runtime_secret_store, registry=self.registry_snapshot)
                if metadata is not None:
                    secrets = tuple(self.settings.runtime_secrets.private_values().values())
                    metadata = lifecycle.snapshot_sources(
                        self.output_dir,
                        metadata,
                        sources={
                            **{
                                f"case-{index}-{self._safe_case_path(path)}": lifecycle.case_source_bytes(self.cases_by_path[path])
                                for index, path in enumerate(dict.fromkeys([self.case_path, *self.cases_by_path]))
                            },
                            "config-lifecycle": self._config_lifecycle_bytes(),
                        },
                        secret_values=secrets,
                        configuration=lifecycle.safe_configuration(self.settings),
                    )
                    recording = self.case.config.properties.get("recording")
                    if isinstance(recording, dict) and isinstance(recording.get("source_run_id"), str):
                        steps = self._source_steps(self.case_path, self.case)
                        mappings = []
                        for item in recording.get("command_mapping", []):
                            if not isinstance(item, dict):
                                continue
                            index = item.get("command_index")
                            if isinstance(index, int) and not isinstance(index, bool) and 0 <= index < len(steps):
                                mappings.append(
                                    {
                                        **{key: item[key] for key in ("command_index", "source_step_id", "step_execution_id", "invocation_path") if key in item},
                                        "current_source_step_id": lifecycle.source_step_id(self.case, index, source_path=self._safe_case_path(self.case_path), secret_values=secrets),
                                        "current_invocation_path": ["root", "body", str(index)],
                                    }
                                )
                        lifecycle.append_lineage(
                            self.output_dir,
                            {"kind": "replay", "originating_run_id": recording["source_run_id"], "case_digest": metadata.source.digest, "command_mapping": mappings},
                            secret_values=secrets,
                        )
                    lifecycle.heartbeat(self.output_dir)
                    if metadata.status == "preparing":
                        metadata = transition_run(self.output_dir, metadata, "running")
                    thread.start()
                self.cancellation_check()
                self._execute_case(self.case_path, self.case, stack=())
            except BaseException as exc:
                if metadata is not None:
                    with suppress(Exception):
                        self.recorder.write_manifest()
                        cancelled = isinstance(exc, (asyncio.CancelledError, KeyboardInterrupt)) or type(exc).__name__ in {"ExecutionCancelled", "RunCancelled", "TaskCancelledError"}
                        frozen = lifecycle.freeze(
                            self.output_dir,
                            metadata,
                            bundle=self.recorder.build_bundle(),
                            status="cancelled" if cancelled else "error",
                            summary="Execution cancelled." if cancelled else f"Execution stopped ({type(exc).__name__}).",
                        )
                        lifecycle.finalize(self.output_dir, metadata, execution_result=frozen)
                raise lifecycle.execution_error(exc, metadata) from exc
            manifest_path = self.recorder.write_manifest()
            frozen = lifecycle.freeze(self.output_dir, metadata, bundle=self.recorder.build_bundle()) if metadata else None
            try:
                artifact = CoreEvidenceReportGenerator().generate_from_manifest(manifest_path)
                processing = {"report": {"status": "success"}}
            except Exception as exc:
                if frozen is None:
                    raise
                artifact = ReportArtifact(run_id=self.run_id, path=self.output_dir / "execution-result.json", evidence_manifest_path=manifest_path)
                processing = {"report": {"status": "failed", "error_type": type(exc).__name__}}
            if metadata is not None:
                lifecycle.finalize(self.output_dir, metadata, execution_result=frozen, processing=processing)
            return artifact
        finally:
            stop.set()
            if thread.is_alive():
                thread.join(timeout=3)

    def _execute_case(self, case_path, case, stack, parent_hook_action=None) -> bool:
        self._check_cancel()
        if case_path in stack:
            raise ConfigurationError("Recursive lifecycle hook runCase detected.")
        stack = (*stack, case_path)
        root = parent_hook_action is None and case_path == self.case_path
        start_ok = True
        interrupted = None
        main_ok = False
        complete_ok = True
        config_complete_ok = True
        try:
            if root:
                start_ok = self._execute_hooks(case_path, case, "onCaseStart", self.settings.case_lifecycle.on_case_start, stack, hook_origin="config", continue_after_failure=False)
            if start_ok:
                start_ok = self._execute_hooks(case_path, case, "onCaseStart", case.config.on_case_start, stack, hook_origin="case", continue_after_failure=False)
            else:
                self._skip_scope((*self._invocation_path, "case", "onCaseStart"), "lifecycle_start_failed")
            if start_ok:
                main_ok = self._execute_case_commands(case_path, case, "case", stack, parent_hook_action)
            else:
                body, teardown = _split_trailing_teardown_steps(self._planned_case_steps[self._invocation_path])
                for leaf in body:
                    self._skip_scope(leaf.invocation_path, "lifecycle_start_failed")
                if teardown:
                    StepSequenceRunner(self.step_runner, self.recorder).run_steps(self.run_id, [], teardown_steps=teardown)
        except BaseException as exc:  # noqa: BLE001 - preserve cancellation while completing eligible hooks.
            interrupted = exc
        finally:
            self._completion_depth += 1
            try:
                complete_ok = self._execute_hooks(
                    case_path, case, "onCaseComplete", case.config.on_case_complete, stack, hook_origin="case", continue_after_failure=True, ignore_cancellation=interrupted is not None
                )
                if root:
                    config_complete_ok = self._execute_hooks(
                        case_path,
                        case,
                        "onCaseComplete",
                        self.settings.case_lifecycle.on_case_complete,
                        stack,
                        hook_origin="config",
                        continue_after_failure=True,
                        ignore_cancellation=interrupted is not None,
                    )
            except BaseException as exc:  # noqa: BLE001 - cleanup does not replace the original interruption.
                interrupted = interrupted or exc
            finally:
                self._completion_depth -= 1
        if interrupted is not None:
            try:
                self._skip_scope(self._invocation_path, "execution_interrupted", status="incomplete")
            except BaseException as persistence_error:  # noqa: BLE001 - preserve the original interruption.
                logging.getLogger(__name__).warning("Interrupted scope persistence failed (%s)", type(persistence_error).__name__)
            raise interrupted
        return start_ok and main_ok and complete_ok and config_complete_ok

    def _source_steps(self, case_path, case):
        resolved = case_path.resolve()
        if resolved not in self.resolved_steps_by_path:
            steps = FsqExecutableStepAdapter(registry_snapshot=self.registry_snapshot).to_executable_steps(case)
            self.resolved_steps_by_path[resolved] = self.resolve_steps(steps, case)
        return list(self.resolved_steps_by_path[resolved])

    def _config_lifecycle_bytes(self):
        content = json.dumps(self.settings.case_lifecycle.model_dump(mode="json", by_alias=True), sort_keys=True, ensure_ascii=False)
        return content.encode("utf-8")

    def _config_hook_identity(self, origin):
        safe = RunLifecycleService.safe_source_text(self._config_lifecycle_bytes().decode("utf-8"), tuple(self.settings.runtime_secrets.private_values().values()))
        return ":config@" + hashlib.sha256(safe.encode("utf-8")).hexdigest() if origin == "config" else ""

    def _plan_case(self, case_path, case, stack, parent, invocation):
        if case_path in stack:
            raise ConfigurationError("Recursive lifecycle hook runCase detected.")
        stack = (*stack, case_path)
        self.cases_by_path[case_path.resolve()] = case
        previous = self._invocation_path
        self._invocation_path = invocation
        try:
            root = parent is None and case_path == self.case_path
            groups = [("case", "onCaseStart", case.config.on_case_start), ("case", "onCaseComplete", case.config.on_case_complete)]
            if root:
                groups = [("config", "onCaseStart", self.settings.case_lifecycle.on_case_start), *groups, ("config", "onCaseComplete", self.settings.case_lifecycle.on_case_complete)]
            for origin, phase, hooks in groups:
                for hook_index, hook in enumerate(hooks, start=1):
                    for action_index, action in enumerate(hook.actions, start=1):
                        path = (*invocation, origin, phase, str(hook_index), str(action_index))
                        metadata = self._hook_metadata(case_path, case, phase, hook_index, action_index, action, stack, origin, path)
                        if action.action_name == "runCase":
                            container = ExecutableStep(
                                step_id=f"{case.id}-hook-run-case-{origin}-{phase}-{hook_index}-{action_index}",
                                kind="setup" if phase == "onCaseStart" else "teardown",
                                action_name="runCase",
                                source_step_id=RunLifecycleService.source_step_id(
                                    case, action_index, source_path=self._safe_case_path(case_path), secret_values=tuple(self.settings.runtime_secrets.private_values().values())
                                )
                                + f":{origin}:{phase}:{hook_index}:container"
                                + self._config_hook_identity(origin),
                                invocation_path=path,
                                metadata={**metadata, "structural": True},
                            )
                            self._planned_hook_steps[path] = container
                            self.recorder.record_planned_step(container)
                            child_path, child_case = self._resolve_child_case(action.value)
                            self._plan_case(child_path, child_case, stack, metadata, (*path, "child"))
                        else:
                            step = ExecutableStep(
                                step_id=f"{case.id}-shell-{phase}-{hook_index}-{action_index}",
                                kind="setup" if phase == "onCaseStart" else "teardown",
                                action_name="runShell",
                                source_step_id=RunLifecycleService.source_step_id(
                                    case, action_index, source_path=self._safe_case_path(case_path), secret_values=tuple(self.settings.runtime_secrets.private_values().values())
                                )
                                + f":{origin}:{phase}:{hook_index}"
                                + self._config_hook_identity(origin),
                                invocation_path=path,
                                metadata=metadata,
                            )
                            self._planned_hook_steps[path] = step
                            self.recorder.record_planned_step(step)
            steps = [self._annotate_step(step, case_path, case, "case", stack, parent) for step in self._source_steps(case_path, case)]
            self._planned_case_steps[invocation] = steps
            for step in steps:
                self.recorder.record_planned_step(step)
        finally:
            self._invocation_path = previous

    def _skip_scope(self, prefix, reason, *, status="skipped"):
        bundle = self.recorder.build_bundle()
        recorded = {(step.source_step_id or step.step_id, step.invocation_path) for step in bundle.steps if not step.metadata.get("recovered_incomplete")}
        blocker = next((step.step_execution_id for step in reversed(bundle.steps) if step.status == "failed"), None)
        for step in bundle.planned_steps:
            key = (step.source_step_id or step.step_id, step.invocation_path)
            if step.invocation_path[: len(prefix)] != prefix or key in recorded:
                continue
            started = any(event.event_type == "step_start" and event.source_step_id == key[0] and event.invocation_path == key[1] for event in bundle.events)
            if started:
                continue
            self.recorder.record_step_result(
                RunnerStepResult(
                    step_id=step.step_id,
                    source_step_id=key[0],
                    invocation_path=key[1],
                    source_ref=step.source_ref,
                    action_name=step.action_name,
                    kind=step.kind,
                    status=status,
                    skip_reason=reason,
                    blocked_by_step=blocker,
                    unavailable_reason="not_executed",
                    metadata=step.metadata,
                )
            )
            recorded.add(key)

    def _safe_case_path(self, case_path):
        root = self.settings.cases.dir or self.case_path.parent
        resolved = case_path.resolve()
        return resolved.relative_to(root.resolve()).as_posix() if resolved.is_relative_to(root.resolve()) else resolved.name

    def _check_cancel(self):
        if not self._completion_depth:
            self.cancellation_check()

    def _hook_metadata(self, case_path, case, phase, hook_index, action_index, action, stack, origin, invocation):
        return {
            "lifecycle_phase": phase,
            "hook_index": hook_index,
            "hook_action_index": action_index,
            "hook_action_name": action.action_name,
            "hook_origin": origin,
            "root_case_path": self._safe_case_path(self.case_path),
            "case_path": self._safe_case_path(case_path),
            "case_id": case.id,
            "hook_chain": [self._safe_case_path(path) for path in stack],
            "value": action.value,
            "invocation_path": invocation,
            "root_invocation": case_path == self.case_path and invocation[0] == "root" and len(invocation) == 5,
        }

    def _execute_hooks(
        self,
        case_path: Path,
        case: FsqCase,
        phase: LifecyclePhase,
        hooks: list[FsqCaseHook],
        stack: tuple[Path, ...],
        *,
        hook_origin: str,
        continue_after_failure: bool,
        ignore_cancellation: bool = False,
    ) -> bool:
        if hooks:
            self._log_phase_start(phase)
        all_passed = True
        for hook_index, hook in enumerate(hooks, start=1):
            for action_index, action in enumerate(hook.actions, start=1):
                if not ignore_cancellation:
                    self._check_cancel()
                passed = self._execute_hook_action(
                    case_path,
                    case,
                    phase,
                    hook_index,
                    action_index,
                    action,
                    stack,
                    hook_origin,
                )
                if not ignore_cancellation:
                    self._check_cancel()
                all_passed = all_passed and passed
                if not passed and not continue_after_failure:
                    self._skip_scope((*self._invocation_path, hook_origin, phase), "lifecycle_start_failed")
                    return False
        return all_passed

    def _execute_hook_action(
        self,
        case_path: Path,
        case: FsqCase,
        phase: LifecyclePhase,
        hook_index: int,
        action_index: int,
        action: FsqCaseHookAction,
        stack: tuple[Path, ...],
        hook_origin: str,
    ) -> bool:
        path = (*self._invocation_path, hook_origin, phase, str(hook_index), str(action_index))
        metadata = self._hook_metadata(case_path, case, phase, hook_index, action_index, action, stack, hook_origin, path)
        if action.action_name == "runCase":
            self._check_cancel()
            child_path, child_case = self._resolve_child_case(action.value)
            step_id = self._start_run_case_hook(metadata)
            started_at = datetime.now(UTC)
            started = time.perf_counter()
            previous = self._invocation_path
            self._invocation_path = (*path, "child")
            try:
                passed = self._execute_case(child_path, child_case, stack, parent_hook_action=metadata)
            finally:
                self._invocation_path = previous
            duration_ms = max(0, int((time.perf_counter() - started) * 1000))
            self._finish_run_case_hook(step_id, action.value, metadata, passed, started_at, duration_ms)
            return passed
        return self._execute_shell_hook(action.value, metadata)

    def _resolve_child_case(self, path_text: str) -> tuple[Path, FsqCase]:
        requested = Path(path_text.strip())
        candidates = (
            [requested]
            if requested.is_absolute()
            else [
                *([self.settings.cases.dir / requested] if self.settings.cases.dir is not None else []),
                Path.cwd() / requested,
            ]
        )
        for candidate in candidates:
            resolved = candidate.resolve()
            if resolved in self.cases_by_path:
                return resolved, self.cases_by_path[resolved]
        child_path = _resolve_case_yaml_path(path_text, self.settings.cases.dir)
        return child_path, self.loader.load_case(child_path)

    def _execute_case_commands(
        self,
        case_path: Path,
        case: FsqCase,
        phase: LifecyclePhase,
        stack: tuple[Path, ...],
        parent_hook_action: dict[str, object] | None,
    ) -> bool:
        steps = self._planned_case_steps[self._invocation_path]
        if steps:
            self._log_phase_start(_effective_lifecycle_phase(phase, parent_hook_action))
        normal_steps, teardown_steps = _split_trailing_teardown_steps(steps)
        previous_results = {step.step_execution_id or step.step_id for step in self.recorder.build_bundle().steps if not step.metadata.get("recovered_incomplete")}
        StepSequenceRunner(step_runner=self.step_runner, evidence_recorder=self.recorder, cancellation_check=None if self._completion_depth else self.cancellation_check).run_steps(
            run_id=self.run_id,
            steps=normal_steps,
            teardown_steps=teardown_steps,
        )
        self._check_cancel()
        new_steps = [step for step in self.recorder.build_bundle().steps if not step.metadata.get("recovered_incomplete") and (step.step_execution_id or step.step_id) not in previous_results]
        return all(step.status == "passed" for step in new_steps)

    def _annotate_step(
        self,
        step: ExecutableStep,
        case_path: Path,
        case: FsqCase,
        phase: LifecyclePhase,
        stack: tuple[Path, ...],
        parent_hook_action: dict[str, object] | None,
    ) -> ExecutableStep:
        parent_metadata = {"parent_hook_action": parent_hook_action} if parent_hook_action is not None else {}
        if parent_hook_action is not None and "hook_origin" in parent_hook_action:
            parent_metadata["hook_origin"] = parent_hook_action["hook_origin"]
        metadata = {
            **step.metadata,
            "lifecycle_phase": phase,
            "root_case_path": self._safe_case_path(self.case_path),
            "case_path": self._safe_case_path(case_path),
            "hook_chain": [self._safe_case_path(path) for path in stack],
            "invocation_path": self._invocation_path,
            "root_invocation": parent_hook_action is None and case_path == self.case_path,
            "legacy_step_id": step.step_id,
            **parent_metadata,
        }
        source_ref = step.source_ref
        if source_ref is not None:
            source_ref = source_ref.model_copy(
                update={
                    "source_id": self._safe_case_path(case_path),
                    "metadata": {
                        **source_ref.metadata,
                        "lifecycle_phase": phase,
                        "root_case_path": self._safe_case_path(self.case_path),
                        **parent_metadata,
                    },
                }
            )
        step = step.model_copy(
            update={
                "source_step_id": RunLifecycleService.source_step_id(
                    case,
                    step.source_ref.step_index if step.source_ref else 0,
                    source_path=self._safe_case_path(case_path),
                    secret_values=tuple(self.settings.runtime_secrets.private_values().values()),
                ),
                "invocation_path": (*self._invocation_path, "body", str(step.source_ref.step_index if step.source_ref else step.step_id)),
            }
        )
        if phase != "case" or case_path != self.case_path:
            step_id = f"{case.id}-{phase}-step-{step.source_ref.step_index + 1:03d}" if step.source_ref else step.step_id
            return step.model_copy(update={"step_id": step_id, "metadata": metadata, "source_ref": source_ref})
        return step.model_copy(update={"metadata": metadata, "source_ref": source_ref})

    def _execute_shell_hook(self, command: str, metadata: dict[str, object]) -> bool:
        self._shell_step_index += 1
        planned = self._planned_hook_steps[tuple(metadata["invocation_path"])]
        allocated = self.recorder.allocate_step_identity(planned)
        step_id = allocated.step_id
        started_at = datetime.now(UTC)
        started = time.perf_counter()
        self.recorder.record_event(
            RunnerEvent(run_id=self.run_id, event_type="step_start", step_id=step_id, source_step_id=allocated.source_step_id, step_execution_id=step_id, invocation_path=allocated.invocation_path)
        )
        self.recorder.record_event(RunnerEvent(run_id=self.run_id, event_type="phase_start", step_id=step_id, phase="invoke"))
        try:
            completed = _run_shell_command(command)
            exit_code = completed.returncode
            stdout_length = len((completed.stdout or "").encode("utf-8", errors="replace"))
            stderr_length = len((completed.stderr or "").encode("utf-8", errors="replace"))
            error_message = None if exit_code == 0 else f"Shell hook failed with exit code {exit_code}."
        except BaseException as exc:
            if not isinstance(exc, Exception) or type(exc).__name__ in {"TaskCancelledError", "ExecutionCancelled", "RunCancelled"}:
                self.recorder.record_step_result(
                    RunnerStepResult(
                        step_id=step_id,
                        source_step_id=allocated.source_step_id,
                        step_execution_id=step_id,
                        invocation_path=allocated.invocation_path,
                        action_name="runShell",
                        kind=allocated.kind,
                        status="cancelled",
                        failure_category="cancelled",
                        error_message="Execution cancelled.",
                        started_at=started_at,
                        ended_at=datetime.now(UTC),
                        duration_ms=max(0, int((time.perf_counter() - started) * 1000)),
                        unavailable_reason=None,
                        metadata=metadata,
                    )
                )
                raise
            exit_code = None
            stdout_length = 0
            stderr_length = 0
            error_message = f"Shell hook failed to start ({type(exc).__name__})."
        ended_at = datetime.now(UTC)
        duration_ms = max(0, int((time.perf_counter() - started) * 1000))
        status = "passed" if error_message is None else "failed"
        failure_category = None if status == "passed" else "action_error"
        phase_metadata = {
            **metadata,
            "timing_measured": True,
            "command": command,
            "exit_code": exit_code,
            "stdout_bytes": stdout_length,
            "stderr_bytes": stderr_length,
        }
        result = RunnerStepResult(
            step_id=step_id,
            source_step_id=allocated.source_step_id,
            step_execution_id=step_id,
            invocation_path=allocated.invocation_path,
            action_name="runShell",
            action_status=status,
            kind=allocated.kind,
            unavailable_reason=None,
            source_ref=SourceRef(
                source_type="fsq_hook",
                source_id=str(metadata["case_path"]),
                metadata={key: value for key, value in metadata.items() if key != "hook_chain"},
            ),
            status=status,
            started_at=started_at,
            ended_at=ended_at,
            duration_ms=duration_ms,
            phase_reports=[
                StepPhaseReport(
                    step_id=step_id,
                    phase="invoke",
                    status=status,
                    duration_ms=duration_ms,
                    started_at=started_at,
                    ended_at=ended_at,
                    unavailable_reason=None,
                    failure_category=failure_category,
                    error_message=error_message,
                    metadata=phase_metadata,
                )
            ],
            failure_category=failure_category,
            error_message=error_message,
            metadata=phase_metadata,
        )
        self.recorder.record_event(
            RunnerEvent(
                run_id=self.run_id,
                event_type="action_result",
                step_id=step_id,
                source_step_id=allocated.source_step_id,
                step_execution_id=step_id,
                invocation_path=allocated.invocation_path,
                phase="invoke",
                payload={"status": status, "failure_category": failure_category, "error_message": error_message},
            )
        )
        self.recorder.record_event(
            RunnerEvent(
                run_id=self.run_id,
                event_type="phase_finish",
                step_id=step_id,
                phase="invoke",
                payload={"status": status, "exit_code": exit_code},
            )
        )
        if status != "passed":
            self.recorder.record_event(RunnerEvent(run_id=self.run_id, event_type="step_error", step_id=step_id, phase="invoke"))
        self.recorder.record_event(RunnerEvent(run_id=self.run_id, event_type="step_finish", step_id=step_id, payload={"status": status}))
        self.recorder.record_step_result(result)
        return status == "passed"

    def _start_run_case_hook(self, metadata: dict[str, object]) -> str:
        self._run_case_step_index += 1
        planned = self._planned_hook_steps[tuple(metadata["invocation_path"])]
        step_id = planned.step_id
        self.recorder.record_event(
            RunnerEvent(
                run_id=self.run_id,
                event_type="step_start",
                step_id=step_id,
                source_step_id=planned.source_step_id,
                invocation_path=planned.invocation_path,
                payload={"metadata": {**metadata, "structural": True}, "action_name": "runCase"},
            )
        )
        self.recorder.record_event(RunnerEvent(run_id=self.run_id, event_type="phase_start", step_id=step_id, phase="invoke"))
        return step_id

    def _finish_run_case_hook(
        self,
        step_id: str,
        target: str,
        metadata: dict[str, object],
        passed: bool,
        started_at: datetime,
        duration_ms: int,
    ) -> None:
        status = "passed" if passed else "failed"
        failure_category = None if passed else "action_error"
        error_message = None if passed else f"Hook runCase failed: {target}"
        phase_metadata = {**metadata, "target": target, "timing_measured": True, "structural": True}
        result = RunnerStepResult(
            step_id=step_id,
            source_step_id=self._planned_hook_steps[tuple(metadata["invocation_path"])].source_step_id,
            action_name="runCase",
            invocation_path=tuple(metadata["invocation_path"]),
            unavailable_reason=None,
            source_ref=SourceRef(
                source_type="fsq_hook",
                source_id=str(metadata["case_path"]),
                metadata={key: value for key, value in phase_metadata.items() if key != "hook_chain"},
            ),
            status=status,
            started_at=started_at,
            ended_at=datetime.now(UTC),
            duration_ms=duration_ms,
            phase_reports=[
                StepPhaseReport(
                    step_id=step_id,
                    phase="invoke",
                    status=status,
                    duration_ms=duration_ms,
                    failure_category=failure_category,
                    error_message=error_message,
                    metadata=phase_metadata,
                )
            ],
            failure_category=failure_category,
            error_message=error_message,
            metadata=phase_metadata,
        )
        if status != "passed":
            self.recorder.record_event(RunnerEvent(run_id=self.run_id, event_type="step_error", step_id=step_id, phase="invoke"))
        self.recorder.record_event(
            RunnerEvent(
                run_id=self.run_id,
                event_type="phase_finish",
                step_id=step_id,
                phase="invoke",
                payload={"status": status},
            )
        )
        self.recorder.record_event(RunnerEvent(run_id=self.run_id, event_type="step_finish", step_id=step_id, payload={"status": status}))
        self.recorder.record_step_result(result)

    def _log_phase_start(self, phase: LifecyclePhase) -> None:
        if phase in self._logged_phase_starts:
            return
        self._logged_phase_starts.add(phase)
        logger.info("Strict phase %s: start", _PHASE_LABELS[phase])


class _LifecycleRecorder:
    def __init__(self, recorder: EvidenceRecorder) -> None:
        self.recorder = recorder

    def allocate_step_identity(self, step: ExecutableStep) -> ExecutableStep:
        return self.recorder.allocate_step_identity(step)

    def record_planned_step(self, step: ExecutableStep) -> None:
        self.recorder.record_planned_step(step)

    def record_event(self, event: RunnerEvent) -> None:
        self.recorder.record_event(event)

    def record_step_result(self, result: RunnerStepResult) -> None:
        self.recorder.record_step_result(result)
        result = next(step for step in reversed(self.recorder.build_bundle().steps) if step.step_id == result.step_id and step.invocation_path == result.invocation_path)
        action = _result_action_label(result)
        phase = _PHASE_LABELS[_result_lifecycle_phase(result)]
        suffix = f": {result.error_message}" if result.status != "passed" and result.error_message else ""
        logger.info("Strict %s action %s: %s%s", phase, action, result.status, suffix)

    def build_bundle(self):
        return self.recorder.build_bundle()

    def write_manifest(self) -> Path:
        return self.recorder.write_manifest()


def _resolve_case_yaml_path(path_text: str, cases_dir: Path | None) -> Path:
    requested = Path(path_text.strip())
    if cases_dir is not None:
        try:
            candidate = resolve_workspace_cases_path(requested, cases_dir)
        except ConfigurationError as exc:
            raise ValueError("Case lifecycle dependency escapes the workspace cases directory.") from exc
        if candidate.exists() and candidate.is_file():
            return candidate
        raise FileNotFoundError(f"Case YAML not found: {path_text}")
    candidates = (
        [requested]
        if requested.is_absolute()
        else [
            Path.cwd() / requested,
        ]
    )
    for candidate in candidates:
        if candidate.exists() and candidate.is_file():
            return candidate.resolve()
    raise FileNotFoundError(f"Case YAML not found: {path_text}")


def _effective_lifecycle_phase(phase: LifecyclePhase, parent_hook_action: dict[str, object] | None) -> LifecyclePhase:
    if parent_hook_action is None:
        return phase
    parent_phase = parent_hook_action.get("lifecycle_phase")
    if parent_phase in {"onCaseStart", "onCaseComplete"}:
        return parent_phase  # type: ignore[return-value]
    return phase


def _result_lifecycle_phase(result: RunnerStepResult) -> LifecyclePhase:
    source_metadata = result.source_ref.metadata if result.source_ref is not None else {}
    parent = source_metadata.get("parent_hook_action") or result.metadata.get("parent_hook_action")
    if isinstance(parent, dict) and parent.get("lifecycle_phase") in {"onCaseStart", "onCaseComplete"}:
        return parent["lifecycle_phase"]  # type: ignore[return-value]
    lifecycle = source_metadata.get("lifecycle_phase") or result.metadata.get("lifecycle_phase")
    if lifecycle in {"onCaseStart", "onCaseComplete", "case"}:
        return lifecycle  # type: ignore[return-value]
    return "case"


def _result_action_label(result: RunnerStepResult) -> str:
    source_metadata = result.source_ref.metadata if result.source_ref is not None else {}
    hook_action = source_metadata.get("hook_action_name") or result.metadata.get("hook_action_name")
    command = result.metadata.get("command") or _phase_metadata_value(result, "command")
    if hook_action == "runShell":
        return f"runShell: {command}" if command else "runShell"
    if hook_action == "runCase":
        target = result.metadata.get("value") or result.metadata.get("target")
        return f"runCase: {target}" if target else "runCase"
    return str(hook_action or "unknown")


def _phase_metadata_value(result: RunnerStepResult, key: str) -> object | None:
    for phase_report in result.phase_reports:
        if key in phase_report.metadata:
            return phase_report.metadata[key]
    return None


def _run_shell_command(command: str) -> subprocess.CompletedProcess[str]:
    if sys.platform == "win32":
        # PowerShell is a fixed platform executable; only trusted, operator-authored command text is dynamic.
        return subprocess.run(  # noqa: S603
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],  # noqa: S607
            capture_output=True,
            text=True,
            check=False,
        )
    # Strict lifecycle commands are trusted, operator-authored configuration.
    return subprocess.run(command, shell=True, capture_output=True, text=True, check=False)  # noqa: S602


def _split_trailing_teardown_steps(steps: list[ExecutableStep]) -> tuple[list[ExecutableStep], list[ExecutableStep]]:
    split_at = len(steps)
    while split_at > 0 and steps[split_at - 1].kind == "teardown":
        split_at -= 1
    return steps[:split_at], steps[split_at:]


def _no_op() -> None:
    return None
