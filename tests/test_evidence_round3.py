# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import asyncio

import pytest
from pydantic import ValidationError

from fsq_agent.core import EvidenceRecorder, StepRunner
from fsq_agent.execution import RunLifecycleService, allocate_run
from fsq_agent.models import EvidenceBundle, RunnerEvent, RunnerStepResult, RunSource


def _run(root):
    metadata = allocate_run(workspace=root, workspace_name="test", platform="web", mode="strict", source_id="test", source=RunSource(kind="case", case_id="test"))
    return root / ".fsq/runs/web" / metadata.run_id, metadata


@pytest.mark.parametrize(
    "source",
    [
        "token: |\n  SENTINEL_PRIVATE\n  SECOND_PRIVATE\nname: safe\n",
        "items:\n- access_token: >-\n    SENTINEL_PRIVATE\n    SECOND_PRIVATE\n",
        '{"properties":{"refresh_token":"SENTINEL_PRIVATE\\nSECOND_PRIVATE"},"name":"safe"}',
        "properties:\n  token: [SENTINEL_PRIVATE, SECOND_PRIVATE]\n",
    ],
)
def test_complete_credential_values_removed_before_source_hash(tmp_path, source):
    run, metadata = _run(tmp_path)
    updated = RunLifecycleService.snapshot_sources(run, metadata, sources={"case": source.encode()})
    retained = (run / updated.source.snapshot_path).read_text()
    assert "SENTINEL_PRIVATE" not in retained
    assert "SECOND_PRIVATE" not in retained
    assert updated.provenance["sources"][0]["transformed"] is True


@pytest.mark.parametrize("verification", [{"status": "not_requested"}, {"status": "failed"}])
def test_strict_blocking_skip_or_failed_verification_never_passes(tmp_path, verification):
    run, metadata = _run(tmp_path)
    steps = [RunnerStepResult(step_id="ok", status="passed"), RunnerStepResult(step_id="blocked", status="skipped", action_status="skipped", failure_category="action_error")]
    frozen = RunLifecycleService.freeze(run, metadata, bundle=EvidenceBundle(bundle_id="e", run_id=metadata.run_id, completeness="complete", steps=steps), verification=verification)
    assert frozen.outcome == "failed"
    assert frozen.failed_step == "blocked"
    with pytest.raises(ValidationError):
        type(frozen).model_validate({**frozen.model_dump(), "outcome": "success"})


@pytest.mark.parametrize("child", ["sources", "lineage.jsonl"])
def test_derived_write_rejects_existing_symlink_escape(tmp_path, child):
    run, metadata = _run(tmp_path)
    outside = tmp_path / "outside"
    if child == "sources":
        outside.mkdir()
    else:
        outside.write_text("preserve")
    (run / child).symlink_to(outside, target_is_directory=child == "sources")

    def write():
        if child == "sources":
            RunLifecycleService.snapshot_sources(run, metadata, sources={"case": b"name: safe"})
        else:
            RunLifecycleService.append_lineage(run, {"kind": "test"})

    with pytest.raises(ValueError, match="contain"):
        write()
    assert list(outside.iterdir()) == [] if outside.is_dir() else outside.read_text() == "preserve"


def test_action_failure_cannot_be_recovered_as_passed(tmp_path):
    recorder = EvidenceRecorder(run_id="run", output_dir=tmp_path)
    recorder.record_event(RunnerEvent(run_id="run", event_type="step_start", step_id="one", step_execution_id="one", source_step_id="source"))
    recorder.record_event(
        RunnerEvent(run_id="run", event_type="action_result", step_id="one", step_execution_id="one", source_step_id="source", payload={"status": "failed", "failure_category": "assertion_error"})
    )
    with pytest.raises(ValueError, match="outcome"):
        recorder.record_step_result(RunnerStepResult(step_id="one", step_execution_id="one", source_step_id="source", status="passed", action_status="passed"))


def test_late_cancel_keeps_primary_action_failure(tmp_path):
    from test_durable_evidence import JournalHarness, _runner, _step

    class LateCancel(JournalHarness):
        def after_action(self, step, context, result):
            raise asyncio.CancelledError

    runner, recorder = _runner(tmp_path, LateCancel(tmp_path, failure=True))
    with pytest.raises(asyncio.CancelledError):
        runner.run_step("run", _step())
    result = recorder.build_bundle().steps[0]
    assert result.status == "cancelled"
    assert result.failure_category == "assertion_error"
    assert result.error_message == "Expected button missing"
    assert result.metadata["interruption"]["status"] == "cancelled"


@pytest.mark.parametrize(
    "url", ["https://user:SENTINEL_PRIVATE@example.test/?token=QUERY_PRIVATE", "https://example.test/?safe=ok&access_token=QUERY_PRIVATE", "https://example.test/?%74oken=QUERY_PRIVATE"]
)
def test_url_credentials_are_persisted_safely_without_changing_action(tmp_path, url):
    from pydantic import BaseModel

    from fsq_agent.core import CapabilityRegistry
    from fsq_agent.models import CapabilityDefinition, ExecutableStep, HarnessActionResult, HarnessContext, ReplayPolicy

    class Params(BaseModel):
        url: str

    class Harness:
        invoked = None

        def get_context(self):
            return HarnessContext(platform="web")

        def before_action(self, step, context):
            pass

        def invoke_action(self, step, context):
            self.invoked = step.params["url"]
            return HarnessActionResult(status="passed", action_name=step.action_name)

        def after_action(self, step, context, result):
            pass

        def classify_error(self, error, phase, step):
            return "action_error"

    harness = Harness()
    recorder = EvidenceRecorder(run_id="run", output_dir=tmp_path)
    registry = CapabilityRegistry.from_definitions([CapabilityDefinition(name="navigate", executor_kind="driver", params_model=Params, replay=ReplayPolicy(kind="fsq_command", alias="navigateTo"))])
    runner = StepRunner(harness, capability_registry=registry, evidence_sink=recorder)
    result = runner.run_step("run", ExecutableStep(step_id="nav", kind="diagnostic", action_name="navigate", params={"url": url}))
    assert harness.invoked == url
    persisted = (tmp_path / "evidence-events.jsonl").read_text()
    assert "SENTINEL_PRIVATE" not in persisted
    assert "QUERY_PRIVATE" not in persisted
    invoke = next(phase for phase in result.phase_reports if phase.phase == "invoke")
    assert invoke.metadata["safe_replay_params"] is None
    assert invoke.metadata["parameters_transformed"] is True


