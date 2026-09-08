# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import json
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from fsq_agent.models import AgentFinalOutput, ReportArtifact, ReportGenerationError, StepResult, Task, ToolCallRecord, VerificationResult
from fsq_agent.report._evidence import EvidenceBundler
from fsq_agent.report._failure_analysis import FailureAnalyzer


class ReportGenerator:
    def __init__(
        self,
        runs_dir: Path,
        evidence_bundler: EvidenceBundler | None = None,
        *,
        secret_values: tuple[str, ...] | list[str] = (),
    ) -> None:
        self.runs_dir = runs_dir
        self.evidence_bundler = evidence_bundler or EvidenceBundler(runs_dir)
        self.failure_analyzer = FailureAnalyzer()
        self.secret_values = tuple(sorted({value for value in secret_values if value}, key=len, reverse=True))

    def generate(
        self,
        run_id: str,
        task: Task,
        steps: list[StepResult],
        verification: VerificationResult,
    ) -> ReportArtifact:
        report_dir = self.runs_dir / run_id
        report_dir.mkdir(parents=True, exist_ok=True)
        manifest_path = self.evidence_bundler.create_manifest(run_id, steps)
        report_path = report_dir / "report.md"
        try:
            self._write_rich_reports(report_dir, report_path, task, steps, verification)
        except OSError as exc:
            return self._write_minimal_fallback(run_id, report_dir, task, verification, manifest_path, exc)
        return ReportArtifact(run_id=run_id, path=report_path, evidence_manifest_path=manifest_path)

    def _write_rich_reports(
        self,
        report_dir: Path,
        report_path: Path,
        task: Task,
        steps: list[StepResult],
        verification: VerificationResult,
    ) -> None:
        tool_calls = self._load_tool_calls(report_dir)
        markdown = self._redact_text(self._render_markdown(task, steps, verification, tool_calls))
        report_path.write_text(markdown, encoding="utf-8")
        json_path = report_dir / "report.json"
        json_report = self._redact_value(self._build_json_report(report_dir, task, steps, verification, tool_calls))
        json_path.write_text(json.dumps(json_report, indent=2, ensure_ascii=False), encoding="utf-8")

    def _build_json_report(
        self,
        report_dir: Path,
        task: Task,
        steps: list[StepResult],
        verification: VerificationResult,
        tool_calls: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        agent_output = self._parse_runner_output(steps)
        tool_calls = tool_calls if tool_calls is not None else self._load_tool_calls(report_dir)
        return {
            "task": task.model_dump(mode="json"),
            "agent_output": agent_output.model_dump(mode="json") if agent_output else None,
            "execution": {
                "runtime_steps": [self._step_record(step) for step in steps],
                "tool_calls": tool_calls,
            },
            "verification": {"verification_goal": task.verification_goal, **verification.model_dump(mode="json")},
            "failure_classification": self.failure_analyzer.classify(steps, verification, tool_calls),
        }

    def _parse_runner_output(self, steps: list[StepResult]) -> AgentFinalOutput | None:
        runner_steps = [step for step in steps if step.tool_name == "agent_runtime.runner"]
        if not runner_steps:
            return None
        raw_output = runner_steps[-1].tool_output or runner_steps[-1].actual_outcome
        if isinstance(raw_output, AgentFinalOutput):
            return raw_output
        if isinstance(raw_output, dict):
            try:
                return AgentFinalOutput.model_validate(raw_output)
            except ValidationError:
                return None
        try:
            return AgentFinalOutput.model_validate_json(str(raw_output))
        except (ValidationError, ValueError):
            return None

    def _step_record(self, step: StepResult) -> dict[str, Any]:
        return {
            "step_id": step.step_id,
            "status": step.status,
            "source": step.tool_name or "runtime",
            "outcome": step.actual_outcome,
            "error": step.error,
            "duration_ms": step.duration_ms,
            "screenshot_path": str(step.screenshot_path) if step.screenshot_path else None,
            "tool_output": step.tool_output,
        }

    def _load_tool_calls(self, report_dir: Path) -> list[dict[str, Any]]:
        events_path = report_dir / "events.jsonl"
        if not events_path.exists():
            return []

        starts: dict[str, dict[str, Any]] = {}
        unpaired_starts: list[dict[str, Any]] = []
        calls: list[ToolCallRecord] = []
        for line in events_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            event_type = event.get("type")
            call_id = str(event.get("tool_call_id") or "")
            if event_type == "tool_call_started" and call_id:
                starts[call_id] = event
                continue
            if event_type == "tool_call_started":
                unpaired_starts.append(event)
                continue
            if event_type not in {"tool_call_completed", "tool_call_failed"}:
                continue

            start = starts.get(call_id, {}) if call_id else self._pop_unpaired_start(unpaired_starts, event)
            tool_name = start.get("tool_name") or event.get("tool_name") or "unknown"
            start_payload = start.get("payload") if isinstance(start.get("payload"), dict) else {}
            event_payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
            calls.append(
                ToolCallRecord(
                    tool_call_id=call_id or None,
                    tool_name=str(tool_name),
                    tool_origin=self._tool_origin(str(tool_name), start_payload.get("tool_origin") or event_payload.get("tool_origin")),
                    status="failed" if event_type == "tool_call_failed" else "completed",
                    started_sequence=start.get("sequence"),
                    completed_sequence=event.get("sequence"),
                    started_at=start.get("timestamp"),
                    completed_at=event.get("timestamp"),
                    arguments=start.get("tool_arguments"),
                    output_preview=event.get("tool_output_preview"),
                    artifact_path=event_payload.get("artifact_path"),
                    error=event.get("message") if event_type == "tool_call_failed" else None,
                    duration_ms=event.get("duration_ms"),
                )
            )
        return [call.model_dump(mode="json") for call in calls]

    def _pop_unpaired_start(self, starts: list[dict[str, Any]], event: dict[str, Any]) -> dict[str, Any]:
        event_tool = event.get("tool_name")
        if event_tool:
            for index, start in enumerate(starts):
                if start.get("tool_name") == event_tool:
                    return starts.pop(index)
        return starts.pop(0) if starts else {}

    def _tool_origin(self, tool_name: str, explicit_origin: Any) -> str:
        if explicit_origin in {"agent_tool", "common", "platform", "harness", "runtime", "unknown"}:
            return str(explicit_origin)
        if tool_name == "unknown":
            return "unknown"
        return "unknown"

    def _write_minimal_fallback(
        self,
        run_id: str,
        report_dir: Path,
        task: Task,
        verification: VerificationResult,
        manifest_path: Path,
        error: OSError,
    ) -> ReportArtifact:
        fallback_path = report_dir / "report-fallback.json"
        try:
            payload = self._redact_value(
                {
                    "run_id": run_id,
                    "task_id": task.id,
                    "status": verification.status,
                    "summary": verification.summary,
                    "error": str(error),
                }
            )
            fallback_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        except OSError as fallback_error:
            raise ReportGenerationError("Unable to generate report.", context={"run_id": run_id}) from fallback_error
        return ReportArtifact(
            run_id=run_id,
            path=fallback_path,
            format="json",
            evidence_manifest_path=manifest_path,
        )

    def _render_markdown(
        self,
        task: Task,
        steps: list[StepResult],
        verification: VerificationResult,
        tool_calls: list[dict[str, Any]] | None = None,
    ) -> str:
        agent_output = self._parse_runner_output(steps)
        failure_classification = self.failure_analyzer.classify(steps, verification, tool_calls or [])
        lines = [
            f"# Test Report: {task.name}",
            "",
            f"Task ID: `{task.id}`",
            f"Status: `{verification.status}`",
            f"Failure Classification: `{failure_classification}`",
            "",
            "## Summary",
            "",
            verification.summary,
            "",
            "## Verification Goal",
            "",
            task.verification_goal or "No verification goal was recorded.",
            "",
            "## Plan",
            "",
        ]
        if agent_output and agent_output.pre_plan:
            for item in agent_output.pre_plan:
                lines.extend(
                    [
                        f"### Plan Item {item.step_id}: {item.status}",
                        "",
                        item.action,
                        "",
                    ]
                )
                criteria = item.success_criteria
                if criteria:
                    lines.extend(f"- {criterion}" for criterion in criteria)
                    lines.append("")
        else:
            lines.extend(["No plan was available in the runner final output.", ""])
        if agent_output and agent_output.plan_updates:
            lines.extend(["## Plan Updates", ""])
            lines.extend(f"- {update}" for update in agent_output.plan_updates)
            lines.append("")

        lines.extend(["## Execution Records", ""])
        for step in steps:
            lines.extend(
                [
                    f"### Step {step.step_id}: {step.status} ({step.tool_name or 'runtime'})",
                    "",
                    step.actual_outcome,
                    "",
                ]
            )
            if step.error:
                lines.extend([f"Error: `{step.error}`", ""])
        if verification.unmet_criteria:
            lines.extend(["## Unmet Criteria", ""])
            lines.extend(f"- {criterion}" for criterion in verification.unmet_criteria)
            lines.append("")
        if verification.satisfied_criteria:
            lines.extend(["## Satisfied Criteria", ""])
            lines.extend(f"- {criterion}" for criterion in verification.satisfied_criteria)
            lines.append("")
        if verification.diagnostics:
            lines.extend(["## Diagnostics", ""])
            lines.extend(f"- {diagnostic}" for diagnostic in verification.diagnostics)
            lines.append("")
        return "\n".join(lines)

    def _redact_value(self, value: Any) -> Any:
        if not self.secret_values:
            return value
        if isinstance(value, dict):
            return {key: self._redact_value(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self._redact_value(item) for item in value]
        if isinstance(value, str):
            return self._redact_text(value)
        return value

    def _redact_text(self, text: str) -> str:
        redacted = text
        for secret_value in self.secret_values:
            redacted = redacted.replace(secret_value, "***")
        return redacted
