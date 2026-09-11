# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import hashlib
import json
import os
import re
import tempfile
import threading
import uuid
from pathlib import Path

from fsq_agent.models import EvidenceArtifactRef, EvidenceBundle, EvidenceJournalRecord, ExecutableStep, RunnerEvent, RunnerStepResult, SourceRef, StepPhaseReport


class EvidenceRecorder:
    def __init__(self, *, run_id: str, output_dir: Path, bundle_id: str | None = None, metadata: dict[str, object] | None = None, secret_values: tuple[str, ...] = ()) -> None:
        self.run_id = run_id
        self.output_dir = output_dir.resolve()
        self.bundle_id = bundle_id or f"{run_id}-evidence"
        self._secret_values = tuple(value for value in secret_values if value)
        self.metadata = self._sanitize(metadata or {})
        self._events: list[RunnerEvent] = []
        self._steps: list[RunnerStepResult] = []
        self._planned: list[ExecutableStep] = []
        self._planned_keys: set[tuple[str, tuple[str, ...]]] = set()
        self._sequence = 0
        self._identities: set[str] = set()
        self._occurrences: dict[str, int] = {}
        self._lock = threading.RLock()
        self._write_failed = False
        if (self.output_dir / "evidence-events.jsonl").exists():
            raise ValueError("An evidence journal already exists for this Run; use read-only recovery.")

    def allocate_step_identity(self, step: ExecutableStep) -> ExecutableStep:
        with self._lock:
            if self._write_failed:
                error = OSError("Run evidence is unavailable after a persistence failure.")
                error.fsq_evidence_fatal = True
                raise error
            source = step.source_step_id or step.step_id
            occurrence = self._occurrences.get(source, 0) + 1
            self._occurrences[source] = occurrence
            identity = step.step_execution_id or step.step_id
            if identity in self._identities:
                if step.step_execution_id:
                    raise ValueError("Step execution identity is already allocated.")
                identity = f"{step.step_id}-invocation-{occurrence}-attempt-{step.attempt_index}"
                while identity in self._identities:
                    identity = f"{identity}-{uuid.uuid4().hex[:8]}"
            self._identities.add(identity)
            path = step.invocation_path or (source, str(occurrence))
            return step.model_copy(update={"source_step_id": source, "step_id": identity, "step_execution_id": identity, "invocation_path": path})

    @property
    def write_failed(self) -> bool:
        return self._write_failed

    def record_planned_step(self, step: ExecutableStep) -> None:
        with self._lock:
            key = (step.source_step_id or step.step_id, step.invocation_path)
            if key in self._planned_keys:
                return
            self._append(planned_step=step)
            self._planned.append(step)
            self._planned_keys.add(key)

    def record_event(self, event: RunnerEvent) -> None:
        if event.run_id != self.run_id:
            raise ValueError("Evidence journal Run identity mismatch.")
        with self._lock:
            _validate_execution_facts([*self._events, event], self._steps)
            self._append(event=event)
            self._events.append(event)

    def record_step_result(self, result: RunnerStepResult) -> None:
        with self._lock:
            _validate_execution_facts(self._events, [*self._steps, result])
            self._append(step_result=result)
            self._steps.append(result)
            self.write_manifest()

    def _append(self, **fact) -> None:
        if self._write_failed:
            error = OSError("Evidence journal is unavailable after a failed append.")
            error.fsq_evidence_fatal = True
            raise error
        try:
            self.output_dir.mkdir(parents=True, exist_ok=True)
            path = self._contained("evidence-events.jsonl")
            record = EvidenceJournalRecord(sequence=self._sequence + 1, event_id=uuid.uuid4().hex, run_id=self.run_id, **fact)
            record = EvidenceJournalRecord.model_validate(self._sanitize(record.model_dump(mode="json")))
            with path.open("ab") as stream:
                stream.write(record.model_dump_json().encode("utf-8") + b"\n")
                stream.flush()
                os.fsync(stream.fileno())
        except OSError as exc:
            self._write_failed = True
            exc.fsq_evidence_fatal = True
            raise
        self._sequence = record.sequence

    def _sanitize(self, value):
        return _credential_safe(value, self._secret_values)

    def build_bundle(self) -> EvidenceBundle:
        with self._lock:
            bundle = _bundle(self.bundle_id, self.run_id, self._events, self._steps, self._planned, self._sequence, self.metadata, include_unresolved=True)
            return EvidenceBundle.model_validate(self._sanitize(bundle.model_dump(mode="json")))

    def _contained(self, filename: str) -> Path:
        path = self.output_dir / filename
        if Path(filename).name != filename or path.is_symlink() or not path.resolve().is_relative_to(self.output_dir):
            raise ValueError("Evidence path must remain contained in the Run.")
        return path

    def write_manifest(self, filename: str = "evidence-manifest.json") -> Path:
        try:
            return self._write_manifest(filename)
        except OSError as exc:
            self._write_failed = True
            exc.fsq_evidence_fatal = True
            raise

    def _write_manifest(self, filename: str) -> Path:
        with self._lock:
            self.output_dir.mkdir(parents=True, exist_ok=True)
            path = self._contained(filename)
            bundle = self.build_bundle().model_copy(update={"manifest_path": Path(filename)})
            descriptor, temporary = tempfile.mkstemp(prefix=".evidence-", suffix=".tmp", dir=self.output_dir)
            try:
                with os.fdopen(descriptor, "wb") as stream:
                    stream.write(bundle.model_dump_json(indent=2).encode("utf-8") + b"\n")
                    stream.flush()
                    os.fsync(stream.fileno())
                Path(temporary).replace(path)
            finally:
                Path(temporary).unlink(missing_ok=True)
            return path

    @classmethod
    def recover_bundle(cls, run_dir: Path) -> EvidenceBundle:
        root = run_dir.resolve()
        manifest = root / "evidence-manifest.json"
        journal = root / "evidence-events.jsonl"
        if manifest.is_symlink() or journal.is_symlink():
            raise ValueError("Evidence files must remain contained in the Run.")
        checkpoint = None
        compatibility_warnings = []
        manifest_bytes = manifest.read_bytes() if manifest.is_file() else None
        journal_bytes = journal.read_bytes() if journal.is_file() else None
        fingerprints = {
            "evidence-manifest.json": hashlib.sha256(manifest_bytes).hexdigest() if manifest_bytes is not None else None,
            "evidence-events.jsonl": hashlib.sha256(journal_bytes).hexdigest() if journal_bytes is not None else None,
        }
        if manifest.is_file():
            payload = json.loads(manifest_bytes)
            if "schema_version" not in payload and "run_id" not in payload and isinstance(payload.get("artifacts"), list) and all(isinstance(item, dict) for item in payload["artifacts"]):
                payload = {**payload, "run_id": root.name}
            legacy_manifest = payload.get("schema_version") == "1.0" or ("schema_version" not in payload and "run_id" in payload)
            if legacy_manifest and journal.is_file():
                compatibility_warnings.append("Legacy report manifest is superseded by the authoritative evidence journal.")
            elif payload.get("schema_version") in {"fsq.evidence/v2", "1.0"}:
                checkpoint = EvidenceBundle.model_validate(payload)
                if checkpoint.schema_version == "1.0":
                    checkpoint = _normalize_legacy(checkpoint)
            elif "schema_version" not in payload and "run_id" in payload:
                return _with_snapshot(_legacy_dynamic(payload), fingerprints, 0, 0)
            else:
                raise ValueError("Unsupported evidence manifest schema.")
        if not journal.is_file():
            if checkpoint is None:
                raise FileNotFoundError("Run evidence is unavailable.")
            if checkpoint.schema_version == "fsq.evidence/v2":
                _validate_execution_facts(checkpoint.events, [step for step in checkpoint.steps if not step.metadata.get("recovered_incomplete")])
                artifact_map = {}
                for ref in checkpoint.artifacts:
                    _put_artifact(artifact_map, ref)
                rebuilt = _bundle(
                    checkpoint.bundle_id,
                    checkpoint.run_id,
                    checkpoint.events,
                    [step for step in checkpoint.steps if not step.metadata.get("recovered_incomplete")],
                    checkpoint.planned_steps,
                    checkpoint.checkpoint_sequence,
                    checkpoint.metadata,
                    include_unresolved=True,
                )
                if any(event.run_id != checkpoint.run_id for event in checkpoint.events):
                    raise ValueError("Evidence checkpoint Run identity disagrees.")
                for ref in rebuilt.artifacts:
                    _put_artifact(artifact_map, ref)
                if set(artifact_map) != {ref.artifact_id for ref in rebuilt.artifacts}:
                    raise ValueError("Evidence checkpoint artifact identity has no execution fact.")
                if rebuilt.completeness != checkpoint.completeness:
                    checkpoint = checkpoint.model_copy(update={"completeness": "partial", "warnings": [*checkpoint.warnings, "Checkpoint completeness disagrees with required facts."]})
                checkpoint = checkpoint.model_copy(update={"steps": rebuilt.steps, "artifacts": rebuilt.artifacts})
            return _with_snapshot(checkpoint.model_copy(update={"manifest_path": manifest}), fingerprints, checkpoint.checkpoint_sequence, 0)
        records, warnings = _read_journal(journal_bytes)
        run_id = checkpoint.run_id if checkpoint else records[0].run_id if records else root.name
        if any(record.run_id != run_id for record in records):
            raise ValueError("Evidence journal Run identity mismatch.")
        covered = checkpoint.checkpoint_sequence if checkpoint else 0
        if covered > (records[-1].sequence if records else 0):
            raise ValueError("Evidence journal is shorter than its checkpoint.")
        if checkpoint is not None and checkpoint.schema_version == "fsq.evidence/v2":
            prefix = [record for record in records if record.sequence <= covered]
            expected_events = [record.event for record in prefix if record.event is not None]
            expected_steps = [record.step_result for record in prefix if record.step_result is not None]
            expected_planned = [record.planned_step for record in prefix if record.planned_step is not None]
            persisted_steps = [step for step in checkpoint.steps if not step.metadata.get("recovered_incomplete")]
            if checkpoint.events != expected_events or persisted_steps != expected_steps or checkpoint.planned_steps != expected_planned:
                raise ValueError("Evidence checkpoint disagrees with acknowledged journal facts.")
            rebuilt_checkpoint = _bundle(
                checkpoint.bundle_id, checkpoint.run_id, expected_events, expected_steps, expected_planned, checkpoint.checkpoint_sequence, checkpoint.metadata, include_unresolved=True
            )
            if checkpoint.artifacts != rebuilt_checkpoint.artifacts:
                raise ValueError("Evidence checkpoint artifact inventory disagrees with acknowledged journal facts.")
            if checkpoint.completeness != rebuilt_checkpoint.completeness:
                compatibility_warnings.append("Checkpoint completeness disagrees with acknowledged journal facts; partial evidence is preserved.")
        events = list(checkpoint.events) if checkpoint else []
        steps = list(checkpoint.steps) if checkpoint else []
        planned = list(checkpoint.planned_steps) if checkpoint else []
        steps = [step for step in steps if not step.metadata.get("recovered_incomplete")]
        for record in records:
            if record.sequence <= covered:
                continue
            if record.event is not None:
                events.append(record.event)
            elif record.step_result is not None:
                steps.append(record.step_result)
            elif record.planned_step is not None:
                planned.append(record.planned_step)
        bundle = _bundle(
            checkpoint.bundle_id if checkpoint else f"{run_id}-evidence",
            run_id,
            events,
            steps,
            planned,
            records[-1].sequence if records else covered,
            checkpoint.metadata if checkpoint else {},
            include_unresolved=True,
        )
        sequence_by_event = {record.event.event_id: record.sequence for record in records if record.event is not None and record.event.event_id is not None}
        ordered_sequences = [record.sequence for record in records if record.event is not None]
        bundle = bundle.model_copy(
            update={"events": [event.model_copy(update={"sequence": sequence_by_event.get(event.event_id, ordered_sequences[index])}) for index, event in enumerate(bundle.events)]}
        )
        return _with_snapshot(
            bundle.model_copy(
                update={
                    "warnings": [*(checkpoint.warnings if checkpoint else []), *compatibility_warnings, *warnings],
                    "completeness": "partial" if warnings or (checkpoint is not None and checkpoint.completeness in {"partial", "unavailable"}) or compatibility_warnings else bundle.completeness,
                    "manifest_path": manifest if manifest.is_file() else None,
                }
            ),
            fingerprints,
            covered,
            records[-1].sequence if records else 0,
        )


