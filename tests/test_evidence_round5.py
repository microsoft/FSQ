# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.


import pytest

from fsq_agent.core import ArtifactStore, EvidenceRecorder, StepRunner
from fsq_agent.models import ExecutableStep, RunMetadata, RunResultSummary, RunStepCounts


@pytest.mark.parametrize(
    "params",
    [
        {"password": "CREDENTIAL_CANARY"},
        {"headers": {"Authorization": "Bearer CREDENTIAL_CANARY", "Cookie": "a=x; session=CREDENTIAL_CANARY"}},
        {"nested": '{"password":"CREDENTIAL_CANARY"}'},
        {"%70assword": "CREDENTIAL_CANARY"},
    ],
)
def test_invalid_unregistered_params_never_persist_credentials(tmp_path, params):
    from test_step_runner import SuccessfulHarness

    runner = StepRunner(SuccessfulHarness(), evidence_sink=EvidenceRecorder(run_id="run", output_dir=tmp_path))
    runner.run_step("run", ExecutableStep(step_id="invalid", kind="diagnostic", action_name="unknown", params=params))
    assert "CREDENTIAL_CANARY" not in (tmp_path / "evidence-events.jsonl").read_text()
    assert "CREDENTIAL_CANARY" not in (tmp_path / "evidence-manifest.json").read_text()


def test_v1_summary_model_instances_preserve_known_counts():
    counts = RunStepCounts(total=1, failed=1, attempt_count=None)
    summary = RunResultSummary(summary="failed", steps=counts)
    metadata = RunMetadata(
        schema_version="fsq.run/v1",
        run_id="old",
        workspace={"name": "test"},
        platform="web",
        mode="strict",
        status="failed",
        started_at=None,
        source={"kind": "case", "case_id": "old"},
        result=summary,
    )
    assert metadata.result.steps is not None
    assert metadata.result.steps.failed == 1
    assert metadata.result.steps.attempt_count is None


def test_assertion_invoke_screenshot_acknowledged_before_evaluator_failure(tmp_path):
    from test_web_harness import FakeWebDriver

    from fsq_agent._capability_bootstrap import build_capability_registry
    from fsq_agent.harnesses._web import WebHarness

    class Evaluator:
        seen = None

        def evaluate(self, request):
            bundle = EvidenceRecorder.recover_bundle(tmp_path)
            self.seen = [ref for ref in bundle.artifacts if ref.phase == "invoke"]
            raise RuntimeError("evaluator failed")

    evaluator = Evaluator()
    harness = WebHarness(driver=FakeWebDriver(), artifact_store=ArtifactStore(tmp_path), ai_assertion_evaluator=evaluator)
    recorder = EvidenceRecorder(run_id="run", output_dir=tmp_path)
    runner = StepRunner(harness, capability_registry=build_capability_registry(platform="web"), evidence_sink=recorder)
    result = runner.run_step("run", ExecutableStep(step_id="assert", kind="assertion", action_name="assertWithAI", params={"prompt": "Check"}))
    assert evaluator.seen is not None
    assert len(evaluator.seen) == 1
    assert evaluator.seen[0].kind == "screenshot"
    assert result.status == "failed"
    assert any(ref.artifact_id == evaluator.seen[0].artifact_id for ref in recorder.build_bundle().artifacts)


@pytest.mark.parametrize(
    "text",
    [
        "Authorization: Bearer CREDENTIAL_CANARY",
        "Proxy-Authorization=Bearer CREDENTIAL_CANARY",
        "Cookie: first=other; session=CREDENTIAL_CANARY",
        "Set-Cookie: session=CREDENTIAL_CANARY; HttpOnly",
        '{"headers":{"Authorization":"Bearer CREDENTIAL_CANARY"}}',
    ],
)
def test_direct_recorder_redacts_complete_header_and_json_string(tmp_path, text):
    from fsq_agent.models import RunnerEvent

    recorder = EvidenceRecorder(run_id="run", output_dir=tmp_path)
    recorder.record_event(RunnerEvent(run_id="run", event_type="step_error", payload={"message": text}))
    assert "CREDENTIAL_CANARY" not in (tmp_path / "evidence-events.jsonl").read_text()


def test_runtime_secret_reference_name_is_not_removed_by_structure_sanitizer(tmp_path):
    from fsq_agent.models import RunnerEvent

    recorder = EvidenceRecorder(run_id="run", output_dir=tmp_path)
    recorder.record_event(RunnerEvent(run_id="run", event_type="step_start", payload={"params": {"textType": "runtimeSecret", "text": "PASSWORD"}, "required_runtime_secret_names": ["PASSWORD"]}))
    assert "PASSWORD" in (tmp_path / "evidence-events.jsonl").read_text()


@pytest.mark.parametrize(
    "credential",
    [
        {"password": "CREDENTIAL_CANARY"},
        {"clientSecret": "CREDENTIAL_CANARY"},
        {"Proxy-Authorization": "Bearer CREDENTIAL_CANARY"},
        {"privateValues": {"PASSWORD": "CREDENTIAL_CANARY"}},
        {"note": '{"accessToken":"CREDENTIAL_CANARY"}'},
        {"note": "Set-Cookie: first=ok; session=CREDENTIAL_CANARY"},
    ],
)
def test_frozen_verifier_credentials_never_persist(tmp_path, credential):
    from fsq_agent.execution import RunLifecycleService, allocate_run
    from fsq_agent.models import EvidenceBundle, RunnerStepResult, RunSource

    metadata = allocate_run(workspace=tmp_path, workspace_name="test", platform="web", mode="explore", source_id="run", source=RunSource(kind="goal", goal_summary="test"))
    run = tmp_path / ".fsq/runs/web" / metadata.run_id
    frozen = RunLifecycleService.freeze(
        run,
        metadata,
        bundle=EvidenceBundle(bundle_id="e", run_id=metadata.run_id, completeness="complete", steps=[RunnerStepResult(step_id="one", status="passed")]),
        verification={"status": "success", "summary": "Verified", "details": credential, "required_runtime_secret_names": ["PASSWORD"]},
    )
    assert "CREDENTIAL_CANARY" not in (run / "execution-result.json").read_text()
    assert frozen.verification["status"] == "success"
    assert frozen.verification["required_runtime_secret_names"] == ["PASSWORD"]


@pytest.mark.parametrize("key", ["pwd", "Proxy-Authorization", "Set-Cookie", "privateValues", "clientSecret", "accessToken", "%70assword"])
def test_source_credential_key_variants_are_removed(tmp_path, key):
    from fsq_agent.execution import RunLifecycleService

    safe = RunLifecycleService.safe_source_text(f'"{key}": "CREDENTIAL_CANARY"\nallowed_names: [PASSWORD]\n')
    assert "CREDENTIAL_CANARY" not in safe
    assert "PASSWORD" in safe
