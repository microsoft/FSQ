# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import asyncio
from pathlib import Path
from typing import Any

import pytest

import fsq_agent.core.runner._runner as runner_module
from fsq_agent._capability_bootstrap import build_capability_registry
from fsq_agent.core import EvidenceRecorder, StepRunner, StepSequenceRunner
from fsq_agent.models import (
    ExecutableStep,
    FailureCategory,
    HarnessActionResult,
    HarnessArtifactRef,
    HarnessContext,
    StepPhase,
)


class SequenceHarness:
    def __init__(self, fail_action: str | None = None) -> None:
        self.fail_action = fail_action
        self.calls: list[str] = []

    def get_context(self) -> HarnessContext:
        self.calls.append("get_context")
        return HarnessContext(platform="android", session_id="session-1")

    def action_space(self) -> dict[str, Any]:
        return {}

    def before_action(self, step: ExecutableStep, context: HarnessContext) -> None:
        self.calls.append(f"before:{step.step_id}")

    def invoke_action(self, step: ExecutableStep, context: HarnessContext) -> HarnessActionResult:
        self.calls.append(f"invoke:{step.step_id}")
        status = "failed" if step.action_name == self.fail_action else "passed"
        return HarnessActionResult(status=status, action_name=step.action_name)

    def after_action(
        self,
        step: ExecutableStep,
        context: HarnessContext,
        action_result: HarnessActionResult | None,
    ) -> None:
        self.calls.append(f"after:{step.step_id}:{action_result.status if action_result else 'none'}")

    def capture_artifact(
        self,
        kind: str,
        reason: str,
        context: HarnessContext,
        step_id: str,
        phase: StepPhase,
    ) -> HarnessArtifactRef:
        return HarnessArtifactRef(artifact_id=f"{kind}-{step_id}-{phase}-{reason}", kind="log", path=Path(f"runs/{step_id}-{phase}-{reason}.log"))

    def classify_error(self, error: BaseException, phase: StepPhase, step: ExecutableStep) -> FailureCategory:
        return "unknown"


def _step(step_id: str, action_name: str) -> ExecutableStep:
    params: dict[str, object] = {}
    if action_name == "tapOn":
        params = {"target": "Button"}
    elif action_name == "inputText":
        params = {"target": "Field", "text": "hello"}
    return ExecutableStep(step_id=step_id, kind="action", action_name=action_name, params=params)


def _runner(harness: SequenceHarness, recorder: EvidenceRecorder) -> StepSequenceRunner:
    return StepSequenceRunner(
        step_runner=StepRunner(
            harness=harness,
            capability_registry=build_capability_registry(),
        ),
        evidence_recorder=recorder,
    )


def test_step_sequence_runner_runs_steps_in_order_and_records_evidence(tmp_path: Path) -> None:
    harness = SequenceHarness()
    recorder = EvidenceRecorder(run_id="run-1", output_dir=tmp_path)
    runner = _runner(harness, recorder)

    bundle = runner.run_steps(run_id="run-1", steps=[_step("step-1", "tapOn"), _step("step-2", "inputText")])

    assert [step.step_id for step in bundle.steps] == ["step-1", "step-2"]
    assert [step.status for step in bundle.steps] == ["passed", "passed"]
    assert [event.event_type for event in bundle.events].count("step_start") == 2
    assert harness.calls == [
        "get_context",
        "before:step-1",
        "invoke:step-1",
        "after:step-1:passed",
        "get_context",
        "get_context",
        "before:step-2",
        "invoke:step-2",
        "after:step-2:passed",
        "get_context",
    ]


def test_interrupted_sequence_persistence_cannot_replace_cancellation(tmp_path: Path, monkeypatch, caplog) -> None:
    harness = SequenceHarness()
    recorder = EvidenceRecorder(run_id="run-1", output_dir=tmp_path)
    runner = _runner(harness, recorder)
    primary = asyncio.CancelledError("primary cancellation")

    def cancel():
        raise primary

    def fail_unexecuted(*args, **kwargs):
        raise OSError("private disk failure")

    runner.cancellation_check = cancel
    monkeypatch.setattr(runner, "_unexecuted", fail_unexecuted)
    with pytest.raises(asyncio.CancelledError) as failure:
        runner.run_steps("run-1", [_step("first", "tapOn"), _step("second", "tapOn")], [_step("teardown", "killApp")])
    assert failure.value is primary
    assert "invoke:teardown" in harness.calls
    assert caplog.text.count("Interrupted step persistence failed (OSError)") == 2
    assert "private disk failure" not in caplog.text


