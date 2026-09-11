# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import json
import logging
from collections.abc import Iterator

import pytest

from fsq_agent.adapters.cli._formatting import log_run_event
from fsq_agent.models import RunEvent


@pytest.fixture
def captured_format_logs() -> Iterator[list[logging.LogRecord]]:
    records: list[logging.LogRecord] = []

    class CaptureHandler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    target = logging.getLogger("fsq_agent.adapters.cli._formatting")
    handler = CaptureHandler()
    previous_level = target.level
    previous_propagate = target.propagate
    target.setLevel(logging.INFO)
    target.propagate = False
    target.addHandler(handler)
    try:
        yield records
    finally:
        target.removeHandler(handler)
        target.setLevel(previous_level)
        target.propagate = previous_propagate


def _messages(records: list[logging.LogRecord]) -> list[str]:
    return [record.getMessage() for record in records]


@pytest.mark.parametrize(
    ("event", "phase"),
    [
        (RunEvent(run_id="run-1", task_id="task", type="planning_started", title="Pre-plan started", sequence=1), "PRE-PLAN"),
        (RunEvent(run_id="run-1", task_id="task", type="planning_update", title="Provider setup started", sequence=2), "STARTUP"),
        (RunEvent(run_id="run-1", task_id="task", type="planning_started", title="Planning started", sequence=3), "EXECUTION"),
        (RunEvent(run_id="run-1", task_id="task", type="planning_update", title="Verification started", sequence=4), "VERIFICATION"),
        (RunEvent(run_id="run-1", task_id="task", type="run_completed", title="Run completed", sequence=5), "RUN"),
        (RunEvent(run_id="run-1", task_id="task", type="planning_update", title="Agent runtime ready", sequence=6), "STARTUP"),
        (
            RunEvent(run_id="run-1", task_id="task", type="planning_update", title="Agent result", sequence=7, payload={"tool_name": "agent_runtime.verifier"}),
            "VERIFICATION",
        ),
    ],
)
def test_log_run_event_concise_renders_phase_labels(captured_format_logs: list[logging.LogRecord], event: RunEvent, phase: str) -> None:
    log_run_event(event)

    assert _messages(captured_format_logs)[0].startswith(f"[{phase} #{event.sequence}]")


def test_log_run_event_concise_summarizes_tool_calls_without_verbose_output(captured_format_logs: list[logging.LogRecord]) -> None:
    verbose_output = json.dumps(
        {
            "status": "passed",
            "runner_result": {"phase_reports": [{"metadata": {"large": "x" * 2000}}]},
            "result": {"output": {"raw": "y" * 2000}},
        }
    )

    log_run_event(
        RunEvent(
            run_id="run-1",
            task_id="task",
            type="tool_call_started",
            title="Tool call started",
            sequence=12,
            tool_name="tap_on",
            tool_arguments={"target": "Downloads"},
            payload={"tool_origin": "platform"},
        )
    )
    log_run_event(
        RunEvent(
            run_id="run-1",
            task_id="task",
            type="tool_call_completed",
            title="Tool call completed",
            sequence=13,
            tool_call_id="call-1",
            tool_output_preview=verbose_output,
            duration_ms=842,
            payload={
                "tool_name": "tap_on",
                "status": "passed",
                "artifact_refs": [
                    {"kind": "screenshot", "path": "artifacts/screenshots/before.png"},
                    {"kind": "ui_snapshot", "path": "artifacts/ui-snapshots/after.json"},
                ],
            },
        )
    )

    messages = _messages(captured_format_logs)
    assert messages[0] == '[EXECUTION #12] tool started: tap_on args={"target":"Downloads"}'
    assert messages[1] == "[EXECUTION #13] tool passed: tap_on duration=842ms artifacts=screenshot,ui_snapshot"
    rendered = "\n".join(messages)
    assert "phase_reports" not in rendered
    assert "yyyy" not in rendered


def test_log_run_event_concise_preserves_null_arguments_and_pairs_completed_tool_name(
    captured_format_logs: list[logging.LogRecord],
) -> None:
    log_run_event(
        RunEvent(
            run_id="run-1",
            task_id="pre-plan",
            type="tool_call_started",
            title="Tool call started",
            sequence=6,
            tool_name="read_knowledge_page",
            tool_call_id="call-knowledge",
            tool_arguments='{"page_id":"edge_android_new_tab_page","file":null,"reason":"Need NTP actions."}',
        )
    )
    log_run_event(
        RunEvent(
            run_id="run-1",
            task_id="pre-plan",
            type="tool_call_completed",
            title="Tool call completed",
            sequence=10,
            tool_call_id="call-knowledge",
            tool_output_preview=json.dumps(
                {
                    "ok": True,
                    "page_id": "edge_android_new_tab_page",
                    "path": "pages/edge_android_new_tab_page.md",
                    "content": "# New Tab Page\n" + ("large " * 1000),
                }
            ),
        )
    )

    assert _messages(captured_format_logs) == [
        '[PRE-PLAN #6] tool started: read_knowledge_page args={"page_id":"edge_android_new_tab_page","file":null,"reason":"Need NTP actions."}',
        "[PRE-PLAN #10] tool passed: read_knowledge_page result=page=edge_android_new_tab_page path=pages/edge_android_new_tab_page.md",
    ]


