# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import logging
from pathlib import Path

import pytest
from pydantic import ValidationError

from fsq_agent.execution import RunLifecycleService, allocate_run, run_fsq_core_case
from fsq_agent.models import ConfigurationError, EvidenceBundle, ExecutableStep, RunExecutionResult, RunnerStepResult, RunSource


@pytest.mark.parametrize(
    "source",
    [
        "client_secret: |\n  CREDENTIAL_SENTINEL\n",
        'url: "https://example.test/?%74oken=CREDENTIAL_SENTINEL"',
        'url: "https://example.test/?client%5Fsecret=CREDENTIAL_SENTINEL"',
        "headers:\n  Authorization: Bearer CREDENTIAL_SENTINEL\n",
    ],
)
def test_source_credential_families_redacted_before_hash(tmp_path, source):
    metadata = allocate_run(workspace=tmp_path, workspace_name="demo", platform="web", mode="strict", source_id="run", source=RunSource(kind="case", case_id="case"))
    run = tmp_path / ".fsq/runs/web" / metadata.run_id
    updated = RunLifecycleService.snapshot_sources(run, metadata, sources={"case": source.encode()})
    assert "CREDENTIAL_SENTINEL" not in (run / updated.source.snapshot_path).read_text()
    assert updated.provenance["sources"][0]["transformed"] is True


def test_checkpoint_failure_poison_prevents_every_later_action(tmp_path, monkeypatch):
    from test_durable_evidence import JournalHarness, _runner, _step

    harness = JournalHarness(tmp_path)
    runner, recorder = _runner(tmp_path, harness)
    original = Path.replace

    def fail(self, target):
        if Path(target).name == "evidence-manifest.json":
            raise OSError("checkpoint failed")
        return original(self, target)

    with monkeypatch.context() as scoped:
        scoped.setattr(Path, "replace", fail)
        with pytest.raises(OSError, match="checkpoint failed") as caught:
            runner.run_step("run", _step("one"))
    assert getattr(caught.value, "fsq_evidence_fatal", False)
    with pytest.raises(OSError, match="evidence"):
        runner.run_step("run", _step("two"))
    assert len(harness.invocations) == 1
    assert recorder.write_failed is True


@pytest.mark.parametrize(
    "second", [ExecutableStep(step_id="invalid", kind="action", action_name="inputText", params={"target": "Email"}), ExecutableStep(step_id="unknown", kind="action", action_name="notRegistered")]
)
def test_explicit_registry_and_parameter_preflight_happens_before_any_action(tmp_path, second):
    from test_cli_core_execution import CliCoreHarness

    harness = CliCoreHarness()
    with pytest.raises(ConfigurationError):
        run_fsq_core_case(
            case_path=tmp_path / "explicit.fsq.yaml", output_dir=tmp_path / "run", run_id="run", harness=harness, steps=[ExecutableStep(step_id="first", kind="setup", action_name="launchApp"), second]
        )
    assert harness.actions == []


def test_explore_success_rejects_failed_verification(tmp_path):
    metadata = allocate_run(workspace=tmp_path, workspace_name="demo", platform="web", mode="explore", source_id="run", source=RunSource(kind="goal", goal_summary="test"))
    run = tmp_path / ".fsq/runs/web" / metadata.run_id
    frozen = RunLifecycleService.freeze(
        run,
        metadata,
        bundle=EvidenceBundle(bundle_id="e", run_id=metadata.run_id, completeness="complete", steps=[RunnerStepResult(step_id="one", status="passed")]),
        verification={"status": "failed"},
    )
    with pytest.raises(ValidationError, match="verification"):
        RunExecutionResult.model_validate({**frozen.model_dump(), "outcome": "success"})


def test_shell_operator_log_uses_sanitized_result(tmp_path, monkeypatch, caplog):
    import subprocess

    from test_strict_lifecycle_service import LifecycleHarness

    from fsq_agent._capability_bootstrap import build_capability_registry
    from fsq_agent.case_dsl import FsqCaseLoader
    from fsq_agent.config import Settings
    from fsq_agent.execution import run_strict_lifecycle_case

    path = tmp_path / "case.fsq.yaml"
    path.write_text("schemaVersion: fsq.ai-test/v1\nname: Case\nplatform: android\nonCaseStart:\n- runShell: echo PRIVATE_SENTINEL\n---\n- launchApp: {}\n")
    settings = Settings(cases={"dir": tmp_path})
    settings.runtime_secrets.set_values({"PASSWORD": "PRIVATE_SENTINEL"})
    monkeypatch.setattr("fsq_agent.execution.lifecycle._run_shell_command", lambda command: subprocess.CompletedProcess(command, 0))
    registry = build_capability_registry(platform="android")
    with caplog.at_level(logging.INFO):
        run_strict_lifecycle_case(
            case_path=path,
            case=FsqCaseLoader().load_case(path),
            settings=settings,
            harness=LifecycleHarness(),
            output_dir=tmp_path / "run",
            run_id="run",
            registry=registry,
            registry_snapshot=registry.snapshot(),
            resolve_steps=lambda steps, _: steps,
        )
    assert "PRIVATE_SENTINEL" not in caplog.text
