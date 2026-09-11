# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import json
import logging
import re
from typing import Any

from fsq_agent.models import RunEvent, TaskResult

logger = logging.getLogger("fsq_agent.adapters.cli._formatting")

_GENERIC_REASONING_SUMMARY = "The model produced a reasoning summary."
_TOOL_CALLS: dict[tuple[str, str, str], dict[str, Any]] = {}


def log_result(result: TaskResult) -> None:
    logger.info("Task %s: %s", result.task_id, result.status)
    logger.info("Report: %s", result.report.path)


def log_run_event(event: RunEvent, stream_format: str = "concise") -> None:
    if stream_format == "jsonl":
        logger.info(json.dumps(event.model_dump(mode="json"), ensure_ascii=False), extra={"fsq_raw_message": True})
        return

    if event.type == "run_started":
        _forget_run(event.run_id)
    if event.type == "tool_call_started":
        _remember_tool_call(event)

    message = _format_concise_event(event)
    if message is not None:
        _event_logger(event)(message)

    if event.type in {"tool_call_completed", "tool_call_failed"}:
        _forget_tool_call(event)


def _event_logger(event: RunEvent):
    if event.type in {"tool_call_failed", "run_failed"} or _tool_status(event) == "failed":
        return logger.error
    return logger.info


def _format_concise_event(event: RunEvent) -> str | None:
    if event.type in {"tool_call_started", "tool_call_completed", "tool_call_failed"}:
        return _format_tool_event(event)
    if event.type == "reasoning_summary":
        if not event.message or event.message == _GENERIC_REASONING_SUMMARY:
            return None
        return f"[{_event_phase(event)} #{event.sequence}] model reason: {_compact(event.message, limit=420)}"
    if event.type == "planning_update" and event.title == "Agent message":
        return f"[{_event_phase(event)} #{event.sequence}] update: {_agent_message_summary(event.message)}"

    status = _event_status(event)
    line = f"[{_event_phase(event)} #{event.sequence}] {status}: {event.title}"
    if event.message:
        line = f"{line} - {_compact(event.message, limit=420)}"
    if event.duration_ms is not None:
        line = f"{line} duration={event.duration_ms}ms"
    output_hint = _output_hint(event.payload)
    if output_hint:
        line = f"{line} {output_hint}"
    return line


def _format_tool_event(event: RunEvent) -> str:
    status = _tool_status(event)
    line = f"[{_event_phase(event)} #{event.sequence}] tool {status}: {_tool_name(event)}"
    arguments = _compact_tool_arguments(event.tool_arguments)
    if arguments:
        line = f"{line} args={arguments}"

    duration_ms = _duration_ms(event)
    if duration_ms is not None:
        line = f"{line} duration={duration_ms}ms"

    failure_category = event.payload.get("failure_category")
    if failure_category:
        line = f"{line} failure_category={_compact(failure_category, limit=120)}"

    error_message = event.payload.get("error_message") or (event.message if event.type == "tool_call_failed" else None)
    if error_message:
        line = f"{line} error={_compact(error_message, limit=240)}"

    result = _tool_result_summary(event)
    artifacts = _artifact_summary(event.payload)
    output_hint = _output_hint(event.payload)
    if result:
        line = f"{line} result={result}"
    if artifacts:
        line = f"{line} artifacts={artifacts}"
    elif output_hint:
        line = f"{line} {output_hint}"
    elif result:
        pass
    elif _should_show_output_preview(event):
        line = f"{line} output={_compact(event.tool_output_preview, limit=240)}"
    elif event.tool_output_preview and not error_message:
        line = f"{line} output=omitted"
    return line


def _event_phase(event: RunEvent) -> str:
    text = f"{event.title} {event.message}".lower()
    if event.task_id == "pre-plan" or "pre-plan" in text:
        return "PRE-PLAN"
    if event.payload.get("report_path") or "report" in text:
        return "REPORT"
    if "verification" in text or event.payload.get("tool_name") == "agent_runtime.verifier":
        return "VERIFICATION"
    if any(marker in text for marker in ("runtime startup", "provider setup", "harness setup", "tool setup", "agent runtime ready")):
        return "STARTUP"
    if event.type in {"run_started", "run_completed", "run_failed"}:
        return "RUN"
    return "EXECUTION"


