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
from fsq_agent.case_dsl import FSQ_CASE_SUFFIX, FsqCaseLoader, FsqCaseSerializer, FsqExecutableStepAdapter
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
    publication_status: Literal["not_requested", "success", "failed"] = "not_requested"
    publication_errors: list[str] = field(default_factory=list)
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
            "publication_status": self.publication_status,
            "publication_errors": self.publication_errors,
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
    publication_status: Literal["not_requested", "success", "failed"] = "not_requested"
    publication_errors: tuple[str, ...] = ()

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
        publication_status=recording.publication_status,
        publication_errors=tuple(recording.publication_errors),
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
        if recording.recorded_case_path and recording.recorded_case_path.is_file():
            from .runs import RunLifecycleService

            digest = hashlib.sha256(recording.recorded_case_path.read_bytes()).hexdigest()
            relation = {
                "kind": "recording",
                "originating_run_id": result.report.run_id,
                "case_digest": digest,
                "candidate_path": recording.recorded_case_path.name,
                "validation_status": recording.validation_status,
                "review_status": "unknown",
            }
            if recording.published_case_path:
                relation["saved_case_path"] = recording.published_case_path.name
            RunLifecycleService.append_lineage(run_dir, relation)
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
    if (run_dir / "evidence-events.jsonl").is_file():
        from .runs import RunLifecycleService

        commands, command_mapping = _commands_from_evidence(RunLifecycleService.read_evidence(run_dir), collector)
    else:
        commands = collector.collect(events)
        command_mapping = []
    incomplete = any(item.get("reason") == "sensitive_parameters_redacted" for item in collector.skipped_tool_calls)
    draft = draft or incomplete
    if not commands:
        recording = _StrictCaseRecording(
            status="failed",
            recording_path=recording_path,
            errors=["No replayable commands were found in the dynamic run event log."],
            skipped_tool_calls=collector.skipped_tool_calls,
            warnings=collector.warnings,
            draft=draft,
            publication_status="failed" if publication_directory is not None and incomplete else "not_requested",
            publication_errors=["Incomplete redacted recording cannot be published."] if publication_directory is not None and incomplete else [],
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
        provenance={
            "source_run_id": result.report.run_id,
            "source_task_id": task.id,
            "source_status": result.status,
            "command_mapping": command_mapping,
        },
    )
    try:
        generated_case = FsqCaseLoader().load_text(yaml.safe_dump_all([metadata_doc, commands], sort_keys=False), recorded_case_path)
        content = FsqCaseSerializer(build_capability_registry(platform=settings.harness.platform).snapshot()).serialize(generated_case)
        _atomic_bytes(recorded_case_path, content)
        FsqExecutableStepAdapter(registry_snapshot=build_capability_registry(platform=settings.harness.platform).snapshot()).to_executable_steps(generated_case)
        recording.validation_status = "passed"
    except (ConfigurationError, OSError) as exc:
        recording.status = "failed"
        recording.validation_status = "failed"
        recording.errors.append(str(exc) if isinstance(exc, ConfigurationError) else "Unable to persist recorded Case.")
        recording.recorded_case_path = None
    effective_publication_directory = settings.cases.dir if publication_directory is _DEFAULT_PUBLICATION else publication_directory
    _write_recording(recording)
    if effective_publication_directory is not None and recording.status == "recorded" and recording.validation_status == "passed" and task.planning_reference_kind == "goal" and not incomplete:
        try:
            recording.published_case_path, recording.publication_outcome = publish_recorded_case(
                candidate_path=recorded_case_path,
                destination_directory=effective_publication_directory.resolve(),
                platform=settings.harness.platform,
                case_name=metadata_doc["name"],
            )
        except (ConfigurationError, OSError):
            recording.published_case_path, recording.publication_outcome = None, "failed"
        recording.publication_status = "success" if recording.published_case_path is not None else "failed"
        if recording.published_case_path is None:
            recording.publication_errors.append("Unable to publish recorded Case.")
            recording.warnings.append("case.publication_conflict" if recording.publication_outcome == "conflict" else "case.publication_failed")
    elif effective_publication_directory is not None and incomplete:
        recording.publication_status = "failed"
        recording.publication_errors.append("Incomplete redacted recording cannot be published.")
    _write_recording(recording)
    return recording


