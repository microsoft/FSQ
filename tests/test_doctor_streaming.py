# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from __future__ import annotations

from io import StringIO
from pathlib import Path
from typing import Literal

import pytest

from fsq_agent.doctor import DoctorProgressTextRenderer, DoctorService, render_doctor_text
from fsq_agent.models import (
    DiagnosticProbeResult,
    DoctorCheckResult,
    DoctorFix,
    DoctorProgressEvent,
    DoctorRepairResult,
    DoctorReport,
    DoctorRequest,
)


def _check(status: str = "pass") -> DoctorCheckResult:
    return DoctorCheckResult(
        id="android.adb.devices",
        category="Android",
        status=status,
        summary="Android device discovery completed.",
    )


def _report(
    status: Literal["ready", "blocked", "usage_error", "cancelled"] = "ready",
    exit_code: Literal[0, 1, 2, 130] = 0,
) -> DoctorReport:
    return DoctorReport(
        platform="android",
        platform_source="explicit",
        status=status,
        exit_code=exit_code,
        checks=[_check()],
        summary={"pass": 1, "warn": 0, "fail": 0, "skip": 0},
    )


def test_tty_renderer_replaces_running_line_and_highlights_status() -> None:
    stream = StringIO()
    renderer = DoctorProgressTextRenderer(stream, tty=True, color="always")

    renderer(DoctorProgressEvent(event_type="phase_started", phase="Android", summary="Android"))
    renderer(
        DoctorProgressEvent(
            event_type="check_started",
            phase="Android",
            check_id="android.adb.devices",
            summary="Discovering Android devices...",
        )
    )
    renderer(
        DoctorProgressEvent(
            event_type="check_completed",
            phase="Android",
            check_id="android.adb.devices",
            status="pass",
            summary="done",
            check=_check(),
        )
    )

    output = stream.getvalue()
    assert "\r\x1b[2K" in output
    assert "\x1b[36m" in output
    assert "\x1b[32m" in output
    assert "RUNNING" in output and "PASS" in output


def test_plain_renderer_appends_lines_without_terminal_sequences() -> None:
    stream = StringIO()
    renderer = DoctorProgressTextRenderer(stream, tty=False, color="auto")

    renderer(
        DoctorProgressEvent(
            event_type="check_started",
            phase="Android",
            check_id="android.adb.devices",
            summary="Discovering Android devices...",
        )
    )
    renderer(
        DoctorProgressEvent(
            event_type="check_completed",
            phase="Android",
            check_id="android.adb.devices",
            status="fail",
            summary="failed",
            check=_check("fail"),
        )
    )

    output = stream.getvalue()
    assert "[ADB devices]" in output
    assert "  Check: RUNNING  — Discovering Android devices..." in output
    assert "  Result: FAIL" in output
    assert "RUNNING" in output and "FAIL" in output
    assert "\x1b[" not in output
    assert "\r" not in output


def test_renderer_displays_action_required_without_final_failure() -> None:
    stream = StringIO()
    renderer = DoctorProgressTextRenderer(stream, tty=False, color="never")
    check = DoctorCheckResult(
        id="workspace.initialized",
        category="Workspace",
        status="fail",
        summary="Workspace is not initialized.",
        fixes=[],
    )

    renderer(
        DoctorProgressEvent(
            event_type="action_required",
            check_id=check.id,
            status=check.status,
            summary=check.summary,
            check=check,
        )
    )

    output = stream.getvalue()
    assert "[Workspace initialized]" in output
    assert "  Check: Workspace is not initialized." in output
    assert "  Action:" in output
    assert "ACTION REQUIRED" in output
    assert "FAIL" not in output


def test_tty_renderer_clears_action_required_after_decision() -> None:
    stream = StringIO()
    renderer = DoctorProgressTextRenderer(stream, tty=True, color="never")
    check = DoctorCheckResult(
        id="workspace.initialized",
        category="Workspace",
        status="fail",
        summary="Workspace is not initialized.",
        fixes=[DoctorFix(description="Initialize the workspace.")],
    )

    renderer(
        DoctorProgressEvent(
            event_type="action_required",
            check_id=check.id,
            check=check,
        )
    )
    renderer(
        DoctorProgressEvent(
            event_type="repair_started",
            check_id=check.id,
            repair_action="workspace.initialize",
            summary="Applying repair...",
        )
    )

    output = stream.getvalue()
    assert "\x1b[3A" in output
    assert output.count("\r\x1b[2K") >= 5
    assert "\x1b[3A\r" in output
    assert "RUNNING" in output


