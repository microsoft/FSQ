# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from fsq_agent._capability_bootstrap import build_capability_registry
from fsq_agent.case_dsl import FsqCaseLoader, FsqExecutableStepAdapter
from fsq_agent.core import CapabilityRegistry, EvidenceRecorder, HarnessInterface, RuntimeSecretStore, StepRunner, StepSequenceRunner
from fsq_agent.models import EvidenceBundle, ExecutableStep, PostActionDelaySettings, ReportArtifact, RunSource
from fsq_agent.report import CoreEvidenceReportGenerator

from .runs import RunLifecycleService, transition_run


class ReportGenerator(Protocol):
    def generate_from_manifest(self, manifest_path: Path) -> ReportArtifact: ...


@dataclass(frozen=True)
class DeterministicExecutionRequest:
    case_path: str | Path
    harness: HarnessInterface
    output_dir: str | Path
    run_id: str
    registry: CapabilityRegistry | None = None
    steps: list[ExecutableStep] | None = None
    post_action_delay_seconds: PostActionDelaySettings | None = None
    runtime_secret_store: RuntimeSecretStore | None = None
    evidence_recorder: EvidenceRecorder | None = None
    cancellation_check: Callable[[], None] | None = None
    report_generator: ReportGenerator | None = None


@dataclass(frozen=True)
class DeterministicExecutionResult:
    evidence: EvidenceBundle
    report: ReportArtifact | None = None


class DeterministicExecutionService:
    def execute(self, request: DeterministicExecutionRequest, *, generate_report: bool = True) -> DeterministicExecutionResult:
        if request.cancellation_check is not None:
            request.cancellation_check()
        evidence, report = _execute(request, generate_report=generate_report)
        return DeterministicExecutionResult(evidence=evidence, report=report)


def run_fsq_core_case(
    *,
    case_path: str | Path,
    harness: HarnessInterface,
    output_dir: str | Path,
    run_id: str,
    registry: CapabilityRegistry | None = None,
    steps: list[ExecutableStep] | None = None,
    post_action_delay_seconds: PostActionDelaySettings | None = None,
    runtime_secret_store: RuntimeSecretStore | None = None,
    evidence_recorder: EvidenceRecorder | None = None,
    cancellation_check: Callable[[], None] | None = None,
) -> EvidenceBundle:
    return _execute(
        DeterministicExecutionRequest(
            case_path=case_path,
            harness=harness,
            output_dir=output_dir,
            run_id=run_id,
            registry=registry,
            steps=steps,
            post_action_delay_seconds=post_action_delay_seconds,
            runtime_secret_store=runtime_secret_store,
            evidence_recorder=evidence_recorder,
            cancellation_check=cancellation_check,
        ),
        generate_report=False,
    )[0]


