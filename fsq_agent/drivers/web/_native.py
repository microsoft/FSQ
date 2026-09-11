# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""The version-tested Playwright selector serialization boundary."""

import json
import re
from typing import Any

from ._resolve import Deadline


class NativeSelectorError(RuntimeError):
    pass


def normalize_selector(page: Any, source: Any, *, timeout: int | Deadline = 10000) -> tuple[str, dict[str, bool]]:
    deadline = Deadline.from_timeout(timeout)
    if source.count() != 1:
        raise NativeSelectorError("Native locator source must be unique.")
    normalize = getattr(source, "normalize", None)
    if not callable(normalize):
        raise NativeSelectorError("Native selector normalization is unavailable.")
    # Pinned normalize() has no timeout argument and performs no readiness wait.
    deadline.remaining()
    normalized = normalize()
    deadline.remaining()
    # Playwright 1.60 has public normalize(), but no public selector serializer.
    selector = getattr(getattr(normalized, "_impl_obj", None), "_selector", None)
    if not isinstance(selector, str) or not selector.strip():
        raise NativeSelectorError("Native selector serialization is unavailable.")
    if any(re.match(r"^\*?aria-ref\s*=", part) for part in _selector_parts(selector)):
        raise NativeSelectorError("Native selector serialization returned a transient reference.")
    replay = page.locator(selector)
    if replay.count() != 1:
        raise NativeSelectorError("Normalized selector is not unique.")
    handle = source.element_handle(timeout=deadline.remaining())
    if handle is None:
        raise NativeSelectorError("Native locator source detached.")
    try:
        if not replay.evaluate("(node, source) => node === source", handle, timeout=deadline.remaining()):
            raise NativeSelectorError("Normalized selector resolved a different source node.")
    finally:
        handle.dispose()
    return selector, {
        "validated_unique": True,
        "source_identity_verified": True,
        **selector_quality(selector),
    }


def selector_quality(selector: str) -> dict[str, bool]:
    quality = {"structural": False, "positional": False}
    for part in _selector_parts(selector):
        if part.startswith(("internal:has=", "internal:has-not=", "internal:and=", "internal:or=")):
            nested = selector_quality(json.loads(part.split("=", 1)[1]))
            quality = {key: value or nested[key] for key, value in quality.items()}
        quality["structural"] |= not part.startswith(("internal:", "#", "[id=", "[data-testid=", "nth="))
        unquoted = re.sub(r"\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*'", "", part)
        quality["positional"] |= part.startswith("nth=") or (not part.startswith("internal:") and bool(re.search(r":(?:nth|first|last)-", unquoted)))
    return quality


def _selector_parts(selector: str) -> list[str]:
    parts = []
    start = 0
    quote = ""
    escaped = False
    index = 0
    while index < len(selector):
        character = selector[index]
        if escaped:
            escaped = False
        elif character == "\\":
            escaped = True
        elif quote:
            if character == quote:
                quote = ""
        elif character in "\"'`":
            quote = character
        elif selector[index : index + 2] == ">>":
            parts.append(selector[start:index].strip())
            index += 1
            start = index + 1
        index += 1
    parts.append(selector[start:].strip())
    return parts


def selector_steps(selector: str) -> list[dict[str, Any]]:
    """Keep frame transitions explicit without changing native selector strings."""
    steps = []
    query = []
    for part in _selector_parts(selector):
        if part == "internal:control=enter-frame":
            if not query:
                raise NativeSelectorError("Native frame selector has no frame query.")
            steps.extend([{"kind": "playwright", "selector": " >> ".join(query)}, {"kind": "enter_frame"}])
            query = []
        else:
            query.append(part)
    if not query:
        raise NativeSelectorError("Native selector ends in a frame transition.")
    steps.append({"kind": "playwright", "selector": " >> ".join(query)})
    return steps