def _read_journal(data: bytes) -> tuple[list[EvidenceJournalRecord], list[str]]:
    records = []
    warnings = []
    identities = set()
    acknowledged = set()
    completed = set()
    for line in data.splitlines(keepends=True):
        if not line.endswith(b"\n"):
            warnings.append("Evidence journal has an incomplete trailing record.")
            break
        try:
            record = EvidenceJournalRecord.model_validate_json(line)
        except ValueError as exc:
            raise ValueError("Evidence journal contains an invalid complete record.") from exc
        if record.sequence != len(records) + 1 or record.event_id in identities:
            raise ValueError("Evidence journal sequence or event identity is invalid.")
        identities.add(record.event_id)
        event = record.event
        result = record.step_result
        if event is not None and event.step_execution_id:
            identity = event.step_execution_id
            if identity in completed and event.event_type != "step_finish":
                raise ValueError("Evidence journal event occurs after its completed result.")
            if event.event_type == "step_start":
                acknowledged.add(identity)
            elif identity not in acknowledged:
                raise ValueError("Evidence journal event precedes its acknowledged start.")
        if result is not None and result.step_execution_id:
            if result.step_execution_id not in acknowledged:
                raise ValueError("Evidence journal result precedes its acknowledged start.")
            completed.add(result.step_execution_id)
        records.append(record)
    _validate_execution_facts([item.event for item in records if item.event is not None], [item.step_result for item in records if item.step_result is not None])
    return records, warnings


