# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import hashlib
import json
import xml.etree.ElementTree as ET
import zipfile
from datetime import UTC, datetime

import pytest

from fsq_agent.models import PublicRunReport, ReportGenerationError, RunExecutionResult, RunMetadata, RunReportExportOptions, RunSource, RunStepCounts
from fsq_agent.report import CoreEvidenceReportGenerator, RunReportService


def _fixture(tmp_path, name="run", *, mode="strict", status="success"):
    root = tmp_path / name
    root.mkdir()
    content = b"name: audit\n---\n- closeBrowser: {}\n"
    (root / "case.yaml").write_bytes(content)
    metadata = RunMetadata(
        run_id=name,
        workspace={"name": "audit"},
        platform="web",
        mode=mode,
        status=status,
        started_at=datetime(2026, 9, 8, tzinfo=UTC),
        source=RunSource(kind="case", case_id="audit", snapshot_path="case.yaml", digest=hashlib.sha256(content).hexdigest()),
    )
    (root / "run.json").write_text(metadata.model_dump_json())
    step = {
        "step_id": "s1",
        "step_execution_id": "s1",
        "source_step_id": "case:0",
        "source_ref": {"source_type": "case", "step_index": 0},
        "invocation_path": ["root"],
        "status": "passed",
        "action_status": "passed",
        "action_name": "click_on",
        "kind": "action",
        "duration_ms": 5,
        "metadata": {"timing_measured": True},
        "phase_reports": [],
    }
    bundle = {"schema_version": "fsq.evidence/v2", "run_id": name, "bundle_id": name, "completeness": "complete", "steps": [step], "artifacts": [], "events": []}
    frozen = RunExecutionResult(
        run_id=name,
        platform="web",
        mode=mode,
        outcome=status,
        summary="Recorded result",
        counts=RunStepCounts(total=1, passed=1, attempt_count=1),
        verification={"status": "not_requested"},
        evidence={"status": "complete"},
        steps=[step],
    )
    (root / "execution-result.json").write_text(frozen.model_dump_json())
    return root, metadata.model_dump(mode="json"), bundle


def _write_frozen(root, **changes):
    path = root / "execution-result.json"
    frozen = json.loads(path.read_text())
    frozen.update(changes)
    path.write_text(json.dumps(frozen))


@pytest.mark.parametrize("change", [{"execution": {}}, {"platform": "android"}, {"mode": "explore"}, {"verification": {"status": "fabricated"}}, {"counts": {"total": 1, "passed": 99}}])
def test_current_facts_are_canonical_and_cross_identity_checked(tmp_path, change):
    root, facts, bundle = _fixture(tmp_path)
    if "execution" in change:
        (root / "execution-result.json").write_text(json.dumps(change))
    else:
        _write_frozen(root, **change)
    with pytest.raises(ReportGenerationError) as error:
        RunReportService().project(root, facts, bundle)
    assert error.value.context["reason"] == "schema_invalid"


@pytest.mark.parametrize("status", ["error", "cancelled"])
def test_verification_errors_are_nonpassing_with_supporting_refs(tmp_path, status):
    root, facts, bundle = _fixture(tmp_path)
    _write_frozen(root, outcome=status, verification={"status": status})
    report = RunReportService().project(root, facts, bundle)
    assert report.run["gate"]["status"] == "error"
    assert report.run["gate"]["references"][0]["run_id"] == root.name


def test_recovered_partial_and_artifact_identity_collision_block_gate(tmp_path):
    root, facts, bundle = _fixture(tmp_path)
    bundle["completeness"] = "partial"
    bundle["warnings"] = ["Interrupted trailing record"]
    report = RunReportService().project(root, facts, bundle)
    assert report.evidence["status"] == "partial"
    assert report.run["gate"]["status"] == "error"
    bundle["completeness"] = "complete"
    bundle["warnings"] = []
    (root / "a.txt").write_text("a")
    (root / "b.txt").write_text("b")
    bundle["artifacts"] = [{"artifact_id": "same", "kind": "text", "path": name} for name in ("a.txt", "b.txt")]
    assert RunReportService().project(root, facts, bundle).run["gate"]["status"] != "passed"


