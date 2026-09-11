# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import json
import re
from copy import deepcopy

from ._contracts import EngineError

_KEYWORDS = {
    "$defs",
    "$ref",
    "type",
    "title",
    "description",
    "properties",
    "required",
    "additionalProperties",
    "items",
    "prefixItems",
    "anyOf",
    "enum",
    "const",
    "default",
    "format",
    "minimum",
    "maximum",
    "exclusiveMinimum",
    "exclusiveMaximum",
    "minItems",
    "maxItems",
    "minLength",
    "maxLength",
    "pattern",
}


def google_schema(schema: dict) -> dict:
    nodes = []

    def convert(value):
        if not isinstance(value, dict) or set(value) - _KEYWORDS:
            raise EngineError("configuration", "The Google backend cannot represent the supplied schema.")
        nodes.append(value)
        _validate_values(value)
        result = dict(value)
        if "const" in result:
            constant = result.pop("const")
            if "enum" in result and constant not in result["enum"]:
                raise EngineError("configuration", "The supplied schema has incompatible constraints.")
            result["enum"] = [constant]
        result.pop("default", None)
        for key in ("properties", "$defs"):
            if key in result:
                if not isinstance(result[key], dict):
                    raise EngineError("configuration", "The Google backend schema is invalid.")
                result[key] = {name: convert(child) for name, child in result[key].items()}
        for key in ("items", "additionalProperties"):
            if key in result and isinstance(result[key], dict):
                result[key] = convert(result[key])
        for key in ("anyOf", "prefixItems"):
            if key in result:
                result[key] = [convert(child) for child in result[key]]
        return result

    try:
        json.dumps(schema, allow_nan=False)
        result = convert(schema)
        identities = {id(node) for node in nodes}
        for node in nodes:
            if "$ref" in node:
                _validate_reference(node["$ref"], schema, identities)
        return deepcopy(result)
    except (TypeError, ValueError, KeyError, IndexError, RecursionError):
        raise EngineError("configuration", "The Google backend schema is invalid.") from None


def _require(condition: bool) -> None:
    if not condition:
        raise ValueError("Invalid schema value")


def _validate_values(value: dict) -> None:
    if "type" in value:
        types = value["type"] if isinstance(value["type"], list) else [value["type"]]
        _require(bool(types) and all(isinstance(kind, str) and kind in {"object", "array", "string", "number", "integer", "boolean", "null"} for kind in types))
        _require(len(types) == len(set(types)))
    for key in ("title", "description", "format", "pattern", "$ref"):
        if key in value:
            _require(isinstance(value[key], str))
    if "pattern" in value:
        try:
            re.compile(value["pattern"])
        except re.error:
            raise ValueError("Invalid schema pattern") from None
    if "required" in value:
        required = value["required"]
        _require(isinstance(required, list) and all(isinstance(name, str) for name in required))
        _require(len(required) == len(set(required)))
    for key in ("properties", "$defs"):
        if key in value:
            _require(isinstance(value[key], dict) and all(isinstance(name, str) for name in value[key]))
    for key in ("anyOf", "prefixItems"):
        if key in value:
            _require(isinstance(value[key], list) and bool(value[key]))
    if "items" in value:
        _require(isinstance(value["items"], dict))
    if "additionalProperties" in value:
        _require(type(value["additionalProperties"]) is bool or isinstance(value["additionalProperties"], dict))
    if "enum" in value:
        _require(isinstance(value["enum"], list) and bool(value["enum"]))
        encoded = [json.dumps(item, sort_keys=True, allow_nan=False) for item in value["enum"]]
        _require(len(encoded) == len(set(encoded)))
    for key in ("minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum"):
        if key in value:
            _require(type(value[key]) in {int, float})
    for key in ("minItems", "maxItems", "minLength", "maxLength"):
        if key in value:
            _require(type(value[key]) is int and value[key] >= 0)


def _validate_reference(reference: str, root: dict, identities: set[int]) -> None:
    _require(reference == "#" or reference.startswith("#/"))
    resolved = root
    if reference != "#":
        for token in reference[2:].split("/"):
            _require(re.search(r"~(?![01])", token) is None)
            key = token.replace("~1", "/").replace("~0", "~")
            if isinstance(resolved, list):
                _require(re.fullmatch(r"0|[1-9][0-9]*", key) is not None)
                resolved = resolved[int(key)]
            else:
                _require(isinstance(resolved, dict))
                resolved = resolved[key]
    _require(id(resolved) in identities)