def _bundle(bundle_id, run_id, events, steps, planned, sequence, metadata, *, include_unresolved=False) -> EvidenceBundle:
    results = list(steps)
    finished = {step.step_execution_id or step.step_id for step in results}
    for event in events:
        identity = event.step_execution_id or event.step_id
        if event.event_type == "step_start" and identity and identity not in finished:
            related = [item for item in events if (item.step_execution_id or item.step_id) == identity]
            action = next((item for item in reversed(related) if item.event_type == "action_result"), None)
            phases = [StepPhaseReport.model_validate(item.payload["phase_report"]) for item in related if item.event_type == "phase_finish" and isinstance(item.payload.get("phase_report"), dict)]
            results.append(
                RunnerStepResult(
                    step_id=identity,
                    step_execution_id=identity,
                    source_step_id=event.source_step_id or identity,
                    invocation_path=event.invocation_path,
                    status="incomplete",
                    started_at=event.timestamp,
                    attempt_index=event.attempt_index,
                    action_status=action.payload.get("status") if action else None,
                    action_name=event.payload.get("action_name"),
                    kind=event.payload.get("kind"),
                    phase_reports=phases,
                    source_ref=SourceRef.model_validate(event.payload["source_ref"]) if event.payload.get("source_ref") else None,
                    failure_category=action.payload.get("failure_category") if action else None,
                    error_message=action.payload.get("error_message") if action else None,
                    unavailable_reason="execution_interrupted",
                    metadata={**event.payload.get("metadata", {}), "recovered_incomplete": True, "evidence_policy": event.payload.get("evidence_policy")},
                )
            )
            finished.add(identity)
    if include_unresolved:
        accounted = {(step.source_step_id or step.step_id, step.invocation_path) for step in results}
        for step in planned:
            key = (step.source_step_id or step.step_id, step.invocation_path)
            if key in accounted:
                continue
            results.append(
                RunnerStepResult(
                    step_id=step.step_id,
                    source_step_id=key[0],
                    invocation_path=key[1],
                    source_ref=step.source_ref,
                    action_name=step.action_name,
                    kind=step.kind,
                    status="incomplete",
                    skip_reason="no_acknowledged_execution",
                    unavailable_reason="execution_interrupted",
                    metadata={**step.metadata, "recovered_incomplete": True},
                )
            )
            accounted.add(key)
    artifact_map = {}
    for event in events:
        if event.event_type in {"artifact_captured", "artifact_failed"} and isinstance(event.payload.get("artifact"), dict):
            ref = EvidenceArtifactRef.model_validate(event.payload["artifact"])
            _put_artifact(artifact_map, ref)
    for step in results:
        for phase in step.phase_reports:
            for ref in phase.artifact_refs:
                _put_artifact(artifact_map, ref)
    partial = any(step.status in {"incomplete", "pending", "running"} or step.evidence_errors for step in results)
    partial = partial or any(ref.availability not in {"available", "not_applicable"} for ref in artifact_map.values())
    for result in results:
        if result.step_execution_id is None and result.status in {"skipped", "incomplete"}:
            continue
        policy = result.metadata.get("evidence_policy") or {}
        for phase, requested in (("prepare", policy.get("capture_before")), ("finalize", policy.get("capture_after"))):
            if not requested:
                continue
            captures = [ref for item in result.phase_reports if item.phase == phase for ref in item.artifact_refs]
            for kind in policy.get("artifact_kinds", []):
                if not any(
                    (ref.kind == kind or ref.metadata.get("requested_kind") == kind or ref.metadata.get("requested_artifact_kind") == kind) and ref.availability in {"available", "not_applicable"}
                    for ref in captures
                ):
                    partial = True
    return EvidenceBundle(
        bundle_id=bundle_id,
        run_id=run_id,
        events=list(events),
        steps=results,
        planned_steps=list(planned),
        artifacts=list(artifact_map.values()),
        metadata=dict(metadata),
        checkpoint_sequence=sequence,
        completeness="partial"
        if partial or (not results and not any(event.event_type == "session_finish" and event.payload.get("capability_execution") == "not_requested" for event in events))
        else "complete",
    )


