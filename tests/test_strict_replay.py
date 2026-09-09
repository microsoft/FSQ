# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from pathlib import Path

import pytest

from fsq_agent._capability_bootstrap import build_capability_registry
from fsq_agent.adapters.cli._strict_replay import collect_runtime_secret_refs, resolve_strict_replay_steps
from fsq_agent.case_dsl import FsqCaseLoader, FsqExecutableStepAdapter
from fsq_agent.config._settings import Settings
from fsq_agent.models import ConfigurationError, RuntimeSecretSettings


def _secret_case_steps(tmp_path: Path):
    case_path = tmp_path / "secret.fsq.yaml"
    case_path.write_text(
        """
schemaVersion: fsq.ai-test/v1
name: Secret Replay
platform: android
---
- inputText:
    text: TEST_ACCOUNT_PASSWORD
    textType: runtimeSecret
    target: Password field
""",
        encoding="utf-8",
    )
    return FsqExecutableStepAdapter(registry_snapshot=build_capability_registry().snapshot()).to_executable_steps(FsqCaseLoader().load_case(case_path))


def test_resolve_strict_replay_steps_preserves_runtime_secret_ref_for_core_resolution(
    tmp_path: Path,
) -> None:
    settings = Settings(runtime_secrets=RuntimeSecretSettings(allowed_env_names=["TEST_ACCOUNT_PASSWORD"]))
    steps = _secret_case_steps(tmp_path)

    resolved = resolve_strict_replay_steps(steps, settings)

    assert collect_runtime_secret_refs(steps[0].params) == {"TEST_ACCOUNT_PASSWORD"}
    assert resolved[0].params["text"] == "TEST_ACCOUNT_PASSWORD"
    assert resolved[0].params["textType"] == "runtimeSecret"
    assert steps[0].params["text"] == "TEST_ACCOUNT_PASSWORD"


def test_resolve_strict_replay_steps_requires_allowlisted_secret(tmp_path: Path) -> None:
    steps = _secret_case_steps(tmp_path)

    with pytest.raises(ConfigurationError, match="not allowed"):
        resolve_strict_replay_steps(steps, Settings())


def test_resolve_strict_replay_steps_keeps_missing_text_type_literal(tmp_path: Path) -> None:
    case_path = tmp_path / "literal.fsq.yaml"
    case_path.write_text(
        """
schemaVersion: fsq.ai-test/v1
name: Literal Replay
platform: android
---
- inputText:
    text: TEST_ACCOUNT_PASSWORD
    target: Search field
""",
        encoding="utf-8",
    )
    steps = FsqExecutableStepAdapter(registry_snapshot=build_capability_registry().snapshot()).to_executable_steps(FsqCaseLoader().load_case(case_path))

    resolved = resolve_strict_replay_steps(steps, Settings())

    assert collect_runtime_secret_refs(steps[0].params) == set()
    assert resolved[0].params["text"] == "TEST_ACCOUNT_PASSWORD"
    assert resolved[0].params["textType"] == "literal"
