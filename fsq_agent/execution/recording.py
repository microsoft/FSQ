# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Literal

import yaml

from fsq_agent._capability_bootstrap import build_capability_registry
from fsq_agent.case_dsl import FSQ_CASE_SUFFIX, FsqCaseLoader, FsqCaseSerializer
from fsq_agent.models import ConfigurationError, RunEvent, Task, TaskResult

if TYPE_CHECKING:
    from collections.abc import Mapping

    from fsq_agent.config import Settings

RecordingStatus = Literal["recorded", "skipped", "failed"]
_DEFAULT_PUBLICATION = object()


@dataclass
class _StrictCaseRecording:
    status: RecordingStatus
    recording_path: Path
    recorded_case_path: Path | None = None
    published_case_path: Path | None = None
    command_count: int = 0
    required_runtime_secret_names: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    skipped_tool_calls: list[dict[str, Any]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    validation_status: str = "not_run"
    draft: bool = False
    case_name: str | None = None
    publication_outcome: str = "not_requested"
    provenance: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "recording_path": str(self.recording_path),
            "recorded_case_path": str(self.recorded_case_path) if self.recorded_case_path else None,
            "published_case_path": str(self.published_case_path) if self.published_case_path else None,
            "command_count": self.command_count,
            "required_runtime_secret_names": self.required_runtime_secret_names,
            "warnings": self.warnings,
            "skipped_tool_calls": self.skipped_tool_calls,
            "errors": self.errors,
            "validation_status": self.validation_status,
            "draft": self.draft,
            "case_name": self.case_name,
            "publication_outcome": self.publication_outcome,
            **self.provenance,
        }


@dataclass(frozen=True)
class RecordingResult:
    status: RecordingStatus
    recording_path: Path
    recorded_case_path: Path | None
    published_case_path: Path | None
    command_count: int
    required_runtime_secret_names: tuple[str, ...]
    warnings: tuple[str, ...]
    skipped_tool_calls: tuple[Mapping[str, str], ...]
    errors: tuple[str, ...]
    validation_status: str
    draft: bool
    case_name: str | None = None
    publication_outcome: str = "not_requested"

    def __post_init__(self) -> None:
        object.__setattr__(self, "skipped_tool_calls", tuple(MappingProxyType(dict(item)) for item in self.skipped_tool_calls))


def _recording_result_from_state(recording: _StrictCaseRecording) -> RecordingResult:
    return RecordingResult(
        status=recording.status,
        recording_path=recording.recording_path,
        recorded_case_path=recording.recorded_case_path,
        published_case_path=recording.published_case_path,
        command_count=recording.command_count,
        required_runtime_secret_names=tuple(recording.required_runtime_secret_names),
        warnings=tuple(recording.warnings),
        skipped_tool_calls=tuple(recording.skipped_tool_calls),
        errors=tuple(recording.errors),
        validation_status=recording.validation_status,
        draft=recording.draft,
        case_name=recording.case_name,
        publication_outcome=recording.publication_outcome,
    )


class RecordingService:
    @staticmethod
    def validate_case_name(value: str) -> str:
        return _valid_case_name(value)

    def record(
        self,
        *,
        run_dir: Path,
        task: Task,
        result: TaskResult,
        settings: Settings,
        allow_failure: bool = False,
        publication_directory: Path | None = None,
        case_name: str | None = None,
    ) -> RecordingResult:
        recording = _record_dynamic_run_as_strict_case(
            run_dir=run_dir,
            task=task,
            result=result,
            settings=settings,
            allow_failure=allow_failure,
            publication_directory=publication_directory,
            case_name=case_name,
        )
        return _recording_result_from_state(recording)


