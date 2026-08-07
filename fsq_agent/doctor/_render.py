# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from __future__ import annotations

from collections import defaultdict

from fsq_agent.models import DoctorReport


def render_doctor_json(report: DoctorReport) -> str:
    return report.model_dump_json(indent=2, exclude_none=True) + "\n"


def render_doctor_text(report: DoctorReport) -> str:
    lines = [
        "FSQ Doctor",
        "",
        f"Platform: {report.platform or 'unresolved'} ({report.platform_source})",
        f"Mode: {report.requested_mode}",
        "",
    ]
    grouped: dict[str, list] = defaultdict(list)
    for check in report.checks:
        grouped[check.category].append(check)
    for category, checks in grouped.items():
        lines.append(category)
        for check in checks:
            lines.append(f"  {check.status.upper():4}  {check.summary}")
            if check.status in {"warn", "fail"}:
                if check.affected_targets:
                    lines.append(f"        Impact: {', '.join(check.affected_targets)}")
                for fix in check.fixes:
                    lines.append(f"        Fix: {fix.description}")
                    if fix.command:
                        lines.append(f"        Run: {fix.command}")
                    if fix.verification_command:
                        lines.append(f"        Verify: {fix.verification_command}")
        lines.append("")
    if report.repairs:
        lines.append("Repairs")
        for repair in report.repairs:
            lines.append(f"  {repair.status.upper():8} {repair.action_id}: {repair.target}")
            if repair.backup_path:
                lines.append(f"           Backup: {repair.backup_path}")
        lines.append("")
    lines.append("Readiness")
    lines.append(f"  {report.readiness.dynamic_llm.status.upper():11} Dynamic LLM")
    lines.append(f"  {report.readiness.strict_core.status.upper():11} Strict core")
    lines.append(f"  {report.readiness.ai_assertion.status.upper():11} AI assertion")
    lines.append("")
    lines.append(
        "Summary: "
        f"{report.summary.get('pass', 0)} passed, "
        f"{report.summary.get('warn', 0)} warnings, "
        f"{report.summary.get('fail', 0)} failed, "
        f"{report.summary.get('skip', 0)} skipped"
    )
    return "\n".join(lines) + "\n"
