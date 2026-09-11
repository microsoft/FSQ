# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from importlib.util import find_spec

import pytest
from pydantic import ValidationError

from fsq_agent import models
from fsq_agent.adapters import coding_agent
from fsq_agent.config import Settings


def test_runtime_settings_exports_are_sdk_neutral() -> None:
    runtime_type = models.AgentRuntimeSettings
    prompt_type = models.AgentPromptConfig

    assert runtime_type.__name__ == "AgentRuntimeSettings"
    assert prompt_type.__name__ == "AgentPromptConfig"
    assert isinstance(runtime_type().prompt, prompt_type)
    assert {"AgentRuntimeSettings", "AgentPromptConfig"}.issubset(models.__all__)
    for legacy_name in ("OpenAIAgentsSettings", "OpenAIAgentPromptConfig"):
        assert legacy_name not in models.__all__
        assert not hasattr(models, legacy_name)


def test_settings_use_only_canonical_runtime_field() -> None:
    settings = Settings.model_validate({"agent_runtime": {"max_turns": 7, "prompt": {"variables": {"goal_label": "Checkout"}}}})

    assert settings.agent_runtime.max_turns == 7
    assert settings.agent_runtime.prompt.variables == {"goal_label": "Checkout"}
    assert settings.agent.name == "fsq-agent"
    assert settings.agent.step_timeout_seconds == 60
    assert "agent_runtime" in settings.model_dump()
    assert "openai_agents" not in settings.model_dump()
    assert "agent_runtime" in Settings.model_fields
    assert "openai_agents" not in Settings.model_fields
    assert not hasattr(settings, "openai_agents")


@pytest.mark.parametrize("values", [{"openai_agents": {}}, {"agent_runtime": {}, "openai_agents": {}}])
def test_settings_reject_legacy_runtime_key(values: dict[str, object]) -> None:
    with pytest.raises(ValidationError, match="openai_agents"):
        Settings.model_validate(values)


def test_coding_agent_exports_only_canonical_runtime_and_factory() -> None:
    runtime_type = coding_agent.DefaultCodingAgentRuntime

    assert runtime_type.__name__ == "DefaultCodingAgentRuntime"
    assert runtime_type.__module__ == "fsq_agent.adapters.coding_agent._runtime"
    assert set(coding_agent.__all__) == {"DefaultCodingAgentRuntime", "create_coding_agent_runtime"}
    assert not hasattr(coding_agent, "OpenAIAgentsRuntime")
    assert find_spec("fsq_agent.adapters.coding_agent._openai_runtime") is None
