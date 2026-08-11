# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from __future__ import annotations

import importlib.util
import platform
from pathlib import Path
import re
from typing import Callable

from fsq_agent.models import DiagnosticProbeResult, DoctorFix, DoctorProgressSink, WindowsHarnessSettings
from fsq_agent.core.diagnostics._progress import emit_completed, emit_started


class WindowsPlatformProbe:
    def __init__(
        self,
        settings: WindowsHarnessSettings,
        *,
        module_finder: Callable[[str], object | None] = importlib.util.find_spec,
        host_system: Callable[[], str] = platform.system,
        progress_sink: DoctorProgressSink | None = None,
    ) -> None:
        self.settings = settings
        self.module_finder = module_finder
        self.host_system = host_system
        self.progress_sink = progress_sink

    def probe(self, timeout_seconds: float = 5.0) -> list[DiagnosticProbeResult]:
        del timeout_seconds
        checks: list[DiagnosticProbeResult] = []
        self._started("windows.host", "Checking the Windows host...")
        if self.host_system() != "Windows":
            self._append(checks, _fail("windows.host", "Windows automation requires a Windows host.", "Run doctor on the Windows target host."))
        else:
            self._append(checks, _pass("windows.host", "The host operating system is Windows."))
        self._started("windows.dependencies", "Checking Windows Python dependencies...")
        missing = [name for name in ("pywinauto", "PIL") if self.module_finder(name) is None]
        if missing:
            self._append(checks, _fail("windows.dependencies", "Windows Python dependencies are missing.", "Install the Windows extra.", command="uv sync --extra dev --extra windows"))
        else:
            self._append(checks, _pass("windows.dependencies", "pywinauto and Pillow are installed."))
        self._started("windows.app_path", "Checking the Windows application path...")
        app = self.settings.app_path
        if app is None or not Path(app).is_file():
            self._append(checks, _fail("windows.app_path", "FSQ_WINDOWS_APP_PATH is missing or invalid.", "Set the target executable path.", env="FSQ_WINDOWS_APP_PATH"))
        else:
            self._append(checks, _pass("windows.app_path", "The configured Windows application path is readable."))
        self._started("windows.backend", "Checking the pywinauto backend...")
        if self.settings.backend_kind not in {"uia", "win32"}:
            self._append(checks, _fail("windows.backend", "The pywinauto backend kind is invalid.", "Set FSQ_WINDOWS_BACKEND_KIND to uia or win32.", env="FSQ_WINDOWS_BACKEND_KIND"))
        else:
            self._append(checks, _pass("windows.backend", f"pywinauto backend {self.settings.backend_kind} is valid."))
        self._started("windows.window_regex", "Checking the window title regular expression...")
        if self.settings.window_title_re:
            try:
                re.compile(self.settings.window_title_re)
            except re.error:
                self._append(checks, _fail("windows.window_regex", "The configured window title regular expression is invalid.", "Correct FSQ_WINDOWS_WINDOW_TITLE_RE.", env="FSQ_WINDOWS_WINDOW_TITLE_RE"))
            else:
                self._append(checks, _pass("windows.window_regex", "The configured window title regular expression compiles."))
        else:
            self._append(checks, _pass("windows.window_regex", "No optional window title regular expression is configured."))
        self._started("windows.launch_args", "Checking Windows launch arguments...")
        self._append(checks,
            _pass(
                "windows.launch_args",
                "Configured Windows launch arguments are parsed and normalized."
                if self.settings.launch_args
                else "No optional Windows launch arguments are configured.",
            )
        )
        self._started("windows.runtime_unverified", "Checking Windows runtime verification scope...")
        self._append(checks,
            DiagnosticProbeResult(
                id="windows.runtime_unverified",
                category="Windows",
                status="warn",
                summary="Application launch and control-tree automation are not tested by doctor.",
                fixes=[DoctorFix(description="Run a small real Windows case to verify the application's accessibility surface.")],
            )
        )
        return checks

    def _started(self, check_id: str, summary: str) -> None:
        emit_started(self.progress_sink, phase="Windows", check_id=check_id, summary=summary)

    def _append(self, checks: list[DiagnosticProbeResult], result: DiagnosticProbeResult) -> None:
        checks.append(result)
        emit_completed(self.progress_sink, result)


def _pass(check_id: str, summary: str) -> DiagnosticProbeResult:
    return DiagnosticProbeResult(id=check_id, category="Windows", status="pass", summary=summary)


def _fail(check_id: str, summary: str, description: str, command: str | None = None, env: str | None = None) -> DiagnosticProbeResult:
    return DiagnosticProbeResult(
        id=check_id,
        category="Windows",
        status="fail",
        summary=summary,
        fixes=[DoctorFix(description=description, command=command, environment_variable=env)],
    )
