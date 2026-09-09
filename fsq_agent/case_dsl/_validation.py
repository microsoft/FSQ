# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from __future__ import annotations

import math
from enum import Enum
from typing import Any

from pydantic import BaseModel, ValidationError

from fsq_agent.models import CapabilityRegistrySnapshot, ConfigurationError, FsqCase


def canonical_value(value: Any, field_path: tuple = ()) -> Any:
    if isinstance(value, BaseModel):
        result = {}
        for name, field in type(value).model_fields.items():
            item = getattr(value, name)
            if item is None and not field.is_required() and field.default is None:
                continue
            result[field.serialization_alias or field.alias or name] = canonical_value(item, (*field_path, field.serialization_alias or field.alias or name))
        result.update(canonical_value(value.model_extra or {}, field_path))
        return result
    if isinstance(value, dict):
        if any(not isinstance(key, str) for key in value):
            raise ConfigurationError("Mapping keys must be strings.", context={"code": "case.mapping_key", "field_path": list(field_path)})
        return {key: canonical_value(value[key], (*field_path, key)) for key in sorted(value)}
    if isinstance(value, list):
        return [canonical_value(item, (*field_path, index)) for index, item in enumerate(value)]
    if isinstance(value, Enum):
        return canonical_value(value.value, field_path)
    if value is None or isinstance(value, str | bool | int):
        return value
    if isinstance(value, float) and math.isfinite(value):
        return value
    raise ConfigurationError("Unsupported Case value.", context={"code": "case.value", "field_path": list(field_path)})


class FsqCaseValidator:
    def __init__(self, registry_snapshot: CapabilityRegistrySnapshot):
        self.registry_snapshot = registry_snapshot

    def validate(self, case: FsqCase) -> None:
        if case.config.schema_version != "fsq.ai-test/v1":
            raise ConfigurationError("Unsupported FSQ case schema version.", context={"path": str(case.path), "code": "case.schema_version", "field_path": ["schemaVersion"]})
        names = {}
        for capability in self.registry_snapshot.capabilities:
            for name in {capability.name, capability.replay.alias if capability.replay else None}:
                if name is None:
                    continue
                if name in names:
                    raise ConfigurationError("Ambiguous capability registry.", context={"path": str(case.path), "code": "case.registry_ambiguous"})
                names[name] = capability
        try:
            canonical_value(case.config)
        except ConfigurationError as exc:
            raise ConfigurationError(str(exc), context={"path": str(case.path), **exc.context}) from exc
        for index, command in enumerate(case.commands):
            self.command(case, command, index)

    def command(self, case: FsqCase, command: Any, index: int):
        context = {"path": str(case.path), "step_index": index, "code": "case.command"}
        if isinstance(command, str):
            name, payload = command, {}
        elif isinstance(command, dict) and len(command) == 1:
            name, payload = next(iter(command.items()))
            payload = {} if payload is None else payload
        else:
            raise ConfigurationError("Invalid FSQ command.", context=context)
        capability = self.registry_snapshot.resolve(name) if isinstance(name, str) else None
        if capability is None or capability.replay is None or capability.replay.kind != "fsq_command" or not capability.replay.alias:
            raise ConfigurationError("Command has no active replay capability.", context=context)
        context["action_name"] = name
        if not isinstance(payload, dict):
            raise ConfigurationError("Command parameters must be an object.", context={**context, "validation_errors": [{"loc": [], "type": "mapping_type", "msg": "Expected an object."}]})
        params = dict(payload)
        timeout = params.pop("timeout", None)
        if "timeout" in payload and (type(timeout) is not int or timeout < 1):
            raise ConfigurationError("Timeout must be a positive integer.", context={**context, "field_path": ["timeout"]})
        text = params.get("text")
        if "textType" not in params and isinstance(text, dict) and set(text) == {"runtimeSecret"}:
            params = {**params, "text": text["runtimeSecret"], "textType": "runtimeSecret"}
        try:
            parsed = capability.params_model.model_validate(params)
        except ValidationError as exc:
            errors = [{"type": item["type"], "loc": list(item["loc"]), "msg": "Invalid parameter."} for item in exc.errors(include_input=False, include_context=False, include_url=False)]
            raise ConfigurationError("Invalid FSQ command parameters.", context={**context, "code": "case.parameters", "validation_errors": errors}) from exc
        try:
            canonical = canonical_value(parsed)
        except ConfigurationError as exc:
            raise ConfigurationError(str(exc), context={**context, **exc.context}) from exc
        return name, capability, canonical, timeout
