# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from fsq_agent.core import EvidenceRecorder
from fsq_agent.execution import RunLifecycleService, allocate_run, transition_run, write_run_metadata
from fsq_agent.models import EvidenceArtifactRef, EvidenceBundle, ExecutableStep, RunnerEvent, RunnerStepResult, RunSource, RunStepCounts, StepPhaseReport


def _run(root, mode="strict"):
    metadata = allocate_run(workspace=root, workspace_name="demo", platform="web", source_id="test", mode=mode, source=RunSource(kind="goal", goal_summary="test"))
    return root / ".fsq/runs/web" / metadata.run_id, metadata


def test_supported_metadata_writer_cannot_rewrite_terminal_or_stale_state(tmp_path):
    run, metadata = _run(tmp_path)
    running = transition_run(run, metadata, "running")
    with pytest.raises(ValueError, match="stale"):
        write_run_metadata(run, metadata)
    terminal = transition_run(run, running, "success")
    with pytest.raises(ValueError, match="immutable"):
        write_run_metadata(run, terminal.model_copy(update={"status": "failed"}))


@pytest.mark.parametrize("step_status,expected", [("cancelled", "cancelled"), ("incomplete", "inconclusive")])
def test_explore_verifier_success_cannot_erase_unfinished_action(tmp_path, step_status, expected):
    run, metadata = _run(tmp_path, "explore")
    bundle = EvidenceBundle(bundle_id="e", run_id=metadata.run_id, completeness="complete", steps=[RunnerStepResult(step_id="one", status=step_status)])
    frozen = RunLifecycleService.freeze(run, metadata, bundle=bundle, verification={"status": "success"})
    assert frozen.outcome == expected


def test_failed_required_artifact_and_partial_bundle_prevent_success(tmp_path):
    run, metadata = _run(tmp_path)
    ref = EvidenceArtifactRef(artifact_id="missing", kind="screenshot", availability="failed", unavailable_reason="capture failed", step_id="one", phase="prepare")
    step = RunnerStepResult(
        step_id="one",
        status="passed",
        metadata={"evidence_policy": {"capture_before": True, "artifact_kinds": ["screenshot"]}},
        phase_reports=[StepPhaseReport(step_id="one", phase="prepare", status="passed", artifact_refs=[ref])],
    )
    bundle = EvidenceBundle(bundle_id="e", run_id=metadata.run_id, steps=[step], artifacts=[ref], completeness="partial", warnings=["Incomplete tail"])
    result = RunLifecycleService.freeze(run, metadata, bundle=bundle)
    assert result.outcome != "success"
    assert result.evidence["status"] == "partial"


def test_run_timing_uses_monotonic_duration_not_wall_clock(tmp_path, monkeypatch):
    monkeypatch.setattr("fsq_agent.execution.runs.time.perf_counter", lambda: 10.0)
    run, metadata = _run(tmp_path)
    monkeypatch.setattr("fsq_agent.execution.runs.time.perf_counter", lambda: 10.125)
    done = transition_run(run, metadata, "success", completed_at=datetime(2099, 1, 1, tzinfo=UTC))
    assert done.duration_ms == 125


def test_count_and_outcome_models_reject_incoherent_facts(tmp_path):
    with pytest.raises(ValidationError, match="counts"):
        RunStepCounts(total=1, passed=99)
    _, metadata = _run(tmp_path)
    with pytest.raises(ValidationError, match="evidence"):
        type(metadata).model_validate({**metadata.model_dump(), "evidence": {"status": "fabricated"}})


def test_recovery_rejects_source_identity_change_and_artifact_collision(tmp_path):
    recorder = EvidenceRecorder(run_id="run", output_dir=tmp_path)
    recorder.record_event(RunnerEvent(run_id="run", event_type="step_start", step_id="exec", step_execution_id="exec", source_step_id="A"))
    with pytest.raises(ValueError, match="identity"):
        recorder.record_step_result(RunnerStepResult(step_id="exec", step_execution_id="exec", source_step_id="B", status="passed"))


def test_live_bundle_accounts_for_unattempted_planned_leaves(tmp_path):
    recorder = EvidenceRecorder(run_id="run", output_dir=tmp_path)
    recorder.record_planned_step(ExecutableStep(step_id="planned", source_step_id="source", invocation_path=("root",), kind="action", action_name="tap"))
    bundle = recorder.build_bundle()
    assert len(bundle.steps) == 1
    assert bundle.steps[0].status == "incomplete"
    assert bundle.steps[0].step_execution_id is None


def test_recovery_snapshot_fingerprints_match_consumed_bytes(tmp_path):
    import hashlib

    recorder = EvidenceRecorder(run_id="run", output_dir=tmp_path)
    recorder.record_event(RunnerEvent(run_id="run", event_type="step_start", step_id="exec"))
    recovered = EvidenceRecorder.recover_bundle(tmp_path)
    snapshot = recovered.metadata["recovery_snapshot"]
    assert snapshot["files"]["evidence-events.jsonl"] == hashlib.sha256((tmp_path / "evidence-events.jsonl").read_bytes()).hexdigest()
    assert snapshot["files"]["evidence-manifest.json"] is None
    assert snapshot["journal_sequence"] == 1


