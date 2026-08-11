# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import asyncio
import json
import logging
from pathlib import Path
import re
import time

import click

from fsq_agent.agent import FsqAgent
from fsq_agent.cli._capability_bootstrap import build_capability_registry
from fsq_agent.cli._case_lifecycle import (
    case_has_lifecycle_hooks,
    collect_lifecycle_cases,
    lifecycle_settings_have_hooks,
    run_strict_fsq_lifecycle_case,
)
from fsq_agent.cli._core_execution import run_strict_fsq_core_case
from fsq_agent.cli._formatting import log_result, log_run_event
from fsq_agent.cli._doctor import run_doctor_command
from fsq_agent.cli._llm_setup import setup_llm_provider
from fsq_agent.cli._logging import configure_cli_logging
from fsq_agent._strict_case_recording import StrictCaseRecording, record_dynamic_run_as_strict_case
from fsq_agent.cli._strict_replay import resolve_strict_replay_steps
from fsq_agent.cli._task_loader import discover_case_yaml_paths, read_raw_text_file, resolve_case_yaml_path
from fsq_agent.config import Settings, load_platform_settings, validate_runtime_settings, validate_strict_core_settings
from fsq_agent.core import RuntimeSecretStore
from fsq_agent.core import (
    ArtifactStore,
    HarnessFactory,
    HarnessInterface,
)
from fsq_agent.fsq import FsqCaseLoader, FsqExecutableStepAdapter
from fsq_agent.models import ConfigurationError, FsqAgentError, FsqCase, Task
from fsq_agent.playground import PlaygroundServerOptions, run_playground
from fsq_agent.providers import build_ai_assertion_evaluator
from fsq_agent.report import resolve_report_path


logger = logging.getLogger(__name__)
PLATFORM_CHOICE = click.Choice(["android", "web", "windows", "macos"])
LLM_PROVIDER_CHOICE = click.Choice(["github_copilot", "azure_openai"])


@click.group()
def main() -> None:
    configure_cli_logging()


@main.command()
@click.option("--platform", type=PLATFORM_CHOICE, required=True)
@click.option("--provider", type=LLM_PROVIDER_CHOICE, default=None)
def init(platform: str, provider: str | None) -> None:
    try:
        if provider is not None:
            setup_llm_provider(provider=provider)
        settings = load_platform_settings(platform, _current_workspace_path())
        logger.info("Initialized fsq-agent workspace: %s", settings.workspace.root_dir)
        logger.info("Output root: %s", settings.output.root_dir)
        _log_readiness("LLM run", lambda: validate_runtime_settings(settings))
        _log_readiness("Strict-core run", lambda: validate_strict_core_settings(settings))
        _log_readiness("AI assertion", lambda: validate_strict_core_settings(settings, requires_ai_assertion=True))
    except FsqAgentError as exc:
        logger.error("Error: %s", exc)
        raise click.Abort() from exc
    except OSError as exc:
        logger.error("Error: %s", exc)
        raise click.Abort() from exc


@main.command()
@click.option("--platform", type=PLATFORM_CHOICE, default=None)
@click.option("--format", "output_format", type=click.Choice(["text", "json"]), default="text", show_default=True)
@click.option("--color", type=click.Choice(["auto", "always", "never"]), default="auto", show_default=True)
@click.option("--non-interactive", is_flag=True, default=False)
@click.option("--repair", is_flag=True, default=False)
def doctor(
    platform: str | None,
    output_format: str,
    color: str,
    non_interactive: bool,
    repair: bool,
) -> None:
    exit_code = run_doctor_command(
        platform=platform,
        output_format=output_format,
        color=color,
        non_interactive=non_interactive,
        repair=repair,
    )
    if exit_code:
        raise click.exceptions.Exit(exit_code)


