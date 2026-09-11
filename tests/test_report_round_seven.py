# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import hashlib
import json
import xml.etree.ElementTree as ET

import pytest
import yaml
from PIL import Image

from fsq_agent.models import ReportGenerationError, RunReportExportOptions
from fsq_agent.report import RunReportService
from tests.test_report_audit import _fixture, _write_frozen


def test_run_unmeasured_duration_stays_unknown_in_metrics_and_junit(tmp_path):
    root, facts, bundle = _fixture(tmp_path)
    facts.update(schema_version="fsq.run/v1", duration_ms=0, duration_unavailable_reason="unmeasured")
    (root / "run.json").write_text(json.dumps(facts))
    report = RunReportService().project(root, facts, bundle)
    assert report.metrics["run_duration"]["value"] is None
    output = RunReportService().export(report, RunReportExportOptions(format="junit", destination=tmp_path / "unknown.xml"))
    assert "time" not in ET.parse(output.path).getroot().find("testcase").attrib  # noqa: S314


def _mapped_reports(tmp_path):
    origin, origin_facts, origin_bundle = _fixture(tmp_path, "origin", mode="explore")
    origin_bundle["steps"][0].update(source_step_id="origin:0", invocation_path=["agent", "1"])
    wrong = {**origin_bundle["steps"][0], "step_id": "other", "step_execution_id": "other", "source_step_id": "origin:1", "action_name": "start_browser", "invocation_path": ["agent", "2"]}
    origin_bundle["steps"].append(wrong)
    _write_frozen(origin, steps=origin_bundle["steps"], counts={"total": 2, "passed": 2, "failed": 0, "skipped": 0, "cancelled": 0, "incomplete": 0, "attempt_count": 2})
    original_mapping = {"command_index": 0, "source_step_id": "origin:0", "step_execution_id": "s1", "invocation_path": ["agent", "1"]}
    candidate = yaml.safe_dump_all([{"name": "case", "properties": {}}, [{"clickOn": {}}]], sort_keys=False).encode()
    (origin / "candidate.yaml").write_bytes(candidate)
    (origin / "recording.json").write_text(json.dumps({"source_run_id": "origin", "command_mapping": [original_mapping]}))
    origin_facts["artifacts"] = {"candidate_case": "candidate.yaml"}
    (origin / "run.json").write_text(json.dumps(origin_facts))
    service = RunReportService()
    baseline = service.project(origin, origin_facts, origin_bundle)
    baseline.lineage["relations"] = [{"kind": "recording", "originating_run_id": "origin", "case_digest": hashlib.sha256(candidate).hexdigest()}]
    current, facts, bundle = _fixture(tmp_path, "current")
    (current / "case.yaml").write_bytes(candidate)
    facts["source"]["digest"] = hashlib.sha256(candidate).hexdigest()
    (current / "run.json").write_text(json.dumps(facts))
    report = service.project(current, facts, bundle)
    mapping = {**original_mapping, "current_source_step_id": bundle["steps"][0]["source_step_id"], "current_invocation_path": ["root"]}
    report.lineage["relations"] = [{"kind": "replay", "originating_run_id": "origin", "case_digest": facts["source"]["digest"], "command_mapping": [mapping]}]
    return service, report, baseline


