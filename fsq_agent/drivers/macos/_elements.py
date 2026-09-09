# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import hashlib
import json
from xml.etree import ElementTree

from fsq_agent.models import MacOSElementQuery

SOURCE_LIMIT = 8_000_000
NODE_LIMIT = 10000
DEPTH_LIMIT = 128
TEXT_LIMIT = 50
LOCATOR_LIMIT = 1024
RESPONSE_LIMIT = 32000
TEXT_FIELDS = ("identifier", "name", "label", "value")


def parse_source(source: str) -> ElementTree.Element:
    if len(source) > SOURCE_LIMIT:
        raise ValueError("source_limit")
    normalized = source.upper()
    if "<!DOCTYPE" in normalized or "<!ENTITY" in normalized:
        raise ElementTree.ParseError("DTD and entity declarations are not allowed.")
    root = ElementTree.fromstring(source)  # noqa: S314 -- DTD/entities rejected, source size bounded.
    stack = [(root, 1)]
    count = 0
    while stack:
        node, depth = stack.pop()
        count += 1
        if count > NODE_LIMIT or depth > DEPTH_LIMIT:
            raise ValueError("tree_limit")
        stack.extend((child, depth + 1) for child in node)
    return root


def candidate(node: ElementTree.Element) -> dict[str, object]:
    attrs = node.attrib
    display = {key: attrs[key][:TEXT_LIMIT] for key in TEXT_FIELDS if attrs.get(key)}
    if (node.text or "").strip():
        display["text"] = node.text.strip()[:TEXT_LIMIT]
    locator = {}
    unavailable = []
    for key in TEXT_FIELDS:
        value = attrs.get(key)
        if not value:
            continue
        if len(value) > LOCATOR_LIMIT:
            unavailable.append(key)
        else:
            locator["accessibilityId" if key == "identifier" else key] = value
    if locator and len(node.tag) <= 128:
        locator["controlType"] = node.tag
    elif len(node.tag) > 128:
        unavailable.append("controlType")
    return {
        "type": node.tag[:128],
        "display": display,
        "locator": locator or None,
        "locator_unavailable_fields": unavailable,
        "text_truncated": any(len(value) > TEXT_LIMIT for key, value in attrs.items() if key in TEXT_FIELDS) or len((node.text or "").strip()) > TEXT_LIMIT,
        "state": {key: attrs[key].lower() == "true" for key in ("enabled", "visible", "selected") if attrs.get(key, "").lower() in {"true", "false"}},
        "geometry": {key: attrs[key][:32] for key in ("x", "y", "width", "height") if key in attrs},
    }


def matches(node: ElementTree.Element, query: MacOSElementQuery) -> bool:
    if query.control_type is not None and node.tag != query.control_type:
        return False
    for key in ("enabled", "visible", "selected"):
        required = getattr(query, key)
        if required is not None and node.attrib.get(key, "").lower() != str(required).lower():
            return False
    if query.text is None:
        return True
    texts = [node.attrib.get(key, "") for key in TEXT_FIELDS] + [(node.text or "").strip()]
    needle = query.text if query.case_sensitive else query.text.casefold()
    texts = texts if query.case_sensitive else [text.casefold() for text in texts]
    return any(needle == text if query.match == "exact" else needle in text for text in texts if text)


def query_source(source: str, query: MacOSElementQuery) -> dict[str, object]:
    revision = hashlib.sha256(source.encode()).hexdigest()
    base = {"snapshot_type": "macos_element_query", "snapshot_revision": revision, "source_length": len(source), "absence_proves_invisibility": False}
    if query.snapshot_revision is not None and query.snapshot_revision != revision:
        return {
            **base,
            "status": "failed",
            "failure_category": "target_resolution_error",
            "error_message": "Snapshot changed; restart the query at offset zero.",
            "metadata": {"resolution_reason": "stale_snapshot"},
        }
    try:
        root = parse_source(source)
    except (ValueError, ElementTree.ParseError):
        return {**base, "candidates": [], "match_count": 0, "count_is_lower_bound": True, "coverage": "incomplete", "scan_truncated": True, "response_truncated": False, "next_offset": None}
    found = [node for node in root.iter() if matches(node, query)]
    page = []
    size = 0
    response_truncated = False
    for node in found[query.offset : query.offset + query.limit]:
        item = candidate(node)
        item_size = len(json.dumps(item, ensure_ascii=False))
        if size + item_size > RESPONSE_LIMIT:
            response_truncated = True
            break
        page.append(item)
        size += item_size
    end = query.offset + len(page)
    return {
        **base,
        "candidates": page,
        "match_count": len(found),
        "count_is_lower_bound": False,
        "coverage": "complete",
        "scan_truncated": False,
        "response_truncated": response_truncated,
        "next_offset": end if end < len(found) else None,
    }
