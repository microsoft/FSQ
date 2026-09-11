# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import json
import zipfile

import pytest
from pydantic import ValidationError

from fsq_agent.models import PublicRunReport, ReportGenerationError, RunReportExportOptions
from fsq_agent.report import RunReportService
from tests.test_report_audit import _fixture


def _walk(value):
    yield value
    if isinstance(value, dict):
        for child in value.values():
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)


def test_profile_selection_and_field_removal_cover_embedded_graph(tmp_path):
    root, facts, bundle = _fixture(tmp_path)
    other, other_facts, other_bundle = _fixture(tmp_path, "baseline")
    for directory, value in ((root, bundle), (other, other_bundle)):
        (directory / "private.txt").write_text("CANARY_ARTIFACT")
        value["artifacts"].append({"artifact_id": "private", "kind": "text", "path": "private.txt"})
    service = RunReportService()
    baseline = service.project(other, other_facts, other_bundle)
    report = service.project(root, facts, bundle, baseline=baseline)
    report.execution["first_failure"] = {"run_id": root.name, "step_execution_id": "s1", "error_message": "CANARY_ERROR"}
    report.steps[0]["error_message"] = "CANARY_ERROR"
    output = service.export(
        report, RunReportExportOptions(format="bundle", destination=tmp_path / "shared.zip", share_profile={"artifacts": [], "remove_fields": ["artifacts.content", "steps.error_message"]})
    )
    with zipfile.ZipFile(output.path) as archive:
        for name in archive.namelist():
            data = archive.read(name)
            assert b"CANARY_ARTIFACT" not in data
            assert b"CANARY_ERROR" not in data
        payload = json.loads(archive.read("report.json"))
        assert payload["comparison"]["baseline_report"]["run"]["gate"]["status"] == "passed"


@pytest.mark.parametrize("identity", [None, 2, "", "../bad"])
def test_public_report_cannot_validate_missing_identity_and_outcome(identity):
    with pytest.raises(ValidationError):
        PublicRunReport(run={"run_id": identity, "gate": {"status": "passed", "reasons": []}})


def test_export_revalidates_mutated_passing_report(tmp_path):
    root, facts, bundle = _fixture(tmp_path)
    report = RunReportService().project(root, facts, bundle)
    report.execution.clear()
    with pytest.raises(ReportGenerationError):
        RunReportService().export(report, RunReportExportOptions(format="junit", destination=tmp_path / "fabricated.xml"))


@pytest.mark.parametrize("state", ["cancelled", "incomplete", "skipped"])
def test_historical_unfinished_steps_cannot_pass(tmp_path, state):
    root, facts, bundle = _fixture(tmp_path)
    (root / "execution-result.json").unlink()
    facts["schema_version"] = "fsq.run/v1"
    (root / "run.json").write_text(json.dumps(facts))
    bundle["schema_version"] = "1.0"
    bundle["steps"][0].update(status=state, action_status=state, step_execution_id=None)
    report = RunReportService().project(root, facts, bundle)
    assert report.run["gate"]["status"] != "passed"


def test_unknown_evidence_version_is_rejected(tmp_path):
    root, facts, bundle = _fixture(tmp_path)
    bundle["schema_version"] = "fsq.evidence/v999"
    with pytest.raises(ReportGenerationError):
        RunReportService().project(root, facts, bundle)


def test_all_historical_reports_contradictions_are_retained(tmp_path):
    root = tmp_path / "legacy"
    root.mkdir()
    (root / "report.json").write_text('{"verification":{"status":"success"}}')
    (root / "core-report.json").write_text('{"summary":{"status":"failed","step_count":1,"failed_steps":1}}')
    report = RunReportService().project(root)
    assert any("conflict" in warning.lower() for warning in report.warnings)
    assert report.run["gate"]["status"] != "passed"
    assert set(report.execution["source_outcomes"]) == {"report.json", "core-report.json"}


def test_existing_normalized_step_is_enriched_with_sdk_link(tmp_path):
    root, facts, bundle = _fixture(tmp_path)
    (root / "events.jsonl").write_text(json.dumps({"type": "tool_call_completed", "tool_call_id": "call-42", "payload": {"runner_result": bundle["steps"][0]}}))
    step = RunReportService().project(root, facts, bundle).steps[0]
    assert step["tool_call_id"] == "call-42"
    assert step["transport_status"] == "completed"


def test_pathless_capture_failure_and_redaction_coverage_survive(tmp_path):
    root, facts, bundle = _fixture(tmp_path)
    bundle["artifacts"] = [
        {
            "artifact_id": "failure",
            "kind": "screenshot",
            "path": None,
            "availability": "failed",
            "unavailable_reason": "capture_timeout",
            "metadata": {"redacted": True, "coverage": {"status": "partial"}, "compaction": {"text_limit": 50}},
        }
    ]
    artifact = RunReportService().project(root, facts, bundle).artifacts[0]
    assert artifact["availability"] == "failed"
    assert artifact["unavailable_reason"] == "capture_timeout"
    assert artifact["redacted"] is True
    assert artifact["compaction"]["text_limit"] == 50


