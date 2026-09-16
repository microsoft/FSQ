# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import json
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest
from click.testing import CliRunner
from pydantic import ValidationError

from fsq_agent.adapters.cli import main
from fsq_agent.agent_engine import ModelRequest, ModelResult, OutputContract, ToolBinding
from fsq_agent.application import ProviderConfigurationResult, ProviderStatusResult
from fsq_agent.config import Settings, load_user_provider_config, refresh_provider_settings, save_deepseek_provider, save_kimi_provider, save_openai_provider, validate_provider_settings
from fsq_agent.control_plane import ControlPlaneServer, ControlPlaneServerOptions
from fsq_agent.models import ConfigurationError
from fsq_agent.providers import KimiModel, build_model_provider_session, list_kimi_models


def _mock_client(monkeypatch: pytest.MonkeyPatch, handler: Callable[[httpx.Request], httpx.Response]) -> list[httpx.Client]:
    original_client = httpx.Client
    clients: list[httpx.Client] = []

    def create_client(**kwargs):
        client = original_client(transport=httpx.MockTransport(handler), **kwargs)
        clients.append(client)
        return client

    monkeypatch.setattr(httpx, "Client", create_client)
    return clients


@pytest.mark.parametrize(
    "result",
    [
        lambda: ProviderConfigurationResult(provider="kimi", model="kimi-k3"),
        lambda: ProviderConfigurationResult(provider="kimi", model="kimi-k3", region="us"),
        lambda: ProviderConfigurationResult(provider="openai", model="gpt-5", region="cn"),
        lambda: ProviderStatusResult(status="ready", configured=True, provider="kimi", model="kimi-k3", authenticated=True, message="Ready."),
        lambda: ProviderStatusResult(status="ready", configured=True, provider="openai", model="gpt-5", region="global", authenticated=True, message="Ready."),
    ],
)
def test_provider_results_enforce_kimi_only_region(result: Callable[[], object]) -> None:
    with pytest.raises(ValidationError):
        result()


def test_provider_results_serialize_valid_kimi_region_without_changing_other_shapes() -> None:
    assert ProviderConfigurationResult(provider="kimi", model="kimi-k3", region="cn").model_dump()["region"] == "cn"
    assert "region" not in ProviderConfigurationResult(provider="openai", model="gpt-5").model_dump()