@main.command()
@click.option("--platform", type=PLATFORM_CHOICE, required=True)
@click.option("--strict", is_flag=True, default=False, show_default=True)
@click.option("--goal", default=None)
@click.option("--case-yaml", "case_yaml_path", type=click.Path(exists=False, dir_okay=False), default=None)
@click.option("--case-dir", "case_dir_path", type=click.Path(exists=False, file_okay=False), default=None)
@click.option("--stream/--no-stream", "stream", default=True, show_default=True)
@click.option("--stream-format", type=click.Choice(["concise", "jsonl"]), default="concise", show_default=True)
@click.option("--record", is_flag=True, default=False, show_default=True)
@click.option("--record-on-failure", is_flag=True, default=False, show_default=True)
@click.option("--tracing/--no-tracing", "tracing", default=None)
def run(
    platform: str,
    strict: bool,
    goal: str | None,
    case_yaml_path: str | None,
    case_dir_path: str | None,
    stream: bool,
    stream_format: str,
    record: bool,
    record_on_failure: bool,
    tracing: bool | None,
) -> None:
    try:
        _validate_run_inputs(
            strict=strict,
            goal=goal,
            case_yaml_path=case_yaml_path,
            case_dir_path=case_dir_path,
            record=record,
            record_on_failure=record_on_failure,
        )
        settings = load_platform_settings(platform, _current_workspace_path())
        if tracing is not None:
            settings.openai_agents.tracing_enabled = tracing
        if strict:
            _run_strict(settings, case_yaml_path=case_yaml_path, case_dir_path=case_dir_path)
            return
        _run_dynamic(
            settings,
            goal=goal,
            case_yaml_path=case_yaml_path,
            case_dir_path=case_dir_path,
            stream=stream,
            stream_format=stream_format,
            record=record,
            record_on_failure=record_on_failure,
        )
    except FsqAgentError as exc:
        logger.error("Error: %s", exc)
        if exc.context:
            logger.error("Details: %s", exc.context)
        raise click.Abort() from exc


@main.command()
@click.option("--platform", type=PLATFORM_CHOICE, required=True)
@click.option("--run-id", required=True)
@click.option("--format", "report_format", type=click.Choice(["markdown", "json"]), default="markdown")
def report(platform: str, run_id: str, report_format: str) -> None:
    try:
        settings = load_platform_settings(platform, _current_workspace_path())
        path = resolve_report_path(Path(settings.output.runs_dir), run_id, report_format)  # type: ignore[arg-type]
        click.echo(path.read_text(encoding="utf-8"), nl=False)
    except FsqAgentError as exc:
        logger.error("Error: %s", exc)
        raise click.Abort() from exc


@main.command()
@click.option("--platform", type=PLATFORM_CHOICE, required=True)
@click.option("--host", default="127.0.0.1", show_default=True)
@click.option("--port", default=8765, show_default=True, type=click.IntRange(1, 65535))
@click.option("--open-browser/--no-open-browser", "open_browser", default=True, show_default=True)
def playground(
    platform: str,
    host: str,
    port: int,
    open_browser: bool,
) -> None:
    try:
        settings = load_platform_settings(platform, _current_workspace_path())
        run_playground(
            settings,
            PlaygroundServerOptions(host=host, port=port, open_browser=open_browser),
        )
    except FsqAgentError as exc:
        logger.error("Error: %s", exc)
        raise click.Abort() from exc
    except OSError as exc:
        logger.error("Playground startup failed: %s", exc)
        raise click.Abort() from exc


def _current_workspace_path() -> Path:
    return Path.cwd() / ".fsq-agent-workspace"


def _validate_run_inputs(
    *,
    strict: bool,
    goal: str | None,
    case_yaml_path: str | None,
    case_dir_path: str | None,
    record: bool = False,
    record_on_failure: bool = False,
) -> None:
    source_count = sum(value is not None for value in (goal, case_yaml_path, case_dir_path))
    if source_count != 1:
        raise ConfigurationError("Exactly one of --goal, --case-yaml, or --case-dir is required.")
    if strict and goal is not None:
        raise ConfigurationError("--strict requires --case-yaml or --case-dir; --goal is only supported by dynamic LLM runs.")
    if strict and (record or record_on_failure):
        raise ConfigurationError("--record and --record-on-failure are only supported by dynamic LLM runs.")
    if record_on_failure and not record:
        raise ConfigurationError("--record-on-failure requires --record.")


