# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import hashlib
import json
import zipfile

import pytest
import yaml

from fsq_agent.models import RunReportExportOptions
from fsq_agent.report import RunReportService
from tests.test_report_audit import _fixture, _write_frozen


def test_events_only_counts_are_explicitly_unavailable(tmp_path):
    root = tmp_path / "history"
    root.mkdir()
    (root / "events.jsonl").write_text('{"message":"Started"}')
    counts = RunReportService().project(root).execution["counts"]
    assert counts["total"] is None
    assert counts["attempt_count"] is None
    assert counts["unavailable_reason"] == "historical_accounting_unavailable"


def test_frozen_primary_details_are_selected_from_complete_matching_record(tmp_path):
    root, facts, bundle = _fixture(tmp_path)
    first = bundle["steps"][0]
    first.update(action_status="failed", status="failed", action_name="early_action", error_message="recovered failure")
    second = {**first, "step_id": "s2", "step_execution_id": "s2", "source_step_id": "case:1", "action_name": "late_assertion", "error_message": "blocking failure", "duration_ms": 42}
    bundle["steps"].append(second)
    _write_frozen(
        root,
        outcome="failed",
        counts={"total": 2, "failed": 2, "passed": 0, "skipped": 0, "cancelled": 0, "incomplete": 0, "attempt_count": 2},
        steps=bundle["steps"],
        primary_failure={"step_id": "s2", "category": "assertion_error", "message": "blocking failure"},
    )
    failure = RunReportService().project(root, facts, bundle).execution["first_failure"]
    assert failure["step_execution_id"] == "s2"
    assert failure["action_name"] == "late_assertion"
    assert failure["metrics"]["duration_ms"]["value"] == 42


@pytest.mark.parametrize(
    "suffix,content",
    [("json", '{"outcome":"failed","summary":"secretword","token":"CANARY_JSON"}'), ("jsonl", '{"status":"failed","message":"secretword","payload":{"authorization":"CANARY_JSON"}}\n')],
)
def test_structured_bundle_sanitizes_credentials_but_profile_preserves_machine_fields(tmp_path, suffix, content):
    root, facts, bundle = _fixture(tmp_path)
    name = f"raw.{suffix}"
    (root / name).write_text(content)
    bundle["artifacts"].append({"artifact_id": "raw", "kind": "log" if suffix == "jsonl" else "json", "path": name})
    report = RunReportService().project(root, facts, bundle)
    output = RunReportService().export(
        report,
        RunReportExportOptions(
            format="bundle", destination=tmp_path / "safe.zip", share_profile={"replacements": [{"text": "secretword", "replacement": "safe"}, {"text": "failed", "replacement": "passed"}]}
        ),
    )
    with zipfile.ZipFile(output.path) as archive:
        copied = archive.read(f"runs/{root.name}/{name}")
        parsed = json.loads(copied)
        assert b"CANARY_JSON" not in copied
        assert b"secretword" not in copied
        assert parsed.get("outcome", parsed.get("status")) == "failed"
        artifact = next(a for a in json.loads(archive.read("report.json"))["artifacts"] if a["artifact_id"] == "raw")
        assert artifact["derived_sha256"] == hashlib.sha256(copied).hexdigest()
    assert (root / name).read_text() == content


def test_interpretation_display_text_uses_same_one_time_replacement(tmp_path):
    root, facts, bundle = _fixture(tmp_path)
    report = RunReportService().project(root, facts, bundle)
    report.execution["summary"] = "secretword"
    report.execution["interpretations"] = [{"kind": "recorded_fact", "availability": "available", "text": "secretword", "references": []}]
    output = RunReportService().export(
        report, RunReportExportOptions(format="json", destination=tmp_path / "shared.json", share_profile={"replacements": [{"text": "secretword", "replacement": "Xsecretword"}]})
    )
    data = json.loads(output.path.read_text())
    assert data["execution"]["summary"] == "Xsecretword"
    assert data["execution"]["interpretations"][0]["text"] == "Xsecretword"


def test_suggestion_manifest_candidate_is_exported_even_without_processing_reference(tmp_path):
    root, facts, bundle = _fixture(tmp_path)
    (root / "candidate.fsq.yaml").write_text("name: suggestion")
    (root / "case-suggestions.json").write_text(json.dumps({"analysis_summary": "Use stable locator", "candidate_case_path": "candidate.fsq.yaml", "candidate_case_status": "available"}))
    report = RunReportService().project(root, facts, bundle)
    candidate = next(a for a in report.artifacts if a["artifact_id"] == "suggested-candidate-case")
    assert candidate["path"] == "candidate.fsq.yaml"
    output = RunReportService().export(report, RunReportExportOptions(format="bundle", destination=tmp_path / "suggestion.zip"))
    with zipfile.ZipFile(output.path) as archive:
        assert archive.read(f"runs/{root.name}/candidate.fsq.yaml") == b"name: suggestion"


