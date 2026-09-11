# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import json
import os
import re
import secrets
import tempfile
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

from fsq_agent.models import RunArtifactIndex, RunMetadata, RunResultSummary, RunRuntime, RunSource, RunStepCounts
from fsq_agent.report import RunReportService

_TERMINAL = {"success", "failed", "inconclusive", "cancelled", "error"}
_START_TIMES: dict[Path, float] = {}
_WRITE_LOCK = threading.RLock()


def allocate_run(
    *,
    workspace: Path,
    workspace_name: str,
    platform: str,
    source_id: str,
    mode: str,
    source: RunSource,
    platform_runs_dir: Path | None = None,
    now: datetime | None = None,
) -> RunMetadata:
    moment = (now or datetime.now(UTC)).astimezone(UTC)
    slug = re.sub(r"[^a-z0-9]+", "-", source_id.casefold()).strip("-")[:60] or "run"
    runs_root = workspace / ".fsq" / "runs"
    target_root = (platform_runs_dir or runs_root / platform).resolve()
    target_root.mkdir(parents=True, exist_ok=True)
    for _ in range(5):
        run_id = f"{slug}-{moment.strftime('%Y%m%dT%H%M%SZ')}-{secrets.token_hex(3)}"
        if any((runs_root / candidate / run_id).exists() for candidate in ("android", "web", "windows", "macos")):
            continue
        run_dir = target_root / run_id
        try:
            run_dir.mkdir()
        except FileExistsError:
            continue
        metadata = RunMetadata(run_id=run_id, workspace={"name": workspace_name}, platform=platform, mode=mode, status="preparing", started_at=moment, source=source)
        try:
            write_run_metadata(run_dir, metadata)
            _START_TIMES[run_dir.resolve()] = time.perf_counter()
        except Exception:
            try:
                run_dir.rmdir()
            except OSError:
                pass
            raise
        return metadata
    raise RuntimeError("Unable to allocate a unique Run ID.")


def transition_run(run_dir: Path, metadata: RunMetadata, status: str, *, completed_at: datetime | None = None, **updates: object) -> RunMetadata:
    terminal = {"success", "failed", "inconclusive", "cancelled", "error"}
    persisted = load_run_metadata(run_dir)
    if persisted.run_id != metadata.run_id or persisted.platform != metadata.platform or persisted.workspace != metadata.workspace:
        raise ValueError("Run metadata identity changed on disk.")
    if persisted.status in terminal:
        raise ValueError("Terminal Run metadata is immutable.")
    if persisted != metadata:
        raise ValueError("Run metadata is stale.")
    order = {"preparing": 0, "running": 1, "finalizing": 2}
    if status not in terminal and order.get(status, -1) <= order.get(metadata.status, -1):
        raise ValueError("Run status transition must move forward.")
    values = {**updates, "status": status, "revision": metadata.revision + 1}
    if status in terminal:
        finished = (completed_at or datetime.now(UTC)).astimezone(UTC)
        duration_ms = _duration(run_dir)
        values.update(
            completed_at=max(finished, metadata.started_at) if metadata.started_at else finished,
            duration_ms=duration_ms,
            duration_unavailable_reason=None if duration_ms is not None else "owner_clock_unavailable",
        )
    changed = RunMetadata.model_validate({**metadata.model_dump(), **values})
    write_run_metadata(run_dir, changed)
    return changed


def write_run_metadata(run_dir: Path, metadata: RunMetadata) -> None:
    with _WRITE_LOCK:
        _write_run_metadata(run_dir, RunMetadata.model_validate(metadata.model_dump()))


