# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import asyncio
import logging
import re
import time
import uuid
from collections.abc import Sequence
from contextlib import nullcontext
from dataclasses import dataclass, field
from datetime import UTC, datetime

from pydantic import BaseModel, ValidationError

from fsq_agent.core.interfaces import CapabilityRegistryInterface, EvidenceJournalSink, HarnessInterface, RuntimeSecretResolver
from fsq_agent.models import (
    CapabilityDefinition,
    CapabilityExecutionResult,
    ConfigurationError,
    EvidenceArtifactRef,
    EvidencePolicy,
    ExecutableStep,
    FailureCategory,
    HarnessActionResult,
    HarnessArtifactRef,
    PostActionDelaySettings,
    ReplayPolicy,
    RunnerEvent,
    RunnerEventType,
    RunnerStatus,
    RunnerStepResult,
    StepPhase,
    StepPhaseReport,
    WebLocator,
)


@dataclass
class _StepExecutionState:
    started: float = field(default_factory=time.perf_counter)
    phase_reports: list[StepPhaseReport] = field(default_factory=list)
    failure_category: FailureCategory | None = None
    error_message: str | None = None
    artifact_error_message: str | None = None
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    evidence_errors: list[dict[str, object]] = field(default_factory=list)
    action_status: RunnerStatus | None = None
    interruption: BaseException | None = None


