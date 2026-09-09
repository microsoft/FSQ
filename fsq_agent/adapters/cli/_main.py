# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import asyncio
import json
import sys
import webbrowser
from enum import IntEnum
from pathlib import Path

import click

from fsq_agent.adapters.control_plane import ControlPlaneServerOptions, run_control_plane
from fsq_agent.application import (
    ApplicationError,
    ApplicationErrorCategory,
    ApplicationErrorCode,
    CaseCreateRequest,
    CaseFormatRequest,
    CaseTestRequest,
    DoctorRequest,
    GenerateRunHtmlRequest,
    ListRunsRequest,
    ReadRunLogsRequest,
    ShowRunRequest,
    WorkspaceInitializeRequest,
    WorkspaceRequest,
    complete_github_configuration,
    configure_azure_openai,
    configure_openai,
    create_case,
    diagnose_workspace,
    event_record,
    format_case,
    generate_run_html,
    initialize_workspace,
    list_openai_models,
    list_runs,
    normalize_application_error,
    provider_status,
    read_run_logs,
    request_github_device_code,
    require_initialized_workspace,
    result_record,
    show_run,
    test_case,
)

from ._composition import create_case_agent

PLATFORMS = click.Choice(["android", "web", "windows", "macos"])
OUTPUTS = click.Choice(["human", "json", "jsonl"])
RUN_STATUSES = click.Choice(["preparing", "running", "finalizing", "success", "failed", "inconclusive", "cancelled", "error", "interrupted"])


class ExitCode(IntEnum):
    SUCCESS = 0
    CASE_FAILED = 1
    USAGE = 2
    WORKSPACE = 3
    UNAVAILABLE = 4
    INTERNAL = 5
    INTERRUPTED = 130


class ProtocolGroup(click.Group):
    def main(self, args=None, prog_name=None, complete_var=None, standalone_mode=True, **extra):
        invocation_args = list(args) if args is not None else sys.argv[1:]
        try:
            result = super().main(
                args=args,
                prog_name=prog_name,
                complete_var=complete_var,
                standalone_mode=False,
                **extra,
            )
        except (KeyboardInterrupt, click.Abort) as exc:
            if standalone_mode:
                raise SystemExit(ExitCode.INTERRUPTED) from exc
            raise click.exceptions.Exit(ExitCode.INTERRUPTED) from exc
        except click.ClickException as exc:
            output = _requested_output(invocation_args)
            if output == "human":
                if standalone_mode:
                    exc.show()
                    raise SystemExit(exc.exit_code) from exc
                raise
            error = ApplicationError(
                code=ApplicationErrorCode.CASE_INVALID,
                category=ApplicationErrorCategory.REQUEST_VALIDATION,
                message=exc.format_message(),
                action="Run the command with --help and correct its arguments.",
            )
            click.echo(json.dumps(error.to_record(operation=_requested_operation(invocation_args)), ensure_ascii=False))
            if standalone_mode:
                raise SystemExit(ExitCode.USAGE) from exc
            raise click.exceptions.Exit(ExitCode.USAGE) from exc
        except ApplicationError as exc:
            return _top_level_error(
                exc,
                _requested_output(invocation_args),
                _requested_operation(invocation_args),
                standalone_mode,
            )
        except Exception as exc:  # noqa: BLE001 - outermost transport boundary.
            return _top_level_error(
                normalize_application_error(exc),
                _requested_output(invocation_args),
                _requested_operation(invocation_args),
                standalone_mode,
            )
        else:
            return _finish_invocation(result, standalone_mode)


def _requested_output(args: list[str] | None) -> str:
    values = args or []
    if "--json" in values and "format" in values:
        return "json"
    for index, value in enumerate(values):
        if value == "--output" and index + 1 < len(values):
            return values[index + 1]
        if value.startswith("--output="):
            return value.partition("=")[2]
    return "human"