def test_log_run_event_concise_preserves_explicit_null_tool_arguments(captured_format_logs: list[logging.LogRecord]) -> None:
    log_run_event(
        RunEvent(
            run_id="run-1",
            task_id="task",
            type="tool_call_started",
            title="Tool call started",
            sequence=28,
            tool_name="launch_app",
            tool_arguments='{"app_id":null}',
            payload={"tool_origin": "platform"},
        )
    )
    log_run_event(
        RunEvent(
            run_id="run-1",
            task_id="task",
            type="tool_call_completed",
            title="Tool call completed",
            sequence=30,
            tool_output_preview=json.dumps({"result": {"output": {"app_id": "com.microsoft.emmx"}}, "large": "x" * 1000}),
            payload={
                "tool_name": "launch_app",
                "status": "passed",
                "duration_ms": 2417,
                "artifact_refs": [{"kind": "screenshot", "path": "artifacts/screenshots/before.png"}],
                "runner_result": {"output": {"app_id": "com.microsoft.emmx"}},
            },
        )
    )

    assert _messages(captured_format_logs) == [
        '[EXECUTION #28] tool started: launch_app args={"app_id":null}',
        "[EXECUTION #30] tool passed: launch_app duration=2417ms result=app_id=com.microsoft.emmx artifacts=screenshot",
    ]


def test_log_run_event_concise_surfaces_failed_tool_payload_as_error(captured_format_logs: list[logging.LogRecord]) -> None:
    log_run_event(
        RunEvent(
            run_id="run-1",
            task_id="task",
            type="tool_call_completed",
            title="Tool call completed",
            sequence=14,
            tool_output_preview=json.dumps({"runner_result": {"phase_reports": ["verbose"]}}),
            payload={
                "tool_name": "tap_on",
                "status": "failed",
                "failure_category": "tool_usage_error",
                "error_message": "Target Downloads was not found.",
            },
        )
    )

    assert _messages(captured_format_logs) == ["[EXECUTION #14] tool failed: tap_on failure_category=tool_usage_error error=Target Downloads was not found."]
    assert captured_format_logs[0].levelno == logging.ERROR


def test_log_run_event_concise_keeps_reasoning_summary_concise(captured_format_logs: list[logging.LogRecord]) -> None:
    message = "Need to verify the current page before final output.\n" + ("detail " * 200)

    log_run_event(
        RunEvent(
            run_id="run-1",
            task_id="task",
            type="reasoning_summary",
            title="Reasoning summary",
            sequence=18,
            message=message,
        )
    )

    rendered = _messages(captured_format_logs)[0]
    assert rendered.startswith("[EXECUTION #18] model reason: Need to verify the current page before final output.")
    assert "\n" not in rendered
    assert len(rendered) < 500


def test_log_run_event_concise_suppresses_generic_reasoning_summary(captured_format_logs: list[logging.LogRecord]) -> None:
    log_run_event(
        RunEvent(
            run_id="run-1",
            task_id="task",
            type="reasoning_summary",
            title="Reasoning summary",
            sequence=19,
            message="The model produced a reasoning summary.",
        )
    )

    assert _messages(captured_format_logs) == []


def test_log_run_event_concise_summarizes_structured_agent_messages(captured_format_logs: list[logging.LogRecord]) -> None:
    log_run_event(
        RunEvent(
            run_id="run-1",
            task_id="task",
            type="planning_update",
            title="Agent message",
            sequence=102,
            message=(
                "ResponseOutputMessage(id='abc', content=[ResponseOutputText(annotations=[], "
                'text=\'{"schema_version":"task_run_v1","status":"success",'
                '"summary":"The verification goal is satisfied.","pre_plan":[]}\', type=\'output_text\')])'
            ),
        )
    )

    assert _messages(captured_format_logs) == ["[VERIFICATION #102] update: agent message schema=task_run_v1 status=success summary=The verification goal is satisfied."]


def test_log_run_event_concise_surfaces_existing_report_path_hint(captured_format_logs: list[logging.LogRecord]) -> None:
    log_run_event(
        RunEvent(
            run_id="run-1",
            task_id="task",
            type="run_completed",
            title="Run completed",
            sequence=20,
            duration_ms=1200,
            payload={"status": "success", "report_path": "runs/run-1/report.md"},
        )
    )

    assert _messages(captured_format_logs) == ["[REPORT #20] completed: Run completed duration=1200ms report=runs/run-1/report.md"]


def test_log_run_event_jsonl_preserves_raw_event_json(captured_format_logs: list[logging.LogRecord]) -> None:
    event = RunEvent(
        run_id="run-1",
        task_id="task",
        type="tool_call_completed",
        title="Tool call completed",
        sequence=22,
        tool_output_preview=json.dumps({"runner_result": {"phase_reports": ["verbose"]}}),
        payload={"tool_name": "tap_on", "status": "passed"},
    )

    log_run_event(event, stream_format="jsonl")

    rendered = _messages(captured_format_logs)[0]
    payload = json.loads(rendered)
    assert rendered.startswith("{")
    assert payload["tool_output_preview"] == event.tool_output_preview
    assert payload["payload"] == {"tool_name": "tap_on", "status": "passed"}
