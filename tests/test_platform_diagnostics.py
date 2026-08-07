# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from __future__ import annotations

import subprocess
from pathlib import Path
import time

import pytest

from fsq_agent.core import PlatformProbeFactory
from fsq_agent.models import HarnessSettings


def test_android_probe_reports_missing_adb_with_install_fix() -> None:
    settings = HarnessSettings(platform="android")
    probe = PlatformProbeFactory(
        dependencies={"android": {"which": lambda _name: None}}
    ).create("android", settings)

    checks = probe.probe()

    assert checks[0].id == "android.adb.installed"
    assert checks[0].status == "fail"
    assert checks[0].fixes[0].verification_command == "adb version"
    assert checks[1].status == "skip"


def test_android_probe_distinguishes_unauthorized_configured_device() -> None:
    def runner(command, **_kwargs):
        if command[-1] == "version":
            return subprocess.CompletedProcess(command, 0, "Android Debug Bridge version 1.0.41\n", "")
        return subprocess.CompletedProcess(command, 0, "List of devices attached\nserial-1\tunauthorized\n", "")

    harness = HarnessSettings(platform="android")
    harness.android.serial = "serial-1"
    probe = PlatformProbeFactory(
        dependencies={"android": {"which": lambda _name: "adb", "runner": runner}}
    ).create("android", harness)

    checks = probe.probe()

    assert checks[1].id == "android.adb.devices"
    assert checks[1].status == "fail"
    assert "unauthorized" in checks[1].summary
    assert "serial-1" not in checks[1].model_dump_json()


def test_android_probe_retries_device_discovery_during_daemon_startup() -> None:
    device_attempts = 0

    def runner(command, **_kwargs):
        nonlocal device_attempts
        if command[-1] == "version":
            return subprocess.CompletedProcess(command, 0, "Android Debug Bridge version 1.0.41\n", "")
        if command[-1] == "devices":
            device_attempts += 1
            if device_attempts == 1:
                return subprocess.CompletedProcess(command, 1, "", "daemon starting")
            return subprocess.CompletedProcess(command, 0, "List of devices attached\nserial-1\tdevice\n", "")
        return subprocess.CompletedProcess(command, 0, "package:/data/app/base.apk\n", "")

    settings = HarnessSettings(platform="android")
    settings.android.app_id = "com.example.app"
    probe = PlatformProbeFactory(
        dependencies={
            "android": {
                "which": lambda _name: "adb",
                "runner": runner,
                "module_finder": lambda _name: object(),
                "device_probe": lambda _serial, _timeout: {},
            }
        }
    ).create("android", settings)

    checks = probe.probe()

    assert device_attempts == 2
    assert next(check for check in checks if check.id == "android.adb.devices").status == "pass"


def test_android_probe_empty_device_list_is_warning_not_blocker() -> None:
    def runner(command, **_kwargs):
        if command[-1] == "version":
            return subprocess.CompletedProcess(command, 0, "Android Debug Bridge version 1.0.41\n", "")
        return subprocess.CompletedProcess(command, 0, "List of devices attached\n", "")

    settings = HarnessSettings(platform="android")
    probe = PlatformProbeFactory(
        dependencies={"android": {"which": lambda _name: "adb", "runner": runner}}
    ).create("android", settings)

    checks = probe.probe()

    device_check = next(check for check in checks if check.id == "android.adb.devices")
    assert device_check.status == "warn"
    assert next(check for check in checks if check.id == "android.uiautomator2").status == "skip"
    assert next(check for check in checks if check.id == "android.package.installed").status == "skip"


def test_android_probe_empty_device_list_with_configured_serial_remains_failure() -> None:
    def runner(command, **_kwargs):
        if command[-1] == "version":
            return subprocess.CompletedProcess(command, 0, "Android Debug Bridge version 1.0.41\n", "")
        return subprocess.CompletedProcess(command, 0, "List of devices attached\n", "")

    settings = HarnessSettings(platform="android")
    settings.android.serial = "configured-device"
    probe = PlatformProbeFactory(
        dependencies={"android": {"which": lambda _name: "adb", "runner": runner}}
    ).create("android", settings)

    checks = probe.probe()

    assert next(check for check in checks if check.id == "android.adb.devices").status == "fail"