def _run_dynamic(
    settings: Settings,
    *,
    goal: str | None,
    case_yaml_path: str | None,
    case_dir_path: str | None,
    stream: bool,
    stream_format: str,
    record: bool,
    record_on_failure: bool,
) -> None:
    if goal is not None:
        _run_dynamic_task(settings, _task_from_goal(goal), stream, stream_format, record, record_on_failure)
        return
    if case_yaml_path is not None:
        source_path, content = read_raw_text_file(case_yaml_path, settings.cases.dir)
        _run_dynamic_task(settings, _task_from_raw_case_source(source_path, content), stream, stream_format, record, record_on_failure)
        return
    if case_dir_path is None:
        raise ConfigurationError("--case-dir is required for directory runs.")
    case_paths = discover_case_yaml_paths(case_dir_path, settings.cases.dir)
    tasks = [_task_from_raw_case_source(source_path, read_raw_text_file(source_path)[1]) for source_path in case_paths]
    asyncio.run(_run_dynamic_case_tasks(settings, tasks, stream, stream_format, record, record_on_failure))


def _run_dynamic_task(
    settings: Settings,
    task: Task,
    stream: bool,
    stream_format: str,
    record: bool,
    record_on_failure: bool,
) -> None:
    sink = (lambda event: log_run_event(event, stream_format)) if stream else None
    result = asyncio.run(FsqAgent.from_settings(settings).run(task, event_sink=sink))
    log_result(result)
    if record:
        _log_recording(_record_dynamic_result(settings, task, result, record_on_failure))


async def _run_dynamic_case_tasks(
    settings: Settings,
    tasks: list[Task],
    stream: bool,
    stream_format: str,
    record: bool,
    record_on_failure: bool,
) -> None:
    summaries: list[dict[str, object]] = []
    for task in tasks:
        try:
            sink = (lambda event: log_run_event(event, stream_format)) if stream else None
            result = await FsqAgent.from_settings(settings).run(task, event_sink=sink)
            log_result(result)
            recording = _record_dynamic_result(settings, task, result, record_on_failure) if record else None
            if recording is not None:
                _log_recording(recording)
            summaries.append(
                {
                    "task_id": task.id,
                    "status": result.status,
                    "report_path": str(result.report.path),
                    "error": None if result.status == "success" else result.verification.summary,
                    "recording_status": recording.status if recording else None,
                    "recorded_case_path": str(recording.recorded_case_path) if recording and recording.recorded_case_path else None,
                }
            )
        except Exception as exc:
            summaries.append({"task_id": task.id, "status": "failed", "report_path": None, "error": str(exc)})
            logger.error("Dynamic case failed: %s: %s", task.id, exc)
    _log_dynamic_case_summary(summaries)