def test_repair_events_reuse_target_check_section() -> None:
    stream = StringIO()
    renderer = DoctorProgressTextRenderer(stream, tty=False, color="never")
    check = DoctorCheckResult(
        id="workspace.initialized",
        category="Workspace",
        status="fail",
        summary="Workspace is not initialized.",
    )

    renderer(
        DoctorProgressEvent(
            event_type="action_required",
            check_id=check.id,
            check=check,
        )
    )
    renderer(
        DoctorProgressEvent(
            event_type="repair_started",
            check_id=check.id,
            repair_action="workspace.initialize",
            summary="Applying repair...",
        )
    )
    renderer(
        DoctorProgressEvent(
            event_type="repair_completed",
            repair_action="workspace.initialize",
            repair=DoctorRepairResult(
                action_id="workspace.initialize",
                target=check.id,
                status="applied",
            ),
        )
    )

    output = stream.getvalue()
    assert output.count("[Workspace initialized]") == 1
    assert "  Repair: RUNNING" in output
    assert "  Choice:" not in output
    assert "  Input: y" in output
    assert "  Repair: APPLIED" in output


def test_declined_repair_keeps_action_without_choice_line() -> None:
    stream = StringIO()
    renderer = DoctorProgressTextRenderer(stream, tty=False, color="never")
    check = DoctorCheckResult(
        id="macos.target",
        category="macOS",
        status="fail",
        summary="The configured application path does not exist.",
        fixes=[DoctorFix(description="Correct FSQ_MACOS_APP_PATH.")],
    )

    renderer(DoctorProgressEvent(event_type="action_required", check_id=check.id, check=check))
    renderer(
        DoctorProgressEvent(
            event_type="repair_completed",
            repair_action="environment.update",
            repair=DoctorRepairResult(
                action_id="environment.update",
                target=check.id,
                status="declined",
            ),
        )
    )

    output = stream.getvalue()
    assert "Correct FSQ_MACOS_APP_PATH." in output
    assert "  Choice:" not in output
    assert "  Input: n" in output
    assert "  Repair: DECLINED" in output


def test_renderer_header_has_no_mode_line() -> None:
    stream = StringIO()
    renderer = DoctorProgressTextRenderer(stream, tty=False, color="never")

    renderer.write_header("android")

    assert stream.getvalue() == "FSQ Doctor\n\nPlatform: android (explicit)\n\n"


def test_phase_events_do_not_render_duplicate_headings() -> None:
    stream = StringIO()
    renderer = DoctorProgressTextRenderer(stream, tty=False, color="never")

    renderer(DoctorProgressEvent(event_type="phase_started", phase="Provider", summary="Provider"))

    assert stream.getvalue() == ""


@pytest.mark.parametrize(
    ("status", "escape"),
    [
        ("pass", "\x1b[32m"),
        ("warn", "\x1b[33m"),
        ("fail", "\x1b[1;31m"),
        ("skip", "\x1b[2;37m"),
    ],
)
def test_renderer_highlights_every_final_check_status(status: str, escape: str) -> None:
    stream = StringIO()
    renderer = DoctorProgressTextRenderer(stream, tty=True, color="always")
    check = _check(status)

    renderer(
        DoctorProgressEvent(
            event_type="check_completed",
            phase="Android",
            check_id=check.id,
            status=status,
            summary=check.summary,
            check=check,
        )
    )

    assert escape in stream.getvalue()


def test_color_never_disables_ansi_on_tty() -> None:
    stream = StringIO()
    renderer = DoctorProgressTextRenderer(stream, tty=True, color="never")
    check = _check("fail")

    renderer(DoctorProgressEvent(event_type="check_completed", check_id=check.id, check=check))

    assert "\x1b[" not in stream.getvalue()


def test_color_always_enables_ansi_on_plain_stream() -> None:
    stream = StringIO()
    renderer = DoctorProgressTextRenderer(stream, tty=False, color="always")
    check = _check("pass")

    renderer(DoctorProgressEvent(event_type="check_completed", check_id=check.id, check=check))

    assert "\x1b[32m" in stream.getvalue()


