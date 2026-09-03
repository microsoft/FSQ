# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from fsq_agent.config._paths import resolve_runtime_paths, resolve_workspace_runtime_paths
from fsq_agent.config._settings import Settings
from fsq_agent.config._user_provider import refresh_provider_settings
from fsq_agent.config._workspace import (
    WEB_CHANNEL_EXECUTABLE_NAMES,
    _is_macos_app_bundle_or_executable,
    load_workspace_config,
)
from fsq_agent.models import (
    AndroidWorkspaceTarget,
    ConfigurationError,
    MacOSWorkspaceTarget,
    WebWorkspaceTarget,
    WindowsWorkspaceTarget,
    WorkspaceSettings,
    web_executable_matches_channel,
)

DEFAULT_CONFIG_PATHS = (Path("config.yaml"), Path("config.yml"))
_PLATFORM_CONFIG_FILENAMES = {
    "android": "config.android.yaml",
    "web": "config.web.yaml",
    "windows": "config.windows.yaml",
    "macos": "config.macos.yaml",
}

_MACOS_APPIUM_SERVER_URL_ENV = "FSQ_MACOS_APPIUM_SERVER_URL"


def _package_config_root() -> Path:
    return Path(__file__).resolve().parent


def _package_skill_root() -> Path:
    return (Path(__file__).resolve().parents[1] / "resources" / "skills").resolve()


def _is_package_platform_config(path: Path) -> bool:
    resolved_path = path.expanduser().resolve()
    return any(resolved_path == preset_path.expanduser().resolve() for preset_path in PLATFORM_CONFIG_PATHS.values())


def _bind_package_skill_resources(settings: Settings) -> None:
    settings.agent_context.knowledge.skills.dir = _package_skill_root()


PLATFORM_CONFIG_PATHS = {platform: _package_config_root() / filename for platform, filename in _PLATFORM_CONFIG_FILENAMES.items()}
SUPPORTED_LLM_PROVIDERS = ("github_copilot", "azure_openai")


def _read_yaml(path: Path) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as config_file:
            data = yaml.safe_load(config_file) or {}
    except OSError as exc:
        raise ConfigurationError("Unable to read configuration file.", context={"path": str(path)}) from exc
    if not isinstance(data, dict):
        raise ConfigurationError("Configuration file must contain a YAML mapping.", context={"path": str(path)})
    return data


def _find_default_config() -> Path | None:
    for path in DEFAULT_CONFIG_PATHS:
        if path.exists():
            return path
    return None


def load_settings(
    path: str | Path | None = None,
    workspace: str | Path | None = None,
    user_config_root: str | Path | None = None,
) -> Settings:
    config_path = Path(path) if path is not None else _find_default_config()
    data = _read_yaml(config_path) if config_path else {}
    _reject_obsolete_settings(data)
    try:
        settings = Settings.model_validate(data)
    except ValidationError as exc:
        raise ConfigurationError("Invalid configuration.", context={"errors": exc.errors()}) from exc
    if workspace is not None:
        settings.workspace.root_dir = Path(workspace)
    settings = refresh_provider_settings(settings, user_config_root)
    base_dir = config_path.parent if config_path is not None else Path.cwd()
    resolve_runtime_paths(settings, base_dir)
    if config_path is not None and _is_package_platform_config(config_path):
        _bind_package_skill_resources(settings)
    return settings


def resolve_platform_config_path(platform: str) -> Path:
    platform_id = platform.strip().lower()
    config_path = PLATFORM_CONFIG_PATHS.get(platform_id)
    if config_path is None:
        raise ConfigurationError(
            "Unsupported harness platform.",
            context={"platform": platform, "supported": sorted(PLATFORM_CONFIG_PATHS)},
        )
    if not config_path.is_file():
        raise ConfigurationError(
            "Platform configuration file is missing.",
            context={"platform": platform_id, "path": str(config_path)},
        )
    return config_path