def _run_strict(settings: Settings, *, case_yaml_path: str | None, case_dir_path: str | None) -> None:
    loader = FsqCaseLoader()
    if case_yaml_path is not None:
        case_path = resolve_case_yaml_path(case_yaml_path, settings.cases.dir)
        case = loader.load_case(case_path)
        _validate_strict_case_platform(settings, case)
        validate_strict_core_settings(settings, requires_ai_assertion=_case_requires_ai_assertion(settings, case))
        _validate_strict_case_app_id(settings, case)
        artifact = _run_strict_case(settings, case_path, case, case.id)
        logger.info("Core report: %s", artifact.path)
        logger.info("Evidence manifest: %s", artifact.evidence_manifest_path)
        click.echo(f"Core report: {artifact.path}")
        click.echo(f"Evidence manifest: {artifact.evidence_manifest_path}")
        case_status, case_error = _strict_core_case_report_status(artifact.path)
        if case_status != "passed":
            logger.error("Strict core case failed: %s: %s", case_path, case_error)
            raise click.exceptions.Exit(1)
        return
    if case_dir_path is None:
        raise ConfigurationError("--case-dir is required for strict directory runs.")
    case_paths = discover_case_yaml_paths(case_dir_path, settings.cases.dir)
    cases = [(case_path, loader.load_case(case_path)) for case_path in case_paths]
    cases = _filter_top_level_strict_cases(settings, cases, loader)
    for _, case in cases:
        _validate_strict_case_platform(settings, case)
    validate_strict_core_settings(settings, requires_ai_assertion=any(_case_requires_ai_assertion(settings, case) for _, case in cases))
    for _, case in cases:
        _validate_strict_case_app_id(settings, case)
    summary = _run_strict_case_batch(settings, cases)
    if summary["failed"]:
        raise click.exceptions.Exit(1)


def _filter_top_level_strict_cases(
    settings: Settings,
    cases: list[tuple[Path, FsqCase]],
    loader: FsqCaseLoader,
) -> list[tuple[Path, FsqCase]]:
    dependency_paths = _config_lifecycle_dependency_paths(settings)
    for case_path, case in cases:
        if case_path.resolve() in dependency_paths:
            continue
        if not (case_has_lifecycle_hooks(case) or lifecycle_settings_have_hooks(settings.case_lifecycle)):
            continue
        lifecycle_cases = collect_lifecycle_cases(
            case_path=case_path,
            case=case,
            cases_dir=settings.cases.dir,
            case_lifecycle=settings.case_lifecycle,
            loader=loader,
        )
        dependency_paths.update(path.resolve() for path, _ in lifecycle_cases[1:])
    top_level_cases = [(case_path, case) for case_path, case in cases if case_path.resolve() not in dependency_paths]
    if top_level_cases:
        return top_level_cases
    raise ConfigurationError("No top-level strict case files found.", context={"case_count": len(cases)})


def _config_lifecycle_dependency_paths(settings: Settings) -> set[Path]:
    dependency_paths: set[Path] = set()
    for hook in [*settings.case_lifecycle.on_case_start, *settings.case_lifecycle.on_case_complete]:
        for action in hook.actions:
            if action.action_name == "runCase":
                dependency_paths.add(resolve_case_yaml_path(action.value, settings.cases.dir).resolve())
    return dependency_paths


def _run_strict_case(settings: Settings, case_path: Path, case: FsqCase, run_id: str):
    run_dir = Path(settings.output.runs_dir) / run_id
    registry = build_capability_registry(platform=settings.harness.platform)
    registry_snapshot = registry.snapshot()
    if case_has_lifecycle_hooks(case) or lifecycle_settings_have_hooks(settings.case_lifecycle):
        lifecycle_cases = collect_lifecycle_cases(
            case_path=case_path,
            case=case,
            cases_dir=settings.cases.dir,
            case_lifecycle=settings.case_lifecycle,
        )
        for _, lifecycle_case in lifecycle_cases:
            _validate_strict_case_platform(settings, lifecycle_case)
            _validate_strict_lifecycle_case_app_id(settings, root_case=case, case=lifecycle_case)
        requires_ai_assertion = any(_case_requires_ai_assertion(settings, lifecycle_case, registry_snapshot) for _, lifecycle_case in lifecycle_cases)
        validate_strict_core_settings(settings, requires_ai_assertion=requires_ai_assertion)
        harness = _build_strict_harness(settings, case, run_dir, requires_ai_assertion)
        return run_strict_fsq_lifecycle_case(
            case_path=case_path,
            case=case,
            settings=settings,
            harness=harness,
            output_dir=run_dir,
            run_id=run_id,
            registry=registry,
            registry_snapshot=registry_snapshot,
            post_action_delay_seconds=settings.execution.post_action_delay_seconds,
        )
    steps = resolve_strict_replay_steps(
        FsqExecutableStepAdapter(registry_snapshot=registry_snapshot).to_executable_steps(case),
        settings,
        registry_snapshot=registry_snapshot,
    )
    harness = _build_strict_harness(settings, case, run_dir, _case_requires_ai_assertion(settings, case, registry_snapshot))
    return run_strict_fsq_core_case(
        case_path=case_path,
        harness=harness,
        output_dir=run_dir,
        run_id=run_id,
        registry=registry,
        steps=steps,
        post_action_delay_seconds=settings.execution.post_action_delay_seconds,
        runtime_secret_store=RuntimeSecretStore.from_settings(settings.runtime_secrets),
    )


