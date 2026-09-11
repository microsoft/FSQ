# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import base64
import hashlib
import html
import json
from collections.abc import Mapping
from pathlib import Path

from fsq_agent.models import PublicRunReport, RunReportExportOptions
from fsq_agent.report._run_report import MAX_INLINE_IMAGE_BYTES, RunReportService

_SCRIPT = "document.querySelectorAll('[data-step-link]').forEach(function(b){b.addEventListener('click',function(){var s=document.getElementById(b.dataset.stepLink);if(s){s.open=true;s.scrollIntoView({block:'start'});}});});"
_CSS = "body{font:15px system-ui;margin:0;color:#20242c;background:#f5f6fa}main{max-width:1250px;margin:auto;padding:28px}h1{font-size:26px}h2{font-size:20px}section,details{background:white;border:1px solid #d8dce5;border-radius:8px;padding:16px;margin:16px 0}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f3f4f8;padding:12px}table{width:100%;border-collapse:collapse;table-layout:fixed}td,th{padding:8px;border-bottom:1px solid #ddd;text-align:left;overflow-wrap:anywhere}button{background:#164fbd;color:white;border:0;border-radius:4px;padding:6px 10px;cursor:pointer;white-space:nowrap}.pair{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(260px,100%),1fr));gap:12px}img{max-width:100%;max-height:600px;object-fit:contain}.failed,.error{border-left:5px solid #a32b2b}.passed{border-left:5px solid #258143}.incomplete{border-left:5px solid #aa7618}.changed{background:#fff1bd}.added{background:#e1f4e7}.removed{background:#ffe0df}.muted{color:#596579}code{white-space:pre-wrap}summary{cursor:pointer}a{color:#164fbd}mark{background:#ffe08a}.metrics{display:flex;gap:16px;flex-wrap:wrap}.metrics span{font-size:13px;padding:6px 8px;background:#f3f4f8;border-radius:4px}@media(max-width:600px){main{padding:12px}section,details{padding:12px}td,th{padding:6px 3px;font-size:12px}button{padding:6px;font-size:12px}}"


def _escape(value):
    return html.escape(str(value or ""), quote=True)


def _json(value):
    return _escape(json.dumps(value, ensure_ascii=False, indent=2))