class StepRunner:
    def __init__(
        self,
        harness: HarnessInterface,
        *,
        capability_registry: CapabilityRegistryInterface | None = None,
        post_action_delay_seconds: PostActionDelaySettings | None = None,
        runtime_secret_store: RuntimeSecretResolver | None = None,
        evidence_sink: EvidenceJournalSink | None = None,
    ) -> None:
        self.harness = harness
        self.capability_registry = capability_registry
        self.post_action_delay_seconds = post_action_delay_seconds or PostActionDelaySettings(platform=0.0, common=0.0)
        self.runtime_secret_store = runtime_secret_store
        self.evidence_sink = evidence_sink
        self._identities: set[str] = set()
        self._source_occurrences: dict[str, int] = {}
        redaction_values = getattr(runtime_secret_store, "redaction_values", None)
        self._secret_values: set[str] = set(redaction_values()) if callable(redaction_values) else set()
        self._persistence_failed = False
        self._state: _StepExecutionState | None = None
        self.last_step_result: RunnerStepResult | None = None
        self._events: list[RunnerEvent] = []
        self._last_capability_execution_result: CapabilityExecutionResult | None = None

    @property
    def events(self) -> Sequence[RunnerEvent]:
        return tuple(self._events)

    @property
    def last_capability_execution_result(self) -> CapabilityExecutionResult | None:
        return self._last_capability_execution_result

    def run_step(self, run_id: str, step: ExecutableStep) -> RunnerStepResult:
        if self._persistence_failed or getattr(self.evidence_sink, "write_failed", False):
            error = OSError("Run evidence is unavailable after a persistence failure.")
            error.fsq_evidence_fatal = True
            raise error
        self._events = []
        self._last_capability_execution_result = None
        self.last_step_result = None
        step = self._allocate_identity(step)
        capability, step = self._resolve_capability_step(step)
        step = self._with_effective_evidence_policy(step, capability)
        state = self._start_step(run_id, step)
        self._state = state
        try:
            return self._run_harness_step(run_id, step, capability, state)
        except BaseException as exc:
            if state.interruption is not None and exc is not state.interruption:
                logging.getLogger(__name__).warning("Interrupted phase persistence failed (%s)", type(exc).__name__)
                raise state.interruption from None
            if self._persistence_failed:
                raise
            cancelled = self._is_cancelled(exc, step, "invoke")
            step = step.model_copy(update={"metadata": {**step.metadata, "interruption": {"status": "cancelled" if cancelled else "incomplete", "reason": type(exc).__name__}}})
            try:
                self._finish_step(
                    run_id,
                    step,
                    state,
                    status="cancelled" if cancelled else "incomplete",
                    failure_category=state.failure_category or ("cancelled" if cancelled else None),
                    error_message=state.error_message or ("Execution cancelled." if cancelled else "Execution interrupted."),
                )
            except BaseException as persistence_error:  # noqa: BLE001 - preserve the original interruption.
                logging.getLogger(__name__).warning("Interrupted step checkpoint failed (%s)", type(persistence_error).__name__)
            raise
        finally:
            self._state = None

    def _allocate_identity(self, step: ExecutableStep) -> ExecutableStep:
        if self.evidence_sink is not None and hasattr(self.evidence_sink, "allocate_step_identity"):
            return self.evidence_sink.allocate_step_identity(step)
        source = step.source_step_id or step.step_id
        occurrence = self._source_occurrences.get(source, 0) + 1
        self._source_occurrences[source] = occurrence
        identity = step.step_execution_id or step.step_id
        if identity in self._identities:
            if step.step_execution_id:
                raise ValueError("Step execution identity is already allocated.")
            identity = f"{step.step_id}-invocation-{occurrence}-attempt-{step.attempt_index}-{uuid.uuid4().hex[:8]}"
        self._identities.add(identity)
        return step.model_copy(update={"source_step_id": source, "step_execution_id": identity, "step_id": identity, "invocation_path": step.invocation_path or (source, str(occurrence))})

    def _resolve_capability_step(self, step: ExecutableStep) -> tuple[CapabilityDefinition | None, ExecutableStep]:
        capability = self._resolve_capability(step.action_name)
        if capability is not None and capability.name != step.action_name:
            return capability, step.model_copy(update={"action_name": capability.name})
        return capability, step

    def _with_effective_evidence_policy(
        self,
        step: ExecutableStep,
        capability: CapabilityDefinition | None,
    ) -> ExecutableStep:
        return step.model_copy(update={"evidence_policy": self._step_kind_evidence_policy(step, capability)})

    def _step_kind_evidence_policy(
        self,
        step: ExecutableStep,
        capability: CapabilityDefinition | None,
    ) -> EvidencePolicy:
        if capability is None:
            return EvidencePolicy(capture_after=False)
        if step.kind == "action":
            capture_before = True
            capture_after = True
        elif step.kind == "assertion":
            capture_before = True
            capture_after = False
        elif step.kind == "setup":
            capture_before = False
            capture_after = True
        elif step.kind == "teardown":
            capture_before = True
            capture_after = False
        else:
            return EvidencePolicy(capture_after=False)
        return EvidencePolicy(
            capture_before=capture_before,
            capture_after=capture_after,
            capture_on_failure=False,
            artifact_kinds=["screenshot", "ui_snapshot"],
        )

    def _start_step(self, run_id: str, step: ExecutableStep) -> _StepExecutionState:
        state = _StepExecutionState()
        self._emit(
            run_id=run_id,
            event_type="step_start",
            step=step,
            payload={
                "action_name": step.action_name,
                "kind": step.kind,
                "source_ref": step.source_ref.model_dump(mode="json") if step.source_ref else None,
                "evidence_policy": step.evidence_policy.model_dump(mode="json"),
                "proposal": {
                    "params": None
                    if self._resolve_capability(step.action_name) and self._resolve_capability(step.action_name).sensitivity and step.params.get("textType") != "runtimeSecret"
                    else self._safe_value(step.params),
                    "authored_action_name": step.metadata.get("authored_action_name"),
                    "capability": self._capability_metadata(self._resolve_capability(step.action_name), step, None),
                },
            },
        )
        return state

    def _run_harness_step(self, run_id, step, capability, state) -> RunnerStepResult:
        delay_seconds = self._effective_post_action_delay_seconds(capability)
        context = self._phase(run_id, step, state, "prepare", lambda: self._prepare(run_id, step))
        action_result = None
        if context is not None:
            action_result = self._phase(run_id, step, state, "invoke", lambda: self._invoke(run_id, step, capability, context, state))
            self._phase(run_id, step, state, "settle", lambda: self._apply_post_action_delay(delay_seconds))
            self._phase(run_id, step, state, "finalize", lambda: self._finalize(run_id, step, context, action_result))
        status = self._result_status(action_result, state.failure_category, state.artifact_error_message)
        return self._finish_step(
            run_id,
            step,
            state,
            status=status,
            failure_category=state.failure_category or ("artifact_error" if state.artifact_error_message else None),
            error_message=state.error_message or state.artifact_error_message,
        )

    def _phase(self, run_id, step, state, phase, operation):
        started_at = datetime.now(UTC)
        started = time.perf_counter()
        self._emit(run_id=run_id, event_type="phase_start", step=step, phase=phase)
        artifacts_before = len(self._events)
        errors_before = len(state.evidence_errors)
        status = "passed"
        category = None
        message = None
        value = None
        interrupted = None
        metadata = {}
        try:
            value = operation()
            if isinstance(value, HarnessActionResult):
                status, category, message = value.status, value.failure_category, self._safe_text(value.error_message)
                metadata = getattr(self, "_invoke_report_metadata", {})
        except BaseException as exc:
            if self._persistence_failed:
                raise
            classified = self._classify(exc, phase, step) if isinstance(exc, Exception) else None
            if self._is_cancelled(exc) or classified == "cancelled":
                status, category, message, interrupted = "cancelled", "cancelled", "Execution cancelled.", exc
            elif not isinstance(exc, Exception):
                status, message, interrupted = "incomplete", "Execution interrupted.", exc
            else:
                status = "failed"
                category = "configuration_error" if isinstance(exc, ConfigurationError) else classified
                if phase == "finalize" and len(state.evidence_errors) > errors_before:
                    category = "artifact_error"
                message = self._safe_text(_safe_exception_message(exc))
            if interrupted is not None:
                state.interruption = interrupted
            if phase == "invoke":
                state.action_status = status
                self._emit(run_id=run_id, event_type="action_result", step=step, phase=phase, payload={"status": status, "failure_category": category, "error_message": message})
            if not state.failure_category or phase == "invoke":
                state.failure_category, state.error_message = category, message
            self._emit(run_id=run_id, event_type="step_error", step=step, phase=phase, payload={"status": status, "failure_category": category, "message": message})
        refs = [
            EvidenceArtifactRef.model_validate(event.payload["artifact"])
            for event in self._events[artifacts_before:]
            if event.event_type in {"artifact_captured", "artifact_failed"} and "artifact" in event.payload
        ]
        if len(state.evidence_errors) > errors_before and status == "passed":
            status, category = "failed", "artifact_error"
            message = str(state.evidence_errors[errors_before]["message"])
        ended_at = datetime.now(UTC)
        report = StepPhaseReport(
            step_id=step.step_id,
            phase=phase,
            status=status,
            started_at=started_at,
            ended_at=ended_at,
            duration_ms=self._duration_ms(started),
            unavailable_reason=None,
            failure_category=category,
            error_message=message,
            artifact_refs=refs,
            metadata={**self._safe_value(metadata), "timing_measured": True},
        )
        state.phase_reports.append(report)
        if phase == "invoke" and self._last_capability_execution_result is not None:
            self._last_capability_execution_result = self._last_capability_execution_result.model_copy(
                update={
                    "duration_ms": report.duration_ms,
                    "unavailable_reason": None,
                    "metadata": {**self._last_capability_execution_result.metadata, "timing_measured": True, "timing_scope": "invoke"},
                }
            )
        self._emit(
            run_id=run_id,
            event_type="phase_finish",
            step=step,
            phase=phase,
            payload={
                "status": status,
                "post_action_delay_seconds": self._effective_post_action_delay_seconds(self._resolve_capability(step.action_name)),
                "phase_report": report.model_dump(mode="json"),
            },
        )
        if interrupted is not None:
            raise interrupted
        return value

    def _prepare(self, run_id, step):
        context = self.harness.get_context()
        self._capture_artifacts(run_id=run_id, step=step, context=context, phase="prepare", reason="before-action", enabled=step.evidence_policy.capture_before)
        self.harness.before_action(step, context)
        return context

    def _invoke(self, run_id, step, capability, context, state):
        self._emit(run_id=run_id, event_type="harness_call_start", step=step, phase="invoke")
        validated = self._with_validated_params(step, capability)
        invoke_step = self._resolve_runtime_secret_text_step(validated, capability)
        capture_scope = getattr(self.harness, "capture_scope", None)
        with capture_scope(lambda ref: self._acknowledge_invoke_artifact(run_id, step, ref)) if capture_scope else nullcontext():
            result = self.harness.invoke_action(invoke_step, context)
        if capability is not None and capability.sensitivity and (not isinstance(result.output, dict) or "value" not in result.output):
            raise ConfigurationError("Sensitive capability result requires the normalized output.value shape.")
        if result.status in {"pending", "running"}:
            result = result.model_copy(update={"status": "incomplete", "error_message": "Action did not return a terminal result."})
        state.action_status = result.status
        self._last_capability_execution_result = self._capability_execution_result(result, validated, capability, context)
        if result.status in {"failed", "cancelled", "skipped", "incomplete"}:
            state.failure_category = result.failure_category or ("cancelled" if result.status == "cancelled" else "action_error")
            state.error_message = self._safe_text(result.error_message)
            self._emit(run_id=run_id, event_type="step_error", step=step, phase="invoke")
        metadata = self._action_result_metadata(result, validated, invoke_step, capability, context)
        self._invoke_report_metadata = self._with_post_action_delay_metadata(metadata, self._effective_post_action_delay_seconds(capability))
        self._emit(
            run_id=run_id,
            event_type="action_result",
            step=step,
            phase="invoke",
            payload={
                "status": result.status,
                "failure_category": result.failure_category,
                "error_message": self._safe_text(result.error_message),
                "action_name": step.action_name,
                "metadata": self._safe_value(metadata),
            },
        )
        self._emit(run_id=run_id, event_type="harness_call_finish", step=step, phase="invoke")
        self._action_result_artifacts(run_id, step, result, "invoke")
        return result

    def _acknowledge_invoke_artifact(self, run_id, step, artifact):
        ref = artifact if isinstance(artifact, EvidenceArtifactRef) else self._to_evidence_artifact_ref(artifact, step.step_id, "invoke")
        ref = ref.model_copy(update={"step_id": step.step_id, "step_execution_id": step.step_execution_id, "phase": "invoke"})
        existing = next(
            (
                event.payload["artifact"]
                for event in self._events
                if event.event_type in {"artifact_captured", "artifact_failed"} and isinstance(event.payload.get("artifact"), dict) and event.payload["artifact"].get("artifact_id") == ref.artifact_id
            ),
            None,
        )
        if existing is not None:
            stored = EvidenceArtifactRef.model_validate(existing)
            if (stored.path, stored.kind, stored.sha256, stored.size_bytes) != (ref.path, ref.kind, ref.sha256, ref.size_bytes):
                raise ValueError("Duplicate captured artifact identity changed content.")
            return stored
        if ref.availability == "failed" and self._state is not None:
            self._state.evidence_errors.append(
                {"kind": ref.kind, "phase": "invoke", "reason": ref.metadata.get("capture_reason", "action-result"), "message": ref.unavailable_reason, "artifact_id": ref.artifact_id}
            )
            self._state.artifact_error_message = self._state.artifact_error_message or ref.unavailable_reason
        self._emit(
            run_id=run_id,
            event_type="artifact_failed" if ref.availability == "failed" else "artifact_captured",
            step=step,
            phase="invoke",
            payload={
                "artifact": ref.model_dump(mode="json"),
                "artifact_id": ref.artifact_id,
                "kind": ref.kind,
                "path": ref.path.as_posix() if ref.path else None,
                "reason": ref.metadata.get("capture_reason", "action-result"),
                "phase": "invoke",
            },
        )
        return ref

    def _finalize(self, run_id, step, context, action_result):
        try:
            self.harness.after_action(step, context, action_result)
        finally:
            try:
                fresh_context = self.harness.get_context() if step.evidence_policy.capture_after else context
            except BaseException as exc:
                if step.evidence_policy.capture_after:
                    for kind in step.evidence_policy.artifact_kinds:
                        message = "Capture context unavailable." if not isinstance(exc, Exception) else self._safe_text(_safe_exception_message(exc))
                        ref = EvidenceArtifactRef(
                            artifact_id=f"{kind}-{step.step_id}-context-{uuid.uuid4().hex[:12]}",
                            kind=kind,
                            step_id=step.step_id,
                            step_execution_id=step.step_execution_id,
                            phase="finalize",
                            availability="failed",
                            unavailable_reason=message,
                            metadata={"requested_kind": kind, "capture_reason": "after-action"},
                        )
                        self._state.evidence_errors.append({"kind": kind, "phase": "finalize", "reason": "capture_context_unavailable", "message": message, "artifact_id": ref.artifact_id})
                        self._state.artifact_error_message = self._state.artifact_error_message or message
                        self._emit(run_id=run_id, event_type="artifact_failed", step=step, phase="finalize", payload={"artifact": ref.model_dump(mode="json")})
                raise
            self._capture_artifacts(run_id=run_id, step=step, context=fresh_context, phase="finalize", reason="after-action", enabled=step.evidence_policy.capture_after)

    def _classify(self, error, phase, step):
        try:
            return self.harness.classify_error(error, phase, step)
        except Exception:  # noqa: BLE001 - backend classifiers cannot suppress safe failure facts.
            return "harness_error"

    def _is_cancelled(self, error, step=None, phase=None):
        return isinstance(error, (asyncio.CancelledError, KeyboardInterrupt)) or type(error).__name__ == "TaskCancelledError"

    def _safe_text(self, text):
        return _credential_safe(text, self._secret_values)

    def _safe_value(self, value):
        return _credential_safe(value, self._secret_values)

    def _with_validated_params(self, step: ExecutableStep, capability: CapabilityDefinition | None) -> ExecutableStep:
        if capability is None:
            return step
        try:
            parsed = capability.params_model.model_validate(step.params, context={"safe_value": self._safe_value, "params_type": capability.params_model})
        except ValidationError as exc:
            raise ConfigurationError(
                "Invalid capability parameters.",
                context={
                    "step_id": step.step_id,
                    "action_name": step.action_name,
                    "validation_errors": self._validation_errors(exc),
                },
            ) from exc
        return step.model_copy(update={"params": parsed.model_dump(mode="json", exclude_none=True)})

    def _validation_errors(self, error: ValidationError) -> list[dict[str, object]]:
        try:
            return error.errors(include_url=False, include_context=False)
        except TypeError:
            return error.errors()

    def _resolve_capability(self, name: str) -> CapabilityDefinition | None:
        return self.capability_registry.resolve(name) if self.capability_registry is not None else None

    def _resolve_runtime_secret_text_step(self, step: ExecutableStep, capability: CapabilityDefinition | None) -> ExecutableStep:
        if capability is None:
            return step
        parsed = capability.params_model.model_validate(step.params)
        params = self._resolve_runtime_secret_text_params(parsed, step)
        return step.model_copy(update={"params": params}) if params != step.params else step

    def _resolve_runtime_secret_text_params(self, parsed: BaseModel, step: ExecutableStep) -> dict[str, object]:
        params = parsed.model_dump(mode="json", exclude_none=True)
        for name, value in parsed:
            if isinstance(value, BaseModel):
                params[name] = self._resolve_runtime_secret_text_params(value, step)
        if params.get("textType") != "runtimeSecret":
            return params
        text = params.get("text")
        if not isinstance(text, str):
            raise ConfigurationError(
                "Runtime secret text input requires a string text value.",
                context={"step_id": step.step_id, "action_name": step.action_name},
            )
        if self.runtime_secret_store is None:
            if not text.strip():
                raise ConfigurationError("Runtime secret name is empty.", context={"name": text})
            raise ConfigurationError("Runtime secret name is not allowed.", context={"name": text.strip(), "allowed": []})
        params["text"] = self.runtime_secret_store.resolve(text)
        if params["text"]:
            self._secret_values.add(params["text"])
        params["textType"] = "literal"
        return params

    def _finish_step(
        self,
        run_id: str,
        step: ExecutableStep,
        state: _StepExecutionState,
        *,
        status: RunnerStatus,
        failure_category: FailureCategory | None,
        error_message: str | None,
    ) -> RunnerStepResult:
        result = RunnerStepResult(
            step_id=step.step_id,
            source_step_id=step.source_step_id,
            step_execution_id=step.step_execution_id,
            invocation_path=step.invocation_path,
            source_ref=step.source_ref,
            status=status,
            action_status=state.action_status,
            action_name=step.action_name,
            kind=step.kind,
            started_at=state.started_at,
            ended_at=datetime.now(UTC),
            duration_ms=self._duration_ms(state.started),
            unavailable_reason=None,
            phase_reports=state.phase_reports,
            attempt_index=step.attempt_index,
            max_attempts=step.retry_policy.max_attempts,
            failure_category=failure_category,
            error_message=self._safe_text(error_message),
            evidence_errors=state.evidence_errors,
            metadata={**self._safe_value(step.metadata), "timing_measured": True, "evidence_policy": step.evidence_policy.model_dump(mode="json")},
        )
        if self.evidence_sink is not None:
            try:
                self.evidence_sink.record_step_result(result)
            except Exception as exc:
                self._persistence_failed = True
                exc.fsq_evidence_fatal = True
                raise
        self.last_step_result = result
        self._emit(run_id=run_id, event_type="step_finish", step=step, payload={"status": status})
        return result

    def _effective_post_action_delay_seconds(self, capability: CapabilityDefinition | None) -> float:
        if capability is None:
            return 0.0
        if capability.post_action_delay_seconds is not None:
            return capability.post_action_delay_seconds
        if capability.executor_kind == "common":
            return self.post_action_delay_seconds.common
        return self.post_action_delay_seconds.platform

    def _apply_post_action_delay(self, seconds: float) -> None:
        if seconds > 0:
            time.sleep(seconds)

    def _post_action_delay_metadata(self, seconds: float) -> dict[str, object]:
        return {"post_action_delay_seconds": seconds}

    def _with_post_action_delay_metadata(self, metadata: dict[str, object], seconds: float) -> dict[str, object]:
        metadata["post_action_delay_seconds"] = seconds
        return metadata

    def _emit(
        self,
        *,
        run_id: str,
        event_type: RunnerEventType,
        step: ExecutableStep,
        phase: StepPhase | None = None,
        payload: dict[str, object] | None = None,
    ) -> None:
        event = RunnerEvent(
            event_id=uuid.uuid4().hex,
            event_type=event_type,
            run_id=run_id,
            step_id=step.step_id,
            source_step_id=step.source_step_id,
            step_execution_id=step.step_execution_id,
            invocation_path=step.invocation_path,
            attempt_index=step.attempt_index,
            phase=phase,
            payload=self._safe_value(payload or {}),
        )
        if self.evidence_sink is not None:
            try:
                self.evidence_sink.record_event(event)
            except Exception as exc:
                self._persistence_failed = True
                exc.fsq_evidence_fatal = True
                raise
        self._events.append(event)

    def _capture_artifacts(
        self,
        *,
        run_id: str,
        step: ExecutableStep,
        context: object,
        phase: StepPhase,
        reason: str,
        enabled: bool,
    ) -> tuple[list[EvidenceArtifactRef], str | None]:
        if not enabled or not step.evidence_policy.artifact_kinds:
            return [], None

        refs: list[EvidenceArtifactRef] = []
        errors: list[str] = []
        for kind in step.evidence_policy.artifact_kinds:
            started = time.perf_counter()
            try:
                harness_ref = self.harness.capture_artifact(
                    kind=kind,
                    reason=reason,
                    context=context,
                    step_id=step.step_id,
                    phase=phase,
                )
                ref = self._to_evidence_artifact_ref(harness_ref, step.step_id, phase)
                if ref.metadata.get("status") == "unavailable":
                    ref = ref.model_copy(update={"availability": "unavailable", "unavailable_reason": str(ref.metadata.get("reason") or "capture_unavailable")})
            except Exception as exc:
                if self._is_cancelled(exc, step, phase) or self._classify(exc, phase, step) == "cancelled":
                    raise
                message = self._safe_text(_safe_exception_message(exc))
                ref = EvidenceArtifactRef(
                    artifact_id=f"{kind}-{step.step_id}-{phase}-{uuid.uuid4().hex[:12]}",
                    kind=kind,
                    step_id=step.step_id,
                    step_execution_id=step.step_execution_id,
                    phase=phase,
                    availability="failed",
                    unavailable_reason=message,
                )
            ref.metadata.update({"capture_reason": reason, "capture_duration_ms": self._duration_ms(started), "timing_measured": True, "requested_kind": kind})
            if ref.availability not in {"available", "not_applicable"}:
                message = ref.unavailable_reason or "Required evidence unavailable."
                errors.append(message)
                if self._state is not None:
                    self._state.artifact_error_message = self._state.artifact_error_message or message
                    self._state.evidence_errors.append({"kind": kind, "phase": phase, "reason": reason, "message": message, "artifact_id": ref.artifact_id})
            refs.append(ref)
            self._emit(
                run_id=run_id,
                event_type="artifact_captured" if ref.availability == "available" else "artifact_failed",
                step=step,
                phase=phase,
                payload={
                    "artifact_id": ref.artifact_id,
                    "kind": ref.kind,
                    "path": ref.path.as_posix() if ref.path is not None else None,
                    "reason": reason,
                    "phase": phase,
                    "artifact": ref.model_dump(mode="json"),
                },
            )
        return refs, errors[0] if errors else None

    def _to_evidence_artifact_ref(
        self,
        ref: HarnessArtifactRef,
        step_id: str,
        phase: StepPhase,
    ) -> EvidenceArtifactRef:
        return EvidenceArtifactRef(
            artifact_id=ref.artifact_id,
            kind=ref.kind,
            path=ref.path,
            mime_type=ref.mime_type,
            size_bytes=ref.size_bytes,
            sha256=ref.sha256,
            availability=ref.availability,
            unavailable_reason=ref.unavailable_reason,
            capture_occurrence=ref.capture_occurrence,
            created_at=ref.created_at,
            step_id=step_id,
            step_execution_id=step_id,
            phase=phase,
            metadata=dict(ref.metadata),
        )

    def _action_result_artifacts(
        self,
        run_id: str,
        step: ExecutableStep,
        action_result: HarnessActionResult,
        phase: StepPhase,
    ) -> list[EvidenceArtifactRef]:
        refs: list[EvidenceArtifactRef] = []
        for harness_ref in action_result.artifact_refs:
            if phase == "invoke":
                refs.append(self._acknowledge_invoke_artifact(run_id, step, harness_ref))
                continue
            ref = self._to_evidence_artifact_ref(harness_ref, step.step_id, phase)
            refs.append(ref)
            self._emit(
                run_id=run_id,
                event_type="artifact_captured",
                step=step,
                phase=phase,
                payload={
                    "artifact_id": ref.artifact_id,
                    "kind": ref.kind,
                    "path": ref.path.as_posix() if ref.path is not None else None,
                    "reason": "action-result",
                    "phase": phase,
                    "artifact": ref.model_dump(mode="json"),
                },
            )
        return refs

    def _action_result_metadata(
        self,
        action_result: HarnessActionResult,
        original_step: ExecutableStep,
        invoke_step: ExecutableStep,
        capability: CapabilityDefinition | None,
        context: object,
    ) -> dict[str, object]:
        if action_result.metadata.get("executor_kind") == "common":
            metadata = {**self._capability_metadata(capability, original_step, context), **action_result.metadata}
            if action_result.output is not None and not metadata.get("sensitivity"):
                metadata.setdefault("common_output", action_result.output)
            return metadata
        metadata: dict[str, object] = self._capability_metadata(capability, original_step, context)
        if action_result.metadata:
            metadata["harness_metadata"] = action_result.metadata
        if action_result.output is not None and not self._used_runtime_secret_text(original_step, invoke_step) and not (capability and capability.sensitivity):
            metadata["harness_output"] = action_result.output
        return metadata

    def _used_runtime_secret_text(self, original_step: ExecutableStep, invoke_step: ExecutableStep) -> bool:
        return original_step.params.get("textType") == "runtimeSecret" and original_step.params != invoke_step.params

    def _capability_metadata(
        self,
        capability: CapabilityDefinition | None,
        step: ExecutableStep,
        context: object,
    ) -> dict[str, object]:
        if capability is None:
            return {}
        metadata: dict[str, object] = {
            "capability_name": capability.name,
            "executor_kind": capability.executor_kind,
            "step_kind": capability.step_kind,
            "platform": capability.platform,
            "backend": capability.backend,
            "owner": capability.owner,
            "replay": capability.replay.model_dump(mode="json") if capability.replay else None,
            "sensitivity": capability.sensitivity,
        }
        params = self._safe_replay_params(step, capability, context)
        if params is not None and self._safe_value(params) != params:
            metadata["safe_replay_params"] = None
            metadata["replay_unavailable_reason"] = "sensitive_parameters_redacted"
            metadata["parameters_transformed"] = True
        else:
            metadata["safe_replay_params"] = params
        return metadata

    def _safe_replay_params(
        self,
        step: ExecutableStep,
        capability: CapabilityDefinition,
        context: object,
    ) -> dict[str, object] | None:
        params = dict(step.params)
        if params.get("textType") == "runtimeSecret":
            return params
        if capability.sensitivity:
            return None
        if callable(getattr(capability.params_model, "replay_params", None)):
            parsed = capability.params_model.model_validate(params)
            return parsed.replay_params(context)
        return params

    def _capability_execution_result(
        self,
        action_result: HarnessActionResult,
        step: ExecutableStep,
        capability: CapabilityDefinition | None,
        context: object,
    ) -> CapabilityExecutionResult | None:
        metadata = action_result.metadata
        if metadata.get("executor_kind") != "common":
            if capability is None:
                return None
            metadata = self._capability_metadata(capability, step, context)
            replay = metadata.get("replay")
            safe_replay_params = metadata.get("safe_replay_params")
            return CapabilityExecutionResult(
                capability_name=capability.name,
                executor_kind=capability.executor_kind,
                status=action_result.status,
                output=None if capability.sensitivity or step.params.get("textType") == "runtimeSecret" else self._safe_value(action_result.output),
                artifact_refs=list(action_result.artifact_refs),
                error_message=self._safe_text(action_result.error_message),
                failure_category=action_result.failure_category,
                duration_ms=action_result.duration_ms,
                replay=ReplayPolicy.model_validate(replay) if isinstance(replay, dict) else None,
                sensitivity=capability.sensitivity,
                safe_replay_params=safe_replay_params if isinstance(safe_replay_params, dict) else {},
                metadata=metadata,
            )
        replay = metadata.get("replay")
        safe_replay_params = metadata.get("safe_replay_params")
        duration_ms = metadata.get("duration_ms")
        return CapabilityExecutionResult(
            capability_name=str(metadata.get("capability_name") or action_result.action_name),
            executor_kind="common",
            status=action_result.status,
            output=action_result.output,
            error_message=action_result.error_message,
            failure_category=action_result.failure_category,
            duration_ms=duration_ms if isinstance(duration_ms, int) else None,
            unavailable_reason=None if isinstance(duration_ms, int) else "unmeasured",
            replay=ReplayPolicy.model_validate(replay) if isinstance(replay, dict) else None,
            sensitivity=bool(metadata.get("sensitivity")),
            safe_replay_params=safe_replay_params if isinstance(safe_replay_params, dict) else {},
            metadata=dict(metadata),
        )

    def _is_failed_result(
        self,
        action_result: HarnessActionResult | None,
        failure_category: FailureCategory | None,
    ) -> bool:
        return bool(failure_category) or bool(action_result and action_result.status in {"failed", "cancelled", "skipped"})

    def _result_status(
        self,
        action_result: HarnessActionResult | None,
        failure_category: FailureCategory | None,
        artifact_error_message: str | None,
    ) -> RunnerStatus:
        if action_result and action_result.status in {"cancelled", "incomplete"}:
            return action_result.status
        if artifact_error_message:
            return "failed"
        if action_result and action_result.status in {"failed", "cancelled", "skipped"}:
            return action_result.status
        if failure_category:
            return "failed"
        return "passed"

    def _duration_ms(self, started: float) -> int:
        return max(0, int((time.perf_counter() - started) * 1000))


