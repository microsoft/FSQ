# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from __future__ import annotations

import json
import time

from fsq_agent.config import Settings
from fsq_agent.models import HarnessSettings, OpenAIAgentsSettings
from fsq_agent.models import DoctorProgressEvent
from fsq_agent.providers import ProviderDiagnosticService


def test_copilot_probe_is_nonmutating_and_offers_explicit_refresh(tmp_path, monkeypatch) -> None:
    auth = tmp_path / "auth"
    auth.mkdir()
    oauth_path = auth / "github-copilot-token.json"
    oauth_path.write_text(
        json.dumps({"access_token": "secret-oauth-token", "expires_at": time.time() + 3600}),
        encoding="utf-8",
    )
    settings = Settings(
        openai_agents=OpenAIAgentsSettings(provider="github_copilot"),
        harness=HarnessSettings(platform="android"),
    )
    settings.workspace.root_dir = tmp_path
    monkeypatch.setattr(
        "fsq_agent.providers._diagnostics.httpx.get",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("endpoint must not be called without provider token")),
    )

    checks = ProviderDiagnosticService().probe(settings)

    assert checks[0].status == "fail"
    assert checks[0].fixes[0].repair_action == "provider.refresh_copilot_token"
    assert not (auth / "github-copilot-provider-token.json").exists()
    assert "secret-oauth-token" not in checks[0].model_dump_json()


def test_azure_probe_never_exposes_api_key(tmp_path, monkeypatch) -> None:
    settings = Settings(openai_agents=OpenAIAgentsSettings(provider="azure_openai"))
    settings.openai_agents.model = "deployment"
    settings.openai_agents.base_url = "https://example.openai.azure.com/openai/v1/"
    settings.workspace.root_dir = tmp_path
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "super-secret-key")

    class Response:
        status_code = 401

    monkeypatch.setattr("fsq_agent.providers._diagnostics.httpx.get", lambda *args, **kwargs: Response())

    checks = ProviderDiagnosticService().probe(settings)
    output = "".join(check.model_dump_json() for check in checks)

    assert checks[0].status == "pass"
    assert "super-secret-key" not in output
    assert "https://example.openai.azure.com/openai/v1/" in output


def test_copilot_refresh_emits_targeted_repair_started(tmp_path, monkeypatch) -> None:
    settings = Settings(harness=HarnessSettings(platform="web"))
    settings.workspace.root_dir = tmp_path
    events: list[DoctorProgressEvent] = []

    class Session:
        def close_sync(self):
            return None

    monkeypatch.setattr("fsq_agent.providers._diagnostics.refresh_model_provider_session", lambda _settings: Session())

    ProviderDiagnosticService().refresh_cached_copilot_provider_token(
        settings,
        progress_sink=events.append,
    )

    assert events[0].event_type == "repair_started"
    assert events[0].check_id == "provider.github_copilot.credentials"


def test_provider_emits_endpoint_started_before_network_access(tmp_path, monkeypatch) -> None:
    settings = Settings(openai_agents=OpenAIAgentsSettings(provider="azure_openai"))
    settings.openai_agents.model = "deployment"
    settings.openai_agents.base_url = "https://example.openai.azure.com/openai/v1/"
    settings.workspace.root_dir = tmp_path
    events: list[DoctorProgressEvent] = []

    class Response:
        status_code = 401

    def http_get(*_args, **_kwargs):
        assert events[-1].event_type == "check_started"
        assert events[-1].check_id == "provider.azure_openai.endpoint"
        return Response()

    service = ProviderDiagnosticService(
        environ={"AZURE_OPENAI_API_KEY": "secret"},
        http_get=http_get,
    )

    service.probe(settings, progress_sink=events.append)

    endpoint_events = [event.event_type for event in events if event.check_id == "provider.azure_openai.endpoint"]
    assert endpoint_events == ["check_started", "check_completed"]