def _record_dynamic_result(
    settings: Settings,
    task: Task,
    result,
    record_on_failure: bool,
) -> StrictCaseRecording:
    run_dir = Path(settings.output.runs_dir) / result.report.run_id
    try:
        return record_dynamic_run_as_strict_case(
            run_dir=run_dir,
            task=task,
            result=result,
            settings=settings,
            allow_failure=record_on_failure,
        )
    except Exception as exc:  # noqa: BLE001 - recording must not change dynamic run status.
        recording_path = run_dir / "recording.json"
        recording = StrictCaseRecording(status="failed", recording_path=recording_path, errors=[str(exc)])
        try:
            recording_path.parent.mkdir(parents=True, exist_ok=True)
            recording_path.write_text(json.dumps(recording.to_json(), indent=2, ensure_ascii=False), encoding="utf-8")
        except OSError:
            logger.error("Unable to write recording failure manifest: %s", recording_path)
        return recording


def _log_recording(recording: StrictCaseRecording) -> None:
    if recording.status == "recorded" and recording.recorded_case_path is not None:
        logger.info("Recorded strict case: %s", recording.recorded_case_path)
        click.echo(f"Recorded strict case: {recording.recorded_case_path}")
        logger.info("Recording manifest: %s", recording.recording_path)
        click.echo(f"Recording manifest: {recording.recording_path}")
        return
    if recording.status == "skipped":
        logger.warning("Recording skipped: %s", "; ".join(recording.warnings) or recording.recording_path)
        click.echo(f"Recording skipped: {recording.recording_path}")
        return
    logger.error("Recording failed: %s", "; ".join(recording.errors) or recording.recording_path)
    click.echo(f"Recording failed: {recording.recording_path}")


def _run_strict_case_batch(settings: Settings, cases: list[tuple[Path, FsqCase]]) -> dict[str, object]:
    batch_id = f"strict-core-batch-{time.strftime('%Y-%m-%d_%H-%M-%S')}"
    batch_dir = Path(settings.output.runs_dir) / batch_id
    batch_dir.mkdir(parents=True, exist_ok=True)
    case_summaries: list[dict[str, object]] = []
    for index, (case_path, case) in enumerate(cases, start=1):
        run_id = f"{batch_id}-{_case_run_slug(case_path, index)}"
        try:
            artifact = _run_strict_case(settings, case_path, case, run_id)
            case_status, case_error = _strict_core_case_report_status(artifact.path)
            case_summaries.append(
                {
                    "case_path": str(case_path),
                    "run_id": run_id,
                    "status": case_status,
                    "report_path": str(artifact.path),
                    "evidence_manifest_path": str(artifact.evidence_manifest_path),
                    "error": case_error,
                }
            )
            if case_status == "passed":
                logger.info("Strict core case passed: %s", case_path)
            else:
                logger.error("Strict core case failed: %s: %s", case_path, case_error)
        except Exception as exc:
            case_summaries.append(
                {
                    "case_path": str(case_path),
                    "run_id": run_id,
                    "status": "failed",
                    "report_path": None,
                    "evidence_manifest_path": None,
                    "error": str(exc),
                }
            )
            logger.error("Strict core case failed: %s: %s", case_path, exc)
    summary = _strict_core_batch_summary(batch_id, batch_dir, case_summaries)
    summary_json_path = batch_dir / "strict-core-batch-summary.json"
    summary_md_path = batch_dir / "strict-core-batch-summary.md"
    summary_json_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    summary_md_path.write_text(_strict_core_batch_markdown(summary), encoding="utf-8")
    logger.info("Strict core batch summary: %s", summary_json_path)
    logger.info("Strict core batch report: %s", summary_md_path)
    click.echo(f"Strict core batch summary: {summary_json_path}")
    click.echo(f"Strict core batch report: {summary_md_path}")
    return summary


