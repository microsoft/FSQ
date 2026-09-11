# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import json
from pathlib import Path

import pytest

from fsq_agent.models import ReportGenerationError, StepResult, Task, VerificationResult
from fsq_agent.report import ReportGenerator, resolve_report_path


class _FailingRichReportGenerator(ReportGenerator):
    def _write_rich_reports(
        self,
        report_dir: Path,
        report_path: Path,
        task: Task,
        steps: list[StepResult],
        verification: VerificationResult,
    ) -> None:
        raise OSError("simulated rich report failure")


def _task() -> Task:
    return Task(
        id="report-task",
        name="Report Task",
        description="Generate a report.",
        acceptance_criteria=["Report exists."],
    )


def _verification() -> VerificationResult:
    return VerificationResult(
        status="failed",
        summary="Reportable failure.",
        unmet_criteria=["Report exists."],
    )


def _steps() -> list[StepResult]:
    return [
        StepResult(
            step_id=1,
            status="failed",
            actual_outcome="Action: Press Back.",
            tool_name="pre_plan",
            tool_output={"step_id": 1, "status": "failed"},
        ),
        StepResult(
            step_id=2,
            status="success",
            actual_outcome='{"status":"failed","summary":"Back failed","pre_plan":[{"step_id":1,"action":"Press Back","success_criteria":["Back is pressed"],"status":"failed"}],"plan_updates":["Back payload failed"],"satisfied_criteria":[],"unmet_criteria":["Back is pressed"],"evidence":[],"errors":["Back failed"]}',
            tool_name="agent_runtime.runner",
        ),
    ]


def test_report_generator_writes_markdown_and_json(tmp_path: Path) -> None:
    artifact = ReportGenerator(tmp_path).generate("run-1", _task(), _steps(), _verification())

    assert artifact.path.name == "report.md"
    assert artifact.path.exists()
    payload = json.loads((tmp_path / "run-1" / "report.json").read_text(encoding="utf-8"))
    assert "steps" not in payload
    assert "plan" not in payload
    assert payload["agent_output"]["schema_version"] == "task_run_v1"
    assert payload["agent_output"]["pre_plan"][0]["action"] == "Press Back"
    assert payload["execution"]["runtime_steps"][0]["source"] == "pre_plan"
    assert payload["execution"]["tool_calls"] == []
    assert artifact.evidence_manifest_path is not None
    assert artifact.evidence_manifest_path.exists()


def test_report_generator_redacts_configured_secret_values(tmp_path: Path) -> None:
    run_dir = tmp_path / "run-secret"
    run_dir.mkdir(parents=True)
    (run_dir / "events.jsonl").write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "type": "tool_call_started",
                        "sequence": 1,
                        "timestamp": "2026-05-09T00:00:00Z",
                        "tool_call_id": "call-1",
                        "tool_name": "input_text",
                        "tool_arguments": {"text": "super-secret"},
                        "payload": {"tool_origin": "harness"},
                    }
                ),
                json.dumps(
                    {
                        "type": "tool_call_completed",
                        "sequence": 2,
                        "timestamp": "2026-05-09T00:00:01Z",
                        "tool_call_id": "call-1",
                        "tool_output_preview": "typed super-secret",
                        "payload": {"artifact_path": None},
                    }
                ),
            ]
        ),
        encoding="utf-8",
    )
    steps = [StepResult(step_id=1, status="success", actual_outcome="used super-secret", tool_name="agent_runtime.runner")]
    verification = VerificationResult(status="success", summary="super-secret hidden")

    ReportGenerator(tmp_path, secret_values=("super-secret",)).generate("run-secret", _task(), steps, verification)

    markdown = (run_dir / "report.md").read_text(encoding="utf-8")
    payload = json.loads((run_dir / "report.json").read_text(encoding="utf-8"))
    assert "super-secret" not in markdown
    assert "super-secret" not in json.dumps(payload, ensure_ascii=False)
    assert payload["execution"]["runtime_steps"][0]["outcome"] == "used ***"
    assert payload["execution"]["tool_calls"][0]["arguments"] == {"text": "***"}
    assert payload["execution"]["tool_calls"][0]["output_preview"] == "typed ***"


