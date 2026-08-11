# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from __future__ import annotations

import importlib.util
import platform
from pathlib import Path
import json
import shutil
import subprocess
import tempfile
from urllib.parse import urlsplit, urlunsplit
from typing import Any, Callable

import httpx

from fsq_agent.models import DiagnosticProbeResult, DoctorFix, DoctorProgressSink, MacOSHarnessSettings
from fsq_agent.core.diagnostics._progress import emit_completed, emit_started


class MacOSPlatformProbe:
    def __init__(
        self,
        settings: MacOSHarnessSettings,
        *,
        http_get: Callable[..., Any] | None = None,
        which: Callable[[str], str | None] = shutil.which,
        runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
        module_finder: Callable[[str], object | None] = importlib.util.find_spec,
        host_system: Callable[[], str] = platform.system,
        progress_sink: DoctorProgressSink | None = None,
    ) -> None:
        self.settings = settings
        self.http_get = http_get or httpx.get
        self.which = which
        self.runner = runner
        self.module_finder = module_finder
        self.host_system = host_system
        self.progress_sink = progress_sink

    def probe(self, timeout_seconds: float = 5.0) -> list[DiagnosticProbeResult]:
        checks: list[DiagnosticProbeResult] = []
        self._started("macos.host", "Checking the macOS host...")
        if self.host_system() != "Darwin":
            self._append(checks, _fail("macos.host", "macOS automation requires a macOS host.", "Run doctor on the macOS target host."))
        else:
            self._append(checks, _pass("macos.host", "The host operating system is macOS."))
        self._started("macos.dependencies", "Checking the Appium Python dependency...")
        if self.module_finder("appium") is None:
            self._append(checks, _fail("macos.dependencies", "The Appium Python Client is not installed.", "Install the macOS extra.", command="uv sync --extra dev --extra macos"))
        else:
            self._append(checks, _pass("macos.dependencies", "The Appium Python Client is installed."))
        self._started("macos.appium.status", "Checking Appium server readiness...")
        server_url = (self.settings.appium_server_url or "").strip()
        parsed = urlsplit(server_url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            self._append(checks, _fail("macos.appium.status", "FSQ_MACOS_APPIUM_SERVER_URL is invalid.", "Set a valid Appium server URL.", env="FSQ_MACOS_APPIUM_SERVER_URL"))
            self._append(checks, _skip("macos.appium.mac2", "Mac2 availability was skipped because the Appium URL is invalid."))
        else:
            status_url = server_url.rstrip("/") + "/status"
            try:
                response = self.http_get(status_url, timeout=timeout_seconds)
                response.raise_for_status()
                payload = response.json()
                value = payload.get("value", payload) if isinstance(payload, dict) else {}
                if not isinstance(value, dict) or value.get("ready") is not True:
                    raise RuntimeError("Appium reports not ready")
                drivers = _reported_drivers(value)
            except Exception as exc:  # noqa: BLE001 - service failures become diagnostics
                self._append(checks, _fail("macos.appium.status", f"Appium /status is not ready ({type(exc).__name__}).", "Start Appium and install the Mac2 driver, then rerun doctor.", command="appium driver install mac2"))
                self._append(checks, _skip("macos.appium.mac2", "Mac2 availability was skipped because Appium is not ready."))
            else:
                metadata: dict[str, object] = {"endpoint": _sanitize(status_url)}
                self._append(checks, _pass("macos.appium.status", "Appium /status is reachable and ready.", metadata=metadata))
                self._started("macos.appium.mac2", "Checking the Appium Mac2 driver...")
                mac2_available = _contains_mac2(drivers) if drivers is not None else self._local_mac2_available(timeout_seconds)
                if mac2_available:
                    self._append(checks, _pass("macos.appium.mac2", "The Appium Mac2 driver is installed."))
                else:
                    self._append(checks, _fail("macos.appium.mac2", "The Appium Mac2 driver could not be verified.", "Install the Mac2 driver.", command="appium driver install mac2"))
        self._started("macos.target", "Checking the macOS application target...")
        app_path = self.settings.app_path
        if not self.settings.bundle_id and not app_path:
            self._append(checks, _fail("macos.target", "No macOS bundle id or app path is configured.", "Set FSQ_MACOS_BUNDLE_ID or FSQ_MACOS_APP_PATH.", env="FSQ_MACOS_BUNDLE_ID"))
        elif app_path and not Path(app_path).exists():
            self._append(checks, _fail("macos.target", "The configured macOS application path does not exist.", "Correct FSQ_MACOS_APP_PATH.", env="FSQ_MACOS_APP_PATH"))
        else:
            self._append(checks, _pass("macos.target", "A macOS application target is configured."))
        self._started("macos.runtime_unverified", "Checking macOS runtime verification scope...")
        self._append(checks,
            DiagnosticProbeResult(
                id="macos.runtime_unverified",
                category="macOS",
                status="warn",
                summary="Appium session creation and accessibility automation are not tested by doctor.",
                fixes=[DoctorFix(description="Run a small real macOS case to verify Mac2 session and accessibility behavior.")],
            )
        )
        return checks

    def _started(self, check_id: str, summary: str) -> None:
        emit_started(self.progress_sink, phase="macOS", check_id=check_id, summary=summary)

    def _append(self, checks: list[DiagnosticProbeResult], result: DiagnosticProbeResult) -> None:
        if result.status == "skip":
            self._started(result.id, f"Skipping {result.id}...")
        checks.append(result)
        emit_completed(self.progress_sink, result)

    def _local_mac2_available(self, timeout_seconds: float) -> bool:
        appium = self.which("appium")
        if not appium:
            return False
        try:
            with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
                result = self.runner(
                    [appium, "driver", "list", "--installed", "--json"],
                    stdout=stdout,
                    stderr=stderr,
                    timeout=timeout_seconds,
                    check=False,
                )
                stdout.seek(0)
                output = stdout.read(65536).decode("utf-8", errors="replace")
        except (OSError, subprocess.TimeoutExpired):
            return False
        if result.returncode != 0:
            return False
        try:
            payload = json.loads(output)
        except json.JSONDecodeError:
            return any(token.casefold() == "mac2" for token in output.replace(",", " ").split())
        return _contains_mac2(payload)


def _sanitize(url: str) -> str:
    parsed = urlsplit(url)
    host = parsed.hostname or ""
    if parsed.port:
        host = f"{host}:{parsed.port}"
    return urlunsplit((parsed.scheme, host, parsed.path, "", ""))


def _reported_drivers(value: dict[str, object]) -> object | None:
    if "drivers" in value:
        return value["drivers"]
    build = value.get("build")
    return build.get("drivers") if isinstance(build, dict) else None


def _contains_mac2(value: object) -> bool:
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key).casefold() == "mac2" and not (
                isinstance(item, dict) and item.get("installed") is False
            ):
                return True
            if str(key).casefold() in {"name", "driver", "automationname"} and str(item).casefold() == "mac2":
                return True
            if _contains_mac2(item):
                return True
    elif isinstance(value, list):
        return any(_contains_mac2(item) for item in value)
    return False


def _pass(check_id: str, summary: str, metadata: dict[str, object] | None = None) -> DiagnosticProbeResult:
    return DiagnosticProbeResult(id=check_id, category="macOS", status="pass", summary=summary, metadata=metadata or {})


def _fail(check_id: str, summary: str, description: str, command: str | None = None, env: str | None = None) -> DiagnosticProbeResult:
    return DiagnosticProbeResult(
        id=check_id,
        category="macOS",
        status="fail",
        summary=summary,
        fixes=[DoctorFix(description=description, command=command, environment_variable=env)],
    )


def _skip(check_id: str, summary: str) -> DiagnosticProbeResult:
    return DiagnosticProbeResult(
        id=check_id,
        category="macOS",
        status="skip",
        summary=summary,
        prerequisite_ids=["macos.appium.status"],
    )