def _build_strict_harness(
    settings: Settings,
    case: FsqCase,
    run_dir: Path,
    requires_ai_assertion: bool = False,
) -> HarnessInterface:
    return HarnessFactory().create_harness(
        platform=settings.harness.platform,
        harness_settings=settings.harness,
        artifact_store=ArtifactStore(run_dir=run_dir),
        ai_assertion_evaluator=build_ai_assertion_evaluator(settings) if requires_ai_assertion else None,
        runtime_secret_settings=settings.runtime_secrets,
        app_id=_strict_case_app_id(settings, case) if settings.harness.platform == "android" else None,
    )


def _case_requires_ai_assertion(settings: Settings, case: FsqCase, registry_snapshot=None) -> bool:
    registry_snapshot = registry_snapshot or build_capability_registry(platform=settings.harness.platform).snapshot()
    return any(
        step.action_name == "assert_with_ai"
        for step in FsqExecutableStepAdapter(registry_snapshot=registry_snapshot).to_executable_steps(case)
    )


def _strict_case_app_id(settings: Settings, case: FsqCase) -> str:
    return settings.harness.android.app_id or case.config.app_id or ""


def _validate_strict_case_app_id(settings: Settings, case: FsqCase) -> None:
    if settings.harness.platform != "android":
        return
    if not _strict_case_app_id(settings, case):
        raise ConfigurationError(
            "Android app id is required for strict-core runs.",
            context={"case_path": str(case.path), "config_key": "harness.android.app_id", "case_key": "appId"},
        )


def _validate_strict_lifecycle_case_app_id(settings: Settings, *, root_case: FsqCase, case: FsqCase) -> None:
    if settings.harness.platform != "android":
        return
    if settings.harness.android.app_id or root_case.config.app_id or case.config.app_id:
        return
    raise ConfigurationError(
        "Android app id is required for strict-core lifecycle runs.",
        context={
            "root_case_path": str(root_case.path),
            "case_path": str(case.path),
            "config_key": "harness.android.app_id",
            "case_key": "appId",
        },
    )


def _validate_strict_case_platform(settings: Settings, case: FsqCase) -> None:
    if case.config.platform == settings.harness.platform:
        return
    raise ConfigurationError(
        "Strict case platform must match the configured harness platform.",
        context={"case_path": str(case.path), "case_platform": case.config.platform, "harness_platform": settings.harness.platform},
    )


def _task_from_goal(goal: str) -> Task:
    normalized_goal = " ".join(goal.split())
    if not normalized_goal:
        raise ConfigurationError("Goal cannot be empty.")
    task_id = _goal_task_id(normalized_goal)
    return Task(
        id=task_id,
        name=normalized_goal,
        description=(
            "Run this natural-language goal as a goal-driven automation task. "
            "First derive ordered key actions from page knowledge, then execute them while adapting to live UI state. "
            "Final verification should judge whether the goal is complete.\n\n"
            f"Goal: {normalized_goal}"
        ),
        planning_reference_kind="goal",
        planning_reference_text=normalized_goal,
    )


def _task_from_raw_case_source(source_path: Path, content: str) -> Task:
    label = source_path.name
    planning_reference_text = _raw_case_planning_reference(source_path, content)
    return Task(
        id=_goal_task_id(source_path.name.removesuffix(".codex.yaml")),
        name=f"Case reference: {label}",
        description=(
            "Run this case through dynamic LLM execution using the raw file content below as reference material. "
            "The CLI has not parsed, normalized, or converted this content into local steps.\n\n"
            f"{planning_reference_text}"
        ),
        planning_reference_kind="raw_case",
        planning_reference_text=planning_reference_text,
    )