def _event_status(event: RunEvent) -> str:
    title = event.title.lower()
    if event.type == "run_failed" or "failed" in title:
        return "failed"
    if "started" in title or event.type in {"run_started", "agent_started", "planning_started"}:
        return "started"
    if "completed" in title or event.type in {"run_completed", "step_completed"}:
        return "completed"
    return "update"


def _tool_status(event: RunEvent) -> str | None:
    status = event.payload.get("status")
    runner_result = event.payload.get("runner_result")
    if status is None and isinstance(runner_result, dict):
        status = runner_result.get("status")
    if status is not None:
        return str(status).lower()
    ok = _json_bool_field(event.tool_output_preview or "", "ok")
    if ok is True:
        return "passed"
    if ok is False:
        return "failed"
    if event.type == "tool_call_started":
        return "started"
    if event.type == "tool_call_failed":
        return "failed"
    if event.type == "tool_call_completed":
        return "completed"
    return None


def _tool_name(event: RunEvent) -> str:
    remembered = _remembered_tool_call(event)
    for value in (
        event.tool_name,
        event.payload.get("tool_name"),
        event.payload.get("capability_name"),
        remembered.get("tool_name"),
        _json_string_field(event.tool_output_preview or "", "tool_name"),
        _json_string_field(event.tool_output_preview or "", "capability_name"),
    ):
        if value:
            return str(value)
    return "unknown"


def _remember_tool_call(event: RunEvent) -> None:
    if not event.tool_call_id:
        return
    _TOOL_CALLS[(event.run_id, event.task_id, event.tool_call_id)] = {
        "tool_name": event.tool_name,
        "tool_arguments": event.tool_arguments,
    }


def _remembered_tool_call(event: RunEvent) -> dict[str, Any]:
    if not event.tool_call_id:
        return {}
    return _TOOL_CALLS.get((event.run_id, event.task_id, event.tool_call_id), {})


def _forget_tool_call(event: RunEvent) -> None:
    if event.tool_call_id:
        _TOOL_CALLS.pop((event.run_id, event.task_id, event.tool_call_id), None)


def _forget_run(run_id: str) -> None:
    for key in [key for key in _TOOL_CALLS if key[0] == run_id]:
        _TOOL_CALLS.pop(key, None)


def _duration_ms(event: RunEvent) -> int | None:
    if event.duration_ms is not None:
        return event.duration_ms
    payload_duration = event.payload.get("duration_ms")
    return payload_duration if isinstance(payload_duration, int) else None


def _artifact_summary(payload: dict[str, Any]) -> str | None:
    refs = payload.get("artifact_refs")
    kinds: list[str] = []
    if isinstance(refs, list):
        for ref in refs:
            if not isinstance(ref, dict):
                continue
            kind = ref.get("kind")
            if kind and str(kind) not in kinds:
                kinds.append(str(kind))
    if kinds:
        return ",".join(kinds)
    artifact_path = payload.get("artifact_path")
    return str(artifact_path) if artifact_path else None


def _tool_result_summary(event: RunEvent) -> str | None:
    knowledge_summary = _knowledge_page_summary(event.tool_output_preview or "")
    if knowledge_summary:
        return knowledge_summary

    app_id = _json_string_field(event.tool_output_preview or "", "app_id")
    if app_id:
        return f"app_id={app_id}"

    runner_result = event.payload.get("runner_result")
    if isinstance(runner_result, dict):
        output_summary = _output_value_summary(runner_result.get("output"))
        if output_summary:
            return output_summary

    parsed_output = _json_object(event.tool_output_preview or "")
    if parsed_output:
        result = parsed_output.get("result")
        if isinstance(result, dict):
            output_summary = _output_value_summary(result.get("output"))
            if output_summary:
                return output_summary
        return _output_value_summary(parsed_output.get("output"))
    return None


