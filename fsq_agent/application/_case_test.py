# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import json
import os
import tempfile
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path
from typing import Never, Protocol

from fsq_agent._capability_bootstrap import build_capability_registry, provider_required_capability_names, steps_require_provider
from fsq_agent.ai_services import CaseSuggestionAnalysis, build_ai_assertion_evaluator
from fsq_agent.application.contracts import (
    ApplicationError,
    ApplicationErrorCategory,
    ApplicationErrorCode,
    CaseTestRequest,
    CaseTestResult,
    WorkspaceRequest,
)
from fsq_agent.application.workspace import require_initialized_workspace
from fsq_agent.case_dsl import FsqCaseLoader, FsqExecutableStepAdapter
from fsq_agent.config import Settings, list_workspace_registry, load_workspace_platform_settings, validate_strict_core_settings
from fsq_agent.core import ArtifactStore, HarnessFactory, RuntimeSecretStore
from fsq_agent.execution import RunArtifactIndex, RunResultSummary, RunSource, RunStepCounts, allocate_run, collect_strict_lifecycle_cases, run_strict_lifecycle_case, transition_run
from fsq_agent.models import ConfigurationError

_MAX_FACT_ITEMS = 100
_MAX_FACT_STRING = 2_000
_MAX_FACT_BYTES = 200_000


class _SuggestionAnalyzer(Protocol):
    def analyze(self, *, parsed_case: dict[str, object], execution_report: dict[str, object]) -> CaseSuggestionAnalysis: ...


def execute_case_test(
    request: CaseTestRequest,
    *,
    suggestion_analyzer_factory: Callable[[Settings], _SuggestionAnalyzer] | None,
) -> CaseTestResult:
    workspace = require_initialized_workspace(WorkspaceRequest(current_directory=request.current_directory))
    settings = load_workspace_platform_settings(workspace.workspace, request.platform)
    try:
        case_path = _resolve_case_path(request.case_path, settings.cases.dir, request.current_directory)
    except FileNotFoundError as exc:
        raise ApplicationError(
            code=ApplicationErrorCode.CASE_NOT_FOUND,
            category=ApplicationErrorCategory.REQUEST_VALIDATION,
            message=str(exc),
            action="Provide an existing *.fsq.yaml Case path.",
        ) from exc
    source_before = case_path.read_bytes()
    try:
        case = FsqCaseLoader().load_case(case_path)
    except ConfigurationError as exc:
        raise _invalid_case_error(exc) from exc
    if case.config.platform != request.platform:
        raise ApplicationError(
            code=ApplicationErrorCode.CASE_INVALID,
            category=ApplicationErrorCategory.REQUEST_VALIDATION,
            message="Case platform does not match the requested platform.",
            details={"case_platform": case.config.platform, "requested_platform": request.platform},
        )

    registry = build_capability_registry(platform=request.platform)
    snapshot = registry.snapshot()
    try:
        lifecycle_cases = collect_strict_lifecycle_cases(case_path=case_path, case=case, settings=settings)
        resolved_steps = {path.resolve(): FsqExecutableStepAdapter(registry_snapshot=snapshot).to_executable_steps(item) for path, item in lifecycle_cases}
    except ConfigurationError as exc:
        raise _invalid_case_error(exc) from exc
    requires_ai = any(steps_require_provider(steps, snapshot, provider_required_capability_names(request.platform)) for steps in resolved_steps.values())
    validate_strict_core_settings(settings, requires_ai_assertion=requires_ai)
    workspace_name = _registered_workspace_name(workspace.workspace)
    metadata = allocate_run(
        workspace=workspace.workspace,
        workspace_name=workspace_name,
        platform=request.platform,
        source_id=case.id,
        mode="strict",
        source=RunSource(kind="case", case_id=case.id, case_path=str(case_path.relative_to(workspace.workspace)) if case_path.is_relative_to(workspace.workspace) else case_path.name),
        platform_runs_dir=Path(settings.output.runs_dir),
    )
    run_id = metadata.run_id
    run_dir = Path(settings.output.runs_dir) / run_id
    metadata = transition_run(run_dir, metadata, "running")
    try:
        evaluator = build_ai_assertion_evaluator(settings) if requires_ai else None
        harness = HarnessFactory().create_harness(
            platform=request.platform,
            harness_settings=settings.harness,
            artifact_store=ArtifactStore(run_dir=run_dir),
            ai_assertion_evaluator=evaluator,
            runtime_secret_settings=settings.runtime_secrets,
            app_id=(settings.harness.android.app_id or case.config.app_id) if request.platform == "android" else None,
        )
        artifact = run_strict_lifecycle_case(
            case_path=case_path,
            case=case,
            settings=settings,
            harness=harness,
            output_dir=run_dir,
            run_id=run_id,
            registry=registry,
            registry_snapshot=snapshot,
            resolve_steps=lambda steps, _case: steps,
            post_action_delay_seconds=settings.execution.post_action_delay_seconds,
            runtime_secret_store=RuntimeSecretStore.from_settings(settings.runtime_secrets),
            resolved_steps_by_path=resolved_steps,
            cases_by_path={path.resolve(): item for path, item in lifecycle_cases},
        )
    except BaseException:
        _best_effort_terminal(run_dir, metadata, "error")
        raise
    try:
        if case_path.read_bytes() != source_before:
            _raise_source_modified()
        status, summary = _report_status(artifact.path)
        metadata = transition_run(run_dir, metadata, "finalizing")
        counts = _report_step_counts(artifact.path)
    except BaseException:
        _best_effort_terminal(run_dir, metadata, "error")
        raise
    suggestion_path = None
    candidate_case_path = None
    analysis_error: BaseException | None = None
    try:
        if request.suggest:
            if suggestion_analyzer_factory is None:
                _raise_suggestion_not_configured(run_id, artifact.path)
            report = json.loads(artifact.path.with_suffix(".json").read_text(encoding="utf-8"))
            analysis = suggestion_analyzer_factory(settings).analyze(
                parsed_case={
                    "config": case.config.model_dump(mode="json", by_alias=True),
                    "commands": case.commands,
                },
                execution_report=_bounded_execution_facts(report),
            )
            suggestion_path, candidate_case_path = _write_analysis_artifacts(
                run_dir=run_dir,
                source_case=case_path,
                source_platform=case.config.platform,
                execution_status=status,
                execution_summary=summary,
                analysis=analysis,
            )
    except BaseException as error:  # noqa: BLE001 - finalize completed execution before propagating analysis failure.
        analysis_error = error
    result_status = "success" if status == "passed" else "failed"
    try:
        transition_run(
            run_dir,
            metadata,
            result_status,
            result=RunResultSummary(summary=summary, steps=counts),
            artifacts=RunArtifactIndex(
                report=artifact.path.with_suffix(".json").name,
                report_markdown=artifact.path.name,
                events="events.jsonl" if (run_dir / "events.jsonl").is_file() else None,
                evidence_manifest=artifact.evidence_manifest_path.name if artifact.evidence_manifest_path else None,
                suggestions=suggestion_path.name if suggestion_path else None,
                candidate_case=candidate_case_path.name if candidate_case_path else None,
            ),
        )
    except BaseException:
        if analysis_error is not None:
            with suppress(BaseException):
                _best_effort_terminal(run_dir, metadata, result_status)
            _raise_analysis_error(analysis_error, run_id, artifact.path)
        _best_effort_terminal(run_dir, metadata, "error")
        raise
    if analysis_error is not None:
        _raise_analysis_error(analysis_error, run_id, artifact.path)
    return CaseTestResult(
        run_id=run_id,
        status=result_status,
        summary=summary,
        report_path=artifact.path,
        evidence_manifest_path=artifact.evidence_manifest_path,
        suggestion_path=suggestion_path,
        candidate_case_path=candidate_case_path,
        warnings=["case.suffix_deprecated: rename this Case to *.fsq.yaml"] if case_path.name.endswith(".codex.yaml") else [],
    )


