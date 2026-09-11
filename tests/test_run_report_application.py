# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from fsq_agent.application import ApplicationError, ApplicationErrorCode, ExportRunReportRequest, GetRunReportRequest, export_run_report, get_run_report, runs
from fsq_agent.execution import RunLifecycleService, RunSource, allocate_run
from fsq_agent.models import EvidenceBundle, RunnerStepResult


def _scope(root: Path, monkeypatch):
    monkeypatch.setattr(runs, "list_workspace_registry", lambda: [SimpleNamespace(name="demo", root_path=root)])
    monkeypatch.setattr(runs, "inspect_registered_workspace", lambda _name, *args, **kwargs: SimpleNamespace(platforms=[SimpleNamespace(platform="web")]))
    metadata = allocate_run(workspace=root, workspace_name="demo", platform="web", source_id="search", mode="strict", source=RunSource(kind="case", case_id="search"))
    directory = root / ".fsq/runs/web" / metadata.run_id
    service = RunLifecycleService()
    metadata = service.snapshot_sources(directory, metadata, sources={"case": b"name: search\n"})
    frozen = service.freeze(
        directory,
        metadata,
        bundle=EvidenceBundle(bundle_id="e", run_id=metadata.run_id, steps=[RunnerStepResult(step_id="one", status="failed", failure_category="assertion_error", error_message="Missing heading")]),
    )
    service.finalize(directory, metadata, execution_result=frozen)
    return metadata, directory


def test_failed_run_export_preserves_facts_and_needs_no_provider(tmp_path, monkeypatch):
    metadata, directory = _scope(tmp_path, monkeypatch)
    before = (directory / "run.json").read_bytes()
    report = get_run_report(GetRunReportRequest(workspace_name="demo", run_id=metadata.run_id, platform="web"))
    assert report.report.run["gate"]["status"] != "passed"
    result = export_run_report(ExportRunReportRequest(current_directory=tmp_path, run_id=metadata.run_id, format="json"))
    assert result.output_path.is_file()
    assert json.loads(result.output_path.read_text())["schema_version"] == "fsq.report/v1"
    assert (directory / "run.json").read_bytes() == before


def test_untrustworthy_source_export_maps_to_report_unavailable(tmp_path, monkeypatch):
    metadata, directory = _scope(tmp_path, monkeypatch)
    persisted = json.loads((directory / "run.json").read_text())
    (directory / persisted["source"]["snapshot_path"]).unlink()
    with pytest.raises(ApplicationError) as error:
        export_run_report(ExportRunReportRequest(current_directory=tmp_path, run_id=metadata.run_id, format="json"))
    assert error.value.code == ApplicationErrorCode.RUN_REPORT_UNAVAILABLE
    assert error.value.details["reason"] == "source_identity_unavailable"


def test_export_does_not_overwrite_explicit_destination(tmp_path, monkeypatch):
    metadata, _ = _scope(tmp_path, monkeypatch)
    output = tmp_path / "keep.json"
    output.write_text("unchanged")
    with pytest.raises(Exception, match="exists"):
        export_run_report(ExportRunReportRequest(current_directory=tmp_path, run_id=metadata.run_id, format="json", output_path=output))
    assert output.read_text() == "unchanged"


def test_scope_requires_exactly_one_selector():
    with pytest.raises(ValueError, match="exactly one"):
        GetRunReportRequest(current_directory=Path(), workspace_name="demo", run_id="run")


@pytest.mark.parametrize("legacy_schema", ["1.0", None])
@pytest.mark.parametrize("status", ["success", "failed"])
def test_recovered_legacy_coverage_does_not_invent_required_evidence_failure(tmp_path, monkeypatch, legacy_schema, status):
    metadata, directory = _scope(tmp_path, monkeypatch)
    raw = json.loads((directory / "run.json").read_text())
    raw.update(schema_version="fsq.run/v1", status=status, evidence={})
    (directory / "run.json").write_text(json.dumps(raw))
    (directory / "execution-result.json").unlink()
    manifest = {"bundle_id": "historical", "run_id": metadata.run_id, "steps": [{"step_id": "one", "status": "passed" if status == "success" else "failed"}]}
    if legacy_schema:
        manifest["schema_version"] = legacy_schema
    (directory / "evidence-manifest.json").write_text(json.dumps(manifest))
    before = {path: path.read_bytes() for path in directory.rglob("*") if path.is_file()}
    report = get_run_report(GetRunReportRequest(current_directory=tmp_path, run_id=metadata.run_id)).report
    assert report.execution["outcome"] == status
    assert report.evidence["availability"] == "unknown"
    assert report.evidence["required_missing"] == []
    assert "required_evidence_incomplete" not in report.run["gate"]["reasons"]
    assert {path: path.read_bytes() for path in before} == before


def test_source_artifact_size_mismatch_remains_unavailable(tmp_path, monkeypatch):
    from fsq_agent.application import ResolveRunArtifactRequest, resolve_run_artifact

    metadata, directory = _scope(tmp_path, monkeypatch)
    content = b"recorded artifact"
    (directory / "artifact.txt").write_bytes(content)
    artifact = {"artifact_id": "single", "kind": "text", "path": "artifact.txt", "size_bytes": len(content) + 1, "sha256": hashlib.sha256(content).hexdigest()}
    (directory / "evidence-manifest.json").write_text(json.dumps({"run_id": metadata.run_id, "artifacts": [artifact]}))
    report = get_run_report(GetRunReportRequest(current_directory=tmp_path, run_id=metadata.run_id)).report
    projected = next(item for item in report.artifacts if item["artifact_id"] == "single")
    assert projected["availability"] == "unavailable"
    assert projected["unavailable_reason"] == "size_mismatch"
    assert projected["size_bytes"] == len(content) + 1
    with pytest.raises(ApplicationError):
        resolve_run_artifact(ResolveRunArtifactRequest(current_directory=tmp_path, run_id=metadata.run_id, reference={"kind": "source", "artifact_id": "single"}))


def test_metadata_free_history_local_html_is_unverified_and_read_only(tmp_path, monkeypatch):
    from fsq_agent.application import GenerateRunHtmlRequest, generate_run_html

    metadata, directory = _scope(tmp_path, monkeypatch)
    (directory / "run.json").unlink()
    (directory / "execution-result.json").unlink()
    (directory / "report.json").write_text(json.dumps({"run_id": metadata.run_id, "verification": {"status": "success", "summary": "Historical result"}}))
    before = {path: path.read_bytes() for path in directory.rglob("*") if path.is_file()}
    generated = generate_run_html(GenerateRunHtmlRequest(current_directory=tmp_path, run_id=metadata.run_id))
    text = (tmp_path / generated.html_path).read_text(encoding="utf-8")
    assert "local compatibility view, not a verified source export" in text
    assert {path: path.read_bytes() for path in before} == before
    assert not (directory / "run.json").exists()
    with pytest.raises(ApplicationError) as error:
        export_run_report(ExportRunReportRequest(current_directory=tmp_path, run_id=metadata.run_id, format="json"))
    assert error.value.details["reason"] == "source_identity_unavailable"
