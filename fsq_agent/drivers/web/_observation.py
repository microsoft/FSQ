# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Collect semantic/node facts, validate native identities, then bound display."""

import json
import uuid
from datetime import UTC, datetime
from typing import Any

from fsq_agent import models as m

from ._errors import WebBackendError
from ._native import normalize_selector, selector_quality, selector_steps
from ._resolve import Deadline
from ._semantic import CONTROLS, DOM_FACTS, REGIONS, parse_semantics


def _size(value: Any) -> int:
    return len(json.dumps(value))


def _dump(value: Any) -> dict[str, Any]:
    result = value.model_dump(mode="json", exclude_none=True, exclude_defaults=True)
    if isinstance(value, m.WebObservation):
        result["schema_version"] = value.schema_version
    return result


def _display(value: str, limit: int = 160) -> str:
    return value if len(value) <= limit else value[:limit] + "…"


def _generated(page: Any, source: Any, alias: str, timeout: int | Deadline) -> tuple[m.WebLocator, str, dict[str, bool]]:
    selector, quality = normalize_selector(page, source, timeout=timeout)
    locator = m.WebLocator.model_validate({"page": alias, "steps": selector_steps(selector)})
    grade = "positional" if quality["positional"] else "structural" if quality["structural"] else "semantic"
    return locator, grade, quality


def _state(facts: dict[str, Any], semantic: dict[str, Any]) -> m.WebObservedState:
    values = {key: value for key, value in facts["state"].items() if key in m.WebObservedState.model_fields}
    if isinstance(semantic.get("disabled"), bool):
        values["enabled"] = not semantic["disabled"]
    for key in ("checked", "selected", "expanded"):
        value = semantic.get(key)
        if isinstance(value, bool) or (key == "checked" and value == "mixed"):
            values[key] = value
        attribute = facts["attributes"].get(f"aria-{key}")
        if attribute in {"true", "false", "mixed"}:
            values[key] = attribute if attribute == "mixed" else attribute == "true"
    return m.WebObservedState(**values)


def _authored_quality(steps: Any) -> str:
    structural = False
    for step in steps:
        if step.kind in {"first", "last", "nth"}:
            return "positional"
        if isinstance(step, m.WebSelectorStep):
            native = selector_quality(step.selector)
            if native["positional"]:
                return "positional"
            structural |= step.kind in {"css", "xpath"} or native["structural"]
        descendant = getattr(step, "has", None)
        if descendant is not None:
            grade = _authored_quality(descendant.steps)
            if grade == "positional":
                return grade
            structural |= grade == "structural"
    return "structural" if structural else "semantic"


def _element(
    page: Any,
    source: Any,
    candidate: dict[str, Any],
    alias: str,
    *,
    authored: m.WebLocator | None = None,
    timeout: int | Deadline = 10000,
    max_options: int = 100,
    parent: bool = False,
) -> tuple[m.WebElementView, dict[str, Any]]:
    timeout = Deadline.from_timeout(timeout)
    facts = source.evaluate(DOM_FACTS, timeout=timeout.remaining())
    extra: dict[str, Any] = {}
    try:
        locator, quality, diagnostics = _generated(page, source, alias, timeout)
        if authored is not None:
            locator = authored
            quality = _authored_quality(authored.steps)
            diagnostics = {**diagnostics, "structural": quality == "structural", "positional": quality == "positional"}
        extra.update(locator=locator, quality=quality)
        facts["locator_quality"] = diagnostics
    except Exception:  # noqa: BLE001 - unsupported native generation is descriptive-only, never guessed.
        extra["locator_unavailable_reason"] = "native_locator_generation_or_identity_validation_failed"
    if parent:
        try:
            ancestor = source.locator("xpath=..")
            extra["parent"], _, _ = _generated(page, ancestor, alias, timeout)
        except Exception:  # noqa: BLE001 - parent scope is optional; the element still reports locator availability.
            facts["parent_locator_unavailable_reason"] = "native_parent_locator_unavailable"
    options = facts["options"]
    view = m.WebElementView(
        role=candidate["role"],
        name=candidate["name"],
        state=_state(facts, candidate.get("state", {})),
        options=[m.WebSelectOptionView(**option) for option in options[:max_options]],
        option_count=len(options) if facts["tag"] == "select" else None,
        **extra,
    )
    return view, facts


def _preview(element: m.WebElementView) -> m.WebElementView:
    return element.model_copy(update={"name": _display(element.name)})


