# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import hashlib
import json
import xml.etree.ElementTree as ET
import zipfile

import pytest
from PIL import Image
from pydantic import ValidationError

from fsq_agent.models import PublicRunReport, ReportGenerationError, RunReportExportOptions
from fsq_agent.report import RunReportService


def _run(root, run_id="run-1", *, status="success", mode="strict"):
    run = root / run_id
    run.mkdir()
    facts = {
        "schema_version": "fsq.run/v1",
        "run_id": run_id,
        "platform": "web",
        "workspace": {"name": "test"},
        "started_at": None,
        "mode": mode,
        "status": status,
        "source": {"kind": "case", "case_id": "search", "snapshot_path": "case.yaml", "digest": hashlib.sha256(b"name: search").hexdigest()},
        "result": {"summary": "Done"},
    }
    (run / "case.yaml").write_bytes(b"name: search")
    (run / "run.json").write_text(json.dumps(facts))
    return run, facts


def _bundle(run_id, *, status="passed", artifacts=()):
    return {
        "schema_version": "1.0",
        "run_id": run_id,
        "steps": [
            {
                "step_id": "s1",
                "source_ref": {"source_type": "case", "source_id": "search", "step_index": 0},
                "status": status,
                "duration_ms": 25,
                "phase_reports": [
                    {"phase": "prepare", "status": "passed", "duration_ms": 0},
                    {"phase": "invoke", "status": status, "metadata": {"capability_name": "click_on", "step_kind": "action"}},
                ],
            }
        ],
        "artifacts": list(artifacts),
    }


def test_public_report_historical_dynamic_reconstructs_actual_actions_and_logs(tmp_path):
    run, facts = _run(tmp_path, status="failed", mode="explore")
    runner = _bundle(run.name, status="failed")["steps"][0]
    runner.update(failure_category="target_resolution_error", error_message="Missing button")
    event = {"type": "tool_call_completed", "sequence": 2, "timestamp": "2026-09-08T00:00:00Z", "tool_name": "click_on", "title": "Click", "payload": {"status": "failed", "runner_result": runner}}
    (run / "events.jsonl").write_text(json.dumps(event) + chr(10))
    (run / "report.json").write_text(json.dumps({"execution": {"runtime_steps": [{"source": "pre_plan", "duration_ms": 9999}]}, "verification": {"status": "failed"}}))
    report = RunReportService().project(run, facts)
    assert report.steps[0]["status"] == "failed"
    assert report.steps[0]["transport_status"] == "completed"
    assert report.steps[0]["metrics"]["prepare_ms"]["value"] is None
    assert report.logs[0]["time"] == event["timestamp"]
    assert report.logs[0]["label"] == "Click"
    assert report.execution["counts"]["total"] == 1
    assert report.run["gate"]["status"] == "failed"


def test_public_report_preserves_primary_failure_and_evidence_error(tmp_path):
    run, facts = _run(tmp_path, status="failed")
    bundle = _bundle(run.name, status="failed")
    bundle["steps"][0].update(failure_category="assertion_failed", error_message="Expected saved", evidence_errors=[{"kind": "screenshot", "message": "Capture failed"}])
    bundle["steps"][0]["metadata"] = {"required_evidence": [{"kind": "screenshot", "phase": "prepare"}]}
    report = RunReportService().project(run, facts, bundle)
    assert report.execution["first_failure"]["error_message"] == "Expected saved"
    assert report.evidence["status"] == "partial"
    assert report.run["gate"]["status"] == "error"


def test_public_report_frozen_success_survives_processing_interruption(tmp_path):
    run, facts = _run(tmp_path)
    facts.update(schema_version="fsq.run/v2", status="interrupted", processing={"report": {"status": "failed"}})
    (run / "suggestion-processing.json").write_text(json.dumps({"report": {"status": "failed"}}))
    (run / "execution-result.json").write_text(
        json.dumps(
            {
                "schema_version": "fsq.execution-result/v1",
                "run_id": run.name,
                "platform": "web",
                "mode": "strict",
                "outcome": "success",
                "summary": "Done",
                "counts": {"total": 1, "passed": 1, "attempt_count": 1},
                "verification": {"status": "not_requested"},
                "evidence": {"status": "not_applicable"},
            }
        )
    )
    report = RunReportService().project(run, facts, _bundle(run.name))
    assert report.execution["outcome"] == "success"
    assert report.run["gate"]["status"] == "passed"
    assert report.processing["report"]["status"] == "failed"


