# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import hashlib
import json
import xml.etree.ElementTree as ET
import zipfile

import pytest
from PIL import Image

from fsq_agent.models import ReportGenerationError, RunReportExportOptions
from fsq_agent.report import RunReportService
from tests.test_report_audit import _fixture


def test_structured_api_and_export_share_safe_display_base(tmp_path):
    root, facts, bundle = _fixture(tmp_path)
    (root / "ui.json").write_text(json.dumps({"password": "CANARY_CREDENTIAL", "snapshot": "open /opt/company/private.txt"}))
    bundle["artifacts"].append({"artifact_id": "ui", "kind": "ui_snapshot", "path": "ui.json", "step_execution_id": "s1"})
    report = RunReportService().project(root, facts, bundle)
    assert "CANARY_CREDENTIAL" not in report.model_dump_json()
    assert "/opt/company" not in report.model_dump_json()
    result = RunReportService().export(report, RunReportExportOptions(format="bundle", destination=tmp_path / "safe.zip"))
    with zipfile.ZipFile(result.path) as archive:
        for filename in archive.namelist():
            assert b"CANARY_CREDENTIAL" not in archive.read(filename)
            assert b"/opt/company" not in archive.read(filename)


@pytest.mark.parametrize("suffix,raw", [("json", '{"snapshot":"CANARY_CONTENT"}'), ("jsonl", '{"message":"CANARY_CONTENT"}\n'), ("yaml", "description: CANARY_CONTENT\n")])
def test_structured_content_removal_is_not_ignored(tmp_path, suffix, raw):
    root, facts, bundle = _fixture(tmp_path)
    name = f"content.{suffix}"
    (root / name).write_text(raw)
    bundle["artifacts"].append({"artifact_id": "content", "kind": "text", "path": name})
    report = RunReportService().project(root, facts, bundle)
    result = RunReportService().export(report, RunReportExportOptions(format="bundle", destination=tmp_path / "removed.zip", share_profile={"remove_fields": ["artifacts.content"]}))
    with zipfile.ZipFile(result.path) as archive:
        assert all(b"CANARY_CONTENT" not in archive.read(name) for name in archive.namelist())


@pytest.mark.parametrize("field", ["snapshot", "xml"])
def test_wrapped_snapshot_profile_replacement_applies_once(tmp_path, field):
    root, facts, bundle = _fixture(tmp_path)
    (root / "ui.json").write_text(json.dumps({field: "CANARY_REPLACE"}))
    bundle["artifacts"].append({"artifact_id": "ui", "kind": "ui_snapshot", "path": "ui.json"})
    report = RunReportService().project(root, facts, bundle)
    result = RunReportService().export(
        report, RunReportExportOptions(format="bundle", destination=tmp_path / "changed.zip", share_profile={"replacements": [{"text": "CANARY_REPLACE", "replacement": "safe"}]})
    )
    with zipfile.ZipFile(result.path) as archive:
        assert all(b"CANARY_REPLACE" not in archive.read(name) for name in archive.namelist())


@pytest.mark.parametrize("status", ["pending", "running", "skipped"])
def test_historical_pending_and_mixed_skipped_are_consistent_nonpassing(tmp_path, status):
    root, facts, bundle = _fixture(tmp_path)
    (root / "execution-result.json").unlink()
    facts["schema_version"] = "fsq.run/v1"
    (root / "run.json").write_text(json.dumps(facts))
    bundle["schema_version"] = "1.0"
    bundle["steps"].append({**bundle["steps"][0], "step_id": "s2", "step_execution_id": None, "source_step_id": "case:1", "status": status, "action_status": status})
    report = RunReportService().project(root, facts, bundle)
    counts = report.execution["counts"]
    assert counts["total"] == sum(counts[k] for k in ("passed", "failed", "skipped", "cancelled", "incomplete"))
    assert report.run["gate"]["status"] != "passed"


def test_bundle_aliases_have_unique_paths_and_masked_bytes_match_identity(tmp_path):
    root, facts, bundle = _fixture(tmp_path)
    Image.new("RGB", (4, 4), "white").save(root / "shot.png")
    bundle["artifacts"].extend({"artifact_id": name, "kind": "screenshot", "path": "shot.png"} for name in ("masked", "unmasked"))
    report = RunReportService().project(root, facts, bundle)
    output = RunReportService().export(
        report,
        RunReportExportOptions(format="bundle", destination=tmp_path / "alias.zip", share_profile={"masks": [{"run_id": root.name, "artifact_id": "masked", "x": 0, "y": 0, "width": 4, "height": 4}]}),
    )
    with zipfile.ZipFile(output.path) as archive:
        artifacts = json.loads(archive.read("index.json"))["artifacts"]
        aliases = [a for a in artifacts if a["artifact_id"] in ("masked", "unmasked")]
        assert len({a["export_path"] for a in aliases}) == 2
        for artifact in aliases:
            assert hashlib.sha256(archive.read(artifact["export_path"])).hexdigest() == artifact.get("derived_sha256", artifact["sha256"])


