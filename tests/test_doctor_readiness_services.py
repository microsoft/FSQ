# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from pathlib import Path
from types import SimpleNamespace

from fsq_agent.agent import check_dynamic_agent_readiness
from fsq_agent.ai_services import check_case_suggestion_readiness
from fsq_agent.environments import PlatformRuntimeService
from fsq_agent.providers import check_provider_readiness


class _Session:
    def __init__(self) -> None:
        self.closed = False

    def close_sync(self) -> None:
        self.closed = True

    def complete_sync(self, request):
        raise AssertionError("Doctor readiness must not send model inference")


def test_provider_readiness_constructs_and_closes_without_inference(monkeypatch) -> None:
    session = _Session()
    monkeypatch.setattr("fsq_agent.providers._factory.prepare_model_provider_session", lambda _settings: session)

    ready, _, _ = check_provider_readiness(object())

    assert ready is True
    assert session.closed is True


def test_suggestion_readiness_constructs_analyzer_and_closes_without_inference(monkeypatch) -> None:
    session = _Session()
    monkeypatch.setattr("fsq_agent.ai_services._factory.build_model_provider_session", lambda _settings: session)

    ready, _, _ = check_case_suggestion_readiness(object())

    assert ready is True
    assert session.closed is True


def test_web_target_checks_are_static_and_do_not_construct_driver(tmp_path: Path, monkeypatch) -> None:
    executable = tmp_path / "Google Chrome.app" / "Contents" / "MacOS" / "Google Chrome"
    executable.parent.mkdir(parents=True)
    executable.write_text("browser", encoding="utf-8")
    settings = SimpleNamespace(
        harness=SimpleNamespace(
            platform="web",
            web=SimpleNamespace(channel="chrome", browser_executable_path=executable),
        )
    )

    def fail_driver_import(*_args, **_kwargs):
        raise AssertionError("Target readiness must not construct a Driver")

    monkeypatch.setattr("fsq_agent.environments._service.discover_android_devices", fail_driver_import)
    service = PlatformRuntimeService()

    assert service.check_target_configuration(settings)[0] is True
    assert service.check_target_availability(settings)[0] is True


def test_android_target_requires_exact_online_device_and_installed_app(monkeypatch) -> None:
    from fsq_agent.models import AndroidDevice, AndroidDeviceDiscoveryResult

    settings = SimpleNamespace(
        harness=SimpleNamespace(
            platform="android",
            android=SimpleNamespace(app_id="com.example.app", serial="device-2"),
        )
    )
    monkeypatch.setattr(
        "fsq_agent.environments._service.discover_android_devices",
        lambda: AndroidDeviceDiscoveryResult(devices=[AndroidDevice(serial="device-1", state="device"), AndroidDevice(serial="device-2", state="device")]),
    )
    calls = []
    monkeypatch.setattr("fsq_agent.environments._service.android_application_is_installed", lambda serial, app_id: calls.append((serial, app_id)) or True)

    ready, _, _ = PlatformRuntimeService().check_target_availability(settings)

    assert ready is True
    assert calls == [("device-2", "com.example.app")]


def test_android_target_fails_when_application_is_absent(monkeypatch) -> None:
    from fsq_agent.models import AndroidDevice, AndroidDeviceDiscoveryResult

    settings = SimpleNamespace(harness=SimpleNamespace(platform="android", android=SimpleNamespace(app_id="com.example.app", serial="device-1")))
    monkeypatch.setattr(
        "fsq_agent.environments._service.discover_android_devices",
        lambda: AndroidDeviceDiscoveryResult(devices=[AndroidDevice(serial="device-1", state="device")]),
    )
    monkeypatch.setattr("fsq_agent.environments._service.android_application_is_installed", lambda *_args: False)

    ready, message, _ = PlatformRuntimeService().check_target_availability(settings)

    assert ready is False
    assert "not installed" in message


def test_android_target_reports_missing_adb_action(monkeypatch) -> None:
    from fsq_agent.models import AndroidDeviceDiscoveryResult

    settings = SimpleNamespace(harness=SimpleNamespace(platform="android", android=SimpleNamespace(app_id="com.example.app", serial="device-1")))
    monkeypatch.setattr(
        "fsq_agent.environments._service.discover_android_devices",
        lambda: AndroidDeviceDiscoveryResult(error_code="adb_missing", error_message="unsafe detail"),
    )

    ready, message, action = PlatformRuntimeService().check_target_availability(settings)

    assert ready is False
    assert "ADB is unavailable" in message
    assert "platform tools" in action


def test_macos_target_requires_available_appium_endpoint(tmp_path: Path, monkeypatch) -> None:
    app = tmp_path / "Example.app"
    app.mkdir()
    settings = SimpleNamespace(
        harness=SimpleNamespace(
            platform="macos",
            macos=SimpleNamespace(appium_server_url="http://127.0.0.1:4723", app_path=app, bundle_id=None),
        )
    )
    monkeypatch.setattr("fsq_agent.environments._service._endpoint_available", lambda _url: False)

    ready, message, _ = PlatformRuntimeService().check_target_availability(settings)

    assert ready is False
    assert "Appium endpoint" in message


def test_dynamic_agent_readiness_builds_static_inputs_without_runtime_session(tmp_path: Path, monkeypatch) -> None:
    prompt = SimpleNamespace(agent_template_path=tmp_path / "agent.j2", task_template_path=None, variables={})
    prompt.agent_template_path.write_text("{{ private_knowledge }} {{ skills }}", encoding="utf-8")
    settings = SimpleNamespace(
        harness=SimpleNamespace(platform="web"),
        agent_runtime=SimpleNamespace(prompt=prompt, local_tool_output=None),
        agent_context=SimpleNamespace(knowledge=SimpleNamespace(root_dir=tmp_path, skills=SimpleNamespace(dir=tmp_path), pre_plan=SimpleNamespace(dir=None))),
        cases=SimpleNamespace(dir=tmp_path),
        output=SimpleNamespace(root_dir=tmp_path, runs_dir=tmp_path),
        runtime_secrets=None,
        skills=[],
    )
    monkeypatch.setattr("fsq_agent.agent._readiness.validate_runtime_settings", lambda _settings: None)
    monkeypatch.setattr("fsq_agent.agent._readiness.CapabilityDefinitionFactory.platform_definitions", lambda *_args, **_kwargs: [])
    monkeypatch.setattr("fsq_agent.agent._readiness.CommonPlatformTools.capability_definitions", list)

    ready, _, _ = check_dynamic_agent_readiness(settings)

    assert ready is True