def _requested_operation(args: list[str] | None) -> str:
    values = args or []
    positional: list[str] = []
    skip_next = False
    for value in values:
        if skip_next:
            skip_next = False
            continue
        if value == "--output":
            skip_next = True
            continue
        if value.startswith("-"):
            continue
        positional.append(value)
    if not positional:
        return "fsq"
    if positional[0] in {"case", "providers", "runs"} and len(positional) > 1:
        return ".".join(positional[:2])
    return positional[0]


def _finish_invocation(result: object, standalone_mode: bool) -> object:
    if standalone_mode and isinstance(result, int) and result != 0:
        raise SystemExit(result)
    return result


def _top_level_error(error: ApplicationError, output: str, operation: str, standalone_mode: bool) -> None:
    if output == "human":
        click.echo(f"Error: {error.message}", err=True)
        if error.action:
            click.echo(f"Action: {error.action}", err=True)
        _emit_safe_internal_diagnostic(error)
    else:
        click.echo(json.dumps(error.to_record(operation=operation), ensure_ascii=False, default=str))
    exit_code = _error_exit_code(error)
    if standalone_mode:
        raise SystemExit(exit_code) from error
    raise click.exceptions.Exit(exit_code) from error


@click.group(cls=ProtocolGroup)
@click.option("--output", "output_format", type=OUTPUTS, default="human", show_default=True)
@click.option("--non-interactive", is_flag=True, default=False)
@click.pass_context
def main(context: click.Context, output_format: str, non_interactive: bool) -> None:
    context.ensure_object(dict)
    context.obj.update(output=output_format, non_interactive=non_interactive)


@main.command()
@click.option("--platform", type=PLATFORMS, required=True)
@click.option("--name", default=None)
@click.option("--app-id", default=None)
@click.option("--browser-channel", type=click.Choice(["chromium", "chrome", "chrome-beta", "chrome-dev", "chrome-canary", "msedge", "msedge-beta", "msedge-dev", "msedge-canary"]), default=None)
@click.option("--browser-executable-path", type=click.Path(path_type=Path, dir_okay=False), default=None)
@click.option("--app-path", type=click.Path(path_type=Path), default=None)
@click.option("--window-title-re", default=None)
@click.option("--launch-args", default=None)
@click.option("--bundle-id", default=None)
@click.option("--env", "env_values", multiple=True, metavar="NAME=VALUE")
@click.option("--update-existing", is_flag=True, default=False)
@click.pass_context
def init(
    context: click.Context,
    platform: str,
    name: str | None,
    app_id: str | None,
    browser_channel: str | None,
    browser_executable_path: Path | None,
    app_path: Path | None,
    window_title_re: str | None,
    launch_args: str | None,
    bundle_id: str | None,
    env_values: tuple[str, ...],
    update_existing: bool,
) -> None:
    env: dict[str, str] = {}
    for value in env_values:
        key, separator, secret = value.partition("=")
        if not separator or not key or not secret:
            raise click.UsageError("Each --env must use non-empty NAME=VALUE syntax.")
        if key in env:
            raise click.UsageError("Each --env name may be supplied only once.")
        env[key] = secret
    workspace = initialize_workspace(
        WorkspaceInitializeRequest(
            current_directory=Path.cwd(),
            platform=platform,
            name=name,
            app_id=app_id,
            browser_channel=browser_channel,
            browser_executable_path=browser_executable_path,
            app_path=app_path,
            window_title_re=window_title_re,
            launch_args=launch_args,
            bundle_id=bundle_id,
            env=env,
            update_existing=update_existing,
        )
    )
    if context.obj["output"] == "human":
        click.echo(f"Workspace {workspace.name} {workspace.status}: {workspace.platform} at {workspace.root_path}")
        if workspace.browser_executable_path is not None:
            click.echo(f"Browser: {workspace.browser_executable_path}")
    else:
        _emit_terminal(context, workspace.model_dump(mode="json"))