def render_html(report: PublicRunReport, contents: dict) -> str:
    run_id = str(report.run["run_id"])
    gate = report.run["gate"]
    script_hash = base64.b64encode(hashlib.sha256(_SCRIPT.encode()).digest()).decode()
    body = [
        f'<header id="run-{_escape(run_id)}"><p class="muted">FSQ · Evidence report · {_escape(report.schema_version)}</p><h1>{_escape(str(report.run.get("mode") or "Run").title())} evidence report</h1><p><strong>Run:</strong> {_escape(run_id)}</p><p class="muted"><strong>Case / Goal:</strong> {_escape(report.source.get("case_id") or report.source.get("goal_summary") or "Unavailable")}</p></header>'
    ]
    body.append(
        f'<section class="{gate["status"]}"><h2>{_escape(gate["status"]).upper()}</h2><p>{_escape(report.execution.get("summary"))}</p><p>Execution: {_escape(report.execution.get("outcome"))} · Verification: {_escape(report.verification.get("status"))} · Evidence: {_escape(report.evidence.get("status"))}</p><p>{_escape(", ".join(gate["reasons"]))}</p></section>'
    )
    failure = report.execution.get("first_failure")
    if isinstance(failure, dict):
        details = failure.get("assertion_details") or next((step.get("assertion_details") for step in report.steps if step["step_execution_id"] == failure.get("step_execution_id")), None)
        body.append(
            f'<section class="failed"><h2>First blocking failure</h2><p>{_escape(failure.get("action_name"))}: {_escape(failure.get("error_message"))}</p><p>{_escape(failure.get("failure_category"))}</p>{_assertion_html(details)}<button type="button" data-step-link="step-{_escape(failure.get("step_execution_id"))}">Inspect step evidence</button></section>'
        )
    body.append(_outcomes_html(report))
    body.append(
        f"<details><summary>Run, source, metrics and processing</summary><pre>{_json({'run': report.run, 'source': report.source, 'metrics': report.metrics, 'processing': report.processing})}</pre></details>"
    )
    body.append("<section><h2>Step timeline</h2><table><thead><tr><th>Action / source</th><th>Status</th><th>Duration</th><th>Evidence</th></tr></thead><tbody>")
    for step in report.steps:
        timing = step.get("metrics", {}).get("duration_ms", {})
        duration = f"{timing['value']} ms" if timing.get("value") is not None else "Unavailable"
        body.append(
            f'<tr><td>{_escape(step.get("authored_action_name") or step.get("action_name") or step["step_id"])}<br><small>{_escape(step.get("source_step_id"))} · {_escape(step.get("lifecycle_phase"))}</small></td><td>{_escape(step["status"])}</td><td>{duration}</td><td><button type="button" data-step-link="step-{_escape(step["step_execution_id"] or step["step_id"])}">Inspect</button></td></tr>'
        )
    body.append("</tbody></table></section>")
    image_bytes = 0
    for step in report.steps:
        identity = step["step_execution_id"] or step["step_id"]
        open_attribute = " open" if isinstance(failure, dict) and identity == failure.get("step_execution_id") else ""
        body.append(
            f'<details id="step-{_escape(identity)}"{open_attribute}><summary>{_escape(step.get("action_name") or identity)} · {_escape(step["status"])}</summary><p>{_escape(step.get("error_message"))}</p>{_assertion_html(step.get("assertion_details"))}<div class="metrics">'
        )
        body.extend(
            f"<span>{_escape(name.replace('_ms', ''))}: {str(value['value']) + ' ms' if value.get('value') is not None else 'Unavailable'}</span>" for name, value in step.get("metrics", {}).items()
        )
        body.append('</div><div class="pair">')
        artifacts = [item for item in report.artifacts if item.get("run_id") == run_id and item.get("step_execution_id") == identity]
        for artifact in artifacts:
            content = contents.get((artifact["run_id"], artifact["artifact_id"]))
            body.append(f'<article id="artifact-{_escape(artifact["run_id"])}-{_escape(artifact["artifact_id"])}"><h3>{_escape(artifact.get("phase"))} · {_escape(artifact["kind"])}</h3>')
            if artifact["kind"] == "screenshot" and content:
                image_bytes += len(content)
                if image_bytes <= MAX_INLINE_IMAGE_BYTES:
                    encoded = base64.b64encode(content).decode()
                    body.append(f'<img alt="{_escape(artifact["artifact_id"])}" src="data:image/png;base64,{encoded}">')
                else:
                    artifact.update(display_availability="omitted", display_unavailable_reason="inline_image_budget")
                    body.append("<p>Image omitted: report inline resource limit.</p>")
            elif isinstance(artifact.get("content"), str):
                body.append(f"<pre>{_escape(artifact.get('normalized_content') or artifact['content'])}</pre>")
            else:
                body.append(f"<p>{_escape(artifact.get('availability'))}: {_escape(artifact.get('unavailable_reason'))}</p>")
            if artifact.get("truncated") or artifact.get("transformed"):
                body.append('<p class="muted">Partial or transformed evidence; consult inventory.</p>')
            body.append("</article>")
        body.append("</div>")
        body.extend(_diff_html(diff) for diff in report.comparison.get("before_after", []) if diff.get("step_execution_id") == identity)
        body.append(f"<details><summary>Complete step facts</summary><pre>{_json(step)}</pre></details>")
        body.append("</details>")
    if report.comparison.get("baseline_current"):
        body.append(_baseline_html(report))
    else:
        body.append('<section><h2>Baseline vs current</h2><p class="muted">No comparable baseline was supplied.</p></section>')
    body.append("<section><h2>Source, candidate, suggestions and related evidence</h2>")
    for artifact in report.artifacts:
        if artifact.get("step_execution_id") and artifact.get("run_id") == run_id:
            continue
        body.append(f"<details><summary>{_escape(artifact['run_id'])} · {_escape(artifact['artifact_id'])}</summary>")
        content = contents.get((artifact["run_id"], artifact["artifact_id"]))
        if artifact.get("kind") == "screenshot" and content and image_bytes + len(content) <= MAX_INLINE_IMAGE_BYTES:
            image_bytes += len(content)
            body.append(f'<img alt="{_escape(artifact["artifact_id"])}" src="data:image/png;base64,{base64.b64encode(content).decode()}">')
        elif artifact.get("kind") == "screenshot" and content:
            artifact.update(display_availability="omitted", display_unavailable_reason="inline_image_budget")
            body.append("<p>Image omitted: inline_image_budget. Complete artifact remains available in the bundle.</p>")
        elif artifact.get("content") is not None:
            body.append(f"<pre>{_escape(artifact.get('normalized_content') or artifact['content'])}</pre>")
        else:
            body.append(f"<p>{_escape(artifact.get('availability'))}: {_escape(artifact.get('unavailable_reason'))}</p>")
        body.append("</details>")
    body.append("</section>")
    body.append(
        f"<details><summary>Lineage and comparison provenance</summary><pre>{_json({'lineage': report.lineage, 'comparison': {key: value for key, value in report.comparison.items() if key != 'baseline_current'}})}</pre></details>"
    )
    body.append(f"<details><summary>Structured logs</summary><pre>{_json(report.logs)}</pre></details>")
    body.append(f"<details><summary>Evidence inventory</summary><pre>{_json([{key: value for key, value in item.items() if key != 'content'} for item in report.artifacts])}</pre></details>")
    body.append(
        f'<section><h2>Availability and warnings</h2><pre>{_json({"evidence": report.evidence, "warnings": report.warnings})}</pre><p class="muted">Derived local evidence. Sharing transformations do not establish anonymity.</p></section>'
    )
    policy = f"default-src 'none'; img-src data:; style-src 'unsafe-inline'; script-src 'sha256-{script_hash}'; connect-src 'none'; base-uri 'none'; form-action 'none'"
    return f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="{_escape(policy)}"><meta name="viewport" content="width=device-width,initial-scale=1"><title>FSQ Run {_escape(run_id)}</title><style>{_CSS}</style></head><body><main>{"".join(body)}</main><script>{_SCRIPT}</script></body></html>'