def _with_snapshot(bundle, files, checkpoint, journal):
    return bundle.model_copy(update={"metadata": {**bundle.metadata, "recovery_snapshot": {"checkpoint_sequence": checkpoint, "journal_sequence": journal, "files": files}}})


def _sanitize_urls(text: str) -> str:
    """Persist safe URL copies; callers retain the original invocation values in memory."""
    from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

    sensitive = re.compile(r"^(?:token|access_token|refresh_token|id_token|password|passwd|api[_-]?key|client[_-]?secret|secret|signature|sig|authorization|cookie)$", re.I)

    def replace(match):
        original = match.group(0)
        try:
            parsed = urlsplit(original)
            query = parse_qsl(parsed.query, keep_blank_values=True)
            changed = "@" in parsed.netloc or any(sensitive.fullmatch(key) for key, _ in query)
            if not changed:
                return original
            host = parsed.netloc.rsplit("@", 1)[-1]
            safe_query = urlencode([(key, "[REDACTED]" if sensitive.fullmatch(key) else value) for key, value in query])
            return urlunsplit((parsed.scheme, host, parsed.path, safe_query, parsed.fragment))
        except ValueError:
            return "[REDACTED_URL]"

    return re.sub(r"https?://[^\s<>\"']+", replace, text, flags=re.I)


