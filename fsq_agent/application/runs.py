# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import hashlib
import json
import mimetypes
import os
import re
import secrets
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal
from urllib.parse import parse_qsl, unquote, urlencode, urlsplit, urlunsplit

from pydantic import BaseModel, ConfigDict, Field

from fsq_agent.application.contracts import (
    ApplicationError,
    ApplicationErrorCategory,
    ApplicationErrorCode,
    ExportRunReportRequest,
    ExportRunReportResult,
    GenerateRunHtmlRequest,
    GenerateRunHtmlResult,
    GetRunReportRequest,
    GetRunReportResult,
    ListRunsRequest,
    ListRunsResult,
    ReadRunLogsRequest,
    ReadRunLogsResult,
    ResolvedRunArtifact,
    ResolveRunArtifactRequest,
    RunDetail,
    RunLogEvent,
    RunSummary,
    ShowRunRequest,
    ShowRunResult,
)
from fsq_agent.config import inspect_registered_workspace, list_workspace_registry
from fsq_agent.execution import RunArtifactIndex, RunLifecycleService, RunResultSummary, RunSource, load_run_metadata
from fsq_agent.models import ReportGenerationError, RunReportExportOptions, RunShareProfile, RunStepCounts
from fsq_agent.report import RunReportService, generate_static_run_report

PLATFORMS = ("android", "web", "windows", "macos")


def list_runs(request: ListRunsRequest) -> ListRunsResult:
    name, root, platforms = _workspace_scope(request.current_directory, request.platform, request.workspace_name, request.user_config_root)
    summaries = [_summary(path, platform, name) for platform in platforms for path in _run_directories(root, platform)]
    warnings = _mark_conflicts(summaries)
    threshold = _since_threshold(request.since)
    selected = [item for item in summaries if _matches(item, request, threshold)]
    selected.sort(key=lambda item: (item.started_at is None, -(item.started_at.timestamp()) if item.started_at else 0, item.run_id, item.platform))
    returned = tuple(selected[: request.limit])
    return ListRunsResult(
        workspace=name,
        platforms=platforms,
        filters={"platform": request.platform, "statuses": request.statuses, "mode": request.mode, "since": request.since, "case_id": request.case_id, "limit": request.limit},
        matched_count=len(selected),
        returned_count=len(returned),
        truncated=len(selected) > len(returned),
        runs=returned,
        warnings=tuple(warnings),
    )


def show_run(request: ShowRunRequest) -> ShowRunResult:
    name, root, platforms = _workspace_scope(request.current_directory, request.platform, request.workspace_name, request.user_config_root)
    platform, run_dir = _find_run(root, platforms, request.run_id)
    metadata, warnings = _metadata(run_dir, platform, name)
    html = str((run_dir / "report.html").relative_to(root)) if (run_dir / "report.html").is_file() else None
    owner = RunLifecycleService.inspect_owner(run_dir)
    persisted_status = metadata.status
    observed = "interrupted" if metadata.status in {"preparing", "running", "finalizing"} and owner["status"] == "dead" else metadata.status
    detail = RunDetail.model_validate({**metadata.model_dump(), "status": observed, "persisted_status": persisted_status, "liveness": owner["status"]})
    detail = _safe_metadata_view(detail)
    return ShowRunResult(workspace=name, run=detail, html_path=html, warnings=warnings)


def read_run_logs(request: ReadRunLogsRequest) -> ReadRunLogsResult:
    _, root, platforms = _workspace_scope(request.current_directory, request.platform, request.workspace_name, request.user_config_root)
    platform, run_dir = _find_run(root, platforms, request.run_id)
    path = _contained_existing(run_dir, "events.jsonl")
    if path is None:
        raise _error(ApplicationErrorCode.RUN_LOGS_UNAVAILABLE, "Run logs are unavailable.", "Inspect the Run artifacts or execute the Case again.")
    events, warnings = _read_events(path)
    levels = {value.casefold() for value in request.levels}
    phases = {value.casefold() for value in request.phases}
    matched = [event for event in events if (not levels or (event.level or "").casefold() in levels) and (not phases or (event.phase or "").casefold() in phases)]
    chosen = matched[-request.limit :]
    return ReadRunLogsResult(
        run_id=request.run_id,
        platform=platform,
        filters={"levels": request.levels, "phases": request.phases, "limit": request.limit},
        matched_count=len(matched),
        returned_count=len(chosen),
        truncated=len(matched) > len(chosen),
        events=tuple(chosen),
        warnings=warnings,
    )


def generate_run_html(request: GenerateRunHtmlRequest) -> GenerateRunHtmlResult:
    shown = show_run(request)
    _, root, _ = _workspace_scope(request.current_directory, request.platform, request.workspace_name, request.user_config_root)
    run_dir = root / ".fsq" / "runs" / shown.run.platform / shown.run.run_id
    try:
        raw_metadata, _ = _metadata(run_dir, shown.run.platform, shown.workspace)
        facts = {**raw_metadata.model_dump(mode="json"), "status": shown.run.status, "liveness": shown.run.liveness, "persisted_status": shown.run.persisted_status}
        facts["normalized_evidence"] = _recovered_evidence(run_dir)
        path = generate_static_run_report(run_dir, facts)
    except Exception as exc:
        raise _error(ApplicationErrorCode.RUN_REPORT_GENERATION_FAILED, "Static Run report generation failed.", "Inspect the Run artifacts and retry.", internal=True) from exc
    return GenerateRunHtmlResult(run_id=shown.run.run_id, platform=shown.run.platform, html_path=str(path.relative_to(root)))


