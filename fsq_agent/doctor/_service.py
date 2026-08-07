# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from __future__ import annotations

from collections.abc import Mapping
import importlib.util
import os
from pathlib import Path
import sys
import tomllib
from typing import Any

from fsq_agent.config import (
    Settings,
    initialize_workspace_safely,
    inspect_platform_settings,
    read_env_values,
    upsert_env_values_atomic,
)
from fsq_agent.core import PlatformProbeFactory
from fsq_agent.models import (
    DiagnosticProbeResult,
    DoctorCheckResult,
    DoctorFix,
    DoctorProgressEvent,
    DoctorProgressSink,
    DoctorReadiness,
    DoctorReadinessItem,
    DoctorRepairResult,
    DoctorReport,
    DoctorRequest,
    HarnessSettings,
)
from fsq_agent.providers import ProviderDiagnosticService
from fsq_agent.doctor._checks import compute_readiness, normalize_probes, sanitize_checks
from fsq_agent.doctor._environment import PLATFORM_ENV, effective_environment, platform_candidates
from fsq_agent.doctor._prompts import ConsoleDoctorPrompter, DoctorPrompter
from fsq_agent.doctor._repairs import REPAIR_AFFECTED_CHECKS, validate_platform_environment_value


_ALLOWED_REPAIR_ENV = {name for names in PLATFORM_ENV.values() for name in names} | {
    "FSQ_WINDOWS_BACKEND_KIND",
    "FSQ_WINDOWS_WINDOW_TITLE_RE",
    "FSQ_WINDOWS_LAUNCH_ARGS",
}
_PLATFORM_CHECK_IDS = {
    "android": ("android.adb.installed", "android.adb.devices", "android.uiautomator2", "android.package.installed"),
    "web": ("web.playwright", "web.browser.executable", "web.browser.startup"),
    "windows": (
        "windows.host", "windows.dependencies", "windows.app_path", "windows.backend",
        "windows.window_regex", "windows.launch_args", "windows.runtime_unverified",
    ),
    "macos": (
        "macos.host", "macos.dependencies", "macos.appium.status", "macos.appium.mac2",
        "macos.target", "macos.runtime_unverified",
    ),
}