def test_android_probe_timeout_retries_once_then_succeeds() -> None:
    attempts = 0

    def runner(command, **_kwargs):
        nonlocal attempts
        if command[-1] == "version":
            return subprocess.CompletedProcess(command, 0, "version\n", "")
        if command[-1] == "devices":
            attempts += 1
            if attempts == 1:
                raise subprocess.TimeoutExpired(command, 0.01)
            return subprocess.CompletedProcess(command, 0, "List of devices attached\nserial-1\tdevice\n", "")
        return subprocess.CompletedProcess(command, 0, "package:/data/app/base.apk\n", "")

    settings = HarnessSettings(platform="android")
    settings.android.app_id = "com.example.app"
    probe = PlatformProbeFactory(dependencies={"android": {
        "which": lambda _name: "adb", "runner": runner,
        "module_finder": lambda _name: object(), "device_probe": lambda *_args: {},
    }}).create("android", settings)

    checks = probe.probe()

    assert attempts == 2
    assert next(check for check in checks if check.id == "android.adb.devices").status == "pass"


def test_android_probe_second_discovery_failure_is_blocking_and_never_controls_server() -> None:
    commands: list[list[str]] = []

    def runner(command, **_kwargs):
        commands.append(command)
        if command[-1] == "version":
            return subprocess.CompletedProcess(command, 0, "version\n", "")
        return subprocess.CompletedProcess(command, 1, "", "failure")

    settings = HarnessSettings(platform="android")
    probe = PlatformProbeFactory(
        dependencies={"android": {"which": lambda _name: "adb", "runner": runner}}
    ).create("android", settings)

    checks = probe.probe()

    device = next(check for check in checks if check.id == "android.adb.devices")
    assert device.status == "fail"
    assert sum(command[-1] == "devices" for command in commands) == 2
    assert all("kill-server" not in command and "start-server" not in command for command in commands)


def test_android_probe_successful_first_discovery_does_not_retry() -> None:
    attempts = 0

    def runner(command, **_kwargs):
        nonlocal attempts
        if command[-1] == "version":
            return subprocess.CompletedProcess(command, 0, "version\n", "")
        if command[-1] == "devices":
            attempts += 1
            return subprocess.CompletedProcess(command, 0, "List of devices attached\n", "")
        raise AssertionError(command)

    probe = PlatformProbeFactory(
        dependencies={"android": {"which": lambda _name: "adb", "runner": runner}}
    ).create("android", HarnessSettings(platform="android"))

    probe.probe()

    assert attempts == 1


@pytest.mark.parametrize(
    "device_output",
    [
        "List of devices attached\nserial-1\toffline\n",
        "List of devices attached\nfirst\tdevice\nsecond\tdevice\n",
    ],
)
def test_android_probe_unusable_or_ambiguous_devices_fail(device_output: str) -> None:
    def runner(command, **_kwargs):
        if command[-1] == "version":
            return subprocess.CompletedProcess(command, 0, "version\n", "")
        return subprocess.CompletedProcess(command, 0, device_output, "")

    probe = PlatformProbeFactory(
        dependencies={"android": {"which": lambda _name: "adb", "runner": runner}}
    ).create("android", HarnessSettings(platform="android"))

    checks = probe.probe()

    assert next(check for check in checks if check.id == "android.adb.devices").status == "fail"


def test_android_probe_progress_order_for_empty_list() -> None:
    from fsq_agent.models import DoctorProgressEvent

    events: list[DoctorProgressEvent] = []

    def runner(command, **_kwargs):
        if command[-1] == "version":
            return subprocess.CompletedProcess(command, 0, "version\n", "")
        return subprocess.CompletedProcess(command, 0, "List of devices attached\n", "")

    probe = PlatformProbeFactory(
        dependencies={"android": {"which": lambda _name: "adb", "runner": runner}}
    ).create("android", HarnessSettings(platform="android"), progress_sink=events.append)

    checks = probe.probe()

    device_events = [event.event_type for event in events if event.check_id == "android.adb.devices"]
    assert device_events == ["check_started", "check_completed"]
    assert next(check for check in checks if check.id == "android.adb.devices").status == "warn"


def test_android_probe_uses_injected_device_connector() -> None:
    def runner(command, **_kwargs):
        if command[-1] == "version":
            return subprocess.CompletedProcess(command, 0, "Android Debug Bridge version 1.0.41\n", "")
        if command[-1] == "devices":
            return subprocess.CompletedProcess(command, 0, "List of devices attached\nserial-1\tdevice\n", "")
        return subprocess.CompletedProcess(command, 0, "package:/data/app/base.apk\n", "")

    class Device:
        info = {"displayWidth": 100, "displayHeight": 200}

    settings = HarnessSettings(platform="android")
    settings.android.app_id = "com.example.app"
    probe = PlatformProbeFactory(
        dependencies={
            "android": {
                "which": lambda _name: "adb",
                "runner": runner,
                "module_finder": lambda _name: object(),
                "device_connector": lambda serial: Device(),
            }
        }
    ).create("android", settings)

    checks = probe.probe()

    assert next(check for check in checks if check.id == "android.uiautomator2").status == "pass"