def test_public_report_compares_only_stable_source_identities(tmp_path):
    service = RunReportService()
    first, first_facts = _run(tmp_path, "before")
    second, second_facts = _run(tmp_path, "after")
    before = service.project(first, first_facts, _bundle(first.name))
    after = service.project(second, second_facts, _bundle(second.name))
    assert service.compare(after, before)["status"] == "comparable"
    second_facts["source"]["digest"] = "b" * 64
    (second / "run.json").write_text(json.dumps(second_facts))
    different = service.project(second, second_facts, _bundle(second.name))
    with pytest.raises(ReportGenerationError, match="comparable"):
        service.compare(different, before)


def test_exports_share_verdict_standalone_media_and_bundle_checksums(tmp_path):
    run, facts = _run(tmp_path, status="failed")
    Image.new("RGB", (5, 5), "white").save(run / "before.png")
    (run / "before.txt").write_text("Button: Save")
    (run / "after.txt").write_text("Button: Saved")
    artifacts = [
        {"artifact_id": name, "kind": kind, "path": name, "step_id": "s1", "phase": phase, "metadata": {"coverage": {"status": "complete"}}}
        for name, kind, phase in [("before.png", "screenshot", "prepare"), ("before.txt", "ui_snapshot", "prepare"), ("after.txt", "ui_snapshot", "finalize")]
    ]
    service = RunReportService()
    report = service.project(run, facts, _bundle(run.name, status="failed", artifacts=artifacts))
    assert report.comparison["before_after"][0]["status"] == "changed"
    for fmt, name in [("json", "out.json"), ("junit", "junit.xml"), ("html", "out.html"), ("bundle", "out.zip")]:
        result = service.export(report, RunReportExportOptions(format=fmt, destination=tmp_path / name, export_id=fmt, run_dirs={run.name: run}))
        assert result.path.is_file()
    junit = ET.parse(tmp_path / "junit.xml").getroot()  # noqa: S314 - generated local test output.
    assert len(junit.findall(".//testcase")) == 1
    assert len(junit.findall(".//failure")) == 1
    html = (tmp_path / "out.html").read_text()
    assert "data:image/png;base64," in html
    assert "Button: Saved" in html
    assert "Content-Security-Policy" in html
    with zipfile.ZipFile(tmp_path / "out.zip") as archive:
        assert "runs/run-1/before.png" in archive.namelist()
        index = json.loads(archive.read("index.json"))
        for entry in index["files"]:
            assert hashlib.sha256(archive.read(entry["path"])).hexdigest() == entry["sha256"]


def test_export_rejects_overwrite_changed_source_and_symlink(tmp_path):
    run, facts = _run(tmp_path)
    service = RunReportService()
    report = service.project(run, facts, _bundle(run.name))
    destination = tmp_path / "out.json"
    destination.write_text("keep")
    with pytest.raises(ReportGenerationError):
        service.export(report, RunReportExportOptions(format="json", destination=destination))
    assert destination.read_text() == "keep"
    (run / "run.json").write_text("{}")
    with pytest.raises(ReportGenerationError) as error:
        service.export(report, RunReportExportOptions(format="json", destination=tmp_path / "fresh.json"))
    assert error.value.context["reason"] == "source_identity_unavailable"
    (tmp_path / "link").symlink_to(run, target_is_directory=True)
    with pytest.raises(ReportGenerationError):
        service.project(tmp_path / "link", facts)


def test_profile_cannot_change_verdict_and_masks_only_export_copy(tmp_path):
    run, facts = _run(tmp_path)
    Image.new("RGB", (5, 5), "white").save(run / "shot.png")
    original = (run / "shot.png").read_bytes()
    service = RunReportService()
    report = service.project(run, facts, _bundle(run.name, artifacts=[{"artifact_id": "shot", "kind": "screenshot", "path": "shot.png", "step_id": "s1"}]))
    with pytest.raises(ValidationError):
        RunReportExportOptions(format="json", destination=tmp_path / "bad", share_profile={"remove_fields": ["run.gate"]})
    options = RunReportExportOptions(
        format="bundle", destination=tmp_path / "shared.zip", share_profile={"masks": [{"run_id": run.name, "artifact_id": "shot", "x": 0, "y": 0, "width": 5, "height": 5}]}
    )
    service.export(report, options)
    assert (run / "shot.png").read_bytes() == original
    with zipfile.ZipFile(options.destination) as archive:
        assert archive.read("runs/run-1/shot.png") != original