def _workspace_scope(current: Path | None, platform: str | None, workspace_name: str | None = None, user_config_root: Path | None = None) -> tuple[str, Path, tuple]:
    entries = list_workspace_registry(user_config_root) if user_config_root else list_workspace_registry()
    if workspace_name is not None:
        entry = next((item for item in entries if item.name.casefold() == workspace_name.casefold()), None)
    else:
        current = current.expanduser().resolve() if current else None
        entry = next((item for item in entries if item.root_path.resolve() == current), None)
    if entry is None or not entry.root_path.is_dir():
        raise _error(ApplicationErrorCode.WORKSPACE_NOT_INITIALIZED, "Registered Workspace is unavailable.", "Select a readable registered Workspace.")
    root = entry.root_path.resolve()
    known = set()
    try:
        known.update(item.platform for item in inspect_registered_workspace(entry.name, user_config_root, validate_target_paths=False).platforms)
    except Exception as exc:
        raise _error(ApplicationErrorCode.CONFIGURATION_INVALID, "Workspace platform inventory is unavailable.", "Repair the registered Workspace configuration.") from exc
    known.update(item for item in PLATFORMS if (root / ".fsq" / "runs" / item).is_dir() and not (root / ".fsq" / "runs" / item).is_symlink())
    platforms = tuple(item for item in PLATFORMS if item in known)
    if platform is not None and platform not in platforms:
        raise _error(ApplicationErrorCode.CONFIGURATION_INVALID, "The requested Workspace platform cannot be identified.", "Select a recorded platform.")
    return entry.name, root, (platform,) if platform else platforms


def _run_directories(root: Path, platform: str):
    path = _trusted_platform_root(root, platform)
    return sorted((item for item in path.iterdir() if item.is_dir() and not item.is_symlink()), key=lambda item: item.name) if path.is_dir() else []


def _trusted_platform_root(root: Path, platform: str) -> Path:
    candidate = root / ".fsq" / "runs" / platform
    for path in (root / ".fsq", root / ".fsq/runs", candidate):
        if path.is_symlink():
            raise _error(ApplicationErrorCode.RUN_METADATA_INVALID, "Run scope contains a symbolic link.", "Use contained regular Run directories.")
    if not candidate.resolve().is_relative_to(root.resolve()):
        raise _error(ApplicationErrorCode.RUN_METADATA_INVALID, "Run scope is not contained.", "Inspect the Workspace mapping.")
    return candidate


def _metadata(run_dir: Path, platform: str, workspace: str):
    path = run_dir / "run.json"
    if path.is_symlink():
        raise _error(ApplicationErrorCode.RUN_METADATA_INVALID, "Run metadata path is not contained.", "Inspect the Run directory.")
    if path.is_file():
        try:
            value = load_run_metadata(run_dir)
        except Exception as exc:
            raise _error(ApplicationErrorCode.RUN_METADATA_INVALID, "Run metadata is invalid.", "Repair or remove the damaged Run directory.") from exc
        if value.run_id != run_dir.name or value.platform != platform or value.workspace.get("name") != workspace:
            raise _error(ApplicationErrorCode.RUN_METADATA_INVALID, "Run metadata identity is invalid.", "Repair or remove the damaged Run directory.")
        warnings = []
        if _contained_existing(run_dir, "execution-result.json") is not None:
            try:
                frozen = RunLifecycleService.load_result(run_dir)
                if frozen.run_id != value.run_id or frozen.platform != platform:
                    warnings.append("Frozen execution identity conflicts with metadata.")
                elif value.status in {"success", "failed", "inconclusive", "cancelled", "error"} and (value.status != frozen.outcome or value.result.steps != frozen.counts):
                    warnings.append("Persisted metadata and frozen execution facts disagree; neither was rewritten.")
            except (ValueError, OSError):
                warnings.append("Frozen execution facts are invalid or unavailable.")
        return value, tuple(warnings)
    inferred = _infer_metadata(run_dir, platform, workspace)
    conflicts = tuple(f"Historical {key} conflicts between persisted sources; displayed as unknown." for key, value in inferred.availability.items() if value == "conflicting_sources")
    return inferred, ("Historical Run metadata was inferred from persisted artifacts.", *conflicts)


