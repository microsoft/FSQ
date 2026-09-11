# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import logging
from collections.abc import Sequence

from fsq_agent.core.interfaces import CancellationCheck, EvidenceSink
from fsq_agent.core.runner._runner import StepRunner
from fsq_agent.models import EvidenceBundle, ExecutableStep, RunnerStepResult

_STOP_STATUSES = {"failed", "cancelled", "skipped", "incomplete"}


class StepSequenceRunner:
    def __init__(self, step_runner: StepRunner, evidence_recorder: EvidenceSink, *, cancellation_check: CancellationCheck | None = None) -> None:
        self.step_runner = step_runner
        self.evidence_recorder = evidence_recorder
        if self.step_runner.evidence_sink is None:
            self.step_runner.evidence_sink = evidence_recorder
        elif self.step_runner.evidence_sink is not evidence_recorder:
            raise ValueError("A step sequence must use its runner's evidence sink.")
        self._sequence_index = 0
        self.cancellation_check = cancellation_check

    def run_steps(self, run_id: str, steps: Sequence[ExecutableStep], teardown_steps: Sequence[ExecutableStep] = ()) -> EvidenceBundle:
        self._sequence_index += 1
        normal = self._plan(steps, "case")
        teardown = self._plan(teardown_steps, "teardown")
        interrupted = None
        try:
            for index, step in enumerate(normal):
                if self.cancellation_check is not None:
                    self.cancellation_check()
                result = self.step_runner.run_step(run_id=run_id, step=step)
                if result.status in _STOP_STATUSES:
                    for remaining in normal[index + 1 :]:
                        self._unexecuted(remaining, "skipped", "blocked_by_prior_step", result.step_execution_id)
                    break
        except BaseException as exc:  # noqa: BLE001 - preserve the original interruption after bounded teardown.
            interrupted = exc
            attempted = self.step_runner.last_step_result is not None and self.step_runner.last_step_result.invocation_path == step.invocation_path
            for remaining in normal[index + 1 if attempted else index :]:
                try:
                    self._unexecuted(remaining, "incomplete", "execution_interrupted", None)
                except BaseException as persistence_error:  # noqa: BLE001 - preserve the original interruption.
                    logging.getLogger(__name__).warning("Interrupted step persistence failed (%s)", type(persistence_error).__name__)
        finally:
            for step in teardown:
                try:
                    self.step_runner.run_step(run_id=run_id, step=step)
                except BaseException as exc:  # noqa: BLE001 - finish independent teardown without swallowing interruption.
                    interrupted = interrupted or exc
        if interrupted is not None:
            raise interrupted
        return self.evidence_recorder.build_bundle()

    def _plan(self, steps, phase):
        planned = []
        for index, step in enumerate(steps, start=1):
            source = step.source_step_id or step.step_id
            path = step.invocation_path or (f"sequence-{self._sequence_index}", phase, str(index))
            item = step.model_copy(update={"source_step_id": source, "invocation_path": path})
            if hasattr(self.evidence_recorder, "record_planned_step"):
                self.evidence_recorder.record_planned_step(item)
            planned.append(item)
        return planned

    def _unexecuted(self, step, status, reason, blocker):
        self.evidence_recorder.record_step_result(
            RunnerStepResult(
                step_id=step.step_id,
                source_step_id=step.source_step_id,
                invocation_path=step.invocation_path,
                source_ref=step.source_ref,
                status=status,
                action_name=step.action_name,
                kind=step.kind,
                skip_reason=reason,
                blocked_by_step=blocker,
                unavailable_reason="not_executed",
                metadata=dict(step.metadata),
            )
        )