def test_public_contract_rejects_paths_and_non_json_fields():
    with pytest.raises(ValidationError):
        PublicRunReport(run={"run_id": "../escape", "gate": {"status": "passed", "reasons": []}})


def test_corrupt_current_freeze_never_falls_back_to_success(tmp_path):
    run, facts = _run(tmp_path)
    facts["schema_version"] = "fsq.run/v2"
    (run / "execution-result.json").write_text("invalid-json")
    with pytest.raises(ReportGenerationError, match="execution"):
        RunReportService().project(run, facts, _bundle(run.name))


def test_success_without_execution_or_verification_is_incomplete(tmp_path):
    run, facts = _run(tmp_path)
    (run / "core-report.json").write_text('{"summary":{"status":"passed","step_count":0}}')
    report = RunReportService().project(run, facts)
    assert report.run["gate"]["status"] == "incomplete"


def test_unknown_snapshot_coverage_is_not_unchanged(tmp_path):
    run, facts = _run(tmp_path)
    for name in ("before.txt", "after.txt"):
        (run / name).write_text("same")
    artifacts = [
        {"artifact_id": name, "kind": "ui_snapshot", "path": name, "step_id": "s1", "phase": phase, "metadata": {"truncated": True}}
        for name, phase in (("before.txt", "prepare"), ("after.txt", "finalize"))
    ]
    report = RunReportService().project(run, facts, _bundle(run.name, artifacts=artifacts))
    assert report.comparison["before_after"][0]["status"] == "incomplete"


def test_current_source_snapshot_is_included_in_bundle(tmp_path):
    run, facts = _run(tmp_path)
    (run / "source.yaml").write_text("name: search")
    facts["source"].update(snapshot_path="source.yaml", digest=hashlib.sha256((run / "source.yaml").read_bytes()).hexdigest())
    (run / "run.json").write_text(json.dumps(facts))
    report = RunReportService().project(run, facts, _bundle(run.name))
    result = RunReportService().export(report, RunReportExportOptions(format="bundle", destination=tmp_path / "source.zip"))
    with zipfile.ZipFile(result.path) as archive:
        assert archive.read("runs/run-1/source.yaml") == b"name: search"


def test_profile_removing_snapshot_content_removes_export_file_content(tmp_path):
    run, facts = _run(tmp_path)
    (run / "snapshot.txt").write_text("private contact name")
    report = RunReportService().project(run, facts, _bundle(run.name, artifacts=[{"artifact_id": "ui", "path": "snapshot.txt", "kind": "ui_snapshot", "step_id": "s1"}]))
    output = RunReportService().export(report, RunReportExportOptions(format="bundle", destination=tmp_path / "private.zip", share_profile={"remove_fields": ["artifacts.content"]}))
    with zipfile.ZipFile(output.path) as archive:
        assert b"private contact name" not in archive.read("runs/run-1/snapshot.txt")


def test_old_zero_measurement_is_unavailable_but_current_zero_is_measured(tmp_path):
    run, facts = _run(tmp_path)
    bundle = _bundle(run.name)
    bundle["steps"][0]["phase_reports"][0]["metadata"] = {"timing_measured": True}
    report = RunReportService().project(run, facts, bundle)
    assert report.steps[0]["metrics"]["prepare_ms"]["value"] == 0


def test_strict_core_events_are_projected_from_normalized_bundle(tmp_path):
    run, facts = _run(tmp_path)
    bundle = _bundle(run.name)
    bundle["events"] = [{"event_type": "step_finish", "timestamp": "2026-09-08T00:00:00Z", "step_id": "s1", "payload": {"status": "passed"}}]
    report = RunReportService().project(run, facts, bundle)
    assert report.logs[0]["event_kind"] == "step_finish"
    assert report.logs[0]["step_execution_id"] == "s1"