def _knowledge_page_summary(text: str) -> str | None:
    page_id = _json_string_field(text, "page_id")
    if not page_id:
        return None
    path = _json_string_field(text, "path")
    summary = f"page={page_id}"
    return f"{summary} path={path}" if path else summary


def _output_value_summary(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, dict):
        parts: list[str] = []
        for key, raw in value.items():
            if raw is None or key in {"content", "html", "source", "xml"}:
                continue
            if isinstance(raw, str) and (len(raw) > 120 or raw.lstrip().startswith("<")):
                continue
            if isinstance(raw, str | int | float | bool):
                parts.append(f"{key}={_compact(raw, limit=80)}")
        return " ".join(parts) if parts else None
    if isinstance(value, str) and (len(value) <= 120 and not value.lstrip().startswith("<")):
        return _compact(value, limit=120)
    if isinstance(value, int | float | bool):
        return _compact(value, limit=80)
    return None


def _output_hint(payload: dict[str, Any]) -> str | None:
    for key, label in (
        ("report_path", "report"),
        ("run_output_path", "run_output"),
        ("output_path", "output"),
        ("events_path", "events"),
    ):
        value = payload.get(key)
        if value:
            return f"{label}={_compact(value, limit=240)}"
    return None


def _should_show_output_preview(event: RunEvent) -> bool:
    if not event.tool_output_preview:
        return False
    has_summary = any(
        key in event.payload
        for key in (
            "status",
            "runner_result",
            "artifact_refs",
            "artifact_path",
            "report_path",
            "run_output_path",
            "output_path",
            "events_path",
            "failure_category",
            "error_message",
        )
    )
    return not has_summary and len(event.tool_output_preview) <= 240


def _compact_tool_arguments(value: object) -> str | None:
    if value is None:
        return None
    decoded = _decode_json_value(value)
    if decoded in ({}, [], None, ""):
        return None
    return _compact(decoded, limit=320)


def _decode_json_value(value: object) -> object:
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


def _agent_message_summary(message: str) -> str:
    response_text = _extract_response_text(message) or message
    schema_version = _json_string_field(response_text, "schema_version")
    status = _json_string_field(response_text, "status")
    summary = _json_string_field(response_text, "summary")

    parts = ["agent message"]
    if schema_version:
        parts.append(f"schema={schema_version}")
    if status:
        parts.append(f"status={status}")
    if summary:
        parts.append(f"summary={_compact(summary, limit=260)}")
    if len(parts) > 1:
        return " ".join(parts)
    if message.startswith("ResponseOutputMessage("):
        return "agent message: structured response omitted"
    return f"agent message: {_compact(message, limit=240)}"


def _extract_response_text(message: str) -> str | None:
    for marker in ("text='", 'text="'):
        start = message.find(marker)
        if start == -1:
            continue
        quote = marker[-1]
        fragment = message[start + len(marker) :]
        end = _find_unescaped_quote(fragment, quote)
        return fragment[:end] if end != -1 else fragment
    return None


def _find_unescaped_quote(text: str, quote: str) -> int:
    escaped = False
    for index, char in enumerate(text):
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if char == quote:
            return index
    return -1


def _json_object(text: str) -> dict[str, Any] | None:
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def _json_string_field(text: str, field: str) -> str | None:
    if not text:
        return None
    normalized = text.replace('\\"', '"')
    match = re.search(rf'"{re.escape(field)}"\s*:\s*"((?:\\.|[^"\\])*)"', normalized)
    if not match:
        return None
    raw = match.group(1)
    try:
        value = json.loads(f'"{raw}"')
    except json.JSONDecodeError:
        value = raw
    return str(value)


def _json_bool_field(text: str, field: str) -> bool | None:
    if not text:
        return None
    normalized = text.replace('\\"', '"')
    match = re.search(rf'"{re.escape(field)}"\s*:\s*(true|false)', normalized)
    if not match:
        return None
    return match.group(1) == "true"


def _compact(value: object, limit: int = 1000) -> str:
    text = json.dumps(value, ensure_ascii=False, default=str, separators=(",", ":")) if not isinstance(value, str) else value
    text = text.replace("\r", " ").replace("\n", " ")
    return text if len(text) <= limit else f"{text[:limit]}..."
