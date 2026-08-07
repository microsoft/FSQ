# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from __future__ import annotations

from pathlib import Path

import click

from fsq_agent.doctor import DoctorProgressTextRenderer, DoctorService, render_doctor_json
from fsq_agent.models import (
    DoctorCheckResult,
    DoctorFix,
    DoctorReadiness,
    DoctorReadinessItem,
    DoctorReport,
    DoctorRequest,
)


def run_doctor_command(
    *,
    platform: str | None,
    mode: str | None,
    output_format: str,
    color: str,
    non_interactive: bool,
    repair: bool,
) -> int:
    try:
        if output_format == "json" and repair:
            report = _usage_error_report(mode or "all", "--format json cannot be combined with --repair.")
            click.echo(render_doctor_json(report), nl=False)
            return 2
        stdout = click.get_text_stream("stdout")
        stdin_tty = bool(click.get_text_stream("stdin").isatty())
        stdout_tty = bool(stdout.isatty())
        interactive = (
            output_format == "text"
            and not non_interactive
            and stdin_tty
            and stdout_tty
        )
        selected_mode = mode
        if selected_mode is None and interactive:
            selected_mode = click.prompt(
                "Diagnostic mode",
                type=click.Choice(["dynamic", "strict", "all"]),
                default="all",
                show_choices=True,
            )
        request = DoctorRequest(
            platform=platform,
            mode=selected_mode or "all",
            output_format=output_format,
            interactive=interactive,
            repair=repair,
            working_directory=Path.cwd(),
        )
        if output_format == "json":
            report = DoctorService().run(request)
        else:
            renderer = DoctorProgressTextRenderer(stdout, tty=stdout_tty, color=color)
            renderer.write_header(platform, selected_mode or "all")
            report = DoctorService(progress_sink=renderer).run(request)
    except KeyboardInterrupt:
        raise click.exceptions.Exit(130) from None
    if output_format == "json":
        click.echo(render_doctor_json(report), nl=False)
    return report.exit_code


__all__ = ["run_doctor_command"]


def _usage_error_report(mode: str, summary: str) -> DoctorReport:
    check = DoctorCheckResult(
        id="doctor.option_combination",
        category="Usage",
        status="fail",
        summary=summary,
        fixes=[
            DoctorFix(
                description="Remove --repair or use --format text.",
                verification_command="fsq-agent doctor --format json --non-interactive --platform <platform>",
            )
        ],
    )
    not_checked = DoctorReadinessItem(status="not_checked")
    return DoctorReport(
        requested_mode=mode,  # type: ignore[arg-type]
        status="usage_error",
        exit_code=2,
        checks=[check],
        readiness=DoctorReadiness(
            dynamic_llm=not_checked,
            strict_core=not_checked,
            ai_assertion=not_checked,
        ),
        summary={"pass": 0, "warn": 0, "fail": 1, "skip": 0},
    )