def test_recovery_hash_boundary_detects_journal_change(tmp_path):
    root, facts, bundle = _fixture(tmp_path)
    journal = root / "evidence-events.jsonl"
    journal.write_bytes(b"one\n")
    bundle["metadata"] = {
        "recovery_snapshot": {"checkpoint_sequence": 0, "journal_sequence": 1, "files": {"evidence-events.jsonl": hashlib.sha256(journal.read_bytes()).hexdigest(), "evidence-manifest.json": None}}
    }
    report = RunReportService().project(root, facts, bundle)
    assert report.run["snapshot"]["journal_sequence"] == 1
    journal.write_bytes(b"two\n")
    with pytest.raises(ReportGenerationError) as error:
        RunReportService().export(report, RunReportExportOptions(format="json", destination=tmp_path / "out.json"))
    assert error.value.context["reason"] == "source_changed"


@pytest.mark.parametrize("report_format", ["json", "junit", "html", "bundle"])
@pytest.mark.parametrize("source_failure", ["missing", "digest_mismatch"])
def test_export_rejects_unavailable_source_identity_before_creating_destination(tmp_path, report_format, source_failure):
    root, facts, bundle = _fixture(tmp_path)
    source = root / "case.yaml"
    if source_failure == "missing":
        source.unlink()
    else:
        facts["source"]["digest"] = "0" * 64
        (root / "run.json").write_text(json.dumps(facts))
    report = RunReportService().project(root, facts, bundle)
    assert report.run["gate"] == {"status": "error", "reasons": ["source_identity_unavailable"]}
    destination = tmp_path / "not-created" / f"report.{report_format}"
    with pytest.raises(ReportGenerationError) as error:
        RunReportService().export(report, RunReportExportOptions(format=report_format, destination=destination))
    assert error.value.context["reason"] == "source_identity_unavailable"
    assert not destination.parent.exists()


@pytest.mark.parametrize("relationship", ["baseline", "related"])
def test_export_rejects_nested_unavailable_source_identity(tmp_path, relationship):
    root, facts, bundle = _fixture(tmp_path, "current")
    other_root, other_facts, other_bundle = _fixture(tmp_path, "other")
    service = RunReportService()
    report = service.project(root, facts, bundle)
    other = service.project(other_root, other_facts, other_bundle)
    other.run["gate"] = {"status": "error", "reasons": ["source_identity_unavailable"]}
    if relationship == "baseline":
        report.comparison["baseline_report"] = other.model_dump(mode="json")
    else:
        report.lineage["related_runs"] = [other.model_dump(mode="json")]
    destination = tmp_path / "nested" / "report.json"
    with pytest.raises(ReportGenerationError) as error:
        service.export(report, RunReportExportOptions(format="json", destination=destination))
    assert error.value.context["reason"] == "source_identity_unavailable"
    assert not destination.parent.exists()


@pytest.mark.parametrize(
    "mutation",
    [
        "missing_identity",
        "forged_gate",
        "artifact_identity",
        "run_directory_binding",
        "non_path_run_root",
        "persisted_metadata",
        "persisted_non_object",
        "persisted_source_list",
        "persisted_source_string",
    ],
)
def test_export_independently_validates_retained_source_identity(tmp_path, mutation):
    root, facts, bundle = _fixture(tmp_path)
    service = RunReportService()
    if mutation == "missing_identity":
        facts["source"].update(snapshot_path=None, digest=None)
        (root / "run.json").write_text(json.dumps(facts))
        report = service.project(root, facts, bundle)
    else:
        report = service.project(root, facts, bundle)
        if mutation == "forged_gate":
            (root / "case.yaml").unlink()
            report.run["gate"] = {"status": "incomplete", "reasons": ["completion_unresolved"]}
        elif mutation == "artifact_identity":
            next(item for item in report.artifacts if item["artifact_id"] == "source-snapshot")["sha256"] = "f" * 64
        elif mutation == "run_directory_binding":
            report._run_dirs[root.name] = tmp_path / "other-root"
        elif mutation == "non_path_run_root":
            report._run_dirs[root.name] = object()
        elif mutation == "persisted_metadata":
            persisted = json.loads((root / "run.json").read_text())
            persisted["source"]["digest"] = "e" * 64
            (root / "run.json").write_text(json.dumps(persisted))
        elif mutation == "persisted_non_object":
            (root / "run.json").write_text("[]")
        elif mutation == "persisted_source_list":
            persisted = json.loads((root / "run.json").read_text())
            persisted["source"] = []
            (root / "run.json").write_text(json.dumps(persisted))
        else:
            persisted = json.loads((root / "run.json").read_text())
            persisted["source"] = "invalid"
            (root / "run.json").write_text(json.dumps(persisted))
    destination = tmp_path / "not-created" / "report.json"
    with pytest.raises(ReportGenerationError) as error:
        service.export(report, RunReportExportOptions(format="json", destination=destination))
    assert error.value.context["reason"] == "source_identity_unavailable"
    assert not destination.parent.exists()


