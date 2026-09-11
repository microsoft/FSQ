# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import hashlib
import io
import json
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from urllib.parse import parse_qsl, unquote, urlencode, urlsplit, urlunsplit

import yaml
from PIL import Image

from fsq_agent.models import EvidenceBundle, PublicRunReport, ReportGenerationError, RunReportExportOptions, RunReportExportResult
from fsq_agent.report._comparison import compare_reports, normalize_snapshot, step_diffs

MAX_FACT_BYTES = 32 * 1024 * 1024
MAX_SNAPSHOT_BYTES = 512 * 1024
MAX_TEXT_BYTES = 8 * 1024 * 1024
MAX_IMAGE_BYTES = 16 * 1024 * 1024
MAX_IMAGE_PIXELS = 40_000_000
MAX_INLINE_IMAGE_BYTES = 64 * 1024 * 1024
MAX_BUNDLE_BYTES = 512 * 1024 * 1024
RESOURCE_POLICY = {
    "version": "fsq.report-limits/v1",
    "fact_bytes": MAX_FACT_BYTES,
    "snapshot_bytes": MAX_SNAPSHOT_BYTES,
    "text_bytes": MAX_TEXT_BYTES,
    "raster_bytes": MAX_IMAGE_BYTES,
    "raster_pixels": MAX_IMAGE_PIXELS,
    "inline_raster_bytes": MAX_INLINE_IMAGE_BYTES,
    "bundle_bytes": MAX_BUNDLE_BYTES,
}
FACT_FILES = (
    "run.json",
    "execution-result.json",
    "evidence-manifest.json",
    "evidence-events.jsonl",
    "report.json",
    "core-report.json",
    "report-fallback.json",
    "events.jsonl",
    "lineage.jsonl",
    "recording.json",
    "suggestion-processing.json",
)


def report_error(reason, message):
    return ReportGenerationError(message, context={"reason": reason})


def _safe_url(match):
    raw = match.group(0)
    try:
        parts = urlsplit(raw)
        host = parts.netloc.rsplit("@", 1)[-1]
        query = [(key, "[REDACTED]" if _sensitive_key(key) else value) for key, value in parse_qsl(parts.query, keep_blank_values=True)]
        return urlunsplit((parts.scheme, host, parts.path, urlencode(query), parts.fragment))
    except ValueError:
        return "[REDACTED_URL]"


def _sensitive_key(key):
    normalized = _normalized_credential_key(key)
    return normalized in {
        "authorization",
        "proxy_authorization",
        "set_cookie",
        "id_token",
        "client_secret",
        "cookie",
        "password",
        "passwd",
        "pwd",
        "secret",
        "token",
        "access_token",
        "refresh_token",
        "api_key",
        "apikey",
        "private_values",
        "private_value",
        "credentials",
        "env",
        "environment",
        "config",
    }


def safe_text(value, limit=4000):
    text = str(value if value is not None else "")
    if text.lstrip().startswith(("{", "[")):
        try:
            parsed = json.loads(text)
        except ValueError:
            pass
        else:
            safe = safe_structured(parsed)
            text = text if safe == parsed else json.dumps(safe, ensure_ascii=False)
    text = safe_display_text(text)
    return text if limit is None else text[:limit]