def _infer_metadata(run_dir: Path, platform: str, workspace: str) -> RunDetail:
    reports = []
    for name in ("execution-result.json", "report.json", "core-report.json", "report-fallback.json", "evidence-manifest.json"):
        candidate = _contained_existing(run_dir, name)
        if candidate is None:
            continue
        try:
            value = json.loads(RunReportService.read_fact_bytes(candidate))
            if isinstance(value, dict):
                if value.get("run_id") not in {None, run_dir.name}:
                    raise _error(ApplicationErrorCode.RUN_METADATA_INVALID, "Historical Run identity conflicts.", "Inspect the Run facts.")
                _validate_historical_scope(value, platform, workspace)
                reports.append((name, value))
        except ReportGenerationError as exc:
            raise _error(ApplicationErrorCode.RUN_METADATA_INVALID, "Historical Run facts exceed the resource limit.", "Inspect the oversized Run artifacts.") from exc
        except (OSError, ValueError):
            continue
    if not reports and _contained_existing(run_dir, "events.jsonl") is None and _contained_existing(run_dir, "evidence-events.jsonl") is None:
        raise _error(ApplicationErrorCode.RUN_METADATA_INVALID, "Historical Run metadata cannot be inferred.", "Inspect the damaged Run directory.")
    status = mode = source = started = completed = duration = None
    duration_reason = "unmeasured"
    summary_text = "Historical Run; unavailable fields are unknown."
    counts = None
    availability = dict.fromkeys(("status", "mode", "source", "started_at", "completed_at", "duration_ms", "counts"), "unavailable")
    candidates = {key: [] for key in availability}
    for name, report in reports:
        summary = report.get("summary")
        summary_map = summary if isinstance(summary, dict) else {}
        verification = report.get("verification") if isinstance(report.get("verification"), dict) else {}
        reported = report.get("outcome") or report.get("status") or summary_map.get("status") or verification.get("status")
        if reported is not None and not isinstance(reported, str):
            raise _error(ApplicationErrorCode.RUN_METADATA_INVALID, "Historical Run status is malformed.", "Inspect the historical Run facts.")
        reported = {"passed": "success"}.get(reported, reported)
        if reported in {"success", "failed", "cancelled", "error", "inconclusive"}:
            candidates["status"].append(reported)
        proposed = report.get("mode") or ("strict" if name == "core-report.json" else "explore" if isinstance(report.get("task"), dict) else None)
        if proposed in {"strict", "explore"}:
            candidates["mode"].append(proposed)
        reported_counts = _historical_counts(report)
        if reported_counts is not None:
            candidates["counts"].append(reported_counts.model_dump_json())
        if status is None and reported in {"success", "failed", "cancelled", "error", "inconclusive"}:
            status = reported
            availability["status"] = f"inferred:{name}"
        if mode is None:
            proposed = report.get("mode") or ("strict" if name == "core-report.json" else "explore" if isinstance(report.get("task"), dict) else None)
            if proposed in {"strict", "explore"}:
                mode = proposed
                availability["mode"] = f"inferred:{name}"
        if isinstance(summary, str) and summary:
            summary_text = summary[:2000]
        elif verification.get("summary"):
            summary_text = str(verification["summary"])[:2000]
        supplied = report.get("source")
        task = report.get("task")
        proposed_source = None
        try:
            if isinstance(supplied, dict):
                proposed_source = RunSource.model_validate(supplied)
            elif isinstance(task, dict) and (task.get("description") or task.get("name")):
                proposed_source = RunSource(kind="goal", goal_summary=str(task.get("description") or task["name"])[:500])
        except ValueError:
            pass
        if proposed_source is not None:
            candidates["source"].append(proposed_source)
            availability["source"] = f"inferred:{name}"
        for key in ("started_at", "completed_at"):
            for raw in (report.get(key), summary_map.get(key)):
                moment = _parse_moment(raw)
                if moment is not None:
                    candidates[key].append(moment)
                    availability[key] = f"inferred:{name}"
        started = next(iter(candidates["started_at"]), None)
        completed = next(iter(candidates["completed_at"]), None)
        candidate_duration = report.get("duration_ms")
        if isinstance(candidate_duration, int) and not isinstance(candidate_duration, bool) and candidate_duration >= 0:
            reason = report.get("duration_unavailable_reason")
            candidates["duration_ms"].append((candidate_duration, str(reason) if reason else None))
            if duration is None:
                duration = candidate_duration if not reason else None
                duration_reason = str(reason) if reason else None
                availability["duration_ms"] = f"inferred:{name}" if not reason else str(reason)
        if counts is None:
            counts = _historical_counts(report)
            if counts:
                availability["counts"] = f"inferred:{name}"
    if len(set(candidates["status"])) > 1:
        status = None
        availability["status"] = "conflicting_sources"
    if len(set(candidates["mode"])) > 1:
        mode = None
        availability["mode"] = "conflicting_sources"
    if len(set(candidates["counts"])) > 1:
        counts = None
        availability["counts"] = "conflicting_sources"
    sources = candidates["source"]
    source_fields = {key: {getattr(value, key) for value in sources if getattr(value, key) is not None} for key in RunSource.model_fields}
    if any(len(values) > 1 for values in source_fields.values()):
        source = None
        availability["source"] = "conflicting_sources"
    elif sources:
        source = RunSource.model_validate({key: next(iter(values)) for key, values in source_fields.items() if values})
    if len(set(candidates["started_at"])) > 1:
        started = None
        availability["started_at"] = "conflicting_sources"
    if len(set(candidates["completed_at"])) > 1:
        completed = None
        availability["completed_at"] = "conflicting_sources"
    if started is not None and completed is not None and completed < started:
        started = completed = None
        availability["started_at"] = availability["completed_at"] = "conflicting_sources"
    if len(set(candidates["duration_ms"])) > 1:
        duration = None
        duration_reason = "conflicting_sources"
        availability["duration_ms"] = "conflicting_sources"
    artifacts = RunArtifactIndex(
        report=next((name for name, _ in reports if name in {"report.json", "core-report.json", "report-fallback.json"}), None),
        events="events.jsonl" if _contained_existing(run_dir, "events.jsonl") else None,
        evidence_manifest="evidence-manifest.json" if _contained_existing(run_dir, "evidence-manifest.json") else None,
        execution_result="execution-result.json" if _contained_existing(run_dir, "execution-result.json") else None,
    )
    return RunDetail(
        schema_version="fsq.run/v1",
        run_id=run_dir.name,
        workspace={"name": workspace},
        platform=platform,
        mode=mode,
        status=status,
        started_at=started,
        completed_at=completed,
        duration_ms=duration,
        duration_unavailable_reason=duration_reason,
        source=source,
        result=RunResultSummary(summary=_sanitize(summary_text), steps=counts),
        artifacts=artifacts,
        availability=availability,
    )


