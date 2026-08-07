# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from __future__ import annotations

import os
from typing import TextIO

from fsq_agent.models import DoctorCheckResult, DoctorProgressEvent, DoctorReport


_RESET = "\x1b[0m"
_STYLES = {
    "pass": "\x1b[32m",
    "ready": "\x1b[32m",
    "warn": "\x1b[33m",
    "fail": "\x1b[1;31m",
    "blocked": "\x1b[1;31m",
    "skip": "\x1b[2;37m",
    "not_checked": "\x1b[2;37m",
    "running": "\x1b[36m",
    "applied": "\x1b[32m",
    "declined": "\x1b[33m",
    "skipped": "\x1b[2;37m",
    "failed": "\x1b[1;31m",
}


class DoctorProgressTextRenderer:
    def __init__(
        self,
        stream: TextIO,
        *,
        tty: bool,
        color: str = "auto",
    ) -> None:
        self.stream = stream
        self.tty = tty
        self.color_enabled = _color_enabled(color, tty)
        self._running = False
        self._phase: str | None = None
        self._header_platform_written = False

    def write_header(self, platform: str | None, mode: str) -> None:
        self._write("FSQ Doctor\n\n")
        if platform is not None:
            self._write(f"Platform: {platform} (explicit)\n")
            self._header_platform_written = True
        self._write(f"Mode: {mode}\n")
        if platform is not None:
            self._write("\n")

    def __call__(self, event: DoctorProgressEvent) -> None:
        if event.event_type == "phase_started":
            if event.phase == "Platform":
                if not self._header_platform_written:
                    self._write(event.summary + "\n\n")
                    self._header_platform_written = True
            else:
                self._render_phase(event.phase or event.summary)
        elif event.event_type == "check_started":
            self._render_started(event.summary)
        elif event.event_type == "check_completed" and event.check is not None:
            self._render_check(event.check)
        elif event.event_type == "repair_started":
            self._render_started(event.summary)
        elif event.event_type == "repair_completed" and event.repair is not None:
            self._render_repair(event)
        elif event.event_type == "summary_ready" and event.report is not None:
            self._render_summary(event.report)

    def _render_phase(self, phase: str) -> None:
        if not phase or phase == self._phase:
            return
        self._finish_running_line()
        self._phase = phase
        self._write(f"{phase}\n")

    def _render_started(self, summary: str) -> None:
        self._finish_running_line()
        line = f"  {self._status('RUNNING', 'running', width=8)}  {summary}"
        if self.tty:
            self._write("\r\x1b[2K" + line, flush=True)
            self._running = True
        else:
            self._write(line + "\n", flush=True)

    def _render_check(self, check: DoctorCheckResult) -> None:
        prefix = "\r\x1b[2K" if self.tty and self._running else ""
        self._running = False
        self._write(
            prefix + f"  {self._status(check.status.upper(), check.status, width=8)}  {check.summary}\n",
            flush=True,
        )
        if check.status in {"warn", "fail"}:
            if check.affected_targets:
                self._write(f"            Impact: {', '.join(check.affected_targets)}\n")
            for fix in check.fixes:
                self._write(f"            Fix: {fix.description}\n")
                if fix.command:
                    self._write(f"            Run: {fix.command}\n")
                if fix.verification_command:
                    self._write(f"            Verify: {fix.verification_command}\n")

    def _render_repair(self, event: DoctorProgressEvent) -> None:
        repair = event.repair
        if repair is None:
            return
        prefix = "\r\x1b[2K" if self.tty and self._running else ""
        self._running = False
        self._write(
            prefix
            + f"  {self._status(repair.status.upper(), repair.status, width=8)}  "
            + f"{repair.action_id}: {repair.target}\n",
            flush=True,
        )
        if repair.backup_path:
            self._write(f"            Backup: {repair.backup_path}\n")

    def _render_summary(self, report: DoctorReport) -> None:
        self._finish_running_line()
        self._write("\nReadiness\n")
        self._readiness("Dynamic LLM", report.readiness.dynamic_llm.status)
        self._readiness("Strict core", report.readiness.strict_core.status)
        self._readiness("AI assertion", report.readiness.ai_assertion.status)
        self._write("\n")
        self._write(
            "Summary: "
            f"{report.summary.get('pass', 0)} passed, "
            f"{report.summary.get('warn', 0)} warnings, "
            f"{report.summary.get('fail', 0)} failed, "
            f"{report.summary.get('skip', 0)} skipped\n",
            flush=True,
        )

    def _readiness(self, label: str, status: str) -> None:
        self._write(f"  {self._status(status.upper(), status, width=11)} {label}\n")

    def _status(self, label: str, style: str, *, width: int) -> str:
        padded = f"{label:<{width}}"
        if not self.color_enabled:
            return padded
        return f"{_STYLES.get(style, '')}{padded}{_RESET}"

    def _finish_running_line(self) -> None:
        if not self._running:
            return
        self._write("\n")
        self._running = False

    def _write(self, value: str, *, flush: bool = False) -> None:
        self.stream.write(value)
        if flush:
            self.stream.flush()


def _color_enabled(policy: str, tty: bool) -> bool:
    if policy == "always":
        return True
    if policy == "never":
        return False
    return tty and "NO_COLOR" not in os.environ and os.environ.get("TERM", "").casefold() != "dumb"
