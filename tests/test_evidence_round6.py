# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from fsq_agent.core import EvidenceRecorder
from fsq_agent.execution import RecordingService, RunLifecycleService, allocate_run
from fsq_agent.models import EvidenceBundle, RunExecutionResult, RunnerStepResult, RunSource


def _run(root):
    metadata = allocate_run(workspace=root, workspace_name="test", platform="web", mode="strict", source_id="run", source=RunSource(kind="case", case_id="test"))
    return root / ".fsq/runs/web" / metadata.run_id, metadata


def test_checkpoint_artifact_conflict_cannot_be_discarded(tmp_path):
    from test_durable_evidence import JournalHarness, _runner, _step

    runner, recorder = _runner(tmp_path, JournalHarness(tmp_path))
    runner.run_step("run", _step())
    path = recorder.write_manifest()
    payload = json.loads(path.read_text())
    payload["artifacts"][0]["sha256"] = "0" * 64
    path.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="checkpoint"):
        EvidenceRecorder.recover_bundle(tmp_path)


def test_checkpoint_partial_completeness_is_not_cleared(tmp_path):
    from test_durable_evidence import JournalHarness, _runner, _step

    runner, recorder = _runner(tmp_path, JournalHarness(tmp_path))
    runner.run_step("run", _step())
    path = recorder.write_manifest()
    payload = json.loads(path.read_text())
    payload["completeness"] = "partial"
    path.write_text(json.dumps(payload))
    bundle = EvidenceRecorder.recover_bundle(tmp_path)
    assert bundle.completeness == "partial"
    assert bundle.warnings


@pytest.mark.parametrize("value", ["line1\nPRIVATE_CANARY", 'quote"PRIVATE_CANARY', "back\\PRIVATE_CANARY"])
def test_escaped_known_source_secret_removed_before_hash(tmp_path, value):
    run, metadata = _run(tmp_path)
    source = json.dumps({"description": value, "name": "safe"})
    result = RunLifecycleService.snapshot_sources(run, metadata, sources={"case": source.encode()}, secret_values=(value,))
    retained = (run / result.source.snapshot_path).read_text()
    assert "PRIVATE_CANARY" not in retained
    assert result.provenance["sources"][0]["transformed"] is True


def test_lineage_mapping_removes_unapproved_fields_and_secret_values(tmp_path):
    run, _ = _run(tmp_path)
    RunLifecycleService.append_lineage(run, {"kind": "replay", "command_mapping": [{"command_index": 0, "source_step_id": "safe", "password": "PRIVATE_CANARY"}]})
    recorded = json.loads((run / "lineage.jsonl").read_text())
    assert "password" not in recorded["command_mapping"][0]
    assert "PRIVATE_CANARY" not in (run / "lineage.jsonl").read_text()


def test_frozen_complete_evidence_rejects_missing_required_capture(tmp_path):
    run, metadata = _run(tmp_path)
    result = RunLifecycleService.freeze(run, metadata, bundle=EvidenceBundle(bundle_id="e", run_id=metadata.run_id, completeness="complete", steps=[RunnerStepResult(step_id="one", status="passed")]))
    with pytest.raises(ValidationError, match="evidence"):
        RunExecutionResult.model_validate({**result.model_dump(), "evidence": {"status": "complete", "required_missing": [{"kind": "screenshot"}]}})


def test_skipped_recording_cannot_write_through_manifest_symlink(tmp_path):
    from test_strict_case_recording import _recordable_web_run

    run, task, result, settings = _recordable_web_run(tmp_path, status="failed")
    outside = tmp_path / "outside.json"
    outside.write_text("unchanged")
    (run / "recording.json").symlink_to(outside)
    with pytest.raises(ValueError, match="contain"):
        RecordingService().record(run_dir=run, task=task, result=result, settings=settings)
    assert outside.read_text() == "unchanged"


def test_recording_atomic_failure_preserves_previous_manifest(tmp_path, monkeypatch):
    from test_strict_case_recording import _recordable_web_run

    run, task, result, settings = _recordable_web_run(tmp_path, status="failed")
    manifest = run / "recording.json"
    manifest.write_text("previous")
    original = Path.replace

    def fail(self, target):
        if Path(target) == manifest:
            raise OSError("atomic install failed")
        return original(self, target)

    monkeypatch.setattr(Path, "replace", fail)
    with pytest.raises(OSError, match="atomic install failed"):
        RecordingService().record(run_dir=run, task=task, result=result, settings=settings)
    assert manifest.read_text() == "previous"
    assert not list(run.glob(".recording-*.tmp"))


def test_coordinate_replay_enrichment_follows_parameter_model_not_action_name():
    from test_step_runner import ScreenSizeHarness

    from fsq_agent.core import CapabilityRegistry, StepRunner
    from fsq_agent.models import AndroidTapAtParams, CapabilityDefinition, ExecutableStep

    registry = CapabilityRegistry.from_definitions([CapabilityDefinition(name="renamed_coordinate_action", executor_kind="driver", params_model=AndroidTapAtParams)])
    result = StepRunner(ScreenSizeHarness(), capability_registry=registry).run_step(
        "run", ExecutableStep(step_id="one", kind="diagnostic", action_name="renamed_coordinate_action", params={"point": {"x": 2, "y": 3}})
    )
    invoke = next(phase for phase in result.phase_reports if phase.phase == "invoke")
    assert invoke.metadata["safe_replay_params"]["reference_screen_size"] == {"width": 1080, "height": 2400}