def _raw_case_planning_reference(source_path: Path, content: str) -> str:
    return (
        f"Source path: {source_path}\n\n"
        "Raw case content:\n"
        "```yaml\n"
        f"{content}\n"
        "```"
    )


def _goal_task_id(goal: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", goal.casefold()).strip("-")
    return slug[:80] or "goal-task"


def _case_run_slug(case_path: Path, index: int) -> str:
    stem = case_path.name.removesuffix(".codex.yaml")
    slug = re.sub(r"[^a-z0-9]+", "-", stem.casefold()).strip("-")
    return f"{index:03d}-{slug[:80] or 'case'}"


def _log_readiness(label: str, validator) -> bool:
    try:
        validator()
    except FsqAgentError as exc:
        logger.warning("%s readiness: not ready: %s", label, exc)
        if exc.context:
            logger.warning("%s readiness details: %s", label, exc.context)
        return False
    logger.info("%s readiness: ready", label)
    return True


def _log_dynamic_case_summary(cases: list[dict[str, object]]) -> None:
    success = sum(1 for case in cases if case["status"] == "success")
    failed = sum(1 for case in cases if case["status"] == "failed")
    inconclusive = sum(1 for case in cases if case["status"] == "inconclusive")
    logger.info(
        "Dynamic case directory summary: total=%s success=%s failed=%s inconclusive=%s",
        len(cases),
        success,
        failed,
        inconclusive,
    )
    for case in cases:
        if case["status"] == "success":
            logger.info("Dynamic case succeeded: %s report=%s", case["task_id"], case["report_path"])
        else:
            logger.error("Dynamic case %s: %s: %s", case["status"], case["task_id"], case["error"])


def _strict_core_batch_summary(batch_id: str, batch_dir: Path, cases: list[dict[str, object]]) -> dict[str, object]:
    passed = sum(1 for case in cases if case["status"] == "passed")
    failed = sum(1 for case in cases if case["status"] == "failed")
    return {
        "batch_id": batch_id,
        "batch_dir": str(batch_dir),
        "total": len(cases),
        "passed": passed,
        "failed": failed,
        "cases": cases,
    }


def _strict_core_case_report_status(report_path: Path) -> tuple[str, str | None]:
    json_report_path = report_path.with_suffix(".json")
    try:
        report = json.loads(json_report_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return "failed", f"Unable to read core-report.json: {exc}"
    summary = report.get("summary") if isinstance(report, dict) else None
    status = summary.get("status") if isinstance(summary, dict) else None
    if status == "passed":
        return "passed", None
    failed_steps = summary.get("failed_steps") if isinstance(summary, dict) else None
    if isinstance(failed_steps, int):
        return "failed", f"core-report summary status={status or 'unknown'} failed_steps={failed_steps}"
    return "failed", f"core-report summary status={status or 'unknown'}"


def _strict_core_batch_markdown(summary: dict[str, object]) -> str:
    lines = [
        f"# Strict Core Batch: {summary['batch_id']}",
        "",
        f"Total: `{summary['total']}`",
        f"Passed: `{summary['passed']}`",
        f"Failed: `{summary['failed']}`",
        "",
        "| Status | Case | Run | Error |",
        "|---|---|---|---|",
    ]
    for case in summary["cases"]:
        if not isinstance(case, dict):
            continue
        lines.append(
            "| {status} | {case_path} | {run_id} | {error} |".format(
                status=case.get("status", ""),
                case_path=case.get("case_path", ""),
                run_id=case.get("run_id", ""),
                error=str(case.get("error") or "").replace("|", "\\|"),
            )
        )
    return "\n".join(lines) + "\n"