def test_historical_yaml_block_credentials_are_sanitized_with_derived_hash(tmp_path):
    root, facts, bundle = _fixture(tmp_path)
    text = "name: legacy\nproperties:\n  password: |\n    CANARY_YAML\n  required_runtime_secret_names: []\n---\n- closeBrowser: {}\n"
    (root / "legacy.fsq.yaml").write_text(text)
    bundle["artifacts"].append({"artifact_id": "legacy", "kind": "text", "path": "legacy.fsq.yaml"})
    report = RunReportService().project(root, facts, bundle)
    assert "CANARY_YAML" not in report.model_dump_json()
    output = RunReportService().export(report, RunReportExportOptions(format="bundle", destination=tmp_path / "legacy.zip"))
    with zipfile.ZipFile(output.path) as archive:
        raw = archive.read(f"runs/{root.name}/legacy.fsq.yaml")
        docs = list(yaml.safe_load_all(raw))
        assert b"CANARY_YAML" not in raw
        assert docs[1] == [{"closeBrowser": {}}]
        assert docs[0]["properties"]["required_runtime_secret_names"] == []
        artifact = next(a for a in json.loads(archive.read("report.json"))["artifacts"] if a["artifact_id"] == "legacy")
        assert artifact["derived_sha256"] == hashlib.sha256(raw).hexdigest()
    assert (root / "legacy.fsq.yaml").read_text() == text


def test_exact_recording_source_mismatch_cannot_fall_back_to_nested_index(tmp_path):
    service = RunReportService()
    origin, origin_facts, origin_bundle = _fixture(tmp_path, "origin", mode="explore")
    origin_bundle["steps"][0].update(source_step_id="origin:0", invocation_path=["agent", "1"])
    _write_frozen(origin, steps=origin_bundle["steps"])
    original_mapping = {"command_index": 0, "source_step_id": "origin:0", "step_execution_id": "s1", "invocation_path": ["agent", "1"]}
    retained_case = yaml.safe_dump({"name": "recorded", "properties": {}}).encode()
    (origin / "case.yaml").write_bytes(retained_case)
    (origin / "recording.json").write_text(json.dumps({"source_run_id": "origin", "command_mapping": [original_mapping]}))
    origin_facts["source"]["digest"] = hashlib.sha256(retained_case).hexdigest()
    (origin / "run.json").write_text(json.dumps(origin_facts))
    origin_report = service.project(origin, origin_facts, origin_bundle)
    origin_report.lineage["relations"] = [{"kind": "recording", "originating_run_id": "origin", "case_digest": hashlib.sha256(retained_case).hexdigest()}]
    original = (origin / "case.yaml").read_bytes()
    origin_report.artifacts.append({**next(a for a in origin_report.artifacts if a["artifact_id"] == "source-snapshot"), "artifact_id": "candidate_case"})
    current, facts, bundle = _fixture(tmp_path, "replay")
    (current / "case.yaml").write_bytes(retained_case)
    facts["source"]["digest"] = hashlib.sha256(retained_case).hexdigest()
    (current / "run.json").write_text(json.dumps(facts))
    bundle["steps"][0].update(source_step_id="root:0", invocation_path=["root"])
    nested = {**bundle["steps"][0], "step_id": "nested", "step_execution_id": "nested", "source_step_id": "nested:0", "invocation_path": ["root", "nested"]}
    bundle["steps"].append(nested)
    _write_frozen(current, steps=bundle["steps"], counts={"total": 2, "passed": 2, "failed": 0, "skipped": 0, "cancelled": 0, "incomplete": 0, "attempt_count": 2})
    report = service.project(current, facts, bundle)
    report.lineage["relations"] = [
        {
            "kind": "replay",
            "originating_run_id": "origin",
            "case_digest": hashlib.sha256(original).hexdigest(),
            "command_mapping": [{**original_mapping, "current_source_step_id": "root:0"}],
        }
    ]
    comparison = service.compare(report, origin_report)
    rows = {r["current"]["step_execution_id"]: r for r in comparison["steps"]}
    assert rows["s1"]["status"] == "matched"
    assert rows["nested"]["status"] == "unmatched"


def test_historical_summary_counts_remain_available_without_fabricated_attempts(tmp_path):
    root = tmp_path / "summary"
    root.mkdir()
    (root / "core-report.json").write_text(json.dumps({"run_id": "summary", "summary": {"status": "passed", "step_count": 8, "passed_steps": 8, "failed_steps": 0}}))
    counts = RunReportService().project(root).execution["counts"]
    assert counts["total"] == 8
    assert counts["passed"] == 8
    assert counts["attempt_count"] is None


def test_structured_nested_display_and_json_string_credentials_are_safe(tmp_path):
    root, facts, bundle = _fixture(tmp_path)
    value = {"payload": {"message": "CANARY_REPLACE", "arguments": '{"password":"CANARY_NESTED"}'}, "execution": {"outcome": "failed"}}
    (root / "nested.json").write_text(json.dumps(value))
    bundle["artifacts"].append({"artifact_id": "nested", "kind": "json", "path": "nested.json"})
    report = RunReportService().project(root, facts, bundle)
    output = RunReportService().export(
        report, RunReportExportOptions(format="bundle", destination=tmp_path / "nested.zip", share_profile={"replacements": [{"text": "CANARY_REPLACE", "replacement": "safe"}]})
    )
    with zipfile.ZipFile(output.path) as archive:
        raw = archive.read(f"runs/{root.name}/nested.json")
        parsed = json.loads(raw)
        assert b"CANARY_NESTED" not in raw
        assert parsed["payload"]["message"] == "safe"
        assert parsed["execution"]["outcome"] == "failed"