def _write_run_metadata(run_dir: Path, metadata: RunMetadata) -> None:
    run_dir = run_dir.resolve()
    path = run_dir / "run.json"
    if path.is_symlink():
        raise ValueError("Run metadata must remain contained.")
    if path.exists():
        persisted = load_run_metadata(run_dir)
        if persisted.status in _TERMINAL:
            raise ValueError("Terminal Run metadata is immutable.")
        if any(getattr(persisted, field) != getattr(metadata, field) for field in ("run_id", "platform", "workspace", "mode", "started_at")):
            raise ValueError("Run metadata identity changed.")
        if metadata.revision != persisted.revision + 1:
            raise ValueError("Run metadata is stale.")
        order = {"preparing": 0, "running": 1, "finalizing": 2}
        if metadata.status not in _TERMINAL and order[metadata.status] < order[persisted.status]:
            raise ValueError("Run metadata is stale.")
    elif metadata.revision != 0:
        raise ValueError("Initial Run metadata revision must be zero.")
    data = metadata.model_dump_json(indent=2).encode() + b"\n"
    descriptor, temporary = tempfile.mkstemp(prefix=".run.", suffix=".tmp", dir=run_dir)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if path.exists():
            Path(temporary).replace(path)
        else:
            os.link(temporary, path)
            Path(temporary).unlink()
    except Exception:
        try:
            Path(temporary).unlink()
        except FileNotFoundError:
            pass
        raise


def load_run_metadata(run_dir: Path) -> RunMetadata:
    return RunMetadata.model_validate_json(_read_fact(run_dir / "run.json"))


def _read_fact(path: Path) -> bytes:
    from fsq_agent.models import ReportGenerationError

    try:
        return RunReportService.read_fact_bytes(path)
    except ReportGenerationError as exc:
        raise ValueError("Run facts exceed the resource limit.") from exc


__all__ = [
    "RunArtifactIndex",
    "RunLifecycleService",
    "RunMetadata",
    "RunResultSummary",
    "RunRuntime",
    "RunSource",
    "RunStepCounts",
    "allocate_run",
    "load_run_metadata",
    "transition_run",
    "write_run_metadata",
]


