# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import json
import zipfile

import pytest

from fsq_agent.models import ReportGenerationError, RunReportExportOptions
from fsq_agent.report import RunReportService
from tests.test_report_audit import _fixture, _write_frozen


@pytest.mark.parametrize(
    "message", ["Authorization: Bearer CANARY_AUTH", "Cookie: first=a; second=CANARY_AUTH", "Proxy-Authorization: Basic CANARY_AUTH", '{"headers":{"Authorization":"Bearer CANARY_AUTH"}}']
)
def test_full_credential_values_redacted_in_projection_and_bundle(tmp_path, message):
    root, facts, bundle = _fixture(tmp_path)
    (root / "ui.txt").write_text(message)
    (root / "events.jsonl").write_text(json.dumps({"message": message}))
    bundle["artifacts"].append({"artifact_id": "ui", "kind": "ui_snapshot", "path": "ui.txt"})
    report = RunReportService().project(root, facts, bundle)
    assert "CANARY_AUTH" not in report.model_dump_json()
    output = RunReportService().export(report, RunReportExportOptions(format="bundle", destination=tmp_path / "safe.zip"))
    with zipfile.ZipFile(output.path) as archive:
        assert all(b"CANARY_AUTH" not in archive.read(name) for name in archive.namelist())


@pytest.mark.parametrize("identity", [{"platform": "android"}, {"metadata": {"platform": "android"}}, {"metadata": {"workspace": {"name": "foreign"}}}])
def test_historical_scope_facts_must_match_supplied_scope(tmp_path, identity):
    root = tmp_path / "historical"
    root.mkdir()
    (root / "core-report.json").write_text(json.dumps({"run_id": root.name, "summary": {"status": "passed"}, **identity}))
    with pytest.raises(ReportGenerationError):
        RunReportService().project(root, {"run_id": root.name, "platform": "web", "mode": "strict", "workspace": {"name": "audit"}})


def test_attempt_count_conflict_blocks_passing_gate(tmp_path):
    root, facts, bundle = _fixture(tmp_path)
    _write_frozen(root, counts={"total": 1, "passed": 1, "failed": 0, "skipped": 0, "cancelled": 0, "incomplete": 0, "attempt_count": 2})
    report = RunReportService().project(root, facts, bundle)
    assert report.execution["accounting_complete"] is False
    assert report.run["gate"]["status"] != "passed"
    assert report.execution["observed_attempt_count"] == 1


@pytest.mark.parametrize("conflict", [{"sha256": "b" * 64}, {"kind": "json"}, {"step_execution_id": "other"}, {"phase": "finalize"}, {"capture_occurrence": 2}])
def test_duplicate_artifact_same_path_conflicts_in_every_identity_dimension(tmp_path, conflict):
    root, facts, bundle = _fixture(tmp_path)
    (root / "ui.txt").write_text("value")
    first = {"artifact_id": "ui", "kind": "ui_snapshot", "path": "ui.txt", "step_execution_id": "s1", "phase": "prepare", "capture_occurrence": 1}
    bundle["artifacts"] = [first, {**first, **conflict}]
    report = RunReportService().project(root, facts, bundle)
    assert report.artifacts[0]["availability"] == "unavailable"
    assert report.run["gate"]["status"] != "passed"


@pytest.mark.parametrize("category", ["configuration_error", "context_error", "harness_error", "provider_error", "artifact_error"])
def test_structured_infrastructure_category_is_error_gate(tmp_path, category):
    root, facts, bundle = _fixture(tmp_path, status="failed")
    bundle["steps"][0].update(status="failed", action_status="failed", failure_category=category)
    if category == "provider_error":
        bundle["schema_version"] = "1.0"
    _write_frozen(root, outcome="failed", counts={"total": 1, "passed": 0, "failed": 1, "skipped": 0, "cancelled": 0, "incomplete": 0, "attempt_count": 1}, steps=bundle["steps"])
    report = RunReportService().project(root, facts, bundle)
    assert report.execution["outcome"] == "failed"
    assert report.run["gate"]["status"] == "error"


@pytest.mark.parametrize("kind,availability,selection", [("text", "available", None), ("screenshot", "failed", None), ("screenshot", "available", [])])
def test_mask_target_must_be_available_selected_screenshot(tmp_path, kind, availability, selection):
    root, facts, bundle = _fixture(tmp_path)
    (root / "target.txt").write_text("text")
    bundle["artifacts"].append({"artifact_id": "target", "kind": kind, "path": "target.txt", "availability": availability})
    report = RunReportService().project(root, facts, bundle)
    profile = {"masks": [{"run_id": root.name, "artifact_id": "target", "x": 0, "y": 0, "width": 1, "height": 1}]}
    if selection is not None:
        profile["artifacts"] = selection
    destination = tmp_path / "absent" / "out.json"
    with pytest.raises(ReportGenerationError):
        RunReportService().validate_export(report, RunReportExportOptions(format="json", destination=destination, share_profile=profile))
    assert not destination.parent.exists()


