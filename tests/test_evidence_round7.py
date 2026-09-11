# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import json

import pytest

from fsq_agent.core import ArtifactStore, EvidenceRecorder, StepRunner
from fsq_agent.execution import RunLifecycleService
from fsq_agent.models import ExecutableStep, RunnerEvent


def test_windows_unknown_owner_inspection_never_calls_kill(tmp_path, monkeypatch):
    (tmp_path / "owner.json").write_text(json.dumps({"pid": 42, "process_start": "start"}))
    monkeypatch.setattr("fsq_agent.execution.runs._process_start", lambda pid: None)
    monkeypatch.setattr("sys.platform", "win32")
    calls = []
    monkeypatch.setattr("fsq_agent.execution.runs.os.kill", lambda *args: calls.append(args))
    assert RunLifecycleService.inspect_owner(tmp_path)["status"] == "unknown"
    assert calls == []


@pytest.mark.parametrize("text", ["password=CREDENTIAL_CANARY", "token: CREDENTIAL_CANARY", "Bearer CREDENTIAL_CANARY", "failed password='CREDENTIAL_CANARY'", "clientSecret=CREDENTIAL_CANARY"])
def test_core_textual_credentials_removed_from_all_persistence(tmp_path, text):
    from test_step_runner import SuccessfulHarness

    class Harness(SuccessfulHarness):
        def invoke_action(self, step, context):
            raise RuntimeError(text)

    recorder = EvidenceRecorder(run_id="run", output_dir=tmp_path)
    result = StepRunner(Harness(), evidence_sink=recorder).run_step("run", ExecutableStep(step_id="one", kind="diagnostic", action_name="unknown"))
    assert "CREDENTIAL_CANARY" not in result.model_dump_json()
    assert "CREDENTIAL_CANARY" not in (tmp_path / "evidence-events.jsonl").read_text()
    ref = ArtifactStore(tmp_path).write_text(kind="text", step_id="one", phase="invoke", name="log", text=text)
    assert "CREDENTIAL_CANARY" not in (tmp_path / ref.path).read_text()


def test_recovered_event_retains_original_journal_sequence(tmp_path):
    recorder = EvidenceRecorder(run_id="run", output_dir=tmp_path)
    recorder.record_event(RunnerEvent(run_id="run", event_type="session_start"))
    recorder.write_manifest()
    recorder.record_event(RunnerEvent(run_id="run", event_type="session_finish"))
    before = (tmp_path / "evidence-manifest.json").read_bytes()
    recovered = EvidenceRecorder.recover_bundle(tmp_path)
    assert [event.sequence for event in recovered.events] == [1, 2]
    assert (tmp_path / "evidence-manifest.json").read_bytes() == before


def test_capture_context_failure_after_passed_action_is_evidence_error(tmp_path):
    from test_durable_evidence import JournalHarness, _runner, _step

    class Harness(JournalHarness):
        def get_context(self):
            if self.invocations:
                raise RuntimeError("context lost")
            return super().get_context()

    runner, _ = _runner(tmp_path, Harness(tmp_path))
    result = runner.run_step("run", _step())
    assert result.action_status == "passed"
    assert result.failure_category == "artifact_error"
    assert result.evidence_errors


def test_ai_invoke_failed_capture_is_acknowledged(tmp_path):
    from test_web_harness import FakeWebDriver

    from fsq_agent._capability_bootstrap import build_capability_registry
    from fsq_agent.harnesses._web import WebHarness

    class Driver(FakeWebDriver):
        calls_to_screenshot = 0

        def screenshot(self, params=None):
            self.calls_to_screenshot += 1
            if self.calls_to_screenshot > 1:
                raise RuntimeError("capture unavailable")
            return b"before"

    class Evaluator:
        def evaluate(self, request):
            raise AssertionError("must not run without screenshot")

    recorder = EvidenceRecorder(run_id="run", output_dir=tmp_path)
    harness = WebHarness(driver=Driver(), artifact_store=ArtifactStore(tmp_path), ai_assertion_evaluator=Evaluator())
    result = StepRunner(harness, capability_registry=build_capability_registry(platform="web"), evidence_sink=recorder).run_step(
        "run", ExecutableStep(step_id="assert", kind="assertion", action_name="assertWithAI", params={"prompt": "check"})
    )
    failed = [event for event in recorder.build_bundle().events if event.event_type == "artifact_failed" and event.phase == "invoke"]
    assert len(failed) == 1
    assert result.evidence_errors
