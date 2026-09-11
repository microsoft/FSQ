# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import json

import pytest
from pydantic import BaseModel, ValidationError

from fsq_agent.core import ArtifactStore, CapabilityRegistry, EvidenceRecorder, RuntimeSecretStore, StepRunner
from fsq_agent.execution import RunLifecycleService, allocate_run, run_fsq_core_case
from fsq_agent.models import (
    CapabilityDefinition,
    EvidenceBundle,
    ExecutableStep,
    HarnessActionResult,
    HarnessContext,
    RunExecutionResult,
    RunMetadata,
    RunnerEvent,
    RunnerStepResult,
    RunSource,
)


def test_json_escaped_secret_is_redacted_in_payload_and_coverage(tmp_path):
    secret = 'quote"slash\\newline\nvalue'  # noqa: S105 - synthetic redaction fixture.
    store = ArtifactStore(tmp_path, secret_values=(secret,))
    ref = store.write_json(kind="ui_snapshot", step_id="one", phase="prepare", name="snapshot", payload={"xml": secret, "coverage": {"reason": secret}})
    saved = json.loads((tmp_path / ref.path).read_text())
    assert saved["xml"] == "***"
    assert ref.metadata["coverage"]["reason"] == "***"
    assert ref.metadata["redacted"] is True


@pytest.mark.parametrize("field", ["token", "access_token", "refresh_token", "id_token"])
def test_source_credentials_are_redacted_before_digest(tmp_path, field):
    metadata = allocate_run(workspace=tmp_path, workspace_name="test", platform="web", mode="strict", source_id="test", source=RunSource(kind="case", case_id="test"))
    run = tmp_path / ".fsq/runs/web" / metadata.run_id
    source = f"properties:\n  {field}: FAKE_CREDENTIAL\n"
    result = RunLifecycleService.snapshot_sources(run, metadata, sources={"case": source.encode()})
    assert "FAKE_CREDENTIAL" not in (run / result.source.snapshot_path).read_text()
    assert result.provenance["sources"][0]["transformed"] is True


def test_skipped_only_cannot_freeze_success(tmp_path):
    metadata = allocate_run(workspace=tmp_path, workspace_name="test", platform="web", mode="strict", source_id="test", source=RunSource(kind="case", case_id="test"))
    run = tmp_path / ".fsq/runs/web" / metadata.run_id
    bundle = EvidenceBundle(bundle_id="e", run_id=metadata.run_id, completeness="complete", steps=[RunnerStepResult(step_id="not-run", status="skipped", skip_reason="blocked")])
    frozen = RunLifecycleService.freeze(run, metadata, bundle=bundle)
    assert frozen.outcome == "inconclusive"
    with pytest.raises(ValidationError, match="success"):
        RunExecutionResult.model_validate({**frozen.model_dump(), "outcome": "success"})


def test_bundle_rejects_unknown_schema():
    with pytest.raises(ValidationError):
        EvidenceBundle(bundle_id="e", run_id="r", schema_version="fsq.evidence/v999")


def test_no_journal_checkpoint_validates_execution_identity(tmp_path):
    bundle = EvidenceBundle(
        bundle_id="e",
        run_id="run",
        completeness="complete",
        events=[RunnerEvent(run_id="run", event_type="step_start", step_id="a", step_execution_id="a", source_step_id="source")],
        steps=[
            RunnerStepResult(step_id="a", step_execution_id="a", source_step_id="source", status="passed"),
            RunnerStepResult(step_id="a", step_execution_id="a", source_step_id="other", status="failed"),
        ],
    )
    (tmp_path / "evidence-manifest.json").write_text(bundle.model_dump_json())
    with pytest.raises(ValueError, match="identity"):
        EvidenceRecorder.recover_bundle(tmp_path)


def test_missing_old_counts_and_attempts_remain_unknown():
    base = {
        "schema_version": "fsq.run/v1",
        "run_id": "old",
        "workspace": {"name": "test"},
        "platform": "web",
        "mode": "strict",
        "status": "success",
        "started_at": None,
        "source": {"kind": "case", "case_id": "old"},
    }
    assert RunMetadata.model_validate(base).result.steps is None
    counted = RunMetadata.model_validate({**base, "result": {"steps": {"total": 1, "passed": 1}}})
    assert counted.result.steps.attempt_count is None