def test_historical_string_summary_and_tool_calls_remain_readable(tmp_path):
    root = tmp_path / "legacy"
    root.mkdir()
    (root / "report-fallback.json").write_text(json.dumps({"run_id": "legacy", "status": "success", "summary": "Completed"}))
    report = RunReportService().project(root)
    assert report.execution["outcome"] == "success"
    assert report.execution["summary"] == "Completed"
    (root / "report.json").write_text(
        json.dumps({"verification": {"status": "success"}, "execution": {"tool_calls": [{"tool_name": "read_file", "tool_origin": "agent_tool", "status": "completed", "duration_ms": 2}]}})
    )
    assert RunReportService().project(root).tool_calls[0]["tool_name"] == "read_file"


def test_planned_execution_identity_stays_null_and_hook_metadata_survives(tmp_path):
    root, facts, bundle = _fixture(tmp_path)
    planned = {
        "step_id": "plan-2",
        "step_execution_id": None,
        "source_step_id": "case:1",
        "status": "skipped",
        "kind": "assertion",
        "metadata": {"parent_hook_action": {"lifecycle_phase": "onCaseStart"}, "authored_action_name": "assertWithAI"},
        "phase_reports": [],
        "blocked_by_step": "s1",
        "skip_reason": "Start failed",
    }
    bundle["steps"].append(planned)
    _write_frozen(root, outcome="failed", counts={"total": 2, "passed": 1, "skipped": 1, "failed": 0, "cancelled": 0, "incomplete": 0, "attempt_count": 1}, steps=bundle["steps"])
    projected = RunReportService().project(root, facts, bundle).steps[1]
    assert projected["step_execution_id"] is None
    assert projected["step_id"] == "plan-2"
    assert projected["lifecycle_phase"] == "onCaseStart"
    assert projected["authored_action_name"] == "assertWithAI"


def test_logs_truncation_and_order_are_explicit(tmp_path):
    root, facts, bundle = _fixture(tmp_path)
    (root / "events.jsonl").write_text("\n".join(json.dumps(item) for item in [{"sequence": 2, "message": "x" * 5000}, 42, {"sequence": 1, "message": "first"}]))
    report = RunReportService().project(root, facts, bundle)
    assert [item["sequence"] for item in report.logs] == [1, 2]
    assert report.logs[1]["truncated"] is True
    assert report.logs[1]["original_size_bytes"] == 5000
    assert report.warnings


def test_junit_omits_unmeasured_time_and_retains_tool_detail(tmp_path):
    root, facts, bundle = _fixture(tmp_path)
    report = RunReportService().project(root, facts, bundle)
    report.tool_calls.append({"tool_name": "read_artifact", "tool_origin": "agent_tool"})
    output = RunReportService().export(report, RunReportExportOptions(format="junit", destination=tmp_path / "junit.xml"))
    xml = ET.parse(output.path).getroot()  # noqa: S314 - generated local fixture.
    assert "time" not in xml.find("testcase").attrib
    assert "read_artifact" in xml.find(".//system-out").text


def test_xml_wrapper_metadata_change_is_not_unchanged(tmp_path):
    root, facts, bundle = _fixture(tmp_path)
    for name, phase, state in (("before.json", "prepare", "before"), ("after.json", "finalize", "after")):
        (root / name).write_text(json.dumps({"xml": "<node />", "state": state}))
        bundle["artifacts"].append({"artifact_id": name, "kind": "ui_snapshot", "path": name, "step_execution_id": "s1", "phase": phase, "metadata": {"coverage": {"status": "complete"}}})
    report = RunReportService().project(root, facts, bundle)
    assert report.comparison["before_after"][0]["status"] == "changed"


def test_qualified_pairing_and_public_related_roundtrip(tmp_path):
    root, facts, bundle = _fixture(tmp_path)
    baseline_root, baseline_facts, baseline_bundle = _fixture(tmp_path, "baseline")
    for run, value, target in ((root, "CURRENT", bundle), (baseline_root, "BASELINE", baseline_bundle)):
        for name, phase in (("before.txt", "prepare"), ("after.txt", "finalize")):
            (run / name).write_text(value + name)
            target["artifacts"].append({"artifact_id": name, "kind": "ui_snapshot", "path": name, "step_execution_id": "s1", "phase": phase})
    service = RunReportService()
    baseline = service.project(baseline_root, baseline_facts, baseline_bundle)
    report = service.project(root, facts, bundle, baseline=baseline)
    roundtrip = PublicRunReport.model_validate_json(report.model_dump_json())
    assert roundtrip.comparison["baseline_report"]["execution"]["outcome"] == "success"
    output = service.export(report, RunReportExportOptions(format="json", destination=tmp_path / "out.json", share_profile={}))
    payload = json.loads(output.path.read_text())
    diff = payload["comparison"]["before_after"][0]
    assert diff["before"]["run_id"] == root.name
    assert diff["after"]["run_id"] == root.name
    assert payload["comparison"]["baseline_report"]["run"]["run_id"] == "baseline"