class RunLifecycleService:
    """One authority for durable Run conclusions and their derived processing."""

    @staticmethod
    def ensure_run(run_dir: Path, *, run_id: str, platform: str, mode: str, source: RunSource, workspace_name: str | None = None) -> RunMetadata:
        run_dir = run_dir.resolve()
        run_dir.mkdir(parents=True, exist_ok=True)
        if (run_dir / "run.json").exists():
            metadata = load_run_metadata(run_dir)
            if metadata.run_id != run_id or metadata.platform != platform or metadata.mode != mode or metadata.status in _TERMINAL:
                raise ValueError("Run identity or active lifecycle is incompatible with execution.")
            return metadata
        if any((run_dir / name).exists() for name in ("execution-result.json", "evidence-events.jsonl", "evidence-manifest.json")):
            raise ValueError("Existing Run evidence cannot be adopted as a new execution.")
        metadata = RunMetadata(
            run_id=run_id, workspace={"name": workspace_name or run_dir.parent.name or "local"}, platform=platform, mode=mode, status="preparing", started_at=datetime.now(UTC), source=source
        )
        write_run_metadata(run_dir, metadata)
        _START_TIMES[run_dir] = time.perf_counter()
        return metadata

    @staticmethod
    def safe_text(text: str, secret_values: tuple[str, ...] = ()) -> str:
        return _safe_text(text, secret_values)

    @staticmethod
    def safe_source_text(text: str, secret_values: tuple[str, ...] = ()) -> str:
        return _safe_source_text(text, secret_values)

    @staticmethod
    def safe_value(value, secret_values: tuple[str, ...] = ()):
        return _safe_structure(value, secret_values)

    @staticmethod
    def preflight_steps(steps, runtime_secret_store, *, registry=None):
        """Validate all declared invocations and secrets before any external action."""
        from fsq_agent.models import ConfigurationError

        validated = []
        for source_step in steps:
            step = source_step
            if registry is not None:
                capability = registry.resolve(step.action_name)
                if capability is None:
                    raise ConfigurationError("Unknown capability in executable Case.", context={"step_id": step.step_id})
                try:
                    params = capability.params_model.model_validate(step.params).model_dump(mode="json", exclude_none=True)
                except ValueError as exc:
                    raise ConfigurationError("Invalid capability parameters in executable Case.", context={"step_id": step.step_id, "capability": capability.name}) from exc
                step = step.model_copy(update={"action_name": capability.name, "params": params})
            if step.params.get("textType") == "runtimeSecret":
                name = step.params.get("text")
                if not isinstance(name, str):
                    raise ConfigurationError("Runtime secret reference must be a string.")
                runtime_secret_store.resolve(name)
            validated.append(step)
        return validated

    @staticmethod
    def execution_error(error: BaseException, metadata: RunMetadata) -> BaseException:
        from fsq_agent.models import FsqAgentError, ToolExecutionError

        context = {"run_id": metadata.run_id, "platform": metadata.platform}
        if isinstance(error, FsqAgentError):
            error.context = {**error.context, **context}
            return error
        if not isinstance(error, Exception) or type(error).__name__ in {"TaskCancelledError", "ExecutionCancelled", "RunCancelled"}:
            error.context = context
            return error
        return ToolExecutionError(f"Run execution failed ({type(error).__name__}).", context=context)

    @staticmethod
    def safe_configuration(settings) -> dict[str, object]:
        platform = settings.harness.platform
        block = getattr(settings.harness, platform)
        result = {
            "platform": platform,
            "backend": getattr(block, "backend", None),
            "agent_runtime": {"reasoning_effort": settings.agent_runtime.reasoning_effort, "max_turns": settings.agent_runtime.max_turns},
            "post_action_delay_seconds": settings.execution.post_action_delay_seconds.model_dump(),
            "app_version": None,
            "app_version_unavailable_reason": "not_reported",
            "git_revision": None,
            "git_revision_unavailable_reason": "not_reported",
        }
        for name in ("headless", "channel", "viewport_width", "viewport_height", "action_timeout_seconds", "new_command_timeout_seconds", "backend_kind"):
            if hasattr(block, name):
                result[name] = getattr(block, name)
        return result

    @staticmethod
    def case_source_bytes(case) -> bytes:
        if case.source_text is not None:
            return case.source_text.encode("utf-8")
        import yaml

        return yaml.safe_dump_all([case.config.model_dump(mode="json", by_alias=True), case.commands], sort_keys=False).encode("utf-8")

    @staticmethod
    def source_step_id(case, index: int, *, source_path: str | None = None, secret_values: tuple[str, ...] = ()) -> str:
        import hashlib

        safe = _safe_source_text(RunLifecycleService.case_source_bytes(case).decode("utf-8"), secret_values).encode("utf-8")
        digest = hashlib.sha256(safe).hexdigest()
        return f"{source_path or case.path.as_posix()}@{digest}:step:{index}"

    @staticmethod
    def load_result(run_dir: Path):
        from fsq_agent.models import RunExecutionResult

        return RunExecutionResult.model_validate_json(_read_fact(run_dir / "execution-result.json"))

    @staticmethod
    def read_evidence(run_dir: Path):
        from fsq_agent.core.evidence import EvidenceRecorder

        for name in ("evidence-manifest.json", "evidence-events.jsonl"):
            path = run_dir / name
            if path.is_file():
                RunReportService.read_fact_bytes(path)
        return EvidenceRecorder.recover_bundle(run_dir)

    @staticmethod
    def inspect_owner(run_dir: Path) -> dict[str, object]:
        import sys

        try:
            owner = json.loads(_read_fact(run_dir / "owner.json"))
            pid = int(owner["pid"])
            recorded = owner.get("process_start")
            if pid < 1 or not recorded:
                return {"status": "unknown", "reason": "Owner identity is unavailable."}
            current = _process_start(pid)
            if current is None:
                if sys.platform == "win32":
                    return {"status": "unknown", "reason": "Owner process identity cannot be checked safely."}
                try:
                    os.kill(pid, 0)
                except ProcessLookupError:
                    return {"status": "dead", "reason": "Owner process has exited."}
                except (PermissionError, OSError):
                    pass
                return {"status": "unknown", "reason": "Owner process identity cannot be checked."}
            if current != recorded:
                return {"status": "dead", "reason": "Owner process identity changed."}
            return {"status": "alive", "heartbeat": owner.get("heartbeat")}
        except (OSError, ValueError, KeyError, TypeError):
            return {"status": "unknown", "reason": "No trustworthy owner record."}

    @staticmethod
    def heartbeat(run_dir: Path) -> None:
        _atomic_json(run_dir / "owner.json", {"schema_version": "fsq.owner/v1", "pid": os.getpid(), "process_start": _process_start(os.getpid()), "heartbeat": datetime.now(UTC).isoformat()})

    @staticmethod
    def context(run_dir: Path, metadata: RunMetadata):
        from fsq_agent.models import RunExecutionContext

        return RunExecutionContext(run_id=metadata.run_id, platform=metadata.platform, run_dir=run_dir, workspace_name=metadata.workspace["name"], provenance=metadata.provenance)

    @staticmethod
    def snapshot_sources(
        run_dir: Path, metadata: RunMetadata, *, sources: dict[str, Path | str | bytes], secret_values: tuple[str, ...] = (), configuration: dict[str, object] | None = None
    ) -> RunMetadata:
        import hashlib
        from importlib.metadata import PackageNotFoundError, version

        persisted = load_run_metadata(run_dir)
        if persisted != metadata:
            raise ValueError("Run metadata is stale.")
        if persisted.status in {"success", "failed", "inconclusive", "cancelled", "error"}:
            raise ValueError("Terminal Run metadata is immutable.")
        entries = []
        for index, (label, source) in enumerate(sources.items()):
            raw = source.read_bytes().decode("utf-8") if isinstance(source, Path) else source.decode("utf-8") if isinstance(source, bytes) else source
            safe = _safe_source_text(raw, secret_values)
            relative = f"sources/{index:03d}-{re.sub(r'[^a-zA-Z0-9_-]', '-', label)[:60]}.txt"
            target = run_dir / relative
            _contained_destination(run_dir, target)
            target.parent.mkdir(exist_ok=True)
            _contained_destination(run_dir, target)
            _atomic_bytes(target, safe.encode("utf-8"))
            entries.append(
                {
                    "name": label,
                    "path": relative,
                    "sha256": hashlib.sha256(safe.encode()).hexdigest(),
                    "size_bytes": len(safe.encode()),
                    "transformed": safe != raw,
                    "kind": "case" if isinstance(source, (Path, bytes)) else "goal",
                }
            )
        try:
            producer = version("fsq-agent")
        except PackageNotFoundError:
            producer = None
        allowed = {
            "platform",
            "backend",
            "headless",
            "viewport",
            "viewport_width",
            "viewport_height",
            "channel",
            "post_action_delay_seconds",
            "app_version",
            "git_revision",
            "app_version_unavailable_reason",
            "git_revision_unavailable_reason",
            "action_timeout_seconds",
            "new_command_timeout_seconds",
            "backend_kind",
        }
        safe_config = {key: value for key, value in (configuration or {}).items() if key in allowed}
        runtime_policy = (configuration or {}).get("agent_runtime")
        if isinstance(runtime_policy, dict):
            safe_policy = {}
            if runtime_policy.get("reasoning_effort") in ("low", "mid", "high"):
                safe_policy["reasoning_effort"] = runtime_policy["reasoning_effort"]
            if type(runtime_policy.get("max_turns")) is int and runtime_policy["max_turns"] > 0:
                safe_policy["max_turns"] = runtime_policy["max_turns"]
            if safe_policy:
                safe_config["agent_runtime"] = safe_policy
        import platform as python_platform

        packages = {"web": "playwright", "android": "uiautomator2", "windows": "pywinauto", "macos": "Appium-Python-Client"}
        try:
            backend_version = version(packages[metadata.platform])
        except PackageNotFoundError:
            backend_version = None
        source_update = persisted.source
        if entries:
            source_update = source_update.model_copy(update={"snapshot_path": entries[0]["path"], "digest": entries[0]["sha256"]})
        provenance = {
            "sources": entries,
            "producer": {
                "fsq_version": producer,
                "fsq_version_unavailable_reason": None if producer else "package_not_installed",
                "python_version": python_platform.python_version(),
                "backend": safe_config.get("backend"),
                "backend_package": packages[metadata.platform],
                "backend_version": backend_version,
                "backend_version_unavailable_reason": None if backend_version else "package_not_installed",
                "application_revision": safe_config.get("app_version"),
                "application_revision_unavailable_reason": None if safe_config.get("app_version") else "not_reported",
                "repository_revision": safe_config.get("git_revision"),
                "repository_revision_unavailable_reason": None if safe_config.get("git_revision") else "not_reported",
            },
            "configuration": RunLifecycleService.safe_value(safe_config, secret_values),
        }
        updated = persisted.model_copy(update={"provenance": provenance, "source": source_update, "revision": persisted.revision + 1})
        write_run_metadata(run_dir, updated)
        return updated

    @staticmethod
    def freeze(
        run_dir: Path,
        metadata: RunMetadata,
        *,
        bundle,
        verification=None,
        status: str | None = None,
        summary: str | None = None,
        fatal_errors=(),
        secret_values: tuple[str, ...] = (),
        duration_ms: int | None = None,
    ):
        from fsq_agent.models import RunExecutionResult

        if bundle.run_id != metadata.run_id:
            raise ValueError("Evidence Run identity does not match metadata.")
        persisted = load_run_metadata(run_dir)
        if persisted.status in _TERMINAL:
            raise ValueError("Terminal Run metadata is immutable.")
        if (persisted.run_id, persisted.platform, persisted.mode) != (metadata.run_id, metadata.platform, metadata.mode):
            raise ValueError("Run identity changed before freeze.")
        path = run_dir / "execution-result.json"
        if path.exists():
            raise ValueError("Frozen execution result is immutable.")
        step_values = RunLifecycleService.safe_value([step.model_dump(mode="json") for step in bundle.steps], secret_values)
        leaves: dict[tuple[str, str], dict[str, object]] = {}
        attempts = 0
        for step in step_values:
            info = step.get("metadata") or {}
            if info.get("hook_action_name") == "runCase":
                continue
            source = step.get("source_step_id") or step["step_id"]
            invocation = step.get("invocation_path") or info.get("invocation_path") or "root"
            leaves[(str(source), str(invocation))] = step
            if step.get("step_execution_id") or step["status"] not in {"skipped", "incomplete"}:
                attempts += 1
        counts = dict.fromkeys(("passed", "failed", "skipped", "cancelled", "incomplete"), 0)
        for step in leaves.values():
            counts[str(step["status"]) if step["status"] in counts else "incomplete"] += 1
        counted = RunStepCounts(total=len(leaves), attempt_count=attempts, **counts)
        first = next((step for step in leaves.values() if step["status"] == "failed" or (step["status"] == "skipped" and (step.get("failure_category") or step.get("step_execution_id")))), None)
        evidence_errors = [error for step in step_values for error in step.get("evidence_errors", [])]
        evidence_failed = bool(evidence_errors) or any(step.get("failure_category") == "artifact_error" for step in step_values)
        evidence_failed = evidence_failed or bundle.completeness in {"partial", "unavailable"} or any(ref.availability not in {"available", "not_applicable"} for ref in bundle.artifacts)
        for step in bundle.steps:
            policy = step.metadata.get("evidence_policy") or {}
            if step.step_execution_id is None and step.status in {"skipped", "incomplete"}:
                continue
            for phase, enabled in (("prepare", policy.get("capture_before")), ("finalize", policy.get("capture_after"))):
                if not enabled:
                    continue
                refs = [ref for item in step.phase_reports if item.phase == phase for ref in item.artifact_refs]
                for kind in policy.get("artifact_kinds", []):
                    if not any(
                        (ref.kind == kind or ref.metadata.get("requested_kind") == kind or ref.metadata.get("requested_artifact_kind") == kind) and ref.availability in {"available", "not_applicable"}
                        for ref in refs
                    ):
                        evidence_failed = True
                        evidence_errors.append({"step_id": step.step_id, "phase": phase, "kind": kind, "reason": "required_capture_missing"})
        evidence = {"status": "partial" if evidence_failed else "complete" if step_values else "not_applicable", "errors": evidence_errors, "warnings": bundle.warnings}
        verdict = verification.model_dump(mode="json") if hasattr(verification, "model_dump") else dict(verification or {"status": "not_requested"})
        outcome = status or ("cancelled" if counts["cancelled"] else "inconclusive" if counts["incomplete"] or not leaves or not attempts else "failed" if counts["failed"] else "success")
        if metadata.mode == "explore" and not status and not counts["cancelled"] and not counts["incomplete"]:
            outcome = verdict.get("status", "inconclusive")
        if metadata.mode == "strict" and outcome == "success" and (first is not None or counts["skipped"] or verdict.get("status") == "failed"):
            outcome = "failed" if first is not None or verdict.get("status") == "failed" else "inconclusive"
        if fatal_errors or status == "error":
            outcome = "error"
        if status == "cancelled" or counts["cancelled"] or verdict.get("status") == "cancelled":
            outcome = "cancelled"
        elif verdict.get("status") == "error":
            outcome = "error"
        elif (counts["incomplete"] and outcome == "success") or (not attempts and verdict.get("status") not in {"success", "passed"} and outcome == "success"):
            outcome = "inconclusive"
        if evidence_failed and outcome == "success":
            outcome = "error"
        if outcome not in {"success", "failed", "inconclusive", "cancelled", "error"}:
            outcome = "inconclusive"
        if metadata.mode == "explore" and outcome == "success":
            first = None
        duration = duration_ms if duration_ms is not None else _duration(run_dir)
        verdict = RunLifecycleService.safe_value(verdict, secret_values)
        frozen = RunExecutionResult(
            run_id=metadata.run_id,
            platform=metadata.platform,
            mode=metadata.mode,
            outcome=outcome,
            summary=_safe_text(summary or verdict.get("summary") or f"Execution {outcome}.", secret_values),
            counts=counted,
            failed_step=first["step_id"] if first else None,
            verification=verdict,
            evidence=evidence,
            primary_failure={"step_id": first["step_id"], "category": first.get("failure_category"), "message": first.get("error_message")} if first else None,
            duration_ms=duration,
            duration_unavailable_reason=None if duration is not None else "owner_clock_unavailable",
            steps=step_values,
        )
        _atomic_json(path, frozen.model_dump(mode="json"), replace=False)
        return frozen

    @staticmethod
    def finalize(
        run_dir: Path, metadata: RunMetadata, *, execution_result, processing: dict[str, dict[str, object]] | None = None, artifacts: RunArtifactIndex | None = None, runtime: RunRuntime | None = None
    ) -> RunMetadata:
        persisted = load_run_metadata(run_dir)
        if persisted.run_id != execution_result.run_id or persisted.platform != execution_result.platform:
            raise ValueError("Frozen execution identity does not match Run.")
        frozen_path = run_dir / "execution-result.json"
        if json.loads(frozen_path.read_text(encoding="utf-8")) != execution_result.model_dump(mode="json"):
            raise ValueError("Frozen execution result changed.")
        if persisted.status in {"preparing", "running"}:
            persisted = transition_run(run_dir, persisted, "finalizing")
        index = artifacts or RunArtifactIndex(
            report=next((name for name in ("report.json", "core-report.json", "report-fallback.json", "execution-result.json") if (run_dir / name).is_file()), None),
            report_markdown=next((name for name in ("report.md", "core-report.md") if (run_dir / name).is_file()), None),
            html_report="report.html" if (run_dir / "report.html").is_file() else None,
            candidate_case=next((name for name in ("recorded.fsq.yaml", "candidate.fsq.yaml") if (run_dir / name).is_file()), None),
            evidence_manifest="evidence-manifest.json" if (run_dir / "evidence-manifest.json").is_file() else None,
            evidence_journal="evidence-events.jsonl" if (run_dir / "evidence-events.jsonl").is_file() else None,
            events="events.jsonl" if (run_dir / "events.jsonl").is_file() else None,
            execution_result="execution-result.json",
        )
        return transition_run(
            run_dir,
            persisted,
            execution_result.outcome,
            execution_result="execution-result.json",
            result=RunResultSummary(summary=execution_result.summary[:2000], steps=execution_result.counts, failed_step=execution_result.failed_step),
            evidence=execution_result.evidence,
            processing=processing or {},
            artifacts=index,
            runtime=runtime or persisted.runtime,
        )

    @staticmethod
    def append_lineage(run_dir: Path, relation: dict[str, object], *, secret_values: tuple[str, ...] = ()) -> None:
        relation = dict(relation)
        if "command_mapping" in relation:
            mappings = []
            allowed = {"command_index", "source_step_id", "step_execution_id", "invocation_path", "current_source_step_id", "current_invocation_path"}
            for item in relation["command_mapping"]:
                if not isinstance(item, dict) or not isinstance(item.get("command_index"), int) or isinstance(item["command_index"], bool) or item["command_index"] < 0:
                    raise ValueError("Invalid lineage command mapping.")
                clean = {key: value for key, value in item.items() if key in allowed}
                for key in ("source_step_id", "step_execution_id", "current_source_step_id"):
                    if key in clean and (not isinstance(clean[key], str) or not clean[key] or len(clean[key]) > 2000):
                        raise ValueError("Invalid lineage source identity.")
                for key in ("invocation_path", "current_invocation_path"):
                    if key in clean and (not isinstance(clean[key], (list, tuple)) or any(not isinstance(part, str) or len(part) > 2000 for part in clean[key])):
                        raise ValueError("Invalid lineage invocation identity.")
                mappings.append(clean)
            relation["command_mapping"] = mappings
        record = {**RunLifecycleService.safe_value(relation, secret_values), "schema_version": "fsq.lineage/v1", "timestamp": datetime.now(UTC).isoformat()}
        path = run_dir / "lineage.jsonl"
        _contained_destination(run_dir, path)
        with path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, ensure_ascii=False) + "\n")
            stream.flush()
            os.fsync(stream.fileno())