def test_deterministic_source_transformation_flag_compares_original(tmp_path):
    from test_cli_core_execution import CliCoreHarness

    from fsq_agent.core import RuntimeSecretStore
    from fsq_agent.execution import load_run_metadata, run_fsq_core_case

    path = tmp_path / "case.fsq.yaml"
    path.write_text("# SENTINEL_PRIVATE\nschemaVersion: fsq.ai-test/v1\nname: Test\nplatform: android\n---\n- launchApp: {}\n")
    run = tmp_path / "run"
    run_fsq_core_case(case_path=path, output_dir=run, run_id="run", harness=CliCoreHarness(), runtime_secret_store=RuntimeSecretStore(["P"], {"P": "SENTINEL_PRIVATE"}))
    assert load_run_metadata(run).provenance["sources"][0]["transformed"] is True


def test_body_entry_cancellation_still_runs_trailing_teardown(tmp_path, monkeypatch):
    from test_strict_lifecycle_service import LifecycleHarness

    from fsq_agent._capability_bootstrap import build_capability_registry
    from fsq_agent.case_dsl import FsqCaseLoader
    from fsq_agent.config import Settings
    from fsq_agent.execution import run_strict_lifecycle_case

    path = tmp_path / "case.fsq.yaml"
    path.write_text("schemaVersion: fsq.ai-test/v1\nname: Test\nplatform: android\n---\n- launchApp: {}\n- killApp: {}\n")
    original = __import__("fsq_agent.execution.lifecycle", fromlist=["_StrictLifecycleExecutor"])._StrictLifecycleExecutor._execute_case_commands

    def cancelled_body(self, *args):
        self.cancellation_check = lambda: (_ for _ in ()).throw(asyncio.CancelledError())
        return original(self, *args)

    monkeypatch.setattr("fsq_agent.execution.lifecycle._StrictLifecycleExecutor._execute_case_commands", cancelled_body)
    harness = LifecycleHarness()
    registry = build_capability_registry(platform="android")
    with pytest.raises(asyncio.CancelledError):
        run_strict_lifecycle_case(
            case_path=path,
            case=FsqCaseLoader().load_case(path),
            settings=Settings(cases={"dir": tmp_path}),
            harness=harness,
            output_dir=tmp_path / "run",
            run_id="run",
            registry=registry,
            registry_snapshot=registry.snapshot(),
            resolve_steps=lambda steps, _: steps,
        )
    assert harness.actions == ["kill_app"]


def test_secret_reference_names_and_clean_source_bytes_are_preserved(tmp_path):
    import yaml

    run, metadata = _run(tmp_path)
    source = "properties:\n  required_runtime_secret_names: [LOGIN_PASSWORD]\n---\n- inputText:\n    textType: runtimeSecret\n    text: LOGIN_PASSWORD\n"
    updated = RunLifecycleService.snapshot_sources(run, metadata, sources={"case": source.encode()})
    assert (run / updated.source.snapshot_path).read_bytes() == source.encode()
    assert updated.provenance["sources"][0]["transformed"] is False
    assert list(yaml.safe_load_all((run / updated.source.snapshot_path).read_text()))[1][0]["inputText"]["text"] == "LOGIN_PASSWORD"


def test_redacted_replay_is_incomplete_and_cannot_publish(tmp_path):
    from test_strict_case_recording import _recordable_web_run

    from fsq_agent.execution import RecordingService
    from fsq_agent.models import StepPhaseReport

    run, task, result, settings = _recordable_web_run(tmp_path)
    recorder = EvidenceRecorder(run_id=result.report.run_id, output_dir=run)
    for identity, params, reason in [("one", {"target": "Search"}, None), ("two", None, "sensitive_parameters_redacted")]:
        recorder.record_event(RunnerEvent(run_id=result.report.run_id, event_type="step_start", step_id=identity, step_execution_id=identity, source_step_id=identity))
        recorder.record_step_result(
            RunnerStepResult(
                step_id=identity,
                step_execution_id=identity,
                source_step_id=identity,
                status="passed",
                phase_reports=[
                    StepPhaseReport(
                        step_id=identity,
                        phase="invoke",
                        status="passed",
                        metadata={"step_kind": "action", "replay": {"kind": "fsq_command", "alias": "clickOn"}, "safe_replay_params": params, "replay_unavailable_reason": reason},
                    )
                ],
            )
        )
    recorded = RecordingService().record(run_dir=run, task=task, result=result, settings=settings, publication_directory=settings.cases.dir)
    assert recorded.draft is True
    assert recorded.command_count == 1
    assert recorded.publication_status == "failed"
    assert recorded.published_case_path is None
    assert "REDACTED" not in recorded.recorded_case_path.read_text()