@main.command()
@click.pass_context
def doctor(context: click.Context) -> None:
    result = diagnose_workspace(DoctorRequest(current_directory=Path.cwd()))
    if context.obj["output"] == "human":
        _render_doctor(result)
    else:
        _emit_terminal(context, result.model_dump(mode="json"))
    if result.status == "unavailable":
        raise click.exceptions.Exit(ExitCode.UNAVAILABLE)


@main.group(name="case")
def case_group() -> None:
    pass


@case_group.command(name="create")
@click.option("--platform", type=PLATFORMS, required=True)
@click.option("--goal", required=True)
@click.option("--name", default=None, help="Stable Case name without a suffix.")
@click.pass_context
def case_create(context: click.Context, platform: str, goal: str, name: str | None) -> None:
    events: list[dict[str, object]] = []

    def collect_event(event: object) -> None:
        events.append(event.model_dump(mode="json"))

    try:
        result = asyncio.run(
            create_case(
                CaseCreateRequest(current_directory=Path.cwd(), platform=platform, goal=goal, case_name=name),
                event_sink=collect_event,
                agent_factory=create_case_agent,
            )
        )
        _emit_terminal(context, result.model_dump(mode="json"), events=events)
    except ApplicationError as exc:
        _application_error(context, exc)
    except KeyboardInterrupt as exc:
        raise click.exceptions.Exit(ExitCode.INTERRUPTED) from exc
    except Exception as exc:  # noqa: BLE001 - transport boundary normalization.
        _application_error(context, normalize_application_error(exc))
    if result.status != "success" or result.publication_outcome == "conflict":
        raise click.exceptions.Exit(ExitCode.CASE_FAILED)


@case_group.command(name="test")
@click.argument("case_path", type=click.Path(path_type=Path))
@click.option("--platform", type=PLATFORMS, required=True)
@click.option("--suggest", is_flag=True, default=False)
@click.pass_context
def case_test(context: click.Context, case_path: Path, platform: str, suggest: bool) -> None:
    try:
        result = test_case(CaseTestRequest(current_directory=Path.cwd(), platform=platform, case_path=case_path, suggest=suggest))
        if context.obj["output"] == "human":
            click.echo(f"Case {result.status}: {result.summary}")
            click.echo(f"Report: {result.report_path}")
            if result.suggestion_path is not None:
                click.echo(f"Suggestions: {result.suggestion_path}")
            if result.candidate_case_path is not None:
                click.echo(f"Candidate Case: {result.candidate_case_path}")
            if suggest:
                click.echo("Source Case was not modified.")
        else:
            _emit_terminal(context, result.model_dump(mode="json"))
    except ApplicationError as exc:
        _application_error(context, exc)
    except KeyboardInterrupt as exc:
        raise click.exceptions.Exit(ExitCode.INTERRUPTED) from exc
    except Exception as exc:  # noqa: BLE001 - transport boundary normalization.
        _application_error(context, normalize_application_error(exc))
    if result.status != "success":
        raise click.exceptions.Exit(ExitCode.CASE_FAILED)


@main.command()
@click.option("--host", default="127.0.0.1")
@click.option("--port", type=click.IntRange(1, 65535), default=8879)
@click.option("--open-browser/--no-open-browser", default=True)
def ui(host: str, port: int, open_browser: bool) -> None:
    run_control_plane(ControlPlaneServerOptions(host=host, port=port, open_browser=open_browser))


@main.group()
def providers() -> None:
    pass