@pytest.mark.parametrize(
    ("region", "endpoint"),
    [
        ("cn", "https://api.moonshot.cn/v1/models"),
        ("global", "https://api.moonshot.ai/v1/models"),
    ],
)
def test_kimi_discovery_uses_region_endpoint_and_stable_k3_floor(monkeypatch: pytest.MonkeyPatch, region: str, endpoint: str) -> None:
    eligible = ["kimi-k3", "kimi-k3.1", "kimi-k4", "kimi-k4-code", "kimi-k4-code-highspeed", "kimi-k4-source"]
    excluded = [
        "kimi-k2.7-code",
        "kimi-k03",
        "kimi-k3.01",
        "kimi-k3-preview",
        "kimi-k3-beta2",
        "kimi-k4-alpha",
        "kimi-k4-experimental",
        "kimi-k4-rc1",
        "kimi-k4-release-candidate",
        "kimi-k4-latest",
        "kimi-k3-20260915",
        "kimi-k3-2026-09-15",
        "moonshot-v1-128k",
    ]

    def respond(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == endpoint
        scheme, token = request.headers["authorization"].split(" ", 1)
        assert scheme == "Bearer"
        assert token == "candidate-key"  # noqa: S105 - synthetic test credential.
        assert request.headers["accept"] == "application/json"
        return httpx.Response(200, json={"object": "list", "data": [{"id": item} for item in [*eligible, *excluded, *eligible]]})

    clients = _mock_client(monkeypatch, respond)

    models = list_kimi_models(region=region, api_key=" candidate-key ")

    assert [model.id for model in models] == ["kimi-k4", "kimi-k4-code", "kimi-k4-code-highspeed", "kimi-k4-source", "kimi-k3.1", "kimi-k3"]
    assert all(model.id == model.name for model in models)
    assert all(client.is_closed for client in clients)


@pytest.mark.parametrize("region", ["", "CN", "us", None])
def test_kimi_discovery_rejects_invalid_region_before_network(monkeypatch: pytest.MonkeyPatch, region: object) -> None:
    clients = _mock_client(monkeypatch, lambda request: pytest.fail(f"invalid region contacted {request.url}"))

    with pytest.raises(ConfigurationError) as caught:
        list_kimi_models(region=region, api_key="candidate-key")  # type: ignore[arg-type]

    assert caught.value.context == {"provider": "kimi", "reason": "invalid_candidate"}
    assert clients == []


@pytest.mark.parametrize(
    "payload",
    [
        None,
        {},
        {"object": "list", "data": None},
        {"object": "list", "data": [None]},
        {"object": "list", "data": [{"id": " "}]},
        {"object": "list", "data": [{"id": 5}]},
    ],
)
def test_kimi_discovery_rejects_malformed_response_without_partial_models(monkeypatch: pytest.MonkeyPatch, payload: object) -> None:
    _mock_client(monkeypatch, lambda request: httpx.Response(200, json=payload))

    with pytest.raises(ConfigurationError) as caught:
        list_kimi_models(region="cn", api_key="candidate-key")

    assert caught.value.context == {"provider": "kimi", "region": "cn", "reason": "malformed_response"}


@pytest.mark.parametrize(
    ("status", "reason"),
    [(401, "authentication"), (403, "access_denied"), (429, "rate_limited"), (500, "network"), (302, "network")],
)
def test_kimi_discovery_http_errors_are_safe(monkeypatch: pytest.MonkeyPatch, status: int, reason: str) -> None:
    _mock_client(
        monkeypatch,
        lambda request: httpx.Response(status, text="private-upstream candidate-key", headers={"location": "https://untrusted.example/models"}),
    )

    with pytest.raises(ConfigurationError) as caught:
        list_kimi_models(region="global", api_key="candidate-key")

    assert caught.value.context == {"provider": "kimi", "region": "global", "reason": reason}
    assert "candidate-key" not in str(caught.value)
    assert "private-upstream" not in str(caught.value)


@pytest.mark.parametrize("exception_type,reason", [(httpx.ReadTimeout, "timeout"), (httpx.ConnectError, "network")])
def test_kimi_discovery_network_errors_are_classified(monkeypatch: pytest.MonkeyPatch, exception_type: type[httpx.HTTPError], reason: str) -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        raise exception_type("private-upstream candidate-key", request=request)

    clients = _mock_client(monkeypatch, respond)

    with pytest.raises(ConfigurationError) as caught:
        list_kimi_models(region="cn", api_key="candidate-key")

    assert caught.value.context == {"provider": "kimi", "region": "cn", "reason": reason}
    assert "candidate-key" not in str(caught.value)
    assert all(client.is_closed for client in clients)


def test_kimi_discovery_oversized_response_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    clients = _mock_client(monkeypatch, lambda request: httpx.Response(200, content=b"x" * (1024 * 1024 + 1)))

    with pytest.raises(ConfigurationError) as caught:
        list_kimi_models(region="global", api_key="candidate-key")

    assert caught.value.context == {"provider": "kimi", "region": "global", "reason": "malformed_response"}
    assert all(client.is_closed for client in clients)


def test_kimi_discovery_empty_eligible_set_is_success_and_ignores_image_capability(monkeypatch: pytest.MonkeyPatch) -> None:
    payloads = [
        {"object": "list", "data": [{"id": "kimi-k2.7-code", "supports_image_in": True}]},
        {"object": "list", "data": [{"id": "kimi-k3", "supports_image_in": False}]},
    ]
    _mock_client(monkeypatch, lambda request: httpx.Response(200, json=payloads.pop(0)))

    assert list_kimi_models(region="cn", api_key="candidate-key") == ()
    assert [model.id for model in list_kimi_models(region="cn", api_key="candidate-key")] == ["kimi-k3"]


def test_kimi_save_round_trip_and_session_use_typed_profile(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from fsq_agent.providers import _session as session_module

    captured: dict[str, object] = {}

    class CapturingProvider:
        def __init__(self, **kwargs) -> None:
            captured.update(kwargs)

        def get_model(self, model_name: str):
            captured["model"] = model_name
            return self

        async def complete(self, request: ModelRequest) -> ModelResult:
            return ModelResult(text="ok")

        async def aclose(self) -> None:
            return None

    monkeypatch.setattr(session_module, "create_model_provider", CapturingProvider)
    saved = save_kimi_provider(region="cn", model=" kimi-k3 ", api_key=" candidate-key ", user_config_root=tmp_path)
    loaded = load_user_provider_config(tmp_path)
    settings = refresh_provider_settings(Settings(), tmp_path)
    validate_provider_settings(settings)
    session = build_model_provider_session(settings)
    session.complete_sync(ModelRequest(input="check"))

    assert saved.provider is not None
    assert saved.provider.model_dump() == {"type": "kimi", "region": "cn", "model": "kimi-k3"}
    assert loaded.provider == saved.provider
    assert loaded.api_key == "candidate-key"
    assert settings.agent_runtime.provider == "kimi"
    assert settings.agent_runtime.provider_region == "cn"
    assert settings.agent_runtime.base_url == ""
    assert session.client_config.backend == "kimi_responses"
    assert session.client_config.base_url == "https://api.moonshot.cn/v1"
    assert session.metadata == {"endpoint_family": "kimi", "region": "cn"}
    assert captured["responses_profile"] == "kimi"
    assert captured["model"] == "kimi-k3"
    assert "candidate-key" not in repr(session.client_config)
    assert {path.name for path in (tmp_path / "auth").iterdir()} == {"kimi.json"}


def test_kimi_and_other_provider_replacements_cleanup_only_inactive_credentials(tmp_path: Path) -> None:
    save_openai_provider(model="gpt-5", api_key="openai-key", user_config_root=tmp_path)
    save_kimi_provider(region="global", model="kimi-k3", api_key="kimi-key", user_config_root=tmp_path)
    assert {path.name for path in (tmp_path / "auth").iterdir()} == {"kimi.json"}

    save_openai_provider(model="gpt-5", api_key="new-openai-key", user_config_root=tmp_path)
    assert {path.name for path in (tmp_path / "auth").iterdir()} == {"openai.json"}


def test_kimi_and_deepseek_replacements_remove_reciprocal_credentials(tmp_path: Path) -> None:
    save_deepseek_provider(model="deepseek-flash", api_key="deepseek-key", user_config_root=tmp_path)
    save_kimi_provider(region="global", model="kimi-k3", api_key="kimi-key", user_config_root=tmp_path)
    assert {path.name for path in (tmp_path / "auth").iterdir()} == {"kimi.json"}

    save_deepseek_provider(model="deepseek-flash", api_key="new-deepseek-key", user_config_root=tmp_path)
    assert {path.name for path in (tmp_path / "auth").iterdir()} == {"deepseek.json"}


@pytest.mark.parametrize(
    ("effort", "native"),
    [("low", "low"), ("mid", "high"), ("high", "max")],
)
def test_kimi_response_profile_sends_only_supported_fields(effort: str, native: str) -> None:
    from fsq_agent.agent_engine._conversion import response_parameters

    async def invoke(_call):
        return "ok"

    tool = ToolBinding(
        name="lookup",
        description="Look up a value.",
        parameters_schema={"type": "object", "properties": {}, "additionalProperties": False},
        strict=True,
        invoke=invoke,
    )
    output = OutputContract(
        name="result",
        schema={
            "type": "object",
            "properties": {"value": {"type": "string"}},
            "required": ["value"],
            "additionalProperties": False,
        },
        parse=lambda value: value,
    )

    parameters = response_parameters(
        "kimi-k3",
        [{"role": "user", "content": "check"}],
        "instructions",
        tools=(tool,),
        output=output,
        agent=True,
        reasoning_effort=effort,
        responses_profile="kimi",
    )

    assert parameters["reasoning"] == {"effort": native}
    assert set(parameters) == {"model", "input", "instructions", "reasoning", "tools", "text"}
    assert set(parameters["text"]) == {"format"}
    assert parameters["tools"][0]["name"] == "lookup"


def test_kimi_response_profile_omits_empty_tools_and_text_for_plain_request() -> None:
    from fsq_agent.agent_engine._conversion import response_parameters

    parameters = response_parameters(
        "kimi-k3",
        [{"role": "user", "content": "check"}],
        None,
        reasoning_effort="mid",
        responses_profile="kimi",
    )

    assert parameters == {
        "model": "kimi-k3",
        "reasoning": {"effort": "high"},
        "input": [{"role": "user", "content": "check"}],
    }


def test_kimi_control_plane_discovery_save_and_readback(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, str]] = []

    def discover(*, region: str, api_key: str):
        calls.append((region, api_key))
        return (KimiModel(id="kimi-k3", name="kimi-k3"),)

    monkeypatch.setattr("fsq_agent.application.providers._discover_kimi_models", discover)
    server = ControlPlaneServer(ControlPlaneServerOptions(user_config_root=tmp_path, static_path=tmp_path / "static"))

    status, models = server.handle_post("/api/control-plane/config/kimi/models", {"region": "global", "apiKey": "candidate-key"})
    save_status, saved = server.handle_put(
        "/api/control-plane/config/kimi",
        {"region": "global", "modelName": "kimi-k3", "apiKey": "candidate-key"},
    )
    read_status, current, _headers = server.handle_get("/api/control-plane/config")

    assert status == save_status == read_status == 200
    assert models == {"models": [{"id": "kimi-k3", "name": "kimi-k3"}]}
    assert (
        saved
        == current
        == {
            "configured": True,
            "provider": {"type": "kimi", "region": "global", "modelName": "kimi-k3", "apiKey": "candidate-key"},
        }
    )
    assert calls == [("global", "candidate-key"), ("global", "candidate-key")]


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"region": "", "apiKey": "key"},
        {"region": "us", "apiKey": "key"},
        {"region": [], "apiKey": "key"},
        {"region": "cn", "apiKey": ""},
        {"region": "cn", "apiKey": "key", "baseUrl": "https://untrusted.example"},
    ],
)
def test_kimi_control_plane_rejects_invalid_discovery_body(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, body: dict[str, object]) -> None:
    calls: list[object] = []
    monkeypatch.setattr("fsq_agent.application.providers._discover_kimi_models", lambda **kwargs: calls.append(kwargs))
    server = ControlPlaneServer(ControlPlaneServerOptions(user_config_root=tmp_path))

    status, _error = server.handle_post("/api/control-plane/config/kimi/models", body)

    assert status == 400
    assert calls == []