def _record_dynamic_run_as_strict_case(
    *,
    run_dir: Path,
    task: Task,
    result: TaskResult,
    settings: Settings,
    allow_failure: bool = False,
    publication_directory: Path | object | None = _DEFAULT_PUBLICATION,
    case_name: str | None = None,
) -> _StrictCaseRecording:
    run_dir.mkdir(parents=True, exist_ok=True)
    recording_path = run_dir / "recording.json"
    recorded_case_path = run_dir / "recorded.fsq.yaml"
    draft = result.status != "success"
    if draft and not allow_failure:
        recording = _StrictCaseRecording(
            status="skipped",
            recording_path=recording_path,
            warnings=["Run did not finish successfully; use --record-on-failure to write a draft recording."],
            draft=draft,
        )
        _write_recording(recording)
        return recording
    if recorded_case_path.exists():
        recording = _StrictCaseRecording(
            status="failed",
            recording_path=recording_path,
            recorded_case_path=recorded_case_path,
            errors=["recorded.fsq.yaml already exists for this run."],
            draft=draft,
        )
        _write_recording(recording)
        return recording

    collector = _RecordingCollector()
    events = _load_events(run_dir / "events.jsonl")
    commands = collector.collect(events)
    if not commands:
        recording = _StrictCaseRecording(
            status="failed",
            recording_path=recording_path,
            errors=["No replayable commands were found in the dynamic run event log."],
            skipped_tool_calls=collector.skipped_tool_calls,
            warnings=collector.warnings,
            draft=draft,
        )
        _write_recording(recording)
        return recording

    required_secret_names = sorted(collector.required_runtime_secret_names)
    warnings = list(collector.warnings)
    metadata_doc = _metadata_doc(task, result, settings, required_secret_names, warnings, draft)
    if case_name is not None:
        metadata_doc["name"] = _valid_case_name(case_name)

    recording = _StrictCaseRecording(
        status="recorded",
        recording_path=recording_path,
        recorded_case_path=recorded_case_path,
        command_count=len(commands),
        required_runtime_secret_names=required_secret_names,
        warnings=warnings,
        skipped_tool_calls=collector.skipped_tool_calls,
        validation_status="not_run",
        draft=draft,
        case_name=metadata_doc["name"],
        provenance={"source_run_id": result.report.run_id, "source_task_id": task.id, "source_status": result.status},
    )
    try:
        generated_case = FsqCaseLoader().load_text(yaml.safe_dump_all([metadata_doc, commands], sort_keys=False), recorded_case_path)
        content = FsqCaseSerializer(build_capability_registry(platform=settings.harness.platform).snapshot()).serialize(generated_case)
        _atomic_bytes(recorded_case_path, content)
        recording.validation_status = "passed"
    except (ConfigurationError, OSError) as exc:
        recording.status = "failed"
        recording.validation_status = "failed"
        recording.errors.append(str(exc) if isinstance(exc, ConfigurationError) else "Unable to persist recorded Case.")
        recording.recorded_case_path = None
    effective_publication_directory = settings.cases.dir if publication_directory is _DEFAULT_PUBLICATION else publication_directory
    if effective_publication_directory is not None and recording.status == "recorded" and recording.validation_status == "passed" and task.planning_reference_kind == "goal":
        try:
            publication_root = effective_publication_directory.resolve()
            recording.published_case_path, recording.publication_outcome = publish_recorded_case(
                candidate_path=recorded_case_path, destination_directory=publication_root, platform=settings.harness.platform, case_name=metadata_doc["name"]
            )
            if recording.publication_outcome in {"conflict", "failed"}:
                recording.warnings.append("case.publication_conflict" if recording.publication_outcome == "conflict" else "case.publication_failed")
        except (ConfigurationError, OSError):
            recording.publication_outcome = "failed"
            recording.published_case_path = None
            recording.warnings.append("case.publication_failed")
    _write_recording(recording)
    return recording


