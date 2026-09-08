# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from fsq_agent.agent._structured_output import coerce_agent_final_output
from fsq_agent.models import AgentFinalOutput, StepResult, Task, ToolCallRecord


class VerificationEvidenceBundle(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = "verification_evidence_v1"
    verification_goal: str
    task: dict[str, Any]
    agent_claims: dict[str, Any] | None = None
    execution_steps: list[dict[str, Any]] = Field(default_factory=list)
    tool_calls: list[dict[str, Any]] = Field(default_factory=list)
    artifacts: list[dict[str, Any]] = Field(default_factory=list)
    instructions: list[str] = Field(default_factory=list)


class VerificationEvidenceBuilder:
    def __init__(self, artifact_preview_chars: int = 12000) -> None:
        self.artifact_preview_chars = artifact_preview_chars

    def build(
        self,
        task: Task,
        results: list[StepResult],
        events_path: Path | None = None,
        image_root: Path | None = None,
    ) -> VerificationEvidenceBundle:
        _ = image_root
        report_dir = events_path.parent if events_path else None
        tool_calls = self._load_tool_calls(events_path)
        return VerificationEvidenceBundle(
            verification_goal=task.verification_goal or "",
            task=task.model_dump(mode="json"),
            agent_claims=self._agent_claims(results),
            execution_steps=[self._step_record(step) for step in results],
            tool_calls=tool_calls,
            artifacts=self._load_artifacts(report_dir),
            instructions=[
                "Use only the supplied evidence bundle.",
                "Treat agent_claims as claims, not proof.",
                "Check exactly the supplied verification_goal and do not infer extra final goals from key actions.",
                "Mark the verification_goal satisfied only when supplied events, tool outputs, or artifact excerpts support it.",
                "Mark the verification_goal unmet only when supplied evidence proves it did not happen or the required final state is false.",
                "If evidence is missing, truncated, ambiguous, or outside the supplied bundle, use status=inconclusive.",
                "For visual assertions such as assertWithAI, do not re-inspect screenshot pixels. Verify that execution evidence contains the backend AI assertion tool result, including verdict metadata and screenshot artifact references, and that no supplied evidence contradicts that result.",
                "Do not transform key actions into independent final verification requirements.",
            ],
        )

    def build_json(self, task: Task, results: list[StepResult], events_path: Path | None = None) -> str:
        return self.build(task, results, events_path).model_dump_json(indent=2)

    def build_model_input(
        self,
        task: Task,
        results: list[StepResult],
        events_path: Path | None = None,
        image_root: Path | None = None,
    ) -> str:
        bundle = self.build(task, results, events_path, image_root)
        return bundle.model_dump_json(indent=2)

    def _agent_claims(self, results: list[StepResult]) -> dict[str, Any] | None:
        runner_steps = [step for step in results if step.tool_name == "agent_runtime.runner"]
        if not runner_steps:
            return None
        raw_output = runner_steps[-1].tool_output or runner_steps[-1].actual_outcome
        payload = raw_output if isinstance(raw_output, AgentFinalOutput) else coerce_agent_final_output(raw_output)
        if payload:
            return payload.model_dump(mode="json")
        return {"raw_output": str(raw_output)}

    def _step_record(self, step: StepResult) -> dict[str, Any]:
        return {
            "step_id": step.step_id,
            "status": step.status,
            "source": step.tool_name or "runtime",
            "outcome": step.actual_outcome,
            "error": step.error,
            "duration_ms": step.duration_ms,
            "screenshot_path": str(step.screenshot_path) if step.screenshot_path else None,
            "tool_output": self._compact_value(step.tool_output),
        }

    def _load_tool_calls(self, events_path: Path | None) -> list[dict[str, Any]]:
        if not events_path or not events_path.exists():
            return []
        starts: dict[str, dict[str, Any]] = {}
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
            if event_type not in {"tool_call_completed", "tool_call_failed"} or not call_id:
                continue
            start = starts.get(call_id, {})
            start_payload = start.get("payload") if isinstance(start.get("payload"), dict) else {}
            event_payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
            tool_name = str(start.get("tool_name") or event.get("tool_name") or "unknown")
            calls.append(
                ToolCallRecord(
                    tool_call_id=call_id,
                    tool_name=tool_name,
                    tool_origin=self._tool_origin(tool_name, start_payload.get("tool_origin") or event_payload.get("tool_origin")),
                    status="failed" if event_type == "tool_call_failed" else "completed",
                    arguments=start.get("tool_arguments"),
                    output_preview=event.get("tool_output_preview"),
                    artifact_path=event_payload.get("artifact_path"),
                    error=event.get("message") if event_type == "tool_call_failed" else None,
                    started_sequence=start.get("sequence"),
                    completed_sequence=event.get("sequence"),
                    started_at=start.get("timestamp"),
                    completed_at=event.get("timestamp"),
                    duration_ms=event.get("duration_ms"),
                )
            )
        return [call.model_dump(mode="json") for call in calls]

    def _load_artifacts(self, report_dir: Path | None) -> list[dict[str, Any]]:
        if not report_dir:
            return []
        artifacts_dir = report_dir / "artifacts" / "tools"
        if not artifacts_dir.exists():
            return []
        artifacts: list[dict[str, Any]] = []
        for path in sorted(artifacts_dir.glob("*.json")):
            try:
                text = path.read_text(encoding="utf-8")
            except OSError as exc:
                artifacts.append({"path": str(path), "error": str(exc)})
                continue
            artifacts.append(
                {
                    "path": str(path),
                    "content_chars": len(text),
                    "preview": self._preview(text),
                }
            )
        return artifacts

    def _preview(self, text: str) -> str:
        limit = self.artifact_preview_chars
        if len(text) <= limit:
            return text
        head = text[:limit]
        tail = text[-2000:] if len(text) > limit + 2000 else ""
        return f"{head}\n...[truncated {len(text) - len(head) - len(tail)} chars]...\n{tail}"

    def _compact_value(self, value: Any) -> Any:
        if value is None:
            return None
        if isinstance(value, (dict, list, str, int, float, bool)):
            text = json.dumps(value, ensure_ascii=False, default=str) if not isinstance(value, str) else value
            return self._preview(text) if len(text) > self.artifact_preview_chars else value
        return self._preview(str(value))

    def _tool_origin(self, tool_name: str, explicit_origin: Any) -> str:
        if explicit_origin in {"agent_tool", "common", "platform", "harness", "runtime", "unknown"}:
            return str(explicit_origin)
        if tool_name == "unknown":
            return "unknown"
        return "unknown"


VERIFICATION_AGENT_INSTRUCTIONS = """You are an evidence-based automation result verifier.

Your only job is to determine whether the completed automation run satisfied the supplied verification_goal.

Rules:
- Use only the evidence bundle in the input. Do not assume facts that are not present in the verification goal, task text, execution records, tool outputs, or artifact excerpts.
- Treat the main agent's final output as a claim, not proof.
- Check exactly one final target: verification_goal.
- Do not infer extra final goals from key actions, planning records, file names, or intermediate operations.
- Mark the verification_goal as satisfied only when the supplied evidence supports it.
- Mark the verification_goal as unmet only when the supplied evidence proves the required action/state did not occur or a permanent execution failure prevents it.
- If evidence is insufficient or ambiguous, explain it in evidence/errors and use status=inconclusive unless the verification_goal is proven unmet.
- For visual assertions such as assertWithAI, do not re-inspect screenshot pixels. The execution stage evaluates authored visual assertions through the active backend AI assertion tool. Verify that execution records contain the AI assertion result, verdict metadata, and screenshot artifact reference, that the main agent's structured output reports the corresponding result, and that no supplied evidence contradicts that result.
- Final status must be success only when verification_goal is satisfied; failed only when verification_goal is proven unmet; inconclusive when verification_goal is not proven either way.

Return only the configured structured final output. Do not perform external actions.
"""