def observe(
    page: Any,
    alias: str,
    root: Any,
    *,
    active: bool,
    max_chars: int,
    max_items: int,
    timeout: int | Deadline = 10000,
    max_options: int = 100,
    max_depth: int = 8,
    query: Any = None,
    authored: m.WebLocator | None = None,
) -> dict[str, Any]:
    timeout = Deadline.from_timeout(timeout)
    observation_id = uuid.uuid4().hex[:12]
    title = page.title() if callable(getattr(page, "title", None)) else ""
    page_view = m.WebPageView(page=alias, url=page.url or "", title=title or "", active=active)
    try:
        raw = root.aria_snapshot(mode="ai", timeout=timeout.remaining())
        tree, candidates = parse_semantics(raw) if isinstance(raw, str) and raw.strip() else ([], [])
    except Exception:  # noqa: BLE001 - native observation fallback explicitly discloses reduced coverage.
        return _text_fallback(page, root, page_view, observation_id, max_chars, timeout=timeout)
    if not isinstance(raw, str) or not raw.strip():
        return _text_fallback(page, root, page_view, observation_id, max_chars, semantic_empty=isinstance(raw, str), timeout=timeout)

    semantic_count = _count_tree(tree)
    full_view = m.WebObservationView()
    inline_view = m.WebObservationView()
    source_details = []
    omissions = ["Full semantic structure is retained separately; field contents are omitted for privacy.", "Unloaded or virtualized content totals are unknown unless explicitly reported."]
    coverage = m.WebObservationCoverage(
        semantic="partial",
        locators="partial",
        observed_count=semantic_count,
        virtualized=None,
        omissions=omissions,
    )
    omissions = coverage.omissions
    observation = m.WebObservation(observation_id=observation_id, observed_at=datetime.now(UTC), page=page_view, view=inline_view, coverage=coverage)
    payload = _dump(observation)
    native_candidates = [item for item in candidates if item["role"] in REGIONS | CONTROLS and (item["role"] not in REGIONS or item.get("depth", 0) <= max_depth)]
    if any(item["role"] in REGIONS and item.get("depth", 0) > max_depth for item in candidates):
        omissions.append("Deeper region outlines are omitted by max_depth; semantic traversal is not depth-limited.")
    native_candidates.sort(key=lambda item: 0 if item["state"].get("active") or item["role"] in {"dialog", "alertdialog"} else 1 if item["role"] in REGIONS else 2)
    query_count = query.count() if query is not None else None
    limit = min(query_count, max_items) if query_count is not None else min(len(native_candidates), max_items)
    if query_count is not None:
        semantic_count = query_count
        coverage.observed_count = query_count

    for index in range(limit):
        if timeout.expired:
            omissions.append("Observation wait budget exhausted; remaining locators are unavailable.")
            break
        if query is not None:
            source = query.nth(index)
            _, own = parse_semantics(source.aria_snapshot(mode="ai", timeout=timeout.remaining()))
            candidate = own[0] if own else {"role": "element", "name": "", "state": {}}
        else:
            candidate = native_candidates[index]
            source = page.locator(f"aria-ref={candidate['native_ref']}")
        try:
            element, facts = _element(
                page,
                source,
                candidate,
                alias,
                authored=authored if query_count == 1 else None,
                max_options=max_options,
                timeout=timeout,
                parent=query is not None,
            )
        except Exception:  # noqa: BLE001 - a detached observation candidate is an explicit omission, not action repair.
            if "Some nodes changed during observation." not in omissions:
                omissions.append("Some nodes changed during observation.")
            continue
        element.order = index
        source_details.append({"role": element.role, "name": element.name, "facts": facts, "semantic_parent": candidate.get("parent")})
        full_view.elements.append(element)
        preview = _preview(element)
        if element.role in REGIONS:
            region = m.WebRegionView(**element.model_dump(), depth=candidate.get("depth", 0))
            full_view.regions.append(region)
            inline_view.regions.append(region.model_copy(update={"name": _display(region.name)}))
        else:
            inline_view.elements.append(preview)
        if facts["state"].get("focused"):
            full_view.focused = element
            inline_view.focused = preview
        if facts["modal"] or element.role in {"dialog", "alertdialog"}:
            full_view.dialogs.append(element)
            inline_view.dialogs.append(preview)
        if facts["items"]:
            count = len(facts["items"])
            declared = facts["attributes"].get("aria-setsize", facts["attributes"].get("aria-rowcount", ""))
            total = int(declared) if str(declared).isdigit() and int(declared) >= count else None
            listing = m.WebListView(**element.model_dump(), observed_count=count, total_count=total, complete=total == count, item_names=[item["name"] for item in facts["items"]])
            full_view.lists.append(listing)
            inline_view.lists.append(listing.model_copy(update={"name": _display(listing.name), "item_names": [_display(name, 80) for name in listing.item_names[:5]]}))
            if total is None or total > count:
                coverage.virtualized = True if total is not None else coverage.virtualized
        coverage.omitted_count = max(0, semantic_count - len(full_view.elements))
        payload = _dump(observation)
        if _size(payload) > max_chars:
            _remove_entry(inline_view, preview.order)
            omissions.append("Inline budget omitted whole candidates/locators; inspect a narrower scope.")
            break
    if query_count is not None and query_count > max_items:
        omissions.append("Query matches exceed max_items; refine the query to expose further candidates.")
    elif len(native_candidates) > max_items:
        omissions.append("Additional controls/regions are omitted by max_items; expand a relevant scope.")
    coverage.omitted_count = max(0, semantic_count - len(inline_view.elements) - len(inline_view.regions))
    source_coverage = coverage.model_copy(deep=True, update={"semantic": "complete", "observed_count": _count_tree(tree), "omitted_count": max(0, _count_tree(tree) - len(full_view.elements))})
    source = m.WebObservationSource(
        semantic_text=json.dumps({"semantic_tree": tree, "dom_details": source_details}, ensure_ascii=False),
        view=full_view,
        coverage=source_coverage,
    )
    payload = _bound(observation, max_chars)
    payload["full_source"] = _dump(source)
    return payload


