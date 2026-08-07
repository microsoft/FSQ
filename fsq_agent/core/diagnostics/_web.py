# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import signal
import subprocess
import tempfile
from typing import Any, Callable

from fsq_agent.models import DiagnosticProbeResult, DoctorFix, DoctorProgressSink, WebHarnessSettings
from fsq_agent.core.diagnostics._progress import emit_completed, emit_started


class WebPlatformProbe:
    def __init__(
        self,
        settings: WebHarnessSettings,
        *,
        popen: Callable[..., Any] = subprocess.Popen,
        temporary_directory: Callable[..., Any] = tempfile.TemporaryDirectory,
        module_finder: Callable[[str], object | None] = importlib.util.find_spec,
        terminate_process_tree: Callable[[Any], None] | None = None,
        progress_sink: DoctorProgressSink | None = None,
    ) -> None:
        self.settings = settings
        self.popen = popen
        self.temporary_directory = temporary_directory
        self.module_finder = module_finder
        self.terminate_process_tree = terminate_process_tree or _terminate_process_tree
        self.progress_sink = progress_sink

    def probe(self, timeout_seconds: float = 5.0) -> list[DiagnosticProbeResult]:
        checks: list[DiagnosticProbeResult] = []
        self._started("web.playwright", "Checking the Playwright dependency...")
        if self.module_finder("playwright") is None:
            self._append(checks, _fail("web.playwright", "The Playwright Python package is not installed.", "Install the Web extra.", "uv sync --extra dev --extra web"))
        else:
            self._append(checks, _pass("web.playwright", "The Playwright Python package is installed."))
        self._started("web.browser.executable", "Checking the Chrome executable...")
        path = self.settings.browser_executable_path
        if path is None:
            self._append(checks, _fail("web.browser.executable", "FSQ_WEB_BROWSER_EXECUTABLE_PATH is not configured.", "Set the local Chrome executable path.", env="FSQ_WEB_BROWSER_EXECUTABLE_PATH"))
            self._append(checks, _skip("web.browser.startup", "Chrome startup was skipped because no executable is configured.", "web.browser.executable"))
            return checks
        executable = Path(path)
        if not executable.is_file() or executable.name.casefold() not in {"chrome", "chrome.exe", "google chrome", "google-chrome", "google-chrome-stable"}:
            self._append(checks, _fail("web.browser.executable", "The configured Chrome executable path is invalid or channel-mismatched.", "Correct FSQ_WEB_BROWSER_EXECUTABLE_PATH.", env="FSQ_WEB_BROWSER_EXECUTABLE_PATH"))
            self._append(checks, _skip("web.browser.startup", "Chrome startup was skipped because the executable is invalid.", "web.browser.executable"))
            return checks
        self._append(checks, _pass("web.browser.executable", "The configured Chrome executable is valid."))
        self._started("web.browser.startup", "Starting Chrome with an isolated profile...")
        process = None
        temp_context = None
        try:
            temp_context = self.temporary_directory(prefix="fsq-doctor-chrome-")
            profile = temp_context.__enter__()
            process = self.popen(
                [str(executable), "--headless=new", "--no-first-run", "--disable-gpu", f"--user-data-dir={profile}", "about:blank"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            try:
                code = process.wait(timeout=min(timeout_seconds, 1.0))
                if code != 0:
                    raise RuntimeError(f"Chrome exited with code {code}")
            except subprocess.TimeoutExpired:
                pass
        except Exception as exc:  # noqa: BLE001 - process failures become diagnostics
            self._append(checks, _fail("web.browser.startup", f"Chrome isolated startup failed ({type(exc).__name__}).", "Check Chrome permissions and local security software, then rerun doctor."))
        else:
            self._append(checks, _pass("web.browser.startup", "Chrome starts with an isolated temporary profile."))
        finally:
            if process is not None and process.poll() is None:
                self.terminate_process_tree(process)
            if temp_context is not None:
                try:
                    temp_context.__exit__(None, None, None)
                except OSError:
                    pass
        return checks

    def _started(self, check_id: str, summary: str) -> None:
        emit_started(self.progress_sink, phase="Web", check_id=check_id, summary=summary)

    def _append(self, checks: list[DiagnosticProbeResult], result: DiagnosticProbeResult) -> None:
        if result.status == "skip":
            self._started(result.id, f"Skipping {result.id}...")
        checks.append(result)
        emit_completed(self.progress_sink, result)


def _terminate_process_tree(process: Any) -> None:
    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=3,
                check=False,
            )
        else:
            os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=2)
    except Exception:  # noqa: BLE001 - best-effort diagnostic cleanup
        try:
            process.kill()
            process.wait(timeout=2)
        except Exception:
            pass


def _targets() -> list[str]:
    return ["dynamic", "strict", "ai_assertion"]


def _pass(check_id: str, summary: str) -> DiagnosticProbeResult:
    return DiagnosticProbeResult(id=check_id, category="Web", status="pass", summary=summary, affected_targets=_targets())


def _fail(check_id: str, summary: str, description: str, command: str | None = None, env: str | None = None) -> DiagnosticProbeResult:
    return DiagnosticProbeResult(
        id=check_id,
        category="Web",
        status="fail",
        summary=summary,
        affected_targets=_targets(),
        fixes=[DoctorFix(description=description, command=command, environment_variable=env)],
    )


def _skip(check_id: str, summary: str, prerequisite_id: str) -> DiagnosticProbeResult:
    return DiagnosticProbeResult(
        id=check_id,
        category="Web",
        status="skip",
        summary=summary,
        affected_targets=_targets(),
        prerequisite_ids=[prerequisite_id],
    )