def test_historical_identity_and_foreign_event_are_rejected(tmp_path):
    root = tmp_path / "history"
    root.mkdir()
    (root / "report.json").write_text('{"run_id":"foreign","status":"success"}')
    with pytest.raises(ReportGenerationError):
        RunReportService().project(root)
    (root / "report.json").unlink()
    (root / "events.jsonl").write_text('{"run_id":"foreign","type":"tool_call_completed","payload":{"runner_result":{"step_id":"one","status":"passed"}}}')
    with pytest.raises(ReportGenerationError):
        RunReportService().project(root)


def test_optional_nested_budget_content_is_omitted_before_required_error(tmp_path, monkeypatch):
    from fsq_agent.report import _run_report

    root, facts, bundle = _fixture(tmp_path)
    other, other_facts, other_bundle = _fixture(tmp_path, "baseline")
    (other / "large.txt").write_text("x" * 20000)
    other_bundle["artifacts"].append({"artifact_id": "large", "kind": "text", "path": "large.txt"})
    service = RunReportService()
    baseline = service.project(other, other_facts, other_bundle)
    monkeypatch.setattr(_run_report, "MAX_TEXT_BYTES", 18000)
    report = service.project(root, facts, bundle, baseline=baseline)
    assert "x" * 10000 not in report.model_dump_json()
    assert report.comparison["baseline_report"]["artifacts"][-1].get("content") is None or "x" * 10000 not in report.comparison["baseline_report"]["artifacts"][-1].get("content", "")


def test_xml_preserved_whitespace_changes_remain_visible(tmp_path):
    root, facts, bundle = _fixture(tmp_path)
    for name, phase, text in [("before.xml", "prepare", '<a xml:space="preserve"> <b/> </a>'), ("after.xml", "finalize", '<a xml:space="preserve">   <b/> </a>')]:
        (root / name).write_text(text)
        bundle["artifacts"].append({"artifact_id": name, "kind": "ui_snapshot", "path": name, "step_execution_id": "s1", "phase": phase})
    report = RunReportService().project(root, facts, bundle)
    assert report.comparison["before_after"][0]["status"] != "unchanged"


def test_junit_forbidden_characters_are_sanitized_and_warned(tmp_path):
    root, facts, bundle = _fixture(tmp_path)
    report = RunReportService().project(root, facts, bundle)
    report.execution["summary"] = "bad" + chr(0) + "character"
    report.run["gate"] = {"status": "failed", "reasons": ["failed"]}
    output = RunReportService().export(report, RunReportExportOptions(format="junit", destination=tmp_path / "valid.xml"))
    ET.parse(output.path)  # noqa: S314 - generated local test output.
    assert any("XML" in warning for warning in output.warnings)


def test_ui_json_payload_without_json_extension_uses_structured_sanitizer(tmp_path):
    root, facts, bundle = _fixture(tmp_path)
    (root / "ui.txt").write_text(' {"client_secret":"CANARY_UI","snapshot":"read /opt/company/x"}')
    bundle["artifacts"].append({"artifact_id": "ui", "kind": "ui_snapshot", "path": "ui.txt"})
    report = RunReportService().project(root, facts, bundle)
    assert "CANARY_UI" not in report.model_dump_json()
    assert "/opt/company" not in report.model_dump_json()


def test_foreign_nested_runner_result_identity_is_rejected(tmp_path):
    root, facts, bundle = _fixture(tmp_path)
    event = {"run_id": root.name, "type": "tool_call_completed", "payload": {"runner_result": {**bundle["steps"][0], "run_id": "foreign"}}}
    (root / "events.jsonl").write_text(json.dumps(event))
    with pytest.raises(ReportGenerationError):
        RunReportService().project(root, facts, bundle)


def test_bundle_export_paths_do_not_collide_with_real_by_artifact_path(tmp_path):
    root, facts, bundle = _fixture(tmp_path)
    Image.new("RGB", (2, 2), "white").save(root / "shot.png")
    alias = "masked"
    qualified = hashlib.sha256(json.dumps((root.name, alias), ensure_ascii=False).encode()).hexdigest()
    trap = f"by-artifact/{qualified}/shot.png"
    (root / trap).parent.mkdir(parents=True)
    Image.new("RGB", (2, 2), "blue").save(root / trap)
    bundle["artifacts"].extend(
        [
            {"artifact_id": alias, "kind": "screenshot", "path": "shot.png"},
            {"artifact_id": "other", "kind": "screenshot", "path": "shot.png"},
            {"artifact_id": "trap", "kind": "screenshot", "path": trap},
        ]
    )
    report = RunReportService().project(root, facts, bundle)
    with pytest.raises(ReportGenerationError):
        RunReportService().export(report, RunReportExportOptions(format="bundle", destination=tmp_path / "collision.zip"))
    assert not (tmp_path / "collision.zip").exists()