def test_related_run_requires_actual_other_run_and_candidate_digest(tmp_path):
    service = RunReportService()
    first, facts = _run(tmp_path, "origin", mode="explore")
    facts["artifacts"] = {"candidate_case": "case.yaml"}
    (first / "run.json").write_text(json.dumps(facts))
    second, other_facts = _run(tmp_path, "replay")
    other_facts["lineage"] = [{"kind": "replay", "originating_run_id": "origin", "case_digest": hashlib.sha256(b"name: search").hexdigest()}]
    (second / "run.json").write_text(json.dumps(other_facts))
    replay = service.project(second, other_facts, _bundle("replay"))
    combined = service.project(first, facts, _bundle("origin"), related_runs=[replay])
    assert combined.lineage["related_runs"][0]["run"]["run_id"] == "replay"
    other_facts["lineage"][0]["originating_run_id"] = "replay"
    (second / "run.json").write_text(json.dumps(other_facts))
    invalid = service.project(second, other_facts, _bundle("replay"))
    with pytest.raises(ReportGenerationError, match="lineage"):
        service.project(first, facts, _bundle("origin"), related_runs=[invalid])


def test_incomplete_required_accounting_never_passes_gate(tmp_path):
    run, facts = _run(tmp_path)
    facts["schema_version"] = "fsq.run/v2"
    (run / "execution-result.json").write_text(
        json.dumps(
            {
                "schema_version": "fsq.execution-result/v1",
                "run_id": run.name,
                "platform": "web",
                "mode": "strict",
                "summary": "Done",
                "evidence": {"status": "complete"},
                "outcome": "success",
                "counts": {"total": 2, "passed": 2, "failed": 0, "skipped": 0, "cancelled": 0, "incomplete": 0, "attempt_count": 2},
                "verification": {"status": "not_requested"},
            }
        )
    )
    report = RunReportService().project(run, facts, _bundle(run.name))
    assert report.run["gate"]["status"] != "passed"


def test_share_replacement_does_not_rewrite_raw_machine_artifact(tmp_path):
    run, facts = _run(tmp_path)
    raw = '{"outcome":"failed","summary":"failed request"}'
    (run / "result.json").write_text(raw)
    report = RunReportService().project(run, facts, _bundle(run.name, artifacts=[{"artifact_id": "raw", "path": "result.json", "kind": "json"}]))
    output = RunReportService().export(
        report, RunReportExportOptions(format="bundle", destination=tmp_path / "machine.zip", share_profile={"replacements": [{"text": "failed", "replacement": "passed"}]})
    )
    with zipfile.ZipFile(output.path) as archive:
        assert json.loads(archive.read("runs/run-1/result.json"))["outcome"] == "failed"


def test_duplicate_step_ids_are_preserved_and_block_success(tmp_path):
    run, facts = _run(tmp_path)
    bundle = _bundle(run.name)
    bundle["steps"].append({**bundle["steps"][0], "status": "failed", "error_message": "Second invocation"})
    report = RunReportService().project(run, facts, bundle)
    assert len(report.steps) == 2
    assert any("identity" in warning for warning in report.warnings)
    assert report.run["gate"]["status"] != "passed"


def test_profile_transformation_clears_original_baseline_diff_text(tmp_path):
    run, facts = _run(tmp_path)
    report = RunReportService().project(run, facts, _bundle(run.name))
    report.comparison["baseline_current"] = {"status": "comparable", "steps": [{"snapshot": {"rows": [{"before": "private value", "after": "private value"}]}}]}
    output = RunReportService().export(
        report, RunReportExportOptions(format="json", destination=tmp_path / "shared.json", share_profile={"replacements": [{"text": "private", "replacement": "hidden"}]})
    )
    assert "private value" not in output.path.read_text()


def test_review_declaration_rejects_omitted_case(tmp_path):
    run, facts = _run(tmp_path)
    (run / "case.yaml").write_text("name: case")
    digest = hashlib.sha256((run / "case.yaml").read_bytes()).hexdigest()
    report = RunReportService().project(run, facts, _bundle(run.name, artifacts=[{"artifact_id": "case", "path": "case.yaml", "kind": "text"}]))
    with pytest.raises(ReportGenerationError) as error:
        RunReportService().export(
            report,
            RunReportExportOptions(
                format="json",
                destination=tmp_path / "declaration.json",
                share_profile={"artifacts": [], "case_review_declaration": {"run_id": run.name, "artifact_id": "case", "sha256": digest, "attribution": "User"}},
            ),
        )
    assert error.value.context["reason"] == "source_identity_unavailable"