@providers.command(name="configure")
@click.argument("name", type=click.Choice(["github_copilot", "azure_openai", "openai"]))
@click.option("--base-url", default=None)
@click.option("--model", default=None)
@click.option("--api-key", default=None)
@click.pass_context
def providers_configure(context: click.Context, name: str, base_url: str | None, model: str | None, api_key: str | None) -> None:
    machine = context.obj["output"] != "human"
    if name == "github_copilot":
        if base_url is not None or api_key is not None:
            raise click.UsageError("--base-url and --api-key do not apply to github_copilot")
        if machine or context.obj["non_interactive"]:
            raise click.UsageError("GitHub Copilot configuration requires Human interactive mode")
        device = request_github_device_code()
        click.echo(f"Open: {device.verification_uri}")
        click.echo(f"Code: {device.user_code}")

        def select_model(models):
            if not models:
                raise click.UsageError("GitHub Copilot returned no eligible models")
            if len(models) == 1:
                return models[0].id
            choices = {str(index): item.id for index, item in enumerate(models, start=1)}
            for index, item in enumerate(models, start=1):
                click.echo(f"{index}. {item.name} ({item.id})")
            return choices[click.prompt("Select model", type=click.Choice(list(choices)))]

        result = complete_github_configuration(device, model=model, select_model=select_model, cancel_requested=lambda: False)
    elif name == "openai":
        if base_url is not None:
            raise click.UsageError("--base-url applies only to azure_openai; OpenAI uses the official endpoint")
        if (machine or context.obj["non_interactive"]) and (not model or not api_key):
            raise click.UsageError("--model and --api-key are required")
        if not api_key:
            api_key = click.prompt("OpenAI API key", hide_input=True)
        if not model:
            models = list_openai_models(api_key=api_key)
            if not models:
                raise ApplicationError(
                    code=ApplicationErrorCode.PROVIDER_UNAVAILABLE,
                    category=ApplicationErrorCategory.UNAVAILABLE,
                    message="No eligible OpenAI models are available.",
                    action="Check model access or use another Provider.",
                )
            choices = {str(index): item.id for index, item in enumerate(models, start=1)}
            for index, item in enumerate(models, start=1):
                click.echo(f"{index}. {item.name}")
            model = choices[click.prompt("Select model", type=click.Choice(list(choices)))]
        result = configure_openai(model=model, api_key=api_key)
    else:
        if not base_url:
            if machine or context.obj["non_interactive"]:
                raise click.UsageError("--base-url, --model, and --api-key are required")
            base_url = click.prompt("Azure OpenAI base URL")
        if not model:
            if machine or context.obj["non_interactive"]:
                raise click.UsageError("--base-url, --model, and --api-key are required")
            model = click.prompt("Azure OpenAI model")
        if not api_key:
            if machine or context.obj["non_interactive"]:
                raise click.UsageError("--base-url, --model, and --api-key are required")
            api_key = click.prompt("Azure OpenAI API key", hide_input=True)
        result = configure_azure_openai(base_url=base_url, model=model, api_key=api_key)
    _emit_terminal(context, result.model_dump(mode="json"))


@providers.command(name="status")
@click.pass_context
def providers_status(context: click.Context) -> None:
    result = provider_status()
    value = result.model_dump(mode="json")
    if context.obj["output"] == "human":
        click.echo(f"Provider: {result.provider or 'not configured'}")
        click.echo(f"Model: {result.model or 'not configured'}")
        click.echo(f"Status: {result.status}")
        click.echo(result.message)
        if result.action:
            click.echo(f"Action: {result.action}")
    else:
        _emit_terminal(context, value)
    if result.status != "ready":
        raise click.exceptions.Exit(ExitCode.UNAVAILABLE)


@main.group()
def runs() -> None:
    pass


@runs.command(name="list")
@click.option("--platform", type=PLATFORMS)
@click.option("--status", "statuses", type=RUN_STATUSES, multiple=True)
@click.option("--mode", type=click.Choice(["strict", "explore"]))
@click.option("--since")
@click.option("--case", "case_id")
@click.option("--limit", type=click.IntRange(1, 200), default=20)
@click.pass_context
def runs_list(context: click.Context, platform: str | None, statuses: tuple[str, ...], mode: str | None, since: str | None, case_id: str | None, limit: int) -> None:
    result = list_runs(ListRunsRequest(current_directory=Path.cwd(), platform=platform, statuses=statuses, mode=mode, since=since, case_id=case_id, limit=limit))
    if context.obj["output"] == "human":
        click.echo("RUN ID  PLATFORM  MODE  STATUS  STARTED  DURATION  CASE/GOAL")
        for item in result.runs:
            source = item.source.case_id or item.source.goal_summary if item.source else "—"
            click.echo(f"{item.run_id}  {item.platform}  {item.mode or '—'}  {item.status}  {item.started_at or '—'}  {item.duration_ms or '—'}  {source}")
    else:
        _emit_terminal(context, result.model_dump(mode="json"))