class _RecordingCollector:
    def __init__(self) -> None:
        self.required_runtime_secret_names: set[str] = set()
        self.warnings: list[str] = []
        self.skipped_tool_calls: list[dict[str, Any]] = []

    def collect(self, events: list[RunEvent]) -> list[dict[str, Any]]:
        commands: list[dict[str, Any]] = []
        starts_by_call_id: dict[str, RunEvent] = {}
        unpaired_starts: list[RunEvent] = []
        for event in events:
            if event.type == "tool_call_started":
                if event.tool_call_id:
                    starts_by_call_id[event.tool_call_id] = event
                else:
                    unpaired_starts.append(event)
                continue
            if event.type not in {"tool_call_completed", "tool_call_failed"}:
                continue

            if self._has_replayable_common_policy(event):
                self._collect_common(event, commands)
                continue

            start = starts_by_call_id.get(event.tool_call_id or "") if event.tool_call_id else self._pop_unpaired_start(unpaired_starts, event)
            if start is None:
                continue
            if self._origin(start, event) not in {"platform", "harness"}:
                continue
            self._collect_harness(start, event, commands)
        return commands

    def _collect_common(self, event: RunEvent, commands: list[dict[str, Any]]) -> None:
        payload = event.payload
        if event.type != "tool_call_completed":
            self._skip(event.tool_name, "common tool did not complete successfully")
            return
        replay = self._replay_policy(payload)
        if replay.get("kind") == "fsq_command" and replay.get("alias") == "waitMs":
            duration_ms = payload.get("duration_ms")
            if not isinstance(duration_ms, int):
                self._skip(event.tool_name, "wait_ms event did not include duration_ms")
                return
            params: dict[str, Any] = {"duration_ms": duration_ms}
            reason = payload.get("reason")
            if isinstance(reason, str) and reason:
                params["reason"] = reason
            commands.append({"waitMs": params})
            return
        self._skip(event.tool_name, "common tool replay metadata is not recorded as a command", warn=False)

    def _collect_harness(self, start: RunEvent, event: RunEvent, commands: list[dict[str, Any]]) -> None:
        payload = event.payload
        replay = self._replay_policy(payload) or self._replay_policy(start.payload)
        if replay.get("kind") != "fsq_command":
            self._skip(start.tool_name, "platform tool did not include fsq_command replay metadata")
            return
        if self._step_kind(start, event) == "observation":
            self._skip(start.tool_name, "observation tool is not recorded", warn=False)
            return
        fsq_action_name = replay.get("alias")
        if not isinstance(fsq_action_name, str) or not fsq_action_name:
            self._skip(start.tool_name, "platform tool did not include fsq_command replay alias")
            return
        if event.type != "tool_call_completed" or payload.get("status") not in {"passed", "success", None}:
            self._skip(start.tool_name, f"platform action status was {payload.get('status') or event.type}")
            return
        safe_replay_params = payload.get("safe_replay_params")
        args = dict(safe_replay_params) if isinstance(safe_replay_params, dict) else _event_arguments(start.tool_arguments)
        if args is None:
            self._skip(start.tool_name, "platform tool arguments were not a JSON object")
            return
        self._collect_runtime_secret_names(args)
        commands.append({fsq_action_name: args})

    def _collect_runtime_secret_names(self, value: Any) -> None:
        if isinstance(value, dict):
            text_type = value.get("textType")
            text = value.get("text")
            if text_type == "runtimeSecret" and isinstance(text, str) and text.strip():
                self.required_runtime_secret_names.add(text.strip())
                return
            for item in value.values():
                self._collect_runtime_secret_names(item)
            return
        if isinstance(value, list):
            for item in value:
                self._collect_runtime_secret_names(item)

    def _origin(self, start: RunEvent, event: RunEvent) -> str:
        origin = start.payload.get("tool_origin") or event.payload.get("tool_origin")
        return str(origin) if origin else "unknown"

    def _has_replayable_common_policy(self, event: RunEvent) -> bool:
        return event.payload.get("tool_origin") == "common" and bool(self._replay_policy(event.payload))

    def _replay_policy(self, payload: dict[str, Any]) -> dict[str, Any]:
        replay = payload.get("replay")
        if isinstance(replay, dict):
            return replay
        replay_kind = payload.get("replay_kind")
        # Legacy event logs used replay_kind before structured replay metadata existed.
        # New recorder behavior must not infer replayability from tool names or fsq_action_name.
        if replay_kind == "waitMs":
            return {"kind": "fsq_command", "alias": "waitMs"}
        return {}

    def _step_kind(self, start: RunEvent, event: RunEvent) -> str | None:
        for payload in (event.payload, start.payload):
            value = payload.get("step_kind")
            if isinstance(value, str):
                return value
            metadata = payload.get("metadata")
            if isinstance(metadata, dict):
                value = metadata.get("step_kind")
                if isinstance(value, str):
                    return value
        return None

    def _pop_unpaired_start(self, starts: list[RunEvent], event: RunEvent) -> RunEvent | None:
        if event.tool_name:
            for index, start in enumerate(starts):
                if start.tool_name == event.tool_name:
                    return starts.pop(index)
        return starts.pop(0) if starts else None

    def _skip(self, tool_name: str | None, reason: str, *, warn: bool = True) -> None:
        self.skipped_tool_calls.append({"tool_name": tool_name or "unknown", "reason": reason})
        if warn:
            self.warnings.append(f"Skipped {tool_name or 'unknown'}: {reason}")