def _raise_analysis_error(error: BaseException, run_id: str, report_path: Path) -> Never:
    if not isinstance(error, Exception) or isinstance(error, ApplicationError):
        raise error
    raise ApplicationError(
        code=ApplicationErrorCode.CASE_SUGGESTION_FAILED,
        category=ApplicationErrorCategory.UNAVAILABLE,
        message="Case suggestion analysis failed.",
        action="The completed Run is preserved. Check Provider readiness and retry suggestion analysis.",
        details={"run_id": run_id, "report_path": str(report_path)},
    ) from error


def _resolve_case_path(value: Path, cases_dir: Path, current_directory: Path) -> Path:
    candidates = [value] if value.is_absolute() else [cases_dir / value, current_directory / value]
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    raise FileNotFoundError(f"Case not found: {value}")


def _registered_workspace_name(workspace_root: Path) -> str:
    resolved = workspace_root.resolve()
    entry = next((item for item in list_workspace_registry() if item.root_path.resolve() == resolved), None)
    if entry is None:
        raise ApplicationError(
            code=ApplicationErrorCode.WORKSPACE_NOT_INITIALIZED,
            category=ApplicationErrorCategory.WORKSPACE_CONFIGURATION,
            message="Current directory is not a registered Workspace.",
            action="Run fsq init from this exact directory.",
        )
    return entry.name


def _invalid_case_error(error: ConfigurationError) -> ApplicationError:
    return ApplicationError(
        code=ApplicationErrorCode.CASE_INVALID,
        category=ApplicationErrorCategory.REQUEST_VALIDATION,
        message=str(error).splitlines()[0],
        action="Correct the FSQ Case and retry.",
        details=error.context,
    )