def test_kimi_cli_non_interactive_requires_and_persists_region(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(
        "fsq_agent.application.providers._discover_kimi_models",
        lambda **kwargs: (KimiModel(id="kimi-k3", name="kimi-k3"),),
    )

    result = CliRunner().invoke(
        main,
        [
            "--output",
            "json",
            "--non-interactive",
            "providers",
            "configure",
            "kimi",
            "--region",
            "global",
            "--model",
            "kimi-k3",
            "--api-key",
            "cli-secret",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["result"]["region"] == "global"
    saved = load_user_provider_config(tmp_path / ".fsq")
    assert saved.provider is not None
    assert saved.provider.model_dump() == {"type": "kimi", "region": "global", "model": "kimi-k3"}
    assert "cli-secret" not in result.output


def test_kimi_cli_human_requires_explicit_region_and_one_model_selection(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(
        "fsq_agent.application.providers._discover_kimi_models",
        lambda **kwargs: (KimiModel(id="kimi-k3", name="kimi-k3"),),
    )

    result = CliRunner().invoke(main, ["providers", "configure", "kimi"], input="global\ncli-secret\n1\n")

    assert result.exit_code == 0, result.output
    assert "Kimi region" in result.output
    assert "Select model" in result.output
    assert "cli-secret" not in result.output
    saved = load_user_provider_config(tmp_path / ".fsq")
    assert saved.provider is not None
    assert saved.provider.model_dump() == {"type": "kimi", "region": "global", "model": "kimi-k3"}


@pytest.mark.parametrize(
    "options",
    [
        ["--model", "kimi-k3", "--api-key", "cli-secret"],
        ["--region", "cn", "--api-key", "cli-secret"],
        ["--region", "cn", "--model", "kimi-k3"],
        ["--region", "cn", "--model", "kimi-k3", "--api-key", "cli-secret", "--base-url", "https://untrusted.example"],
    ],
)
def test_kimi_cli_rejects_incomplete_or_custom_endpoint_options(options: list[str]) -> None:
    result = CliRunner().invoke(main, ["--output", "json", "--non-interactive", "providers", "configure", "kimi", *options])

    assert result.exit_code == 2
    assert "cli-secret" not in result.output
    assert "https://untrusted.example" not in result.output
