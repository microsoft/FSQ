# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from fsq_agent.adapters.cli import _composition
from fsq_agent.config import Settings


def test_create_case_agent_uses_public_runtime_factory(monkeypatch) -> None:
    settings = Settings()
    from types import SimpleNamespace

    sentinel = SimpleNamespace()
    captured = {}

    def fake_from_settings(configured, runtime_factory):
        captured.update(settings=configured, runtime_factory=runtime_factory)
        return sentinel

    monkeypatch.setattr(_composition.FsqAgent, "from_settings", fake_from_settings)
    assert _composition.create_case_agent(settings) is sentinel
    assert captured == {"settings": settings, "runtime_factory": _composition.create_coding_agent_runtime}