def test_report_generator_summarizes_tool_calls_from_events(tmp_path: Path) -> None:
    run_dir = tmp_path / "run-events"
    run_dir.mkdir(parents=True)
    (run_dir / "events.jsonl").write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "type": "tool_call_started",
                        "sequence": 10,
                        "timestamp": "2026-05-09T00:00:00Z",
                        "tool_call_id": "call-1",
                        "tool_name": "tap_on",
                        "tool_arguments": {"strategy": "id", "selector": "target"},
                        "payload": {"tool_origin": "harness"},
                    }
                ),
                json.dumps(
                    {
                        "type": "tool_call_completed",
                        "sequence": 11,
                        "timestamp": "2026-05-09T00:00:01Z",
                        "tool_call_id": "call-1",
                        "tool_output_preview": "found",
                        "payload": {"artifact_path": None},
                    }
                ),
            ]
        ),
        encoding="utf-8",
    )

    ReportGenerator(tmp_path).generate("run-events", _task(), [], _verification())

    payload = json.loads((run_dir / "report.json").read_text(encoding="utf-8"))
    assert payload["execution"]["tool_calls"] == [
        {
            "tool_call_id": "call-1",
            "tool_name": "tap_on",
            "tool_origin": "harness",
            "status": "completed",
            "transport_status": "completed",
            "execution_status": None,
            "started_sequence": 10,
            "completed_sequence": 11,
            "started_at": "2026-05-09T00:00:00Z",
            "completed_at": "2026-05-09T00:00:01Z",
            "arguments": {"strategy": "id", "selector": "target"},
            "output_preview": "found",
            "artifact_path": None,
            "error": None,
            "duration_ms": None,
        }
    ]


def test_report_generator_classifies_tool_usage_error_with_unmet_semantic_action(tmp_path: Path) -> None:
    run_dir = tmp_path / "run-tool-usage-error"
    run_dir.mkdir(parents=True)
    (run_dir / "events.jsonl").write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "type": "tool_call_started",
                        "sequence": 89,
                        "timestamp": "2026-05-09T00:00:00Z",
                        "tool_call_id": "call-1",
                        "tool_name": "perform_actions",
                        "tool_arguments": {"actions": [{"type": "key", "parameters": {"pointerType": "touch"}}]},
                        "payload": {"tool_origin": "harness"},
                    }
                ),
                json.dumps(
                    {
                        "type": "tool_call_completed",
                        "sequence": 90,
                        "timestamp": "2026-05-09T00:00:01Z",
                        "tool_call_id": "call-1",
                        "tool_output_preview": "Failed to perform actions. pointerType parameter is only supported for action type 'pointer' in 'backKey' action",
                        "payload": {"artifact_path": None},
                    }
                ),
            ]
        ),
        encoding="utf-8",
    )
    verification = VerificationResult(
        status="failed",
        summary="Could not prove required ordered key action pressKey: Back.",
        unmet_criteria=["Key action 11: pressKey: Back"],
    )

    artifact = ReportGenerator(tmp_path).generate("run-tool-usage-error", _task(), [], verification)

    payload = json.loads((run_dir / "report.json").read_text(encoding="utf-8"))
    assert payload["failure_classification"] == "tool_usage_error + semantic_action_unmet"
    assert "Failure Classification: `tool_usage_error + semantic_action_unmet`" in artifact.path.read_text(encoding="utf-8")


def test_report_generator_classifies_conflicting_key_identity_diagnostic(tmp_path: Path) -> None:
    run_dir = tmp_path / "run-conflicting-key"
    run_dir.mkdir(parents=True)
    verification = VerificationResult(
        status="failed",
        summary="Required ordered key action pressKey: Enter was unmet.",
        unmet_criteria=["Key action 9: pressKey: Enter"],
        diagnostics=["Tool usage issue: press_key was called with conflicting key identities."],
    )

    artifact = ReportGenerator(tmp_path).generate("run-conflicting-key", _task(), [], verification)

    payload = json.loads((run_dir / "report.json").read_text(encoding="utf-8"))
    assert payload["failure_classification"] == "tool_usage_error + semantic_action_unmet"
    assert "Failure Classification: `tool_usage_error + semantic_action_unmet`" in artifact.path.read_text(encoding="utf-8")