def _safe_exception_message(error: BaseException) -> str:
    message = str(error).strip()
    exception_type = type(error).__name__
    diagnostic = _filesystem_path_diagnostic(error)
    text = f"{exception_type}: {message}" if message else exception_type
    return f"{text} ({diagnostic})" if diagnostic else text


def _filesystem_path_diagnostic(error: BaseException) -> str | None:
    if not isinstance(error, OSError):
        return None
    filename = getattr(error, "filename", None)
    if not isinstance(filename, str) or not filename:
        return None
    path_length = len(filename)
    return f"local path length: {path_length}"


def _sanitize_urls(text: str) -> str:
    """Persist safe URL copies; callers retain the original invocation values in memory."""
    from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

    sensitive = re.compile(r"^(?:token|access_token|refresh_token|id_token|password|passwd|api[_-]?key|client[_-]?secret|secret|signature|sig|authorization|cookie)$", re.I)

    def replace(match):
        original = match.group(0)
        try:
            parsed = urlsplit(original)
            query = parse_qsl(parsed.query, keep_blank_values=True)
            changed = "@" in parsed.netloc or any(sensitive.fullmatch(key) for key, _ in query)
            if not changed:
                return original
            host = parsed.netloc.rsplit("@", 1)[-1]
            safe_query = urlencode([(key, "[REDACTED]" if sensitive.fullmatch(key) else value) for key, value in query])
            return urlunsplit((parsed.scheme, host, parsed.path, safe_query, parsed.fragment))
        except ValueError:
            return "[REDACTED_URL]"

    return re.sub(r"https?://[^\s<>\"']+", replace, text, flags=re.I)