def _process_start(pid: int) -> str | None:
    import subprocess
    import sys

    if sys.platform == "win32":
        try:
            import ctypes
            from ctypes import wintypes

            handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
            if not handle:
                return None
            created, exited, kernel, user = (wintypes.FILETIME() for _ in range(4))
            try:
                if ctypes.windll.kernel32.GetProcessTimes(handle, ctypes.byref(created), ctypes.byref(exited), ctypes.byref(kernel), ctypes.byref(user)):
                    return str((created.dwHighDateTime << 32) | created.dwLowDateTime)
            finally:
                ctypes.windll.kernel32.CloseHandle(handle)
        except (AttributeError, OSError):
            return None
        return None
    try:
        result = subprocess.run(["/bin/ps", "-o", "lstart=", "-p", str(pid)], capture_output=True, text=True, timeout=2, check=False)  # noqa: S603 - fixed program/options and integer process identity.
        return result.stdout.strip() or None
    except (OSError, subprocess.TimeoutExpired):
        return None


def _safe_text(text: str, secrets_to_remove: tuple[str, ...] = ()) -> str:
    for value in sorted((item for item in secrets_to_remove if item), key=len, reverse=True):
        text = text.replace(value, "[REDACTED]")
    text = re.sub(r"(?i)\b(?:Bearer|Basic)\s+[^\s,;]+", "[REDACTED_AUTH]", text)
    text = re.sub(r"(?im)((?:authorization|cookie|password|passwd|api[_-]?key|client[_-]?secret|secret|(?:access_|refresh_|id_)?token)[\"']?\s*[:=]\s*)[^\r\n]+", r"\1[REDACTED]", text)
    text = re.sub(r"(?i)([?&](?:token|access_token|api[_-]?key|secret|password|signature|sig|authorization)=)[^&#\s\"']*", r"\1[REDACTED]", text)
    return _safe_urls(text)