def _put_artifact(artifacts, ref):
    previous = artifacts.get(ref.artifact_id)
    if previous is not None and previous != ref:
        raise ValueError("Evidence artifact identity has conflicting facts.")
    artifacts[ref.artifact_id] = ref


def _validate_execution_facts(events, results):
    starts = {}
    actions = {}
    artifacts = {}
    phases = {}
    completions = {}
    for event in events:
        if event.event_type in {"artifact_captured", "artifact_failed"} and isinstance(event.payload.get("artifact"), dict):
            ref = EvidenceArtifactRef.model_validate(event.payload["artifact"])
            _put_artifact(artifacts, ref)
            if ref.step_execution_id and ref.step_execution_id != event.step_execution_id:
                raise ValueError("Evidence artifact execution identity disagrees with event.")
        identity = event.step_execution_id
        if identity is None:
            continue
        if event.step_id != identity:
            raise ValueError("Evidence execution identity alias disagrees.")
        if event.event_type == "step_start":
            if identity in starts:
                raise ValueError("Evidence execution identity started more than once.")
            starts[identity] = event
        elif identity in starts:
            start = starts[identity]
            if (event.source_step_id, event.invocation_path, event.attempt_index) != (start.source_step_id, start.invocation_path, start.attempt_index):
                raise ValueError("Evidence event source/attempt identity disagrees with start.")
        else:
            raise ValueError("Evidence execution event has no acknowledged start identity.")
        if event.event_type == "action_result":
            if identity in actions and actions[identity].payload != event.payload:
                raise ValueError("Evidence action outcome conflicts with an earlier result.")
            actions[identity] = event
        if event.event_type == "phase_finish":
            report = event.payload.get("phase_report")
            if isinstance(report, dict):
                if report.get("step_id") != identity or report.get("phase") != event.phase or ("status" in event.payload and event.payload["status"] != report.get("status")):
                    raise ValueError("Evidence durable phase identity or outcome conflicts.")
                key = (identity, event.phase)
                if key in phases and phases[key] != report:
                    raise ValueError("Evidence durable phase outcomes conflict.")
                phases[key] = report
        if event.event_type == "step_finish":
            if identity in completions and completions[identity] != event.payload.get("status"):
                raise ValueError("Evidence durable completion outcomes conflict.")
            completions[identity] = event.payload.get("status")
    finished = set()
    for result in results:
        identity = result.step_execution_id
        if identity is None:
            continue
        if identity in finished or result.step_id != identity or identity not in starts:
            raise ValueError("Evidence result execution identity is missing or duplicated.")
        start = starts[identity]
        if (result.source_step_id, result.invocation_path, result.attempt_index) != (start.source_step_id, start.invocation_path, start.attempt_index):
            raise ValueError("Evidence result source/attempt identity disagrees with start.")
        action = actions.get(identity)
        if identity in completions and completions[identity] != result.status:
            raise ValueError("Evidence final outcome contradicts durable completion.")
        if action is not None:
            observed = action.payload.get("status")
            if result.action_status is not None and observed != result.action_status:
                raise ValueError("Evidence final action outcome contradicts the acknowledged action result.")
            if observed != "passed" and result.status == "passed":
                raise ValueError("Evidence final outcome cannot pass after an unsuccessful action.")
            if action.payload.get("failure_category") and result.failure_category != action.payload["failure_category"]:
                raise ValueError("Evidence final outcome lost the primary action failure.")
        for phase in result.phase_reports:
            if phase.step_id != identity:
                raise ValueError("Evidence phase execution identity disagrees with result.")
            for ref in phase.artifact_refs:
                if ref.step_execution_id and ref.step_execution_id != identity:
                    raise ValueError("Evidence artifact execution identity disagrees with result.")
                _put_artifact(artifacts, ref)
            recorded = phases.get((identity, phase.phase))
            if recorded is not None:
                if StepPhaseReport.model_validate(recorded) != phase:
                    raise ValueError("Evidence final phase contradicts durable phase outcome.")
        reported_phases = {phase.phase for phase in result.phase_reports}
        if any(key[0] == identity and key[1] not in reported_phases for key in phases):
            raise ValueError("Evidence final phases conflict with durable phase inventory.")
        finished.add(identity)