@runs.command(name="show")
@click.argument("run_id")
@click.option("--platform", type=PLATFORMS)
@click.option("--open", "open_report", is_flag=True)
@click.pass_context
def runs_show(context: click.Context, run_id: str, platform: str | None, open_report: bool) -> None:
    if open_report and (context.obj["output"] != "human" or context.obj["non_interactive"]):
        raise click.UsageError("--open requires Human interactive mode")
    request = ShowRunRequest(current_directory=Path.cwd(), run_id=run_id, platform=platform)
    shown = show_run(request)
    if open_report:
        generated = generate_run_html(GenerateRunHtmlRequest(**request.model_dump()))
        if not webbrowser.open((Path.cwd() / generated.html_path).as_uri()):
            raise ApplicationError(
                code=ApplicationErrorCode.RUN_REPORT_OPEN_FAILED,
                category=ApplicationErrorCategory.INTERNAL,
                message="The static Run report could not be opened.",
                action=f"Open {generated.html_path} manually.",
            )
        shown = shown.model_copy(update={"html_path": generated.html_path})
    if context.obj["output"] == "human":
        run = shown.run
        source = run.source.case_id or run.source.goal_summary or "—"
        click.echo(f"Run ID: {run.run_id}")
        click.echo(f"Platform: {run.platform}")
        click.echo(f"Mode: {run.mode}")
        click.echo(f"Status: {run.status}")
        click.echo(f"Started: {run.started_at.astimezone() if run.started_at else '—'}")
        click.echo(f"Completed: {run.completed_at.astimezone() if run.completed_at else '—'}")
        click.echo(f"Duration: {run.duration_ms if run.duration_ms is not None else '—'} ms")
        click.echo(f"Source: {source}")
        if run.result.summary:
            click.echo(f"Summary: {run.result.summary}")
        if run.runtime.provider or run.runtime.model:
            click.echo(f"Runtime: {run.runtime.provider or '—'} / {run.runtime.model or '—'}")
        for label, path in (
            ("Report", run.artifacts.report),
            ("Report Markdown", run.artifacts.report_markdown),
            ("Logs", run.artifacts.events),
            ("Evidence", run.artifacts.evidence_manifest),
            ("Suggestions", run.artifacts.suggestions),
            ("Candidate Case", run.artifacts.candidate_case),
            ("HTML", shown.html_path),
        ):
            if path:
                click.echo(f"{label}: .fsq/runs/{run.platform}/{run.run_id}/{Path(path).name}")
        for warning in shown.warnings:
            click.echo(f"Warning: {warning}")
    else:
        _emit_terminal(context, shown.model_dump(mode="json"))


@runs.command(name="logs")
@click.argument("run_id")
@click.option("--platform", type=PLATFORMS)
@click.option("--level", "levels", multiple=True)
@click.option("--phase", "phases", multiple=True)
@click.option("--limit", type=click.IntRange(1, 5000), default=200)
@click.pass_context
def runs_logs(context: click.Context, run_id: str, platform: str | None, levels: tuple[str, ...], phases: tuple[str, ...], limit: int) -> None:
    result = read_run_logs(ReadRunLogsRequest(current_directory=Path.cwd(), run_id=run_id, platform=platform, levels=levels, phases=phases, limit=limit))
    if context.obj["output"] == "human":
        for event in result.events:
            click.echo(f"{event.time or '—'}  {event.level or 'info'}  {event.phase or '—'}  {event.tool or event.label or '—'}  {event.status or '—'}  {(event.message or '')[:160]}")
    elif context.obj["output"] == "jsonl":
        terminal = {key: value for key, value in result.model_dump(mode="json").items() if key != "events"}
        _emit_terminal(context, terminal, events=[event.model_dump(mode="json") for event in result.events])
    else:
        _emit_terminal(context, result.model_dump(mode="json"))