def _historical_counts(report):
    summary = report.get("summary") if isinstance(report.get("summary"), dict) else {}
    supplied = report.get("counts")
    if isinstance(supplied, dict):
        if not isinstance(supplied.get("total"), int) or not any(key in supplied for key in ("passed", "failed", "skipped", "cancelled", "incomplete")):
            return None
        if "attempt_count" not in supplied:
            supplied = {**supplied, "attempt_count": None, "unavailable_reason": "historical_attempt_count_unavailable"}
        try:
            return RunStepCounts.model_validate(supplied)
        except ValueError:
            return None
    rows = report.get("steps")
    if isinstance(rows, list) and rows and all(isinstance(row, dict) and "phase_reports" in row for row in rows):
        leaves = {}
        for row in rows:
            if (row.get("metadata") or {}).get("hook_action_name") == "runCase":
                continue
            key = (row.get("source_step_id") or row.get("step_id"), str(row.get("invocation_path") or "root"))
            leaves[key] = row
        values = dict.fromkeys(("passed", "failed", "skipped", "cancelled", "incomplete"), 0)
        for row in leaves.values():
            value = {"success": "passed"}.get(row.get("status"), row.get("status"))
            if value not in values:
                return None
            values[value] += 1
        return RunStepCounts(total=len(leaves), attempt_count=None, unavailable_reason="historical_attempt_count_unavailable", **values)
    total = summary.get("step_count", summary.get("total_steps"))
    if isinstance(total, int) and not isinstance(total, bool) and total >= 0:
        values = {key: summary.get(f"{key}_steps", 0) for key in ("passed", "failed", "skipped", "cancelled", "incomplete")}
        try:
            return RunStepCounts(total=total, attempt_count=None, unavailable_reason="historical_attempt_count_unavailable", **values)
        except ValueError:
            return None
    return None


def _parse_moment(value):
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.astimezone(UTC) if parsed.tzinfo else None
    except ValueError:
        return None


def _contained_existing(root: Path, relative: str) -> Path | None:
    candidate = root / relative
    if any(path.is_symlink() for path in (candidate, *candidate.parents) if path != root.parent):
        return None
    return candidate if candidate.is_file() and candidate.resolve().is_relative_to(root.resolve()) else None


def _summary(path: Path, platform: str, workspace: str) -> RunSummary:
    try:
        metadata, warnings = _metadata(path, platform, workspace)
        metadata = _safe_metadata_view(metadata)
        owner = RunLifecycleService.inspect_owner(path)
        status = "interrupted" if metadata.status in {"preparing", "running", "finalizing"} and owner["status"] == "dead" else metadata.status
        if status == "interrupted":
            warnings = (*warnings, "Persisted Run has no terminal result and is displayed as interrupted.")
        return RunSummary(
            run_id=metadata.run_id,
            platform=metadata.platform,
            mode=metadata.mode,
            status=status,
            started_at=metadata.started_at,
            duration_ms=metadata.duration_ms,
            source=metadata.source,
            result=metadata.result,
            evidence=metadata.evidence or None,
            liveness=owner["status"],
            persisted_status=metadata.status,
            warnings=warnings,
        )
    except (ApplicationError, ValueError, TypeError, OSError):
        return RunSummary(run_id=path.name, platform=platform, status="error", warnings=("Run metadata is damaged.",))


def _mark_conflicts(items):
    counts = {}
    for item in items:
        counts[item.run_id] = counts.get(item.run_id, 0) + 1
    conflicts = {key for key, count in counts.items() if count > 1}
    for index, item in enumerate(items):
        if item.run_id in conflicts:
            items[index] = item.model_copy(update={"warnings": (*item.warnings, "Run ID conflicts with another platform.")})
    return [f"Run ID conflict: {key}" for key in sorted(conflicts)]


def _find_run(root: Path, platforms: tuple, run_id: str):
    if not run_id or run_id in {".", ".."} or any(char in run_id for char in ("/", chr(92), chr(0))) or Path(run_id).name != run_id:
        raise _error(ApplicationErrorCode.RUN_NOT_FOUND, "Run was not found.", "Provide a valid Run ID.", request=True)
    matches = []
    for platform in platforms:
        platform_root = _trusted_platform_root(root, platform).resolve()
        candidate = platform_root / run_id
        if candidate.is_symlink() or not candidate.is_dir():
            continue
        resolved = candidate.resolve()
        if not resolved.is_relative_to(platform_root):
            continue
        matches.append((platform, resolved))
    if not matches:
        raise _error(ApplicationErrorCode.RUN_NOT_FOUND, "Run was not found.", "List Runs and provide an existing Run ID.", request=True)
    if len(matches) > 1:
        raise _error(ApplicationErrorCode.RUN_ID_CONFLICT, "Run ID exists on multiple platforms.", "Specify --platform and repair the historical conflict.")
    return matches[0]