def _diff_html(diff, *, title="UI snapshot diff", left="Before", right="After"):
    rows = "".join(f'<tr class="{_escape(row["kind"])}"><td><code>{_segments_html(row, "before")}</code></td><td><code>{_segments_html(row, "after")}</code></td></tr>' for row in diff.get("rows", []))
    return f"<h3>{_escape(title)} · {_escape(diff['status'])}</h3><p>{_escape(diff.get('reason'))}</p><table><thead><tr><th>{_escape(left)}</th><th>{_escape(right)}</th></tr></thead><tbody>{rows}</tbody></table>"


def _assertion_html(details):
    if not isinstance(details, dict):
        return ""
    return f'<div class="pair"><article><h3>Expected</h3><pre>{_json(details.get("expected"))}</pre></article><article><h3>Observed</h3><pre>{_escape(details.get("observed"))}</pre></article></div>'


def _outcomes_html(report):
    reports = [("Current", report.model_dump(mode="json"))]
    baseline = report.comparison.get("baseline_report")
    if isinstance(baseline, dict):
        reports.append(("Baseline", baseline))
    reports.extend(("Related", item) for item in report.lineage.get("related_runs", []) if isinstance(item, dict) and isinstance(item.get("run"), dict))
    rows = []
    for label, item in reports:
        rows.append(
            f"<tr><td>{label}<br><small>{_escape(item['run']['run_id'])}</small></td><td>{_escape(item['run'].get('mode'))}</td><td>{_escape(item['execution'].get('outcome'))}</td><td>{_escape(item['run']['gate']['status'])}</td><td>{_escape(item['execution'].get('summary'))}</td></tr>"
        )
    return (
        f"<section><h2>Run outcomes</h2><table><thead><tr><th>Run</th><th>Mode</th><th>Execution</th><th>Report gate</th><th>Result</th></tr></thead><tbody>{''.join(rows)}</tbody></table></section>"
    )


def _baseline_html(report):
    comparison = report.comparison["baseline_current"]
    body = [
        f'<section><h2>Baseline vs current</h2><p>Baseline: {_escape(comparison.get("baseline_run_id"))}</p><p>Current: {_escape(report.run["run_id"])}</p><p class="muted">Matched by persisted source identity. Differences describe observed UI changes.</p>'
    ]
    if comparison.get("status") != "comparable":
        body.append(f"<p>{_escape(comparison.get('status'))}: {_escape(comparison.get('reason'))}</p>")
    for row in comparison.get("steps", []):
        current = row.get("current") or {}
        step = next((item for item in report.steps if item["step_execution_id"] == current.get("step_execution_id")), {})
        diff = row.get("snapshot") or {"status": "unavailable"}
        opened = " open" if diff.get("status") == "changed" else ""
        body.append(
            f"<details{opened}><summary>{_escape(step.get('authored_action_name') or step.get('action_name') or current.get('step_execution_id'))} · {_escape(row.get('status'))} · {_escape(diff.get('status'))}</summary>"
        )
        body.append(_diff_html(diff, title="Baseline UI snapshot comparison", left="Baseline", right="Current"))
        body.append("</details>")
    if comparison.get("unmatched_baseline"):
        body.append(f"<p>Unmatched baseline steps: {len(comparison['unmatched_baseline'])}</p>")
    body.append("</section>")
    return "".join(body)


def _segments_html(row, side):
    segments = row.get(f"{side}_segments")
    if not segments:
        return _escape(row.get(side))
    return "".join(f"<mark>{_escape(item['text'])}</mark>" if item["changed"] else _escape(item["text"]) for item in segments)


def generate_static_run_report(run_dir: Path, facts: Mapping[str, object]) -> Path:
    from fsq_agent.report._export import export_report

    supplied = dict(facts)
    identity = supplied.get("run_id")
    if isinstance(identity, str) and any(char in identity for char in ("/", chr(92), "<", ">")):
        supplied["run_id"] = Path(run_dir).name
    report = RunReportService().project(run_dir, supplied)
    return export_report(report, RunReportExportOptions(format="html", destination=Path(run_dir) / "report.html"), replace_derived=True).path


__all__ = ["generate_static_run_report"]
