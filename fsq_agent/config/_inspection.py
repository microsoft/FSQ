# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from __future__ import annotations

from collections.abc import Mapping
import os
from pathlib import Path

from pydantic import ValidationError

from fsq_agent.config._env_file import read_env_values
from fsq_agent.config._loader import (
    ANDROID_APP_ID_ENV,
    ANDROID_SERIAL_ENV,
    AZURE_OPENAI_BASE_URL_ENV,
    AZURE_OPENAI_MODEL_ENV,
    GITHUB_COPILOT_MODEL,
    LLM_PROVIDER_ENV,
    MACOS_APPIUM_SERVER_URL_ENV,
    MACOS_APP_PATH_ENV,
    MACOS_BUNDLE_ID_ENV,
    PLATFORM_CONFIG_PATHS,
    SUPPORTED_LLM_PROVIDERS,
    WEB_BROWSER_EXECUTABLE_PATH_ENV,
    WINDOWS_APP_PATH_ENV,
    WINDOWS_BACKEND_KIND_ENV,
    WINDOWS_LAUNCH_ARGS_ENV,
    WINDOWS_WINDOW_TITLE_RE_ENV,
    _parse_windows_launch_args,
    _read_yaml,
    _reject_obsolete_settings,
    _validate_windows_backend_kind,
)
from fsq_agent.config._settings import Settings
from fsq_agent.models import (
    DiagnosticProbeResult,
    DoctorCheckResult,
    DoctorFix,
    DoctorProgressEvent,
    DoctorProgressSink,
)


def inspect_platform_settings(
    platform: str,
    workspace: str | Path,
    environ: Mapping[str, str] | None = None,
    progress_sink: DoctorProgressSink | None = None,
) -> tuple[Settings | None, list[DiagnosticProbeResult]]:
    platform_id = platform.strip().lower()
    config_path = PLATFORM_CONFIG_PATHS.get(platform_id)
    if config_path is None:
        _emit_started(progress_sink, "config.valid", "Checking the selected platform...")
        checks = [_failure("config.valid", "Unsupported platform.", "Use a supported --platform value.")]
        _emit_results(progress_sink, checks)
        return None, checks
    _emit_started(progress_sink, "config.valid", "Validating platform configuration...")
    config_path = config_path.resolve()
    if not config_path.is_file():
        checks = [
            _failure(
                "config.valid",
                "Platform configuration file is missing.",
                f"Restore {config_path.name} or run doctor from the repository/setup root.",
            )
        ]
        _emit_results(progress_sink, checks)
        return None, checks
    try:
        env_file_values = read_env_values(Path(workspace).expanduser().resolve().parent / ".env")
        effective = dict(env_file_values)
        effective.update({key: value for key, value in (environ or os.environ).items() if value.strip()})
        data = _read_yaml(config_path)
        _reject_obsolete_settings(data)
        settings = Settings.model_validate(data)
        settings.workspace.root_dir = Path(workspace).expanduser().resolve()
        _apply_effective_environment(settings, effective)
        _resolve_paths_without_creation(settings, config_path.parent)
        if settings.harness.platform != platform_id:
            checks = [
                _failure(
                    "config.valid",
                    "Platform preset does not match the selected platform.",
                    f"Restore {config_path.name} or select the configured platform.",
                )
            ]
            _emit_results(progress_sink, checks)
            return None, checks
    except Exception as exc:  # noqa: BLE001 - converted to a sanitized diagnostic boundary
        message = _safe_error_summary(exc)
        checks = [
            DiagnosticProbeResult(
                id="config.valid",
                category="Configuration",
                status="fail",
                summary=f"Configuration is invalid: {message}",
                fixes=[DoctorFix(description="Correct the reported configuration issue and rerun doctor.")],
                metadata={"config_file": config_path.name},
            )
        ]
        context = getattr(exc, "context", {})
        if isinstance(context, dict) and context.get("launch_args_env") == WINDOWS_LAUNCH_ARGS_ENV:
            checks.append(
                DiagnosticProbeResult(
                    id="windows.launch_args",
                    category="Windows",
                    status="fail",
                    summary="FSQ_WINDOWS_LAUNCH_ARGS could not be parsed.",
                    fixes=[
                        DoctorFix(
                            description="Correct the Windows launch argument quoting.",
                            environment_variable=WINDOWS_LAUNCH_ARGS_ENV,
                        )
                    ],
                )
            )
        _emit_results(progress_sink, checks)
        return None, checks
    checks = [
        DiagnosticProbeResult(
            id="config.valid",
            category="Configuration",
            status="pass",
            summary=f"{config_path.name} is valid for {platform_id}.",
            metadata={"config_file": config_path.name},
        )
    ]
    _emit_results(progress_sink, checks)
    return settings, checks


def _emit_results(
    sink: DoctorProgressSink | None,
    checks: list[DiagnosticProbeResult],
) -> None:
    for probe in checks:
        if probe.id not in {"config.valid", "config.platform", "config.preset"}:
            _emit(
                sink,
                DoctorProgressEvent(
                    event_type="check_started",
                    phase=probe.category,
                    check_id=probe.id,
                    summary=f"Checking {probe.id}...",
                ),
            )
        check = DoctorCheckResult.model_validate(probe.model_dump())
        _emit(
            sink,
            DoctorProgressEvent(
                event_type="check_completed",
                phase=check.category,
                check_id=check.id,
                status=check.status,
                summary=check.summary,
                check=check,
            ),
        )


