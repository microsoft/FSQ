# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from __future__ import annotations

from typing import TYPE_CHECKING

import yaml

if TYPE_CHECKING:
    from fsq_agent.models import CapabilityRegistrySnapshot, FsqCase

from ._validation import FsqCaseValidator, canonical_value


class _CanonicalDumper(yaml.SafeDumper):
    def ignore_aliases(self, data):
        return True

    def increase_indent(self, flow=False, indentless=False):
        return super().increase_indent(flow, False)


class FsqCaseSerializer:
    def __init__(self, registry_snapshot: CapabilityRegistrySnapshot):
        self.validator = FsqCaseValidator(registry_snapshot)

    def normalize(self, case: FsqCase) -> FsqCase:
        self.validator.validate(case)
        commands = []
        for index, command in enumerate(case.commands):
            _, capability, params, timeout = self.validator.command(case, command, index)
            if timeout is not None:
                params["timeout"] = timeout
            commands.append({capability.replay.alias: params})
        return case.model_copy(deep=True, update={"commands": commands})

    def serialize(self, case: FsqCase) -> bytes:
        normalized = self.normalize(case)
        metadata = canonical_value(normalized.config)
        for public, hooks in [("onCaseStart", normalized.config.on_case_start), ("onCaseComplete", normalized.config.on_case_complete)]:
            entries = []
            for hook in hooks:
                entry = {}
                for action in hook.actions:
                    if action.action_name in entry:
                        entries.append(entry)
                        entry = {}
                    entry[action.action_name] = action.value
                if entry:
                    entries.append(entry)
            metadata[public] = entries
        text = yaml.dump_all([metadata, normalized.commands], Dumper=_CanonicalDumper, allow_unicode=True, sort_keys=False, default_flow_style=False, indent=2, width=1000000, line_break="\n")
        return (text.rstrip("\n") + "\n").encode("utf-8")
