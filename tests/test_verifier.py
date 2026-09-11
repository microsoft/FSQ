# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from pathlib import Path

import pytest

from fsq_agent.agent import Verifier
from fsq_agent.models import StepResult, Task


def _task() -> Task:
    return Task(
        id="verify-1",
        name="Verify structured result",
        description="Complete a task.",
        acceptance_criteria=["Criterion A", "Criterion B"],
        verification_goal="Complete criteria A and B.",
    )


@pytest.mark.asyncio
async def test_verifier_accepts_structured_success() -> None:
    result = await Verifier().verify(
        _task(),
        [
            StepResult(
                step_id=1,
                status="success",
                actual_outcome='{"status":"success","summary":"Done","pre_plan":[],"plan_updates":[],"satisfied_criteria":["Criterion A","Criterion B"],"unmet_criteria":[],"evidence":["Observed A and B"],"errors":[]}',
                tool_name="agent_runtime.runner",
            )
        ],
    )

    assert result.status == "success"
    assert result.satisfied_criteria == ["Criterion A", "Criterion B"]
    assert result.unmet_criteria == []
    assert result.diagnostics == ["Observed A and B"]


@pytest.mark.asyncio
async def test_verifier_preserves_structured_goal_failure() -> None:
    result = await Verifier().verify(
        _task(),
        [
            StepResult(
                step_id=1,
                status="success",
                actual_outcome='{"status":"failed","summary":"Goal unmet","pre_plan":[],"plan_updates":[],"satisfied_criteria":["Criterion A"],"unmet_criteria":["Criterion B"],"evidence":[],"errors":[]}',
                tool_name="agent_runtime.runner",
            )
        ],
    )

    assert result.status == "failed"
    assert result.unmet_criteria == ["Criterion B"]


@pytest.mark.asyncio
async def test_verifier_preserves_structured_goal_inconclusive() -> None:
    result = await Verifier().verify(
        _task(),
        [
            StepResult(
                step_id=1,
                status="success",
                actual_outcome='{"status":"inconclusive","summary":"Partial evidence","pre_plan":[],"plan_updates":[],"satisfied_criteria":["Criterion A"],"unmet_criteria":["Criterion B"],"evidence":["Observed A"],"errors":[]}',
                tool_name="agent_runtime.runner",
            )
        ],
    )

    assert result.status == "inconclusive"
    assert result.unmet_criteria == ["Criterion B"]


@pytest.mark.asyncio
async def test_verifier_uses_structured_runtime_unmet_criteria_when_pre_plan_step_failed() -> None:
    result = await Verifier().verify(
        _task(),
        [
            StepResult(
                step_id=1,
                status="failed",
                actual_outcome="Action: Complete criterion B.\nSuccess criteria:\n- Criterion B",
                tool_name="pre_plan",
            ),
            StepResult(
                step_id=2,
                status="success",
                actual_outcome='{"status":"failed","summary":"Only B failed","pre_plan":[],"plan_updates":["B tool failed"],"satisfied_criteria":["Criterion A"],"unmet_criteria":["Criterion B"],"evidence":["Observed A"],"errors":["B failed"]}',
                tool_name="agent_runtime.runner",
            ),
        ],
    )

    assert result.status == "failed"
    assert result.satisfied_criteria == ["Criterion A"]
    assert result.unmet_criteria == ["Criterion B"]
    assert result.diagnostics == ["Observed A", "B tool failed", "B failed"]


@pytest.mark.asyncio
async def test_verifier_uses_structured_success_when_execution_step_failed() -> None:
    result = await Verifier().verify(
        _task(),
        [
            StepResult(
                step_id=1,
                status="failed",
                actual_outcome="A recovery step failed.",
                tool_name="pre_plan",
            ),
            StepResult(
                step_id=2,
                status="success",
                actual_outcome='{"status":"success","summary":"Done","pre_plan":[],"plan_updates":[],"satisfied_criteria":["Criterion A","Criterion B"],"unmet_criteria":[],"evidence":["Observed A and B"],"errors":[]}',
                tool_name="agent_runtime.runner",
            ),
        ],
    )

    assert result.status == "success"
    assert result.satisfied_criteria == ["Criterion A", "Criterion B"]
    assert result.unmet_criteria == []
    assert result.diagnostics == ["Observed A and B"]


@pytest.mark.asyncio
async def test_verifier_marks_non_json_output_inconclusive() -> None:
    result = await Verifier().verify(
        _task(),
        [
            StepResult(
                step_id=1,
                status="success",
                actual_outcome="I think it worked.",
                tool_name="agent_runtime.runner",
            )
        ],
    )

    assert result.status == "inconclusive"
    assert result.unmet_criteria == []
    assert result.diagnostics == ["I think it worked."]


@pytest.mark.asyncio
async def test_verifier_accepts_agent_derived_criteria_when_task_has_none() -> None:
    task = Task(id="derive-1", name="Derived", description="Open a page.")

    result = await Verifier().verify(
        task,
        [
            StepResult(
                step_id=1,
                status="success",
                actual_outcome='{"status":"success","summary":"Done","pre_plan":[],"plan_updates":[],"satisfied_criteria":["The task flow completed successfully."],"unmet_criteria":[],"evidence":["Flow completed"],"errors":[]}',
                tool_name="agent_runtime.runner",
            )
        ],
    )

    assert result.status == "success"
    assert result.satisfied_criteria == ["The task flow completed successfully."]