def _since_threshold(value):
    if value is None:
        return None
    match = re.fullmatch(r"([1-9]\d*)([mhd])", value)
    if match is None:
        raise _error(ApplicationErrorCode.CASE_INVALID, "Invalid --since duration.", "Use a positive duration such as 30m, 24h, or 7d.", request=True)
    try:
        seconds = int(match.group(1)) * {"m": 60, "h": 3600, "d": 86400}[match.group(2)]
        return datetime.now(UTC) - timedelta(seconds=seconds)
    except (ValueError, OverflowError):
        raise _error(ApplicationErrorCode.CASE_INVALID, "--since duration exceeds the supported time range.", "Use a bounded duration such as 30m, 24h, or 7d.", request=True) from None


def _matches(item, request, threshold):
    return (
        (not request.statuses or item.status in request.statuses)
        and (request.mode is None or item.mode == request.mode)
        and (threshold is None or (item.started_at is not None and item.started_at >= threshold))
        and (request.case_id is None or (item.source is not None and item.source.case_id == request.case_id.strip()))
    )


def _read_events(path):
    events = []
    warnings = []
    try:
        content = RunReportService.read_fact_bytes(path).decode("utf-8")
    except (OSError, ValueError, ReportGenerationError) as exc:
        raise _error(ApplicationErrorCode.RUN_LOGS_INVALID, "Run logs are unreadable or exceed the resource limit.", "Inspect the Run log.") from exc
    for index, line in enumerate(content.splitlines()):
        if not line.strip():
            continue
        try:
            value = _event_object(json.loads(line))
            value = _safe_event(_safe_display_value(value))
            sequence = value.get("sequence")
            _validate_sequence(sequence)
            if sequence is None:
                warnings.append("Historical event has no sequence; file order was preserved.")
            events.append((sequence if sequence is not None else index, index, RunLogEvent.model_validate(value)))
        except Exception as exc:
            raise _error(ApplicationErrorCode.RUN_LOGS_INVALID, "Run logs are invalid.", "Inspect the Run log and execute the Case again if needed.") from exc
    if [item[0] for item in events] != sorted(item[0] for item in events) or len({item[0] for item in events}) != len(events):
        warnings.append("Event sequence was normalized for display.")
    events.sort(key=lambda item: (item[0], item[1]))
    return [item[2] for item in events], tuple(dict.fromkeys(warnings))


def _event_object(value):
    if not isinstance(value, dict):
        raise TypeError("event must be an object")
    return value


def _safe_event(value: dict[str, object]) -> dict[str, object]:
    payload = value.get("payload") if isinstance(value.get("payload"), dict) else {}
    event_type = value.get("type", value.get("event_type"))
    normalized = {
        **value,
        "time": value.get("time", value.get("timestamp")),
        "tool": value.get("tool", value.get("tool_name")),
        "label": value.get("label", value.get("title")),
        "event_type": event_type,
        "step_id": value.get("step_id", payload.get("step_execution_id", payload.get("runner_step_id"))),
        "phase": value.get("phase", payload.get("phase")),
        "status": value.get("status", payload.get("status")),
        "level": value.get("level", "error" if event_type in {"run_failed", "tool_call_failed", "step_error"} else "info"),
    }
    allowed = ("sequence", "time", "level", "phase", "tool", "label", "status", "message", "step_id", "event_type", "duration_ms")
    return {key: normalized[key] for key in allowed if normalized.get(key) is not None}


def _validate_sequence(sequence):
    if sequence is not None and (not isinstance(sequence, int) or sequence < 0):
        raise ValueError("event sequence is invalid")


def _sanitize(value):
    secret_keys = ("token", "api_key", "apikey", "authorization", "proxy_authorization", "cookie", "set_cookie", "secret", "password", "passwd", "pwd")
    if isinstance(value, dict):
        return {str(key): ("[REDACTED]" if any(part in str(key).casefold() for part in secret_keys) else _sanitize(item)) for key, item in value.items()}
    if isinstance(value, list):
        return [_sanitize(item) for item in value]
    if isinstance(value, str):
        value = re.sub(r"(?i)(authorization|cookie)\s*[:=]\s*[^\r\n]+", r"\1=[REDACTED]", value)
        return re.sub(r"(?i)(bearer|token|api[_-]?key|secret|password|passwd|pwd)\s*[:=]?\s*[^\s,;]+", r"\1=[REDACTED]", value)
    return value


def _error(code, message, action, *, request=False, internal=False):
    category = ApplicationErrorCategory.INTERNAL if internal else ApplicationErrorCategory.REQUEST_VALIDATION if request else ApplicationErrorCategory.CONFIGURATION
    return ApplicationError(code=code, category=category, message=message, action=action)


__all__ = ["export_run_report", "generate_run_html", "get_run_report", "list_runs", "read_run_logs", "resolve_run_artifact", "show_run"]