def _execute(request, *, generate_report):
    import asyncio
    import hashlib
    import json
    from contextlib import suppress

    registry = request.registry or build_capability_registry()
    case = FsqCaseLoader().load_case(Path(request.case_path)) if request.steps is None else None
    steps = request.steps if request.steps is not None else FsqExecutableStepAdapter(registry_snapshot=registry.snapshot()).to_executable_steps(case)
    secret_store = request.runtime_secret_store or RuntimeSecretStore.empty()
    secret_values = secret_store.redaction_values()
    steps = RunLifecycleService.preflight_steps(steps, secret_store, registry=registry)
    platform = case.config.platform if case else request.harness.get_context().platform
    name = Path(request.case_path).name
    source_bytes = RunLifecycleService.case_source_bytes(case) if case else json.dumps([step.model_dump(mode="json") for step in steps], sort_keys=True).encode()
    safe_identity_bytes = RunLifecycleService.safe_source_text(source_bytes.decode("utf-8"), secret_values).encode("utf-8")
    digest = hashlib.sha256(safe_identity_bytes).hexdigest()
    steps = [
        step.model_copy(
            update={
                "source_step_id": RunLifecycleService.source_step_id(case, step.source_ref.step_index if step.source_ref else index, source_path=name, secret_values=secret_values)
                if case
                else f"{name}@{digest}:step:{index}"
            }
        )
        for index, step in enumerate(steps)
    ]
    lifecycle = RunLifecycleService()
    root = Path(request.output_dir)
    metadata = lifecycle.ensure_run(root, run_id=request.run_id, platform=platform, mode="strict", source=RunSource(kind="case", case_id=case.id if case else name, case_path=name))
    try:
        metadata = lifecycle.snapshot_sources(
            root, metadata, sources={"case" if case else "explicit-executable-steps": source_bytes}, configuration={"platform": platform}, secret_values=secret_values
        )
        metadata = transition_run(root, metadata, "running") if metadata.status == "preparing" else metadata
        lifecycle.heartbeat(root)
        recorder = request.evidence_recorder or EvidenceRecorder(run_id=request.run_id, output_dir=root, secret_values=secret_values)
        for index, step in enumerate(steps):
            steps[index] = step.model_copy(update={"invocation_path": step.invocation_path or ("root", "body", str(index))})
            recorder.record_planned_step(steps[index])
        normal, teardown = _split_trailing_teardown_steps(steps)
        runner = StepRunner(harness=request.harness, capability_registry=registry, post_action_delay_seconds=request.post_action_delay_seconds, runtime_secret_store=request.runtime_secret_store)
    except BaseException as exc:
        raise lifecycle.execution_error(exc, metadata) from exc
    try:
        if request.cancellation_check is not None:
            request.cancellation_check()
        bundle = StepSequenceRunner(step_runner=runner, evidence_recorder=recorder, cancellation_check=request.cancellation_check).run_steps(
            run_id=request.run_id, steps=normal, teardown_steps=teardown
        )
        if request.cancellation_check is not None:
            request.cancellation_check()
    except BaseException as exc:
        with suppress(Exception):
            recorder.write_manifest()
            cancelled = isinstance(exc, (asyncio.CancelledError, KeyboardInterrupt)) or type(exc).__name__ in {"TaskCancelledError", "ExecutionCancelled", "RunCancelled"}
            frozen = lifecycle.freeze(root, metadata, bundle=recorder.build_bundle(), status="cancelled" if cancelled else "error")
            lifecycle.finalize(root, metadata, execution_result=frozen)
        raise lifecycle.execution_error(exc, metadata) from exc
    manifest = recorder.write_manifest()
    bundle = bundle.model_copy(update={"manifest_path": manifest})
    frozen = lifecycle.freeze(root, metadata, bundle=bundle)
    report = None
    processing = {"report": {"status": "not_requested"}}
    if generate_report:
        try:
            report = (request.report_generator or CoreEvidenceReportGenerator()).generate_from_manifest(manifest)
            processing = {"report": {"status": "success"}}
        except Exception as exc:  # noqa: BLE001 - processing failure cannot replace frozen execution.
            report = ReportArtifact(run_id=request.run_id, path=root / "execution-result.json", evidence_manifest_path=manifest)
            processing = {"report": {"status": "failed", "error_type": type(exc).__name__}}
    lifecycle.finalize(root, metadata, execution_result=frozen, processing=processing)
    return bundle, report


def _split_trailing_teardown_steps(steps: list[ExecutableStep]) -> tuple[list[ExecutableStep], list[ExecutableStep]]:
    split_at = len(steps)
    while split_at > 0 and steps[split_at - 1].kind == "teardown":
        split_at -= 1
    return steps[:split_at], steps[split_at:]


def run_strict_fsq_core_case(
    *,
    case_path: str | Path,
    harness: HarnessInterface,
    output_dir: str | Path,
    run_id: str,
    registry: CapabilityRegistry | None = None,
    steps: list[ExecutableStep] | None = None,
    post_action_delay_seconds: PostActionDelaySettings | None = None,
    runtime_secret_store: RuntimeSecretStore | None = None,
) -> ReportArtifact:
    _, report = _execute(
        DeterministicExecutionRequest(
            case_path=case_path,
            harness=harness,
            output_dir=output_dir,
            run_id=run_id,
            registry=registry,
            steps=steps,
            post_action_delay_seconds=post_action_delay_seconds,
            runtime_secret_store=runtime_secret_store,
        ),
        generate_report=True,
    )
    return report
