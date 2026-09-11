# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from __future__ import annotations

from copy import deepcopy
from math import isfinite
from typing import Any, TypeGuard

from ._contracts import EngineError

_EMPTY_SCHEMA = {
    "additionalProperties": False,
    "type": "object",
    "properties": {},
    "required": [],
}
_ABSENT = object()
_SUPPORTED_KEYWORDS = {
    "$defs",
    "$ref",
    "definitions",
    "type",
    "title",
    "description",
    "properties",
    "required",
    "additionalProperties",
    "items",
    "anyOf",
    "allOf",
    "enum",
    "const",
    "default",
    "format",
    "pattern",
    "minLength",
    "maxLength",
    "minimum",
    "maximum",
    "exclusiveMinimum",
    "exclusiveMaximum",
    "multipleOf",
    "minItems",
    "maxItems",
}
_SUPPORTED_FORMATS = {"date-time", "time", "date", "duration", "email", "hostname", "ipv4", "ipv6", "uuid"}


def ensure_strict_json_schema(schema: dict[str, Any]) -> dict[str, Any]:
    try:
        schema = deepcopy(schema)
        if schema == {}:
            return deepcopy(_EMPTY_SCHEMA)
        return _ensure_strict_json_schema(schema, path=(), root=schema)
    except (KeyError, TypeError, ValueError, RecursionError):
        raise EngineError("configuration", "Agent backend schema configuration is invalid.") from None


def _require_shape(condition: bool) -> None:
    if not condition:
        raise ValueError("Unsupported schema shape.")


def _validate_schema_shapes(schema: dict[str, Any], root: dict[str, object]) -> None:
    for key in ("$defs", "definitions", "properties"):
        if key in schema:
            _require_shape(is_dict(schema[key]) and all(isinstance(name, str) for name in schema[key]))
    if "items" in schema:
        _require_shape(is_dict(schema["items"]))
    for key in ("anyOf", "allOf"):
        if key in schema:
            _require_shape(is_list(schema[key]) and bool(schema[key]) and all(is_dict(child) for child in schema[key]))
    if "additionalProperties" in schema:
        _require_shape(schema["additionalProperties"] is False)
    if "required" in schema:
        required = schema["required"]
        _require_shape(is_list(required) and all(isinstance(name, str) for name in required))
        _require_shape(len(required) == len(set(required)))
        if "properties" in schema:
            _require_shape(set(required).issubset(schema["properties"]))
    if "type" in schema:
        types = schema["type"] if is_list(schema["type"]) else [schema["type"]]
        _require_shape(bool(types) and all(isinstance(kind, str) and kind in {"object", "array", "string", "number", "integer", "boolean", "null"} for kind in types))
        _require_shape(len(types) == len(set(types)))
    for key in ("title", "description", "format", "pattern", "$ref"):
        if key in schema:
            _require_shape(isinstance(schema[key], str))
    for key in ("minLength", "maxLength", "minItems", "maxItems"):
        if key in schema:
            _require_shape(type(schema[key]) is int and schema[key] >= 0)
    for key in ("minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "multipleOf"):
        if key in schema:
            _require_shape(type(schema[key]) in {int, float} and isfinite(schema[key]))
    if "multipleOf" in schema:
        _require_shape(schema["multipleOf"] > 0)
    if "enum" in schema:
        _require_shape(is_list(schema["enum"]) and bool(schema["enum"]))
    if "$ref" in schema:
        _require_shape(is_dict(resolve_ref(root=root, ref=schema["$ref"])))


def _ensure_strict_json_schema(
    json_schema: object,
    *,
    path: tuple[str, ...],
    root: dict[str, object],
) -> dict[str, Any]:
    if not is_dict(json_schema):
        raise TypeError("Expected a JSON schema object.")
    if set(json_schema) - _SUPPORTED_KEYWORDS:
        raise EngineError("configuration", "The model backend cannot represent the supplied schema constraints.")
    _validate_schema_shapes(json_schema, root)
    if "format" in json_schema and json_schema["format"] not in _SUPPORTED_FORMATS:
        raise EngineError("configuration", "The model backend cannot represent the supplied schema format.")

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

    all_of = json_schema.get("allOf")
    if is_list(all_of):
        if len(all_of) == 1:
            nested = _ensure_strict_json_schema(all_of[0], path=(*path, "allOf", "0"), root=root)
            json_schema.update(_merge_constraints(json_schema, nested))
            json_schema.pop("allOf")
        else:
            raise EngineError("configuration", "The model backend cannot represent the supplied schema constraints.")

    if json_schema.get("default", _ABSENT) is None:
        json_schema.pop("default")

    ref = json_schema.get("$ref")
    if ref and len(json_schema) > 1:
        if not isinstance(ref, str):
            raise ValueError("Invalid JSON schema reference.")
        resolved = resolve_ref(root=root, ref=ref)
        if not is_dict(resolved):
            raise ValueError("Expected a JSON schema reference to an object.")
        json_schema.update(_merge_constraints(resolved, json_schema))
        json_schema.pop("$ref")
        return _ensure_strict_json_schema(json_schema, path=path, root=root)

    return json_schema


def _merge_constraints(base: dict, overlay: dict) -> dict:
    if any(key not in {"title", "description", "default"} and key in base and base[key] != value for key, value in overlay.items()):
        raise EngineError("configuration", "The model backend cannot represent the supplied schema constraints.")
    return {**base, **overlay}


def resolve_ref(*, root: dict[str, object], ref: str) -> object:
    if ref == "#":
        return root
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