def _safe_source_text(text: str, secret_values: tuple[str, ...] = ()) -> str:
    """Redact complete structured credential values without rewriting unchanged source bytes."""
    import yaml

    replacements = []
    seen = set()
    try:
        scalar_spans = [(token.start_mark.index, token.end_mark.index) for token in yaml.scan(text) if isinstance(token, yaml.ScalarToken)]
        for match in re.finditer(r"(?m)(?<!\S)#[^\r\n]*", text):
            if not any(start <= match.start() < end for start, end in scalar_spans):
                safe = _safe_text(match.group(), secret_values)
                if safe != match.group():
                    replacements.append((match.start(), match.end(), safe))
    except yaml.YAMLError:
        return _safe_text(text, secret_values)

    def visit(node):
        if node is None or id(node) in seen:
            return
        seen.add(id(node))
        if isinstance(node, yaml.MappingNode):
            for key, value in node.value:
                if isinstance(key, yaml.ScalarNode) and _credential_key(key.value):
                    replacements.append((value.start_mark.index, value.end_mark.index, '"[REDACTED]"'))
                else:
                    visit(value)
        elif isinstance(node, yaml.SequenceNode):
            for value in node.value:
                visit(value)
        elif isinstance(node, yaml.ScalarNode):
            safe = _safe_structure(node.value, secret_values)
            if safe != node.value:
                replacements.append((node.start_mark.index, node.end_mark.index, json.dumps(safe, ensure_ascii=False)))

    try:
        for document in yaml.compose_all(text, Loader=yaml.SafeLoader):
            visit(document)
    except yaml.YAMLError:
        # Unparseable content has no trustworthy structured snapshot to retain.
        candidates = re.findall(r"(?m)^\s*(?:-\s*)?[\"']?([^\s:\"']+)[\"']?\s*:", text)
        if any(_credential_key(key) for key in candidates):
            return "[REDACTED: unparseable sensitive source]"
    for start, end, value in sorted(set(replacements), reverse=True):
        replacement = value + ("\n" if text[start:end].endswith("\n") else "")
        text = text[:start] + replacement + text[end:]
    for secret in sorted((value for value in secret_values if value), key=len, reverse=True):
        text = text.replace(secret, "[REDACTED]")
    text = re.sub(r"(?i)([?&](?:token|access_token|refresh_token|api[_-]?key|secret|password|signature|sig|authorization)=)[^&#\s\"']*", r"\1[REDACTED]", text)
    return _safe_urls(text)


