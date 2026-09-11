# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import json
import re
from pathlib import Path

from fsq_agent._capability_bootstrap import build_capability_registry

ROOT = Path(__file__).resolve().parents[1]


def test_web_skill_examples_validate_against_active_capabilities() -> None:
    text = (ROOT / "fsq_agent" / "resources" / "skills" / "web-harness.md").read_text(encoding="utf-8")
    examples = re.findall(r"^### `([a-z_]+)`[^\n]*\n(?:(?!^### ).)*?```json\n(.*?)\n```", text, flags=re.MULTILINE | re.DOTALL)
    assert len(examples) >= 12, "Document representative observation, editing, selection, and event arguments."
    registry = build_capability_registry(platform="web").snapshot()
    selection_rules = []
    for name, source in examples:
        capability = registry.resolve(name)
        assert capability is not None, name
        arguments = json.loads(source)
        parsed = capability.params_model.model_validate(arguments)
        normalized = parsed.model_dump(mode="json", by_alias=True, exclude_none=True)
        if name == "click_on" and any(step["kind"] == "first" for step in normalized["target"]["steps"]):
            selection_rules.append(normalized["target"]["steps"])
    assert selection_rules, "Document an explicit first-eligible rule rather than an observed fixed identity."
    assert any(any(step["kind"] == "filter" for step in steps[: next(index for index, step in enumerate(steps) if step["kind"] == "first")]) for steps in selection_rules)
