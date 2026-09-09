# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import plistlib
from pathlib import Path
from types import SimpleNamespace

import pytest

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

    monkeypatch.setattr("fsq_agent.environments.providers._android.discover_android_devices", fail_driver_import)
    service = PlatformRuntimeService()

    assert service.check_target_configuration(settings)[0] is True
    assert service.check_target_availability(settings)[0] is True


def test_android_target_requires_exact_online_device_and_installed_app(monkeypatch) -> None:
    monkeypatch.setattr("fsq_agent.environments.providers._android.shutil.which", lambda _: "/adb")
    from fsq_agent.models import AndroidDevice, AndroidDeviceDiscoveryResult

    settings = SimpleNamespace(
        harness=SimpleNamespace(
            platform="android",
            android=SimpleNamespace(app_id="com.example.app", serial="device-2"),
        )
    )
    monkeypatch.setattr(
        "fsq_agent.environments.providers._android.discover_android_devices",
        lambda: AndroidDeviceDiscoveryResult(devices=[AndroidDevice(serial="device-1", state="device"), AndroidDevice(serial="device-2", state="device")]),
    )
    calls = []
    monkeypatch.setattr("fsq_agent.environments.providers._android.package_status", lambda serial, app_id: calls.append((serial, app_id)) or "installed")

    ready, _, _ = PlatformRuntimeService().check_target_availability(settings)

    assert ready is True
    assert calls == [("device-2", "com.example.app")]


def test_android_target_fails_when_application_is_absent(monkeypatch) -> None:
    monkeypatch.setattr("fsq_agent.environments.providers._android.shutil.which", lambda _: "/adb")
    from fsq_agent.models import AndroidDevice, AndroidDeviceDiscoveryResult

    settings = SimpleNamespace(harness=SimpleNamespace(platform="android", android=SimpleNamespace(app_id="com.example.app", serial="device-1")))
    monkeypatch.setattr(
        "fsq_agent.environments.providers._android.discover_android_devices",
        lambda: AndroidDeviceDiscoveryResult(devices=[AndroidDevice(serial="device-1", state="device")]),
    )
    monkeypatch.setattr("fsq_agent.environments.providers._android.package_status", lambda *_args: "absent")

    ready, message, _ = PlatformRuntimeService().check_target_availability(settings)

    assert ready is False
    assert "not installed" in message


def test_android_target_reports_missing_adb_action(monkeypatch) -> None:
    monkeypatch.setattr("fsq_agent.environments.providers._android.shutil.which", lambda _: None)
    from fsq_agent.models import AndroidDeviceDiscoveryResult

    settings = SimpleNamespace(harness=SimpleNamespace(platform="android", android=SimpleNamespace(app_id="com.example.app", serial="device-1")))
    monkeypatch.setattr(
        "fsq_agent.environments.providers._android.discover_android_devices",
        lambda: AndroidDeviceDiscoveryResult(error_code="adb_missing", error_message="unsafe detail"),
    )

    ready, message, action = PlatformRuntimeService().check_target_availability(settings)

    assert ready is False
    assert "ADB is unavailable" in message
    assert "Platform-Tools" in action


def test_macos_target_requires_available_appium_endpoint(tmp_path: Path, monkeypatch) -> None:
    from fsq_agent.models import PlatformPrerequisiteCheck

    monkeypatch.setattr("fsq_agent.environments._service.platform.system", lambda: "Darwin")
    app = tmp_path / "Example.app"
    app.mkdir()
    settings = SimpleNamespace(
        harness=SimpleNamespace(
            platform="macos",
            macos=SimpleNamespace(appium_server_url="http://127.0.0.1:4723", app_path=app, bundle_id=None),
        )
    )
    monkeypatch.setattr(
        PlatformRuntimeService,
        "check_prerequisites",
        lambda *_args: (
            PlatformPrerequisiteCheck(
                identifier="appium_endpoint",
                status="unavailable",
                message="The configured macOS Appium endpoint is unavailable.",
                action="Start Appium.",
            ),
        ),
    )

    ready, message, _ = PlatformRuntimeService().check_target_availability(settings)

    assert ready is False
    assert "Appium endpoint" in message


