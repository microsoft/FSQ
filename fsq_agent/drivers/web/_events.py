# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Trigger-bound listeners; never retry an effect to manufacture an event."""

import time
from collections.abc import Callable
from typing import Any

from ._errors import WebBackendError


def run_trigger(
    page: Any,
    trigger: Callable[[], Any],
    *,
    kind: str | None = None,
    timeout: int,
    dialog_type: str | None = None,
    action: str | None = None,
    prompt_text: str | None = None,
) -> Any:
    if kind != "dialog":
        if kind is None:
            return trigger()
        with page.expect_event(kind, timeout=timeout) as pending:
            trigger()
        return pending.value
    received: list[Any] = []
    errors: list[Exception] = []
    deadline = time.monotonic() + timeout / 1000

    def handle(dialog: Any) -> None:
        received.append(dialog)
        try:
            if dialog.type != dialog_type or len(received) > 1:
                errors.append(WebBackendError("unexpected_dialog", "Dialog type did not match the trigger contract."))
            elif action == "accept":
                dialog.accept(prompt_text=prompt_text) if prompt_text is not None else dialog.accept()
            else:
                dialog.dismiss()
        except Exception as error:  # noqa: BLE001 - event callback failures return through the trigger boundary.
            errors.append(error)

    page.on("dialog", handle)
    try:
        try:
            trigger()
        except Exception:
            if errors:
                raise errors[0] from None
            raise
        while not received and time.monotonic() < deadline:
            page.wait_for_timeout(min(25, max(1, (deadline - time.monotonic()) * 1000)))
        if errors:
            raise errors[0]
        if not received:
            raise WebBackendError("event_timeout", "The trigger ran but its expected dialog did not arrive.")
        return {"kind": "dialog", "dialog_type": dialog_type, "action": action}
    finally:
        page.remove_listener("dialog", handle)
