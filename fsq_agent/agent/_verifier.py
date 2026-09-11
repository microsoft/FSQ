# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from pathlib import Path

from fsq_agent.agent._structured_output import coerce_agent_final_output
from fsq_agent.models import AgentFinalOutput, StepResult, Task, VerificationResult


class Verifier:
    async def verify(
        self,
        task: Task,
        results: list[StepResult],
        events_path: Path | None = None,
    ) -> VerificationResult:
        _ = events_path
        verifier_steps = [step for step in results if step.tool_name == "agent_runtime.verifier"]
        runner_steps = [step for step in results if step.tool_name == "agent_runtime.runner"]
        runtime_steps = verifier_steps or runner_steps
        if runtime_steps:
            return self._verify_first_parseable_runtime_result(task, runtime_steps, require_parseable=bool(verifier_steps))

        failed_steps = [step for step in results if step.status == "failed"]
        if failed_steps:
            return VerificationResult(
                status="failed",
                summary="One or more execution steps failed.",
                unmet_criteria=self._goal_texts(task) or ["The task flow did not complete successfully."],
                diagnostics=[step.error or step.actual_outcome for step in failed_steps],
            )
        return VerificationResult(
            status="inconclusive",
            summary="Execution completed, but final verification requires an LLM verifier or domain-specific evidence.",
            satisfied_criteria=[],
            unmet_criteria=self._goal_texts(task) or ["No verification goal was reported."],
            diagnostics=["Verifier avoids claiming UI task success without direct evidence."],
        )

    def _verify_first_parseable_runtime_result(
        self,
        task: Task,
        steps: list[StepResult],
        *,
        require_parseable: bool = False,
    ) -> VerificationResult:
        invalid_diagnostics: list[str] = []
        for step in reversed(steps):
            if self._parse_final_output(step.actual_outcome) is None and self._parse_final_output(step.tool_output) is None:
                invalid_diagnostics.append(step.error or step.actual_outcome)
                continue
            result = self._verify_runtime_result(task, step)
            if invalid_diagnostics:
                result.diagnostics.extend(invalid_diagnostics)
            return result
        return VerificationResult(
            status="inconclusive",
            summary="Verifier agent did not produce valid verification JSON." if require_parseable else "No verifier or runner step produced valid verification JSON.",
            satisfied_criteria=[],
            unmet_criteria=[],
            diagnostics=invalid_diagnostics or ["No structured verification output was available."],
        )

    def _verify_runtime_result(self, task: Task, step: StepResult) -> VerificationResult:
        payload = self._parse_final_output(step.tool_output) or self._parse_final_output(step.actual_outcome)
        if payload is None:
            return VerificationResult(
                status="inconclusive",
                summary="Agent runtime completed, but the final output was not valid verification JSON.",
                satisfied_criteria=[],
                unmet_criteria=self._goal_texts(task) or ["No verification goal was reported."],
                diagnostics=[step.actual_outcome],
            )

        return self._from_payload(payload, step)

    def _from_payload(self, payload: AgentFinalOutput, step: StepResult) -> VerificationResult:
        satisfied_criteria = list(payload.satisfied_criteria)
        unmet_criteria = list(payload.unmet_criteria)
        evidence = list(payload.evidence)
        errors = list(payload.errors)
        plan_updates = list(payload.plan_updates)
        summary = payload.summary or "Task verification completed."

        diagnostics = [*evidence, *plan_updates, *errors]
        if not diagnostics:
            diagnostics = [step.actual_outcome]

        return VerificationResult(
            status=payload.status,
            summary=summary,
            satisfied_criteria=satisfied_criteria,
            unmet_criteria=unmet_criteria,
            diagnostics=diagnostics,
        )

    def _parse_final_output(self, output: object) -> AgentFinalOutput | None:
        return coerce_agent_final_output(output)

    def _goal_texts(self, task: Task) -> list[str]:
        if task.verification_goal:
            return [task.verification_goal]
        return list(task.acceptance_criteria)