def test_macos_prerequisites_report_each_ready_component_in_stable_order(tmp_path: Path, monkeypatch) -> None:
    app = tmp_path / "Example.app"
    app.mkdir()
    settings = SimpleNamespace(
        harness=SimpleNamespace(
            platform="macos",
            macos=SimpleNamespace(appium_server_url="http://127.0.0.1:4723", app_path=app, bundle_id="com.example.app"),
        )
    )
    monkeypatch.setattr("fsq_agent.environments._service.platform.system", lambda: "Darwin")
    monkeypatch.setattr("fsq_agent.environments.providers._macos._full_xcode_path", lambda: Path("/Applications/Xcode.app"))
    developer = tmp_path / "Xcode.app" / "Contents" / "Developer"
    xcodebuild = developer / "usr" / "bin" / "xcodebuild"
    xcodebuild.parent.mkdir(parents=True)
    xcodebuild.write_bytes(b"")
    monkeypatch.setattr("fsq_agent.environments.providers._macos._active_developer_directory", lambda: developer)
    monkeypatch.setattr("fsq_agent.environments.providers._macos.shutil.which", lambda name: f"/usr/local/bin/{name}" if name == "appium" else None)
    monkeypatch.setattr("fsq_agent.environments.providers._macos._appium_mac2_driver_installed", lambda _path: True)
    monkeypatch.setattr("fsq_agent.environments.providers._macos._endpoint_available", lambda _url: True)
    monkeypatch.setattr("fsq_agent.environments.providers._macos._macos_bundle_for_app", lambda _path: "com.example.app")

    checks = PlatformRuntimeService().check_prerequisites(settings)

    assert [check.identifier for check in checks] == [
        "xcode_installation",
        "xcode_developer_directory",
        "appium_cli",
        "appium_mac2_driver",
        "appium_endpoint",
        "application_path",
        "bundle_identifier",
    ]
    assert all(check.status == "ready" for check in checks)


def test_macos_prerequisites_distinguish_command_line_tools_and_block_driver_check(tmp_path: Path, monkeypatch) -> None:
    settings = SimpleNamespace(
        harness=SimpleNamespace(
            platform="macos",
            macos=SimpleNamespace(appium_server_url="http://127.0.0.1:4723", app_path=tmp_path / "Missing.app", bundle_id="com.example.missing"),
        )
    )
    monkeypatch.setattr("fsq_agent.environments._service.platform.system", lambda: "Darwin")
    monkeypatch.setattr("fsq_agent.environments.providers._macos._full_xcode_path", lambda: None)
    monkeypatch.setattr("fsq_agent.environments.providers._macos._active_developer_directory", lambda: Path("/Library/Developer/CommandLineTools"))
    monkeypatch.setattr("fsq_agent.environments.providers._macos.shutil.which", lambda _name: None)
    monkeypatch.setattr("fsq_agent.environments.providers._macos._endpoint_available", lambda _url: False)
    monkeypatch.setattr("fsq_agent.environments.providers._macos._macos_bundle_is_installed", lambda _bundle: False)

    checks = {check.identifier: check for check in PlatformRuntimeService().check_prerequisites(settings)}

    assert checks["xcode_installation"].status == "unavailable"
    assert "Full Xcode" in checks["xcode_installation"].message
    assert checks["xcode_developer_directory"].status == "unavailable"
    assert "Command Line Tools" in checks["xcode_developer_directory"].message
    assert checks["appium_cli"].status == "unavailable"
    assert checks["appium_mac2_driver"].status == "not_applicable"
    assert checks["appium_endpoint"].status == "unavailable"
    assert checks["application_path"].status == "unavailable"
    assert checks["bundle_identifier"].status == "unavailable"


