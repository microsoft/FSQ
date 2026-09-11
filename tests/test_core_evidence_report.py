# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import json
from pathlib import Path

from fsq_agent.models import (
    EvidenceArtifactRef,
    EvidenceBundle,
    RunnerEvent,
    RunnerStepResult,
    StepPhaseReport,
)
from fsq_agent.report import CoreEvidenceReportGenerator


def test_core_evidence_report_generator_writes_markdown_and_json(tmp_path: Path) -> None:
    manifest_path = tmp_path / "evidence-manifest.json"
    bundle = EvidenceBundle(
        bundle_id="run-1-evidence",
        run_id="run-1",
        manifest_path=manifest_path,
        events=[
            RunnerEvent(run_id="run-1", event_type="step_start", step_id="step-1"),
            RunnerEvent(run_id="run-1", event_type="step_error", step_id="step-2", phase="invoke"),
        ],
        steps=[
            RunnerStepResult(
                step_id="step-1",
                status="passed",
                phase_reports=[StepPhaseReport(step_id="step-1", phase="invoke", status="passed")],
            ),
            RunnerStepResult(
                step_id="step-2",
                status="failed",
                failure_category="target_resolution_error",
                error_message="Target was not found.",
                phase_reports=[
                    StepPhaseReport(step_id="step-2", phase="prepare", status="passed"),
                    StepPhaseReport(
                        step_id="step-2",
                        phase="invoke",
                        status="failed",
                        failure_category="target_resolution_error",
                        error_message="Target was not found.",
                    ),
                ],
            ),
        ],
        artifacts=[
            EvidenceArtifactRef(
                artifact_id="step-2-finalize-failure",
                kind="screenshot",
                path=Path("artifacts/screenshots/step-2-finalize-failure.png"),
                step_id="step-2",
                phase="finalize",
            )
        ],
        metadata={"case_id": "case-1", "source_path": "cases/case-1.fsq.yaml"},
    )
    manifest_path.write_text(json.dumps(bundle.model_dump(mode="json"), indent=2), encoding="utf-8")

    artifact = CoreEvidenceReportGenerator().generate_from_manifest(manifest_path)

    assert artifact.run_id == "run-1"
    assert artifact.path == tmp_path / "core-report.md"
    assert artifact.evidence_manifest_path == manifest_path
    assert artifact.path.exists()
    json_report_path = tmp_path / "core-report.json"
    assert json_report_path.exists()

    markdown = artifact.path.read_text(encoding="utf-8")
    assert "# Core Evidence Report: run-1" in markdown
    assert "Status: `failed`" in markdown
    assert "`step-2`" in markdown
    assert "target_resolution_error" in markdown
    assert "artifacts/screenshots/step-2-finalize-failure.png" in markdown

    payload = json.loads(json_report_path.read_text(encoding="utf-8"))
    assert payload["run_id"] == "run-1"
    assert payload["summary"] == {
        "status": "failed",
        "step_count": 2,
        "passed_steps": 1,
        "failed_steps": 1,
        "artifact_count": 1,
    }
    assert payload["steps"][1]["failure_category"] == "target_resolution_error"
    assert payload["events"][1]["event_type"] == "step_error"
    assert payload["artifacts"][0]["path"] == "artifacts/screenshots/step-2-finalize-failure.png"


def test_core_report_does_not_count_skipped_and_cancelled_as_failures(tmp_path: Path) -> None:
    bundle = EvidenceBundle(run_id="partial", bundle_id="partial", steps=[RunnerStepResult(step_id="skip", status="skipped"), RunnerStepResult(step_id="cancel", status="cancelled")])
    manifest = tmp_path / "evidence-manifest.json"
    manifest.write_text(bundle.model_dump_json(), encoding="utf-8")
    CoreEvidenceReportGenerator().generate_from_manifest(manifest)
    report = json.loads((tmp_path / "core-report.json").read_text(encoding="utf-8"))
    assert report["summary"]["failed_steps"] == 0
    assert report["summary"]["skipped_steps"] == 1
    assert report["summary"]["cancelled_steps"] == 1
    assert report["summary"]["status"] == "cancelled"