def test_color_auto_respects_no_color(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NO_COLOR", "1")
    stream = StringIO()
    renderer = DoctorProgressTextRenderer(stream, tty=True, color="auto")
    check = _check("pass")

    renderer(DoctorProgressEvent(event_type="check_completed", check_id=check.id, check=check))

    assert "\x1b[" not in stream.getvalue()


def test_summary_event_prints_overall_result_without_repeating_checks() -> None:
    stream = StringIO()
    renderer = DoctorProgressTextRenderer(stream, tty=False, color="never")
    report = _report()

    renderer(
        DoctorProgressEvent(
            event_type="summary_ready",
            phase="Summary",
            summary="ready",
            report=report,
        )
    )

    output = stream.getvalue()
    assert "Summary: PASS" in output
    assert "Checks: 1 passed, 0 warnings, 0 failed, 0 skipped" in output
    assert "Readiness" not in output
    assert "Strict core" not in output
    assert "Android device discovery completed" not in output


@pytest.mark.parametrize(
    ("report_status", "exit_code", "display_status"),
    [
        ("ready", 0, "PASS"),
        ("blocked", 1, "FAIL"),
        ("usage_error", 2, "ERROR"),
        ("cancelled", 130, "CANCELLED"),
    ],
)
def test_summary_event_maps_report_status(
    report_status: Literal["ready", "blocked", "usage_error", "cancelled"],
    exit_code: Literal[0, 1, 2, 130],
    display_status: str,
) -> None:
    stream = StringIO()
    renderer = DoctorProgressTextRenderer(stream, tty=False, color="never")

    renderer(
        DoctorProgressEvent(
            event_type="summary_ready",
            report=_report(report_status, exit_code),
        )
    )

    expected = f"Summary: {display_status}\nChecks:"
    assert expected in stream.getvalue()
    assert expected in render_doctor_text(_report(report_status, exit_code))


def test_doctor_service_forwards_progress_sink_to_platform_probe(tmp_path: Path) -> None:
    events: list[DoctorProgressEvent] = []

    class Probe:
        def __init__(self, sink):
            self.sink = sink

        def probe(self, timeout_seconds=5.0):
            result = DiagnosticProbeResult(
                id="android.live",
                category="Android",
                status="pass",
                summary="Live check complete.",
            )
            self.sink(
                DoctorProgressEvent(
                    event_type="check_started",
                    phase="Android",
                    check_id=result.id,
                    summary="Running live check...",
                )
            )
            self.sink(
                DoctorProgressEvent(
                    event_type="check_completed",
                    phase="Android",
                    check_id=result.id,
                    status=result.status,
                    summary=result.summary,
                    check=DoctorCheckResult.model_validate(result.model_dump()),
                )
            )
            return [result]

    class Factory:
        def create(self, platform, harness_settings, progress_sink=None):
            return Probe(progress_sink)

    class Provider:
        def probe(self, settings, timeout_seconds=5.0, progress_sink=None):
            return []

    (tmp_path / "config.android.yaml").write_text("harness:\n  platform: android\n", encoding="utf-8")
    workspace = tmp_path / ".fsq-agent-workspace"
    workspace.mkdir()
    (workspace / ".fsq-agent-workspace").write_text("ok", encoding="utf-8")

    DoctorService(
        provider_diagnostics=Provider(),
        platform_probe_factory=Factory(),
        progress_sink=events.append,
        environ={"FSQ_ANDROID_APP_ID": "com.example.app"},
    ).run(DoctorRequest(platform="android", working_directory=tmp_path))

    live = [event.event_type for event in events if event.check_id == "android.live"]
    assert live == ["check_started", "check_completed"]
    assert events[-1].event_type == "summary_ready"


def test_progress_sink_failure_does_not_change_report(tmp_path: Path) -> None:
    class FailingSink:
        def __call__(self, event):
            raise RuntimeError("renderer failed")

    class Probe:
        def probe(self, timeout_seconds=5.0):
            return [
                DiagnosticProbeResult(
                    id="android.ready",
                    category="Android",
                    status="pass",
                    summary="ready",
                )
            ]

    class Factory:
        def create(self, platform, harness_settings, progress_sink=None):
            return Probe()

    (tmp_path / "config.android.yaml").write_text("harness:\n  platform: android\n", encoding="utf-8")
    workspace = tmp_path / ".fsq-agent-workspace"
    workspace.mkdir()
    (workspace / ".fsq-agent-workspace").write_text("ok", encoding="utf-8")

    report = DoctorService(
        provider_diagnostics=type("Provider", (), {"probe": lambda *_args, **_kwargs: []})(),
        platform_probe_factory=Factory(),
        progress_sink=FailingSink(),
        environ={"FSQ_ANDROID_APP_ID": "com.example.app"},
    ).run(DoctorRequest(platform="android", working_directory=tmp_path))

    assert report.exit_code == 0