def test_macos_target_availability_uses_all_prerequisite_failures(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("fsq_agent.environments._service.platform.system", lambda: "Darwin")
    app = tmp_path / "Example.app"
    app.mkdir()
    settings = SimpleNamespace(
        harness=SimpleNamespace(
            platform="macos",
            macos=SimpleNamespace(appium_server_url="http://127.0.0.1:4723", app_path=app, bundle_id=None),
        )
    )
    monkeypatch.setattr(
        PlatformRuntimeService,
        "check_prerequisites",
        lambda *_args: (
            __import__("fsq_agent.models", fromlist=["PlatformPrerequisiteCheck"]).PlatformPrerequisiteCheck(
                identifier="xcode_developer_directory",
                status="unavailable",
                message="The active developer directory uses Command Line Tools.",
                action="Select full Xcode.",
            ),
        ),
    )

    ready, message, action = PlatformRuntimeService().check_target_availability(settings)

    assert ready is False
    assert "Command Line Tools" in message
    assert action == "Select full Xcode."


def test_macos_active_developer_directory_accepts_nonstandard_full_xcode(tmp_path: Path) -> None:
    developer = tmp_path / "Xcode-Beta.app" / "Contents" / "Developer"
    xcodebuild = developer / "usr" / "bin" / "xcodebuild"
    xcodebuild.parent.mkdir(parents=True)
    xcodebuild.write_text("", encoding="utf-8")

    from fsq_agent.environments.providers._macos import _developer_directory_is_full_xcode

    assert _developer_directory_is_full_xcode(developer) is True


def test_macos_bundle_mismatch_does_not_fall_back_when_app_plist_is_unreadable(tmp_path: Path, monkeypatch) -> None:
    from fsq_agent.models import PlatformPrerequisiteCheck

    app = tmp_path / "Broken.app"
    app.mkdir()
    settings = SimpleNamespace(harness=SimpleNamespace(platform="macos", macos=SimpleNamespace(appium_server_url="http://127.0.0.1:4723", app_path=app, bundle_id="com.example.other")))
    monkeypatch.setattr("fsq_agent.environments._service.platform.system", lambda: "Darwin")
    monkeypatch.setattr("fsq_agent.environments.providers._macos._full_xcode_path", lambda: Path("/Applications/Xcode.app"))
    monkeypatch.setattr("fsq_agent.environments.providers._macos._active_developer_directory", lambda: Path("/Applications/Xcode.app/Contents/Developer"))
    monkeypatch.setattr("fsq_agent.environments.providers._macos._developer_directory_is_full_xcode", lambda _path: True)
    monkeypatch.setattr("fsq_agent.environments.providers._macos.shutil.which", lambda name: f"/usr/local/bin/{name}" if name == "appium" else None)
    monkeypatch.setattr("fsq_agent.environments.providers._macos._appium_mac2_driver_installed", lambda _path: True)
    monkeypatch.setattr("fsq_agent.environments.providers._macos._endpoint_available", lambda _url: True)
    monkeypatch.setattr("fsq_agent.environments.providers._macos._macos_bundle_is_installed", lambda _bundle: True)

    checks = {item.identifier: item for item in PlatformRuntimeService().check_prerequisites(settings)}

    assert isinstance(checks["bundle_identifier"], PlatformPrerequisiteCheck)
    assert checks["bundle_identifier"].status == "unavailable"


def test_macos_endpoint_requires_appium_status_payload(monkeypatch) -> None:
    import io

    from fsq_agent.environments.providers._macos import _endpoint_available

    class _Socket:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

    class _Response(io.BytesIO):
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            self.close()

    monkeypatch.setattr("fsq_agent.environments.providers._macos.socket.create_connection", lambda *_args, **_kwargs: _Socket())
    opener = SimpleNamespace(open=lambda *_args, **_kwargs: _Response(b'{"service":"not-appium"}'))
    monkeypatch.setattr("fsq_agent.environments.providers._macos.urllib.request.build_opener", lambda *_args: opener)

    assert _endpoint_available("http://127.0.0.1:4723") is False

    opener.open = lambda *_args, **_kwargs: _Response(b'{"value":{"ready":true}}')

    assert _endpoint_available("http://127.0.0.1:4723") is True

    opener.open = lambda *_args, **_kwargs: _Response(b'{"value":{"ready":false,"build":{}}}')

    assert _endpoint_available("http://127.0.0.1:4723") is False


@pytest.fixture
def macos_probe_environment(tmp_path: Path, monkeypatch):
    developer = tmp_path / "CustomTools.app" / "Contents" / "Developer"
    builder = developer / "usr" / "bin" / "xcodebuild"
    builder.parent.mkdir(parents=True)
    builder.write_bytes(b"")
    app = tmp_path / "Example.app"
    (app / "Contents").mkdir(parents=True)
    (app / "Contents" / "Info.plist").write_bytes(plistlib.dumps({"CFBundleIdentifier": "com.example.app"}))
    monkeypatch.setattr("fsq_agent.environments._service.platform.system", lambda: "Darwin")
    monkeypatch.setattr("fsq_agent.environments.providers._macos._active_developer_directory", lambda: developer)
    monkeypatch.setattr("fsq_agent.environments.providers._macos._full_xcode_path", lambda: None)
    monkeypatch.setattr("fsq_agent.environments.providers._macos.shutil.which", lambda _name: "/test/appium")
    monkeypatch.setattr("fsq_agent.environments.providers._macos._appium_mac2_driver_installed", lambda _path: True)
    monkeypatch.setattr("fsq_agent.environments.providers._macos._endpoint_available", lambda _url: True)
    return SimpleNamespace(
        harness=SimpleNamespace(
            platform="macos",
            macos=SimpleNamespace(
                app_path=app,
                bundle_id="com.example.app",
                appium_server_url="http://127.0.0.1:4723",
            ),
        )
    )


def test_custom_active_xcode_satisfies_installation_and_developer_checks(macos_probe_environment) -> None:
    service = PlatformRuntimeService()
    checks = service.check_prerequisites(macos_probe_environment)
    assert len(checks) == 7
    assert all(item.status == "ready" for item in checks)
    assert service.check_target_availability(macos_probe_environment, checks)[0] is True


def test_missing_appium_url_keeps_independent_prerequisites(macos_probe_environment, monkeypatch) -> None:
    macos_probe_environment.harness.macos.appium_server_url = None
    monkeypatch.setattr("fsq_agent.environments.providers._macos._endpoint_available", lambda _url: False)
    checks = PlatformRuntimeService().check_prerequisites(macos_probe_environment)
    assert len(checks) == 7
    assert checks[4].status == "unavailable"
    assert checks[4].commands == ()
    assert all(item.status == "ready" for item in checks[:4] + checks[5:])


def test_macos_prerequisites_accept_executable_target(macos_probe_environment, tmp_path: Path) -> None:
    executable = tmp_path / "application"
    executable.write_bytes(b"")
    executable.chmod(0o755)
    macos_probe_environment.harness.macos.app_path = executable
    macos_probe_environment.harness.macos.bundle_id = None
    service = PlatformRuntimeService()
    checks = {item.identifier: item for item in service.check_prerequisites(macos_probe_environment)}
    assert checks["application_path"].status == "ready"
    assert checks["bundle_identifier"].status == "not_applicable"
    assert service.check_target_availability(macos_probe_environment)[0] is True


@pytest.mark.parametrize("plist_content", [plistlib.dumps([]), b"not a plist", plistlib.dumps({"CFBundleIdentifier": 7})])
def test_bad_plist_keeps_other_diagnostics(macos_probe_environment, plist_content: bytes) -> None:
    (macos_probe_environment.harness.macos.app_path / "Contents" / "Info.plist").write_bytes(plist_content)
    checks = PlatformRuntimeService().check_prerequisites(macos_probe_environment)
    assert len(checks) == 7
    assert checks[-1].identifier == "bundle_identifier"
    assert checks[-1].status == "unavailable"
    assert all(item.status == "ready" for item in checks[:-1])


@pytest.mark.parametrize(
    ("helper", "identifier"),
    [
        ("_endpoint_available", "appium_endpoint"),
        ("_macos_application_path_available", "application_path"),
        ("_macos_bundle_matches_target", "bundle_identifier"),
        ("_appium_mac2_driver_installed", "appium_mac2_driver"),
    ],
)
def test_individual_probe_error_does_not_discard_other_checks(macos_probe_environment, monkeypatch, helper: str, identifier: str) -> None:
    def fail(*_args):
        raise RuntimeError("private probe detail")

    monkeypatch.setattr(f"fsq_agent.environments.providers._macos.{helper}", fail)
    checks = PlatformRuntimeService().check_prerequisites(macos_probe_environment)
    assert len(checks) == 7
    assert next(item for item in checks if item.identifier == identifier).status == "error"
    assert all(item.status == "ready" for item in checks if item.identifier != identifier)
    assert "private probe detail" not in str(checks)


def test_executable_in_bundle_checks_its_own_identity(macos_probe_environment, monkeypatch) -> None:
    bundle = macos_probe_environment.harness.macos.app_path
    executable = bundle / "Contents" / "MacOS" / "Example"
    executable.parent.mkdir()
    executable.write_bytes(b"")
    executable.chmod(0o755)
    macos_probe_environment.harness.macos.app_path = executable
    monkeypatch.setattr("fsq_agent.environments.providers._macos._macos_bundle_is_installed", lambda _bundle: True)
    service = PlatformRuntimeService()
    assert service.check_target_availability(macos_probe_environment)[0] is True
    macos_probe_environment.harness.macos.bundle_id = "com.other.installed.app"
    checks = service.check_prerequisites(macos_probe_environment)
    assert checks[-1].status == "unavailable"


@pytest.mark.parametrize("payload", ['{"mac2": {"version": "1.0"}}', "{}"])
def test_driver_discovery_only_invokes_bounded_read_only_command(monkeypatch, payload: str) -> None:
    import fsq_agent.environments.providers._macos as service_module

    calls = []

    def run(arguments, **kwargs):
        calls.append((arguments, kwargs))
        return SimpleNamespace(returncode=0, stdout=payload)

    monkeypatch.setattr(service_module.subprocess, "run", run)
    assert service_module._appium_mac2_driver_installed("/test/appium") == (payload != "{}")
    assert calls == [
        (
            ["/test/appium", "driver", "list", "--installed", "--json"],
            {
                "capture_output": True,
                "text": True,
                "timeout": 10.0,
                "check": False,
            },
        )
    ]


def test_status_probe_never_follows_redirects() -> None:
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    from fsq_agent.environments.providers._macos import _endpoint_available

    paths = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            paths.append(self.path)
            if self.path == "/status":
                self.send_response(302)
                self.send_header("Location", "/other/status")
                self.end_headers()
            else:
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'{"value":{"ready":true}}')

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        assert _endpoint_available(f"http://127.0.0.1:{server.server_port}") is False
        assert paths == ["/status"]
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)


@pytest.mark.parametrize("host", ["Linux", "Windows"])
def test_macos_checks_on_other_hosts_do_not_probe(macos_probe_environment, monkeypatch, host: str) -> None:
    monkeypatch.setattr("fsq_agent.environments._service.platform.system", lambda: host)

    def fail():
        raise AssertionError("must not probe macOS on other hosts")

    monkeypatch.setattr("fsq_agent.environments.providers._macos._active_developer_directory", fail)
    service = PlatformRuntimeService()
    assert all(item.status == "not_applicable" for item in service.check_prerequisites(macos_probe_environment))
    assert service.check_target_availability(macos_probe_environment)[0] is False


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