def load_platform_settings(
    platform: str,
    workspace: str | Path | None = None,
    user_config_root: str | Path | None = None,
) -> Settings:
    platform_id = platform.strip().lower()
    preset_path = resolve_platform_config_path(platform_id)
    settings = load_settings(preset_path, workspace, user_config_root)
    if settings.harness.platform != platform_id:
        raise ConfigurationError(
            "Platform configuration does not match requested platform.",
            context={"platform": platform_id, "configured_platform": settings.harness.platform},
        )
    return settings


def load_workspace_platform_settings(
    workspace: str | Path,
    platform: str,
    user_config_root: str | Path | None = None,
) -> Settings:
    platform_id = platform.strip().lower()
    workspace_config, workspace_root, workspace_config_path = load_workspace_config(workspace, platform_id)
    preset_path = resolve_platform_config_path(platform_id)
    preset_data = _read_yaml(preset_path)
    _reject_obsolete_settings(preset_data)
    _reject_workspace_owned_preset_settings(preset_data)
    try:
        settings = Settings.model_validate(preset_data)
    except ValidationError as exc:
        raise ConfigurationError("Invalid platform preset.", context={"errors": exc.errors()}) from exc
    _bind_package_skill_resources(settings)

    settings.workspace = WorkspaceSettings(root_dir=workspace_root, config_path=workspace_config_path)
    settings.harness.platform = platform_id
    target = workspace_config.target
    if isinstance(target, AndroidWorkspaceTarget):
        settings.harness.android.app_id = target.app_id
    elif isinstance(target, WebWorkspaceTarget):
        settings.harness.web.channel = target.browser_channel
        settings.harness.web.browser_executable_path = target.browser_executable_path
    elif isinstance(target, WindowsWorkspaceTarget):
        settings.harness.windows.app_path = target.app_path
        settings.harness.windows.window_title_re = target.window_title_re
        settings.harness.windows.launch_args = _parse_windows_launch_args(target.launch_args) if target.launch_args else []
    elif isinstance(target, MacOSWorkspaceTarget):
        settings.harness.macos.bundle_id = target.bundle_id
        settings.harness.macos.app_path = target.app_path

    settings.runtime_secrets.set_values(workspace_config.env)
    settings = refresh_provider_settings(settings, user_config_root)
    resolve_workspace_runtime_paths(settings, workspace_root, preset_path.parent, platform_id)
    return _apply_operator_environment(settings, platform_id)


def _apply_operator_environment(settings: Settings, platform_id: str) -> Settings:
    if platform_id == "macos":
        operator_url = os.environ.get(_MACOS_APPIUM_SERVER_URL_ENV)
        if operator_url and operator_url.strip():
            settings.harness.macos.appium_server_url = operator_url.strip()
    return settings


def _reject_workspace_owned_preset_settings(data: dict[str, Any]) -> None:
    for key in ("workspace", "cases", "output", "runtime_secrets"):
        if key in data:
            raise ConfigurationError(
                "Platform preset contains workspace-owned configuration.",
                context={"config_key": key},
            )


def _reject_obsolete_settings(data: dict[str, Any]) -> None:
    if "verification" in data:
        raise ConfigurationError(
            "Obsolete verification configuration is no longer supported.",
            context={"config_key": "verification", "removed_key": "verification.mode"},
        )
    harness = data.get("harness")
    if isinstance(harness, dict) and "strict_core" in harness:
        raise ConfigurationError(
            "Obsolete strict-core step interval configuration is no longer supported; use execution.post_action_delay_seconds instead.",
            context={"config_key": "harness.strict_core", "replacement_key": "execution.post_action_delay_seconds"},
        )
    openai_agents = data.get("openai_agents")
    if not isinstance(openai_agents, dict):
        return
    if "provider" in openai_agents:
        raise ConfigurationError(
            "Invalid configuration.",
            context={"config_key": "openai_agents.provider", "provider_source": "user_config"},
        )
    prompt = openai_agents.get("prompt")
    if not isinstance(prompt, dict):
        return
    for key in ("custom_instructions", "custom_instructions_path"):
        if key in prompt:
            raise ConfigurationError(
                "Obsolete custom instruction configuration is no longer supported; move guidance into knowledge/project.md or configured skills.",
                context={"config_key": f"openai_agents.prompt.{key}"},
            )