def test_core_evidence_report_groups_lifecycle_steps(tmp_path: Path) -> None:
    manifest_path = tmp_path / "evidence-manifest.json"
    bundle = EvidenceBundle(
        bundle_id="run-lifecycle-evidence",
        run_id="run-lifecycle",
        manifest_path=manifest_path,
        steps=[
            RunnerStepResult(
                step_id="setup-step-001",
                source_ref={
                    "source_type": "fsq",
                    "source_id": "hooks/setup.fsq.yaml",
                    "metadata": {
                        "case_name": "Setup Hook",
                        "lifecycle_phase": "case",
                        "parent_hook_action": {
                            "lifecycle_phase": "onCaseStart",
                            "hook_action_name": "runCase",
                            "case_path": "root.fsq.yaml",
                        },
                    },
                },
                status="passed",
                phase_reports=[
                    StepPhaseReport(
                        step_id="setup-step-001",
                        phase="invoke",
                        status="passed",
                        metadata={"replay": {"alias": "launchApp"}, "capability_name": "launch_app"},
                    )
                ],
            ),
            RunnerStepResult(
                step_id="root-step-001",
                source_ref={
                    "source_type": "fsq",
                    "source_id": "root.fsq.yaml",
                    "metadata": {"case_name": "Root Case", "lifecycle_phase": "case"},
                },
                status="passed",
                phase_reports=[
                    StepPhaseReport(
                        step_id="root-step-001",
                        phase="invoke",
                        status="passed",
                        metadata={"replay": {"alias": "tapOn"}, "capability_name": "tap_on"},
                    )
                ],
            ),
            RunnerStepResult(
                step_id="root-hook-run-case-001",
                source_ref={
                    "source_type": "fsq_hook",
                    "source_id": "root.fsq.yaml",
                    "metadata": {
                        "lifecycle_phase": "onCaseStart",
                        "hook_action_name": "runCase",
                        "value": "hooks/setup.fsq.yaml",
                    },
                },
                status="passed",
                phase_reports=[
                    StepPhaseReport(
                        step_id="root-hook-run-case-001",
                        phase="invoke",
                        status="passed",
                        metadata={
                            "lifecycle_phase": "onCaseStart",
                            "hook_action_name": "runCase",
                            "target": "hooks/setup.fsq.yaml",
                        },
                    )
                ],
            ),
            RunnerStepResult(
                step_id="root-hook-shell-001",
                source_ref={
                    "source_type": "fsq_hook",
                    "source_id": "root.fsq.yaml",
                    "metadata": {"lifecycle_phase": "onCaseComplete", "hook_action_name": "runShell"},
                },
                status="failed",
                failure_category="action_error",
                error_message="Shell hook failed with exit code 1.",
                phase_reports=[
                    StepPhaseReport(
                        step_id="root-hook-shell-001",
                        phase="invoke",
                        status="failed",
                        failure_category="action_error",
                        error_message="Shell hook failed with exit code 1.",
                        metadata={
                            "lifecycle_phase": "onCaseComplete",
                            "hook_action_name": "runShell",
                            "command": "echo done",
                            "exit_code": 1,
                        },
                    )
                ],
            ),
        ],
    )
    manifest_path.write_text(json.dumps(bundle.model_dump(mode="json"), indent=2), encoding="utf-8")

    artifact = CoreEvidenceReportGenerator().generate_from_manifest(manifest_path)

    markdown = artifact.path.read_text(encoding="utf-8")
    assert "## Lifecycle Summary" in markdown
    assert "| Before case | `passed` | `1` | `1` | `0` |" in markdown
    assert "| Main case | `passed` | `1` | `1` | `0` |" in markdown
    assert "| After case | `failed` | `1` | `0` | `1` |" in markdown
    assert "| Phase | Source | Action | Step | Status | Failure Category | Error |" in markdown
    assert "| Before case | Setup Hook | `launchApp` | `setup-step-001` | `passed`" in markdown
    assert "| Before case | root.fsq.yaml | `runCase: hooks/setup.fsq.yaml` | `root-hook-run-case-001` | `passed`" in markdown
    assert "| Main case | Root Case | `tapOn` | `root-step-001` | `passed`" in markdown
    assert "| After case | root.fsq.yaml | `runShell: echo done` | `root-hook-shell-001` | `failed`" in markdown

    payload = json.loads((tmp_path / "core-report.json").read_text(encoding="utf-8"))
    assert payload["summary"]["lifecycle"]["onCaseStart"] == {
        "label": "Before case",
        "status": "passed",
        "total_steps": 1,
        "passed_steps": 1,
        "failed_steps": 0,
    }
    assert payload["summary"]["lifecycle"]["onCaseComplete"]["status"] == "failed"
    assert payload["lifecycle"]["steps"][0]["phase"] == "onCaseStart"
    assert payload["lifecycle"]["steps"][2]["action"] == "runCase: hooks/setup.fsq.yaml"
    assert payload["lifecycle"]["steps"][3]["action"] == "runShell: echo done"


def test_core_evidence_report_preserves_ai_assertion_verdict_metadata(tmp_path: Path) -> None:
    manifest_path = tmp_path / "evidence-manifest.json"
    bundle = EvidenceBundle(
        bundle_id="run-ai-evidence",
        run_id="run-ai",
        manifest_path=manifest_path,
        steps=[
            RunnerStepResult(
                step_id="step-ai",
                status="passed",
                phase_reports=[
                    StepPhaseReport(
                        step_id="step-ai",
                        phase="invoke",
                        status="passed",
                        artifact_refs=[
                            EvidenceArtifactRef(
                                artifact_id="step-ai-invoke-assert-with-ai",
                                kind="screenshot",
                                path=Path("artifacts/screenshots/step-ai-invoke-assert-with-ai.png"),
                                step_id="step-ai",
                                phase="invoke",
                            )
                        ],
                        metadata={
                            "harness_metadata": {
                                "prompt": "Verify the logo is visible.",
                                "ai_assertion": {
                                    "status": "passed",
                                    "passed": True,
                                    "explanation": "The logo is visible.",
                                    "provider": "github_copilot",
                                    "model": "gpt-5.5",
                                    "latency_ms": 123,
                                    "token_usage": {"input_tokens": 10, "output_tokens": 5},
                                    "error": None,
                                },
                            }
                        },
                    )
                ],
            )
        ],
        artifacts=[],
    )
    manifest_path.write_text(json.dumps(bundle.model_dump(mode="json"), indent=2), encoding="utf-8")

    artifact = CoreEvidenceReportGenerator().generate_from_manifest(manifest_path)

    markdown = artifact.path.read_text(encoding="utf-8")
    assert "## AI Assertions" in markdown
    assert "The logo is visible." in markdown
    assert "github_copilot" in markdown
    assert "artifacts/screenshots/step-ai-invoke-assert-with-ai.png" in markdown
    payload = json.loads((tmp_path / "core-report.json").read_text(encoding="utf-8"))
    ai_assertion = payload["steps"][0]["phase_reports"][0]["metadata"]["harness_metadata"]["ai_assertion"]
    assert ai_assertion["provider"] == "github_copilot"
    assert ai_assertion["model"] == "gpt-5.5"
    assert ai_assertion["token_usage"] == {"input_tokens": 10, "output_tokens": 5}
