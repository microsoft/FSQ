# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Safe backend errors: never propagate Playwright call logs or execution text."""

from typing import Any


class WebBackendError(RuntimeError):
    def __init__(self, code: str, message: str, **details: Any) -> None:
        super().__init__(message)
        self.code = code
        self.details = details


def failure_result(error: Exception, *, effect: str = "not_started") -> dict[str, Any]:
    details: dict[str, Any] = {}
    if isinstance(error, WebBackendError):
        code, message, details = error.code, str(error), error.details
    else:
        name = type(error).__name__
        if "Timeout" in name:
            code, message = "timeout", "The bounded Web operation timed out."
        elif "TargetClosed" in name:
            code, message = "page_closed", "The Web page or browser is closed."
        elif "selector" in str(error).lower() and any(word in str(error).lower() for word in ("parse", "unexpected token", "unknown engine")):
            code, message = "invalid_selector", "The selector syntax is invalid; correct the authored selector."
        else:
            code, message = "interaction_failed", "The Web operation failed; inspect current state before repeating an effect."
    if code in {"browser_not_started", "page_closed", "page_unknown", "page_alias_conflict", "unexpected_dialog", "unexpected_popup", "frame_unavailable"}:
        category = "context_error"
    elif code in {"target_missing", "target_ambiguous", "frame_ambiguous", "target_not_actionable", "invalid_selector", "target_detached", "native_selector_unavailable"}:
        category = "target_resolution_error"
    elif code in {"timeout", "event_timeout"}:
        category = "timeout_error"
    elif code == "assertion_failed":
        category = "assertion_error"
    elif code in {"invalid_params", "invalid_continuation"}:
        category = "configuration_error"
    elif code in {"observation_failed", "observation_budget_too_small"}:
        category = "observation_error"
    else:
        category = "action_error"
    if effect != "not_started" and code in {"unexpected_dialog", "unexpected_popup", "event_timeout"}:
        details["replay_unavailable_reason"] = "event_contract_unresolved"
    elif effect == "indeterminate":
        details["replay_unavailable_reason"] = "action_outcome_indeterminate"
    return {
        "status": "failed",
        "output": None,
        "failure_category": category,
        "error_message": message,
        "metadata": {"error_code": code, "action_effect": effect, **details},
    }