def _load_events(path: Path) -> list[RunEvent]:
    if not path.exists():
        return []
    events: list[RunEvent] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            events.append(RunEvent.model_validate_json(line))
        except ValueError:
            continue
    return events


def _event_arguments(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return None
        return parsed if isinstance(parsed, dict) else None
    return None


def _metadata_doc(
    task: Task,
    result: TaskResult,
    settings: Settings,
    required_secret_names: list[str],
    warnings: list[str],
    draft: bool,
) -> dict[str, Any]:
    app_id = settings.harness.android.app_id if settings.harness.platform == "android" else None
    reference = task.planning_reference_text or ""
    goal = " ".join((reference if reference.strip() else task.name).split())
    identity = [settings.harness.platform, goal] if task.planning_reference_kind == "goal" else [settings.harness.platform, task.name, task.description]
    name = "case-" + hashlib.sha256(json.dumps(identity, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()
    doc: dict[str, Any] = {
        "schemaVersion": "fsq.ai-test/v1",
        "name": name,
        "description": goal if task.planning_reference_kind == "goal" else task.description,
        "platform": settings.harness.platform,
        "tags": ["recorded", "dynamic-llm"],
    }
    if app_id:
        doc["appId"] = app_id
    return doc


def _write_recording(recording: _StrictCaseRecording) -> None:
    recording.recording_path.write_text(json.dumps(recording.to_json(), indent=2, ensure_ascii=False), encoding="utf-8")


def _valid_case_name(value: str) -> str:
    name = value.strip()
    if not name or name.startswith(".") or ".." in name or name.casefold().endswith(FSQ_CASE_SUFFIX) or any(char in '/\\:*?"<>|[]' or ord(char) < 32 or 127 <= ord(char) <= 159 for char in name):
        raise ConfigurationError("Invalid Case name.", context={"code": "case.name"})
    return name


def _atomic_bytes(path: Path, content: bytes, *, create_only: bool = False) -> None:
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=f".{path.name}.", delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        if create_only:
            os.link(temporary, path)
        else:
            temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def publish_recorded_case(*, candidate_path: Path, destination_directory: Path, platform: str, case_name: str) -> tuple[Path | None, str]:
    name = _valid_case_name(case_name)
    root = destination_directory.resolve()
    destination = root / f"{name}{FSQ_CASE_SUFFIX}"
    if destination.is_symlink() or destination.resolve().parent != root:
        raise ConfigurationError("Case publication must remain in its directory.")
    case = FsqCaseLoader().load_case(candidate_path)
    if case.config.platform != platform:
        raise ConfigurationError("Case platform mismatch.")
    case = case.model_copy(update={"config": case.config.model_copy(update={"name": name})})
    content = FsqCaseSerializer(build_capability_registry(platform=platform).snapshot()).serialize(case)
    try:
        root.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            return (destination, "unchanged") if destination.read_bytes() == content else (None, "conflict")
        try:
            _atomic_bytes(destination, content, create_only=True)
        except FileExistsError:
            return (destination, "unchanged") if destination.read_bytes() == content else (None, "conflict")
    except OSError:
        return None, "failed"
    return destination, "created"