def _workspace(context: click.Context) -> Path:
    try:
        return require_initialized_workspace(WorkspaceRequest(current_directory=Path.cwd())).workspace
    except ApplicationError as exc:
        _application_error(context, exc)
    raise AssertionError("unreachable")


def _render_doctor(result: object) -> None:
    click.echo(f"Workspace: {result.workspace.name}")
    click.echo(f"Status: {result.status}")
    check_labels = {
        "configuration": "Configuration",
        "runtime": "Runtime",
        "target_configuration": "Target configuration",
        "target_availability": "Target availability",
        "strict_core": "Strict core",
        "provider": "Provider",
        "suggestion_analyzer": "Suggestion analyzer",
        "dynamic_agent": "Dynamic agent",
    }
    command_labels = {
        "case_test": "fsq case test",
        "case_test_suggest": "fsq case test --suggest",
        "case_create": "fsq case create",
    }
    for platform in result.platforms:
        click.echo(f"\n{platform.platform.capitalize()}")
        if platform.prerequisites:
            labels = {
                "adb_cli": "ADB CLI",
                "uiautomator2_runtime": "uiautomator2 dependency",
                "adb_server": "Existing ADB server",
                "device_connection": "Device connection/authorization",
                "device_selection": "Device selection",
                "application_identifier": "Application ID",
                "application_installation": "Application installation",
                "xcode_installation": "Full Xcode",
                "xcode_developer_directory": "Xcode developer directory",
                "appium_cli": "Appium CLI",
                "appium_mac2_driver": "Appium Mac2 driver",
                "appium_endpoint": "Appium endpoint",
                "application_path": "Application path",
                "bundle_identifier": "Bundle identifier",
            }
            click.echo("  Prerequisites")
            for detail in platform.prerequisites:
                click.echo(f"    {labels.get(detail.identifier, detail.identifier):28} {detail.status}")
                if detail.status != "ready":
                    click.echo(f"      {detail.message}")
                if detail.status != "ready" and detail.action:
                    click.echo(f"      Action: {detail.action}")
                if detail.code:
                    click.echo(f"      Code: {detail.code}")
                for command in detail.commands:
                    click.echo(f"      Run manually: {command}")
        click.echo("  Checks")
        for name in check_labels:
            detail = getattr(platform.checks, name)
            click.echo(f"    {check_labels[name]:22} {detail.status}")
            if detail.status != "ready" and detail.message:
                click.echo(f"      {detail.message}")
            if detail.status != "ready" and detail.action:
                click.echo(f"      Action: {detail.action}")
        click.echo("  Commands")
        for name in command_labels:
            detail = getattr(platform.commands, name)
            click.echo(f"    {command_labels[name]:26} {detail.status}")
            if detail.status != "ready" and detail.message:
                click.echo(f"      {detail.message}")
            if detail.status != "ready" and detail.action:
                click.echo(f"      Action: {detail.action}")
    if result.actions:
        click.echo("\nActions")
        for action in result.actions:
            click.echo(f"  {action}")


def _emit(context: click.Context, value: object) -> None:
    output = context.obj["output"]
    if output == "human":
        if isinstance(value, list):
            for item in value:
                click.echo(json.dumps(item, ensure_ascii=False) if isinstance(item, dict) else item)
        else:
            click.echo(json.dumps(value, indent=2, ensure_ascii=False, default=str))
    else:
        status = value.get("status", "success") if isinstance(value, dict) else "success"
        warnings = value.get("warnings", []) if isinstance(value, dict) else []
        click.echo(
            json.dumps(
                result_record(value, operation=_context_operation(context), status=str(status), warnings=warnings),
                ensure_ascii=False,
                default=str,
            )
        )


