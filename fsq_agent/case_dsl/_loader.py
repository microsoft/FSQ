# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from fsq_agent.models import ConfigurationError, FsqCase, FsqCaseConfig

FSQ_CASE_SUFFIX = ".fsq.yaml"
LEGACY_CASE_SUFFIX = ".codex.yaml"


class _CaseYamlLoader(yaml.SafeLoader):
    def construct_mapping(self, node, deep=False):
        keys = set()
        for key_node, _ in node.value:
            key = self.construct_object(key_node, deep=deep)
            if not isinstance(key, str) or key in keys:
                raise ConfigurationError("Duplicate or non-string YAML key.", context={"code": "case.mapping_key"})
            keys.add(key)
        return super().construct_mapping(node, deep=deep)


def _reject_cycles(value, active=None):
    if not isinstance(value, (dict, list)):
        return
    active = set() if active is None else active
    if id(value) in active:
        raise ConfigurationError("Recursive YAML aliases are unsupported.", context={"code": "case.recursive_alias"})
    active.add(id(value))
    for item in value.values() if isinstance(value, dict) else value:
        _reject_cycles(item, active)
    active.remove(id(value))


def _validate_yaml_nodes(node, path, source, active=None):
    active = set() if active is None else active
    context = {"path": str(source), "field_path": list(path)}
    if id(node) in active:
        raise ConfigurationError("Recursive YAML aliases are unsupported.", context={**context, "code": "case.recursive_alias"})
    active.add(id(node))
    if isinstance(node, yaml.MappingNode):
        keys = set()
        for key, value in node.value:
            if not isinstance(key, yaml.ScalarNode) or key.tag != "tag:yaml.org,2002:str":
                raise ConfigurationError("Mapping keys must be strings.", context={**context, "code": "case.mapping_key"})
            child_path = (*path, key.value)
            if key.value in keys:
                raise ConfigurationError("Duplicate YAML key.", context={**context, "code": "case.mapping_key", "field_path": list(child_path)})
            keys.add(key.value)
            _validate_yaml_nodes(value, child_path, source, active)
    elif isinstance(node, yaml.SequenceNode):
        for index, item in enumerate(node.value):
            _validate_yaml_nodes(item, (*path, index), source, active)
    active.remove(id(node))


def is_fsq_case_file(path: str | Path) -> bool:
    name = Path(path).name
    return name.endswith((FSQ_CASE_SUFFIX, LEGACY_CASE_SUFFIX))


def _resolve_discovered_case_path(path: str | Path, discovery_root: Path) -> Path:
    root = discovery_root.expanduser().resolve()
    resolved = Path(path).expanduser().resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ConfigurationError(
            "Discovered case path must stay within the case directory.",
            context={"path": str(path)},
        ) from exc
    return resolved


class FsqCaseLoader:
    def load_case(self, path: str | Path) -> FsqCase:
        case_path = Path(path)
        if not is_fsq_case_file(case_path):
            raise ConfigurationError(
                f"FSQ case files must use the {FSQ_CASE_SUFFIX} suffix.",
                context={"path": str(case_path)},
            )
        try:
            source_text = case_path.read_bytes().decode("utf-8")
            return self.load_text(source_text, case_path)
        except (OSError, UnicodeError, yaml.YAMLError) as exc:
            raise ConfigurationError("Unable to read FSQ case file.", context={"path": str(case_path)}) from exc

    def load_text(self, content: str, path: str | Path) -> FsqCase:
        case_path = Path(path)
        try:
            for index, node in enumerate(yaml.compose_all(content, Loader=yaml.SafeLoader)):
                _validate_yaml_nodes(node, () if index == 0 else ("commands",), case_path)
            docs = list(yaml.load_all(content, Loader=_CaseYamlLoader))
            _reject_cycles(docs)
        except (yaml.YAMLError, RecursionError) as exc:
            raise ConfigurationError("Invalid Case YAML.", context={"path": str(case_path), "code": "case.yaml"}) from exc
        return self._build_case(case_path, docs).model_copy(update={"source_text": content})

    def load_cases(self, path: str | Path) -> list[FsqCase]:
        root = Path(path).expanduser().resolve()
        if root.is_file():
            return [self.load_case(root)]
        candidates = sorted(
            _resolve_discovered_case_path(candidate, root) for candidate in {*root.glob("**/*.fsq.yaml"), *root.glob("**/*.codex.yaml")} if candidate.is_file() and is_fsq_case_file(candidate)
        )
        return [self.load_case(candidate) for candidate in candidates]

    def _build_case(self, path: Path, docs: list[Any]) -> FsqCase:
        if len(docs) not in {1, 2}:
            raise ConfigurationError("Invalid FSQ case file.", context={"path": str(path), "reason": "expected one or two YAML documents"})
        config_doc = docs[0]
        commands_doc = docs[1] if len(docs) == 2 else []
        if not isinstance(config_doc, dict):
            raise ConfigurationError("Invalid FSQ case config.", context={"path": str(path)})
        if commands_doc is None:
            commands_doc = []
        if not isinstance(commands_doc, list):
            raise ConfigurationError("Invalid FSQ case commands.", context={"path": str(path)})
        try:
            config = FsqCaseConfig.model_validate(config_doc)
        except ValidationError as exc:
            errors = [{"type": item["type"], "loc": list(item["loc"]), "msg": "Invalid metadata field."} for item in exc.errors(include_input=False, include_context=False, include_url=False)]
            raise ConfigurationError("Invalid FSQ case config.", context={"path": str(path), "code": "case.metadata", "validation_errors": errors}) from exc
        if config.schema_version != "fsq.ai-test/v1":
            raise ConfigurationError(
                "Unsupported FSQ case schema version.",
                context={"path": str(path), "code": "case.schema_version", "field_path": ["schemaVersion"]},
            )
        return FsqCase(path=path, config=config, commands=commands_doc)
