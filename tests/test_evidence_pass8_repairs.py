# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import json
import subprocess

import pytest

from fsq_agent._capability_bootstrap import build_capability_registry
from fsq_agent.adapters.control_plane._evidence import read_step_artifacts
from fsq_agent.case_dsl import FsqCaseLoader
from fsq_agent.config import Settings
from fsq_agent.core import EvidenceRecorder, StepRunner
from fsq_agent.execution import RunLifecycleService, load_run_metadata
from fsq_agent.execution.lifecycle import run_strict_lifecycle_case
from fsq_agent.models import ExecutableStep, ReportGenerationError, RunnerEvent, RunnerStepResult
from fsq_agent.report import RunReportService
from tests.test_durable_evidence import JournalHarness
from tests.test_report_audit import _fixture, _write_frozen
from tests.test_report_round_seven import _mapped_reports
from tests.test_strict_lifecycle_service import LifecycleHarness


@pytest.mark.parametrize("kind", ["phase_finish", "step_finish"])
def test_recovery_rejects_contradictory_durable_completion(tmp_path, kind):
    recorder = EvidenceRecorder(run_id="run", output_dir=tmp_path)
    StepRunner(JournalHarness(tmp_path), capability_registry=build_capability_registry(platform="android"), evidence_sink=recorder).run_step(
        "run", ExecutableStep(step_id="tap", kind="action", action_name="tapOn", params={"target": "Button"})
    )
    journal = tmp_path / "evidence-events.jsonl"
    records = [json.loads(line) for line in journal.read_text().splitlines()]
    for record in records:
        event = record.get("event") or {}
        if event.get("event_type") == kind and (kind == "step_finish" or event.get("phase") == "invoke"):
            value = event["payload"]["phase_report"] if kind == "phase_finish" else event["payload"]
            value.update(status="failed", failure_category="assertion_error")
    (tmp_path / "evidence-manifest.json").unlink()
    journal.write_text("".join(json.dumps(record) + chr(10) for record in records))
    with pytest.raises(ValueError, match=r"contradict|conflict"):
        EvidenceRecorder.recover_bundle(tmp_path)


def test_frozen_and_recovered_execution_identity_must_agree(tmp_path):
    run, facts, _ = _fixture(tmp_path)
    recorder = EvidenceRecorder(run_id=run.name, output_dir=run)
    recorder.record_event(RunnerEvent(run_id=run.name, event_type="step_start", step_id="wrong", step_execution_id="wrong", source_step_id="other"))
    recorder.record_step_result(RunnerStepResult(step_id="wrong", step_execution_id="wrong", source_step_id="other", status="passed", action_status="passed"))
    report = RunReportService().project(run, facts, EvidenceRecorder.recover_bundle(run))
    assert report.run["gate"]["status"] != "passed"
    assert report.execution["accounting_complete"] is False
    assert any("Frozen" in warning and "conflict" in warning for warning in report.warnings)


def test_secondary_infrastructure_failure_has_gate_precedence(tmp_path):
    run, facts, bundle = _fixture(tmp_path, status="failed")
    first = bundle["steps"][0]
    first.update(status="failed", action_status="failed", failure_category="assertion_error")
    bundle["steps"].append({**first, "step_id": "cleanup", "step_execution_id": "cleanup", "source_step_id": "cleanup", "failure_category": "harness_error"})
    _write_frozen(run, outcome="failed", counts={"total": 2, "passed": 0, "failed": 2, "attempt_count": 2}, steps=bundle["steps"])
    report = RunReportService().project(run, facts, bundle)
    assert report.run["gate"]["status"] == "error"
    assert report.execution["first_failure"]["failure_category"] == "assertion_error"


def test_recording_current_binding_cannot_change_command_index(tmp_path):
    service, report, baseline = _mapped_reports(tmp_path)
    report.steps[0]["source_index"] = 1
    with pytest.raises(ReportGenerationError, match=r"command|mapping"):
        service.compare(report, baseline)


@pytest.mark.parametrize("payload", [{"verification": "invalid"}, {"summary": {"status": []}}, {"execution": []}, {"steps": ["bad"]}])
def test_invalid_historical_sections_have_safe_report_error(tmp_path, payload):
    run, facts, bundle = _fixture(tmp_path)
    (run / "report.json").write_text(json.dumps(payload))
    with pytest.raises(ReportGenerationError) as error:
        RunReportService().project(run, facts, bundle)
    assert error.value.context["reason"] == "schema_invalid"


def test_case_loader_preserves_original_line_endings(tmp_path):
    path = tmp_path / "case.fsq.yaml"
    original = b"schemaVersion: fsq.ai-test/v1\r\nname: Test\r\nplatform: android\r\n---\r\n- launchApp: {}\r\n"
    path.write_bytes(original)
    assert RunLifecycleService.case_source_bytes(FsqCaseLoader().load_case(path)) == original


def test_config_hook_content_is_retained_and_changes_source_identity(tmp_path, monkeypatch):
    path = tmp_path / "case.fsq.yaml"
    path.write_text("schemaVersion: fsq.ai-test/v1\nname: Test\nplatform: android\n---\n- launchApp: {}\n")
    case = FsqCaseLoader().load_case(path)
    registry = build_capability_registry(platform="android")
    monkeypatch.setattr("fsq_agent.execution.lifecycle._run_shell_command", lambda cmd: subprocess.CompletedProcess(cmd, 0, "", ""))
    identities = []
    for index, command in enumerate(("echo first", "echo second")):
        settings = Settings(cases={"dir": tmp_path}, case_lifecycle={"onCaseStart": [{"runShell": command}]})
        directory = tmp_path / f"run-{index}"
        run_strict_lifecycle_case(
            case_path=path,
            case=case,
            settings=settings,
            harness=LifecycleHarness(),
            output_dir=directory,
            run_id=directory.name,
            registry=registry,
            registry_snapshot=registry.snapshot(),
            resolve_steps=lambda steps, case: steps,
        )
        metadata = load_run_metadata(directory)
        retained = [(directory / value["path"]).read_text() for value in metadata.provenance["sources"]]
        assert any(command in content for content in retained)
        identities.append(next(step.source_step_id for step in EvidenceRecorder.recover_bundle(directory).steps if step.action_name == "runShell"))
    assert identities[0] != identities[1]


@pytest.mark.parametrize("content", ["{invalid", "[]"])
def test_terminal_invalid_evidence_is_not_reported_as_no_capture(tmp_path, content):
    (tmp_path / "evidence-manifest.json").write_text(content)
    with pytest.raises(ValueError, match=r"invalid|Expecting"):
        read_step_artifacts(tmp_path, "step-one")


def test_terminal_large_valid_manifest_is_not_silently_discarded(tmp_path):
    (tmp_path / "evidence-manifest.json").write_text(
        json.dumps(
            {
                "padding": "x" * (8 * 1024 * 1024),
                "artifacts": [{"artifact_id": "capture", "kind": "ui_snapshot", "step_id": "step-one", "availability": "failed", "unavailable_reason": "capture_failed"}],
            }
        )
    )
    assert read_step_artifacts(tmp_path, "step-one")["artifacts"]
