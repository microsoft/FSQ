# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import asyncio
import json
import os
import tempfile
from collections.abc import Callable
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
from fsq_agent.case_dsl import FsqCaseLoader, FsqCaseSerializer, FsqExecutableStepAdapter
from fsq_agent.config import Settings, list_workspace_registry, load_workspace_platform_settings, validate_strict_core_settings
from fsq_agent.core import ArtifactStore, HarnessFactory, RuntimeSecretStore
from fsq_agent.execution import RunLifecycleService, RunSource, RunStepCounts, allocate_run, collect_strict_lifecycle_cases, load_run_metadata, run_strict_lifecycle_case, transition_run
from fsq_agent.models import ConfigurationError, EvidenceBundle

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
    runtime_secret_store = RuntimeSecretStore.from_settings(settings.runtime_secrets)
    try:
        RunLifecycleService.preflight_steps([step for steps in resolved_steps.values() for step in steps], runtime_secret_store, registry=registry)
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
    try:
        metadata = transition_run(run_dir, metadata, "running")
        evaluator = build_ai_assertion_evaluator(settings) if requires_ai else None
        harness = HarnessFactory().create_harness(
            platform=request.platform,
            harness_settings=settings.harness,
            artifact_store=ArtifactStore(run_dir=run_dir, secret_values=tuple(settings.runtime_secrets.private_values().values())),
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
            runtime_secret_store=runtime_secret_store,
            resolved_steps_by_path=resolved_steps,
            cases_by_path={path.resolve(): item for path, item in lifecycle_cases},
        )
    except BaseException as exc:
        cancelled = isinstance(exc, (asyncio.CancelledError, KeyboardInterrupt)) or type(exc).__name__ in {"TaskCancelledError", "RunCancelled", "ExecutionCancelled"}
        lifecycle = RunLifecycleService()
        try:
            try:
                frozen = lifecycle.load_result(run_dir)
            except FileNotFoundError:
                try:
                    evidence = lifecycle.read_evidence(run_dir)
                except FileNotFoundError:
                    evidence = EvidenceBundle(run_id=run_id, bundle_id=f"{run_id}-evidence")
                frozen = lifecycle.freeze(
                    run_dir, metadata, bundle=evidence, status="cancelled" if cancelled else "error", summary="Execution cancelled." if cancelled else "Execution infrastructure failed."
                )
            current = load_run_metadata(run_dir)
            if current.status in {"preparing", "running", "finalizing"}:
                lifecycle.finalize(run_dir, current, execution_result=frozen)
        except Exception:  # noqa: BLE001, S110 - original failure and allocated identity must survive finalization failure.
            pass
        raise ApplicationError(
            code=ApplicationErrorCode.RUN_CANCELLED if cancelled else ApplicationErrorCode.RUN_EXECUTION_FAILED,
            category=ApplicationErrorCategory.INTERNAL,
            message="Execution cancelled." if cancelled else "Run execution failed.",
            action="Inspect the allocated Run evidence.",
            details={"run_id": run_id, "platform": request.platform, "exception_type": type(exc).__name__},
        ) from exc
    if case_path.read_bytes() != source_before:
        raise ApplicationError(
            code=ApplicationErrorCode.RUN_SOURCE_CHANGED,
            category=ApplicationErrorCategory.CONFIGURATION,
            message="Case source changed during execution.",
            action="Inspect the frozen source and recorded result.",
            details={"run_id": run_id, "platform": request.platform},
        )
    completed = load_run_metadata(run_dir) if (run_dir / "run.json").is_file() else None
    if completed and completed.execution_result:
        status = completed.status
        summary = completed.result.summary
    else:
        # Explicit collaborator compatibility: injected legacy runners may return a stored report.
        legacy_status, summary = _report_status(artifact.path)
        status = "success" if legacy_status == "passed" else "failed"
    suggestion_path = None
    candidate_case_path = None
    warnings = ["case.suffix_deprecated: rename this Case to *.fsq.yaml"] if case_path.name.endswith(".codex.yaml") else []
    processing = {}
    if request.suggest:
        try:
            if suggestion_analyzer_factory is None:
                _raise_suggestion_not_configured(run_id, artifact.path)
            report_path = artifact.path.with_suffix(".json")
            report = json.loads(report_path.read_text(encoding="utf-8"))
            analysis = suggestion_analyzer_factory(settings).analyze(
                parsed_case={"config": case.config.model_dump(mode="json", by_alias=True), "commands": case.commands}, execution_report=_bounded_execution_facts(report)
            )
            suggestion_path, candidate_case_path = _write_analysis_artifacts(
                run_dir=run_dir,
                source_case=case_path,
                parsed_source=case,
                source_platform=case.config.platform,
                execution_status="passed" if status == "success" else status,
                execution_summary=summary,
                analysis=analysis,
            )
            processing["suggestion"] = {"status": "success", "path": suggestion_path.name}
            if candidate_case_path is not None:
                processing["suggestion"]["candidate_case_path"] = candidate_case_path.name
        except (asyncio.CancelledError, KeyboardInterrupt) as exc:
            processing["suggestion"] = {"status": "cancelled", "code": "case.suggestion_cancelled", "error_type": type(exc).__name__}
            warnings.append("case.suggestion_cancelled: analysis interrupted; the completed execution is preserved.")
        except Exception as exc:  # noqa: BLE001 - suggestion failure cannot replace execution truth.
            warnings.append("case.suggestion_failed: analysis failed; the execution result is preserved.")
            processing["suggestion"] = {"status": "failed", "code": "case.suggestion_failed", "error_type": type(exc).__name__}
        try:
            _atomic_write(run_dir / "suggestion-processing.json", json.dumps(processing))
        except OSError:
            warnings.append("case.suggestion_failed: analysis status could not be persisted; execution result is preserved.")
            processing["suggestion"] = {**processing.get("suggestion", {}), "status": "failed", "code": "case.suggestion_failed", "persistence": "unavailable"}
    return CaseTestResult(
        run_id=run_id,
        status=status,
        summary=summary,
        report_path=artifact.path,
        evidence_manifest_path=artifact.evidence_manifest_path,
        suggestion_path=suggestion_path,
        candidate_case_path=candidate_case_path,
        warnings=warnings,
        processing=processing,
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
    total = int(summary.get("step_count", summary.get("total_steps", 0)) or 0)
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
    parsed_source=None,
    source_platform: str,
    execution_status: str,
    execution_summary: str,
    analysis: CaseSuggestionAnalysis,
) -> tuple[Path, Path | None]:
    candidate_path = None
    candidate_status = "absent"
    candidate_diagnostics = []
    if analysis.candidate_case_yaml is not None:
        proposed_path = run_dir / "candidate.fsq.yaml"
        try:
            candidate = FsqCaseLoader().load_text(analysis.candidate_case_yaml, proposed_path)
            source = parsed_source if parsed_source is not None else FsqCaseLoader().load_case(source_case)
            if candidate.config.platform != source_platform:
                _raise_candidate_platform_mismatch()
            candidate = candidate.model_copy(update={"config": source.config.model_copy(deep=True)})
            content = FsqCaseSerializer(build_capability_registry(platform=source_platform).snapshot()).serialize(candidate).decode("utf-8")
        except (ConfigurationError, ValueError):
            candidate_status = "invalid"
            candidate_diagnostics = [{"code": "case.invalid", "message": "Candidate failed static Case validation."}]
        else:
            _atomic_write(proposed_path, content)
            candidate_path = proposed_path
            candidate_status = "available"
    suggestion_path = run_dir / "case-suggestions.json"
    _atomic_write(
        suggestion_path,
        json.dumps(
            {
                "source_case": source_case.name,
                "source_case_immutable": True,
                "execution_status": execution_status,
                "execution_summary": execution_summary,
                "analysis_summary": analysis.summary,
                "suggestions": [dict(item) for item in analysis.suggestions],
                "candidate_case_path": candidate_path.name if candidate_path else None,
                "candidate_case_status": candidate_status,
                "candidate_diagnostics": candidate_diagnostics,
            },
            indent=2,
            ensure_ascii=False,
        ),
    )
    return suggestion_path, candidate_path


def _raise_candidate_platform_mismatch() -> None:
    raise ValueError("Candidate platform mismatch.")


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
