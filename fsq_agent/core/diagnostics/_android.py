# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from __future__ import annotations

import importlib.util
import shutil
import subprocess
import tempfile
import sys
from typing import Callable

from fsq_agent.models import AndroidHarnessSettings, DiagnosticProbeResult, DoctorFix
from fsq_agent.models import DoctorProgressSink
from fsq_agent.core.diagnostics._progress import emit_completed, emit_started


class AndroidPlatformProbe:
    def __init__(
        self,
        settings: AndroidHarnessSettings,
        *,
        which: Callable[[str], str | None] = shutil.which,
        runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
        module_finder: Callable[[str], object | None] = importlib.util.find_spec,
        device_connector: Callable[[str | None], object] | None = None,
        device_probe: Callable[[str | None, float], object] | None = None,
        progress_sink: DoctorProgressSink | None = None,
    ) -> None:
        self.settings = settings
        self.which = which
        self.runner = runner
        self.module_finder = module_finder
        self.device_connector = device_connector
        self.device_probe = device_probe
        self.progress_sink = progress_sink

    def probe(self, timeout_seconds: float = 5.0) -> list[DiagnosticProbeResult]:
        checks: list[DiagnosticProbeResult] = []
        self._started("android.adb.installed", "Checking adb installation...")
        adb = self.which("adb")
        if not adb:
            return self._return([
                _fail(
                    "android.adb.installed",
                    "adb was not found on PATH.",
                    "Install Android SDK Platform-Tools and add its directory to PATH.",
                    verification="adb version",
                ),
                _skip("android.adb.devices", "Device discovery was skipped because adb is unavailable."),
                _skip("android.uiautomator2", "uiautomator2 communication was skipped because adb is unavailable."),
                _skip("android.package.installed", "Package inspection was skipped because adb is unavailable."),
            ])
        version = self._run([adb, "version"], timeout_seconds)
        if version is None or version.returncode != 0:
            return self._return([
                _fail(
                    "android.adb.installed",
                    "adb could not execute successfully.",
                    "Repair Android SDK Platform-Tools or check execution permissions.",
                    verification="adb version",
                ),
                _skip("android.adb.devices", "Device discovery was skipped because adb failed."),
                _skip("android.uiautomator2", "uiautomator2 communication was skipped because adb failed."),
                _skip("android.package.installed", "Package inspection was skipped because adb failed."),
            ])
        self._append(checks,
            DiagnosticProbeResult(
                id="android.adb.installed",
                category="Android",
                status="pass",
                summary="adb is installed and executable.",
                metadata={"version": _first_line(version.stdout)},
            )
        )
        self._started("android.adb.devices", "Discovering Android devices...")
        devices_result = self._run([adb, "devices"], timeout_seconds)
        discovery_retried = devices_result is None or devices_result.returncode != 0
        if discovery_retried:
            devices_result = self._run([adb, "devices"], timeout_seconds)
        if devices_result is None or devices_result.returncode != 0:
            self._append(checks,
                _fail(
                    "android.adb.devices",
                    "adb device discovery failed.",
                    "Check ADB server/port state, then run adb kill-server and adb start-server.",
                    verification="adb devices",
                )
            )
            self._extend(checks, [
                _skip("android.uiautomator2", "uiautomator2 communication was skipped because device discovery failed."),
                _skip("android.package.installed", "Package inspection was skipped because device discovery failed."),
            ])
            return checks
        devices = _parse_devices(devices_result.stdout)
        serial = self.settings.serial
        selected: str | None = None
        if serial:
            state = devices.get(serial)
            if state != "device":
                label = state or "missing"
                self._append(checks,
                    _fail(
                        "android.adb.devices",
                        f"Configured Android device is {label}.",
                        "Correct FSQ_ANDROID_SERIAL, reconnect the device, and accept USB debugging authorization.",
                        env="FSQ_ANDROID_SERIAL",
                        verification="adb devices",
                    )
                )
                self._extend(checks, [
                    _skip("android.uiautomator2", "uiautomator2 communication was skipped because the selected device is unavailable."),
                    _skip("android.package.installed", "Package inspection was skipped because the selected device is unavailable."),
                ])
                return checks
            selected = serial
        else:
            if not devices:
                self._append(
                    checks,
                    DiagnosticProbeResult(
                        id="android.adb.devices",
                        category="Android",
                        status="warn",
                        summary="adb is ready, but no Android device is connected.",
                        fixes=[
                            DoctorFix(
                                description="Connect an Android device or start an emulator before running UI automation.",
                                verification_command="adb devices",
                            )
                        ],
                        metadata={"device_count": 0, "discovery_retried": discovery_retried},
                    ),
                )
                self._extend(
                    checks,
                    [
                        _skip("android.uiautomator2", "uiautomator2 communication was skipped because no device is connected."),
                        _skip("android.package.installed", "Package inspection was skipped because no device is connected."),
                    ],
                )
                return checks
            online = [name for name, state in devices.items() if state == "device"]
            if len(online) != 1:
                states = sorted(set(devices.values()))
                summary = "No online Android device was found." if not online else "Multiple Android devices are online."
                self._append(checks,
                    _fail(
                        "android.adb.devices",
                        summary,
                        "Connect/start one device or set FSQ_ANDROID_SERIAL to the intended online device.",
                        env="FSQ_ANDROID_SERIAL",
                        verification="adb devices",
                        metadata={"device_count": len(devices), "states": states},
                    )
                )
                self._extend(checks, [
                    _skip("android.uiautomator2", "uiautomator2 communication was skipped because device selection is unresolved."),
                    _skip("android.package.installed", "Package inspection was skipped because device selection is unresolved."),
                ])
                return checks
            selected = online[0]
        self._append(checks,
            DiagnosticProbeResult(
                id="android.adb.devices",
                category="Android",
                status="pass",
                summary="An online Android device is selected.",
                metadata={"selection": "configured" if serial else "unique_online"},
            )
        )
        self._started("android.uiautomator2", "Checking uiautomator2 device communication...")
        if self.module_finder("uiautomator2") is None:
            self._append(checks,
                _fail(
                    "android.uiautomator2",
                    "The uiautomator2 Python package is not installed.",
                    "Install the Android extra.",
                    command="uv sync --extra dev --extra android",
                )
            )
        else:
            try:
                if self.device_probe is not None:
                    _ = self.device_probe(selected, timeout_seconds)
                elif self.device_connector is not None:
                    device = self.device_connector(selected)
                    _ = device.info
                else:
                    self._probe_uiautomator2_subprocess(selected, timeout_seconds)
            except Exception as exc:  # noqa: BLE001 - backend failures become diagnostics
                self._append(checks,
                    _fail(
                        "android.uiautomator2",
                        f"uiautomator2 could not communicate with the device ({type(exc).__name__}).",
                        "Check the device connection and uiautomator2 service, then rerun doctor.",
                        verification="fsq-agent doctor --platform android --non-interactive",
                    )
                )
            else:
                self._append(checks,
                    DiagnosticProbeResult(
                        id="android.uiautomator2",
                        category="Android",
                        status="pass",
                        summary="uiautomator2 can read basic device information.",
                    )
                )
        self._started("android.package.installed", "Checking the Android target package...")
        if not self.settings.app_id:
            self._append(checks,
                _fail(
                    "android.package.installed",
                    "FSQ_ANDROID_APP_ID is not configured.",
                    "Set the Android application id.",
                    env="FSQ_ANDROID_APP_ID",
                )
            )
            return checks
        package = self._run([adb, "-s", selected, "shell", "pm", "path", self.settings.app_id], timeout_seconds)
        if package is None or package.returncode != 0 or "package:" not in package.stdout:
            self._append(checks,
                _fail(
                    "android.package.installed",
                    "The configured Android package is not installed on the selected device.",
                    "Install the target APK or correct FSQ_ANDROID_APP_ID.",
                    env="FSQ_ANDROID_APP_ID",
                )
            )
        else:
            self._append(checks,
                DiagnosticProbeResult(
                    id="android.package.installed",
                    category="Android",
                    status="pass",
                    summary="The configured Android package is installed.",
                )
            )
        return checks

    def _started(self, check_id: str, summary: str) -> None:
        emit_started(self.progress_sink, phase="Android", check_id=check_id, summary=summary)

    def _append(
        self,
        checks: list[DiagnosticProbeResult],
        result: DiagnosticProbeResult,
    ) -> None:
        if result.status == "skip":
            self._started(result.id, f"Skipping {result.id}...")
        checks.append(result)
        emit_completed(self.progress_sink, result)

    def _return(self, checks: list[DiagnosticProbeResult]) -> list[DiagnosticProbeResult]:
        for index, result in enumerate(checks):
            if index > 0 or result.status == "skip":
                self._started(result.id, f"Skipping {result.id}..." if result.status == "skip" else f"Checking {result.id}...")
            emit_completed(self.progress_sink, result)
        return checks

    def _extend(
        self,
        checks: list[DiagnosticProbeResult],
        results: list[DiagnosticProbeResult],
    ) -> None:
        for result in results:
            self._append(checks, result)

    def _probe_uiautomator2_subprocess(self, serial: str | None, timeout: float) -> None:
        script = (
            "import sys, uiautomator2 as u2; "
            "device=u2.connect(sys.argv[1] or None); "
            "info=device.info; "
            "assert isinstance(info, dict)"
        )
        try:
            result = subprocess.run(
                [sys.executable, "-c", script, serial or ""],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise TimeoutError("uiautomator2 readiness timed out") from exc
        if result.returncode != 0:
            raise RuntimeError("uiautomator2 readiness subprocess failed")

    def _run(self, command: list[str], timeout: float) -> subprocess.CompletedProcess[str] | None:
        try:
            if self.runner is not None:
                result = self.runner(command, capture_output=True, text=True, timeout=timeout, check=False)
                return subprocess.CompletedProcess(
                    result.args,
                    result.returncode,
                    (result.stdout or "")[:65536],
                    (result.stderr or "")[:65536],
                )
            with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
                result = subprocess.run(command, stdout=stdout, stderr=stderr, timeout=timeout, check=False)
                stdout.seek(0)
                stderr.seek(0)
                return subprocess.CompletedProcess(
                    result.args,
                    result.returncode,
                    stdout.read(65536).decode("utf-8", errors="replace"),
                    stderr.read(65536).decode("utf-8", errors="replace"),
                )
        except (OSError, subprocess.TimeoutExpired):
            return None


def _parse_devices(output: str) -> dict[str, str]:
    devices: dict[str, str] = {}
    for line in output.splitlines()[1:]:
        parts = line.strip().split()
        if len(parts) >= 2:
            devices[parts[0]] = parts[1]
    return devices


def _first_line(value: str) -> str:
    line = next((line.strip() for line in value.splitlines() if line.strip()), "available")
    return line[:160]


def _skip(check_id: str, summary: str) -> DiagnosticProbeResult:
    prerequisite = "android.adb.installed" if check_id == "android.adb.devices" else "android.adb.devices"
    return DiagnosticProbeResult(
        id=check_id,
        category="Android",
        status="skip",
        summary=summary,
        prerequisite_ids=[prerequisite],
    )


def _fail(
    check_id: str,
    summary: str,
    description: str,
    *,
    command: str | None = None,
    verification: str | None = None,
    env: str | None = None,
    metadata: dict[str, object] | None = None,
) -> DiagnosticProbeResult:
    return DiagnosticProbeResult(
        id=check_id,
        category="Android",
        status="fail",
        summary=summary,
        fixes=[DoctorFix(description=description, command=command, verification_command=verification, environment_variable=env)],
        metadata=metadata or {},
    )
