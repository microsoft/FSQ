# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from __future__ import annotations

from copy import deepcopy
from typing import Any, TypeGuard

from ._contracts import EngineError

_EMPTY_SCHEMA = {
    "additionalProperties": False,
    "type": "object",
    "properties": {},
    "required": [],
}
_ABSENT = object()


def ensure_strict_json_schema(schema: dict[str, Any]) -> dict[str, Any]:
    try:
        schema = deepcopy(schema)
        if schema == {}:
            return deepcopy(_EMPTY_SCHEMA)
        return _ensure_strict_json_schema(schema, path=(), root=schema)
    except (KeyError, TypeError, ValueError, RecursionError):
        raise EngineError("configuration", "Agent backend schema configuration is invalid.") from None


def _ensure_strict_json_schema(
    json_schema: object,
    *,
    path: tuple[str, ...],
    root: dict[str, object],
) -> dict[str, Any]:
    if not is_dict(json_schema):
        raise TypeError("Expected a JSON schema object.")

    defs = json_schema.get("$defs")
    if is_dict(defs):
        for def_name, def_schema in defs.items():
            _ensure_strict_json_schema(def_schema, path=(*path, "$defs", def_name), root=root)

    definitions = json_schema.get("definitions")
    if is_dict(definitions):
        for definition_name, definition_schema in definitions.items():
            _ensure_strict_json_schema(definition_schema, path=(*path, "definitions", definition_name), root=root)

    typ = json_schema.get("type")
    if typ == "object" and "additionalProperties" not in json_schema:
        json_schema["additionalProperties"] = False
    elif typ == "object" and "additionalProperties" in json_schema and json_schema["additionalProperties"]:
        raise EngineError("configuration", "Agent backend configuration is invalid.")

    properties = json_schema.get("properties")
    if is_dict(properties):
        json_schema["required"] = list(properties.keys())
        json_schema["properties"] = {key: _ensure_strict_json_schema(prop_schema, path=(*path, "properties", key), root=root) for key, prop_schema in properties.items()}

    items = json_schema.get("items")
    if is_dict(items):
        json_schema["items"] = _ensure_strict_json_schema(items, path=(*path, "items"), root=root)

    any_of = json_schema.get("anyOf")
    if is_list(any_of):
        json_schema["anyOf"] = [_ensure_strict_json_schema(variant, path=(*path, "anyOf", str(index)), root=root) for index, variant in enumerate(any_of)]

    one_of = json_schema.get("oneOf")
    if is_list(one_of):
        existing_any_of = json_schema.get("anyOf", [])
        if not is_list(existing_any_of):
            existing_any_of = []
        json_schema["anyOf"] = existing_any_of + [_ensure_strict_json_schema(variant, path=(*path, "oneOf", str(index)), root=root) for index, variant in enumerate(one_of)]
        json_schema.pop("oneOf")

    all_of = json_schema.get("allOf")
    if is_list(all_of):
        if len(all_of) == 1:
            json_schema.update(_ensure_strict_json_schema(all_of[0], path=(*path, "allOf", "0"), root=root))
            json_schema.pop("allOf")
        else:
            json_schema["allOf"] = [_ensure_strict_json_schema(entry, path=(*path, "allOf", str(index)), root=root) for index, entry in enumerate(all_of)]

    if json_schema.get("default", _ABSENT) is None:
        json_schema.pop("default")

    ref = json_schema.get("$ref")
    if ref and len(json_schema) > 1:
        if not isinstance(ref, str):
            raise ValueError("Invalid JSON schema reference.")
        resolved = resolve_ref(root=root, ref=ref)
        if not is_dict(resolved):
            raise ValueError("Expected a JSON schema reference to an object.")
        json_schema.update({**resolved, **json_schema})
        json_schema.pop("$ref")
        return _ensure_strict_json_schema(json_schema, path=path, root=root)

    return json_schema


def resolve_ref(*, root: dict[str, object], ref: str) -> object:
    if not ref.startswith("#/"):
        raise ValueError("Invalid JSON schema reference format.")
    resolved = root
    for key in ref[2:].split("/"):
        value = resolved[key]
        if not is_dict(value):
            raise ValueError("Invalid JSON schema reference target.")
        resolved = value
    return resolved


def is_dict(value: object) -> TypeGuard[dict[str, object]]:
    return isinstance(value, dict)


def is_list(value: object) -> TypeGuard[list[object]]:
    return isinstance(value, list)