def _parse_windows_launch_args(value: str) -> list[str]:
    try:
        return _split_windows_command_line(value)
    except ValueError as exc:
        raise ConfigurationError(
            "Windows workspace launch arguments could not be parsed.",
            context={"config_key": "target.launch_args", "error": str(exc)},
        ) from exc


def _split_windows_command_line(value: str) -> list[str]:
    args: list[str] = []
    arg: list[str] = []
    in_quotes = False
    token_started = False
    index = 0
    length = len(value)
    while index < length:
        char = value[index]
        if char.isspace() and not in_quotes:
            if token_started:
                args.append("".join(arg))
                arg = []
                token_started = False
            index += 1
            continue
        if char == "\\":
            slash_start = index
            while index < length and value[index] == "\\":
                index += 1
            slash_count = index - slash_start
            if index < length and value[index] == '"':
                arg.extend("\\" * (slash_count // 2))
                if slash_count % 2:
                    arg.append('"')
                else:
                    in_quotes = not in_quotes
                token_started = True
                index += 1
                continue
            arg.extend("\\" * slash_count)
            token_started = True
            continue
        if char == '"':
            in_quotes = not in_quotes
            token_started = True
            index += 1
            continue
        arg.append(char)
        token_started = True
        index += 1
    if in_quotes:
        raise ValueError("No closing quotation")
    if token_started:
        args.append("".join(arg))
    return args


def validate_provider_settings(settings: Settings) -> None:
    _validate_openai_provider_settings(settings)


def validate_runtime_settings(settings: Settings) -> None:
    validate_provider_settings(settings)
    _validate_harness_settings(settings)


def validate_strict_core_settings(settings: Settings, requires_ai_assertion: bool = False) -> None:
    _validate_harness_settings(settings)
    if requires_ai_assertion:
        validate_provider_settings(settings)


def _validate_openai_provider_settings(settings: Settings) -> None:
    if settings.openai_agents.provider is None:
        raise ConfigurationError("Model Provider is not configured. Add a Provider in Control Plane Config.")
    if settings.openai_agents.provider not in SUPPORTED_LLM_PROVIDERS:
        raise ConfigurationError(
            "OpenAI Agents SDK provider is unsupported.",
            context={"provider": settings.openai_agents.provider, "supported": list(SUPPORTED_LLM_PROVIDERS)},
        )
    if not settings.openai_agents.model.strip():
        raise ConfigurationError(
            "OpenAI Agents SDK model deployment name is required.",
            context={"provider": settings.openai_agents.provider},
        )
    if settings.openai_agents.provider == "azure_openai" and not settings.openai_agents.base_url.endswith("/openai/v1/"):
        raise ConfigurationError(
            "Azure OpenAI base URL must use the /openai/v1/ form.",
            context={"base_url": settings.openai_agents.base_url},
        )
    api_key = settings.openai_agents.api_key
    if settings.openai_agents.provider == "azure_openai" and not api_key:
        raise ConfigurationError("Azure OpenAI API key is not configured.")
    if settings.openai_agents.provider == "azure_openai" and api_key and api_key.lower().startswith("replace-with"):
        raise ConfigurationError("Azure OpenAI API key still contains a placeholder value.")


def _validate_harness_settings(settings: Settings) -> None:
    if settings.harness.platform == "android":
        _validate_android_harness_settings(settings)
        return
    if settings.harness.platform == "web":
        _validate_web_harness_settings(settings)
        return
    if settings.harness.platform == "windows":
        _validate_windows_harness_settings(settings)
        return
    if settings.harness.platform == "macos":
        _validate_macos_harness_settings(settings)
        return
    raise ConfigurationError(
        "Unsupported harness platform.",
        context={"platform": settings.harness.platform, "supported": ["android", "web", "windows", "macos"]},
    )


def _validate_android_harness_settings(settings: Settings) -> None:
    if settings.harness.android.backend != "uiautomator2":
        raise ConfigurationError(
            "Unsupported Android harness backend.",
            context={"backend": settings.harness.android.backend, "supported": ["uiautomator2"]},
        )


def _validate_web_harness_settings(settings: Settings) -> None:
    if settings.harness.web.backend != "playwright":
        raise ConfigurationError(
            "Unsupported Web harness backend.",
            context={"backend": settings.harness.web.backend, "supported": ["playwright"]},
        )
    _validate_web_browser_executable_path(settings)


def _validate_web_browser_executable_path(settings: Settings) -> None:
    browser_path = settings.harness.web.browser_executable_path
    if browser_path is None:
        raise ConfigurationError(
            "Web browser executable path is not configured.",
            context={"config_key": "target.browser_executable_path", "channel": settings.harness.web.channel},
        )
    if not browser_path.exists():
        raise ConfigurationError(
            "Configured Web browser executable path does not exist.",
            context={"config_key": "target.browser_executable_path", "path": str(browser_path)},
        )
    if not browser_path.is_file():
        raise ConfigurationError(
            "Configured Web browser executable path must point to the browser executable file.",
            context={"config_key": "target.browser_executable_path", "path": str(browser_path)},
        )
    expected_names = WEB_CHANNEL_EXECUTABLE_NAMES[settings.harness.web.channel]
    if not web_executable_matches_channel(settings.harness.web.channel, browser_path):
        raise ConfigurationError(
            "Configured Web browser executable path does not match harness.web.channel.",
            context={
                "config_key": "target.browser_executable_path",
                "path": str(browser_path),
                "channel": settings.harness.web.channel,
                "expected_file_names": sorted(expected_names),
            },
        )
    if os.name != "nt" and not os.access(browser_path, os.X_OK):
        raise ConfigurationError(
            "Configured Web browser executable path is not executable.",
            context={"config_key": "target.browser_executable_path", "path": str(browser_path)},
        )


def _validate_windows_harness_settings(settings: Settings) -> None:
    if settings.harness.windows.backend != "pywinauto":
        raise ConfigurationError(
            "Unsupported Windows harness backend.",
            context={"backend": settings.harness.windows.backend, "supported": ["pywinauto"]},
        )
    app_path = settings.harness.windows.app_path
    if app_path is None:
        raise ConfigurationError(
            "Windows app path is not configured.",
            context={"config_key": "target.app_path"},
        )
    if not app_path.exists():
        raise ConfigurationError(
            "Configured Windows app path does not exist.",
            context={"config_key": "target.app_path", "path": str(app_path)},
        )
    if not app_path.is_file():
        raise ConfigurationError(
            "Configured Windows app path must point to the application executable file.",
            context={"config_key": "target.app_path", "path": str(app_path)},
        )


def _validate_macos_harness_settings(settings: Settings) -> None:
    if settings.harness.macos.backend != "appium_mac2":
        raise ConfigurationError(
            "Unsupported macOS harness backend.",
            context={"backend": settings.harness.macos.backend, "supported": ["appium_mac2"]},
        )
    if not settings.harness.macos.appium_server_url:
        raise ConfigurationError(
            "macOS Appium server URL is not configured in harness.macos.appium_server_url.",
            context={"config_key": "harness.macos.appium_server_url"},
        )
    app_path = settings.harness.macos.app_path
    bundle_id = settings.harness.macos.bundle_id
    if app_path is None and bundle_id is None:
        raise ConfigurationError(
            "macOS app identity is not configured.",
            context={"config_keys": ["target.bundle_id", "target.app_path"]},
        )
    if app_path is None:
        return
    if not app_path.exists():
        raise ConfigurationError(
            "Configured macOS app path does not exist.",
            context={"config_key": "target.app_path", "path": str(app_path)},
        )
    if not _is_macos_app_bundle_or_executable(app_path):
        raise ConfigurationError(
            "Configured macOS app path must point to an application bundle or executable.",
            context={"config_key": "target.app_path", "path": str(app_path)},
        )