def _emit_terminal(context: click.Context, value: object, *, events: list[dict[str, object]] | None = None) -> None:
    operation = _context_operation(context)
    status = value.get("status", "success") if isinstance(value, dict) else "success"
    warnings = value.get("warnings", []) if isinstance(value, dict) else []
    if context.obj["output"] == "jsonl":
        for event in events or []:
            click.echo(json.dumps(event_record(event, operation=operation), ensure_ascii=False, default=str))
        click.echo(
            json.dumps(
                result_record(value, operation=operation, status=str(status), warnings=warnings),
                ensure_ascii=False,
                default=str,
            )
        )
        return
    _emit(context, value)


def _application_error(context: click.Context, error: ApplicationError) -> None:
    if context.obj["output"] == "human":
        click.echo(f"Error: {error.message}", err=True)
        if error.action:
            click.echo(f"Action: {error.action}", err=True)
        _emit_safe_internal_diagnostic(error)
    else:
        click.echo(json.dumps(error.to_record(operation=_context_operation(context)), ensure_ascii=False, default=str))
    raise click.exceptions.Exit(_error_exit_code(error))


def _emit_safe_internal_diagnostic(error: ApplicationError) -> None:
    if error.code != ApplicationErrorCode.INTERNAL_ERROR:
        return
    exception_type = error.details.get("exception_type")
    if isinstance(exception_type, str) and exception_type:
        click.echo(f"Diagnostic: {exception_type}", err=True)


def _error_exit_code(error: ApplicationError) -> ExitCode:
    mapping = {
        "request_validation": ExitCode.USAGE,
        "workspace_configuration": ExitCode.WORKSPACE,
        "configuration": ExitCode.WORKSPACE,
        "unavailable": ExitCode.UNAVAILABLE,
        "internal": ExitCode.INTERNAL,
    }
    return mapping[error.category.value]


def _context_operation(context: click.Context) -> str:
    parts: list[str] = []
    current: click.Context | None = context
    while current is not None and current.parent is not None:
        parts.append(current.info_name or current.command.name or "unknown")
        current = current.parent
    return ".".join(reversed(parts)) or "fsq"


@case_group.command(name="format")
@click.argument("case_path", type=click.Path(path_type=Path))
@click.option("--check", is_flag=True, help="Validate and check canonical formatting (default).")
@click.option("--diff", "show_diff", is_flag=True, help="Preview the canonical formatting diff.")
@click.option("--write", is_flag=True, help="Validate and atomically write canonical formatting.")
@click.option("--json", "json_output", is_flag=True, help="Return structured static diagnostics and results.")
@click.pass_context
def case_format(context, case_path, check, show_diff, write, json_output):
    if json_output:
        context.obj["output"] = "json"
    if sum((check, show_diff, write)) > 1:
        raise click.UsageError("Choose only one of --check, --diff, or --write.")
    result = format_case(CaseFormatRequest(current_directory=Path.cwd(), case_path=case_path, mode="write" if write else "diff" if show_diff else "check"))
    if context.obj["output"] == "human":
        label = "Invalid Case" if not result.valid else "Formatted" if result.changed else "Already canonical" if result.formatted else "Formatting required"
        click.echo(f"{label}: {result.path}")
        click.echo(f"Scope: {result.validation_scope}")
        for warning in result.warnings:
            click.echo(f"Warning: {warning}")
        for diagnostic in result.diagnostics:
            click.echo(f"{diagnostic.code}: {diagnostic.field_path} {diagnostic.message}")
        if result.diff:
            click.echo(result.diff, nl=False)
    else:
        _emit_terminal(context, result.model_dump(mode="json"))
    if not result.valid:
        raise click.exceptions.Exit(2)
    if not result.formatted:
        raise click.exceptions.Exit(1)
