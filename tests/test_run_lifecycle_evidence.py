# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import json
from pathlib import Path

import pytest

from fsq_agent.config import Settings
from fsq_agent.execution import RunLifecycleService, RunSource, allocate_run, load_run_metadata
from fsq_agent.models import EvidenceBundle, RunnerStepResult


def _run(root: Path):
    metadata = allocate_run(workspace=root, workspace_name="demo", platform="web", source_id="search", mode="strict", source=RunSource(kind="case", case_id="search"))
    return root / ".fsq/runs/web" / metadata.run_id, metadata


def test_freeze_precedes_reporting_and_processing_cannot_flip_result(tmp_path: Path) -> None:
    run_dir, metadata = _run(tmp_path)
    bundle = EvidenceBundle(bundle_id="evidence", run_id=metadata.run_id, completeness="complete", steps=[RunnerStepResult(step_id="one", status="passed")])
    service = RunLifecycleService()
    frozen = service.freeze(run_dir, metadata, bundle=bundle)
    before = (run_dir / "execution-result.json").read_bytes()
    result = service.finalize(run_dir, metadata, execution_result=frozen, processing={"report": {"status": "failed", "message": "Renderer unavailable"}})
    assert result.status == "success"
    assert result.result.steps.total == result.result.steps.passed == 1
    assert (run_dir / "execution-result.json").read_bytes() == before
    assert result.processing["report"]["status"] == "failed"
    with pytest.raises(ValueError, match="immutable"):
        service.freeze(run_dir, result, bundle=bundle, status="failed")


def test_failure_counts_and_first_failure_are_frozen(tmp_path: Path) -> None:
    run_dir, metadata = _run(tmp_path)
    bundle = EvidenceBundle(
        bundle_id="e",
        run_id=metadata.run_id,
        steps=[RunnerStepResult(step_id="one", status="passed"), RunnerStepResult(step_id="two", status="failed", failure_category="assertion_error", error_message="Expected heading")],
    )
    service = RunLifecycleService()
    frozen = service.freeze(run_dir, metadata, bundle=bundle)
    result = service.finalize(run_dir, metadata, execution_result=frozen)
    assert result.result.steps.model_dump()["total"] == 2
    assert result.result.steps.failed == 1
    assert result.result.failed_step == "two"
    assert result.status == "failed"


def test_empty_execution_is_inconclusive_and_unknown_owner_is_not_dead(tmp_path: Path) -> None:
    run_dir, metadata = _run(tmp_path)
    service = RunLifecycleService()
    frozen = service.freeze(run_dir, metadata, bundle=EvidenceBundle(bundle_id="e", run_id=metadata.run_id))
    assert frozen.outcome == "inconclusive"
    (run_dir / "owner.json").unlink(missing_ok=True)
    assert service.inspect_owner(run_dir)["status"] == "unknown"


def test_provenance_never_persists_or_hashes_secret_value(tmp_path: Path) -> None:
    run_dir, metadata = _run(tmp_path)
    source = tmp_path / "input.fsq.yaml"
    source.write_text("name: demo\npassword: hidden-test-value\n", encoding="utf-8")
    service = RunLifecycleService()
    service.snapshot_sources(run_dir, metadata, sources={"case": source}, secret_values=("hidden-test-value",))
    persisted = load_run_metadata(run_dir)
    assert "hidden-test-value" not in json.dumps(persisted.model_dump(mode="json"))
    snapshot = run_dir / persisted.provenance["sources"][0]["path"]
    assert "hidden-test-value" not in snapshot.read_text(encoding="utf-8")


@pytest.mark.parametrize("effort", ["low", "high"])
def test_provenance_captures_neutral_runtime_policy_without_credentials(tmp_path, effort):
    run_dir, metadata = _run(tmp_path)
    settings = Settings()
    settings.harness.platform = "web"
    settings.agent_runtime.reasoning_effort = effort
    settings.agent_runtime.api_key = "not-for-provenance"
    configuration = RunLifecycleService.safe_configuration(settings)
    updated = RunLifecycleService.snapshot_sources(run_dir, metadata, sources={"goal": "Inspect"}, configuration=configuration)
    policy = updated.provenance["configuration"]["agent_runtime"]
    assert policy == {"reasoning_effort": effort, "max_turns": settings.agent_runtime.max_turns}
    assert "not-for-provenance" not in updated.model_dump_json()
