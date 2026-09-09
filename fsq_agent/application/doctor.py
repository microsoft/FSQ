# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from collections.abc import Callable
from pathlib import Path

from fsq_agent.agent import check_dynamic_agent_readiness
from fsq_agent.ai_services import check_case_suggestion_readiness
from fsq_agent.application.contracts import (
    ApplicationError,
    ApplicationErrorCategory,
    ApplicationErrorCode,
    DoctorChecks,
    DoctorCommands,
    DoctorPlatformResult,
    DoctorPrerequisite,
    DoctorRequest,
    DoctorResult,
    DoctorStatusDetail,
    DoctorWorkspaceSummary,
    RegisteredPlatformDoctorRequest,
)
from fsq_agent.config import inspect_registered_workspace, list_workspace_registry, load_workspace_platform_settings, validate_strict_core_settings
from fsq_agent.core import CapabilityDefinitionFactory, CapabilityRegistry, CommonPlatformTools
from fsq_agent.environments import PlatformRuntimeService
from fsq_agent.providers import check_provider_readiness

_PLATFORMS = ("android", "web", "windows", "macos")
_CHECK_ORDER = ("configuration", "runtime", "target_configuration", "target_availability", "strict_core", "provider", "suggestion_analyzer", "dynamic_agent")
_COMMAND_DEPENDENCIES = {
    "case_test": ("configuration", "runtime", "target_configuration", "target_availability", "strict_core"),
    "case_test_suggest": ("configuration", "runtime", "target_configuration", "target_availability", "strict_core", "provider", "suggestion_analyzer"),
    "case_create": ("configuration", "runtime", "target_configuration", "target_availability", "strict_core", "provider", "dynamic_agent"),
}


def diagnose_workspace(request: DoctorRequest) -> DoctorResult:
    root = request.current_directory.expanduser().resolve()
    try:
        entry = next((item for item in list_workspace_registry() if item.root_path.resolve() == root), None)
    except Exception as exc:
        raise _workspace_error() from exc
    if entry is None:
        raise ApplicationError(
            code=ApplicationErrorCode.WORKSPACE_NOT_INITIALIZED,
            category=ApplicationErrorCategory.WORKSPACE_CONFIGURATION,
            message="The current directory is not an initialized FSQ Workspace.",
            action="Run 'fsq init' here or change to an initialized FSQ Workspace.",
        )
    try:
        workspace_status = inspect_registered_workspace(entry.name, validate_target_paths=False)
    except Exception as exc:
        raise _workspace_error() from exc
    if workspace_status.root_path.resolve() != root or not workspace_status.platforms:
        raise _workspace_error()

    status_by_platform = {item.platform: item for item in workspace_status.platforms}
    platforms = tuple(_diagnose_platform(platform, root, status_by_platform[platform]) for platform in _PLATFORMS if platform in status_by_platform)
    result_status = _summary_status([command.status for item in platforms for command in (item.commands.case_test, item.commands.case_test_suggest, item.commands.case_create)])
    actions = tuple(dict.fromkeys(detail.action for item in platforms for detail in _details(item) if detail.action))
    return DoctorResult(
        status=result_status,
        workspace=DoctorWorkspaceSummary(name=entry.name, root=root),
        platforms=platforms,
        actions=actions,
    )


def diagnose_registered_platform(request: RegisteredPlatformDoctorRequest) -> DoctorResult:
    try:
        status = inspect_registered_workspace(request.workspace_name, request.user_config_root, validate_target_paths=False)
        selected = next(item for item in status.platforms if item.platform == request.platform)
    except Exception as exc:
        raise _workspace_error() from exc
    result = _diagnose_platform(request.platform, status.root_path, selected, request.user_config_root, target_id=request.target_id)
    return DoctorResult(
        status=result.status,
        workspace=DoctorWorkspaceSummary(name=status.name, root=status.root_path),
        platforms=(result,),
        actions=tuple(dict.fromkeys(item.action for item in _details(result) if item.action)),
    )


def _diagnose_platform(platform: str, root: Path, workspace_platform, user_config_root: Path | None = None, *, target_id: str | None = None) -> DoctorPlatformResult:
    unavailable = DoctorStatusDetail(status="error", code="doctor.configuration_invalid", message=workspace_platform.message, action=workspace_platform.action)
    if workspace_platform.status != "available":
        checks = DoctorChecks(configuration=unavailable, **{name: _blocked("configuration") for name in _CHECK_ORDER[1:]})
        commands = _commands(checks)
        return DoctorPlatformResult(platform=platform, status="unavailable", checks=checks, commands=commands)
    try:
        settings = load_workspace_platform_settings(root, platform, user_config_root) if user_config_root is not None else load_workspace_platform_settings(root, platform)
    except Exception:  # noqa: BLE001 - diagnostic isolation returns a safe check result.
        checks = DoctorChecks(configuration=unavailable, **{name: _blocked("configuration") for name in _CHECK_ORDER[1:]})
        commands = _commands(checks)
        return DoctorPlatformResult(platform=platform, status="unavailable", checks=checks, commands=commands)

    if platform == "android" and target_id is not None:
        settings = settings.model_copy(deep=True)
        settings.harness.android.serial = target_id
    return diagnose_platform_settings(settings)


