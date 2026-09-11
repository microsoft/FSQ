# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Readiness checks preserve authored queries and strict final cardinality."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable

from ._errors import WebBackendError


class Deadline:
    def __init__(self, timeout: int | float) -> None:
        self.end = time.monotonic() + timeout / 1000

    @classmethod
    def from_timeout(cls, timeout: int | float | Deadline) -> Deadline:
        return timeout if isinstance(timeout, cls) else cls(timeout)

    @property
    def expired(self) -> bool:
        return time.monotonic() >= self.end

    def remaining(self) -> float:
        remaining = (self.end - time.monotonic()) * 1000
        if remaining <= 0:
            raise WebBackendError("timeout", "The Web operation exhausted its bounded wait.")
        return remaining


def _pause(page: Any, deadline: Deadline) -> None:
    page.wait_for_timeout(min(25, deadline.remaining()))


def resolve_unique(
    page: Any,
    locator: Any,
    *,
    timeout: int | Deadline,
    visible: bool = False,
    enabled: bool = False,
    editable: bool = False,
    parameter_path: str = "target",
) -> Any:
    deadline = Deadline.from_timeout(timeout)
    count = 0
    readiness = {}
    while True:
        count = locator.count()
        if count > 1:
            raise WebBackendError("target_ambiguous", "The locator matches multiple elements; refine its scope.", match_count=count, parameter_path=parameter_path)
        if count == 1:
            readiness = {}
            if visible:
                readiness["visible"] = locator.is_visible()
            if enabled:
                readiness["enabled"] = locator.is_enabled(timeout=deadline.remaining())
            if editable:
                readiness["editable"] = locator.is_editable(timeout=deadline.remaining())
            if all(readiness.values()):
                return locator
        if deadline.expired:
            code = "target_missing" if count == 0 else "target_not_actionable"
            raise WebBackendError(code, "The target did not become uniquely ready within the bounded wait.", match_count=count, parameter_path=parameter_path, actionability=readiness)
        _pause(page, deadline)


def wait_condition(
    page: Any,
    locator: Any,
    predicate: Callable[[Any], bool],
    *,
    timeout: int | Deadline,
    allow_absent: bool = False,
) -> bool:
    deadline = Deadline.from_timeout(timeout)
    while True:
        if deadline.expired:
            return False
        count = locator.count()
        if count > 1:
            raise WebBackendError("target_ambiguous", "The condition locator matches multiple elements.", match_count=count, parameter_path="target")
        if (count == 0 and allow_absent) or (count == 1 and predicate(locator)):
            return True
        if deadline.expired:
            return False
        _pause(page, deadline)