def test_source_identity_includes_path_and_executed_snapshot(tmp_path):
    from fsq_agent.case_dsl import FsqCaseLoader

    paths = [tmp_path / "a/login.fsq.yaml", tmp_path / "b/login.fsq.yaml"]
    cases = []
    for path in paths:
        path.parent.mkdir()
        path.write_text("schemaVersion: fsq.ai-test/v1\nname: Login\nplatform: web\n---\n- startBrowser: {}\n")
        cases.append(FsqCaseLoader().load_case(path))
    first = RunLifecycleService.source_step_id(cases[0], 0, source_path="a/login.fsq.yaml")
    second = RunLifecycleService.source_step_id(cases[1], 0, source_path="b/login.fsq.yaml")
    assert first != second
    retained = RunLifecycleService.case_source_bytes(cases[0])
    paths[0].write_text("changed after parsing")
    assert RunLifecycleService.case_source_bytes(cases[0]) == retained


def test_supported_deterministic_api_creates_and_freezes_explicit_run(tmp_path):
    from fsq_agent.execution import load_run_metadata, run_fsq_core_case
    from fsq_agent.models import HarnessActionResult, HarnessContext

    class Harness:
        def get_context(self):
            return HarnessContext(platform="web")

        def before_action(self, step, context):
            pass

        def invoke_action(self, step, context):
            return HarnessActionResult(status="passed", action_name=step.action_name)

        def after_action(self, step, context, result):
            pass

        def classify_error(self, error, phase, step):
            return "action_error"

    run = tmp_path / "custom"
    run_fsq_core_case(
        case_path=tmp_path / "authored.fsq.yaml",
        harness=Harness(),
        output_dir=run,
        run_id="custom-id",
        steps=[ExecutableStep(step_id="explicit", kind="diagnostic", action_name="waitMs", params={"duration_ms": 1})],
    )
    assert load_run_metadata(run).run_id == "custom-id"
    assert load_run_metadata(run).status == "success"
    assert RunLifecycleService.load_result(run).counts.total == 1
    assert (run / "sources").is_dir()


def test_resource_provenance_has_versions_and_explicit_unknown_revision(tmp_path):
    from fsq_agent.config import Settings

    run, metadata = _run(tmp_path)
    settings = Settings(harness={"platform": "web"})
    result = RunLifecycleService.snapshot_sources(run, metadata, sources={"goal": "test"}, configuration=RunLifecycleService.safe_configuration(settings))
    producer = result.provenance["producer"]
    assert producer["python_version"]
    assert producer["backend_package"] == "playwright"
    assert producer["repository_revision"] is None
    assert producer["repository_revision_unavailable_reason"] == "not_reported"


def test_dynamic_proposal_is_persisted_before_action_crash(tmp_path):
    import asyncio

    from test_durable_evidence import JournalHarness, _runner, _step

    runner, _ = _runner(tmp_path, JournalHarness(tmp_path, cancel=True))
    with pytest.raises(asyncio.CancelledError):
        runner.run_step("run", _step())
    bundle = EvidenceRecorder.recover_bundle(tmp_path)
    start = next(event for event in bundle.events if event.event_type == "step_start")
    assert start.payload["proposal"]["params"] == {"target": "Button"}
    assert start.payload["proposal"]["capability"]["capability_name"] == "tap_on"


def test_after_capture_uses_new_session_context(tmp_path):
    from pydantic import BaseModel

    from fsq_agent.core import CapabilityRegistry, StepRunner
    from fsq_agent.models import CapabilityDefinition, HarnessActionResult, HarnessArtifactRef, HarnessContext

    class Params(BaseModel):
        pass

    class Harness:
        started = False

        def __init__(self):
            self.observed = []

        def get_context(self):
            return HarnessContext(platform="web", metadata={"started": self.started})

        def before_action(self, step, context):
            pass

        def invoke_action(self, step, context):
            self.started = True
            return HarnessActionResult(status="passed", action_name=step.action_name)

        def after_action(self, step, context, result):
            pass

        def capture_artifact(self, *, kind, reason, context, step_id, phase):
            self.observed.append(context.metadata["started"])
            return HarnessArtifactRef(artifact_id=kind, kind=kind, path=Path(kind))

        def classify_error(self, error, phase, step):
            return "action_error"

    from pathlib import Path

    harness = Harness()
    runner = StepRunner(harness, capability_registry=CapabilityRegistry.from_definitions([CapabilityDefinition(name="launch", executor_kind="driver", params_model=Params, step_kind="setup")]))
    result = runner.run_step("run", ExecutableStep(step_id="launch", kind="setup", action_name="launch"))
    assert result.status == "passed"
    assert harness.observed == [True, True]


def test_old_inconsistent_counts_are_unknown_without_invented_zeros(tmp_path):
    _, metadata = _run(tmp_path)
    payload = {**metadata.model_dump(), "schema_version": "fsq.run/v1", "result": {"steps": {"total": 0, "passed": 0, "failed": 2}}}
    restored = type(metadata).model_validate(payload)
    assert restored.result.steps is None


def test_frozen_verification_and_evidence_statuses_are_validated(tmp_path):
    from fsq_agent.models import RunExecutionResult

    run, metadata = _run(tmp_path)
    frozen = RunLifecycleService.freeze(run, metadata, bundle=EvidenceBundle(bundle_id="e", run_id=metadata.run_id, completeness="complete", steps=[RunnerStepResult(step_id="ok", status="passed")]))
    for updates in ({"evidence": {"status": "fabricated"}}, {"verification": {"status": "fabricated"}}, {"counts": {"total": 1, "passed": 3}}):
        with pytest.raises(ValidationError):
            RunExecutionResult.model_validate({**frozen.model_dump(), **updates})


def test_unknown_capability_duration_stays_unknown():
    from fsq_agent.models import CapabilityExecutionResult

    result = CapabilityExecutionResult(capability_name="action", executor_kind="driver", status="passed")
    assert result.duration_ms is None
    assert result.unavailable_reason == "unmeasured"