def test_claimed_source_hash_without_retained_bytes_is_not_comparable(tmp_path):
    root, facts, bundle = _fixture(tmp_path)
    other_root, other_facts, other_bundle = _fixture(tmp_path, "other")
    service = RunReportService()
    report = service.project(root, facts, bundle)
    other = service.project(other_root, other_facts, other_bundle)
    (other_root / "case.yaml").unlink()
    with pytest.raises(ReportGenerationError):
        service.compare(report, other)


def test_sanitizer_preserves_case_structure_and_removes_private_display(tmp_path):
    root, facts, bundle = _fixture(tmp_path)
    content = b"required_runtime_secret_names: []\n---\n- closeBrowser: {}\n"
    (root / "candidate.fsq.yaml").write_bytes(content)
    bundle["artifacts"].append({"artifact_id": "candidate", "kind": "text", "metadata": {"source_kind": "case"}, "path": "candidate.fsq.yaml"})
    _write_frozen(root, summary="See /opt/company/runtime.log token=private-value")
    report = RunReportService().project(root, facts, bundle)
    output = RunReportService().export(report, RunReportExportOptions(format="bundle", destination=tmp_path / "bundle.zip"))
    with zipfile.ZipFile(output.path) as archive:
        assert archive.read(f"runs/{root.name}/candidate.fsq.yaml") == content
        assert b"/opt/company" not in archive.read("report.json")
        assert b"private-value" not in archive.read("report.json")


def test_share_transform_once_provenance_and_retained_review_digest(tmp_path):
    root, facts, bundle = _fixture(tmp_path)
    text = "fsq-agent"
    (root / "notes.txt").write_text(text)
    bundle["artifacts"].append({"artifact_id": "notes", "kind": "text", "path": "notes.txt"})
    report = RunReportService().project(root, facts, bundle)
    output = RunReportService().export(
        report, RunReportExportOptions(format="bundle", destination=tmp_path / "share.zip", share_profile={"replacements": [{"text": "fsq-agent", "replacement": "Xfsq-agent"}]})
    )
    with zipfile.ZipFile(output.path) as archive:
        data = archive.read(f"runs/{root.name}/notes.txt")
        payload = json.loads(archive.read("report.json"))
        artifact = next(item for item in payload["artifacts"] if item["artifact_id"] == "notes")
        assert data == b"Xfsq-agent"
        assert artifact["content"] == "Xfsq-agent"
        assert artifact["derived_sha256"] == hashlib.sha256(data).hexdigest()
        index = json.loads(archive.read("index.json"))
        assert index["profile_version"] == "fsq.share/v1"
        assert index["transformations"]


def test_validate_export_does_not_create_dirs_and_dotdot_cannot_enter_run(tmp_path):
    root, facts, bundle = _fixture(tmp_path)
    report = RunReportService().project(root, facts, bundle)
    options = RunReportExportOptions(format="html", destination=root / "exports" / ".." / "bad.html")
    with pytest.raises(ReportGenerationError) as error:
        RunReportService().validate_export(report, options)
    assert error.value.context["reason"] == "path_unsafe"
    assert not (root / "exports").exists()
    with pytest.raises(ReportGenerationError):
        RunReportService().validate_export(
            report, RunReportExportOptions(format="json", destination=tmp_path / "absent" / "out.json", share_profile={"artifacts": [{"run_id": "other", "artifact_id": "no"}]})
        )
    assert not (tmp_path / "absent").exists()


def test_later_processing_is_read_without_changing_frozen_outcome(tmp_path):
    root, facts, bundle = _fixture(tmp_path)
    (root / "suggestion-processing.json").write_text(json.dumps({"suggestion": {"status": "failed", "code": "case.suggestion_failed"}}))
    report = RunReportService().project(root, facts, bundle)
    assert report.processing["suggestion"]["status"] == "failed"
    assert report.run["gate"]["status"] == "passed"