def _publish_goal_recording(*, recorded_case_path: Path, published_case_path: Path, warnings: list[str]) -> Path | None:
    temporary_path: Path | None = None
    try:
        published_case_path.parent.mkdir(parents=True, exist_ok=True)
        with (
            recorded_case_path.open("rb") as source,
            tempfile.NamedTemporaryFile(
                mode="wb",
                dir=published_case_path.parent,
                prefix=f".{published_case_path.name}.",
                suffix=".tmp",
                delete=False,
            ) as temporary,
        ):
            temporary_path = Path(temporary.name)
            while chunk := source.read(1024 * 1024):
                temporary.write(chunk)
            temporary.flush()
            os.fsync(temporary.fileno())
        temporary_path.replace(published_case_path)
    except OSError:
        warnings.append("Unable to publish the recorded Goal case to the selected platform cases directory.")
        return None
    else:
        temporary_path = None
        return published_case_path
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink()
            except OSError:
                pass


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
    from .runs import RunLifecycleService

    secrets = tuple(settings.runtime_secrets.private_values().values())
    app_id = settings.harness.android.app_id if settings.harness.platform == "android" else None
    reference = task.planning_reference_text or ""
    goal = " ".join((reference if reference.strip() else task.name).split())
    identity = [settings.harness.platform, goal] if task.planning_reference_kind == "goal" else [settings.harness.platform, task.name, task.description]
    name = "case-" + hashlib.sha256(json.dumps(identity, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()
    description = goal if task.planning_reference_kind == "goal" else task.description
    doc: dict[str, Any] = {
        "schemaVersion": "fsq.ai-test/v1",
        "name": RunLifecycleService.safe_text(name, secrets),
        "description": RunLifecycleService.safe_text(description, secrets),
        "platform": settings.harness.platform,
        "tags": ["recorded", "dynamic-llm"],
    }
    if app_id:
        doc["appId"] = app_id
    return doc


def _write_recording(recording: _StrictCaseRecording) -> None:
    path = recording.recording_path
    root = path.parent.resolve()
    if path.is_symlink() or not path.resolve().is_relative_to(root):
        raise ValueError("Recording manifest must remain contained in its Run.")
    descriptor, temporary = tempfile.mkstemp(prefix=".recording-", suffix=".tmp", dir=root)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(json.dumps(recording.to_json(), indent=2, ensure_ascii=False).encode("utf-8") + b"\n")
            stream.flush()
            os.fsync(stream.fileno())
        if path.is_symlink() or not path.resolve().is_relative_to(root):
            raise ValueError("Recording manifest must remain contained in its Run.")
        Path(temporary).replace(path)
    finally:
        Path(temporary).unlink(missing_ok=True)


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
    candidate_bytes = candidate_path.read_bytes()
    case = FsqCaseLoader().load_case(candidate_path)
    if candidate_path.read_bytes() != candidate_bytes:
        raise ConfigurationError("Recording source changed during publication.")
    if case.config.platform != platform:
        raise ConfigurationError("Case platform mismatch.")
    case = case.model_copy(update={"config": case.config.model_copy(update={"name": name})})
    content = FsqCaseSerializer(build_capability_registry(platform=platform).snapshot()).serialize(case)
    outcome = "created"
    try:
        root.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            if destination.read_bytes() != content:
                return None, "conflict"
            outcome = "unchanged"
        else:
            try:
                _atomic_bytes(destination, content, create_only=True)
            except FileExistsError:
                if destination.read_bytes() != content:
                    return None, "conflict"
                outcome = "unchanged"
        _append_publication_lineage(candidate_path, candidate_bytes, destination, content)
    except OSError:
        return None, "failed"
    return destination, outcome


def _append_publication_lineage(candidate_path, candidate_bytes, destination, content):
    from .runs import RunLifecycleService

    root = candidate_path.parent.resolve()
    recording_path = root / "recording.json"
    if not recording_path.is_file():
        return
    if candidate_path.is_symlink() or recording_path.is_symlink() or candidate_path.read_bytes() != candidate_bytes:
        raise ConfigurationError("Recording source changed during publication.")
    recording = json.loads(recording_path.read_text(encoding="utf-8"))
    if not recording.get("source_run_id") and not recording.get("command_mapping"):
        return
    mapping = recording.get("command_mapping")
    case = FsqCaseLoader().load_text(candidate_bytes.decode("utf-8"), candidate_path)
    if recording.get("source_run_id") != root.name or not isinstance(mapping, list):
        raise ConfigurationError("Recording source identity is invalid.")
    if mapping and [item.get("command_index") for item in mapping] != list(range(len(case.commands))):
        raise ConfigurationError("Recording command mapping is invalid.")
    digest = hashlib.sha256(content).hexdigest()
    snapshot_dir = root / "saved-cases"
    if snapshot_dir.is_symlink():
        raise ConfigurationError("Saved Case snapshots must remain in the Run.")
    snapshot_dir.mkdir(exist_ok=True)
    snapshot = snapshot_dir / f"{digest}{FSQ_CASE_SUFFIX}"
    if snapshot.is_symlink():
        raise ConfigurationError("Saved Case snapshot is unsafe.")
    try:
        _atomic_bytes(snapshot, content, create_only=True)
    except FileExistsError:
        if snapshot.read_bytes() != content:
            raise ConfigurationError("Saved Case snapshot identity conflicts.") from None
    RunLifecycleService.append_lineage(
        root,
        {
            "kind": "recording",
            "originating_run_id": root.name,
            "candidate_digest": hashlib.sha256(candidate_bytes).hexdigest(),
            "candidate_path": candidate_path.name,
            "case_digest": digest,
            "saved_case_path": destination.name,
            "saved_snapshot_path": snapshot.relative_to(root).as_posix(),
            "command_mapping": mapping,
            "validation_status": "passed",
            "review_status": "unknown",
        },
    )


def _commands_from_evidence(bundle, collector):
    commands = []
    mapping = []
    for result in bundle.steps:
        invoke = next((phase for phase in result.phase_reports if phase.phase == "invoke"), None)
        if result.status != "passed" or invoke is None:
            continue
        metadata = invoke.metadata
        replay = metadata.get("replay")
        if not isinstance(replay, dict) or replay.get("kind") != "fsq_command" or metadata.get("step_kind") in {"observation", "diagnostic"}:
            continue
        params = metadata.get("safe_replay_params", {})
        if metadata.get("replay_unavailable_reason") == "sensitive_parameters_redacted":
            collector.skipped_tool_calls.append({"step_id": result.step_id, "reason": "sensitive_parameters_redacted"})
            collector.warnings.append("A recorded action has redacted parameters; the Case is an incomplete draft.")
            continue
        if not isinstance(params, dict):
            continue
        alias = replay.get("alias")
        if not isinstance(alias, str) or not alias:
            continue
        collector._collect_runtime_secret_names(params)
        commands.append({alias: params})
        mapping.append(
            {"command_index": len(commands) - 1, "source_step_id": result.source_step_id, "step_execution_id": result.step_execution_id or result.step_id, "invocation_path": result.invocation_path}
        )
    return commands, mapping