def _credential_safe(value, secrets=(), depth=0):
    import json
    from urllib.parse import unquote

    if depth > 20:
        return "[REDACTED: nesting limit]"
    keys = {
        "password",
        "passwd",
        "pwd",
        "api_key",
        "apikey",
        "token",
        "access_token",
        "refresh_token",
        "id_token",
        "client_secret",
        "secret",
        "authorization",
        "proxy_authorization",
        "cookie",
        "set_cookie",
        "private_values",
        "credentials",
    }
    if isinstance(value, dict):
        is_locator, locator = WebLocator.preserve_for_redaction(value, secrets)
        if is_locator:
            return locator
        safe = {
            key: "***" if re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", unquote(str(key)).strip()).lower().replace("-", "_") in keys else _credential_safe(item, secrets, depth + 1)
            for key, item in value.items()
        }
        return WebLocator.finish_redaction(value, safe)
    if isinstance(value, (tuple, list)):
        return [_credential_safe(item, secrets, depth + 1) for item in value]
    if not isinstance(value, str):
        return value
    for secret in sorted((item for item in secrets if item), key=len, reverse=True):
        value = value.replace(secret, "***")
    stripped = value.strip()
    if stripped.startswith(("{", "[", '"')):
        try:
            decoded = json.loads(value)
        except ValueError:
            decoded = None
        if isinstance(decoded, (dict, list, str)) and decoded != value:
            safe = _credential_safe(decoded, secrets, depth + 1)
            if safe != decoded:
                return json.dumps(safe, ensure_ascii=False)
    value = re.sub(r"(?im)((?:proxy[-_]authorization|authorization|set[-_]cookie|cookie)\s*[:=]\s*)[^\r\n]+", r"\1***", value)
    value = re.sub(r"(?i)\b(?:Bearer|Basic)\s+[^\s,;\r\n]+", "Bearer ***", value)
    value = re.sub(r"(?i)(\b(?:password|passwd|pwd|api[_-]?key|client[_-]?secret|secret|(?:access[_-]?|refresh[_-]?|id[_-]?)?token)\s*[:=]\s*)(?:\"[^\"]*\"|'[^']*'|[^\s,;]+)", r"\1***", value)
    return _sanitize_urls(value)