def test_core_counts_logical_leaves_and_markdown_retains_status_totals(tmp_path):
    from fsq_agent.models import EvidenceBundle, RunnerStepResult

    bundle = EvidenceBundle(
        run_id="core",
        bundle_id="core",
        steps=[
            RunnerStepResult(step_id="container", status="passed", metadata={"hook_action_name": "runCase"}),
            RunnerStepResult(step_id="attempt-1", source_step_id="same", invocation_path=("root",), status="failed"),
            RunnerStepResult(step_id="attempt-2", source_step_id="same", invocation_path=("root",), status="passed"),
            RunnerStepResult(step_id="skip", status="skipped"),
        ],
    )
    path = tmp_path / "evidence-manifest.json"
    path.write_text(bundle.model_dump_json())
    artifact = CoreEvidenceReportGenerator().generate_from_manifest(path)
    payload = json.loads(artifact.path.with_suffix(".json").read_text())
    assert payload["summary"]["step_count"] == 2
    assert payload["summary"]["failed_steps"] == 0
    assert "Skipped steps:" in artifact.path.read_text()


def test_profile_review_binds_transformed_retained_bytes(tmp_path):
    root, facts, bundle = _fixture(tmp_path)
    (root / "case.yaml").write_bytes(b"name: audit\ndescription: private-display\n")
    facts["source"]["digest"] = hashlib.sha256((root / "case.yaml").read_bytes()).hexdigest()
    (root / "run.json").write_text(json.dumps(facts))
    report = RunReportService().project(root, facts, bundle)
    original = (root / "case.yaml").read_bytes()
    transformed = original.replace(b"private-display", b"reviewed")
    options = {
        "replacements": [{"text": "private-display", "replacement": "reviewed"}],
        "case_review_declaration": {"run_id": root.name, "artifact_id": "source-snapshot", "sha256": hashlib.sha256(original).hexdigest(), "attribution": "Codex automated review"},
    }
    with pytest.raises(ReportGenerationError):
        RunReportService().validate_export(report, RunReportExportOptions(format="bundle", destination=tmp_path / "old.zip", share_profile=options))
    options["case_review_declaration"]["sha256"] = hashlib.sha256(transformed).hexdigest()
    output = RunReportService().export(report, RunReportExportOptions(format="bundle", destination=tmp_path / "new.zip", share_profile=options))
    with zipfile.ZipFile(output.path) as archive:
        assert archive.read(f"runs/{root.name}/case.yaml") == transformed


def test_complete_bundle_budget_includes_index_and_hashes(tmp_path, monkeypatch):
    from fsq_agent.report import _export

    root, facts, bundle = _fixture(tmp_path)
    report = RunReportService().project(root, facts, bundle)
    monkeypatch.setattr(_export, "MAX_BUNDLE_BYTES", 100)
    with pytest.raises(ReportGenerationError):
        RunReportService().export(report, RunReportExportOptions(format="bundle", destination=tmp_path / "huge.zip"))
    assert not (tmp_path / "huge.zip").exists()


def test_projection_raster_and_required_text_use_fixed_resource_policy(tmp_path, monkeypatch):
    from PIL import Image

    from fsq_agent.report import _run_report

    root, facts, bundle = _fixture(tmp_path)
    Image.new("RGB", (8, 8)).save(root / "large.png")
    bundle["artifacts"].append({"artifact_id": "large", "kind": "screenshot", "path": "large.png"})
    monkeypatch.setattr(_run_report, "MAX_IMAGE_PIXELS", 16)
    report = RunReportService().project(root, facts, bundle)
    artifact = next(item for item in report.artifacts if item["artifact_id"] == "large")
    assert artifact["display_availability"] == "omitted"
    assert artifact["unavailable_reason"] == "raster_resource_limit"
    assert artifact["availability"] == "available"
    monkeypatch.setattr(_run_report, "MAX_TEXT_BYTES", 200)
    with pytest.raises(ReportGenerationError) as error:
        RunReportService().project(root, facts, bundle)
    assert error.value.context["reason"] == "resource_limit"


def test_internal_tool_report_exposes_execution_and_transport_separately(tmp_path):
    from fsq_agent.models import Task, VerificationResult
    from fsq_agent.report import ReportGenerator

    root = tmp_path / "tool"
    root.mkdir()
    (root / "events.jsonl").write_text(json.dumps({"type": "tool_call_completed", "tool_name": "click_on", "payload": {"status": "failed"}}))
    ReportGenerator(tmp_path).generate("tool", Task(description="Check"), [], VerificationResult(status="failed", summary="Not achieved"))
    call = json.loads((root / "report.json").read_text())["execution"]["tool_calls"][0]
    assert call["transport_status"] == "completed"
    assert call["execution_status"] == "failed"
