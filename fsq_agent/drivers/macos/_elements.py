# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from xml.etree import ElementTree

SOURCE_LIMIT = 8_000_000
NODE_LIMIT = 10000
DEPTH_LIMIT = 128
TEXT_LIMIT = 50
LOCATOR_LIMIT = 1024
TEXT_FIELDS = ("identifier", "name", "label", "value", "title")


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
