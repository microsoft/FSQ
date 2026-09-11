# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import hashlib
import json
import os
import re
import tempfile
import threading
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from fsq_agent.models import EvidenceArtifactKind, EvidenceArtifactRef, StepPhase, WebLocator

_ARTIFACT_DIRS: dict[EvidenceArtifactKind, str] = {
    "screenshot": "screenshots",
    "ui_tree": "ui-trees",
    "ui_snapshot": "ui-snapshots",
    "tool_call": "harness-calls",
    "log": "logs",
    "json": "raw",
    "text": "logs",
    "other": "raw",
}
_DEFAULT_EXTENSIONS: dict[EvidenceArtifactKind, str] = {
    "screenshot": "png",
    "ui_tree": "json",
    "ui_snapshot": "json",
    "tool_call": "json",
    "log": "txt",
    "json": "json",
    "text": "txt",
    "other": "bin",
}
_DEFAULT_MIME_TYPES: dict[EvidenceArtifactKind, str] = {
    "screenshot": "image/png",
    "ui_tree": "application/json",
    "ui_snapshot": "application/json",
    "tool_call": "application/json",
    "log": "text/plain",
    "json": "application/json",
    "text": "text/plain",
    "other": "application/octet-stream",
}


class ArtifactStore:
    def __init__(self, run_dir: Path, *, secret_values: tuple[str, ...] = ()) -> None:
        self.run_dir = run_dir.resolve()
        self._occurrences: dict[str, int] = {}
        self._lock = threading.Lock()
        self._secret_values = tuple(value for value in secret_values if value)
        self._capture_callback = None

    @contextmanager
    def capture_scope(self, callback):
        previous = self._capture_callback
        self._capture_callback = callback
        try:
            yield
        finally:
            self._capture_callback = previous

    def _acknowledge(self, ref):
        if self._capture_callback is not None:
            self._capture_callback(ref)
        return ref

    def capture_failure(self, *, kind, step_id, phase, name, error):
        ref = EvidenceArtifactRef(
            artifact_id=f"{kind}-{self._slug(step_id)}-{phase}-{uuid.uuid4().hex[:12]}",
            kind=kind,
            step_id=step_id,
            step_execution_id=step_id,
            phase=phase,
            availability="failed",
            unavailable_reason=self._sanitize(str(error) or type(error).__name__),
            metadata={"capture_reason": name, "requested_kind": kind},
        )
        return self._acknowledge(ref)

    def write_json(
        self,
        *,
        kind: EvidenceArtifactKind,
        step_id: str,
        phase: StepPhase,
        name: str,
        payload: Any,
    ) -> EvidenceArtifactRef:
        original = payload
        payload = self._sanitize(payload)
        ref = self._write(kind=kind, step_id=step_id, phase=phase, name=name, extension="json", data=json.dumps(payload, indent=2, ensure_ascii=False).encode("utf-8"), structured=True)
        ref.metadata["redacted"] = ref.metadata.get("redacted", False) or payload != original
        if kind in {"ui_snapshot", "ui_tree"}:
            known = {key: payload[key] for key in ("coverage", "truncated", "compaction", "clipped", "snapshot") if isinstance(payload, dict) and key in payload}
            ref.metadata.update(known or {"coverage": None, "coverage_reason": "driver_did_not_report_coverage"})
        if kind == "json" and isinstance(payload, dict) and payload.get("status") == "unavailable" and payload.get("requested_artifact_kind"):
            ref = ref.model_copy(update={"availability": "not_applicable", "unavailable_reason": str(payload.get("reason") or "session_not_started")})
            ref.metadata["requested_artifact_kind"] = payload["requested_artifact_kind"]
        return self._acknowledge(ref)

    def _sanitize(self, value):
        return _credential_safe(value, self._secret_values)

    def write_text(
        self,
        *,
        kind: EvidenceArtifactKind,
        step_id: str,
        phase: StepPhase,
        name: str,
        text: str,
    ) -> EvidenceArtifactRef:
        return self._acknowledge(self._write(kind=kind, step_id=step_id, phase=phase, name=name, extension=_DEFAULT_EXTENSIONS[kind], data=text.encode("utf-8")))

    def write_bytes(
        self,
        *,
        kind: EvidenceArtifactKind,
        step_id: str,
        phase: StepPhase,
        name: str,
        data: bytes,
    ) -> EvidenceArtifactRef:
        return self._acknowledge(self._write(kind=kind, step_id=step_id, phase=phase, name=name, extension=_DEFAULT_EXTENSIONS[kind], data=data))

    def _write(self, *, kind, step_id, phase, name, extension, data, structured=False) -> EvidenceArtifactRef:
        with self._lock:
            transformed = False
            if kind not in {"screenshot", "other"}:
                if structured:
                    safe_data = json.dumps(self._sanitize(json.loads(data)), indent=2, ensure_ascii=False).encode("utf-8")
                else:
                    safe_data = self._sanitize(data.decode("utf-8")).encode("utf-8")
                transformed = safe_data != data
                data = safe_data
            key = f"{kind}-{self._artifact_id(step_id=step_id, phase=phase, name=name)}"
            occurrence = self._occurrences.get(key, 0) + 1
            self._occurrences[key] = occurrence
            artifact_id = f"{key}-{occurrence}-{uuid.uuid4().hex[:12]}"
            relative = self._relative_path(kind=kind, artifact_id=artifact_id, extension=extension)
            path = self.run_dir / relative
            if not path.resolve().is_relative_to(self.run_dir):
                raise ValueError("Artifact path must remain contained in its Run.")
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.parent.is_symlink() or not path.parent.resolve().is_relative_to(self.run_dir):
                raise ValueError("Artifact path must remain contained in its Run.")
            descriptor, temporary = tempfile.mkstemp(prefix=".artifact-", dir=path.parent)
            try:
                with os.fdopen(descriptor, "wb") as stream:
                    stream.write(data)
                    stream.flush()
                    os.fsync(stream.fileno())
                # Hard-link installation is atomic and refuses an existing destination.
                os.link(temporary, path)
            finally:
                Path(temporary).unlink(missing_ok=True)
            return self._artifact_ref(artifact_id=artifact_id, kind=kind, path=relative, step_id=step_id, phase=phase).model_copy(
                update={
                    "size_bytes": len(data),
                    "sha256": hashlib.sha256(data).hexdigest(),
                    "capture_occurrence": occurrence,
                    "step_execution_id": step_id,
                    "metadata": {"capture_reason": name, "redacted": transformed},
                }
            )

    def _artifact_ref(
        self,
        *,
        artifact_id: str,
        kind: EvidenceArtifactKind,
        path: Path,
        step_id: str,
        phase: StepPhase,
    ) -> EvidenceArtifactRef:
        return EvidenceArtifactRef(
            artifact_id=artifact_id,
            kind=kind,
            path=path,
            mime_type=_DEFAULT_MIME_TYPES[kind],
            step_id=step_id,
            phase=phase,
        )

    def _relative_path(self, *, kind: EvidenceArtifactKind, artifact_id: str, extension: str) -> Path:
        return Path("artifacts") / _ARTIFACT_DIRS[kind] / f"{artifact_id}.{extension}"

    def _artifact_id(self, *, step_id: str, phase: StepPhase, name: str) -> str:
        return "-".join([self._slug(step_id), phase, self._slug(name)])

    def _slug(self, value: str) -> str:
        slug = re.sub(r"[^a-zA-Z0-9]+", "-", value.strip().lower()).strip("-")
        return slug or "artifact"


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
        is_locator, locator = WebLocator.preserve_for_redaction(value, secrets)
        if is_locator:
            return locator
        safe = {
            key: "***" if re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", unquote(str(key)).strip()).lower().replace("-", "_") in keys else _credential_safe(item, secrets, depth + 1)
            for key, item in value.items()
        }
        return WebLocator.finish_redaction(value, safe)
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