def test_partial_snapshot_never_compares_unchanged(tmp_path):
    root, facts, bundle = _fixture(tmp_path)
    for name, phase in (("before.txt", "prepare"), ("after.txt", "finalize")):
        (root / name).write_text("same")
        bundle["artifacts"].append({"artifact_id": name, "kind": "ui_snapshot", "path": name, "phase": phase, "step_execution_id": "s1", "metadata": {"coverage": {"status": "partial"}}})
    assert RunReportService().project(root, facts, bundle).comparison["before_after"][0]["status"] == "incomplete"


def test_historical_step_wall_zero_is_unmeasured(tmp_path):
    root, facts, bundle = _fixture(tmp_path)
    bundle["schema_version"] = "1.0"
    bundle["steps"][0].update(duration_ms=0, metadata={})
    report = RunReportService().project(root, facts, bundle)
    assert report.steps[0]["metrics"]["duration_ms"]["value"] is None


def test_durable_log_payload_message_has_downloadable_journal_reference(tmp_path):
    root, facts, bundle = _fixture(tmp_path)
    (root / "evidence-events.jsonl").write_text("journal fixture")
    bundle["events"] = [{"run_id": root.name, "event_type": "step_error", "step_id": "s1", "payload": {"message": "x" * 5000}}]
    report = RunReportService().project(root, facts, bundle)
    log = report.logs[0]
    assert log["message"]
    reference = log["complete_artifact"]
    artifact = next(item for item in report.artifacts if item["artifact_id"] == reference["artifact_id"])
    assert artifact["path"] == "evidence-events.jsonl"


def test_interpretations_preserve_distinct_attribution_and_planned_anchor(tmp_path):
    root, facts, bundle = _fixture(tmp_path)
    (root / "execution-result.json").unlink()
    facts["schema_version"] = "fsq.run/v1"
    facts["status"] = "failed"
    (root / "run.json").write_text(json.dumps(facts))
    (root / "report.json").write_text('{"failure_classification":"tool_usage_error","verification":{"status":"failed"}}')
    (root / "case-suggestions.json").write_text('{"analysis_summary":"Try a stable selector","suggestions":["Use role"]}')
    bundle["steps"][0].update(status="failed", action_status="failed", error_message="Failure")
    bundle["steps"].append({"step_id": "plan2", "step_execution_id": None, "status": "skipped", "source_step_id": "case:1"})
    report = RunReportService().project(root, facts, bundle)
    interpretations = report.execution["interpretations"]
    assert {value["kind"] for value in interpretations} >= {"recorded_fact", "deterministic_classification", "ai_suggestion"}
    assert all(value["references"] for value in interpretations if value["availability"] == "available")
    output = RunReportService().export(report, RunReportExportOptions(format="html", destination=tmp_path / "planned.html"))
    assert 'data-step-link="step-plan2"' in output.path.read_text()


def test_nested_export_graph_transforms_once_and_preserves_machine_outcomes(tmp_path):
    root, facts, bundle = _fixture(tmp_path)
    other, other_facts, other_bundle = _fixture(tmp_path, "baseline")
    (other / "notes.txt").write_text("seed")
    other_bundle["artifacts"].append({"artifact_id": "notes", "kind": "text", "path": "notes.txt"})
    service = RunReportService()
    report = service.project(root, facts, bundle, baseline=service.project(other, other_facts, other_bundle))
    output = service.export(report, RunReportExportOptions(format="bundle", destination=tmp_path / "once.zip", share_profile={"replacements": [{"text": "seed", "replacement": "Xseed"}]}))
    with zipfile.ZipFile(output.path) as archive:
        payload = json.loads(archive.read("report.json"))
        nested = payload["comparison"]["baseline_report"]
        artifact = next(item for item in nested["artifacts"] if item["artifact_id"] == "notes")
        assert artifact["content"] == "Xseed"
        assert archive.read("runs/baseline/notes.txt") == b"Xseed"
        assert nested["execution"]["outcome"] == "success"
        assert nested["run"]["gate"]["status"] == "passed"


def test_nested_public_report_is_revalidated_for_export(tmp_path):
    root, facts, bundle = _fixture(tmp_path)
    other, other_facts, other_bundle = _fixture(tmp_path, "baseline")
    service = RunReportService()
    report = service.project(root, facts, bundle, baseline=service.project(other, other_facts, other_bundle))
    report.comparison["baseline_report"]["execution"] = {}
    with pytest.raises(ReportGenerationError):
        service.export(report, RunReportExportOptions(format="json", destination=tmp_path / "invalid.json"))