def diagnose_platform_settings(settings) -> DoctorPlatformResult:
    platform = settings.harness.platform
    environment = PlatformRuntimeService()
    prerequisite_facts = _safe_prerequisites(environment, settings)
    prerequisites = tuple(DoctorPrerequisite.model_validate(item.model_dump(exclude={"target_id"})) for item in prerequisite_facts)
    runtime_check = _safe_check("runtime", lambda: _runtime(environment, platform))
    target_configuration = _configuration_check("target_configuration", lambda: environment.check_target_configuration(settings))
    target_availability = _safe_check("target_availability", lambda: environment.check_target_availability(settings, prerequisite_facts))
    foundation = (runtime_check, target_configuration, target_availability)
    strict_core = _dependent_check("strict_core", foundation, lambda: _strict(settings))
    provider = _safe_check("provider", lambda: check_provider_readiness(settings))
    suggestion = _safe_check("suggestion_analyzer", lambda: check_case_suggestion_readiness(settings))
    dynamic = _dependent_check("dynamic_agent", (*foundation, strict_core, provider), lambda: check_dynamic_agent_readiness(settings))
    checks = DoctorChecks(
        configuration=_ready("Platform configuration is ready."),
        runtime=runtime_check,
        target_configuration=target_configuration,
        target_availability=target_availability,
        strict_core=strict_core,
        provider=provider,
        suggestion_analyzer=suggestion,
        dynamic_agent=dynamic,
    )
    commands = _commands(checks)
    return DoctorPlatformResult(
        platform=platform,
        target_id=(getattr(getattr(settings.harness, "android", None), "serial", None) or next((item.target_id for item in prerequisite_facts if item.identifier == "device_selection"), None))
        if platform == "android"
        else None,
        status=_summary_status([commands.case_test.status, commands.case_test_suggest.status, commands.case_create.status]),
        prerequisites=prerequisites,
        checks=checks,
        commands=commands,
    )


def _runtime(environment: PlatformRuntimeService, platform: str) -> tuple[bool, str, str]:
    result = environment.check(platform)
    return result.ready, result.message, result.action or ""


def _safe_prerequisites(environment: PlatformRuntimeService, settings):
    try:
        return environment.check_prerequisites(settings)
    except Exception:  # noqa: BLE001 - diagnostic isolation returns one safe prerequisite fact.
        from fsq_agent.models import PlatformPrerequisiteCheck

        return (
            PlatformPrerequisiteCheck(
                identifier="prerequisite_diagnostics",
                status="error",
                message="Platform prerequisite diagnostics could not be completed safely.",
                action="Inspect the platform prerequisite installation manually.",
            ),
        )


def _strict(settings) -> tuple[bool, str, str]:
    validate_strict_core_settings(settings)
    definitions = [
        *CommonPlatformTools.capability_definitions(),
        *CapabilityDefinitionFactory().platform_definitions(platform=settings.harness.platform),
    ]
    CapabilityRegistry.from_definitions(definitions)
    return True, "Strict Core prerequisites are ready.", ""


def _safe_check(name: str, operation: Callable[[], tuple[bool, str, str]]) -> DoctorStatusDetail:
    try:
        ready, message, action = operation()
    except Exception:  # noqa: BLE001 - diagnostic isolation returns a safe check result.
        return DoctorStatusDetail(
            status="error",
            code=f"doctor.{name}_check_failed",
            message=f"The {name.replace('_', ' ')} check could not be completed safely.",
            action=f"Inspect detailed {name.replace('_', ' ')} diagnostics.",
        )
    if ready:
        return _ready(message)
    return DoctorStatusDetail(status="unavailable", code=f"doctor.{name}_unavailable", message=message, action=action or None)


def _configuration_check(name: str, operation: Callable[[], tuple[bool, str, str]]) -> DoctorStatusDetail:
    detail = _safe_check(name, operation)
    if detail.status != "unavailable":
        return detail
    return DoctorStatusDetail(status="error", code=f"doctor.{name}_invalid", message=detail.message, action=detail.action)


def _dependent_check(name: str, dependencies: tuple[DoctorStatusDetail, ...], operation: Callable[[], tuple[bool, str, str]]) -> DoctorStatusDetail:
    failed = next((detail for detail in dependencies if detail.status != "ready"), None)
    if failed is not None:
        return DoctorStatusDetail(status="unavailable", code=failed.code, message=failed.message, action=failed.action)
    return _safe_check(name, operation)


def _commands(checks: DoctorChecks) -> DoctorCommands:
    values = checks.model_dump()
    results = {}
    for command, dependencies in _COMMAND_DEPENDENCIES.items():
        failed = next((DoctorStatusDetail.model_validate(values[name]) for name in dependencies if values[name]["status"] != "ready"), None)
        results[command] = _ready("Command prerequisites are ready.") if failed is None else DoctorStatusDetail(status="unavailable", code=failed.code, message=failed.message, action=failed.action)
    return DoctorCommands.model_validate(results)


def _summary_status(statuses: list[str]) -> str:
    ready = statuses.count("ready")
    if ready == len(statuses):
        return "ready"
    return "partial" if ready else "unavailable"


def _ready(message: str) -> DoctorStatusDetail:
    return DoctorStatusDetail(status="ready", message=message)


def _blocked(dependency: str) -> DoctorStatusDetail:
    return DoctorStatusDetail(status="error", code="doctor.dependency_unavailable", message=f"Check blocked by {dependency}.")


def _details(platform: DoctorPlatformResult):
    return [
        *platform.prerequisites,
        *(getattr(platform.checks, name) for name in _CHECK_ORDER),
        *(getattr(platform.commands, name) for name in _COMMAND_DEPENDENCIES),
    ]


def _workspace_error() -> ApplicationError:
    return ApplicationError(
        code=ApplicationErrorCode.CONFIGURATION_INVALID,
        category=ApplicationErrorCategory.WORKSPACE_CONFIGURATION,
        message="The registered FSQ Workspace cannot be diagnosed reliably.",
        action="Repair the Workspace registry and root configuration.",
    )


__all__ = ["diagnose_platform_settings", "diagnose_registered_platform", "diagnose_workspace"]
