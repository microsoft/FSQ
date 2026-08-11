# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from __future__ import annotations

from fsq_agent.models import DoctorReport
from fsq_agent.doctor._presentation import check_title


_SUMMARY_LABELS = {
    "ready": "PASS",
    "blocked": "FAIL",
    "usage_error": "ERROR",
    "cancelled": "CANCELLED",
}


def render_doctor_json(report: DoctorReport) -> str:
    return report.model_dump_json(indent=2, exclude_none=True) + "\n"


def render_doctor_text(report: DoctorReport) -> str:
    lines = [
        "FSQ Doctor",
        "",
        f"Platform: {report.platform or 'unresolved'} ({report.platform_source})",
        "",
    ]
    repairs_by_target = {}
    for repair in report.repairs:
        repairs_by_target.setdefault(repair.target, []).append(repair)
    matched_targets: set[str] = set()
    for check in report.checks:
        lines.append(f"[{check_title(check.id)}]")
        lines.append(f"  Check: {check.summary}")
        for repair in repairs_by_target.get(check.id, []):
            matched_targets.add(check.id)
            lines.append(f"  Repair: {repair.status.upper()}")
            if repair.backup_path:
                lines.append(f"  Backup: {repair.backup_path}")
        lines.append(f"  Result: {check.status.upper()}")
        if check.status in {"warn", "fail"}:
            for fix in check.fixes:
                lines.append(f"  Fix: {fix.description}")
                if fix.command:
                    lines.append(f"  Run: {fix.command}")
                if fix.verification_command:
                    lines.append(f"  Verify: {fix.verification_command}")
        lines.append("")
    for target, repairs in repairs_by_target.items():
        if target in matched_targets:
            continue
        lines.append(f"[Repair: {target}]")
        for repair in repairs:
            lines.append(f"  Repair: {repair.status.upper()}")
            if repair.backup_path:
                lines.append(f"  Backup: {repair.backup_path}")
        lines.append("")
    lines.append(f"Summary: {_SUMMARY_LABELS[report.status]}")
    lines.append(
        "Checks: "
        f"{report.summary.get('pass', 0)} passed, "
        f"{report.summary.get('warn', 0)} warnings, "
        f"{report.summary.get('fail', 0)} failed, "
        f"{report.summary.get('skip', 0)} skipped"
    )
    return "\n".join(lines) + "\n"