def safe_tree(value):
    if isinstance(value, Mapping):
        return {str(key): "[REDACTED]" if _sensitive_key(key) else safe_tree(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [safe_tree(item) for item in value]
    if isinstance(value, str):
        return safe_text(value, limit=None)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return safe_text(value)


def contained_file(root: Path, relative: str) -> Path:
    candidate = Path(relative)
    if candidate.is_absolute() or ".." in candidate.parts or chr(92) in relative:
        raise report_error("path_unsafe", "Artifact path is not contained.")
    path = root / candidate
    if any(part.is_symlink() for part in (path, *path.parents) if part != root.parent):
        raise report_error("path_unsafe", "Symlink artifacts are unavailable.")
    if not path.resolve().is_relative_to(root.resolve()):
        raise report_error("path_unsafe", "Artifact path escapes the Run.")
    return path


def digest_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _validate_raster(path, encoded_size):
    if encoded_size > MAX_IMAGE_BYTES:
        raise ValueError("encoded raster limit")
    with Image.open(io.BytesIO(path.read_bytes())) as raster:
        if raster.width * raster.height > MAX_IMAGE_PIXELS:
            raise ValueError("decoded raster limit")
        raster.verify()


def metric(value, *, measured=True, reason="unmeasured", unit="ms", scope="step"):
    known = measured and isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0
    return {"value": value if known else None, "unit": unit, "scope": scope, "availability": "measured" if known else "unavailable", "unavailable_reason": None if known else reason}


def _bind_fingerprint(fingerprints, name, digest):
    if name in fingerprints and fingerprints[name] != digest:
        raise report_error("source_changed", "Persisted report source changed after its first read.")
    fingerprints.setdefault(name, digest)


def _duration_measured(record):
    if (record.get("metadata") or {}).get("timing_measured") is True:
        return True
    if record.get("unavailable_reason"):
        return False
    return isinstance(record.get("duration_ms"), (int, float)) and record["duration_ms"] > 0


def enforce_report_budget(report, *, rendered_size=None):
    def size():
        return max(len(report.model_dump_json(indent=2).encode("utf-8")), rendered_size(report) if rendered_size else 0)

    if size() <= MAX_TEXT_BYTES:
        return
    graph = report.model_dump(mode="json")
    _omit_optional_graph_content(graph)
    report.artifacts = graph["artifacts"]
    report.logs = graph["logs"]
    report.run = graph["run"]
    report.comparison = graph["comparison"]
    report.lineage = graph["lineage"]
    report.warnings.append("Optional report text was omitted to satisfy the shared text budget.")
    if size() > MAX_TEXT_BYTES:
        raise report_error("resource_limit", "Required report facts exceed the shared text resource budget.")


def _omit_optional_graph_content(value):
    if isinstance(value, dict):
        if value.get("schema_version") == "fsq.report/v1":
            logs = value.get("logs", [])
            if logs:
                original_bytes = len(json.dumps(logs, ensure_ascii=False).encode("utf-8"))
                references = [
                    {"run_id": artifact["run_id"], "artifact_id": artifact["artifact_id"]}
                    for artifact in value.get("artifacts", [])
                    if artifact.get("artifact_id") in {"runtime-events", "evidence-journal", "evidence-manifest"} and artifact.get("path") and artifact.get("availability") == "available"
                ]
                budget = min(256 * 1024, MAX_TEXT_BYTES // 16)
                retained = []
                used = 0
                for log in reversed(logs):
                    length = len(json.dumps(log, ensure_ascii=False).encode("utf-8"))
                    if used + length > budget:
                        break
                    retained.append(log)
                    used += length
                value["logs"] = list(reversed(retained))
                value["run"].setdefault("display_omissions", {})["logs"] = {
                    "original_count": len(logs),
                    "returned_count": len(retained),
                    "original_size_bytes": original_bytes,
                    "reason": "aggregate_log_display_budget",
                    "full_artifacts": references,
                }
            for artifact in value.get("artifacts", []):
                if "content" in artifact or "normalized_content" in artifact:
                    artifact.pop("content", None)
                    artifact.pop("normalized_content", None)
                    artifact.update(truncated=True, unavailable_reason="report_text_budget")
            comparison = value.get("comparison", {})
            comparison["before_after"] = []
            if "baseline_current" in comparison:
                comparison["baseline_current"] = {"status": "incomplete", "reason": "report_text_budget", "baseline_run_id": comparison["baseline_current"].get("baseline_run_id")}
        for child in value.values():
            _omit_optional_graph_content(child)
    elif isinstance(value, list):
        for child in value:
            _omit_optional_graph_content(child)


class RunReportService:
    @staticmethod
    def read_fact_bytes(path: Path) -> bytes:
        """Read a resolved fact file under the shared projection resource limit."""
        if path.stat().st_size > MAX_FACT_BYTES:
            raise report_error("resource_limit", "Persisted facts exceed the resource limit.")
        with path.open("rb") as stream:
            content = stream.read(MAX_FACT_BYTES + 1)
        if len(content) > MAX_FACT_BYTES:
            raise report_error("resource_limit", "Persisted facts exceed the resource limit.")
        return content

    def project(
        self,
        run_dir: Path,
        facts: Mapping | None = None,
        normalized_evidence: EvidenceBundle | Mapping | None = None,
        *,
        baseline: PublicRunReport | None = None,
        related_runs: Sequence[PublicRunReport] = (),
    ) -> PublicRunReport:
        from pydantic import ValidationError

        from fsq_agent.models import RunExecutionResult, RunMetadata

        if len(related_runs) > 8:
            raise report_error("resource_limit", "Related Run limit exceeded.")
        root = Path(run_dir)
        if root.is_symlink() or not root.is_dir() or any(parent.is_symlink() for parent in root.absolute().parents):
            raise report_error("path_unsafe", "Run directory is unavailable or contains a symlink.")
        root = root.resolve()
        warnings = []
        fingerprints = {}
        persisted = self._load(root, "run.json", warnings, fingerprints)
        supplied = dict(facts or {})
        if normalized_evidence is None:
            normalized_evidence = supplied.pop("normalized_evidence", None)
        metadata = {**persisted, **supplied}
        if persisted.get("schema_version") in {"fsq.run/v1", "fsq.run/v2"}:
            try:
                checked = RunMetadata.model_validate(persisted).model_dump(mode="json")
            except ValidationError as exc:
                raise report_error("schema_invalid", "Required Run metadata is invalid.") from exc
            supplied_source = supplied.get("source") or {}
            if any(supplied.get(key, checked.get(key)) != checked.get(key) for key in ("run_id", "platform", "mode")) or any(
                checked.get("source", {}).get(key) != value for key, value in supplied_source.items()
            ):
                raise report_error("schema_invalid", "Supplied Run facts conflict with persisted identity.")
            metadata = {**checked, **{key: value for key, value in supplied.items() if key in {"status", "liveness", "persisted_status", "warnings"}}}
        run_id = metadata.get("run_id") or root.name
        if not isinstance(run_id, str) or not run_id or any(char in run_id for char in ("/", chr(92), chr(0))) or run_id in {".", ".."}:
            raise report_error("schema_invalid", "Run identity is invalid.")
        if persisted.get("run_id") and persisted["run_id"] != run_id:
            raise report_error("schema_invalid", "Run identity conflicts with persisted metadata.")
        frozen = self._load(root, "execution-result.json", warnings, fingerprints)
        if (root / "execution-result.json").is_file():
            try:
                frozen = RunExecutionResult.model_validate(frozen).model_dump(mode="json")
            except ValidationError as exc:
                raise report_error("schema_invalid", "Required execution result is invalid.") from exc
            if any(frozen.get(key) != metadata.get(key) for key in ("run_id", "platform", "mode")):
                raise report_error("schema_invalid", "Execution result identity conflicts with Run.")
        documents = {name: self._load(root, name, warnings, fingerprints) for name in ("core-report.json", "report.json", "report-fallback.json")}
        internal = next((document for document in documents.values() if document), {})
        source_outcomes = {}
        for name, document in documents.items():
            summary = document.get("summary") if isinstance(document.get("summary"), dict) else {}
            status = (document.get("verification") or {}).get("status") or summary.get("status") or document.get("status")
            if status:
                source_outcomes[name] = "success" if status == "passed" else status
        if metadata.get("status") in {"success", "failed", "error", "cancelled", "inconclusive"}:
            source_outcomes["run.json"] = metadata["status"]
        history_conflict = len(set(source_outcomes.values())) > 1
        if history_conflict:
            warnings.append("Historical report outcome conflict; all source outcomes were retained.")
        for document_name, document in documents.items():
            if document and document.get("run_id") not in {None, run_id}:
                raise report_error("schema_invalid", f"Historical report identity conflicts with Run in {document_name}.")
            scopes = [document, document.get("metadata") or {}]
            for scope in scopes:
                if not isinstance(scope, dict):
                    raise report_error("schema_invalid", "Historical report scope is invalid.")
                for key in ("platform", "mode"):
                    if scope.get(key) is not None and metadata.get(key) is not None and scope[key] != metadata[key]:
                        raise report_error("schema_invalid", f"Historical report {key} conflicts with selected scope.")
                workspace_value = scope.get("workspace")
                workspace_name = workspace_value.get("name") if isinstance(workspace_value, dict) else workspace_value
                expected_workspace = metadata.get("workspace") or {}
                expected_name = expected_workspace.get("name") if isinstance(expected_workspace, dict) else expected_workspace
                if workspace_name is not None and expected_name is not None and workspace_name != expected_name:
                    raise report_error("schema_invalid", "Historical report Workspace conflicts with selected scope.")
        events = self._events(root, warnings, fingerprints)
        bundle = normalized_evidence.model_dump(mode="json") if isinstance(normalized_evidence, EvidenceBundle) else dict(normalized_evidence or {})
        if bundle.get("schema_version") not in {None, "1.0", "fsq.evidence/v2"}:
            raise report_error("schema_invalid", "Unsupported evidence schema version.")
        if bundle.get("schema_version") == "fsq.evidence/v2":
            try:
                bundle = EvidenceBundle.model_validate(bundle).model_dump(mode="json")
            except ValidationError as exc:
                raise report_error("schema_invalid", "Normalized evidence contract is invalid.") from exc
        if not bundle:
            manifest = self._load(root, "evidence-manifest.json", warnings, fingerprints)
            if manifest.get("schema_version") not in {None, "1.0", "fsq.evidence/v2"}:
                raise report_error("schema_invalid", "Unsupported evidence schema version.")
            if manifest.get("schema_version") == "fsq.evidence/v2":
                warnings.append("Normalized v2 evidence was not supplied by the recovery authority.")
            else:
                bundle = manifest
        if bundle.get("run_id") not in {None, run_id}:
            raise report_error("schema_invalid", "Evidence identity conflicts with Run.")
        recovery = (bundle.get("metadata") or {}).get("recovery_snapshot") or {}
        for filename, expected in recovery.get("files", {}).items():
            path = contained_file(root, filename)
            actual = digest_file(path) if path.is_file() else None
            if actual != expected:
                raise report_error("source_changed", "Recovered evidence changed before report projection.")
            _bind_fingerprint(fingerprints, filename, expected)
        seen_events = {(event.get("event_id"), event.get("timestamp"), event.get("event_type"), event.get("step_id")) for event in events}
        if any(event.get("run_id") not in {None, run_id} for event in events):
            raise report_error("schema_invalid", "Historical event belongs to a different Run.")
        for event in bundle.get("events", []):
            if isinstance(event, dict):
                if event.get("run_id") not in {None, run_id}:
                    raise report_error("schema_invalid", "Evidence event belongs to a different Run.")
                identity = (event.get("event_id"), event.get("timestamp"), event.get("event_type"), event.get("step_id"))
                if identity not in seen_events:
                    events.append(
                        {
                            **event,
                            "_source_artifact_id": "evidence-journal" if (root / "evidence-events.jsonl").is_file() else "evidence-manifest" if (root / "evidence-manifest.json").is_file() else None,
                        }
                    )
                    seen_events.add(identity)
        raw_steps = [item for item in bundle.get("steps", []) if isinstance(item, dict) and ("phase_reports" in item or "action_status" in item)]
        if not raw_steps:
            raw_steps = [
                item for item in frozen.get("steps", internal.get("steps", [])) if isinstance(item, dict) and ("phase_reports" in item or item.get("step_execution_id") or item.get("action_name"))
            ]
        artifacts = list(bundle.get("artifacts", []))
        if bundle.get("schema_version") == "1.0":
            artifacts.extend((bundle.get("metadata") or {}).get("legacy_artifacts", []))
        tool_calls = []
        by_id = {str(item.get("step_execution_id") or item.get("step_id")): item for item in raw_steps}
        duplicate_ids = len(by_id) != len(raw_steps)
        if duplicate_ids:
            warnings.append("Historical step identity is ambiguous; duplicate records were retained.")
        for event in events:
            payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
            result = payload.get("runner_result")
            if isinstance(result, dict):
                if result.get("run_id") not in {None, run_id}:
                    raise report_error("schema_invalid", "Nested execution result belongs to a different Run.")
                item = dict(result)
                item["transport_status"] = "completed" if event.get("type") == "tool_call_completed" else "failed"
                item["tool_call_id"] = event.get("tool_call_id")
                identity = str(item.get("step_execution_id") or item.get("step_id"))
                if identity not in by_id:
                    by_id[identity] = item
                    raw_steps.append(item)
                else:
                    by_id[identity]["transport_status"] = item["transport_status"]
                    by_id[identity]["tool_call_id"] = item["tool_call_id"]
            artifacts.extend(item for item in payload.get("artifact_refs", []) if isinstance(item, dict))
            if event.get("type") in {"tool_call_completed", "tool_call_failed"}:
                tool_calls.append(
                    {
                        "run_id": run_id,
                        "tool_call_id": event.get("tool_call_id"),
                        "tool_name": event.get("tool_name"),
                        "tool_origin": payload.get("tool_origin"),
                        "transport_status": "completed" if event.get("type") == "tool_call_completed" else "failed",
                        "execution_status": payload.get("status"),
                        "duration_ms": event.get("duration_ms"),
                        "error_message": safe_text(payload.get("error_message")),
                    }
                )
        if not tool_calls:
            tool_calls.extend(
                {"run_id": run_id, **safe_tree(call), "transport_status": call.get("transport_status") or call.get("status"), "execution_status": call.get("execution_status")}
                for call in (internal.get("execution") or {}).get("tool_calls", [])
                if isinstance(call, dict) and isinstance(call.get("tool_name"), str)
            )
        for step in raw_steps:
            for phase in step.get("phase_reports", []):
                artifacts.extend(item for item in phase.get("artifact_refs", []) if isinstance(item, dict))
        for filename, artifact_id in (("events.jsonl", "runtime-events"), ("evidence-events.jsonl", "evidence-journal"), ("evidence-manifest.json", "evidence-manifest")):
            if (root / filename).is_file():
                artifacts.append({"artifact_id": artifact_id, "kind": "log", "path": filename, "mime_type": "application/json" if filename.endswith(".json") else "application/x-ndjson"})
        for filename, document in documents.items():
            if document.get("failure_classification"):
                artifacts.append({"artifact_id": f"internal-{filename.replace('.', '-')}", "kind": "json", "path": filename, "mime_type": "application/json"})
        source_metadata = metadata.get("source") or {}
        source_path = source_metadata.get("snapshot_path")
        if source_path:
            artifacts.append(
                {
                    "artifact_id": "source-snapshot",
                    "kind": "text",
                    "path": source_path,
                    "sha256": source_metadata.get("digest"),
                    "mime_type": "text/plain",
                    "metadata": {"source_kind": source_metadata.get("kind")},
                }
            )
        for index, entry in enumerate((metadata.get("provenance") or {}).get("sources", [])):
            if isinstance(entry, dict) and entry.get("path") != source_path:
                artifacts.append(
                    {"artifact_id": f"source-{index}", "kind": "text", "path": entry.get("path"), "sha256": entry.get("sha256"), "metadata": {"source_kind": entry.get("kind") or entry.get("label")}}
                )
        for label, relative in (metadata.get("artifacts") or {}).items():
            if label in {"candidate_case", "suggestions"} and isinstance(relative, str):
                artifacts.append({"artifact_id": label, "kind": "text", "path": relative})
        processing = dict(metadata.get("processing") or {})
        later_processing = self._load(root, "suggestion-processing.json", warnings, fingerprints)
        suggestions = self._load(root, "case-suggestions.json", warnings, fingerprints)
        if suggestions and not any(item.get("path") == "case-suggestions.json" for item in artifacts):
            artifacts.append({"artifact_id": "case-suggestions", "kind": "text", "path": "case-suggestions.json"})
        suggested_candidate = suggestions.get("candidate_case_path")
        if suggestions.get("candidate_case_status") == "available" and isinstance(suggested_candidate, str):
            artifacts.append({"artifact_id": "suggested-candidate-case", "kind": "text", "path": suggested_candidate, "metadata": {"source_kind": "case"}})
        for name, item in later_processing.items():
            if name in {"suggestion", "recording", "report", "publication"} and isinstance(item, dict) and item.get("status") in {"not_requested", "pending", "success", "failed", "cancelled"}:
                processing[name] = item
                artifacts.extend(
                    {"artifact_id": f"{name}-{field}", "kind": "text", "path": item[field]} for field in ("path", "candidate_case_path", "candidate_path") if isinstance(item.get(field), str)
                )
        warnings.extend(str(item) for item in bundle.get("warnings", []))
        steps = [self._step(run_id, step, index) for index, step in enumerate(raw_steps)]
        projected_artifacts = self._artifacts(root, run_id, artifacts, warnings, fingerprints)
        counts = self._counts(steps)
        if not steps and (frozen or bundle.get("schema_version") == "fsq.evidence/v2"):
            counts = {**dict.fromkeys(("total", "passed", "failed", "skipped", "cancelled", "incomplete", "attempt_count"), 0), "scope": "logical_leaf_invocations"}
        execution = dict(frozen.get("execution") or {})
        summary_value = internal.get("summary")
        internal_summary = summary_value if isinstance(summary_value, dict) else {}
        outcome = (
            execution.get("outcome")
            or frozen.get("outcome")
            or internal.get("verification", {}).get("status")
            or metadata.get("status")
            or internal_summary.get("status")
            or internal.get("status")
            or "inconclusive"
        )
        outcome = {"passed": "success"}.get(outcome, outcome)
        verification = dict(frozen.get("verification") or internal.get("verification") or {"status": "not_requested" if metadata.get("mode") == "strict" else "unknown"})
        logical_steps = {}
        for step in steps:
            if not step["container"]:
                logical_steps[(step.get("source_step_id") or step["step_id"], str(step.get("invocation_path")))] = step
        first_failure = next((step for step in logical_steps.values() if step["status"] == "failed"), None)
        if frozen.get("primary_failure"):
            primary = frozen["primary_failure"]
            primary_id = primary.get("step_execution_id") or primary.get("step_id")
            matches = [step for step in steps if primary_id in {step.get("step_execution_id"), step.get("step_id")}]
            primary_step = matches[0] if len(matches) == 1 else {}
            if len(matches) != 1:
                warnings.append("Frozen primary failure has unavailable or ambiguous execution detail.")
            first_failure = {
                **primary_step,
                "run_id": run_id,
                "step_execution_id": primary_id,
                "failure_category": primary.get("failure_category") or primary.get("category"),
                "error_message": primary.get("error_message") or primary.get("message"),
                "provenance": "recorded_fact",
            }
        authoritative_counts = frozen.get("counts")
        observed_attempt_count = counts.get("attempt_count")
        if not authoritative_counts and not steps:
            available_summaries = []
            for document in documents.values():
                summary = document.get("summary") if isinstance(document.get("summary"), dict) else {}
                total = summary.get("step_count", summary.get("total_steps"))
                if isinstance(total, int) and not isinstance(total, bool) and total >= 0:
                    quantities = {
                        "total": total,
                        "passed": summary.get("passed_steps"),
                        "failed": summary.get("failed_steps"),
                        "skipped": summary.get("skipped_steps", 0),
                        "cancelled": summary.get("cancelled_steps", 0),
                        "incomplete": summary.get("incomplete_steps", 0),
                        "attempt_count": None,
                        "scope": "historical_summary",
                        "unavailable_reason": "historical_attempts_unavailable",
                    }
                    if (
                        all(isinstance(quantities[key], int) and quantities[key] >= 0 for key in ("passed", "failed", "skipped", "cancelled", "incomplete"))
                        and sum(quantities[key] for key in ("passed", "failed", "skipped", "cancelled", "incomplete")) == total
                    ):
                        available_summaries.append(quantities)
            if available_summaries and all(item == available_summaries[0] for item in available_summaries):
                counts = available_summaries[0]
            elif available_summaries:
                warnings.append("Historical count summaries conflict; quantities remain unavailable.")
        accounting_complete = True
        if frozen and bundle.get("metadata", {}).get("recovery_snapshot"):
            fields = ("step_execution_id", "source_step_id", "invocation_path", "attempt_index", "status", "action_status", "failure_category")

            def identity_outcomes(values):
                return sorted(json.dumps({key: value.get(key, 1 if key == "attempt_index" else [] if key == "invocation_path" else None) for key in fields}, sort_keys=True) for value in values)

            if identity_outcomes(frozen.get("steps", [])) != identity_outcomes(raw_steps):
                warnings.append("Frozen execution identities or outcomes conflict with recovered evidence.")
                accounting_complete = False
        if duplicate_ids:
            accounting_complete = False
        if isinstance(authoritative_counts, dict):
            if any(authoritative_counts.get(key) != counts.get(key) for key in ("total", "passed", "failed", "skipped", "cancelled", "incomplete", "attempt_count")):
                warnings.append("Frozen result counts conflict with available action accounting.")
                accounting_complete = False
            counts = {**authoritative_counts, "scope": "logical_leaf_invocations"}
        execution.update(
            outcome=outcome,
            summary=safe_text(
                frozen.get("summary") or metadata.get("result", {}).get("summary") or verification.get("summary") or (summary_value if isinstance(summary_value, str) else ""), limit=None
            ),
            counts=counts,
            first_failure=first_failure,
            authoritative_source="execution-result.json" if frozen else "historical_projection",
            trustworthy=bool(frozen or (outcome in {"success", "failed", "cancelled", "error", "inconclusive"} and (steps or internal))),
        )
        execution["accounting_complete"] = accounting_complete
        execution["observed_attempt_count"] = observed_attempt_count
        execution["source_outcomes"] = source_outcomes
        execution["history_conflict"] = history_conflict and not frozen
        execution["recovered_failures"] = []
        if outcome == "success":
            execution["recovered_failures"] = [step for step in steps if step["status"] == "failed"]
            execution["first_failure"] = None
        execution["interpretations"] = self._interpretations(run_id, execution, documents, suggestions, projected_artifacts)
        if isinstance(execution.get("first_failure"), dict):
            execution["first_failure"].update(provenance="recorded_fact", references=[{"run_id": run_id, "step_execution_id": execution["first_failure"].get("step_execution_id")}])
        evidence = self._coverage(steps, projected_artifacts, frozen.get("evidence") or metadata.get("evidence") or {}, bundle, warnings)
        blocking_steps = list(logical_steps.values()) if outcome != "success" else []
        execution["infrastructure_failures"] = [
            {"step_execution_id": step.get("step_execution_id"), "failure_category": step.get("failure_category")}
            for step in blocking_steps
            if step.get("status") in {"failed", "cancelled"}
            and step.get("failure_category")
            in {"configuration_error", "context_error", "harness_error", "provider_error", "artifact_error", "environment_error", "infrastructure_error", "session_error"}
        ]
        gate = self._gate(execution, verification, evidence, metadata, bool(frozen))
        logs = [self._log(event, run_id) for event in events]
        if logs and all(isinstance(event.get("sequence"), int) for event in logs):
            sequences = [event["sequence"] for event in logs]
            if sequences != sorted(sequences) or len(sequences) != len(set(sequences)):
                warnings.append("Log sequence order or duplicate sequence was normalized for display.")
            logs.sort(key=lambda event: event["sequence"])
        elif logs:
            warnings.append("Logs lack a shared sequence; persisted source order was preserved.")
        availability = metadata.get("availability") or {}
        for field, reason in availability.items():
            if reason == "conflicting_sources":
                warnings.append(f"Historical {field} conflicts between persisted sources; displayed as unknown.")
        source = safe_tree(metadata.get("source") or ({} if availability.get("source") == "conflicting_sources" else internal.get("task")) or {})
        if availability.get("source") == "conflicting_sources":
            source["unavailable_reason"] = "conflicting_sources"
        if isinstance(metadata.get("provenance"), dict):
            source.setdefault("provenance", safe_tree(metadata["provenance"]))
        usage = [{"scope": "dynamic_agent_main", **safe_tree(event.get("payload", {}))} for event in events if event.get("type") == "dynamic_agent_token_usage"]
        lineage_value = metadata.get("lineage", {})
        lineage = dict(lineage_value) if isinstance(lineage_value, dict) else {"relations": list(lineage_value or [])}
        lineage.setdefault("relations", []).extend(self._events(root, warnings, fingerprints, name="lineage.jsonl"))
        saved_sources = [
            {
                "artifact_id": "saved-case-" + str(relation.get("case_digest")),
                "kind": "text",
                "path": relation["saved_snapshot_path"],
                "sha256": relation.get("case_digest"),
                "metadata": {"source_kind": "case"},
            }
            for relation in lineage["relations"]
            if isinstance(relation, dict) and relation.get("kind") == "recording" and relation.get("saved_snapshot_path")
        ]
        projected_artifacts.extend(self._artifacts(root, run_id, saved_sources, warnings, fingerprints))
        report = PublicRunReport(
            run={
                "run_id": run_id,
                "platform": metadata.get("platform"),
                "mode": metadata.get("mode"),
                "status": metadata.get("status", outcome),
                "started_at": metadata.get("started_at"),
                "completed_at": metadata.get("completed_at"),
                "duration_ms": metadata.get("duration_ms"),
                "duration_unavailable_reason": metadata.get("duration_unavailable_reason"),
                "runtime": safe_tree(metadata.get("runtime", {})),
                "gate": gate,
                "resource_policy": RESOURCE_POLICY,
                "availability": safe_tree(availability),
                "snapshot": {
                    "checkpoint_sequence": recovery.get("checkpoint_sequence", bundle.get("checkpoint_sequence")),
                    "journal_sequence": recovery.get("journal_sequence"),
                    "files": dict(fingerprints),
                },
            },
            source=source,
            execution=safe_tree(execution),
            verification=safe_tree(verification),
            evidence=evidence,
            processing=safe_tree(processing),
            steps=steps,
            tool_calls=safe_tree(tool_calls),
            logs=logs,
            artifacts=projected_artifacts,
            metrics={
                "run_duration": metric(
                    metadata.get("duration_ms"), measured=not bool(metadata.get("duration_unavailable_reason")), reason=metadata.get("duration_unavailable_reason") or "unmeasured", scope="run"
                ),
                "usage": usage,
            },
            lineage=safe_tree(lineage),
            warnings=list(dict.fromkeys(warnings)),
        )
        report.comparison = {"before_after": step_diffs(report)}
        report._run_dirs = {run_id: root}
        for name in FACT_FILES:
            path = root / name
            fingerprints.setdefault(name, digest_file(path) if path.is_file() and not path.is_symlink() else None)
        report._fingerprints = {run_id: fingerprints}
        report.run["snapshot"]["files"] = dict(fingerprints)
        if source_path:
            source_artifact = next((item for item in report.artifacts if item["artifact_id"] == "source-snapshot"), None)
            if source_artifact and source_artifact.get("availability") != "available":
                report.run["gate"] = {"status": "error", "reasons": ["source_identity_unavailable"]}
        if len(related_runs) > 8:
            raise report_error("resource_limit", "Related Run limit exceeded.")
        if baseline is not None:
            report.comparison["baseline_current"] = self.compare(report, baseline)
            self._include(report, baseline)
            report.comparison["baseline_report"] = baseline.model_dump(mode="json")
        related = []
        for other in related_runs:
            self._validate_related(report, other)
            self._include(report, other)
            related.append(other.model_dump(mode="json"))
        if related:
            report.lineage["related_runs"] = related
        for filename, expected in fingerprints.items():
            path = contained_file(root, filename)
            if (digest_file(path) if path.is_file() else None) != expected:
                raise report_error("source_changed", "Report facts changed during projection.")
        enforce_report_budget(report)
        return report

    def compare(self, current: PublicRunReport, baseline: PublicRunReport) -> dict:
        return compare_reports(current, baseline)

    def validate_export(self, report: PublicRunReport, options: RunReportExportOptions) -> None:
        from fsq_agent.report._export import prepare_export

        prepare_export(report, options)

    def export(self, report: PublicRunReport, options: RunReportExportOptions) -> RunReportExportResult:
        from fsq_agent.report._export import export_report

        return export_report(report, options)

    def _load(self, root, name, warnings, fingerprints):
        path = contained_file(root, name)
        if not path.exists():
            _bind_fingerprint(fingerprints, name, None)
            return {}
        if path.stat().st_size > MAX_FACT_BYTES:
            raise report_error("resource_limit", "Required report facts exceed the resource limit.")
        try:
            content = self.read_fact_bytes(path)
        except OSError as exc:
            raise report_error("artifact_unavailable", "Persisted report facts are unreadable.") from exc
        _bind_fingerprint(fingerprints, name, hashlib.sha256(content).hexdigest())
        try:
            value = json.loads(content)
        except (ValueError, UnicodeError):
            if name in {"run.json", "execution-result.json"}:
                raise report_error("schema_invalid", f"Required {name} document is invalid.") from None
            warnings.append(f"Invalid persisted document: {name}.")
            return {}
        schema = value.get("schema_version") if isinstance(value, dict) else None
        if not isinstance(value, dict):
            raise report_error("schema_invalid", f"Persisted {name} must contain an object.")
        for key in ("verification", "execution", "metadata", "source", "task", "result", "provenance"):
            if value.get(key) is not None and not isinstance(value[key], dict):
                raise report_error("schema_invalid", f"Persisted {name} has an invalid {key} section.")
        for section in (value, value.get("verification") or {}, value.get("summary") if isinstance(value.get("summary"), dict) else {}):
            if section.get("status") is not None and not isinstance(section["status"], str):
                raise report_error("schema_invalid", f"Persisted {name} has an invalid status.")
        for key in ("steps", "events", *(("artifacts",) if name != "run.json" else ())):
            if key in value and (not isinstance(value[key], list) or any(not isinstance(item, dict) for item in value[key])):
                raise report_error("schema_invalid", f"Persisted {name} has invalid {key}.")
        if name == "run.json" and schema not in {None, "fsq.run/v1", "fsq.run/v2"}:
            raise report_error("schema_invalid", "Unsupported Run metadata schema.")
        if name == "execution-result.json" and schema not in {None, "fsq.execution-result/v1"}:
            raise report_error("schema_invalid", "Unsupported execution result schema.")
        return value if isinstance(value, dict) else {}

    def _events(self, root, warnings, fingerprints, name="events.jsonl"):
        path = contained_file(root, name)
        if not path.is_file():
            return []
        if path.stat().st_size > MAX_FACT_BYTES:
            warnings.append("Historical event log exceeds display limit.")
            return []
        content = self.read_fact_bytes(path)
        _bind_fingerprint(fingerprints, name, hashlib.sha256(content).hexdigest())
        events = []
        for index, line in enumerate(content.decode("utf-8", errors="replace").splitlines()):
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except ValueError:
                warnings.append(f"Invalid historical event record at line {index + 1}.")
                continue
            if isinstance(item, dict):
                events.append({**item, "_source_artifact_id": "runtime-events" if name == "events.jsonl" else None})
            else:
                warnings.append(f"Non-object historical event at line {index + 1} was unavailable.")
        return events

    def _step(self, run_id, raw, index):
        source = raw.get("source_ref") or {}
        metadata = raw.get("metadata") or {}
        phases = raw.get("phase_reports") or []
        invoke = next((phase for phase in phases if phase.get("phase") == "invoke"), {})
        invoke_metadata = invoke.get("metadata") or {}
        display_identity = str(raw.get("step_id") or f"unaccounted-{index}")
        planned = "step_execution_id" in raw and raw.get("step_execution_id") is None and raw.get("status") in {"skipped", "incomplete"}
        identity = None if planned else str(raw.get("step_execution_id") or display_identity)
        status = raw.get("action_status") or raw.get("status") or "incomplete"
        status = "passed" if status == "success" else status
        if status not in {"passed", "failed", "skipped", "cancelled", "incomplete"}:
            status = "incomplete"
        timing = {"duration_ms": metric(raw.get("duration_ms"), measured=_duration_measured(raw), reason=raw.get("unavailable_reason") or "unmeasured")}
        for name in ("prepare", "invoke", "settle", "finalize"):
            phase = next((item for item in phases if item.get("phase") == name), {})
            measured = _duration_measured(phase)
            timing[f"{name}_ms"] = metric(phase.get("duration_ms"), measured=measured, reason=phase.get("unavailable_reason") or "unmeasured")
        source_id = raw.get("source_step_id")
        if source_id is None and source.get("source_id") is not None and source.get("step_index") is not None:
            source_id = f"{Path(str(source['source_id'])).name}:{source['step_index']}"
        params = invoke_metadata.get("safe_replay_params") or {}
        output = invoke_metadata.get("harness_output") or {}
        assertion_details = None
        expected = params.get("text") if isinstance(params, dict) else None
        if isinstance(expected, dict) and isinstance(output, dict) and "text" in output:
            assertion_details = {"expected": safe_tree(expected), "observed": safe_tree(output["text"]), "provenance": "recorded_fact", "run_id": run_id, "step_execution_id": identity}
        parent = metadata.get("parent_hook_action") or (source.get("metadata") or {}).get("parent_hook_action") or {}
        assertion = safe_tree((invoke_metadata.get("harness_metadata") or {}).get("ai_assertion"))
        if isinstance(assertion, dict):
            assertion["prompt"] = safe_text((invoke_metadata.get("harness_metadata") or {}).get("prompt"), limit=None)
        return {
            "run_id": run_id,
            "step_id": display_identity,
            "step_execution_id": identity,
            "source_step_id": source_id,
            "source_index": source.get("step_index"),
            "invocation_path": raw.get("invocation_path") or metadata.get("invocation_path") or [],
            "root_invocation": metadata.get("root_invocation") if isinstance(metadata.get("root_invocation"), bool) else raw.get("invocation_path") == ["root"],
            "action_name": raw.get("action_name") or invoke_metadata.get("capability_name"),
            "authored_action_name": metadata.get("authored_action_name") or (invoke_metadata.get("replay") or {}).get("alias"),
            "kind": raw.get("kind") or invoke_metadata.get("step_kind"),
            "status": status,
            "aggregate_status": raw.get("status") if raw.get("status") in {"passed", "failed", "skipped", "cancelled", "incomplete"} else "incomplete",
            "transport_status": raw.get("transport_status"),
            "tool_call_id": raw.get("tool_call_id") or metadata.get("tool_call_id"),
            "evidence_refs": [{"run_id": run_id, "artifact_id": item.get("artifact_id")} for phase in phases for item in phase.get("artifact_refs", []) if item.get("artifact_id")],
            "failure_category": raw.get("failure_category"),
            "error_message": safe_text(raw.get("error_message")),
            "evidence_errors": safe_tree(raw.get("evidence_errors", [])),
            "lifecycle_phase": parent.get("lifecycle_phase") or (source.get("metadata") or {}).get("lifecycle_phase", metadata.get("lifecycle_phase", "case")),
            "attempt_index": raw.get("attempt_index", 1),
            "attempted": bool(raw.get("step_execution_id") or status not in {"skipped", "incomplete"}),
            "skip_reason": safe_text(raw.get("skip_reason")),
            "blocked_by_step": raw.get("blocked_by_step"),
            "started_at": raw.get("started_at"),
            "ended_at": raw.get("ended_at"),
            "metrics": timing,
            "evidence_policy": metadata.get("evidence_policy") or {},
            "required_evidence": metadata.get("required_evidence") or [],
            "container": bool((source.get("metadata") or {}).get("hook_action_name") == "runCase" or metadata.get("hook_action_name") == "runCase"),
            "assertion": assertion,
            "assertion_details": assertion_details,
        }

    def _counts(self, steps):
        if not steps:
            return {
                **dict.fromkeys(("total", "passed", "failed", "skipped", "cancelled", "incomplete", "attempt_count")),
                "scope": "logical_leaf_invocations",
                "unavailable_reason": "historical_accounting_unavailable",
            }
        leaves = [step for step in steps if not step["container"]]
        logical = {}
        for step in leaves:
            key = (step.get("source_step_id") or step["step_execution_id"] or step["step_id"], str(step.get("invocation_path")))
            logical[key] = step
        counts = {status: sum((step.get("aggregate_status") or step["status"]) == status for step in logical.values()) for status in ("passed", "failed", "skipped", "cancelled", "incomplete")}
        counts.update(total=len(logical), attempt_count=sum(step["attempted"] for step in leaves), scope="logical_leaf_invocations")
        return counts

    def _artifacts(self, root, run_id, raw_artifacts, warnings, fingerprints):
        values = {}
        text_bytes = 0
        for index, item in enumerate(raw_artifacts):
            if not isinstance(item, dict):
                continue
            artifact_id = str(item.get("artifact_id") or f"artifact-{index}")
            if artifact_id in values:
                previous = values[artifact_id]
                identity_fields = {
                    "path": item.get("path"),
                    "kind": item.get("kind", "other"),
                    "step_execution_id": item.get("step_execution_id") or item.get("step_id"),
                    "phase": item.get("phase"),
                    "capture_occurrence": item.get("capture_occurrence"),
                    "sha256": item.get("sha256"),
                    "size_bytes": item.get("size_bytes"),
                    "availability": item.get("availability", "available"),
                }
                item_metadata = item.get("metadata") or {}
                identity_fields.update(
                    requested_kind=item_metadata.get("requested_kind") or item_metadata.get("requested_artifact_kind"),
                    capture_reason=item.get("capture_reason") or item_metadata.get("capture_reason") or item_metadata.get("reason"),
                    attempt_index=item.get("attempt_index") or item_metadata.get("attempt_index"),
                )
                if any(value is not None and previous.get(key) != value for key, value in identity_fields.items()):
                    warnings.append(f"Conflicting artifact identity: {artifact_id}.")
                    values[artifact_id].update(availability="unavailable", unavailable_reason="identity_conflict", path=None)
                continue
            relative = item.get("path")
            artifact_metadata = item.get("metadata") or {}
            artifact = {
                "run_id": run_id,
                "artifact_id": artifact_id,
                "kind": item.get("kind", "other"),
                "requested_kind": artifact_metadata.get("requested_kind") or artifact_metadata.get("requested_artifact_kind") or item.get("kind", "other"),
                "source_kind": item.get("source_kind") or artifact_metadata.get("source_kind"),
                "path": relative,
                "step_execution_id": item.get("step_execution_id") or item.get("step_id"),
                "phase": item.get("phase"),
                "capture_occurrence": item.get("capture_occurrence"),
                "attempt_index": item.get("attempt_index") or artifact_metadata.get("attempt_index"),
                "capture_reason": item.get("capture_reason") or artifact_metadata.get("capture_reason") or artifact_metadata.get("reason"),
                "capture_duration": metric(artifact_metadata.get("capture_duration_ms"), measured=bool(artifact_metadata.get("timing_measured")), scope="capture"),
                "mime_type": item.get("mime_type"),
                "availability": item.get("availability", "available"),
                "unavailable_reason": item.get("unavailable_reason"),
                "size_bytes": item.get("size_bytes"),
                "sha256": item.get("sha256"),
                "truncated": bool(item.get("truncated") or artifact_metadata.get("truncated")),
                "transformed": bool(item.get("transformed") or artifact_metadata.get("transformed") or artifact_metadata.get("redacted")),
                "redacted": bool(item.get("redacted") or artifact_metadata.get("redacted")),
                "coverage": safe_tree(artifact_metadata.get("coverage") or artifact_metadata.get("snapshot_coverage") or {}),
                "compaction": safe_tree(artifact_metadata.get("compaction") or {}),
            }
            if not isinstance(relative, str):
                artifact["path"] = None
                if artifact["availability"] == "available":
                    artifact.update(availability="unavailable", unavailable_reason=artifact["unavailable_reason"] or "no_artifact_path")
            else:
                try:
                    path = contained_file(root, relative)
                    if not path.is_file():
                        artifact.update(availability="missing", unavailable_reason="artifact_missing")
                    else:
                        digest = digest_file(path)
                        _bind_fingerprint(fingerprints, relative, digest)
                        actual_size = path.stat().st_size
                        if artifact["size_bytes"] is not None and artifact["size_bytes"] != actual_size:
                            artifact.update(availability="unavailable", unavailable_reason="size_mismatch")
                        elif artifact["sha256"] and artifact["sha256"] != digest:
                            artifact.update(availability="unavailable", unavailable_reason="hash_mismatch")
                        else:
                            artifact["size_bytes"] = actual_size
                            artifact["sha256"] = digest
                            if artifact["kind"] == "screenshot":
                                try:
                                    _validate_raster(path, artifact["size_bytes"])
                                    artifact["display_availability"] = "available"
                                except SyntaxError:
                                    artifact.update(display_availability="omitted", unavailable_reason="raster_invalid")
                                except (OSError, ValueError, Image.DecompressionBombError):
                                    artifact.update(display_availability="omitted", unavailable_reason="raster_resource_limit")
                            if artifact["kind"] in {"ui_snapshot", "ui_tree", "text"}:
                                limit = min(MAX_SNAPSHOT_BYTES, max(0, MAX_TEXT_BYTES - text_bytes))
                                is_case = artifact.get("source_kind") == "case" or path.suffix.casefold() in {".yaml", ".yml"}
                                structured = is_case or path.suffix.casefold() in {".json", ".jsonl"}
                                if structured and artifact["size_bytes"] > MAX_FACT_BYTES:
                                    artifact.update(display_availability="omitted", unavailable_reason="source_sanitization_resource_limit")
                                    values[artifact_id] = artifact
                                    continue
                                with path.open("rb") as stream:
                                    content = stream.read(MAX_FACT_BYTES if structured else limit)
                                text_bytes += len(content)
                                raw_text = content.decode("utf-8", errors="replace")
                                format_kind = "yaml" if is_case else "jsonl" if path.suffix.casefold() == ".jsonl" else "json" if path.suffix.casefold() == ".json" else "text"
                                safe_content = sanitize_content(raw_text, format_kind=format_kind)
                                artifact["content"] = safe_content.encode("utf-8")[:limit].decode("utf-8", errors="replace")
                                if safe_content != raw_text:
                                    artifact.update(
                                        transformed=True,
                                        display_transformed=True,
                                        derived_sha256=hashlib.sha256(safe_content.encode("utf-8")).hexdigest(),
                                        derived_size_bytes=len(safe_content.encode("utf-8")),
                                    )
                                if artifact["kind"] in {"ui_snapshot", "ui_tree"}:
                                    try:
                                        snapshot_value = json.loads(raw_text)
                                    except ValueError:
                                        snapshot_value = None
                                    if isinstance(snapshot_value, dict):
                                        for key in ("coverage", "compaction"):
                                            if not artifact.get(key) and isinstance(snapshot_value.get(key), dict):
                                                artifact[key] = safe_tree(snapshot_value[key])
                                    artifact["normalized_content"] = normalize_snapshot(artifact["content"])
                                if artifact["size_bytes"] > limit:
                                    artifact.update(truncated=True, unavailable_reason="display_limit")
                except ReportGenerationError as exc:
                    if exc.context.get("reason") == "source_changed":
                        raise
                    artifact.update(path=None, availability="unavailable", unavailable_reason="unsafe_or_unreadable_artifact")
                except OSError:
                    artifact.update(path=None, availability="unavailable", unavailable_reason="unsafe_or_unreadable_artifact")
            if artifact["kind"] in {"ui_snapshot", "ui_tree"} and not artifact.get("coverage"):
                artifact["coverage"] = {"status": "unknown", "reason": "coverage_not_recorded"}
            values[artifact_id] = artifact
        return list(values.values())

    def _coverage(self, steps, artifacts, recorded, bundle, warnings):
        missing = list(recorded.get("required_missing") or [])
        for step in steps:
            required = list(step.get("required_evidence") or [])
            policy = step.get("evidence_policy") or {}
            for phase, key in (("prepare", "capture_before"), ("finalize", "capture_after")):
                if policy.get(key):
                    required.extend({"kind": kind, "phase": phase} for kind in policy.get("artifact_kinds", ("screenshot", "ui_snapshot")))
            for expected in required:
                found = any(
                    item.get("step_execution_id") == step["step_execution_id"]
                    and (item.get("requested_kind") or item.get("kind")) == expected.get("kind")
                    and item.get("phase") == expected.get("phase")
                    and (item.get("availability") == "available" or (item.get("availability") == "not_applicable" and item.get("unavailable_reason")))
                    for item in artifacts
                )
                if not found:
                    missing.append({"run_id": step["run_id"], "step_execution_id": step["step_execution_id"], **expected})
        errors = [error for step in steps for error in step.get("evidence_errors", [])]
        unknown_legacy = bundle.get("schema_version") != "fsq.evidence/v2" and (bundle.get("metadata") or {}).get("legacy_coverage_unknown", False)
        incomplete = (not unknown_legacy and bundle.get("completeness") in {"partial", "unavailable"}) or any(item.get("unavailable_reason") == "identity_conflict" for item in artifacts)
        status = "partial" if missing or errors or incomplete else recorded.get("status") or ("complete" if bundle and not unknown_legacy else "unavailable")
        return {
            "status": status,
            "required_missing": missing,
            "errors": [*recorded.get("errors", []), *errors],
            "artifact_count": len(artifacts),
            "availability": "known" if recorded or (bundle and not unknown_legacy) else "unknown",
            "warnings": list(warnings),
        }

    def _gate(self, execution, verification, evidence, metadata, frozen):
        outcome = execution["outcome"]
        reasons = []
        status = "passed"
        failure = execution.get("first_failure") or {}
        category = failure.get("failure_category") or failure.get("category")
        infrastructure = category in {"configuration_error", "context_error", "harness_error", "provider_error", "artifact_error", "environment_error", "infrastructure_error", "session_error"}
        if infrastructure or execution.get("infrastructure_failures"):
            status, reasons = "error", ["execution_infrastructure_failure"]
        elif outcome in {"error", "cancelled", "interrupted"} or verification.get("status") in {"error", "cancelled"} or (metadata.get("status") == "interrupted" and not frozen):
            status, reasons = "error", ["execution_not_completed"]
        elif metadata.get("mode") == "strict" and execution["counts"].get("cancelled"):
            status, reasons = "error", ["cancelled_logical_steps"]
        elif evidence.get("required_missing") or evidence.get("errors") or evidence.get("status") == "partial":
            status, reasons = "error", ["required_evidence_incomplete"]
        elif outcome == "failed" or verification.get("status") == "failed" or (metadata.get("mode") == "strict" and execution["counts"].get("failed")):
            status, reasons = "failed", ["execution_or_verification_failed"]
        elif (
            outcome != "success"
            or verification.get("status") in {"inconclusive", "unknown", "pending"}
            or not execution["trustworthy"]
            or not execution.get("accounting_complete", True)
            or execution.get("history_conflict")
            or evidence.get("status") == "unavailable"
            or metadata.get("platform") not in {"web", "android", "windows", "macos"}
            or metadata.get("mode") not in {"strict", "explore"}
            or (metadata.get("mode") == "strict" and (execution["counts"].get("incomplete") or execution["counts"].get("skipped") or not execution["counts"].get("attempt_count")))
            or (not execution["counts"].get("attempt_count") and not execution["counts"].get("total") and verification.get("status") not in {"success", "passed"})
            or (metadata.get("schema_version") == "fsq.run/v2" and not frozen)
        ):
            status, reasons = "incomplete", ["completion_unresolved"]
        return {"status": status, "reasons": reasons, "references": [{"run_id": metadata.get("run_id"), "kind": "execution_result" if frozen else "historical_result"}]}

    def _interpretations(self, run_id, execution, documents, suggestions, artifacts):
        interpretations = []
        failure = execution.get("first_failure")
        if isinstance(failure, dict):
            interpretations.append(
                {
                    "kind": "recorded_fact",
                    "availability": "available",
                    "label": "Recorded failure",
                    "text": failure.get("error_message") or execution.get("summary"),
                    "references": [{"run_id": run_id, "step_execution_id": failure.get("step_execution_id")}],
                }
            )
        classifications = [(name, value.get("failure_classification")) for name, value in documents.items() if value.get("failure_classification")]
        for name, classification in classifications:
            artifact_id = f"internal-{name.replace('.', '-')}"
            interpretations.append(
                {
                    "kind": "deterministic_classification",
                    "availability": "available",
                    "label": "Rule-assisted classification",
                    "text": safe_text(classification, limit=None),
                    "references": [{"run_id": run_id, "artifact_id": artifact_id}],
                }
            )
        if suggestions:
            artifact = next((item for item in artifacts if item.get("path") == "case-suggestions.json"), None)
            references = [{"run_id": run_id, "artifact_id": artifact["artifact_id"]}] if artifact else []
            interpretations.append(
                {
                    "kind": "ai_suggestion",
                    "availability": "available" if references else "unavailable",
                    "label": "Post-execution AI suggestion",
                    "text": safe_text(suggestions.get("analysis_summary"), limit=None),
                    "suggestions": safe_tree(suggestions.get("suggestions", [])),
                    "references": references,
                }
            )
        else:
            interpretations.append({"kind": "ai_suggestion", "availability": "unavailable", "label": "Post-execution AI suggestion", "unavailable_reason": "not_recorded", "references": []})
        return interpretations

    def _log(self, event, run_id):
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        raw_message = str(event.get("message") or payload.get("message") or "")
        message = safe_text(raw_message)
        return {
            "run_id": run_id,
            "sequence": event.get("sequence"),
            "time": event.get("time") or event.get("timestamp"),
            "event_kind": event.get("type") or event.get("event_type"),
            "level": event.get("level") or ("error" if payload.get("status") == "failed" else "info"),
            "phase": event.get("phase"),
            "tool": event.get("tool") or event.get("tool_name"),
            "label": safe_text(event.get("label") or event.get("title")),
            "step_execution_id": payload.get("runner_step_id") or event.get("step_id"),
            "status": payload.get("status") or event.get("status"),
            "message": message,
            "truncated": len(safe_text(raw_message, limit=None)) > len(message),
            "original_size_bytes": len(raw_message.encode("utf-8")),
            "complete_artifact": {"run_id": run_id, "artifact_id": event["_source_artifact_id"]} if len(raw_message) > 4000 and event.get("_source_artifact_id") else None,
            "duration_ms": event.get("duration_ms"),
        }

    def _include(self, report, other):
        report._run_dirs.update(other._run_dirs)
        report._fingerprints.update(other._fingerprints)
        if other not in report._related:
            report._related.append(other)

    def _validate_related(self, report, other):
        from fsq_agent.report._comparison import _recording_mapping, verified_source_digest

        if report.run.get("platform") != other.run.get("platform"):
            raise report_error("lineage_invalid", "Related Run platform mismatch.")
        linked = False
        for child, parent in ((report, other), (other, report)):
            if any(relation.get("candidate_digest") for relation in parent.lineage.get("relations", [])):
                linked = linked or bool(_recording_mapping(child, parent))
            for relation in [child.lineage, *child.lineage.get("relations", [])]:
                digest = relation.get("case_digest") or relation.get("case_sha256")
                child_digest = verified_source_digest(child)
                candidates = [
                    item
                    for item in parent.artifacts
                    if item.get("run_id") == parent.run["run_id"] and item.get("artifact_id") == "candidate_case" and item.get("sha256") == digest and item.get("availability") == "available"
                ]
                candidate_valid = False
                for candidate in candidates:
                    parent_root = parent._run_dirs.get(parent.run["run_id"])
                    if parent_root:
                        candidate_path = contained_file(parent_root, candidate["path"])
                        candidate_valid = candidate_path.is_file() and digest_file(candidate_path) == digest
                linked = linked or bool(relation.get("originating_run_id") == parent.run["run_id"] and digest and digest == child_digest and candidate_valid)
            for relation in [parent.lineage, *parent.lineage.get("relations", [])]:
                digest = relation.get("case_digest") or relation.get("case_sha256")
                child_digest = verified_source_digest(child)
                candidate = next(
                    (
                        item
                        for item in parent.artifacts
                        if item.get("run_id") == parent.run["run_id"] and item.get("artifact_id") == "candidate_case" and item.get("sha256") == digest and item.get("availability") == "available"
                    ),
                    None,
                )
                parent_root = parent._run_dirs.get(parent.run["run_id"])
                candidate_valid = False
                if candidate is not None and parent_root:
                    candidate_path = contained_file(parent_root, candidate["path"])
                    candidate_valid = candidate_path.is_file() and digest_file(candidate_path) == digest
                linked = linked or bool(relation.get("kind") == "recording" and relation.get("originating_run_id") == parent.run["run_id"] and digest and digest == child_digest and candidate_valid)
        if not linked:
            raise report_error("lineage_invalid", "Related Run lineage or retained source hash is unavailable.")


_CREDENTIAL_KEYS = frozenset(
    {
        "authorization",
        "proxy_authorization",
        "set_cookie",
        "id_token",
        "cookie",
        "password",
        "passwd",
        "pwd",
        "secret",
        "client_secret",
        "token",
        "access_token",
        "refresh_token",
        "api_key",
        "apikey",
        "private_value",
        "private_values",
        "credentials",
        "config",
        "configuration",
        "environment",
        "env",
    }
)
_DISPLAY_KEYS = frozenset(
    {
        "summary",
        "analysis_summary",
        "description",
        "goal_summary",
        "message",
        "label",
        "error_message",
        "error",
        "explanation",
        "prompt",
        "content",
        "normalized_content",
        "outcome_description",
        "snapshot",
        "xml",
    }
)
_PROTECTED_KEYS = frozenset(
    {
        "schema_version",
        "schemaVersion",
        "run_id",
        "step_id",
        "step_execution_id",
        "source_step_id",
        "artifact_id",
        "tool_call_id",
        "invocation_path",
        "outcome",
        "status",
        "gate",
        "counts",
        "metrics",
        "sha256",
        "digest",
        "derived_sha256",
        "source_sha256",
        "retained_sha256",
        "duration_ms",
        "attempt_index",
        "references",
        "evidence_refs",
        "replay",
        "safe_replay_params",
        "command_mapping",
    }
)


def sensitive_key(key):
    normalized = _normalized_credential_key(key)
    return normalized in _CREDENTIAL_KEYS


def _normalized_credential_key(key):
    value = unquote(str(key)).strip().replace("-", "_")
    value = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", value)
    return value.casefold()


def _structured_url(match):
    raw = match.group(0)
    try:
        parts = urlsplit(raw)
        if "@" not in parts.netloc and not any(sensitive_key(key) for key, _ in parse_qsl(parts.query, keep_blank_values=True)):
            return raw
        query = [(key, "[REDACTED]" if sensitive_key(key) else value) for key, value in parse_qsl(parts.query, keep_blank_values=True)]
        return urlunsplit((parts.scheme, parts.netloc.rsplit("@", 1)[-1], parts.path, urlencode(query), parts.fragment))
    except ValueError:
        return "[REDACTED_URL]"


def safe_display_text(text):
    text = re.sub(r"(?im)(?<![\w-])((?:proxy[-_]?)?authorization|(?:set[-_]?)?cookie)\s*[:=]\s*[^\r\n]*", r"\1=[REDACTED]", text)
    text = re.sub(r'https?://[^\s"<>]+', _structured_url, text)
    text = re.sub(
        r"(?i)(?<![\w-])(id[_-]?token|client[_-]?secret|access[_-]?token|refresh[_-]?token|authorization|cookie|token|api[_-]?key|secret|password|passwd|pwd)\s*[:=]\s*[^\s,;\r\n]+",
        r"\1=[REDACTED]",
        text,
    )
    text = re.sub(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]+", r"\1 [REDACTED]", text)
    text = re.sub(r"(?<![A-Za-z0-9:/])(?:[A-Za-z]:[\\/]|/)[A-Za-z0-9_.-]+(?:[/\\][^\s\"<>]+)+", "[LOCAL_PATH]", text)
    return text


def _replace(text, replacements):
    for replacement in replacements:
        text = text.replace(replacement.text, replacement.replacement)
    return text


def safe_structured(value, replacements=(), *, key=None, protected=False):
    if isinstance(value, dict):
        result = {}
        for name, child in value.items():
            if sensitive_key(name):
                result[name] = "[REDACTED]"
            else:
                child_protected = protected or name in _PROTECTED_KEYS
                display = name in _DISPLAY_KEYS or (name == "text" and value.get("kind") in {"recorded_fact", "deterministic_classification", "ai_suggestion"})
                child_replacements = replacements if not child_protected and (display or isinstance(child, (dict, list))) else ()
                result[name] = safe_structured(child, child_replacements, key=name, protected=child_protected)
        return result
    if isinstance(value, list):
        return [safe_structured(item, replacements, key=key, protected=protected) for item in value]
    if isinstance(value, str):
        if value.lstrip().startswith(("{", "[")):
            try:
                parsed = json.loads(value)
            except ValueError:
                pass
            else:
                transformed = safe_structured(parsed, replacements if not protected else (), protected=protected)
                return value if transformed == parsed else json.dumps(transformed, ensure_ascii=False)
        if protected:
            return safe_display_text(value)
        sanitized = safe_display_text(value)
        return _replace(sanitized, replacements)
    return value


def sanitize_content(text, *, format_kind, replacements=(), remove_content=False):
    """Return safe derived text; keep exact original bytes when no values changed."""
    try:
        return _sanitize_content(text, format_kind=format_kind, replacements=replacements, remove_content=remove_content)
    except (ValueError, yaml.YAMLError) as exc:
        raise ReportGenerationError("Structured artifact cannot be safely decoded for export.", context={"reason": "artifact_unavailable"}) from exc


def _sanitize_content(text, *, format_kind, replacements=(), remove_content=False):
    if remove_content:
        return "[OMITTED]"
    if format_kind == "text" and text.lstrip().startswith(("{", "[")):
        try:
            json.loads(text)
        except ValueError:
            pass
        else:
            format_kind = "json"
    if format_kind == "jsonl":
        lines = text.splitlines(keepends=True)
        output = []
        for line in lines:
            if not line.strip():
                output.append(line)
                continue
            value = json.loads(line)
            transformed = safe_structured(value, replacements)
            output.append(line if transformed == value else json.dumps(transformed, ensure_ascii=False) + ("\n" if line.endswith("\n") else ""))
        return "".join(output)
    if format_kind == "json":
        value = json.loads(text)
        transformed = safe_structured(value, replacements)
        return text if transformed == value else json.dumps(transformed, ensure_ascii=False, indent=2)
    if format_kind == "yaml":
        spans = [(token.start_mark.index, token.end_mark.index) for token in yaml.scan(text) if isinstance(token, yaml.ScalarToken)]
        for match in reversed(list(re.finditer(r"(?m)(?<!\S)#[^\r\n]*", text))):
            if not any(start <= match.start() < end for start, end in spans):
                text = text[: match.start()] + safe_display_text(match.group()) + text[match.end() :]
        documents = list(yaml.safe_load_all(text))
        transformed = [safe_structured(value, replacements) for value in documents]
        if transformed == documents:
            return text
        return yaml.safe_dump_all(transformed, sort_keys=False, allow_unicode=True)
    return _replace(safe_display_text(text), replacements)