def test_export_does_not_reintroduce_redacted_snapshot_values(tmp_path):
    run, facts = _run(tmp_path)
    (run / "ui.txt").write_text("password=hidden-credential")
    report = RunReportService().project(run, facts, _bundle(run.name, artifacts=[{"artifact_id": "ui", "path": "ui.txt", "kind": "ui_snapshot", "step_id": "s1"}]))
    output = RunReportService().export(report, RunReportExportOptions(format="bundle", destination=tmp_path / "redacted.zip"))
    with zipfile.ZipFile(output.path) as archive:
        assert all(b"hidden-credential" not in archive.read(name) for name in archive.namelist())


def test_dynamic_runtime_fallback_is_not_an_action_and_recovered_failure_is_not_blocking(tmp_path):
    run, facts = _run(tmp_path, mode="explore")
    raw = _bundle(run.name, status="failed")["steps"][0]
    (run / "events.jsonl").write_text(json.dumps({"type": "tool_call_completed", "payload": {"runner_result": raw}}) + chr(10))
    (run / "report.json").write_text(json.dumps({"steps": [{"step_id": 1, "status": "success", "tool_name": "pre_plan", "duration_ms": 1000}], "verification": {"status": "success"}}))
    report = RunReportService().project(run, facts)
    assert len(report.steps) == 1
    assert report.execution["first_failure"] is None
    assert len(report.execution["recovered_failures"]) == 1
    assert report.run["gate"]["status"] == "incomplete"


def test_not_applicable_capture_is_accounted_using_requested_kind(tmp_path):
    run, facts = _run(tmp_path)
    (run / "sentinel.json").write_text('{"reason":"browser_not_started"}')
    bundle = _bundle(
        run.name,
        artifacts=[
            {
                "artifact_id": "sentinel",
                "kind": "json",
                "path": "sentinel.json",
                "step_id": "s1",
                "phase": "prepare",
                "availability": "not_applicable",
                "unavailable_reason": "browser_not_started",
                "metadata": {"requested_kind": "screenshot"},
            }
        ],
    )
    bundle["steps"][0]["metadata"] = {"required_evidence": [{"kind": "screenshot", "phase": "prepare"}]}
    report = RunReportService().project(run, facts, bundle)
    assert report.evidence["required_missing"] == []
    assert report.evidence["status"] == "complete"
    assert report.artifacts[0]["requested_kind"] == "screenshot"
    assert report.run["gate"]["status"] == "passed"


def test_assertion_expected_observed_and_baseline_are_visually_rendered(tmp_path):
    run, facts = _run(tmp_path, status="failed")
    baseline_dir, baseline_facts = _run(tmp_path, "baseline")
    bundle = _bundle(run.name, status="failed")
    bundle["steps"][0]["phase_reports"][1]["metadata"].update(safe_replay_params={"text": {"equals": "Added to cart"}}, harness_output={"text": "Unable to add item"})
    service = RunReportService()
    baseline = service.project(baseline_dir, baseline_facts, _bundle(baseline_dir.name))
    report = service.project(run, facts, bundle, baseline=baseline)
    assert report.steps[0]["assertion_details"]["expected"] == {"equals": "Added to cart"}
    assert report.steps[0]["assertion_details"]["observed"] == "Unable to add item"
    output = service.export(report, RunReportExportOptions(format="html", destination=tmp_path / "readable.html"))
    content = output.path.read_text()
    assert "Run outcomes" in content
    assert "Baseline vs current" in content
    assert "Expected" in content
    assert "Observed" in content
    assert 'id="step-s1" open' in content


def test_wrapped_web_snapshot_normalization_preserves_metadata_and_lines(tmp_path):
    run, facts = _run(tmp_path)
    (run / "ui.json").write_text(json.dumps({"snapshot_type": "aria", "url": "http://127.0.0.1:8848/", "snapshot": "- button Save\n- status Saved"}))
    report = RunReportService().project(run, facts, _bundle(run.name, artifacts=[{"artifact_id": "ui", "path": "ui.json", "kind": "ui_snapshot", "step_id": "s1"}]))
    normalized = report.artifacts[0]["normalized_content"]
    assert "http://127.0.0.1:8848/" in normalized
    assert "aria" in normalized
    assert "- button Save\n- status Saved" in normalized