def _recovered_evidence(run_dir: Path):
    if not (run_dir / "evidence-manifest.json").is_file() and not (run_dir / "evidence-events.jsonl").is_file():
        return None
    return RunLifecycleService.read_evidence(run_dir)


def get_run_report(request: GetRunReportRequest) -> GetRunReportResult:
    try:
        return _project_run_report(request)
    except (ReportGenerationError, ValueError, OSError) as exc:
        raise _report_error(exc) from exc


def _project_run_report(request: GetRunReportRequest) -> GetRunReportResult:
    shown = show_run(request)
    _, root, _ = _workspace_scope(request.current_directory, request.platform, request.workspace_name, request.user_config_root)
    run_dir = root / ".fsq" / "runs" / shown.run.platform / shown.run.run_id
    service = RunReportService()
    raw_metadata, _ = _metadata(run_dir, shown.run.platform, shown.workspace)
    projection_facts = {**raw_metadata.model_dump(mode="json"), "status": shown.run.status, "liveness": shown.run.liveness, "persisted_status": shown.run.persisted_status}
    report = service.project(run_dir, projection_facts, _recovered_evidence(run_dir))
    baseline = None
    related = []
    for identifier in (*((request.baseline_run_id,) if request.baseline_run_id else ()), *request.related_run_ids):
        related_request = GetRunReportRequest(
            current_directory=request.current_directory, workspace_name=request.workspace_name, user_config_root=request.user_config_root, platform=shown.run.platform, run_id=identifier
        )
        other = get_run_report(related_request).report
        if identifier == request.baseline_run_id:
            baseline = other
        else:
            related.append(other)
    if baseline or related:
        report = service.project(run_dir, projection_facts, _recovered_evidence(run_dir), baseline=baseline, related_runs=related)
    return GetRunReportResult(workspace=shown.workspace, platform=shown.run.platform, run_id=shown.run.run_id, report=report, warnings=shown.warnings)


def export_run_report(request: ExportRunReportRequest) -> ExportRunReportResult:
    report = get_run_report(request)
    _, root, _ = _workspace_scope(request.current_directory, request.platform, request.workspace_name, request.user_config_root)
    run_dir = root / ".fsq" / "runs" / report.platform / report.run_id
    export_id = secrets.token_hex(12)
    names = {"json": "report.json", "junit": "junit.xml", "html": "report.html", "bundle": "evidence-bundle.zip"}
    local = request.output_path is None
    directory = _export_base(run_dir, export_id)
    if local:
        destination = directory / names[request.format]
    else:
        requested = request.output_path.expanduser()
        lexical = root / requested if not requested.is_absolute() else requested
        if any(path.is_symlink() for path in (lexical, *lexical.parents)):
            raise _error(ApplicationErrorCode.RUN_EXPORT_INVALID, "Export destination contains a symbolic link.", "Choose a regular output directory.")
        destination = lexical.resolve()
        if destination.exists():
            raise _error(ApplicationErrorCode.CONFIGURATION_INVALID, "Export destination exists.", "Choose an absent destination file.")
        if destination.is_relative_to(root / ".fsq") or destination.is_relative_to(root / "cases") or destination.is_relative_to(root / "knowledge"):
            raise _error(ApplicationErrorCode.CONFIGURATION_INVALID, "Export destination overlaps project facts.", "Choose a separate output file.")
        if not destination.parent.is_dir():
            raise _error(ApplicationErrorCode.CONFIGURATION_INVALID, "Export destination parent is unavailable.", "Choose an existing output directory.")
    profile = None
    if request.share_profile:
        path = request.share_profile.expanduser()
        path = root / path if not path.is_absolute() else path
        try:
            if path.is_symlink() or not path.is_file() or path.stat().st_size > 256 * 1024:
                _invalid_export_identity()
            profile_bytes = path.read_bytes()
            profile = RunShareProfile.model_validate_json(profile_bytes)
        except (OSError, ValueError):
            raise _error(ApplicationErrorCode.CONFIGURATION_INVALID, "Share profile is invalid or unavailable.", "Use a valid bounded local share profile.") from None
    run_dirs = {
        identifier: root / ".fsq" / "runs" / report.platform / identifier for identifier in (report.run_id, *request.related_run_ids, *((request.baseline_run_id,) if request.baseline_run_id else ()))
    }
    options = RunReportExportOptions(format=request.format, destination=destination, export_id=export_id, run_dirs=run_dirs, share_profile=profile)
    service = RunReportService()
    try:
        service.validate_export(report.report, options)
    except (ReportGenerationError, ValueError, OSError) as exc:
        raise _report_error(exc) from exc
    try:
        if local:
            _export_base(run_dir, export_id)
            directory.mkdir(parents=True, exist_ok=False)
        generated = service.export(report.report, options)
        data = destination.read_bytes()
        files = (
            {
                "file_id": "report",
                "name": destination.name,
                "path": destination.name,
                "mime_type": mimetypes.guess_type(destination.name)[0] or "application/octet-stream",
                "size_bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
            },
        )
        if local:
            manifest = {"schema_version": "fsq.export/v1", "run_id": report.run_id, "platform": report.platform, "export_id": export_id, "files": list(files)}
            descriptor, temporary = tempfile.mkstemp(prefix=".manifest-", dir=directory)
            try:
                with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                    stream.write(json.dumps(manifest, indent=2))
                    stream.flush()
                    os.fsync(stream.fileno())
                Path(temporary).replace(directory / "export-manifest.json")
            finally:
                Path(temporary).unlink(missing_ok=True)
    except Exception as exc:
        if local:
            import shutil

            shutil.rmtree(directory, ignore_errors=True)
        raise _report_error(exc) from exc
    return ExportRunReportResult(
        run_id=report.run_id,
        platform=report.platform,
        export_id=export_id,
        format=request.format,
        output_path=generated.path,
        execution_status=report.report.execution.get("outcome"),
        report_gate=report.report.run["gate"]["status"],
        files=files,
        warnings=tuple(generated.warnings),
    )