def _normalize_legacy(bundle: EvidenceBundle) -> EvidenceBundle:
    steps = []
    for step in bundle.steps:
        phases = [phase.model_copy(update={"duration_ms": None, "unavailable_reason": "legacy_unmeasured"}) if not phase.metadata.get("timing_measured") else phase for phase in step.phase_reports]
        updates = {"phase_reports": phases}
        if not step.metadata.get("timing_measured"):
            updates.update(duration_ms=None, unavailable_reason="legacy_unmeasured")
        steps.append(step.model_copy(update=updates))
    metadata = dict(bundle.metadata)
    metadata["legacy_coverage_unknown"] = "completeness" not in bundle.model_fields_set
    return bundle.model_copy(update={"steps": steps, "metadata": metadata, "warnings": [*bundle.warnings, "Historical evidence measurements or coverage may be unavailable."]})


def _legacy_dynamic(payload: dict) -> EvidenceBundle:
    results = []
    for item in payload.get("steps", []):
        if not isinstance(item, dict) or not item.get("step_id"):
            continue
        status = item.get("status", "incomplete")
        status = {"success": "passed", "error": "failed"}.get(status, status)
        if status not in {"passed", "failed", "skipped", "cancelled", "incomplete", "pending", "running"}:
            status = "incomplete"
        results.append(RunnerStepResult(step_id=str(item["step_id"]), status=status, unavailable_reason="legacy_unmeasured"))
    return EvidenceBundle(
        bundle_id=f"{payload['run_id']}-evidence",
        run_id=payload["run_id"],
        schema_version="1.0",
        steps=results,
        completeness=payload.get("completeness", "partial"),
        metadata={"legacy_artifacts": payload.get("artifacts", []), "legacy_coverage_unknown": "completeness" not in payload},
        warnings=["Historical dynamic manifest has incomplete capability evidence."],
    )