def test_sensitive_invalid_shape_fails_without_persisting_output(tmp_path):
    class Params(BaseModel):
        pass

    class Harness:
        def get_context(self):
            return HarnessContext(platform="web")

        def before_action(self, step, context):
            pass

        def invoke_action(self, step, context):
            return HarnessActionResult(status="passed", action_name=step.action_name, output={"invalid_shape": "never-persist"})

        def after_action(self, step, context, result):
            pass

        def classify_error(self, error, phase, step):
            return "action_error"

    runner = StepRunner(
        Harness(),
        capability_registry=CapabilityRegistry.from_definitions([CapabilityDefinition(name="secret", executor_kind="driver", params_model=Params, sensitivity=True)]),
        evidence_sink=EvidenceRecorder(run_id="run", output_dir=tmp_path),
    )
    result = runner.run_step("run", ExecutableStep(step_id="one", kind="diagnostic", action_name="secret"))
    assert result.status == "failed"
    assert result.failure_category == "configuration_error"
    assert "never-persist" not in (tmp_path / "evidence-events.jsonl").read_text()


def test_whole_sequence_secret_preflight_prevents_earlier_actions(tmp_path):
    from test_cli_core_execution import CliCoreHarness

    harness = CliCoreHarness()
    with pytest.raises(Exception, match="secret"):
        run_fsq_core_case(
            case_path=tmp_path / "explicit.fsq.yaml",
            harness=harness,
            output_dir=tmp_path / "run",
            run_id="run",
            runtime_secret_store=RuntimeSecretStore.empty(),
            steps=[
                ExecutableStep(step_id="launch", kind="setup", action_name="launchApp"),
                ExecutableStep(step_id="input", kind="action", action_name="inputText", params={"target": "Email", "text": "MISSING", "textType": "runtimeSecret"}),
            ],
        )
    assert harness.actions == []


def test_fresh_context_failure_records_both_required_capture_failures(tmp_path):
    from test_durable_evidence import JournalHarness, _runner, _step

    class ContextFailure(JournalHarness):
        def get_context(self):
            if self.invocations:
                raise RuntimeError("context unavailable after action")
            return super().get_context()

    runner, recorder = _runner(tmp_path, ContextFailure(tmp_path, failure=True))
    result = runner.run_step("run", _step())
    assert result.failure_category == "assertion_error"
    assert len(result.evidence_errors) == 2
    assert {ref.kind for ref in result.phase_reports[-1].artifact_refs} == {"screenshot", "ui_snapshot"}
    assert recorder.build_bundle().completeness == "partial"


def test_deterministic_known_values_removed_from_source_and_journal(tmp_path):
    from test_cli_core_execution import CliCoreHarness

    path = tmp_path / "source.fsq.yaml"
    path.write_text("# private literal: QUOTED_SECRET\nschemaVersion: fsq.ai-test/v1\nname: Test\nplatform: android\n---\n- launchApp: {}\n")
    run = tmp_path / "run"
    run_fsq_core_case(case_path=path, harness=CliCoreHarness(), output_dir=run, run_id="run", runtime_secret_store=RuntimeSecretStore(["PASSWORD"], {"PASSWORD": "QUOTED_SECRET"}))
    for item in run.rglob("*"):
        if item.is_file():
            assert "QUOTED_SECRET" not in item.read_text()


def test_normal_step_cancellation_does_not_block_teardown(tmp_path):
    import asyncio

    from test_cli_core_execution import CliCoreHarness

    from fsq_agent.core import StepSequenceRunner

    harness = CliCoreHarness()
    runner = StepRunner(harness)
    checks = []

    def cancelled():
        checks.append(True)
        if len(checks) > 1:
            raise asyncio.CancelledError

    sequence = StepSequenceRunner(runner, EvidenceRecorder(run_id="run", output_dir=tmp_path), cancellation_check=cancelled)
    with pytest.raises(asyncio.CancelledError):
        sequence.run_steps(
            "run",
            [ExecutableStep(step_id="normal1", kind="diagnostic", action_name="first"), ExecutableStep(step_id="normal2", kind="diagnostic", action_name="second")],
            teardown_steps=[ExecutableStep(step_id="cleanup", kind="teardown", action_name="cleanup")],
        )
    assert harness.actions == ["first", "cleanup"]


def test_deterministic_snapshot_failure_preserves_allocated_identity(tmp_path, monkeypatch):
    from test_cli_core_execution import CliCoreHarness

    from fsq_agent.models import ToolExecutionError

    def fail(*args, **kwargs):
        raise OSError("private file detail")

    monkeypatch.setattr(RunLifecycleService, "snapshot_sources", fail)
    with pytest.raises(ToolExecutionError) as caught:
        run_fsq_core_case(
            case_path=tmp_path / "explicit.fsq.yaml",
            harness=CliCoreHarness(),
            output_dir=tmp_path / "run",
            run_id="allocated",
            steps=[ExecutableStep(step_id="one", kind="diagnostic", action_name="waitMs", params={"duration_ms": 1})],
        )
    assert caught.value.context["run_id"] == "allocated"
    assert caught.value.context["platform"] == "android"
    assert "private file detail" not in str(caught.value)
