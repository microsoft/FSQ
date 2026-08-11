# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from __future__ import annotations

import os
from typing import TextIO

from fsq_agent.models import DoctorCheckResult, DoctorProgressEvent, DoctorReport
from fsq_agent.doctor._presentation import check_title


_RESET = "\x1b[0m"
_STYLES = {
    "pass": "\x1b[32m",
    "warn": "\x1b[33m",
    "fail": "\x1b[1;31m",
    "skip": "\x1b[2;37m",
    "running": "\x1b[36m",
    "applied": "\x1b[32m",
    "declined": "\x1b[33m",
    "skipped": "\x1b[2;37m",
    "failed": "\x1b[1;31m",
    "action_required": "\x1b[33m",
}
_SUMMARY_LABELS = {
    "ready": ("PASS", "pass"),
    "blocked": ("FAIL", "fail"),
    "usage_error": ("ERROR", "fail"),
    "cancelled": ("CANCELLED", "warn"),
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
        self._action_required_lines = 0
        self._active_check_id: str | None = None
        self._opened_check_ids: set[str] = set()
        self._action_checks: dict[str, DoctorCheckResult] = {}

    def write_header(self, platform: str | None) -> None:
        self._write("FSQ Doctor\n\n")
        if platform is not None:
            self._write(f"Platform: {platform} (explicit)\n")
            self._header_platform_written = True
        if platform is not None:
            self._write("\n")

    def __call__(self, event: DoctorProgressEvent) -> None:
        if event.event_type == "phase_started":
            if event.phase == "Platform":
                if not self._header_platform_written:
                    self._write(event.summary + "\n\n")
                    self._header_platform_written = True
        elif event.event_type == "check_started":
            self._render_check_started(event)
        elif event.event_type == "action_required" and event.check is not None:
            self._render_action_required(event.check)
        elif event.event_type == "check_completed" and event.check is not None:
            self._render_check(event.check)
        elif event.event_type == "repair_started":
            self._clear_action_required()
            if event.check_id:
                self._open_check_section(event.check_id)
            self._render_started(event.summary, label="Repair")
        elif event.event_type == "repair_completed" and event.repair is not None:
            self._render_repair(event)
        elif event.event_type == "summary_ready" and event.report is not None:
            self._render_summary(event.report)

    def _open_check_section(self, check_id: str) -> None:
        if check_id == self._active_check_id:
            return
        self._finish_running_line()
        if self._active_check_id is not None and check_id not in self._opened_check_ids:
            self._write("\n")
        self._active_check_id = check_id
        if check_id not in self._opened_check_ids:
            self._opened_check_ids.add(check_id)
            self._write(f"[{check_title(check_id)}]\n")

    def _render_check_started(self, event: DoctorProgressEvent) -> None:
        if event.check_id:
            self._open_check_section(event.check_id)
        label = "Verify" if event.summary.casefold().startswith("verifying") else "Check"
        self._render_started(event.summary, label=label)

    def _render_started(self, summary: str, *, label: str) -> None:
        self._finish_running_line()
        line = f"  {label}: {self._status('RUNNING', 'running', width=8)} — {summary}"
        if self.tty:
            self._write("\r\x1b[2K" + line, flush=True)
            self._running = True
        else:
            self._write(line + "\n", flush=True)

    def _render_check(self, check: DoctorCheckResult) -> None:
        self._open_check_section(check.id)
        prefix = "\r\x1b[2K" if self.tty and self._running else ""
        self._running = False
        self._write(
            prefix + f"  Check: {check.summary}\n",
            flush=True,
        )
        self._write(f"  Result: {self._status(check.status.upper(), check.status, width=len(check.status))}\n")
        if check.status in {"warn", "fail"}:
            for fix in check.fixes:
                self._write(f"  Fix: {fix.description}\n")
                if fix.command:
                    self._write(f"  Run: {fix.command}\n")
                if fix.verification_command:
                    self._write(f"  Verify: {fix.verification_command}\n")

    def _render_action_required(self, check: DoctorCheckResult) -> None:
        self._open_check_section(check.id)
        self._action_checks[check.id] = check
        prefix = "\r\x1b[2K" if self.tty and self._running else ""
        self._running = False
        self._action_required_lines = 2 + sum(
            1
            + int(fix.command is not None)
            + int(fix.verification_command is not None)
            for fix in check.fixes
        )
        self._write(
            prefix
            + f"  Check: {check.summary}\n",
            flush=True,
        )
        if not check.fixes:
            self._write(f"  Action: {self._status('ACTION REQUIRED', 'action_required', width=15)}\n")
        for fix in check.fixes:
            self._write(
                f"  Action: {self._status('ACTION REQUIRED', 'action_required', width=15)}"
                f" — {fix.description}\n"
            )
            if fix.command:
                self._write(f"  Run: {fix.command}\n")
            if fix.verification_command:
                self._write(f"  Verify: {fix.verification_command}\n")

    def _clear_action_required(self) -> None:
        if not self.tty or self._action_required_lines == 0:
            self._action_required_lines = 0
            return
        # The confirmation response occupies the line after the transient action block.
        # Erase both, then return to the first content row below the section title so
        # repair and verification output replaces the temporary interaction in place.
        self._write(f"\x1b[{self._action_required_lines}A", flush=True)
        rows_to_clear = self._action_required_lines + 1
        for index in range(rows_to_clear):
            self._write("\r\x1b[2K")
            if index < rows_to_clear - 1:
                self._write("\x1b[1B")
        self._write(f"\x1b[{self._action_required_lines}A\r")
        self._action_required_lines = 0

    def _render_repair(self, event: DoctorProgressEvent) -> None:
        repair = event.repair
        if repair is None:
            return
        self._open_check_section(repair.target)
        prefix = "\r\x1b[2K" if self.tty and self._running else ""
        self._running = False
        action_check = self._action_checks.pop(repair.target, None)
        if action_check is not None:
            if self.tty:
                for fix in action_check.fixes:
                    self._write(prefix + f"  Action: {fix.description}\n", flush=True)
                    prefix = ""
            user_input = "n" if repair.status == "declined" else "y"
            self._write(f"  Input: {user_input}\n")
        self._write(
            prefix
            + f"  Repair: {self._status(repair.status.upper(), repair.status, width=len(repair.status))}\n",
            flush=True,
        )
        if repair.backup_path:
            self._write(f"  Backup: {repair.backup_path}\n")

    def _render_summary(self, report: DoctorReport) -> None:
        self._finish_running_line()
        if self._active_check_id is not None:
            self._write("\n")
            self._active_check_id = None
        label, style = _SUMMARY_LABELS[report.status]
        self._write(f"\nSummary: {self._status(label, style, width=len(label))}\n")
        self._write(
            "Checks: "
            f"{report.summary.get('pass', 0)} passed, "
            f"{report.summary.get('warn', 0)} warnings, "
            f"{report.summary.get('fail', 0)} failed, "
            f"{report.summary.get('skip', 0)} skipped\n",
            flush=True,
        )

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