@pytest.mark.asyncio
async def test_verifier_uses_agent_status_without_provided_or_derived_criteria() -> None:
    task = Task(id="derive-2", name="Derived", description="Do it.")

    result = await Verifier().verify(
        task,
        [
            StepResult(
                step_id=1,
                status="success",
                actual_outcome='{"status":"success","summary":"Done","pre_plan":[],"plan_updates":[],"satisfied_criteria":[],"unmet_criteria":[],"evidence":[],"errors":[]}',
                tool_name="agent_runtime.runner",
            )
        ],
    )

    assert result.status == "success"
    assert result.unmet_criteria == []


@pytest.mark.asyncio
async def test_verifier_preserves_verification_agent_single_goal_output_without_mode_filtering() -> None:
    task = Task(
        id="verify-goal",
        name="Verify goal",
        description="Access downloads.",
        key_actions=[
            "Key action 1: tapOn Downloads menu item",
            "Key action 2: assertVisible Downloads page",
        ],
        verification_goal="Verify that Downloads can be opened.",
    )
    result = await Verifier().verify(
        task,
        [
            StepResult(
                step_id=1,
                status="success",
                actual_outcome='{"status":"failed","summary":"Goal not complete","pre_plan":[],"plan_updates":[],"satisfied_criteria":[],"unmet_criteria":["Verify that Downloads can be opened."],"evidence":["Downloads page was not reached"],"errors":[]}',
                tool_name="agent_runtime.verifier",
            )
        ],
    )

    assert result.status == "failed"
    assert result.unmet_criteria == ["Verify that Downloads can be opened."]
    assert result.diagnostics == ["Downloads page was not reached"]


def _write_event(events_path: Path, **event: object) -> None:
    import json

    base = {
        "run_id": "run-1",
        "task_id": "task-1",
        "type": "tool_call_completed",
        "title": "Tool call completed",
        "sequence": 1,
        "message": "Tool returned output.",
        "tool_name": None,
        "tool_call_id": None,
        "tool_arguments": None,
        "tool_output_preview": None,
        "duration_ms": None,
        "payload": {},
    }
    base.update(event)
    with events_path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(base, ensure_ascii=False) + "\n")


def _write_tool_call(events_path: Path, call_id: str, tool_name: str, arguments: dict[str, object], output: str, *, failed: bool = False) -> None:
    _write_event(
        events_path,
        type="tool_call_started",
        title="Tool call started",
        sequence=len(events_path.read_text(encoding="utf-8").splitlines()) + 1 if events_path.exists() else 1,
        message=f"Calling {tool_name}.",
        tool_name=tool_name,
        tool_call_id=call_id,
        tool_arguments=arguments,
    )
    _write_event(
        events_path,
        type="tool_call_failed" if failed else "tool_call_completed",
        title="Tool call failed" if failed else "Tool call completed",
        sequence=len(events_path.read_text(encoding="utf-8").splitlines()) + 1,
        message=output,
        tool_call_id=call_id,
        tool_output_preview=output,
    )


@pytest.mark.asyncio
async def test_verifier_prefers_verification_agent_output_over_runner_claims() -> None:
    result = await Verifier().verify(
        _task(),
        [
            StepResult(
                step_id=1,
                status="success",
                actual_outcome='{"status":"inconclusive","summary":"Runner was unsure","pre_plan":[],"plan_updates":[],"satisfied_criteria":["Criterion A"],"unmet_criteria":["Criterion B"],"evidence":["Runner evidence"],"errors":[]}',
                tool_name="agent_runtime.runner",
            ),
            StepResult(
                step_id=2,
                status="success",
                actual_outcome='{"status":"success","summary":"Verifier proved both criteria","pre_plan":[],"plan_updates":[],"satisfied_criteria":["Criterion A","Criterion B"],"unmet_criteria":[],"evidence":["Evidence bundle proved A and B"],"errors":[]}',
                tool_name="agent_runtime.verifier",
            ),
        ],
    )

    assert result.status == "success"
    assert result.satisfied_criteria == ["Criterion A", "Criterion B"]
    assert result.unmet_criteria == []
    assert result.diagnostics == ["Evidence bundle proved A and B"]


@pytest.mark.asyncio
async def test_verifier_uses_successful_verification_agent_output_despite_failed_execution_step() -> None:
    result = await Verifier().verify(
        _task(),
        [
            StepResult(
                step_id=1,
                status="failed",
                actual_outcome="A synthetic pre-plan step failed before evidence verification.",
                tool_name="pre_plan",
            ),
            StepResult(
                step_id=2,
                status="success",
                actual_outcome='{"status":"success","summary":"Verifier proved both criteria","pre_plan":[],"plan_updates":[],"satisfied_criteria":["Criterion A","Criterion B"],"unmet_criteria":[],"evidence":["Evidence bundle proved A and B"],"errors":[]}',
                tool_name="agent_runtime.verifier",
            ),
        ],
    )

    assert result.status == "success"
    assert result.satisfied_criteria == ["Criterion A", "Criterion B"]
    assert result.unmet_criteria == []
    assert result.diagnostics == ["Evidence bundle proved A and B"]


@pytest.mark.asyncio
async def test_verifier_marks_invalid_verification_agent_output_inconclusive() -> None:
    result = await Verifier().verify(
        _task(),
        [
            StepResult(
                step_id=1,
                status="success",
                actual_outcome='{"status":"success","summary":"Runner proved both criteria","pre_plan":[],"plan_updates":[],"satisfied_criteria":["Criterion A","Criterion B"],"unmet_criteria":[],"evidence":["Runner evidence"],"errors":[]}',
                tool_name="agent_runtime.runner",
            ),
            StepResult(
                step_id=2,
                status="failed",
                actual_outcome="Verifier crashed before structured output.",
                error="boom",
                tool_name="agent_runtime.verifier",
            ),
        ],
    )

    assert result.status == "inconclusive"
    assert result.satisfied_criteria == []
    assert result.unmet_criteria == []
    assert "boom" in result.diagnostics
