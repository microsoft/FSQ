# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import hashlib
import io
import json
from types import SimpleNamespace

import pytest

from fsq_agent.adapters.control_plane import _evidence, _server
from fsq_agent.application import ShowRunRequest, runs
from fsq_agent.core import EvidenceRecorder
from fsq_agent.harnesses._android import AndroidHarness
from fsq_agent.models import ExecutableStep, RunnerEvent, RunnerStepResult, RunReportExportOptions
from fsq_agent.report import RunReportService
from tests.test_report_audit import _fixture
from tests.test_runs_audit_regressions import _scope


def test_download_does_not_stream_replaced_validated_file(tmp_path):
    path = tmp_path / "report.txt"
    path.write_bytes(b"safe")
    resolved = SimpleNamespace(path=path, size=4, sha256=hashlib.sha256(b"safe").hexdigest(), mime_type="text/plain", filename="report.txt")
    body, headers = _server._history_file_response(resolved)
    external = tmp_path / "external.txt"
    external.write_bytes(b"EXTERNAL_CANARY")
    path.unlink()
    path.symlink_to(external)
    handler = object.__new__(_server._RequestHandler)
    handler.wfile = io.BytesIO()
    handler.path = "/api/control-plane/history/file"
    handler.send_response = lambda *args: None
    handler.send_header = lambda *args: None
    handler.end_headers = lambda: None
    try:
        handler._send(200, body, headers)
    except (OSError, ValueError):
        pass
    assert b"EXTERNAL_CANARY" not in handler.wfile.getvalue()


def test_live_detail_keys_normalize_before_redaction():
    value = {"accessToken": "TOKEN_CANARY", "clientSecret": "SECRET_CANARY"}
    projected = _evidence._safe_details(value, _evidence.safe_text)
    assert "CANARY" not in json.dumps(projected)


def test_history_omits_private_configuration_maps(tmp_path, monkeypatch):
    _scope(monkeypatch, tmp_path)
    directory = tmp_path / ".fsq/runs/web/run"
    directory.mkdir(parents=True)
    _, facts, _ = _fixture(tmp_path, "fixture")
    facts.update(run_id="run", workspace={"name": "demo"}, provenance={"privateValues": {"CUSTOM": "PRIVATE_CANARY"}, "environment": {"CUSTOM": "ENV_CANARY"}})
    (directory / "run.json").write_text(json.dumps(facts))
    result = runs.show_run(ShowRunRequest(current_directory=tmp_path, run_id="run"))
    assert "CANARY" not in result.model_dump_json()


def test_planned_container_metadata_survives_recovery(tmp_path):
    recorder = EvidenceRecorder(run_id="run", output_dir=tmp_path)
    recorder.record_planned_step(ExecutableStep(step_id="container", source_step_id="source", kind="setup", action_name="runCase", metadata={"structural": True, "hook_action_name": "runCase"}))
    recovered = EvidenceRecorder.recover_bundle(tmp_path)
    assert recovered.steps[0].metadata["structural"] is True
    assert recovered.steps[0].metadata["hook_action_name"] == "runCase"


def test_journal_result_cannot_precede_acknowledged_start(tmp_path):
    recorder = EvidenceRecorder(run_id="run", output_dir=tmp_path)
    recorder.record_event(RunnerEvent(run_id="run", event_type="step_start", step_id="s", step_execution_id="s"))
    recorder.record_step_result(RunnerStepResult(step_id="s", step_execution_id="s", status="passed"))
    path = tmp_path / "evidence-events.jsonl"
    records = [json.loads(line) for line in path.read_text().splitlines()][::-1]
    for index, record in enumerate(records, 1):
        record["sequence"] = index
    path.write_text("".join(json.dumps(record) + chr(10) for record in records))
    (tmp_path / "evidence-manifest.json").unlink()
    with pytest.raises(ValueError, match=r"start|order"):
        EvidenceRecorder.recover_bundle(tmp_path)


def test_undecorated_android_driver_does_not_adopt_another_backends_capabilities():
    harness = AndroidHarness(driver=SimpleNamespace())
    assert not any(schema.name in {"tap_on", "tapOn", "launch_app"} for schema in harness.action_space())


@pytest.mark.parametrize("export_format", ["json", "html"])
def test_optional_text_does_not_block_export(tmp_path, export_format):
    root, facts, bundle = _fixture(tmp_path)
    for index in range(5):
        name = f"text-{index}.txt"
        (root / name).write_text("<&>" * 166667)
        bundle["artifacts"].append({"artifact_id": name, "kind": "text", "path": name})
    service = RunReportService()
    report = service.project(root, facts, bundle)
    result = service.export(report, RunReportExportOptions(format=export_format, destination=tmp_path / f"report.{export_format}"))
    assert result.path.is_file()
    assert report.run["gate"]["status"] == "passed"