def test_step_sequence_runner_stops_after_first_failed_step(tmp_path: Path) -> None:
    harness = SequenceHarness(fail_action="tap_on")
    recorder = EvidenceRecorder(run_id="run-1", output_dir=tmp_path)
    runner = _runner(harness, recorder)

    bundle = runner.run_steps(run_id="run-1", steps=[_step("step-1", "tapOn"), _step("step-2", "inputText")])

    assert [step.step_id for step in bundle.steps] == ["step-1", "step-2"]
    assert bundle.steps[1].status == "skipped"
    assert bundle.steps[1].blocked_by_step == bundle.steps[0].step_execution_id
    assert bundle.steps[0].status == "failed"
    assert "before:step-2" not in harness.calls


def test_step_sequence_runner_runs_teardown_after_failed_normal_step(tmp_path: Path) -> None:
    harness = SequenceHarness(fail_action="tap_on")
    recorder = EvidenceRecorder(run_id="run-1", output_dir=tmp_path)
    runner = _runner(harness, recorder)

    bundle = runner.run_steps(
        run_id="run-1",
        steps=[_step("step-1", "tapOn"), _step("step-2", "inputText")],
        teardown_steps=[_step("teardown-1", "killApp")],
    )

    assert [step.step_id for step in bundle.steps] == ["step-1", "step-2", "teardown-1"]
    assert [step.status for step in bundle.steps] == ["failed", "skipped", "passed"]
    assert "before:step-2" not in harness.calls
    assert harness.calls == [
        "get_context",
        "before:step-1",
        "invoke:step-1",
        "after:step-1:failed",
        "get_context",
        "get_context",
        "before:teardown-1",
        "invoke:teardown-1",
        "after:teardown-1:passed",
        "get_context",
    ]


def test_step_sequence_runner_runs_teardown_after_successful_normal_steps(tmp_path: Path) -> None:
    harness = SequenceHarness()
    recorder = EvidenceRecorder(run_id="run-1", output_dir=tmp_path)
    runner = _runner(harness, recorder)

    bundle = runner.run_steps(
        run_id="run-1",
        steps=[_step("step-1", "tapOn")],
        teardown_steps=[_step("teardown-1", "killApp")],
    )

    assert [step.step_id for step in bundle.steps] == ["step-1", "teardown-1"]
    assert [step.status for step in bundle.steps] == ["passed", "passed"]


def test_step_sequence_runner_records_executed_steps_without_sequence_sleep(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = SequenceHarness()
    sleep_calls: list[float] = []

    def fake_sleep(seconds: float) -> None:
        sleep_calls.append(seconds)

    monkeypatch.setattr(runner_module.time, "sleep", fake_sleep)

    recorder = EvidenceRecorder(run_id="run-1", output_dir=tmp_path)
    runner = _runner(harness, recorder)

    bundle = runner.run_steps(
        run_id="run-1",
        steps=[_step("step-1", "tapOn"), _step("step-2", "inputText")],
        teardown_steps=[_step("teardown-1", "killApp")],
    )

    assert sleep_calls == []
    assert [step.step_id for step in bundle.steps] == ["step-1", "step-2", "teardown-1"]
    assert [event.event_type for event in bundle.events].count("step_start") == 3
    assert harness.calls == [
        "get_context",
        "before:step-1",
        "invoke:step-1",
        "after:step-1:passed",
        "get_context",
        "get_context",
        "before:step-2",
        "invoke:step-2",
        "after:step-2:passed",
        "get_context",
        "get_context",
        "before:teardown-1",
        "invoke:teardown-1",
        "after:teardown-1:passed",
        "get_context",
    ]


def test_step_sequence_runner_does_not_sleep_between_steps(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = SequenceHarness()
    sleep_calls: list[float] = []

    def fake_sleep(seconds: float) -> None:
        sleep_calls.append(seconds)

    monkeypatch.setattr(runner_module.time, "sleep", fake_sleep)

    recorder = EvidenceRecorder(run_id="run-1", output_dir=tmp_path)
    runner = _runner(harness, recorder)

    runner.run_steps(
        run_id="run-1",
        steps=[_step("step-1", "tapOn"), _step("step-2", "inputText")],
        teardown_steps=[_step("teardown-1", "killApp")],
    )

    assert sleep_calls == []