class _ExportFile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    file_id: str = Field(min_length=1, max_length=200)
    name: str
    path: str
    size_bytes: int = Field(ge=0, strict=True)
    mime_type: str
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class _ExportManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal["fsq.export/v1"]
    run_id: str
    platform: Literal["android", "web", "windows", "macos"]
    export_id: str
    files: tuple[_ExportFile, ...]


def _export_base(run_dir, export_id):
    if not re.fullmatch(r"[a-f0-9]{24}", export_id):
        raise _error(ApplicationErrorCode.RUN_NOT_FOUND, "Export is unavailable.", "Generate a new export.")
    base = run_dir / "exports" / export_id
    if (run_dir / "exports").is_symlink() or base.is_symlink() or not base.resolve().is_relative_to(run_dir.resolve()):
        raise _error(ApplicationErrorCode.RUN_METADATA_INVALID, "Export scope is not contained.", "Use a contained export directory.")
    return base


def resolve_run_artifact(request: ResolveRunArtifactRequest) -> ResolvedRunArtifact:
    shown = show_run(request)
    _, root, _ = _workspace_scope(request.current_directory, request.platform, request.workspace_name, request.user_config_root)
    _, run_dir = _find_run(root, (shown.run.platform,), shown.run.run_id)
    ref = request.reference
    if ref.kind == "source":
        report = get_run_report(
            GetRunReportRequest(
                current_directory=request.current_directory, workspace_name=request.workspace_name, user_config_root=request.user_config_root, platform=shown.run.platform, run_id=shown.run.run_id
            )
        ).report
        item = next((value for value in report.artifacts if value.get("artifact_id") == ref.artifact_id and value.get("run_id", shown.run.run_id) == shown.run.run_id), None)
        base = run_dir
    else:
        base = _export_base(run_dir, ref.export_id)
        manifest_path = _contained_existing(base, "export-manifest.json")
        try:
            if manifest_path is None or manifest_path.stat().st_size > 256 * 1024:
                _invalid_export_identity()
            manifest = _ExportManifest.model_validate_json(manifest_path.read_bytes())
            if (
                manifest.run_id != shown.run.run_id
                or manifest.platform != shown.run.platform
                or manifest.export_id != ref.export_id
                or len({value.file_id for value in manifest.files}) != len(manifest.files)
            ):
                _invalid_export_identity()
            selected = next((value for value in manifest.files if value.file_id == ref.file_id), None)
            item = selected.model_dump() if selected else None
        except (OSError, ValueError):
            raise _error(ApplicationErrorCode.RUN_METADATA_INVALID, "Export manifest is invalid.", "Generate a new contained export.") from None
    if item is None or not isinstance(item.get("path"), str):
        raise _error(ApplicationErrorCode.RUN_NOT_FOUND, "Artifact is unavailable.", "Inspect the artifact inventory.")
    relative = Path(item["path"])
    if relative.is_absolute() or ".." in relative.parts:
        raise _error(ApplicationErrorCode.RUN_METADATA_INVALID, "Artifact path is not contained.", "Inspect the artifact inventory.")
    path = _contained_existing(base, relative.as_posix())
    allowed = {".png", ".jpg", ".jpeg", ".json", ".jsonl", ".txt", ".md", ".yaml", ".yml", ".xml", ".html", ".zip", ".webm"}
    if path is None or path.suffix.lower() not in allowed:
        raise _error(ApplicationErrorCode.RUN_NOT_FOUND, "Artifact path is unavailable.", "Inspect the artifact inventory.")
    size = path.stat().st_size
    if size > 512 * 1024 * 1024:
        raise _error(ApplicationErrorCode.CONFIGURATION_INVALID, "Artifact exceeds the read limit.", "Inspect the local Run directory.")
    expected_size = item.get("size_bytes")
    expected = item.get("sha256")
    with path.open("rb") as stream:
        actual = hashlib.file_digest(stream, "sha256").hexdigest()
    if (expected_size is not None and size != expected_size) or (expected and actual != expected):
        raise _error(ApplicationErrorCode.RUN_METADATA_INVALID, "Artifact integrity check failed.", "Inspect the local Run directory.")
    return ResolvedRunArtifact(path=path, size=size, sha256=actual, mime_type=mimetypes.guess_type(path.name)[0] or "application/octet-stream", filename=path.name)


def _invalid_export_identity() -> None:
    raise ValueError("Export identity does not match its Run.")