def test_renamed_public_save_supports_strict_baseline_related_and_export(tmp_path):
    from fsq_agent._capability_bootstrap import build_capability_registry
    from fsq_agent.application import CaseSaveRequest, save_recorded_case
    from fsq_agent.case_dsl import FsqCaseLoader, FsqCaseSerializer

    service, current, origin = _mapped_reports(tmp_path)
    origin_dir = origin._run_dirs["origin"]
    candidate = origin_dir / "recorded.fsq.yaml"
    origin_metadata = json.loads((origin_dir / "run.json").read_text())
    origin_metadata["artifacts"]["candidate_case"] = candidate.name
    (origin_dir / "run.json").write_text(json.dumps(origin_metadata))
    candidate.write_text("schemaVersion: fsq.ai-test/v1\nname: original\nplatform: web\n---\n- clickOn:\n    target: {page: main, steps: [{kind: role, role: button, name: Save}]}\n")
    candidate.write_bytes(FsqCaseSerializer(build_capability_registry(platform="web").snapshot()).serialize(FsqCaseLoader().load_text(candidate.read_text(), candidate)))
    recording_path = origin_dir / "recording.json"
    recording = json.loads(recording_path.read_text())
    recording["draft"] = False
    recording_path.write_text(json.dumps(recording))
    before = candidate.read_bytes(), recording_path.read_bytes()
    saved = save_recorded_case(CaseSaveRequest(candidate_path=candidate, destination_directory=tmp_path / "cases", platform="web", case_name="renamed"))
    assert saved.outcome == "created"
    current_dir = current._run_dirs["current"]
    (current_dir / "case.yaml").write_bytes(saved.path.read_bytes())
    metadata = json.loads((current_dir / "run.json").read_text())
    metadata["source"]["digest"] = hashlib.sha256(saved.path.read_bytes()).hexdigest()
    (current_dir / "run.json").write_text(json.dumps(metadata))
    origin = service.project(origin_dir)
    combined = service.project(current_dir, baseline=origin, related_runs=[origin])
    assert combined.comparison["baseline_current"]["steps"][0]["status"] == "matched"
    assert combined.lineage["related_runs"][0]["run"]["run_id"] == "origin"
    exported = service.export(combined, RunReportExportOptions(format="bundle", destination=tmp_path / "renamed.zip"))
    assert exported.path.is_file()
    assert (candidate.read_bytes(), recording_path.read_bytes()) == before
    relation_path = origin_dir / "lineage.jsonl"
    relation = json.loads(relation_path.read_text().splitlines()[-1])
    relation["command_mapping"][0]["step_execution_id"] = "other"
    relation_path.write_text(json.dumps(relation) + "\n")
    tampered = service.project(origin_dir)
    with pytest.raises(ReportGenerationError):
        service.project(current_dir, baseline=tampered)


@pytest.mark.parametrize("tamper", [{"step_execution_id": "other"}, {"source_step_id": "origin:1"}, {"invocation_path": ["agent", "2"]}])
def test_mapping_origin_fields_must_match_retained_case_and_execution(tmp_path, tamper):
    service, report, baseline = _mapped_reports(tmp_path)
    assert service.compare(report, baseline)["steps"][0]["status"] == "matched"
    report.lineage["relations"][0]["command_mapping"][0].update(tamper)
    with pytest.raises(ReportGenerationError):
        service.compare(report, baseline)


def test_nonstep_inline_budget_has_inventory_and_visible_omission(tmp_path, monkeypatch):
    from fsq_agent.report import _static_html

    root, facts, bundle = _fixture(tmp_path)
    Image.new("RGB", (4, 4), "white").save(root / "extra.png")
    bundle["artifacts"].append({"artifact_id": "extra", "kind": "screenshot", "path": "extra.png"})
    report = RunReportService().project(root, facts, bundle)
    monkeypatch.setattr(_static_html, "MAX_INLINE_IMAGE_BYTES", 1)
    output = RunReportService().export(report, RunReportExportOptions(format="html", destination=tmp_path / "limited.html"))
    text = output.path.read_text()
    assert "inline_image_budget" in text
    assert "Image omitted" in text


def test_normalized_runner_sequence_is_not_renumbered_by_report(tmp_path):
    root, facts, bundle = _fixture(tmp_path)
    bundle["events"] = [{"run_id": root.name, "event_type": "step_start", "step_id": "s1", "sequence": 3}, {"run_id": root.name, "event_type": "step_finish", "step_id": "s1", "sequence": 11}]
    report = RunReportService().project(root, facts, bundle)
    assert [event["sequence"] for event in report.logs] == [3, 11]
