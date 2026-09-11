# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import json
import zipfile

import pytest

from fsq_agent.adapters.control_plane._evidence import read_step_artifacts
from fsq_agent.agent._events import RunEventEmitter
from fsq_agent.config import Settings
from fsq_agent.core import ArtifactStore
from fsq_agent.execution import DynamicExecutionRequest, DynamicExecutionService, RunLifecycleService
from fsq_agent.models import DynamicAgentOutcome, RunEvent, RunReportExportOptions, Task, VerificationResult
from fsq_agent.observation import ExecutionLogger
from fsq_agent.report import RunReportService
from tests.test_durable_evidence import JournalHarness, _runner, _step
from tests.test_report_audit import _fixture, _write_frozen


@pytest.mark.parametrize("message", ["Authorization: Bearer PRIVATE_CANARY", "password=PRIVATE_CANARY"])
def test_json_artifact_is_valid_after_redaction(tmp_path, message):
    ref = ArtifactStore(tmp_path).write_json(kind="ui_snapshot", step_id="s", phase="prepare", name="snapshot", payload={"message": message, "other": 1})
    content = (tmp_path / ref.path).read_text()
    assert json.loads(content)["other"] == 1
    assert "PRIVATE_CANARY" not in content


@pytest.mark.parametrize("url", ["https://example.test/?%74oken=PRIVATE_CANARY", "https://PRIVATE_CANARY@example.test/path"])
async def test_progress_url_credentials_are_safe(tmp_path, url):
    await RunEventEmitter(logger=ExecutionLogger(tmp_path)).emit(RunEvent(run_id="progress", task_id="task", type="tool_call_started", title="Call", tool_arguments={"url": url}))
    assert "PRIVATE_CANARY" not in (tmp_path / "progress/events.jsonl").read_text()


def test_source_and_frozen_text_remove_bare_auth():
    assert "PRIVATE_CANARY" not in RunLifecycleService.safe_source_text("Use Bearer PRIVATE_CANARY for login")
    assert "PRIVATE_CANARY" not in json.dumps(RunLifecycleService.safe_value({"summary": "Basic PRIVATE_CANARY"}))


@pytest.mark.parametrize("payload", [{"safe_replay_params": {"text": "password=PRIVATE_CANARY"}}, {"environment": {"CUSTOM": "PRIVATE_CANARY"}}, {"config": {"env": {"CUSTOM": "PRIVATE_CANARY"}}}])
def test_bundle_structured_copies_apply_mandatory_redaction(tmp_path, payload):
    run, facts, bundle = _fixture(tmp_path)
    (run / "legacy.json").write_text(json.dumps(payload))
    bundle["artifacts"].append({"artifact_id": "legacy", "kind": "json", "path": "legacy.json"})
    service = RunReportService()
    output = service.export(service.project(run, facts, bundle), RunReportExportOptions(format="bundle", destination=tmp_path / "bundle.zip"))
    with zipfile.ZipFile(output.path) as archive:
        assert b"PRIVATE_CANARY" not in archive.read("runs/run/legacy.json")


async def test_verified_existing_state_needs_no_capability_evidence(tmp_path):
    class Agent:
        async def run_in_context(self, task, context, event_sink=None, **kwargs):
            return DynamicAgentOutcome(task=task, steps=[], verification=VerificationResult(status="success", summary="Existing state verified"))

    settings = Settings()
    settings.workspace.root_dir = tmp_path
    settings.output.runs_dir = tmp_path / ".fsq/runs/web"
    settings.harness.platform = "web"
    outcome = await DynamicExecutionService(agent=Agent()).execute(DynamicExecutionRequest(task=Task(description="Verify existing state"), settings=settings))
    frozen = RunLifecycleService.load_result(settings.output.runs_dir / outcome.task_result.report.run_id)
    assert frozen.outcome == "success"
    assert frozen.evidence["status"] == "not_applicable"
    directory = settings.output.runs_dir / outcome.task_result.report.run_id
    report = RunReportService().project(directory, normalized_evidence=RunLifecycleService.read_evidence(directory))
    assert report.run["gate"]["status"] == "passed"


def test_verified_empty_frozen_counts_remain_known(tmp_path):
    run, facts, bundle = _fixture(tmp_path, mode="explore")
    bundle["steps"] = []
    _write_frozen(run, steps=[], counts={"total": 0, "passed": 0, "attempt_count": 0}, verification={"status": "success"})
    report = RunReportService().project(run, facts, bundle)
    assert report.execution["accounting_complete"] is True
    assert report.run["gate"]["status"] == "passed"


@pytest.mark.parametrize("category", ["assertion_error", "harness_error"])
def test_recovered_strict_attempt_is_not_a_blocking_failure(tmp_path, category):
    run, facts, bundle = _fixture(tmp_path)
    first = bundle["steps"][0]
    first.update(status="failed", action_status="failed", failure_category=category)
    bundle["steps"].append({**first, "step_id": "s2", "step_execution_id": "s2", "status": "passed", "action_status": "passed", "failure_category": None, "attempt_index": 2})
    _write_frozen(run, steps=bundle["steps"], counts={"total": 1, "passed": 1, "attempt_count": 2})
    report = RunReportService().project(run, facts, bundle)
    assert report.run["gate"]["status"] == "passed"
    assert report.execution["first_failure"] is None
    assert report.execution["recovered_failures"]


@pytest.mark.parametrize("legacy", [False, True])
def test_terminal_reads_journal_without_current_checkpoint(tmp_path, legacy):
    runner, _ = _runner(tmp_path, JournalHarness(tmp_path))
    step = runner.run_step("run", _step())
    checkpoint = tmp_path / "evidence-manifest.json"
    checkpoint.unlink()
    if legacy:
        checkpoint.write_text(json.dumps({"schema_version": "1.0", "bundle_id": "old", "run_id": "run", "artifacts": []}))
    assert len(read_step_artifacts(tmp_path, step.step_id)["artifacts"]) == 4


def test_terminal_hash_mismatch_never_emits_raw_body(tmp_path):
    (tmp_path / "ui.txt").write_text("password=PRIVATE_CANARY")
    (tmp_path / "evidence-manifest.json").write_text(
        json.dumps({"schema_version": "1.0", "run_id": "run", "artifacts": [{"artifact_id": "ui", "kind": "ui_snapshot", "path": "ui.txt", "step_id": "s1", "sha256": "0" * 64}]})
    )
    value = read_step_artifacts(tmp_path, "s1")
    assert "PRIVATE_CANARY" not in json.dumps(value)
    assert value["artifacts"][0]["availability"] == "unavailable"