def _emit_started(sink: DoctorProgressSink | None, check_id: str, summary: str) -> None:
    _emit(
        sink,
        DoctorProgressEvent(
            event_type="check_started",
            phase="Configuration",
            check_id=check_id,
            summary=summary,
        ),
    )


def _emit(sink: DoctorProgressSink | None, event: DoctorProgressEvent) -> None:
    if sink is None:
        return
    try:
        sink(event)
    except Exception:
        pass


def _apply_effective_environment(settings: Settings, env: Mapping[str, str]) -> None:
    provider = _value(env, LLM_PROVIDER_ENV)
    if provider:
        normalized = provider.lower()
        if normalized not in SUPPORTED_LLM_PROVIDERS:
            raise ValueError(f"{LLM_PROVIDER_ENV} must be one of {', '.join(SUPPORTED_LLM_PROVIDERS)}")
        settings.openai_agents.provider = normalized  # type: ignore[assignment]

    if value := _value(env, ANDROID_APP_ID_ENV):
        settings.harness.android.app_id = value
    if value := _value(env, ANDROID_SERIAL_ENV):
        settings.harness.android.serial = value
    if value := _value(env, WEB_BROWSER_EXECUTABLE_PATH_ENV):
        settings.harness.web.browser_executable_path = value
    if value := _value(env, WINDOWS_APP_PATH_ENV):
        settings.harness.windows.app_path = Path(value)
    if value := _value(env, WINDOWS_BACKEND_KIND_ENV):
        settings.harness.windows.backend_kind = _validate_windows_backend_kind(value)
    if value := _value(env, WINDOWS_WINDOW_TITLE_RE_ENV):
        settings.harness.windows.window_title_re = value
    if value := _value(env, WINDOWS_LAUNCH_ARGS_ENV):
        settings.harness.windows.launch_args = _parse_windows_launch_args(value)
    if value := _value(env, MACOS_APPIUM_SERVER_URL_ENV):
        settings.harness.macos.appium_server_url = value
    if value := _value(env, MACOS_BUNDLE_ID_ENV):
        settings.harness.macos.bundle_id = value
    if value := _value(env, MACOS_APP_PATH_ENV):
        settings.harness.macos.app_path = value

    if settings.openai_agents.provider == "github_copilot":
        settings.openai_agents.model = GITHUB_COPILOT_MODEL
        settings.openai_agents.base_url = ""
    else:
        settings.openai_agents.model = _value(env, AZURE_OPENAI_MODEL_ENV) or ""
        base_url = _value(env, AZURE_OPENAI_BASE_URL_ENV) or ""
        if "/openai/responses" in base_url:
            base_url = base_url.split("/openai/responses", 1)[0] + "/openai/v1/"
        elif "/openai/v1" in base_url:
            base_url = base_url.split("/openai/v1", 1)[0] + "/openai/v1/"
        elif base_url.endswith((".openai.azure.com", ".cognitiveservices.azure.com")):
            base_url = base_url.rstrip("/") + "/openai/v1/"
        settings.openai_agents.base_url = base_url


def _resolve_paths_without_creation(settings: Settings, base_dir: Path) -> None:
    workspace = Path(settings.workspace.root_dir or base_dir / ".fsq-agent-workspace").expanduser().resolve()
    settings.workspace.root_dir = workspace
    settings.output.root_dir = _resolve(settings.output.root_dir, workspace)
    settings.output.runs_dir = _resolve(settings.output.runs_dir, settings.output.root_dir)
    settings.cases.dir = _resolve(settings.cases.dir, base_dir)
    knowledge = settings.agent_context.knowledge
    knowledge.root_dir = _resolve(knowledge.root_dir, base_dir)
    knowledge.skills.dir = _resolve(knowledge.skills.dir, knowledge.root_dir)
    if knowledge.pre_plan.dir is not None:
        knowledge.pre_plan.dir = _resolve(knowledge.pre_plan.dir, knowledge.root_dir)
    prompt = settings.openai_agents.prompt
    if prompt.agent_template_path is not None:
        prompt.agent_template_path = _resolve(prompt.agent_template_path, base_dir)
    if prompt.task_template_path is not None:
        prompt.task_template_path = _resolve(prompt.task_template_path, base_dir)


def _resolve(path: Path, base: Path) -> Path:
    expanded = path.expanduser()
    return expanded.resolve() if expanded.is_absolute() else (base / expanded).resolve()


def _value(env: Mapping[str, str], name: str) -> str | None:
    value = env.get(name)
    return value.strip() if value and value.strip() else None


def _failure(check_id: str, summary: str, fix: str) -> DiagnosticProbeResult:
    return DiagnosticProbeResult(
        id=check_id,
        category="Configuration",
        status="fail",
        summary=summary,
        fixes=[DoctorFix(description=fix)],
    )


def _safe_error_summary(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        issues = []
        for error in exc.errors(include_url=False, include_context=False, include_input=False):
            location = ".".join(str(part) for part in error.get("loc", ())) or "<root>"
            issues.append(f"{location}: {error.get('msg', 'invalid value')}")
        return "; ".join(issues[:10]) or "settings validation failed"
    context = getattr(exc, "context", None)
    if isinstance(context, dict):
        safe = {key: value for key, value in context.items() if key not in {"value", "input", "api_key", "token"}}
        return f"{type(exc).__name__} ({', '.join(f'{key}={value}' for key, value in safe.items())})"
    return type(exc).__name__
