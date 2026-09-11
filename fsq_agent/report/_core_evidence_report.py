# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import json
from pathlib import Path
from typing import Any

from fsq_agent.models import EvidenceBundle, ReportArtifact

_LIFECYCLE_PHASES = ("onCaseStart", "case", "onCaseComplete")
_LIFECYCLE_LABELS = {
    "onCaseStart": "Before case",
    "case": "Main case",
    "onCaseComplete": "After case",
}


class CoreEvidenceReportGenerator:
    def generate_from_manifest(self, manifest_path: Path) -> ReportArtifact:
        bundle = self._load_bundle(manifest_path)
        report_dir = manifest_path.parent
        markdown_path = report_dir / "core-report.md"
        json_path = report_dir / "core-report.json"
        report = self._build_report(bundle, manifest_path)
        markdown_path.write_text(self._render_markdown(report), encoding="utf-8")
        json_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        return ReportArtifact(
            run_id=bundle.run_id,
            path=markdown_path,
            evidence_manifest_path=manifest_path,
        )

    def _load_bundle(self, manifest_path: Path) -> EvidenceBundle:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        return EvidenceBundle.model_validate(payload).model_copy(update={"manifest_path": manifest_path})

    def _build_report(self, bundle: EvidenceBundle, manifest_path: Path) -> dict[str, Any]:
        steps = [step.model_dump(mode="json") for step in bundle.steps]
        for step in steps:
            if step.get("status") not in {"passed", "failed", "skipped", "cancelled", "incomplete"}:
                step["original_status"] = step.get("status")
                step["status"] = "incomplete"
        logical = self._logical_steps(steps)
        failed_steps = [step for step in logical if step["status"] == "failed"]
        lifecycle_steps = self._lifecycle_steps(steps)
        summary: dict[str, Any] = {
            "status": self._status([step["status"] for step in logical]),
            "step_count": len(logical),
            "passed_steps": len([step for step in logical if step["status"] == "passed"]),
            "failed_steps": len(failed_steps),
            "artifact_count": len(bundle.artifacts),
        }
        for status in ("skipped", "cancelled", "incomplete"):
            count = sum(step["status"] == status for step in logical)
            if count:
                summary[f"{status}_steps"] = count
        report: dict[str, Any] = {
            "run_id": bundle.run_id,
            "bundle_id": bundle.bundle_id,
            "manifest_path": str(manifest_path),
            "metadata": bundle.metadata,
            "summary": summary,
            "steps": steps,
            "events": [event.model_dump(mode="json") for event in bundle.events],
            "artifacts": [artifact.model_dump(mode="json") for artifact in bundle.artifacts],
        }
        if lifecycle_steps:
            lifecycle_summary = self._lifecycle_summary(self._lifecycle_steps(logical))
            summary["lifecycle"] = lifecycle_summary
            report["lifecycle"] = {"summary": lifecycle_summary, "steps": lifecycle_steps}
        return report

    def _render_markdown(self, report: dict[str, Any]) -> str:
        summary = report["summary"]
        lines = [
            f"# Core Evidence Report: {report['run_id']}",
            "",
            f"Status: `{summary['status']}`",
            f"Manifest: `{report['manifest_path']}`",
            "",
            "## Summary",
            "",
            f"- Steps: `{summary['step_count']}`",
            f"- Passed steps: `{summary['passed_steps']}`",
            f"- Failed steps: `{summary['failed_steps']}`",
            f"- Artifacts: `{summary['artifact_count']}`",
            "",
        ]
        lines.extend(f"- {status.title()} steps: {summary[f'{status}_steps']}" for status in ("skipped", "cancelled", "incomplete") if summary.get(f"{status}_steps"))

        lifecycle = report.get("lifecycle") if isinstance(report.get("lifecycle"), dict) else None
        if lifecycle:
            lines.extend(self._render_lifecycle_summary(lifecycle["summary"]))
            lines.extend(self._render_lifecycle_steps(lifecycle["steps"]))
        else:
            lines.extend(
                [
                    "## Steps",
                    "",
                    "| Step | Status | Failure Category | Error |",
                    "|---|---:|---|---|",
                ]
            )
            lines.extend(f"| `{step['step_id']}` | `{step['status']}` | `{step.get('failure_category') or ''}` | {step.get('error_message') or ''} |" for step in report["steps"])

        failed_steps = [step for step in report["steps"] if step["status"] == "failed"]
        if failed_steps:
            lines.extend(["", "## Failures", ""])
            lines.extend(f"- `{step['step_id']}` failed with `{step.get('failure_category') or 'unknown'}`: {step.get('error_message') or 'No error message.'}" for step in failed_steps)

        ai_assertions = self._ai_assertions(report)
        if ai_assertions:
            lines.extend(["", "## AI Assertions", ""])
            for assertion in ai_assertions:
                verdict = assertion.get("status") or ("passed" if assertion.get("passed") else "failed")
                provider = assertion.get("provider") or "unknown provider"
                model = assertion.get("model") or "unknown model"
                prompt = assertion.get("prompt") or ""
                explanation = assertion.get("explanation") or assertion.get("error") or "No explanation."
                lines.append(f"- `{assertion['step_id']}` `{verdict}` via `{provider}`/`{model}`: {explanation}")
                if prompt:
                    lines.append(f"  Prompt: {prompt}")
                lines.extend(f"  Artifact: `{artifact_path}`" for artifact_path in assertion.get("artifact_paths", []))

        lines.extend(["", "## Events", ""])
        for event in report["events"]:
            phase = f"/{event['phase']}" if event.get("phase") else ""
            step_id = event.get("step_id") or "run"
            lines.append(f"- `{event['event_type']}` `{step_id}{phase}`")

        lines.extend(["", "## Artifacts", ""])
        if report["artifacts"]:
            lines.extend(f"- `{artifact['kind']}` `{artifact['path']}`" for artifact in report["artifacts"])
        else:
            lines.append("No artifacts recorded.")
        lines.append("")
        return "\n".join(lines)

    def _render_lifecycle_summary(self, summary: dict[str, Any]) -> list[str]:
        lines = [
            "## Lifecycle Summary",
            "",
            "| Phase | Status | Steps | Passed | Failed |",
            "|---|---:|---:|---:|---:|",
        ]
        for phase in _LIFECYCLE_PHASES:
            phase_summary = summary[phase]
            lines.append(
                "| {label} | `{status}` | `{total}` | `{passed}` | `{failed}` |".format(
                    label=phase_summary["label"],
                    status=phase_summary["status"],
                    total=phase_summary["total_steps"],
                    passed=phase_summary["passed_steps"],
                    failed=phase_summary["failed_steps"],
                )
            )
            lines.extend(f"{phase_summary['label']} {status}: {phase_summary[f'{status}_steps']}" for status in ("skipped", "cancelled", "incomplete") if phase_summary.get(f"{status}_steps"))
        lines.append("")
        return lines

    def _render_lifecycle_steps(self, steps: list[dict[str, Any]]) -> list[str]:
        lines = [
            "## Steps",
            "",
            "| Phase | Source | Action | Step | Status | Failure Category | Error |",
            "|---|---|---|---|---:|---|---|",
        ]
        lines.extend(
            "| {phase} | {source} | `{action}` | `{step_id}` | `{status}` | `{failure}` | {error} |".format(
                phase=step["phase_label"],
                source=self._escape_markdown_table(str(step["source"])),
                action=self._escape_backticks(str(step["action"])),
                step_id=step["step_id"],
                status=step["status"],
                failure=step.get("failure_category") or "",
                error=self._escape_markdown_table(str(step.get("error_message") or "")),
            )
            for step in steps
        )
        lines.append("")
        return lines

    def _lifecycle_summary(self, steps: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        summary: dict[str, dict[str, Any]] = {}
        for phase in _LIFECYCLE_PHASES:
            phase_steps = [step for step in steps if step["phase"] == phase]
            failed_steps = [step for step in phase_steps if step["status"] == "failed"]
            summary[phase] = {
                "label": _LIFECYCLE_LABELS[phase],
                "status": self._status([step["status"] for step in phase_steps], empty="passed"),
                "total_steps": len(phase_steps),
                "passed_steps": len([step for step in phase_steps if step["status"] == "passed"]),
                "failed_steps": len(failed_steps),
            }
            for status in ("skipped", "cancelled", "incomplete"):
                count = sum(step["status"] == status for step in phase_steps)
                if count:
                    summary[phase][f"{status}_steps"] = count
        return summary

    @staticmethod
    def _logical_steps(steps):
        logical = {}
        for step in steps:
            source_metadata = (step.get("source_ref") or {}).get("metadata") or {}
            metadata = step.get("metadata") or {}
            if (metadata.get("hook_action_name") or source_metadata.get("hook_action_name")) == "runCase":
                continue
            key = (step.get("source_step_id") or step.get("step_id"), str(step.get("invocation_path") or metadata.get("invocation_path") or "root"))
            logical[key] = step
        return list(logical.values())

    @staticmethod
    def _status(statuses: list[str], empty: str = "inconclusive") -> str:
        if not statuses:
            return empty
        for status in ("cancelled", "failed", "incomplete"):
            if status in statuses:
                return "inconclusive" if status == "incomplete" else status
        return "passed" if "passed" in statuses and "skipped" not in statuses else "inconclusive"

    def _lifecycle_steps(self, steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
        lifecycle_steps: list[dict[str, Any]] = []
        for step in steps:
            lifecycle = self._step_lifecycle(step)
            if lifecycle is None:
                continue
            lifecycle_steps.append(
                {
                    "phase": lifecycle,
                    "phase_label": _LIFECYCLE_LABELS[lifecycle],
                    "source": self._step_source_label(step),
                    "action": self._step_action_label(step),
                    "step_id": step["step_id"],
                    "status": step["status"],
                    "failure_category": step.get("failure_category"),
                    "error_message": step.get("error_message"),
                }
            )
        return lifecycle_steps

    def _step_lifecycle(self, step: dict[str, Any]) -> str | None:
        source_metadata = self._source_metadata(step)
        step_metadata = step.get("metadata") or {}
        parent = source_metadata.get("parent_hook_action") or step_metadata.get("parent_hook_action")
        if isinstance(parent, dict) and parent.get("lifecycle_phase") in {"onCaseStart", "onCaseComplete"}:
            return parent["lifecycle_phase"]
        lifecycle = source_metadata.get("lifecycle_phase") or step_metadata.get("lifecycle_phase")
        return lifecycle if lifecycle in _LIFECYCLE_PHASES else None

    def _step_source_label(self, step: dict[str, Any]) -> str:
        source_ref = step.get("source_ref") or {}
        source_metadata = self._source_metadata(step)
        case_name = source_metadata.get("case_name")
        if isinstance(case_name, str) and case_name.strip():
            return case_name
        source_id = source_ref.get("source_id")
        if isinstance(source_id, str) and source_id.strip():
            return Path(source_id).name
        return "unknown"

    def _step_action_label(self, step: dict[str, Any]) -> str:
        source_metadata = self._source_metadata(step)
        step_metadata = step.get("metadata") or {}
        hook_action = source_metadata.get("hook_action_name") or step_metadata.get("hook_action_name")
        command = step_metadata.get("command") or self._phase_metadata_value(step, "command")
        if hook_action == "runShell":
            return f"runShell: {command}" if command else "runShell"
        if hook_action == "runCase":
            target = step_metadata.get("value") or step_metadata.get("target") or source_metadata.get("value") or source_metadata.get("target")
            return f"runCase: {target}" if target else "runCase"
        replay_alias = self._phase_metadata_value(step, "replay", nested_key="alias")
        if isinstance(replay_alias, str) and replay_alias.strip():
            return replay_alias
        capability_name = self._phase_metadata_value(step, "capability_name")
        if isinstance(capability_name, str) and capability_name.strip():
            return capability_name
        return "unknown"

    def _source_metadata(self, step: dict[str, Any]) -> dict[str, Any]:
        source_ref = step.get("source_ref") or {}
        metadata = source_ref.get("metadata") if isinstance(source_ref, dict) else None
        return metadata if isinstance(metadata, dict) else {}

    def _phase_metadata_value(self, step: dict[str, Any], key: str, *, nested_key: str | None = None) -> Any:
        for phase_report in step.get("phase_reports", []):
            metadata = phase_report.get("metadata") if isinstance(phase_report, dict) else None
            if not isinstance(metadata, dict) or key not in metadata:
                continue
            value = metadata[key]
            if nested_key is None:
                return value
            if isinstance(value, dict):
                return value.get(nested_key)
        return None

    def _escape_markdown_table(self, value: str) -> str:
        return value.replace("|", "\\|").replace("\n", " ")

    def _escape_backticks(self, value: str) -> str:
        return value.replace("`", "'")

    def _ai_assertions(self, report: dict[str, Any]) -> list[dict[str, Any]]:
        assertions: list[dict[str, Any]] = []
        for step in report["steps"]:
            for phase_report in step.get("phase_reports", []):
                metadata = phase_report.get("metadata") or {}
                harness_metadata = metadata.get("harness_metadata") or {}
                ai_assertion = harness_metadata.get("ai_assertion")
                if not isinstance(ai_assertion, dict):
                    continue
                artifact_paths = [artifact.get("path") for artifact in phase_report.get("artifact_refs", []) if artifact.get("path")]
                assertions.append(
                    {
                        "step_id": step.get("step_id"),
                        "phase": phase_report.get("phase"),
                        "prompt": harness_metadata.get("prompt"),
                        "artifact_paths": artifact_paths,
                        **ai_assertion,
                    }
                )
        return assertions
