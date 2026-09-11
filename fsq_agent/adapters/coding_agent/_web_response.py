# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import json
from typing import Any

_RESPONSE_MAX_CHARS = 16000
_OBSERVATION_MAX_CHARS = 12000
_OMITTED = object()
_PRIORITY = (
    "page",
    "url",
    "title",
    "coverage",
    "full_artifact_ref",
    "observation_id",
    "observed_at",
    "locator",
    "locator_unavailable_reason",
    "scope",
    "role",
    "name",
    "state",
    "enabled",
    "visible",
    "checked",
    "selected",
    "focused_element",
    "dialogs",
    "active_dialogs",
    "focused",
    "summary",
    "total",
    "match_count",
    "regions",
    "elements",
)
_EVIDENCE_ONLY = {"runner_result", "safe_replay_params", "harness_output", "schema_metadata", "params", "raw_source", "aria_snapshot", "full_source"}


def _encode(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _size(value: Any) -> int:
    return len(_encode(value))


def _locator_count(value: Any) -> int:
    if isinstance(value, dict):
        if isinstance(value.get("steps"), list):
            return 1
        return sum(_locator_count(item) for item in value.values())
    if isinstance(value, list):
        return sum(_locator_count(item) for item in value)
    return 0


class _Projection:
    def __init__(self) -> None:
        self.omitted_fields = 0
        self.omitted_items = 0
        self.omitted_locators = 0

    def omit(self, value: Any) -> object:
        self.omitted_fields += 1
        self.omitted_locators += _locator_count(value)
        return _OMITTED

    def bounded(self, value: Any, budget: int) -> Any:
        if budget < 2:
            return self.omit(value)
        if isinstance(value, dict):
            # Query paths, observation-bound cursors, and evidence references are indivisible.
            if isinstance(value.get("steps"), list) or ("observation_id" in value and "cursor" in value) or ("path" in value and ("kind" in value or "availability" in value)):
                return value if _size(value) <= budget else self.omit(value)
            locator = value.get("locator")
            if isinstance(locator, dict) and _size({"locator": locator}) > budget:
                return self.omit(value)
            unavailable_reason = value.get("locator_unavailable_reason")
            if locator is None and isinstance(unavailable_reason, str) and _size({"locator_unavailable_reason": unavailable_reason}) > budget:
                return self.omit(value)
            result: dict[str, Any] = {}
            keys = [key for key in _PRIORITY if key in value]
            keys.extend(key for key in value if key not in _PRIORITY and key != "observation")
            if "observation" in value:
                keys.append("observation")
            for key in keys:
                item = value[key]
                if key in _EVIDENCE_ONLY or (key == "source" and not (isinstance(item, dict) and "steps" in item)):
                    self.omit(item)
                    continue
                remaining = budget - _size(result) - _size(key) - 2 - (2 if result else 0)
                projected = self.bounded(item, remaining)
                if projected is not _OMITTED:
                    result[key] = projected
            if isinstance(locator, dict) and "locator" not in result:
                return self.omit(value)
            if locator is None and isinstance(unavailable_reason, str) and result.get("locator_unavailable_reason") != unavailable_reason:
                return self.omit(value)
            return result
        if isinstance(value, list):
            items = []
            for item in value:
                remaining = budget - _size(items) - (2 if items else 0)
                projected = self.bounded(item, remaining)
                if projected is _OMITTED:
                    self.omitted_items += 1
                else:
                    items.append(projected)
            return items
        return value if _size(value) <= budget else self.omit(value)


def _error_synopsis(value: str, max_chars: int = 1200) -> str:
    if _size(value) <= max_chars:
        return value
    low, high = 0, min(len(value), max_chars)
    while low < high:
        middle = (low + high + 1) // 2
        if _size(value[:middle] + "...") <= max_chars:
            low = middle
        else:
            high = middle - 1
    return value[:low] + "..."


def project_web_response(payload: dict[str, Any], *, artifact: dict[str, Any], observation_max_chars: int = _OBSERVATION_MAX_CHARS) -> str:
    """Derive a model view only; the caller retains the untouched execution facts."""
    projection = _Projection()
    header_keys = (
        "status",
        "action_status",
        "action_effect",
        "failure_category",
        "tool_name",
        "tool_origin",
        "capability_name",
        "executor_kind",
        "step_kind",
        "platform",
        "driver_method",
        "fsq_action_name",
        "duration_ms",
        "runner_step_id",
        "source_step_id",
        "step_execution_id",
        "replay",
    )
    header = {key: payload[key] for key in header_keys if key in payload}
    error = payload.get("error_message")
    if isinstance(error, str):
        header["error_message"] = _error_synopsis(error)
    reason = payload.get("replay_unavailable_reason")
    if isinstance(reason, str):
        header["replay_unavailable_reason"] = _error_synopsis(reason, 500)
    source = payload.get("result", {})
    result_metadata = source.get("metadata", {}) if isinstance(source, dict) else {}
    harness_metadata = result_metadata.get("harness_metadata", {}) if isinstance(result_metadata, dict) else {}
    if isinstance(harness_metadata, dict):
        diagnostics = {
            key: harness_metadata[key]
            for key in ("error_code", "parameter_path", "page", "validation_errors", "locator_diagnostics", "match_count", "candidates", "retry_guidance", "observation_error")
            if key in harness_metadata
        }
        if diagnostics:
            header["diagnostics"] = projection.bounded(diagnostics, 700)
    header["artifact"] = artifact
    header["artifact_refs"] = [
        {key: ref[key] for key in ("artifact_id", "kind", "path", "availability", "unavailable_reason", "phase") if key in ref} for ref in payload.get("artifact_refs", []) if isinstance(ref, dict)
    ]
    response = projection.bounded(header, 3200)
    response.setdefault("artifact", {"path": None, "availability": "omitted"})
    response["model_output"] = "bounded_web"
    response["result"] = {"output": None}
    response["projection"] = {
        "omitted_fields": 0,
        "omitted_items": 0,
        "omitted_locators": 0,
        "error_message_truncated": isinstance(error, str) and header["error_message"] != error,
        "runner_result_inline": False,
        "safe_replay_params_inline": False,
        "coverage_basis": "Coverage describes the original observation, not the inline subset.",
        "guidance": "Use complete emitted locators. For omitted content, narrow the observation scope or inspect the full evidence/artifact; never guess a clipped locator.",
    }
    output = source.get("output") if isinstance(source, dict) else None
    # Reserve room for omission counters growing after the body is selected.
    body_budget = min(_OBSERVATION_MAX_CHARS, max(2, observation_max_chars), _RESPONSE_MAX_CHARS - _size(response) - 200)
    body = projection.bounded(output, body_budget)
    response["result"]["output"] = body if body is not _OMITTED else None
    response["projection"].update(
        omitted_fields=projection.omitted_fields,
        omitted_items=projection.omitted_items,
        omitted_locators=projection.omitted_locators,
    )
    return _encode(response)