@pytest.mark.parametrize("report_format,extension", [("json", "json"), ("junit", "xml"), ("html", "html"), ("bundle", "zip")])
def test_optional_truncated_jsonl_does_not_block_report_export(tmp_path, report_format, extension):
    root, facts, bundle = _fixture(tmp_path)
    (root / "events.jsonl").write_bytes(b'{"message":"safe"}\n{"message":"unfinished')
    report = RunReportService().project(root, facts, bundle)
    output = RunReportService().export(report, RunReportExportOptions(format=report_format, destination=tmp_path / f"out.{extension}"))
    assert output.path.is_file()
    assert any("truncat" in w.lower() or "trailing" in w.lower() for w in output.warnings)
    if report_format == "bundle":
        with zipfile.ZipFile(output.path) as archive:
            assert archive.read(f"runs/{root.name}/events.jsonl") == b'{"message":"safe"}\n'


def test_display_only_transform_inventory_records_only_real_matches(tmp_path):
    root, facts, bundle = _fixture(tmp_path)
    report = RunReportService().project(root, facts, bundle)
    report.execution["summary"] = "CANARY_MATCH"
    service = RunReportService()
    changed = service.export(report, RunReportExportOptions(format="json", destination=tmp_path / "changed.json", share_profile={"replacements": [{"text": "CANARY_MATCH", "replacement": "safe"}]}))
    payload = json.loads(changed.path.read_text())
    transformations = payload["run"]["export"]["transformations"]
    assert any(item.get("run_id") == root.name and item.get("field_path") == "execution.summary" and item.get("operation") == "literal_replacement" for item in transformations)
    assert "CANARY_MATCH" not in json.dumps(transformations)
    unchanged = service.export(
        report, RunReportExportOptions(format="json", destination=tmp_path / "unchanged.json", share_profile={"replacements": [{"text": "DOES_NOT_MATCH", "replacement": "safe"}]})
    )
    data = json.loads(unchanged.path.read_text())
    assert not data["run"]["export"]["transformations"]
    assert not any("profile transformed" in warning for warning in data["warnings"])


def test_nested_json_strings_in_protected_metadata_still_remove_credential_fields(tmp_path):
    root, facts, bundle = _fixture(tmp_path)
    text = json.dumps({"safe_replay_params": {"opaque": json.dumps({"password": "CANARY_INNER", "outcome": "failed"})}, "outcome": "failed"})
    (root / "safe.json").write_text(text)
    bundle["artifacts"].append({"artifact_id": "safe", "kind": "json", "path": "safe.json"})
    report = RunReportService().project(root, facts, bundle)
    result = RunReportService().export(report, RunReportExportOptions(format="bundle", destination=tmp_path / "nested.zip"))
    with zipfile.ZipFile(result.path) as archive:
        safe = archive.read(f"runs/{root.name}/safe.json")
        assert b"CANARY_INNER" not in safe
        assert json.loads(safe)["outcome"] == "failed"


def test_bad_complete_optional_log_is_explicit_error_before_any_output(tmp_path):
    root, facts, bundle = _fixture(tmp_path)
    (root / "events.jsonl").write_bytes(b'{"message":"good"}\nbroken\n')
    report = RunReportService().project(root, facts, bundle)
    destination = tmp_path / "no-directory" / "result.json"
    with pytest.raises(ReportGenerationError):
        RunReportService().validate_export(report, RunReportExportOptions(format="json", destination=destination))
    assert not destination.parent.exists()


def test_masking_unavailable_or_omitted_is_error_in_every_format(tmp_path):
    root, facts, bundle = _fixture(tmp_path)
    bundle["artifacts"].append({"artifact_id": "missing", "kind": "screenshot", "availability": "missing", "path": None})
    report = RunReportService().project(root, facts, bundle)
    for fmt in ("json", "junit", "html", "bundle"):
        with pytest.raises(ReportGenerationError):
            RunReportService().validate_export(
                report,
                RunReportExportOptions(
                    format=fmt, destination=tmp_path / f"out.{fmt}", share_profile={"masks": [{"run_id": root.name, "artifact_id": "missing", "x": 0, "y": 0, "width": 1, "height": 1}]}
                ),
            )
