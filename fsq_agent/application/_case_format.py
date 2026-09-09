# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from __future__ import annotations

import difflib
import os
import stat
import tempfile
from pathlib import Path

from fsq_agent._capability_bootstrap import build_capability_registry
from fsq_agent.application.contracts.cases import CaseFormatDiagnostic, CaseFormatRequest, CaseFormatResult
from fsq_agent.case_dsl import FsqCaseLoader, FsqCaseSerializer, is_fsq_case_file
from fsq_agent.models import ConfigurationError


def format_case(request: CaseFormatRequest) -> CaseFormatResult:
    path = request.case_path if request.case_path.is_absolute() else request.current_directory / request.case_path
    path = path.resolve()
    base = {"path": path, "mode": request.mode, "warnings": ["case.suffix_deprecated: rename this Case to *.fsq.yaml"] if path.name.endswith(".codex.yaml") else []}
    try:
        if not is_fsq_case_file(path):
            _invalid("Unsupported Case filename.", "case.suffix")
        before = path.read_bytes()
        source_stat = path.stat()
        case = FsqCaseLoader().load_text(before.decode("utf-8"), path)
        if case.config.platform not in {"web", "android", "windows", "macos"}:
            _invalid("Unsupported platform.", "case.platform", ["platform"])
        snapshot = build_capability_registry(platform=case.config.platform).snapshot()
        canonical = FsqCaseSerializer(snapshot).serialize(case)
    except (ConfigurationError, UnicodeError, FileNotFoundError) as exc:
        context = exc.context if isinstance(exc, ConfigurationError) else {}
        errors = context.get("validation_errors") or [{"loc": context.get("field_path", []), "msg": str(exc) if isinstance(exc, ConfigurationError) else "Invalid or unavailable Case input."}]
        diagnostics = [
            CaseFormatDiagnostic(code=context.get("code", "case.invalid"), message=error["msg"], file=str(path), step_index=context.get("step_index"), field_path=error["loc"]) for error in errors
        ]
        return CaseFormatResult(**base, valid=False, diagnostics=diagnostics)
    different = before != canonical
    diff = (
        "".join(difflib.unified_diff(before.decode("utf-8").splitlines(keepends=True), canonical.decode("utf-8").splitlines(keepends=True), fromfile=str(path), tofile=str(path)))
        if request.mode == "diff"
        else None
    )
    changed = False
    if request.mode == "write" and different:
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=path.parent, prefix=f".{path.name}.", delete=False) as handle:
                temporary = handle.name
                handle.write(canonical)
                handle.flush()
                os.fsync(handle.fileno())
            Path(temporary).chmod(stat.S_IMODE(source_stat.st_mode))
            if path.read_bytes() != before or path.stat().st_mtime_ns != source_stat.st_mtime_ns:
                raise OSError("Case changed during formatting.")
            Path(temporary).replace(path)
            changed = True
        finally:
            if temporary is not None and Path(temporary).exists():
                Path(temporary).unlink()
    return CaseFormatResult(**base, valid=True, formatted=not different or changed, changed=changed, needs_formatting=different, diff=diff)


def _invalid(message: str, code: str, field_path: list[str] | None = None) -> None:
    raise ConfigurationError(message, context={"code": code, "field_path": field_path or []})
