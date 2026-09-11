# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import json
import zipfile

import pytest

from fsq_agent.models import EvidenceBundle, ReportGenerationError, RunnerStepResult, RunReportExportOptions
from fsq_agent.report import CoreEvidenceReportGenerator, RunReportService
from tests.test_report_audit import _fixture


@pytest.mark.parametrize(
    "message",
    [
        "Bearer CANARY_CREDENTIAL",
        "Basic CANARY_CREDENTIAL",
        "id_token=CANARY_CREDENTIAL",
        "client_secret=CANARY_CREDENTIAL",
        "accessToken=CANARY_CREDENTIAL",
        '{"clientSecret":"CANARY_CREDENTIAL"}',
        '{"safe_replay_params":{"x":"{\\"privateValues\\":\\"CANARY_CREDENTIAL\\"}"}}',
    ],
)
def test_report_credential_representations_are_consistent(tmp_path, message):
    root, facts, bundle = _fixture(tmp_path)
    (root / "events.jsonl").write_text(json.dumps({"message": message}))
    report = RunReportService().project(root, facts, bundle)
    assert "CANARY_CREDENTIAL" not in report.model_dump_json()
    output = RunReportService().export(report, RunReportExportOptions(format="bundle", destination=tmp_path / "safe.zip"))
    with zipfile.ZipFile(output.path) as archive:
        assert all(b"CANARY_CREDENTIAL" not in archive.read(name) for name in archive.namelist())


def test_first_parsed_fingerprint_cannot_be_replaced_by_artifact_reread(tmp_path, monkeypatch):
    root, facts, bundle = _fixture(tmp_path)
    log = root / "events.jsonl"
    log.write_text('{"message":"old"}')
    service = RunReportService()
    original = service._artifacts

    def changed(*args, **kwargs):
        log.write_text('{"message":"new"}')
        return original(*args, **kwargs)

    monkeypatch.setattr(service, "_artifacts", changed)
    with pytest.raises(ReportGenerationError, match="changed"):
        service.project(root, facts, bundle)


def test_supplied_missing_capture_facts_survive_projection(tmp_path):
    root, facts, bundle = _fixture(tmp_path)
    (root / "execution-result.json").unlink()
    facts["schema_version"] = "fsq.run/v1"
    facts["evidence"] = {"status": "partial", "required_missing": [{"run_id": root.name, "step_execution_id": "s1", "kind": "screenshot", "phase": "prepare"}]}
    (root / "run.json").write_text(json.dumps(facts))
    report = RunReportService().project(root, facts, bundle)
    assert report.evidence["required_missing"]
    assert report.run["gate"]["status"] == "error"


def test_historical_absent_coverage_means_unknown_comparison(tmp_path):
    root, facts, bundle = _fixture(tmp_path)
    bundle["schema_version"] = "1.0"
    for name, phase in [("before.txt", "prepare"), ("after.txt", "finalize")]:
        (root / name).write_text("same")
        bundle["artifacts"].append({"artifact_id": name, "kind": "ui_snapshot", "path": name, "step_execution_id": "s1", "phase": phase})
    report = RunReportService().project(root, facts, bundle)
    assert report.artifacts[0]["coverage"]["status"] == "unknown"
    assert report.comparison["before_after"][0]["status"] == "incomplete"


def test_unmeasured_reason_overrides_timestamp_in_all_timing_scopes(tmp_path):
    root, facts, bundle = _fixture(tmp_path)
    step = bundle["steps"][0]
    step.update(duration_ms=0, started_at="2026-09-08T00:00:00Z", unavailable_reason="unmeasured", metadata={})
    step["phase_reports"] = [{"step_id": "s1", "phase": "prepare", "status": "passed", "duration_ms": 0, "started_at": "2026-09-08T00:00:00Z", "unavailable_reason": "unmeasured"}]
    report = RunReportService().project(root, facts, bundle)
    assert report.steps[0]["metrics"]["duration_ms"]["value"] is None
    assert report.steps[0]["metrics"]["prepare_ms"]["value"] is None


def test_compatibility_core_running_status_counts_as_incomplete(tmp_path):
    bundle = EvidenceBundle(run_id="core", bundle_id="core", steps=[RunnerStepResult(step_id="one", status="passed"), RunnerStepResult(step_id="two", status="running")])
    manifest = tmp_path / "evidence-manifest.json"
    manifest.write_text(bundle.model_dump_json())
    report = CoreEvidenceReportGenerator().generate_from_manifest(manifest)
    summary = json.loads(report.path.with_suffix(".json").read_text())["summary"]
    assert summary["status"] == "inconclusive"
    assert summary["incomplete_steps"] == 1
    assert summary["step_count"] == 2


def test_aggregate_optional_log_budget_is_bounded_with_source_reference(tmp_path, monkeypatch):
    from fsq_agent.report import _run_report

    root, facts, bundle = _fixture(tmp_path)
    (root / "events.jsonl").write_text("\n".join(json.dumps({"sequence": i, "message": "x" * 4000}) for i in range(200)))
    monkeypatch.setattr(_run_report, "MAX_TEXT_BYTES", 30000)
    report = RunReportService().project(root, facts, bundle)
    assert len(report.logs) < 200
    available = report.run["display_omissions"]["logs"]
    assert available["original_count"] == 200
    assert available["original_size_bytes"] > 30000
    assert available["full_artifacts"] == [{"run_id": root.name, "artifact_id": "runtime-events"}]


def test_nested_optional_logs_share_budget_and_keep_original_metadata(tmp_path, monkeypatch):
    from fsq_agent.report import _run_report

    root, facts, bundle = _fixture(tmp_path)
    other, other_facts, other_bundle = _fixture(tmp_path, "baseline")
    (other / "events.jsonl").write_text("\n".join(json.dumps({"sequence": i, "message": "x" * 4000}) for i in range(200)))
    service = RunReportService()
    baseline = service.project(other, other_facts, other_bundle)
    monkeypatch.setattr(_run_report, "MAX_TEXT_BYTES", 45000)
    report = service.project(root, facts, bundle, baseline=baseline)
    nested = report.comparison["baseline_report"]
    assert len(nested["logs"]) < 200
    assert nested["run"]["display_omissions"]["logs"]["original_count"] == 200
    assert nested["run"]["display_omissions"]["logs"]["full_artifacts"] == [{"run_id": "baseline", "artifact_id": "runtime-events"}]


def test_runtime_secret_name_metadata_is_not_removed_by_camel_sanitizer(tmp_path):
    root, facts, bundle = _fixture(tmp_path)
    raw = {"safe_replay_params": {"textType": "runtimeSecret", "text": "WORKSPACE_LOGIN_PASSWORD"}, "required_runtime_secret_names": ["WORKSPACE_LOGIN_PASSWORD"]}
    (root / "names.json").write_text(json.dumps(raw))
    bundle["artifacts"].append({"artifact_id": "names", "kind": "json", "path": "names.json"})
    report = RunReportService().project(root, facts, bundle)
    output = RunReportService().export(report, RunReportExportOptions(format="bundle", destination=tmp_path / "names.zip"))
    with zipfile.ZipFile(output.path) as archive:
        assert json.loads(archive.read(f"runs/{root.name}/names.json")) == raw