def _report_error(error: Exception) -> ApplicationError:
    reason = getattr(error, "context", {}).get("reason", "schema_invalid" if isinstance(error, ValueError) else "persistence_failed")
    codes = {
        "schema_invalid": ApplicationErrorCode.RUN_METADATA_INVALID,
        "source_changed": ApplicationErrorCode.RUN_SOURCE_CHANGED,
        "destination_exists": ApplicationErrorCode.RUN_EXPORT_INVALID,
        "path_unsafe": ApplicationErrorCode.RUN_EXPORT_INVALID,
        "profile_invalid": ApplicationErrorCode.RUN_EXPORT_INVALID,
        "baseline_incomparable": ApplicationErrorCode.RUN_COMPARISON_INVALID,
        "lineage_invalid": ApplicationErrorCode.RUN_COMPARISON_INVALID,
        "resource_limit": ApplicationErrorCode.RUN_EXPORT_INVALID,
        "artifact_unavailable": ApplicationErrorCode.RUN_REPORT_UNAVAILABLE,
        "source_identity_unavailable": ApplicationErrorCode.RUN_REPORT_UNAVAILABLE,
    }
    internal = reason == "persistence_failed"
    return ApplicationError(
        code=codes.get(reason, ApplicationErrorCode.RUN_REPORT_GENERATION_FAILED),
        category=ApplicationErrorCategory.INTERNAL if internal else ApplicationErrorCategory.CONFIGURATION,
        message="Run report operation failed.",
        action="Inspect the recorded facts, requested comparison, profile and output destination.",
        details={"reason": reason},
    )


def _safe_metadata_view(metadata):
    values = metadata.model_dump(mode="json")
    protected = {
        "schema_version",
        "revision",
        "run_id",
        "workspace",
        "platform",
        "mode",
        "status",
        "started_at",
        "completed_at",
        "duration_ms",
        "duration_unavailable_reason",
        "artifacts",
        "execution_result",
        "liveness",
        "persisted_status",
        "availability",
    }
    for key in values.keys() - protected:
        values[key] = _safe_display_value(values[key])
    return RunDetail.model_validate(values)


def _safe_display_value(value, key=""):
    sensitive = {
        "token",
        "access_token",
        "refresh_token",
        "id_token",
        "password",
        "passwd",
        "api_key",
        "apikey",
        "authorization",
        "proxy_authorization",
        "cookie",
        "set_cookie",
        "secret",
        "client_secret",
        "private_value",
        "private_values",
        "credentials",
        "environment",
        "env",
        "configuration",
        "config",
    }
    normalized_key = re.sub(r"([a-z0-9])([A-Z])", lambda match: match[1] + "_" + match[2], unquote(key)).casefold().replace("-", "_")
    if normalized_key in sensitive:
        return "[REDACTED]"
    if isinstance(value, dict):
        return {name: _safe_display_value(item, str(name)) for name, item in value.items()}
    if isinstance(value, list):
        return [_safe_display_value(item) for item in value]
    if not isinstance(value, str):
        return value
    if value.lstrip().startswith(("{", "[")):
        try:
            parsed = json.loads(value)
        except (ValueError, RecursionError):
            parsed = None
        if isinstance(parsed, (dict, list)):
            return json.dumps(_safe_display_value(parsed), ensure_ascii=False)

    def safe_url(match):
        raw = match.group()
        try:
            parts = urlsplit(raw)
            query = [
                (name, "[REDACTED]" if re.sub(r"([a-z0-9])([A-Z])", lambda match: match[1] + "_" + match[2], unquote(name)).casefold().replace("-", "_") in sensitive else item)
                for name, item in parse_qsl(parts.query, keep_blank_values=True)
            ]
            return urlunsplit((parts.scheme, parts.netloc.rsplit("@", 1)[-1], parts.path, urlencode(query), parts.fragment))
        except ValueError:
            return "[REDACTED_URL]"

    value = re.sub(r"https?://[^\s<>\"']+", safe_url, value)
    value = re.sub(r"(?im)\b(authorization|proxy[-_]authorization|cookie|set[-_]cookie)\s*[:=]\s*[^\r\n]*", r"\1=[REDACTED]", value)
    value = re.sub(r"(?i)\b(?:Bearer|Basic)\s+[^\s,;]+", "[REDACTED_AUTH]", value)
    value = re.sub(r"(?i)\b((?:access|refresh|id)[_-]?token|token|client[_-]?secret|password|passwd|pwd|api[_-]?key|authorization|cookie|secret)\s*[:=]\s*[^\s,;]+", r"\1=[REDACTED]", value)
    value = re.sub(r"(?<![\w:/])(?:[A-Za-z]:[\\/]|/)[^\s<>\"']+", "[LOCAL_PATH]", value)
    return value


def _validate_historical_scope(document, platform, workspace):
    for facts in (document, document.get("metadata")):
        if not isinstance(facts, dict):
            continue
        if facts.get("platform") is not None and facts["platform"] != platform:
            raise _error(ApplicationErrorCode.RUN_METADATA_INVALID, "Historical platform identity conflicts with its scope.", "Inspect the original Run facts.")
        supplied = facts.get("workspace_name", facts.get("workspaceName", facts.get("workspace")))
        if isinstance(supplied, dict):
            supplied = supplied.get("name")
        if supplied is not None and supplied != workspace:
            raise _error(ApplicationErrorCode.RUN_METADATA_INVALID, "Historical Workspace identity conflicts with its scope.", "Inspect the original Run facts.")