def _remove_entry(view: m.WebObservationView, order: int | None) -> None:
    for field in ("elements", "regions", "lists", "dialogs"):
        setattr(view, field, [item for item in getattr(view, field) if item.order != order])
    if view.focused is not None and view.focused.order == order:
        view.focused = None


def _bound(observation: m.WebObservation, budget: int) -> dict[str, Any]:
    observation.page = observation.page.model_copy(update={"url": _display(observation.page.url, 600), "title": _display(observation.page.title)})
    while True:
        view = observation.view
        if observation.coverage.observed_count is not None:
            observation.coverage.omitted_count = max(0, observation.coverage.observed_count - len(view.elements) - len(view.regions))
        if not any(item.locator is not None for item in [*view.elements, *view.regions]):
            observation.coverage.locators = "unavailable"
        if _size(_dump(observation)) <= budget:
            break
        entries = [item for field in ("elements", "regions", "lists", "dialogs") for item in getattr(view, field)]
        if entries:
            _remove_entry(view, entries[-1].order)
        elif observation.coverage.omissions:
            observation.coverage.omissions.pop()
        elif observation.observed_at is not None:
            observation.observed_at = None
        elif observation.coverage.observed_count is not None:
            observation.coverage.observed_count = None
            observation.coverage.omitted_count = None
        else:
            observation.page = observation.page.model_copy(update={"url": "", "title": "", "active": False})
            if _size(_dump(observation)) > budget:
                raise WebBackendError("observation_budget_too_small", "The budget cannot fit this observation's required identity; increase max_chars.")
            break
    return _dump(observation)


def _text_fallback(page: Any, root: Any, page_view: m.WebPageView, identity: str, budget: int, *, semantic_empty: bool = False, timeout: int | Deadline = 10000) -> dict[str, Any]:
    timeout = Deadline.from_timeout(timeout)
    try:
        text_root = root.locator("body") if root is page else root
        text = text_root.inner_text(timeout=min(timeout.remaining(), 1000))
    except Exception:  # noqa: BLE001 - failed fallback remains an observation failure, never fabricated content.
        text = None
    if text is None:
        raise WebBackendError("observation_failed", "Semantic and text observations are unavailable; no source evidence was captured.")
    coverage = m.WebObservationCoverage(
        semantic="complete" if semantic_empty and not text else "text_only",
        locators="unavailable",
        omissions=["Reduced text-only coverage; no actionable locators available."] if text else ["The observed scope is empty; no actionable locators."],
    )
    view = m.WebObservationView(elements=[m.WebElementView(role="text", name=_display(text, 1000), locator_unavailable_reason="text_only_fallback")] if text else [])
    observation = m.WebObservation(observation_id=identity, page=page_view, view=view, coverage=coverage)
    result = _bound(observation, budget)
    result["full_source"] = _dump(m.WebObservationSource(semantic_text=text, coverage=coverage))
    return result


def _count_tree(tree: list[dict[str, Any]]) -> int:
    return sum(1 + _count_tree(node.get("children", [])) for node in tree)