def _safe_urls(text):
    from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

    def replace(match):
        raw = match.group(0)
        try:
            parsed = urlsplit(raw)
            query = parse_qsl(parsed.query, keep_blank_values=True)
            if "@" not in parsed.netloc and not any(_credential_key(key) or key.casefold() in {"signature", "sig"} for key, _ in query):
                return raw
            safe_query = urlencode([(key, "[REDACTED]" if _credential_key(key) or key.casefold() in {"signature", "sig"} else value) for key, value in query])
            return urlunsplit((parsed.scheme, parsed.netloc.rsplit("@", 1)[-1], parsed.path, safe_query, parsed.fragment))
        except ValueError:
            return "[REDACTED_URL]"

    return re.sub(r"https?://[^\s<>\"']+", replace, text, flags=re.I)


def _credential_key(key):
    from urllib.parse import unquote

    normalized = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", unquote(str(key)).strip()).lower().replace("-", "_")
    return normalized in {
        "password",
        "passwd",
        "pwd",
        "api_key",
        "apikey",
        "token",
        "access_token",
        "refresh_token",
        "id_token",
        "client_secret",
        "secret",
        "authorization",
        "proxy_authorization",
        "cookie",
        "set_cookie",
        "private_values",
        "credentials",
    }


def _safe_structure(value, secret_values=(), depth=0):
    if depth > 20:
        return "[REDACTED: nesting limit]"
    if isinstance(value, dict):
        return {key: "[REDACTED]" if _credential_key(key) else _safe_structure(item, secret_values, depth + 1) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_safe_structure(item, secret_values, depth + 1) for item in value]
    if not isinstance(value, str):
        return value
    for secret in sorted((item for item in secret_values if item), key=len, reverse=True):
        value = value.replace(secret, "[REDACTED]")
    if value.strip().startswith(("{", "[", '"')):
        try:
            decoded = json.loads(value)
        except ValueError:
            decoded = None
        if isinstance(decoded, (dict, list, str)) and decoded != value:
            safe = _safe_structure(decoded, secret_values, depth + 1)
            if safe != decoded:
                return json.dumps(safe, ensure_ascii=False)
    value = re.sub(r"(?im)((?:proxy[-_]authorization|authorization|set[-_]cookie|cookie)\s*[:=]\s*)[^\r\n]+", r"\1[REDACTED]", value)
    return _safe_text(value, secret_values)


def _contained_destination(root: Path, destination: Path) -> None:
    resolved_root = root.resolve()
    if not destination.resolve().is_relative_to(resolved_root):
        raise ValueError("Run output must remain contained in its allocated directory.")
    current = destination
    while current != root and current != current.parent:
        if current.is_symlink():
            raise ValueError("Run output must remain contained without symbolic links.")
        current = current.parent


def _duration(run_dir: Path) -> int | None:
    started = _START_TIMES.get(run_dir.resolve())
    return max(0, int((time.perf_counter() - started) * 1000)) if started is not None else None


def _atomic_bytes(path: Path, data: bytes, *, replace: bool = True) -> None:
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if replace:
            Path(temporary).replace(path)
        else:
            os.link(temporary, path)
            Path(temporary).unlink()
    finally:
        Path(temporary).unlink(missing_ok=True)


def _atomic_json(path: Path, value: object, *, replace: bool = True) -> None:
    _atomic_bytes(path, (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode(), replace=replace)