def _credential_safe(value, secrets=(), depth=0):
    import json
    from urllib.parse import unquote

    if depth > 20:
        return "[REDACTED: nesting limit]"
    keys = {
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
    if isinstance(value, dict):
        return {
            key: "***" if re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", unquote(str(key)).strip()).lower().replace("-", "_") in keys else _credential_safe(item, secrets, depth + 1)
            for key, item in value.items()
        }
    if isinstance(value, (tuple, list)):
        return [_credential_safe(item, secrets, depth + 1) for item in value]
    if not isinstance(value, str):
        return value
    for secret in sorted((item for item in secrets if item), key=len, reverse=True):
        value = value.replace(secret, "***")
    stripped = value.strip()
    if stripped.startswith(("{", "[", '"')):
        try:
            decoded = json.loads(value)
        except ValueError:
            decoded = None
        if isinstance(decoded, (dict, list, str)) and decoded != value:
            safe = _credential_safe(decoded, secrets, depth + 1)
            if safe != decoded:
                return json.dumps(safe, ensure_ascii=False)
    value = re.sub(r"(?im)((?:proxy[-_]authorization|authorization|set[-_]cookie|cookie)\s*[:=]\s*)[^\r\n]+", r"\1***", value)
    value = re.sub(r"(?i)\b(?:Bearer|Basic)\s+[^\s,;\r\n]+", "Bearer ***", value)
    value = re.sub(r"(?i)(\b(?:password|passwd|pwd|api[_-]?key|client[_-]?secret|secret|(?:access[_-]?|refresh[_-]?|id[_-]?)?token)\s*[:=]\s*)(?:\"[^\"]*\"|'[^']*'|[^\s,;]+)", r"\1***", value)
    return _sanitize_urls(value)
