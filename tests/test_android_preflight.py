# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import socket
import struct
from types import SimpleNamespace

import pytest

from fsq_agent.environments import AndroidDeviceDiscovery, PlatformRuntimeService
from fsq_agent.environments.providers import _android
from fsq_agent.models import AndroidDevice, AndroidDeviceDiscoveryResult


@pytest.fixture(autouse=True)
def no_external_process(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Diagnosis must never start a process")

    monkeypatch.setattr("subprocess.run", forbidden)
    for name in ("ADB_SERVER_SOCKET", "ANDROID_ADB_SERVER_HOST", "ANDROID_ADB_SERVER_PORT", "ADB_SERVER_PORT"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(_android.shutil, "which", lambda _: "/adb")


class Wire:
    def __init__(self, reply):
        self.reply = reply
        self.sent = []
        self.closed = False

    def recv(self, length):
        part, self.reply = self.reply[:length], self.reply[length:]
        return part

    def sendall(self, data):
        self.sent.append(data)

    def settimeout(self, timeout):
        assert timeout > 0

    def close(self):
        self.closed = True


def framed(text):
    data = text.encode()
    return b"OKAY" + f"{len(data):04x}".encode() + data


def test_discovery_existing_server_only(monkeypatch):
    wire = Wire(framed("one device model:Pixel\ntwo unauthorized\n"))
    monkeypatch.setattr(socket, "create_connection", lambda *args, **kwargs: wire)
    result = AndroidDeviceDiscovery().discover()
    assert [(d.serial, d.state) for d in result.devices] == [("one", "device"), ("two", "unauthorized")]
    assert result.devices[0].metadata == {"model": "Pixel"}
    assert wire.sent == [b"000ehost:devices-l"]
    assert wire.closed


@pytest.mark.parametrize("error,code", [(ConnectionRefusedError(), "adb_server_unavailable"), (TimeoutError(), "adb_timeout")])
def test_absent_or_timeout_server_never_autostarts(monkeypatch, error, code):
    def connect(*args, **kwargs):
        raise error

    monkeypatch.setattr(socket, "create_connection", connect)
    assert AndroidDeviceDiscovery().discover().error_code == code


def test_malformed_inventory_is_not_empty_success(monkeypatch):
    wire = Wire(framed("garbage"))
    monkeypatch.setattr(socket, "create_connection", lambda *args, **kwargs: wire)
    assert AndroidDeviceDiscovery().discover().error_code == "adb_failed"
    assert wire.closed


def test_invalid_endpoint_fails_before_connect(monkeypatch):
    monkeypatch.setenv("ADB_SERVER_SOCKET", "tcp:remote:5037")
    monkeypatch.setattr(socket, "create_connection", lambda *args, **kwargs: pytest.fail("wrong endpoint"))
    assert AndroidDeviceDiscovery().discover().error_code == "adb_endpoint_invalid"


def test_package_v2_exit_and_exact_match(monkeypatch):
    output = b"package:/data/app/other/base.apk=com.example.app.other\n"
    wire = Wire(b"OKAYOKAY" + b"\x01" + struct.pack("<I", len(output)) + output + b"\x03\x01\x00\x00\x00\x00")
    monkeypatch.setattr(socket, "create_connection", lambda *args, **kwargs: wire)
    assert _android.package_status("one", "com.example.app") == "absent"
    assert wire.closed


def test_package_query_error_is_not_absent(monkeypatch):
    wire = Wire(b"OKAYOKAY" + b"\x03\x01\x00\x00\x00\x01")
    monkeypatch.setattr(socket, "create_connection", lambda *args, **kwargs: wire)
    assert _android.package_status("one", "com.example.app") == "query_failed"


def settings(serial=None):
    return SimpleNamespace(harness=SimpleNamespace(platform="android", android=SimpleNamespace(serial=serial, app_id="com.example.app")))


def test_selected_device_overrides_multi_device_ambiguity(monkeypatch):
    monkeypatch.setattr(
        _android, "discover_android_devices", lambda **kwargs: AndroidDeviceDiscoveryResult(devices=[AndroidDevice(serial="one", state="device"), AndroidDevice(serial="two", state="device")])
    )
    monkeypatch.setattr(_android.ANDROID_RUNTIME_PROVIDER.__class__, "check", lambda _: SimpleNamespace(ready=True))
    seen = []
    monkeypatch.setattr(_android, "package_status", lambda serial, app_id, **kwargs: seen.append(serial) or "installed")
    facts = PlatformRuntimeService().check_prerequisites(settings("two"))
    assert all(f.status == "ready" for f in facts)
    assert seen == ["two"]
    assert PlatformRuntimeService().check_target_availability(settings("two"), facts)[0]
    assert seen == ["two"]


def test_unselected_multi_device_blocks_package_query(monkeypatch):
    monkeypatch.setattr(
        _android, "discover_android_devices", lambda **kwargs: AndroidDeviceDiscoveryResult(devices=[AndroidDevice(serial="one", state="device"), AndroidDevice(serial="two", state="device")])
    )
    monkeypatch.setattr(_android, "package_status", lambda *args, **kwargs: pytest.fail("ambiguous device"))
    facts = PlatformRuntimeService().check_prerequisites(settings())
    assert next(f for f in facts if f.identifier == "device_selection").code == "android.selection_required"


@pytest.mark.parametrize("state,code", [("unauthorized", "android.device_unauthorized"), ("offline", "android.device_offline"), ("recovery", "android.device_unavailable")])
def test_exact_unusable_device_is_not_replaced(monkeypatch, state, code):
    monkeypatch.setattr(
        _android, "discover_android_devices", lambda **kwargs: AndroidDeviceDiscoveryResult(devices=[AndroidDevice(serial="one", state=state), AndroidDevice(serial="two", state="device")])
    )
    monkeypatch.setattr(_android, "package_status", lambda *args, **kwargs: pytest.fail("Must not inspect another device"))
    facts = PlatformRuntimeService().check_prerequisites(settings("one"))
    assert next(f for f in facts if f.identifier == "device_connection").code == code
    assert next(f for f in facts if f.identifier == "device_selection").target_id == "one"
    assert not PlatformRuntimeService().check_target_availability(settings("one"), facts)[0]


@pytest.mark.parametrize("status,code", [("timeout", "android.application_timeout"), ("query_failed", "android.application_query_failed"), ("absent", "android.application_missing")])
def test_app_failures_remain_distinct(monkeypatch, status, code):
    monkeypatch.setattr(_android, "discover_android_devices", lambda **kwargs: AndroidDeviceDiscoveryResult(devices=[AndroidDevice(serial="one", state="device")]))
    monkeypatch.setattr(_android, "package_status", lambda *args, **kwargs: status)
    result = PlatformRuntimeService().check_prerequisites(settings("one"))[-1]
    assert result.code == code
    assert result.status == ("unavailable" if status == "absent" else "error")


def test_server_disappears_before_package_query_no_restart(monkeypatch):
    first = Wire(framed("one device\n"))
    calls = []

    def connect(*args, **kwargs):
        calls.append(args)
        if len(calls) == 1:
            return first
        raise ConnectionRefusedError

    monkeypatch.setattr(socket, "create_connection", connect)
    facts = PlatformRuntimeService().check_prerequisites(settings("one"))
    assert facts[-1].code == "android.application_query_failed"
    assert first.closed
    assert len(calls) == 2


@pytest.mark.parametrize("reply", [b"FAIL", b"OKAYzzzz", b"OKAY0004x", framed("one device\none offline\n")])
def test_bad_protocol_does_not_succeed(monkeypatch, reply):
    wire = Wire(reply)
    monkeypatch.setattr(socket, "create_connection", lambda *args, **kwargs: wire)
    result = AndroidDeviceDiscovery().discover()
    assert result.error_code == "adb_failed"
    assert wire.closed


def test_package_query_exact_device_and_app(monkeypatch):
    output = b"package:/data/app/~~random==/app/base.apk=com.example.app\n"
    wire = Wire(b"OKAYOKAY" + b"\x01" + struct.pack("<I", len(output)) + output + b"\x03\x01\x00\x00\x00\x00")
    monkeypatch.setattr(socket, "create_connection", lambda *args, **kwargs: wire)
    assert _android.package_status("two", "com.example.app") == "installed"
    assert wire.sent[0].endswith(b"host:transport:two")
    assert wire.sent[1].endswith(b"cmd package list packages -f com.example.app")


@pytest.mark.parametrize("app_id", ["com;echo", "com app", "com$app", "com`app", "com|app", "com&app", "com/app", "com\tapp", "com\napp", "com..app", "-com.app", "com.app; reboot"])
def test_package_grammar_rejects_separators_before_transport(monkeypatch, app_id):
    monkeypatch.setattr(socket, "create_connection", lambda *args, **kwargs: pytest.fail("Invalid package must never reach transport"))
    assert not _android.valid_package(app_id)
    assert _android.package_status("one", app_id) == "query_failed"


@pytest.mark.parametrize(
    "output,expected",
    [
        (b"package:com.example.app\n", "query_failed"),
        (b"package:=com.example.app\n", "query_failed"),
        (b"package:relative.apk=com.example.app\n", "query_failed"),
        (b"package:/data/app/base.apk=com.example.app\n", "installed"),
        (b"", "absent"),
    ],
)
def test_package_requires_valid_path_evidence(monkeypatch, output, expected):
    wire = Wire(b"OKAYOKAY" + b"\x01" + struct.pack("<I", len(output)) + output + b"\x03\x01\x00\x00\x00\x00")
    monkeypatch.setattr(socket, "create_connection", lambda *args, **kwargs: wire)
    assert _android.package_status("one", "com.example.app") == expected
    assert wire.closed


@pytest.mark.parametrize(
    "name,value",
    [
        ("ANDROID_ADB_SERVER_HOST", ""),
        ("ANDROID_ADB_SERVER_PORT", ""),
        ("ANDROID_ADB_SERVER_HOST", "::1"),
        ("ANDROID_ADB_SERVER_HOST", "192.0.2.1"),
        ("ANDROID_ADB_SERVER_PORT", "0"),
        ("ANDROID_ADB_SERVER_PORT", "65536"),
        ("ANDROID_ADB_SERVER_PORT", " 5037"),
    ],
)
def test_invalid_endpoint_never_connects(monkeypatch, name, value):
    monkeypatch.setenv(name, value)
    monkeypatch.setattr(socket, "create_connection", lambda *args, **kwargs: pytest.fail("Invalid endpoint must not connect"))
    assert AndroidDeviceDiscovery().discover().error_code == "adb_endpoint_invalid"


def test_nondefault_ipv4_endpoint_matches_backend_configuration(monkeypatch):
    from adbutils import AdbClient

    from fsq_agent.environments.providers._adb_transport import endpoint

    monkeypatch.setenv("ANDROID_ADB_SERVER_HOST", "127.0.0.2")
    monkeypatch.setenv("ANDROID_ADB_SERVER_PORT", "5038")
    backend = AdbClient()
    assert endpoint() == (backend.host, backend.port)
    wire = Wire(framed(""))
    calls = []
    monkeypatch.setattr(socket, "create_connection", lambda address, **kwargs: calls.append(address) or wire)
    assert AndroidDeviceDiscovery().discover().error_code is None
    assert calls == [("127.0.0.2", 5038)]


def test_fragmented_inventory_and_shared_deadline(monkeypatch):
    from fsq_agent.environments.providers import _adb_transport

    class Fragmented(Wire):
        def recv(self, length):
            return super().recv(min(length, 1))

    wire = Fragmented(framed("one device\n"))
    monkeypatch.setattr(socket, "create_connection", lambda *args, **kwargs: wire)
    assert AndroidDeviceDiscovery().discover().devices[0].serial == "one"
    wire = Fragmented(framed("one device\n"))
    clock = iter(range(100))
    monkeypatch.setattr(_adb_transport.time, "monotonic", lambda: next(clock))
    assert AndroidDeviceDiscovery().discover(timeout_seconds=5).error_code == "adb_timeout"
    assert wire.closed


def test_aggregate_shell_output_is_bounded(monkeypatch):
    from fsq_agent.environments.providers._adb_transport import MAX_BYTES

    data = b"x" * (MAX_BYTES // 2)
    frame = b"\x01" + struct.pack("<I", len(data)) + data
    wire = Wire(b"OKAYOKAY" + frame + frame)
    monkeypatch.setattr(socket, "create_connection", lambda *args, **kwargs: wire)
    assert _android.package_status("one", "com.example.app") == "query_failed"
    assert wire.closed
    assert wire.reply  # second oversized body is not consumed


def test_missing_runtime_does_not_erase_independent_facts(monkeypatch):
    monkeypatch.setattr(_android.ANDROID_RUNTIME_PROVIDER.__class__, "check", lambda _: SimpleNamespace(ready=False))
    monkeypatch.setattr(_android, "discover_android_devices", lambda **kwargs: AndroidDeviceDiscoveryResult(devices=[AndroidDevice(serial="one", state="device")]))
    monkeypatch.setattr(_android, "package_status", lambda *args, **kwargs: "installed")
    facts = PlatformRuntimeService().check_prerequisites(settings())
    assert facts[1].code == "android.runtime_missing"
    assert facts[-1].status == "ready"
    assert not PlatformRuntimeService().check_target_availability(settings(), facts)[0]


@pytest.mark.parametrize("devices,expected", [([], None), ([AndroidDevice(serial="one", state="device"), AndroidDevice(serial="two", state="offline")], "one")])
def test_no_device_or_sole_online_selection(monkeypatch, devices, expected):
    monkeypatch.setattr(_android, "discover_android_devices", lambda **kwargs: AndroidDeviceDiscoveryResult(devices=devices))
    seen = []
    monkeypatch.setattr(_android, "package_status", lambda serial, *args, **kwargs: seen.append(serial) or "installed")
    facts = PlatformRuntimeService().check_prerequisites(settings())
    assert facts[4].target_id == expected
    assert seen == ([expected] if expected else [])
    if not devices:
        assert facts[3].code == "android.no_device"


def test_malicious_package_rejected_before_transport(monkeypatch):
    monkeypatch.setattr(socket, "create_connection", lambda *args, **kwargs: pytest.fail("Must not connect"))
    assert _android.package_status("one", "com.app; reboot") == "query_failed"


def diagnosis(ready=True, provider=True, target="two"):
    from fsq_agent.application import DoctorPlatformResult

    ok = {"status": "ready", "message": "Ready"}
    no = {"status": "unavailable", "message": "Application missing", "action": "Install manually"}
    checks = dict.fromkeys(("configuration", "runtime", "target_configuration", "target_availability", "strict_core", "provider", "suggestion_analyzer", "dynamic_agent"), ok)
    checks["target_availability"] = ok if ready else no
    checks["provider"] = ok if provider else no
    return DoctorPlatformResult(
        platform="android",
        target_id=target,
        status="ready" if ready else "unavailable",
        checks=checks,
        commands={"case_test": ok if ready else no, "case_test_suggest": ok if ready and provider else no, "case_create": ok if ready and provider else no},
    )


def test_start_preflight_provider_free_strict_and_failure(monkeypatch):
    from fsq_agent.adapters.control_plane._readiness import AndroidPreflightError, require_android_preflight

    monkeypatch.setattr("fsq_agent.adapters.control_plane._readiness.diagnose_platform_settings", lambda _: diagnosis(provider=False))
    require_android_preflight(settings("two"), "strict")
    with pytest.raises(AndroidPreflightError):
        require_android_preflight(settings("two"), "strict", requires_provider=True)
    with pytest.raises(AndroidPreflightError):
        require_android_preflight(settings("two"), "explore")


def test_registered_diagnosis_selection_is_private_copy(monkeypatch, tmp_path):
    from fsq_agent.application import RegisteredPlatformDoctorRequest, diagnose_registered_platform
    from fsq_agent.config import Settings

    original = Settings()
    original.harness.platform = "android"
    original.harness.android.serial = None
    platform = SimpleNamespace(platform="android", status="available", message="", action="")
    monkeypatch.setattr("fsq_agent.application.doctor.inspect_registered_workspace", lambda *args, **kwargs: SimpleNamespace(name="test", root_path=tmp_path, platforms=[platform]))
    monkeypatch.setattr("fsq_agent.application.doctor.load_workspace_platform_settings", lambda *args: original)
    seen = []
    monkeypatch.setattr("fsq_agent.application.doctor.diagnose_platform_settings", lambda value: seen.append(value.harness.android.serial) or diagnosis(target=value.harness.android.serial))
    result = diagnose_registered_platform(RegisteredPlatformDoctorRequest(workspace_name="test", platform="android", target_id="two"))
    assert seen == ["two"]
    assert result.platforms[0].target_id == "two"
    assert original.harness.android.serial is None


def test_selected_post_diagnosis_contract_and_origin(monkeypatch):
    from fsq_agent.control_plane import ControlPlaneServer

    server = ControlPlaneServer()
    seen = []
    monkeypatch.setattr("fsq_agent.adapters.control_plane._server.readiness", lambda name, platform, user, **kwargs: seen.append((name, kwargs)) or {"targetId": kwargs["target_id"]})
    body = {"workspaceName": "test", "platform": "android", "targetId": "two"}
    status, payload = server.handle_post("/api/control-plane/readiness", body)
    assert status == 200
    assert payload["targetId"] == "two"
    assert seen == [("test", {"target_id": "two"})]
    status, _ = server.handle_post("/api/control-plane/readiness", body, origin="http://evil.example", host="127.0.0.1:8879")
    assert status == 403
    assert len(seen) == 1


def test_android_start_failure_releases_reservation_without_run(monkeypatch, tmp_path):
    from fsq_agent.config import Settings
    from fsq_agent.control_plane import ControlPlaneServer

    server = ControlPlaneServer()
    current = Settings(harness={"platform": "android"})
    current.workspace.config_path = tmp_path / "config.yaml"
    current.workspace.config_path.write_text("test", encoding="utf-8")
    current.cases.dir = tmp_path / "cases"
    current.harness.android.app_id = "com.example.app"
    seen = []
    monkeypatch.setattr(server, "_load_settings", lambda *args: current)
    monkeypatch.setattr("fsq_agent.adapters.control_plane._readiness.diagnose_platform_settings", lambda value: seen.append(value.harness.android.serial) or diagnosis(ready=False))
    monkeypatch.setattr("fsq_agent.adapters.control_plane._server.start_execution", lambda *args: pytest.fail("No execution before preflight"))
    status, payload = server._start_run({"workspaceName": "test", "platform": "android", "mode": "explore", "targetId": "two", "goal": "Open app"})
    assert status == 400
    assert payload["code"] == "android_preflight_failed"
    assert seen == ["two"]
    assert current.harness.android.serial is None
    assert not server.state.bootstrap()["busy"]
    assert list(tmp_path.iterdir()) == [current.workspace.config_path]


@pytest.mark.parametrize("workspace_app,expected", [("com.workspace.app", "com.workspace.app"), (None, "com.case.app")])
def test_strict_preflight_uses_effective_app_on_selected_device(monkeypatch, tmp_path, workspace_app, expected):
    from fsq_agent.adapters.control_plane._execution import prepare_run
    from fsq_agent.config import Settings

    current = Settings(harness={"platform": "android"})
    current.harness.android.app_id = workspace_app
    current.workspace.config_path = tmp_path / "config.yaml"
    current.workspace.config_path.write_text("platform: android", encoding="utf-8")
    current.cases.dir = tmp_path
    case = tmp_path / "flow.fsq.yaml"
    case.write_text("schemaVersion: fsq.ai-test/v1\nname: flow\nplatform: android\nappId: com.case.app\n---\n- waitMs:\n    duration_ms: 1\n", encoding="utf-8")
    seen = []
    monkeypatch.setattr(
        "fsq_agent.adapters.control_plane._readiness.diagnose_platform_settings", lambda value: seen.append((value.harness.android.serial, value.harness.android.app_id)) or diagnosis(provider=False)
    )
    monkeypatch.setattr("fsq_agent.adapters.control_plane._execution.require_provider", lambda *_args: pytest.fail("Provider-free Strict must not require Provider"))
    prepared = prepare_run(request_id="qa", settings=current, body={"workspaceName": "test", "platform": "android", "targetId": "two", "mode": "strict", "casePath": "flow.fsq.yaml"})
    assert seen == [("two", expected)]
    assert prepared.settings.harness.android.app_id == expected
    assert current.harness.android.app_id == workspace_app
    assert current.harness.android.serial is None


def test_prerequisite_device_binding_rejects_invalid_or_wrong_scope():
    from pydantic import ValidationError

    from fsq_agent.models import PlatformPrerequisiteCheck

    for fields in ({"identifier": "device_selection", "target_id": "one;two"}, {"identifier": "adb_server", "target_id": "one"}):
        with pytest.raises(ValidationError):
            PlatformPrerequisiteCheck(status="ready", message="Ready", **fields)