class DoctorService:
    def __init__(
        self,
        *,
        provider_diagnostics: ProviderDiagnosticService | None = None,
        platform_probe_factory: PlatformProbeFactory | None = None,
        prompter: DoctorPrompter | None = None,
        environ: Mapping[str, str] | None = None,
        config_inspector: Any = inspect_platform_settings,
        workspace_initializer: Any = initialize_workspace_safely,
        environment_updater: Any = upsert_env_values_atomic,
        environment_checker: Any | None = None,
        effective_environment_loader: Any = effective_environment,
        progress_sink: DoctorProgressSink | None = None,
    ) -> None:
        self._provider_diagnostics_supplied = provider_diagnostics is not None
        self.provider_diagnostics = provider_diagnostics or ProviderDiagnosticService()
        self.platform_probe_factory = platform_probe_factory or PlatformProbeFactory()
        self.prompter = prompter or ConsoleDoctorPrompter()
        self.environ = dict(environ) if environ is not None else dict(os.environ)
        self.config_inspector = config_inspector
        self.workspace_initializer = workspace_initializer
        self.environment_updater = environment_updater
        self.environment_checker = environment_checker
        self.effective_environment_loader = effective_environment_loader
        self.progress_sink = progress_sink

    def run(self, request: DoctorRequest) -> DoctorReport:
        working_directory = request.working_directory.expanduser().resolve()
        platform, source, usage_check = self._resolve_platform(request, working_directory)
        if platform is None:
            checks = [usage_check] if usage_check else []
            for check in checks:
                self._started(check.id, "Resolving doctor platform selection...", check.category)
                self._completed(check)
            return self._report(request, None, "unresolved", checks, [], usage_error=True)

        self._emit(
            DoctorProgressEvent(
                event_type="phase_started",
                phase="Platform",
                summary=f"Platform: {platform} ({source})",
            )
        )

        checks, settings = self._diagnose(request, platform, working_directory)
        checks = _with_verification_commands(checks, platform, request.mode)
        repairs: list[DoctorRepairResult] = []
        if request.output_format != "json":
            repairs, interrupted = self._apply_repairs(
                request, platform, working_directory, checks, settings
            )
            if interrupted:
                return self._report(
                    request,
                    platform,
                    source,
                    checks,
                    repairs,
                    interrupted=True,
                )
            if any(repair.status == "applied" for repair in repairs):
                checks, settings = self._rerun_after_repairs(
                    request,
                    platform,
                    working_directory,
                    checks,
                    settings,
                    repairs,
                )
                checks = _with_verification_commands(checks, platform, request.mode)
                repairs = [
                    repair.model_copy(
                        update={
                            "rerun_check_ids": [
                                check.id
                                for check in checks
                                if check.id in REPAIR_AFFECTED_CHECKS[repair.action_id]
                                or repair.action_id == "environment.update"
                                and (check.id == "config.valid" or check.id.startswith(f"{platform}."))
                            ]
                        }
                    )
                    if repair.status == "applied"
                    else repair
                    for repair in repairs
                ]
        return self._report(request, platform, source, checks, repairs)

    def _resolve_platform(
        self,
        request: DoctorRequest,
        working_directory: Path,
    ) -> tuple[str | None, str, DoctorCheckResult | None]:
        if request.platform:
            return request.platform, "explicit", None
        candidates = platform_candidates(self._effective_environment(working_directory))
        if len(candidates) == 1:
            return candidates[0], "environment", None
        if request.interactive:
            options = candidates or list(PLATFORM_ENV)
            selected = self.prompter.choose("Select platform", options, options[0])
            return str(selected), "interactive", None
        summary = "No platform could be detected." if not candidates else "Multiple platform configurations were detected."
        check = DoctorCheckResult(
            id="doctor.platform_selection",
            category="Usage",
            status="fail",
            summary=summary,
            fixes=[
                DoctorFix(
                    description="Pass --platform explicitly.",
                    command="fsq-agent doctor --platform <android|web|windows|macos> --non-interactive",
                    verification_command="fsq-agent doctor --platform <android|web|windows|macos> --non-interactive",
                )
            ],
            metadata={"candidates": candidates},
        )
        return None, "unresolved", check

    def _diagnose(
        self,
        request: DoctorRequest,
        platform: str,
        working_directory: Path,
        *,
        include_base: bool = True,
        include_provider: bool = True,
        include_runtime_secrets: bool = True,
    ) -> tuple[list[DoctorCheckResult], Settings | None]:
        if include_base:
            self._phase("Environment")
        checks = (
            (self.environment_checker or self._environment_checks)(request, working_directory)
            if include_base
            else []
        )
        workspace = working_directory / ".fsq-agent-workspace"
        if include_base:
            self._phase("Workspace")
            self._started("workspace.initialized", "Checking the fsq-agent workspace...", "Workspace")
            workspace_check = self._workspace_check(workspace)
            checks.append(workspace_check)
            self._completed(workspace_check)
        self._phase("Configuration")
        settings, config_probes = self._inspect_config(
            platform, workspace, self._effective_environment(working_directory)
        )
        checks.extend(normalize_probes(config_probes))
        if settings is None:
            independent = self._independent_platform_checks(
                platform,
                working_directory,
                request.timeout_seconds,
                suppressed_check_ids={check.id for check in checks},
            )
            existing_ids = {check.id for check in checks}
            independent_by_id = {check.id: check for check in independent}
            checks.extend(check for check in independent if check.id not in existing_ids)
            actual_ids = existing_ids | set(independent_by_id)
            for check_id in _PLATFORM_CHECK_IDS[platform]:
                if check_id not in actual_ids:
                    skip_check = DoctorCheckResult(
                            id=check_id,
                            category=platform.title(),
                            status="skip",
                            summary="Check was skipped because normalized platform settings are unavailable.",
                            affected_targets=["dynamic", "strict", "ai_assertion"],
                            prerequisite_ids=["config.valid"],
                        )
                    checks.append(skip_check)
                    self._started(skip_check.id, f"Skipping {skip_check.id}...", skip_check.category)
                    self._completed(skip_check)
            if request.mode in {"dynamic", "all"}:
                provider_skip = DoctorCheckResult(
                        id="provider.probe",
                        category="Provider",
                        status="skip",
                        summary="Provider checks were skipped because normalized settings are unavailable.",
                        affected_targets=["dynamic", "ai_assertion"],
                        prerequisite_ids=["config.valid"],
                    )
                checks.append(provider_skip)
                self._started(provider_skip.id, "Skipping provider checks...", "Provider")
                self._completed(provider_skip)
            return checks, None
        try:
            self._phase(platform.title())
            platform_probe = self._create_platform_probe(settings.harness.platform, settings.harness)
            checks.extend(normalize_probes(platform_probe.probe(request.timeout_seconds)))
        except Exception as exc:  # noqa: BLE001 - diagnostic boundary
            self._started(f"{platform}.probe", "Starting the platform diagnostic probe...", platform.title())
            failure = DoctorCheckResult(
                    id=f"{platform}.probe",
                    category=platform.title(),
                    status="fail",
                    summary=f"Platform probe failed ({type(exc).__name__}).",
                    affected_targets=["dynamic", "strict", "ai_assertion"],
                    fixes=[DoctorFix(description="Correct the platform setup and rerun doctor.")],
                )
            checks.append(failure)
            self._completed(failure)
        if include_provider and request.mode in {"dynamic", "all"}:
            try:
                self._phase("Provider")
                if not self._provider_diagnostics_supplied:
                    self.provider_diagnostics = ProviderDiagnosticService(
                        environ=self._effective_environment(working_directory)
                    )
                checks.extend(normalize_probes(self._probe_provider(settings, request.timeout_seconds)))
            except Exception as exc:  # noqa: BLE001 - diagnostic boundary
                self._started("provider.probe", "Starting provider diagnostics...", "Provider")
                failure = DoctorCheckResult(
                        id="provider.probe",
                        category="Provider",
                        status="fail",
                        summary=f"Provider probe failed ({type(exc).__name__}).",
                        affected_targets=["dynamic", "ai_assertion"],
                        fixes=[DoctorFix(description="Run provider initialization and rerun doctor.")],
                    )
                checks.append(failure)
                self._completed(failure)
        effective_environment_values = self._effective_environment(working_directory)
        missing_secrets = [
            name for name in settings.runtime_secrets.allowed_env_names if not effective_environment_values.get(name)
        ]
        if include_runtime_secrets and missing_secrets:
            self._started("runtime_secrets.presence", "Checking runtime-secret presence...", "Configuration")
            secret_check = DoctorCheckResult(
                    id="runtime_secrets.presence",
                    category="Configuration",
                    status="warn",
                    summary="Some allowlisted runtime-secret names have no current value.",
                    affected_targets=["strict"],
                    fixes=[DoctorFix(description="Set required runtime-secret variables before running cases that reference them.")],
                    metadata={"missing_names": missing_secrets},
                )
            checks.append(secret_check)
            self._completed(secret_check)
        return checks, settings

    def _independent_platform_checks(
        self,
        platform: str,
        working_directory: Path,
        timeout_seconds: float,
        suppressed_check_ids: set[str] | None = None,
    ) -> list[DoctorCheckResult]:
        harness = HarnessSettings(platform=platform)  # type: ignore[arg-type]
        effective = self._effective_environment(working_directory)
        if value := effective.get("FSQ_ANDROID_APP_ID"):
            harness.android.app_id = value
        if value := effective.get("FSQ_ANDROID_SERIAL"):
            harness.android.serial = value
        if value := effective.get("FSQ_WEB_BROWSER_EXECUTABLE_PATH"):
            harness.web.browser_executable_path = value
        if value := effective.get("FSQ_WINDOWS_APP_PATH"):
            harness.windows.app_path = Path(value)
        if value := effective.get("FSQ_WINDOWS_BACKEND_KIND"):
            harness.windows.backend_kind = value
        if value := effective.get("FSQ_WINDOWS_WINDOW_TITLE_RE"):
            harness.windows.window_title_re = value
        if value := effective.get("FSQ_WINDOWS_LAUNCH_ARGS"):
            try:
                from fsq_agent.config import validate_doctor_environment_value

                validate_doctor_environment_value("FSQ_WINDOWS_LAUNCH_ARGS", value)
                harness.windows.launch_args = [value]
            except Exception:
                pass
        if value := effective.get("FSQ_MACOS_APPIUM_SERVER_URL"):
            harness.macos.appium_server_url = value
        if value := effective.get("FSQ_MACOS_BUNDLE_ID"):
            harness.macos.bundle_id = value
        if value := effective.get("FSQ_MACOS_APP_PATH"):
            harness.macos.app_path = value
        try:
            self._phase(platform.title())
            probe = self._create_platform_probe(
                platform,
                harness,
                suppressed_check_ids=suppressed_check_ids,
            )  # type: ignore[arg-type]
            return normalize_probes(probe.probe(timeout_seconds))
        except Exception as exc:  # noqa: BLE001 - independent diagnostic boundary
            return [
                DoctorCheckResult(
                    id=f"{platform}.probe",
                    category=platform.title(),
                    status="fail",
                    summary=f"Independent platform probe failed ({type(exc).__name__}).",
                    affected_targets=["dynamic", "strict", "ai_assertion"],
                    fixes=[DoctorFix(description="Correct the platform setup and rerun doctor.")],
                )
            ]

    def _rerun_after_repairs(
        self,
        request: DoctorRequest,
        platform: str,
        working_directory: Path,
        checks: list[DoctorCheckResult],
        settings: Settings | None,
        repairs: list[DoctorRepairResult],
    ) -> tuple[list[DoctorCheckResult], Settings | None]:
        final_by_id = {check.id: check for check in checks}
        applied = {repair.action_id for repair in repairs if repair.status == "applied"}
        if "workspace.initialize" in applied:
            self._started("workspace.initialized", "Rechecking the fsq-agent workspace...", "Workspace")
            workspace_check = self._workspace_check(
                working_directory / ".fsq-agent-workspace"
            )
            final_by_id["workspace.initialized"] = workspace_check
            self._completed(workspace_check)
        if "environment.update" in applied:
            rerun, settings = self._diagnose(
                request,
                platform,
                working_directory,
                include_base=False,
                include_provider=False,
                include_runtime_secrets=False,
            )
            final_by_id.update({check.id: check for check in rerun})
        if "provider.refresh_copilot_token" in applied and settings is not None:
            rerun = normalize_probes(
                self._probe_provider(settings, request.timeout_seconds)
            )
            final_by_id.update({check.id: check for check in rerun})
        ordered_ids = [check.id for check in checks]
        for check_id in final_by_id:
            if check_id not in ordered_ids:
                ordered_ids.append(check_id)
        return [final_by_id[check_id] for check_id in ordered_ids], settings

    def _effective_environment(self, working_directory: Path) -> dict[str, str]:
        return self.effective_environment_loader(working_directory, self.environ)

    def _inspect_config(
        self,
        platform: str,
        workspace: Path,
        environ: dict[str, str],
    ) -> tuple[Settings | None, list[DiagnosticProbeResult]]:
        try:
            return self.config_inspector(
                platform,
                workspace,
                environ,
                progress_sink=self.progress_sink,
            )
        except TypeError as exc:
            if "progress_sink" not in str(exc):
                raise
            result = self.config_inspector(platform, workspace, environ)
            for check in normalize_probes(result[1]):
                self._completed(check)
            return result

    def _create_platform_probe(
        self,
        platform: str,
        harness: HarnessSettings,
        *,
        suppressed_check_ids: set[str] | None = None,
    ) -> Any:
        sink = self.progress_sink
        if sink is not None and suppressed_check_ids:
            sink = lambda event: None if event.check_id in suppressed_check_ids else self._emit(event)
        try:
            return self.platform_probe_factory.create(
                platform,
                harness,
                progress_sink=sink,
            )
        except TypeError as exc:
            if "progress_sink" not in str(exc):
                raise
            return self.platform_probe_factory.create(platform, harness)

    def _probe_provider(
        self,
        settings: Settings,
        timeout_seconds: float,
    ) -> list[DiagnosticProbeResult]:
        try:
            return self.provider_diagnostics.probe(
                settings,
                timeout_seconds,
                progress_sink=self.progress_sink,
            )
        except TypeError as exc:
            if "progress_sink" not in str(exc):
                raise
            results = self.provider_diagnostics.probe(settings, timeout_seconds)
            for check in normalize_probes(results):
                self._completed(check)
            return results

    def _phase(self, phase: str) -> None:
        self._emit(
            DoctorProgressEvent(
                event_type="phase_started",
                phase=phase,
                summary=phase,
            )
        )

    def _started(self, check_id: str, summary: str, phase: str) -> None:
        self._emit(
            DoctorProgressEvent(
                event_type="check_started",
                phase=phase,
                check_id=check_id,
                summary=summary,
            )
        )

    def _completed(self, check: DoctorCheckResult) -> None:
        self._emit(
            DoctorProgressEvent(
                event_type="check_completed",
                phase=check.category,
                check_id=check.id,
                status=check.status,
                summary=check.summary,
                check=check,
            )
        )

    def _emit(self, event: DoctorProgressEvent) -> None:
        if self.progress_sink is None:
            return
        try:
            self.progress_sink(event)
        except Exception:
            pass

    def _environment_checks(self, request: DoctorRequest, working_directory: Path) -> list[DoctorCheckResult]:
        self._started("environment.python_version", "Checking the Python version...", "Environment")
        version_ok = sys.version_info >= (3, 11)
        checks = [
            DoctorCheckResult(
                id="environment.python_version",
                category="Environment",
                status="pass" if version_ok else "fail",
                summary=f"Python {sys.version_info.major}.{sys.version_info.minor} {'satisfies' if version_ok else 'does not satisfy'} >=3.11.",
                affected_targets=["dynamic", "strict", "ai_assertion"],
                fixes=[] if version_ok else [DoctorFix(description="Install Python 3.11 or newer and recreate the project environment.")],
            )
        ]
        self._completed(checks[-1])
        self._started("environment.core_dependencies", "Checking core Python dependencies...", "Environment")
        core_imports = ["pydantic", "click", "yaml", "jinja2"]
        missing = [name for name in core_imports if importlib.util.find_spec(name) is None]
        checks.append(
            DoctorCheckResult(
                id="environment.core_dependencies",
                category="Environment",
                status="fail" if missing else "pass",
                summary="Core Python dependencies are installed." if not missing else "Core Python dependencies are missing.",
                affected_targets=["dynamic", "strict", "ai_assertion"],
                fixes=[] if not missing else [DoctorFix(description="Sync the project environment.", command="uv sync --extra dev")],
                metadata={"missing": missing},
            )
        )
        self._completed(checks[-1])
        self._started("environment.install_provenance", "Checking install and interpreter provenance...", "Environment")
        project_path = working_directory / "pyproject.toml"
        source_checkout = project_path.is_file()
        project_metadata_valid = True
        if source_checkout:
            try:
                project_data = tomllib.loads(project_path.read_text(encoding="utf-8"))
                project = project_data.get("project", {})
                project_metadata_valid = bool(
                    isinstance(project, dict)
                    and project.get("name") == "fsq-agent"
                    and isinstance(project.get("requires-python"), str)
                )
            except (OSError, UnicodeError, tomllib.TOMLDecodeError):
                project_metadata_valid = False
        package_spec = importlib.util.find_spec("fsq_agent")
        package_origin = Path(package_spec.origin).resolve() if package_spec and package_spec.origin else None
        package_matches_checkout = bool(
            not source_checkout
            or package_origin is not None
            and working_directory in (package_origin, *package_origin.parents)
        )
        expected_venv = working_directory / ".venv"
        interpreter_matches_checkout = bool(
            not source_checkout
            or expected_venv.exists()
            and expected_venv.resolve() in (Path(sys.executable).resolve(), *Path(sys.executable).resolve().parents)
        )
        provenance_ready = project_metadata_valid and package_matches_checkout and interpreter_matches_checkout
        checks.append(
            DoctorCheckResult(
                id="environment.install_provenance",
                category="Environment",
                status="pass" if provenance_ready else "fail",
                summary=(
                    "Source checkout and interpreter provenance are coherent."
                    if source_checkout and provenance_ready
                    else "Installed-package execution context detected."
                    if provenance_ready
                    else "The active interpreter or fsq-agent package does not belong to this source checkout."
                ),
                affected_targets=["dynamic", "strict", "ai_assertion"],
                fixes=[]
                if provenance_ready
                else [
                    DoctorFix(
                        description="Run fsq-agent through the synced project environment.",
                        command="uv run fsq-agent doctor --platform <platform> --non-interactive",
                    )
                ],
                metadata={
                    "source_checkout": source_checkout,
                    "project_metadata_valid": project_metadata_valid,
                    "package_from_checkout": package_matches_checkout,
                    "interpreter_from_checkout": interpreter_matches_checkout,
                },
            )
        )
        self._completed(checks[-1])
        return checks

    def _workspace_check(self, workspace: Path) -> DoctorCheckResult:
        marker = workspace / ".fsq-agent-workspace"
        if workspace.is_dir() and marker.is_file():
            return DoctorCheckResult(
                id="workspace.initialized",
                category="Workspace",
                status="pass",
                summary="The fsq-agent workspace is initialized.",
                affected_targets=["dynamic", "strict", "ai_assertion"],
            )
        if workspace.exists() and not workspace.is_dir():
            summary = "The workspace path is not a directory."
            repair_action = None
        elif workspace.is_dir() and any(workspace.iterdir()):
            summary = "The non-empty workspace is not marked as fsq-agent managed."
            repair_action = None
        else:
            summary = "The fsq-agent workspace is not initialized."
            repair_action = "workspace.initialize"
        return DoctorCheckResult(
            id="workspace.initialized",
            category="Workspace",
            status="fail",
            summary=summary,
            affected_targets=["dynamic", "strict", "ai_assertion"],
            fixes=[
                DoctorFix(
                    description="Initialize the current-directory fsq-agent workspace.",
                    command="fsq-agent init --platform <platform>",
                    repair_action=repair_action,
                )
            ],
        )

    def _apply_repairs(
        self,
        request: DoctorRequest,
        platform: str,
        working_directory: Path,
        checks: list[DoctorCheckResult],
        settings: Settings | None,
    ) -> tuple[list[DoctorRepairResult], bool]:
        repairs: list[DoctorRepairResult] = []
        try:
            for check in checks:
                for fix in check.fixes:
                    action = fix.repair_action
                    if not action and (request.interactive or request.repair) and fix.environment_variable in _ALLOWED_REPAIR_ENV:
                        action = "environment.update"
                    if not action:
                        continue
                    should_apply = request.repair or (
                        request.interactive and self.prompter.confirm(f"Apply repair for: {check.summary}", True)
                    )
                    if not should_apply:
                        self._emit(
                            DoctorProgressEvent(
                                event_type="repair_started",
                                phase="Repair",
                                repair_action=action,
                                summary=f"Reviewing repair for {check.id}...",
                            )
                        )
                        repair = DoctorRepairResult(action_id=action, target=check.id, status="declined")
                        repairs.append(repair)
                        self._repair_completed(repair)
                        continue
                    if action != "provider.refresh_copilot_token":
                        self._emit(
                            DoctorProgressEvent(
                                event_type="repair_started",
                                phase="Repair",
                                repair_action=action,
                                summary=f"Applying repair for {check.id}...",
                            )
                        )
                    backup: Path | None = None
                    try:
                        if action == "workspace.initialize":
                            self.workspace_initializer(working_directory / ".fsq-agent-workspace")
                        elif action == "provider.refresh_copilot_token" and settings is not None:
                            try:
                                self.provider_diagnostics.refresh_cached_copilot_provider_token(
                                    settings,
                                    progress_sink=self.progress_sink,
                                )
                            except TypeError as exc:
                                if "progress_sink" not in str(exc):
                                    raise
                                self.provider_diagnostics.refresh_cached_copilot_provider_token(settings)
                        elif action == "environment.update" and request.interactive and fix.environment_variable:
                            value = self.prompter.text(f"Enter {fix.environment_variable}")
                            if not value:
                                repair = DoctorRepairResult(action_id=action, target=check.id, status="skipped")
                                repairs.append(repair)
                                self._repair_completed(repair)
                                continue
                            validate_platform_environment_value(fix.environment_variable, value)
                            update = self.environment_updater(
                                working_directory / ".env",
                                {fix.environment_variable: value},
                                backup=True,
                            )
                            backup = update.backup_path
                        else:
                            repair = DoctorRepairResult(action_id=action, target=check.id, status="skipped")
                            repairs.append(repair)
                            self._repair_completed(repair)
                            continue
                    except Exception:  # noqa: BLE001 - repair failures are report data
                        repair = DoctorRepairResult(action_id=action, target=check.id, status="failed")
                    else:
                        repair = DoctorRepairResult(action_id=action, target=check.id, status="applied", backup_path=backup)
                    repairs.append(repair)
                    self._repair_completed(repair)
        except KeyboardInterrupt:
            return repairs, True
        return repairs, False

    def _repair_completed(self, repair: DoctorRepairResult) -> None:
        self._emit(
            DoctorProgressEvent(
                event_type="repair_completed",
                phase="Repair",
                repair_action=repair.action_id,
                status=repair.status,
                summary=f"Repair {repair.status}: {repair.action_id}",
                repair=repair,
            )
        )

    def _report(
        self,
        request: DoctorRequest,
        platform: str | None,
        source: str,
        checks: list[DoctorCheckResult],
        repairs: list[DoctorRepairResult],
        *,
        usage_error: bool = False,
        interrupted: bool = False,
    ) -> DoctorReport:
        checks = sanitize_checks(checks)
        readiness = compute_readiness(request.mode, checks)
        blocked = any(
            item.status == "blocked"
            for item in (readiness.dynamic_llm, readiness.strict_core, readiness.ai_assertion)
        )
        exit_code = 130 if interrupted else 2 if usage_error else 1 if blocked else 0
        summary = {status: sum(check.status == status for check in checks) for status in ("pass", "warn", "fail", "skip")}
        report = DoctorReport(
            platform=platform,
            platform_source=source,  # type: ignore[arg-type]
            requested_mode=request.mode,
            status="cancelled" if interrupted else "usage_error" if usage_error else "blocked" if blocked else "ready",
            exit_code=exit_code,
            checks=checks,
            repairs=repairs,
            readiness=readiness,
            summary=summary,
        )
        self._emit(
            DoctorProgressEvent(
                event_type="summary_ready",
                phase="Summary",
                status=report.status,
                summary="Doctor summary is ready.",
                report=report,
            )
        )
        return report


def _with_verification_commands(
    checks: list[DoctorCheckResult],
    platform: str,
    mode: str,
) -> list[DoctorCheckResult]:
    command = f"fsq-agent doctor --platform {platform} --mode {mode} --non-interactive"
    updated: list[DoctorCheckResult] = []
    for check in checks:
        fixes = [
            fix if fix.verification_command else fix.model_copy(update={"verification_command": command})
            for fix in check.fixes
        ]
        updated.append(check.model_copy(update={"fixes": fixes}))
    return updated