def test_android_probe_bounds_device_connection() -> None:
    def runner(command, **_kwargs):
        if command[-1] == "version":
            return subprocess.CompletedProcess(command, 0, "Android Debug Bridge version 1.0.41\n", "")
        if command[-1] == "devices":
            return subprocess.CompletedProcess(command, 0, "List of devices attached\nserial-1\tdevice\n", "")
        return subprocess.CompletedProcess(command, 0, "package:/data/app/base.apk\n", "")

    def bounded_probe(_serial, timeout):
        time.sleep(timeout)
        raise TimeoutError("bounded")

    settings = HarnessSettings(platform="android")
    settings.android.app_id = "com.example.app"
    probe = PlatformProbeFactory(
        dependencies={
            "android": {
                "which": lambda _name: "adb",
                "runner": runner,
                "module_finder": lambda _name: object(),
                "device_probe": bounded_probe,
            }
        }
    ).create("android", settings)

    started = time.monotonic()
    checks = probe.probe(timeout_seconds=0.01)

    assert time.monotonic() - started < 0.15
    assert next(check for check in checks if check.id == "android.uiautomator2").status == "fail"


def test_windows_probe_continues_static_checks_on_wrong_host(tmp_path: Path) -> None:
    app = tmp_path / "app.exe"
    app.write_text("", encoding="utf-8")
    settings = HarnessSettings(platform="windows")
    settings.windows.app_path = app
    probe = PlatformProbeFactory(
        dependencies={
            "windows": {
                "host_system": lambda: "Linux",
                "module_finder": lambda _name: object(),
            }
        }
    ).create("windows", settings)

    checks = probe.probe()
    ids = {check.id for check in checks}

    assert next(check for check in checks if check.id == "windows.host").status == "fail"
    assert {"windows.dependencies", "windows.app_path", "windows.launch_args"} <= ids


def test_macos_probe_verifies_reported_mac2_driver(tmp_path: Path) -> None:
    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"value": {"ready": True, "drivers": {"mac2": {"version": "1"}}}}

    settings = HarnessSettings(platform="macos")
    settings.macos.appium_server_url = "http://127.0.0.1:4723"
    settings.macos.bundle_id = "com.example.App"
    probe = PlatformProbeFactory(
        dependencies={
            "macos": {
                "host_system": lambda: "Darwin",
                "module_finder": lambda _name: object(),
                "http_get": lambda *_args, **_kwargs: Response(),
            }
        }
    ).create("macos", settings)

    checks = probe.probe()

    assert next(check for check in checks if check.id == "macos.appium.mac2").status == "pass"


def test_web_probe_always_terminates_running_process(tmp_path: Path) -> None:
    chrome = tmp_path / "chrome.exe"
    chrome.write_text("", encoding="utf-8")
    settings = HarnessSettings(platform="web")
    settings.web.browser_executable_path = chrome

    class Process:
        pid = 123
        killed = False

        def wait(self, timeout):
            if self.killed:
                return 0
            raise subprocess.TimeoutExpired("chrome", timeout)

        def poll(self):
            return None if not self.killed else 0

        def kill(self):
            self.killed = True

    process = Process()
    probe = PlatformProbeFactory(
        dependencies={
            "web": {
                "module_finder": lambda _name: object(),
                "popen": lambda *_args, **_kwargs: process,
            }
        }
    ).create("web", settings)

    checks = probe.probe()

    assert process.killed is True
    assert next(check for check in checks if check.id == "web.browser.startup").status == "pass"


def test_web_probe_keeps_startup_skip_when_executable_is_missing() -> None:
    settings = HarnessSettings(platform="web")
    probe = PlatformProbeFactory(
        dependencies={"web": {"module_finder": lambda _name: object()}}
    ).create("web", settings)

    checks = probe.probe()

    startup = next(check for check in checks if check.id == "web.browser.startup")
    assert startup.status == "skip"
    assert startup.prerequisite_ids == ["web.browser.executable"]


def test_macos_probe_rejects_false_positive_mac2_error_text() -> None:
    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"value": {"ready": True, "drivers": {"error": "Mac2 is unavailable"}}}

    settings = HarnessSettings(platform="macos")
    settings.macos.appium_server_url = "http://127.0.0.1:4723"
    settings.macos.bundle_id = "com.example.App"
    probe = PlatformProbeFactory(
        dependencies={
            "macos": {
                "host_system": lambda: "Darwin",
                "module_finder": lambda _name: object(),
                "http_get": lambda *_args, **_kwargs: Response(),
                "which": lambda _name: None,
            }
        }
    ).create("macos", settings)

    checks = probe.probe()

    assert next(check for check in checks if check.id == "macos.appium.mac2").status == "fail"