def _report_status(path: Path) -> tuple[str, str]:
    payload = json.loads(path.with_suffix(".json").read_text(encoding="utf-8"))
    summary = payload.get("summary", {})
    status = str(summary.get("status", "failed"))
    failed = summary.get("failed_steps", 0)
    return status, "Case passed." if status == "passed" else f"Case failed with {failed} failed step(s)."


def _best_effort_terminal(run_dir: Path, metadata, status: str) -> None:
    try:
        transition_run(run_dir, metadata, status)
    except Exception:  # noqa: BLE001, S110 - preserve the original execution failure.
        pass


def _raise_source_modified() -> None:
    raise RuntimeError("Case source was modified during testing.")


def _raise_suggestion_not_configured(run_id: str, report_path: Path) -> None:
    raise ApplicationError(
        code=ApplicationErrorCode.CASE_SUGGESTION_FAILED,
        category=ApplicationErrorCategory.INTERNAL,
        message="Case suggestion analysis is not configured.",
        action="Start this operation through a supported FSQ adapter.",
        details={"run_id": run_id, "report_path": str(report_path)},
    )


def _report_step_counts(path: Path) -> RunStepCounts:
    payload = json.loads(path.with_suffix(".json").read_text(encoding="utf-8"))
    summary = payload.get("summary", {})
    if not isinstance(summary, dict):
        return RunStepCounts()
    total = int(summary.get("total_steps", 0) or 0)
    failed = int(summary.get("failed_steps", 0) or 0)
    return RunStepCounts(total=total, passed=max(0, total - failed), failed=failed)


def _bounded_execution_facts(report: dict[str, object]) -> dict[str, object]:
    facts = {key: _bound_value(report[key]) for key in ("run_id", "summary", "steps", "events") if key in report}
    encoded = json.dumps(facts, ensure_ascii=False, default=str).encode("utf-8")
    if len(encoded) > _MAX_FACT_BYTES:
        facts = {
            "run_id": facts.get("run_id"),
            "summary": facts.get("summary"),
            "truncated": True,
        }
    return facts


def _bound_value(value: object) -> object:
    if isinstance(value, str):
        return value[:_MAX_FACT_STRING]
    if isinstance(value, list):
        return [_bound_value(item) for item in value[:_MAX_FACT_ITEMS]]
    if isinstance(value, dict):
        return {str(key)[:_MAX_FACT_STRING]: _bound_value(item) for key, item in list(value.items())[:_MAX_FACT_ITEMS]}
    if value is None or isinstance(value, bool | int | float):
        return value
    return str(value)[:_MAX_FACT_STRING]


def _write_analysis_artifacts(
    *,
    run_dir: Path,
    source_case: Path,
    source_platform: str,
    execution_status: str,
    execution_summary: str,
    analysis: CaseSuggestionAnalysis,
) -> tuple[Path, Path | None]:
    candidate_path = None
    candidate_status = "absent"
    if analysis.candidate_case_yaml is not None:
        proposed_path = run_dir / "candidate.fsq.yaml"
        try:
            _validate_candidate(analysis.candidate_case_yaml, proposed_path, source_platform)
        except (ConfigurationError, ValueError):
            candidate_status = "invalid"
        else:
            _atomic_write(proposed_path, analysis.candidate_case_yaml)
            candidate_path = proposed_path
            candidate_status = "available"
    suggestion_path = run_dir / "case-suggestions.json"
    _atomic_write(
        suggestion_path,
        json.dumps(
            {
                "source_case": str(source_case),
                "source_case_immutable": True,
                "execution_status": execution_status,
                "execution_summary": execution_summary,
                "analysis_summary": analysis.summary,
                "suggestions": [dict(item) for item in analysis.suggestions],
                "candidate_case_path": str(candidate_path) if candidate_path else None,
                "candidate_case_status": candidate_status,
            },
            indent=2,
            ensure_ascii=False,
        ),
    )
    return suggestion_path, candidate_path


def _validate_candidate(content: str, destination: Path, source_platform: str) -> None:
    with tempfile.TemporaryDirectory(dir=destination.parent) as temporary_directory:
        temporary_path = Path(temporary_directory) / destination.name
        temporary_path.write_text(content, encoding="utf-8")
        candidate = FsqCaseLoader().load_case(temporary_path)
    if candidate.config.platform != source_platform:
        raise ValueError("Candidate Case platform does not match the source Case.")


def _atomic_write(path: Path, content: str) -> None:
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", delete=False) as temporary:
            temporary.write(content)
            temporary.flush()
            os.fsync(temporary.fileno())
            temporary_path = Path(temporary.name)
        temporary_path.replace(path)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()
