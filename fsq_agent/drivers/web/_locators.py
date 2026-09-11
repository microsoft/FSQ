# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Compile the shared ordered locator path without freezing collection rules."""

import re
from typing import Any

from fsq_agent.models import WebLocator, WebTextAssertion

from ._errors import WebBackendError
from ._resolve import Deadline, resolve_unique


def compile_locator(page: Any, target: WebLocator, *, timeout: int | Deadline) -> Any:
    return _steps(page, target.steps, page=page, timeout=Deadline.from_timeout(timeout))


def text_predicate(predicate: WebTextAssertion) -> re.Pattern[str]:
    pattern = r"\s+".join(re.escape(part) for part in predicate.value.split())
    return re.compile(r"^\s*" + pattern + r"\s*$" if predicate.kind == "equals" else pattern)


def _steps(root: Any, steps: Any, *, page: Any, timeout: Deadline) -> Any:
    current = root
    for index, step in enumerate(steps):
        kind = step.kind
        if kind == "role":
            kwargs = step.model_dump(exclude={"kind", "role"}, exclude_none=True)
            current = current.get_by_role(step.role, **kwargs)
        elif kind in {"text", "label", "placeholder", "test_id", "alt", "title"}:
            method = {"alt": "get_by_alt_text"}.get(kind, f"get_by_{kind}")
            kwargs = {} if kind == "test_id" else {"exact": step.exact}
            current = getattr(current, method)(step.text, **kwargs)
        elif kind in {"css", "xpath", "playwright"}:
            selector = step.selector if kind == "playwright" else f"{kind}={step.selector}"
            current = current.locator(selector)
        elif kind == "filter":
            kwargs = {}
            if step.visible is not None:
                kwargs["visible"] = step.visible
            if step.text is not None:
                kwargs["has_text"] = text_predicate(step.text)
            descendant = getattr(step, "has", None)
            if descendant is not None:
                kwargs["has"] = _steps(page, descendant.steps, page=page, timeout=timeout)
            current = current.filter(**kwargs)
        elif kind in {"first", "last"}:
            current = getattr(current, kind)
        elif kind == "nth":
            current = current.nth(step.index)
        elif kind == "enter_frame":
            try:
                resolve_unique(page, current, timeout=timeout, parameter_path=f"target.steps.{index}")
            except WebBackendError as error:
                if error.code == "target_ambiguous":
                    raise WebBackendError("frame_ambiguous", "The frame query must resolve one iframe.", **error.details) from None
                raise
            if current.evaluate("(node)=>node.tagName", timeout=timeout.remaining()) not in {"IFRAME", "FRAME"}:
                raise WebBackendError("frame_unavailable", "Frame entry requires an iframe element.", parameter_path=f"target.steps.{index}")
            current = current.content_frame
        else:
            raise WebBackendError("invalid_params", "Unsupported locator step.", parameter_path=f"target.steps.{index}")
    return current
