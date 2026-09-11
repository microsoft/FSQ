# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import json
from pathlib import Path

from fsq_agent.core import EvidenceRecorder
from fsq_agent.models import (
    EvidenceArtifactRef,
    RunnerEvent,
    RunnerStepResult,
    StepPhaseReport,
)


def _artifact() -> EvidenceArtifactRef:
    return EvidenceArtifactRef(
        artifact_id="screenshot-1",
        kind="screenshot",
        path=Path("runs/run-1/step-1-after.png"),
        mime_type="image/png",
        step_id="step-1",
        phase="finalize",
    )


def _passed_step_result() -> RunnerStepResult:
    return RunnerStepResult(
        step_id="step-1",
        status="passed",
        phase_reports=[
            StepPhaseReport(step_id="step-1", phase="prepare", status="passed"),
            StepPhaseReport(step_id="step-1", phase="invoke", status="passed"),
            StepPhaseReport(
                step_id="step-1",
                phase="finalize",
                status="passed",
                artifact_refs=[_artifact()],
            ),
        ],
    )


def _failed_step_result() -> RunnerStepResult:
    return RunnerStepResult(
        step_id="step-1",
        status="failed",
        failure_category="action_error",
        error_message="tap failed",
        phase_reports=[
            StepPhaseReport(step_id="step-1", phase="prepare", status="passed"),
            StepPhaseReport(
                step_id="step-1",
                phase="invoke",
                status="failed",
                failure_category="action_error",
                error_message="tap failed",
            ),
            StepPhaseReport(
                step_id="step-1",
                phase="finalize",
                status="passed",
                artifact_refs=[_artifact()],
            ),
        ],
    )


def test_evidence_recorder_builds_bundle_from_events_and_step_results(tmp_path: Path) -> None:
    recorder = EvidenceRecorder(
        run_id="run-1",
        output_dir=tmp_path,
        bundle_id="bundle-1",
        metadata={"case_id": "case-1"},
    )
    recorder.record_event(RunnerEvent(run_id="run-1", event_type="step_start", step_id="step-1"))
    recorder.record_event(RunnerEvent(run_id="run-1", event_type="step_finish", step_id="step-1"))
    recorder.record_step_result(_passed_step_result())

    bundle = recorder.build_bundle()

    assert bundle.bundle_id == "bundle-1"
    assert bundle.run_id == "run-1"
    assert bundle.metadata == {"case_id": "case-1"}
    assert [event.event_type for event in bundle.events] == ["step_start", "step_finish"]
    assert [step.step_id for step in bundle.steps] == ["step-1"]
    assert [artifact.artifact_id for artifact in bundle.artifacts] == ["screenshot-1"]


def test_evidence_recorder_writes_manifest_json(tmp_path: Path) -> None:
    recorder = EvidenceRecorder(run_id="run-1", output_dir=tmp_path, bundle_id="bundle-1")
    recorder.record_event(RunnerEvent(run_id="run-1", event_type="step_error", step_id="step-1", phase="invoke"))
    recorder.record_step_result(_failed_step_result())

    manifest_path = recorder.write_manifest()

    assert manifest_path == tmp_path / "evidence-manifest.json"
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert payload["bundle_id"] == "bundle-1"
    assert payload["run_id"] == "run-1"
    assert payload["manifest_path"] == "evidence-manifest.json"
    assert payload["steps"][0]["failure_category"] == "action_error"
    assert payload["steps"][0]["phase_reports"][1]["phase"] == "invoke"
    assert payload["steps"][0]["phase_reports"][1]["error_message"] == "tap failed"
    assert payload["artifacts"][0]["path"] == "runs/run-1/step-1-after.png"