def test_report_generator_classifies_provider_content_filter(tmp_path: Path) -> None:
    run_dir = tmp_path / "run-provider-content-filter"
    run_dir.mkdir(parents=True)
    steps = [
        StepResult(
            step_id=1,
            status="failed",
            actual_outcome="Agent runtime execution ended with an incomplete provider response due to content filtering.",
            error=("Responses stream ended with terminal event `response.incomplete`. status=incomplete; incomplete_details=IncompleteDetails(reason='content_filter')."),
            tool_name="agent_runtime.runner",
            tool_output={"failure_category": "provider_content_filter", "failure_reason": "content_filter"},
        )
    ]
    verification = VerificationResult(
        status="failed",
        summary="The automation run failed before completion.",
        unmet_criteria=["Final state was not verified."],
    )

    artifact = ReportGenerator(tmp_path).generate("run-provider-content-filter", _task(), steps, verification)

    payload = json.loads((run_dir / "report.json").read_text(encoding="utf-8"))
    assert payload["failure_classification"] == "provider_content_filter"
    assert "Failure Classification: `provider_content_filter`" in artifact.path.read_text(encoding="utf-8")


def test_report_generator_summarizes_common_tool_calls_without_call_id(tmp_path: Path) -> None:
    run_dir = tmp_path / "run-local-events"
    run_dir.mkdir(parents=True)
    (run_dir / "events.jsonl").write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "type": "tool_call_started",
                        "sequence": 3,
                        "timestamp": "2026-05-09T00:00:00Z",
                        "tool_name": "search_artifact",
                        "tool_arguments": {"query": "Settings"},
                        "payload": {"tool_origin": "common"},
                    }
                ),
                json.dumps(
                    {
                        "type": "tool_call_completed",
                        "sequence": 4,
                        "timestamp": "2026-05-09T00:00:01Z",
                        "tool_name": "search_artifact",
                        "tool_output_preview": "match",
                        "payload": {"tool_origin": "common", "artifact_path": "output/runs/run/artifacts/tools/a.json"},
                    }
                ),
            ]
        ),
        encoding="utf-8",
    )

    ReportGenerator(tmp_path).generate("run-local-events", _task(), [], _verification())

    payload = json.loads((run_dir / "report.json").read_text(encoding="utf-8"))
    assert payload["execution"]["tool_calls"][0]["tool_name"] == "search_artifact"
    assert payload["execution"]["tool_calls"][0]["tool_origin"] == "common"
    assert payload["execution"]["tool_calls"][0]["artifact_path"] == "output/runs/run/artifacts/tools/a.json"


def test_report_generator_writes_minimal_json_fallback(tmp_path: Path) -> None:
    artifact = _FailingRichReportGenerator(tmp_path).generate("run-2", _task(), [], _verification())

    assert artifact.path.name == "report-fallback.json"
    assert artifact.format == "json"
    payload = json.loads(artifact.path.read_text(encoding="utf-8"))
    assert payload["run_id"] == "run-2"
    assert payload["task_id"] == "report-task"
    assert payload["status"] == "failed"
    assert payload["summary"] == "Reportable failure."
    assert "simulated rich report failure" in payload["error"]


def test_resolve_report_path_finds_llm_or_strict_report(tmp_path: Path) -> None:
    llm_run = tmp_path / "llm-run"
    strict_run = tmp_path / "strict-run"
    llm_run.mkdir()
    strict_run.mkdir()
    (llm_run / "report.md").write_text("llm", encoding="utf-8")
    (strict_run / "core-report.md").write_text("strict", encoding="utf-8")

    assert resolve_report_path(tmp_path, "llm-run") == llm_run / "report.md"
    assert resolve_report_path(tmp_path, "strict-run") == strict_run / "core-report.md"


def test_resolve_report_path_errors_for_missing_or_ambiguous_report(tmp_path: Path) -> None:
    ambiguous = tmp_path / "ambiguous"
    ambiguous.mkdir()
    (ambiguous / "report.md").write_text("llm", encoding="utf-8")
    (ambiguous / "core-report.md").write_text("strict", encoding="utf-8")

    with pytest.raises(ReportGenerationError, match="Report not found"):
        resolve_report_path(tmp_path, "missing")
    with pytest.raises(ReportGenerationError, match="ambiguous"):
        resolve_report_path(tmp_path, "ambiguous")


def test_resolve_report_path_requires_direct_child_run_id(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside-report-run"
    outside.mkdir(exist_ok=True)
    (outside / "report.md").write_text("outside", encoding="utf-8")

    for run_id in ("../outside-report-run", str(outside), "nested/run"):
        with pytest.raises(ReportGenerationError, match="direct child"):
            resolve_report_path(tmp_path, run_id)

    linked = tmp_path / "linked-run"
    try:
        linked.symlink_to(outside, target_is_directory=True)
    except OSError:
        return
    with pytest.raises(ReportGenerationError, match="direct child"):
        resolve_report_path(tmp_path, linked.name)
