# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from __future__ import annotations

from io import StringIO
from pathlib import Path

import pytest

from fsq_agent.doctor import DoctorProgressTextRenderer, DoctorService
from fsq_agent.models import (
    DiagnosticProbeResult,
    DoctorCheckResult,
    DoctorProgressEvent,
    DoctorReadiness,
    DoctorReadinessItem,
    DoctorReport,
    DoctorRequest,
)


def _check(status: str = "pass") -> DoctorCheckResult:
    return DoctorCheckResult(
        id="android.adb.devices",
        category="Android",
        status=status,
        summary="Android device discovery completed.",
        affected_targets=["strict"],
    )


def _report() -> DoctorReport:
    return DoctorReport(
        platform="android",
        platform_source="explicit",
        requested_mode="strict",
        status="ready",
        exit_code=0,
        checks=[_check()],
        readiness=DoctorReadiness(
            dynamic_llm=DoctorReadinessItem(status="not_checked"),
            strict_core=DoctorReadinessItem(status="ready"),
            ai_assertion=DoctorReadinessItem(status="not_checked"),
        ),
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
    assert "RUNNING" in output and "FAIL" in output
    assert "\x1b[" not in output
    assert "\r" not in output


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


def test_summary_event_prints_readiness_without_repeating_checks() -> None:
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
    assert "Readiness" in output
    assert "Strict core" in output
    assert "Android device discovery completed" not in output


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
                affected_targets=["strict"],
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
    ).run(DoctorRequest(platform="android", mode="strict", working_directory=tmp_path))

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
                    affected_targets=["strict"],
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
    ).run(DoctorRequest(platform="android", mode="strict", working_directory=tmp_path))

    assert report.exit_code == 0
