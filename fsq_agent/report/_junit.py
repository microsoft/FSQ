# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import json
import xml.etree.ElementTree as ET

from fsq_agent.models import PublicRunReport


def render_junit(report: PublicRunReport) -> bytes:
    gate = report.run["gate"]["status"]
    suite = ET.Element("testsuite", name="FSQ", tests="1", failures=str(int(gate == "failed")), errors=str(int(gate in {"error", "incomplete"})), skipped="0")
    duration = report.metrics.get("run_duration", {}).get("value")
    case = ET.SubElement(
        suite,
        "testcase",
        name=str(report.source.get("case_id") or report.source.get("goal_summary") or report.run["run_id"]),
        classname=f"fsq.{report.run.get('platform') or 'unknown'}",
    )
    if isinstance(duration, (int, float)) and not isinstance(duration, bool):
        case.set("time", str(duration / 1000))
    properties = ET.SubElement(case, "properties")
    values = {
        "fsq.run_id": report.run["run_id"],
        "fsq.schema": report.schema_version,
        "fsq.execution": report.execution.get("outcome"),
        "fsq.verification": report.verification.get("status"),
        "fsq.evidence": report.evidence.get("status"),
        "fsq.gate": gate,
        "fsq.processing": report.processing,
        "fsq.counts": report.execution.get("counts"),
        "fsq.html_anchor": f"run-{report.run['run_id']}",
        "fsq.duration_availability": "measured" if isinstance(duration, (int, float)) else "unavailable",
    }
    for name, value in values.items():
        ET.SubElement(properties, "property", name=name, value=json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else str(value or ""))
    if gate != "passed":
        detail = report.execution.get("first_failure") or {}
        failure = ET.SubElement(
            case,
            "failure" if gate == "failed" else "error",
            type=str(detail.get("failure_category") or gate),
            message=str(detail.get("error_message") or report.execution.get("summary") or ", ".join(report.run["gate"]["reasons"])),
        )
        failure.text = json.dumps({"gate": report.run["gate"], "first_failure": detail}, ensure_ascii=False)
    output = ET.SubElement(case, "system-out")
    text = json.dumps({"steps": report.steps, "tool_calls": report.tool_calls, "lineage": report.lineage, "warnings": report.warnings}, ensure_ascii=False)
    output.text = text if len(text) <= 512 * 1024 else text[: 512 * 1024] + "\n[TRUNCATED: complete details in public JSON/HTML]"
    changed = False
    for element in suite.iter():
        for key, value in list(element.attrib.items()):
            cleaned = _xml_text(value)
            changed = changed or cleaned != value
            element.set(key, cleaned)
        if element.text:
            cleaned = _xml_text(element.text)
            changed = changed or cleaned != element.text
            element.text = cleaned
    if changed:
        warning = "XML-forbidden characters were replaced in the derived JUnit view."
        if warning not in report.warnings:
            report.warnings.append(warning)
        ET.SubElement(properties, "property", name="fsq.serialization_warning", value=warning)
    ET.indent(suite)
    return ET.tostring(suite, encoding="utf-8", xml_declaration=True)


def _xml_text(value):
    return "".join(char if ord(char) in {9, 10, 13} or 0x20 <= ord(char) <= 0xD7FF or 0xE000 <= ord(char) <= 0xFFFD or 0x10000 <= ord(char) <= 0x10FFFF else "\ufffd" for char in value)
