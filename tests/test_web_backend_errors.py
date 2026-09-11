# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import pytest


def test_backend_failure_keeps_precise_reason_and_never_echoes_exception_text():
    from fsq_agent.drivers.web._errors import failure_result

    result = failure_result(RuntimeError("secret=password host=C:\\private"), effect="indeterminate")
    assert result["status"] == "failed"
    assert result["failure_category"] == "action_error"
    assert result["metadata"]["action_effect"] == "indeterminate"
    assert result["metadata"]["replay_unavailable_reason"] == "action_outcome_indeterminate"
    assert result["metadata"]["error_code"] == "interaction_failed"
    assert "secret" not in str(result)
    assert "private" not in str(result)


def test_backend_explicit_preparation_failure_has_safe_cardinality_scope():
    from fsq_agent.drivers.web._errors import WebBackendError, failure_result

    error = WebBackendError("target_ambiguous", "Refine the locator.", match_count=2, page="main", parameter_path="target")
    result = failure_result(error)
    assert result["failure_category"] == "target_resolution_error"
    assert result["metadata"]["error_code"] == "target_ambiguous"
    assert result["metadata"]["match_count"] == 2
    assert result["metadata"]["action_effect"] == "not_started"


def test_backend_timeout_and_closed_scope_have_distinct_reason_codes():
    from fsq_agent.drivers.web._errors import failure_result

    timeout = type("TimeoutError", (Exception,), {})("Timeout 300ms exceeded.")
    assert failure_result(timeout)["metadata"]["error_code"] == "timeout"
    closed = type("TargetClosedError", (Exception,), {})("Page has closed")
    assert failure_result(closed)["failure_category"] == "context_error"


def test_trigger_listener_precedes_trigger_and_is_removed_when_trigger_fails():
    from fsq_agent.drivers.web._events import run_trigger

    order = []

    class Page:
        def on(self, event, callback):
            order.append(("listen", event))

        def remove_listener(self, event, callback):
            order.append(("remove", event))

    with pytest.raises(RuntimeError, match="trigger failed"):
        run_trigger(
            Page(),
            lambda: (_ for _ in ()).throw(RuntimeError("trigger failed")),
            kind="dialog",
            dialog_type="confirm",
            action="accept",
            prompt_text=None,
            timeout=10,
        )
    assert order == [("listen", "dialog"), ("remove", "dialog")]
