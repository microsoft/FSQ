# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from pathlib import Path
import os

import pytest

from fsq_agent.config import (
    initialize_workspace_safely,
    inspect_platform_settings,
    load_platform_settings,
    load_settings,
    read_env_values,
    upsert_env_values_atomic,
    validate_doctor_environment_value,
    validate_provider_settings,
    validate_runtime_settings,
    validate_strict_core_settings,
)
from fsq_agent.models import ConfigurationError


def _base_config(tmp_path: Path, body: str = "") -> str:
    return f"""
workspace:
  root_dir: {tmp_path.as_posix()}/workspace
cases:
  dir: cases
output:
  root_dir: output
{body}
"""


def _chrome_executable(tmp_path: Path) -> Path:
    chrome_path = tmp_path / "chrome.exe"
    chrome_path.write_text("", encoding="utf-8")
    chrome_path.chmod(0o755)
    return chrome_path


def _windows_executable(tmp_path: Path, name: str = "app.exe") -> Path:
    app_path = tmp_path / name
    app_path.write_text("", encoding="utf-8")
    return app_path


@pytest.fixture(autouse=True)
def _isolate_dotenv(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    for name in (
        "FSQ_LLM_PROVIDER", "AZURE_OPENAI_BASE_URL", "AZURE_OPENAI_MODEL", "AZURE_OPENAI_API_KEY",
        "FSQ_ANDROID_APP_ID", "FSQ_ANDROID_SERIAL", "FSQ_WEB_BROWSER_EXECUTABLE_PATH",
        "FSQ_WINDOWS_APP_PATH", "FSQ_WINDOWS_BACKEND_KIND", "FSQ_WINDOWS_WINDOW_TITLE_RE",
        "FSQ_WINDOWS_LAUNCH_ARGS", "FSQ_MACOS_APPIUM_SERVER_URL", "FSQ_MACOS_BUNDLE_ID",
        "FSQ_MACOS_APP_PATH",
    ):
        monkeypatch.delenv(name, raising=False)


def test_load_settings_from_yaml(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        _base_config(
            tmp_path,
            """
agent:
  name: test-agent
openai_agents:
  max_turns: 40
  tracing_enabled: false
""",
        ),
        encoding="utf-8",
    )

    settings = load_settings(config_path)

    assert settings.agent.name == "test-agent"
    assert settings.openai_agents.provider == "github_copilot"
    assert settings.openai_agents.model == "gpt-5.5"
    assert settings.openai_agents.max_turns == 40
    assert settings.openai_agents.tracing_enabled is False
    assert not hasattr(settings, "verification")
    assert not hasattr(settings, "cli_tools")
    assert not hasattr(settings, "shell")
    assert settings.workspace.root_dir == tmp_path / "workspace"
    assert (settings.workspace.root_dir / ".fsq-agent-workspace").exists()
    assert settings.output.root_dir == tmp_path / "workspace" / "output"
    assert settings.output.runs_dir == tmp_path / "workspace" / "output" / "runs"
    assert settings.output.runs_dir.exists()
    assert settings.cases.dir == tmp_path / "cases"
    assert not hasattr(settings, "pre_plan")
    assert settings.execution.post_action_delay_seconds.platform == 1.0
    assert settings.execution.post_action_delay_seconds.common == 0.0
    assert settings.case_lifecycle.on_case_start == []
    assert settings.case_lifecycle.on_case_complete == []
    assert settings.agent_context.knowledge.root_dir == tmp_path / "knowledge"
    assert settings.agent_context.knowledge.skills.dir == tmp_path / "knowledge" / "skills"
    assert settings.agent_context.knowledge.pre_plan.dir is None


def test_atomic_env_update_preserves_content_and_creates_backup(tmp_path: Path) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text("# local\r\nOTHER=keep\r\nFSQ_ANDROID_APP_ID=old\r\n", encoding="utf-8", newline="")

    update = upsert_env_values_atomic(env_path, {"FSQ_ANDROID_APP_ID": "com.example"})

    assert update.backup_path is not None and update.backup_path.is_file()
    assert read_env_values(env_path)["FSQ_ANDROID_APP_ID"] == "com.example"
    with env_path.open("r", encoding="utf-8", newline="") as stream:
        assert "# local\r\nOTHER=keep\r\n" in stream.read()


def test_atomic_env_update_refuses_malformed_file_without_backup(tmp_path: Path) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text("BROKEN\n", encoding="utf-8")

    with pytest.raises(ConfigurationError, match="expected KEY=VALUE"):
        upsert_env_values_atomic(env_path, {"FSQ_ANDROID_APP_ID": "com.example"})

    assert env_path.read_text(encoding="utf-8") == "BROKEN\n"
    assert list(tmp_path.glob(".env.*.bak")) == []


def test_initialize_workspace_safely_rejects_nonempty_unmarked_directory(tmp_path: Path) -> None:
    workspace = tmp_path / ".fsq-agent-workspace"
    workspace.mkdir()
    (workspace / "user.txt").write_text("keep", encoding="utf-8")

    with pytest.raises(ConfigurationError, match="not marked"):
        initialize_workspace_safely(workspace)


def test_initialize_workspace_safely_rejects_directory_marker(tmp_path: Path) -> None:
    workspace = tmp_path / ".fsq-agent-workspace"
    marker = workspace / ".fsq-agent-workspace"
    marker.mkdir(parents=True)

    with pytest.raises(ConfigurationError, match="marker must be a file"):
        initialize_workspace_safely(workspace)


def test_doctor_windows_launch_args_use_canonical_parser() -> None:
    validate_doctor_environment_value(
        "FSQ_WINDOWS_LAUNCH_ARGS",
        r'--profile="C:\Temp\Edge Profile" --flag',
    )

    with pytest.raises(ConfigurationError, match="could not be parsed"):
        validate_doctor_environment_value("FSQ_WINDOWS_LAUNCH_ARGS", '--profile="unterminated')


def test_inspect_platform_settings_does_not_create_workspace(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config.android.yaml").write_text("harness:\n  platform: android\n", encoding="utf-8")
    workspace = tmp_path / ".fsq-agent-workspace"

    settings, checks = inspect_platform_settings(
        "android",
        workspace,
        {"FSQ_ANDROID_APP_ID": "com.example"},
    )

    assert settings is not None
    assert checks[0].status == "pass"
    assert not workspace.exists()


def test_load_settings_accepts_case_lifecycle_hooks(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        _base_config(
            tmp_path,
            """
caseLifecycle:
  onCaseStart:
    runShell: ./scripts/config-before.sh
    runCase: hooks/config-before.codex.yaml
  onCaseComplete:
    - runCase: hooks/config-after.codex.yaml
    - runShell: ./scripts/config-after.sh
""",
        ),
        encoding="utf-8",
    )

    settings = load_settings(config_path)

    assert [[action.action_name, action.value] for action in settings.case_lifecycle.on_case_start[0].actions] == [
        ["runShell", "./scripts/config-before.sh"],
        ["runCase", "hooks/config-before.codex.yaml"],
    ]
    assert [[action.action_name, action.value] for action in settings.case_lifecycle.on_case_complete[0].actions] == [
        ["runCase", "hooks/config-after.codex.yaml"],
    ]
    assert [[action.action_name, action.value] for action in settings.case_lifecycle.on_case_complete[1].actions] == [
        ["runShell", "./scripts/config-after.sh"],
    ]


@pytest.mark.parametrize(
    "hook_yaml",
    [
        "onCaseStart: not-a-mapping",
        "onCaseStart:\n  unknown: value",
        "onCaseStart:\n  runCase: ''",
        "onCaseStart:\n  runShell: ''",
        "onCaseStart:\n  - []",
        "onCaseStart:\n  actions: []",
    ],
)
def test_load_settings_rejects_invalid_case_lifecycle_hooks(tmp_path: Path, hook_yaml: str) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        _base_config(
            tmp_path,
            f"""
caseLifecycle:
  {hook_yaml.replace(chr(10), chr(10) + '  ')}
""",
        ),
        encoding="utf-8",
    )

    with pytest.raises(ConfigurationError, match="Invalid configuration"):
        load_settings(config_path)


def test_config_example_is_reference_only_and_shows_case_lifecycle() -> None:
    example_path = Path(__file__).parents[1] / "config.example.yaml"

    assert example_path.exists()
    content = example_path.read_text(encoding="utf-8")
    assert "caseLifecycle:" in content
    assert "onCaseStart:" in content
    assert "onCaseComplete:" in content
    assert "reference" in content.casefold()


@pytest.mark.parametrize(
    ("config_name", "expected_max_turns"),
    [
        ("config.android.yaml", 100),
        ("config.web.yaml", 50),
        ("config.windows.yaml", 100),
        ("config.macos.yaml", 50),
    ],
)
def test_committed_platform_presets_define_max_turns(
    config_name: str, expected_max_turns: int, tmp_path: Path
) -> None:
    config_path = Path(__file__).parents[1] / config_name

    settings = load_settings(config_path, workspace=tmp_path / config_path.stem)

    assert settings.openai_agents.max_turns == expected_max_turns


def test_load_settings_ignores_config_example_by_default(tmp_path: Path) -> None:
        (tmp_path / "config.example.yaml").write_text(
                """
harness:
    platform: web
caseLifecycle:
    onCaseStart:
        runShell: echo should-not-load
""",
                encoding="utf-8",
        )

        settings = load_settings()

        assert settings.harness.platform == "android"
        assert settings.case_lifecycle.on_case_start == []


def test_load_platform_settings_loads_committed_platform_preset(tmp_path: Path) -> None:
    config_path = tmp_path / "config.web.yaml"
    config_path.write_text(
        _base_config(
            tmp_path,
            """
harness:
  platform: web
  web:
    backend: playwright
    base_url: https://www.bing.com
""",
        ),
        encoding="utf-8",
    )

    settings = load_platform_settings("web")

    assert settings.harness.platform == "web"
    assert settings.harness.web.backend == "playwright"
    assert settings.harness.web.base_url == "https://www.bing.com"


def test_load_platform_settings_rejects_missing_platform_preset() -> None:
    with pytest.raises(ConfigurationError, match="Platform configuration file is missing") as exc_info:
        load_platform_settings("windows")

    assert exc_info.value.context == {"platform": "windows", "path": "config.windows.yaml"}


def test_load_platform_settings_rejects_platform_mismatch(tmp_path: Path) -> None:
    config_path = tmp_path / "config.web.yaml"
    config_path.write_text(
        _base_config(
            tmp_path,
            """
harness:
  platform: android
""",
        ),
        encoding="utf-8",
    )

    with pytest.raises(ConfigurationError, match="does not match requested platform") as exc_info:
        load_platform_settings("web")

    assert exc_info.value.context == {"platform": "web", "configured_platform": "android"}


def test_load_settings_defaults_workspace_to_config_directory(tmp_path: Path) -> None:
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    config_path = project_dir / "config.yaml"
    config_path.write_text(
        """
cases:
  dir: cases
output:
  root_dir: output
""",
        encoding="utf-8",
    )

    settings = load_settings(config_path)

    expected_workspace = project_dir / ".fsq-agent-workspace"
    assert settings.workspace.root_dir == expected_workspace
    assert (expected_workspace / ".fsq-agent-workspace").exists()
    assert settings.output.root_dir == expected_workspace / "output"
    assert settings.output.runs_dir == expected_workspace / "output" / "runs"


def test_load_settings_rejects_non_empty_unmarked_workspace(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "user-file.txt").write_text("do not own this", encoding="utf-8")
    config_path = tmp_path / "config.yaml"
    config_path.write_text(_base_config(tmp_path), encoding="utf-8")

    with pytest.raises(ConfigurationError, match="not marked"):
        load_settings(config_path)


def test_load_settings_rejects_directory_workspace_marker(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    (workspace / ".fsq-agent-workspace").mkdir(parents=True)
    config_path = tmp_path / "config.yaml"
    config_path.write_text(_base_config(tmp_path), encoding="utf-8")

    with pytest.raises(ConfigurationError, match="marker must be a file"):
        load_settings(config_path)


def test_load_settings_accepts_android_backend_and_env_values(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FSQ_ANDROID_APP_ID", "com.example.app")
    monkeypatch.setenv("FSQ_ANDROID_SERIAL", "emulator-5554")
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        _base_config(
            tmp_path,
            """
harness:
  platform: android
  android:
    backend: uiautomator2
""",
        ),
        encoding="utf-8",
    )

    settings = load_settings(config_path)

    assert settings.harness.platform == "android"
    assert settings.harness.android.backend == "uiautomator2"
    assert settings.harness.android.app_id == "com.example.app"
    assert settings.harness.android.serial == "emulator-5554"


def test_load_settings_accepts_web_harness_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    chrome_path = _chrome_executable(tmp_path)
    monkeypatch.setenv("FSQ_WEB_BROWSER_EXECUTABLE_PATH", str(chrome_path))
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        _base_config(
            tmp_path,
            """
harness:
  platform: web
  web:
    backend: playwright
    channel: chrome
    headless: false
    base_url: https://www.bing.com
    viewport_width: 1280
    viewport_height: 720
""",
        ),
        encoding="utf-8",
    )

    settings = load_settings(config_path)

    validate_runtime_settings(settings)
    validate_strict_core_settings(settings)
    assert settings.harness.platform == "web"
    assert settings.harness.web.backend == "playwright"
    assert settings.harness.web.channel == "chrome"
    assert settings.harness.web.browser_executable_path == chrome_path
    assert settings.harness.web.headless is False
    assert settings.harness.web.base_url == "https://www.bing.com"
    assert settings.harness.web.viewport_width == 1280
    assert settings.harness.web.viewport_height == 720


def test_load_settings_accepts_web_chrome_channel(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    chrome_path = _chrome_executable(tmp_path)
    monkeypatch.setenv("FSQ_WEB_BROWSER_EXECUTABLE_PATH", str(chrome_path))
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        _base_config(
            tmp_path,
            """
harness:
  platform: web
  web:
    backend: playwright
    channel: chrome
""",
        ),
        encoding="utf-8",
    )

    settings = load_settings(config_path)

    validate_runtime_settings(settings)
    assert settings.harness.web.channel == "chrome"
    assert settings.harness.web.browser_executable_path == chrome_path


def test_load_settings_accepts_windows_env_backed_adapter_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app_path = _windows_executable(tmp_path)
    monkeypatch.setenv("FSQ_WINDOWS_APP_PATH", str(app_path))
    monkeypatch.setenv("FSQ_WINDOWS_BACKEND_KIND", "win32")
    monkeypatch.setenv("FSQ_WINDOWS_WINDOW_TITLE_RE", ".*Legacy App")
    monkeypatch.setenv(
        "FSQ_WINDOWS_LAUNCH_ARGS",
        r'--flag "two words" --profile="C:\Temp\Edge Profile" --kv=value',
    )
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        _base_config(
            tmp_path,
            """
harness:
  platform: windows
  windows:
    backend: pywinauto
    backend_kind: uia
    app_path: C:/Old/app.exe
    window_title_re: Old Title
    launch_args:
      - --old
""",
        ),
        encoding="utf-8",
    )

    settings = load_settings(config_path)

    validate_runtime_settings(settings)
    assert settings.harness.platform == "windows"
    assert settings.harness.windows.backend == "pywinauto"
    assert settings.harness.windows.app_path == app_path
    assert settings.harness.windows.backend_kind == "win32"
    assert settings.harness.windows.window_title_re == ".*Legacy App"
    assert settings.harness.windows.launch_args == [
        "--flag",
        "two words",
        r"--profile=C:\Temp\Edge Profile",
        "--kv=value",
    ]


def test_load_settings_rejects_invalid_windows_backend_kind_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app_path = _windows_executable(tmp_path)
    monkeypatch.setenv("FSQ_WINDOWS_APP_PATH", str(app_path))
    monkeypatch.setenv("FSQ_WINDOWS_BACKEND_KIND", "uia2")
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        _base_config(
            tmp_path,
            """
harness:
  platform: windows
  windows:
    backend: pywinauto
""",
        ),
        encoding="utf-8",
    )

    with pytest.raises(ConfigurationError, match="FSQ_WINDOWS_BACKEND_KIND"):
        load_settings(config_path)


def test_load_settings_rejects_invalid_windows_launch_args_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app_path = _windows_executable(tmp_path)
    monkeypatch.setenv("FSQ_WINDOWS_APP_PATH", str(app_path))
    monkeypatch.setenv("FSQ_WINDOWS_LAUNCH_ARGS", '"unterminated')
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        _base_config(
            tmp_path,
            """
harness:
  platform: windows
  windows:
    backend: pywinauto
""",
        ),
        encoding="utf-8",
    )

    with pytest.raises(ConfigurationError, match="FSQ_WINDOWS_LAUNCH_ARGS"):
        load_settings(config_path)


def test_validate_runtime_settings_rejects_missing_windows_app_path_env(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        _base_config(
            tmp_path,
            """
harness:
  platform: windows
  windows:
    backend: pywinauto
""",
        ),
        encoding="utf-8",
    )
    settings = load_settings(config_path)

    with pytest.raises(ConfigurationError, match="FSQ_WINDOWS_APP_PATH"):
        validate_runtime_settings(settings)


def test_load_settings_accepts_macos_harness_settings_and_env_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app_path = tmp_path / "Example.app"
    app_path.mkdir()
    monkeypatch.setenv("FSQ_MACOS_APPIUM_SERVER_URL", "http://127.0.0.1:4723")
    monkeypatch.setenv("FSQ_MACOS_BUNDLE_ID", "com.example.MacApp")
    monkeypatch.setenv("FSQ_MACOS_APP_PATH", str(app_path))
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        _base_config(
            tmp_path,
            """
harness:
  platform: macos
  macos:
    backend: appium_mac2
    page_source_max_depth: 8
    action_timeout_seconds: 15
""",
        ),
        encoding="utf-8",
    )

    settings = load_settings(config_path)

    validate_runtime_settings(settings)
    validate_strict_core_settings(settings)
    assert settings.harness.platform == "macos"
    assert settings.harness.macos.backend == "appium_mac2"
    assert settings.harness.macos.page_source_max_depth == 8
    assert settings.harness.macos.action_timeout_seconds == 15
    assert settings.harness.macos.appium_server_url == "http://127.0.0.1:4723"
    assert settings.harness.macos.bundle_id == "com.example.MacApp"
    assert settings.harness.macos.app_path == app_path


def test_load_settings_rejects_macos_env_backed_values_in_yaml(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        _base_config(
            tmp_path,
            """
harness:
  platform: macos
  macos:
    backend: appium_mac2
    appium_server_url: http://127.0.0.1:4723
    bundle_id: com.example.MacApp
""",
        ),
        encoding="utf-8",
    )

    with pytest.raises(ConfigurationError, match="Invalid configuration"):
        load_settings(config_path)


def test_validate_runtime_settings_rejects_missing_macos_env_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("FSQ_MACOS_APPIUM_SERVER_URL", raising=False)
    monkeypatch.delenv("FSQ_MACOS_BUNDLE_ID", raising=False)
    monkeypatch.delenv("FSQ_MACOS_APP_PATH", raising=False)
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        _base_config(
            tmp_path,
            """
harness:
  platform: macos
  macos:
    backend: appium_mac2
""",
        ),
        encoding="utf-8",
    )
    settings = load_settings(config_path)

    with pytest.raises(ConfigurationError, match="Appium server URL"):
        validate_runtime_settings(settings)

    settings.harness.macos.appium_server_url = "http://127.0.0.1:4723"
    with pytest.raises(ConfigurationError, match="app identity"):
        validate_runtime_settings(settings)


def test_validate_runtime_settings_rejects_missing_web_browser_executable_path(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        _base_config(
            tmp_path,
            """
harness:
  platform: web
  web:
    backend: playwright
    channel: chrome
""",
        ),
        encoding="utf-8",
    )
    settings = load_settings(config_path)

    with pytest.raises(ConfigurationError, match="executable path environment variable is not set"):
        validate_runtime_settings(settings)


def test_validate_runtime_settings_rejects_missing_web_browser_executable_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    missing_path = tmp_path / "chrome.exe"
    monkeypatch.setenv("FSQ_WEB_BROWSER_EXECUTABLE_PATH", str(missing_path))
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        _base_config(
            tmp_path,
            """
harness:
  platform: web
  web:
    backend: playwright
    channel: chrome
""",
        ),
        encoding="utf-8",
    )
    settings = load_settings(config_path)

    with pytest.raises(ConfigurationError, match="does not exist"):
        validate_runtime_settings(settings)


def test_validate_runtime_settings_rejects_web_browser_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FSQ_WEB_BROWSER_EXECUTABLE_PATH", str(tmp_path))
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        _base_config(
            tmp_path,
            """
harness:
  platform: web
  web:
    backend: playwright
    channel: chrome
""",
        ),
        encoding="utf-8",
    )
    settings = load_settings(config_path)

    with pytest.raises(ConfigurationError, match="browser executable file"):
        validate_runtime_settings(settings)


def test_validate_runtime_settings_rejects_web_browser_path_that_does_not_match_channel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    firefox_path = tmp_path / "firefox.exe"
    firefox_path.write_text("", encoding="utf-8")
    monkeypatch.setenv("FSQ_WEB_BROWSER_EXECUTABLE_PATH", str(firefox_path))
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        _base_config(
            tmp_path,
            """
harness:
  platform: web
  web:
    backend: playwright
    channel: chrome
""",
        ),
        encoding="utf-8",
    )
    settings = load_settings(config_path)

    with pytest.raises(ConfigurationError, match="does not match"):
        validate_runtime_settings(settings)


def test_load_settings_rejects_partial_web_viewport(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        _base_config(
            tmp_path,
            """
harness:
  platform: web
  web:
    viewport_width: 1280
""",
        ),
        encoding="utf-8",
    )

    with pytest.raises(ConfigurationError, match="Invalid configuration"):
        load_settings(config_path)


def test_load_settings_accepts_post_action_delay_defaults(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        _base_config(
            tmp_path,
            """
execution:
    post_action_delay_seconds:
        platform: 0
        common: 0.25
""",
        ),
        encoding="utf-8",
    )

    settings = load_settings(config_path)

    assert settings.execution.post_action_delay_seconds.platform == 0
    assert settings.execution.post_action_delay_seconds.common == 0.25


def test_load_settings_rejects_negative_post_action_delay(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        _base_config(
            tmp_path,
            """
execution:
    post_action_delay_seconds:
        platform: -0.1
""",
        ),
        encoding="utf-8",
    )

    with pytest.raises(ConfigurationError, match="Invalid configuration"):
        load_settings(config_path)


def test_load_settings_rejects_obsolete_strict_core_step_interval(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        _base_config(
            tmp_path,
            """
harness:
    strict_core:
        step_interval_seconds: 0
""",
        ),
        encoding="utf-8",
    )

    with pytest.raises(ConfigurationError, match="Obsolete strict-core step interval"):
        load_settings(config_path)


def test_load_settings_rejects_android_app_and_serial_in_yaml(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        _base_config(
            tmp_path,
            """
harness:
  android:
    backend: uiautomator2
    app_id: com.example.app
    serial: emulator-5554
""",
        ),
        encoding="utf-8",
    )

    with pytest.raises(ConfigurationError, match="Invalid configuration"):
        load_settings(config_path)


def test_load_settings_accepts_runtime_secret_allowlist(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        _base_config(
            tmp_path,
            """
runtime_secrets:
  allowed_env_names:
    - TEST_ACCOUNT_EMAIL
    - TEST_ACCOUNT_PASSWORD
""",
        ),
        encoding="utf-8",
    )

    settings = load_settings(config_path)

    assert settings.runtime_secrets.allowed_env_names == ["TEST_ACCOUNT_EMAIL", "TEST_ACCOUNT_PASSWORD"]


def test_load_settings_accepts_agent_context_knowledge_structure(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        _base_config(
            tmp_path,
            """
agent_context:
  knowledge:
    root_dir: ./knowledge
    skills:
      dir: custom-skills
      items:
        - name: automation-basics
          description: Semantic action and evidence guidance for local runs.
          kind: markdown
          path: automation-basics.md
          required: true
    pre_plan:
      dir: project_android_v1
""",
        ),
        encoding="utf-8",
    )

    settings = load_settings(config_path)

    assert settings.knowledge_dir == tmp_path / "knowledge"
    assert settings.agent_context.knowledge.root_dir == tmp_path / "knowledge"
    assert settings.agent_context.knowledge.skills.dir == tmp_path / "knowledge" / "custom-skills"
    assert [skill.name for skill in settings.skills] == ["automation-basics"]
    assert settings.agent_context.knowledge.pre_plan.dir == tmp_path / "knowledge" / "project_android_v1"


@pytest.mark.parametrize(
    "body",
    [
        """
skills:
  - name: automation-basics
    kind: markdown
    path: automation-basics.md
""",
        """
knowledge_dir: ./knowledge
""",
        """
pre_plan:
  knowledge_dir: ./knowledge/project_android_v1
""",
    ],
)
def test_load_settings_rejects_old_agent_context_yaml_keys(tmp_path: Path, body: str) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(_base_config(tmp_path, body), encoding="utf-8")

    with pytest.raises(ConfigurationError, match="Invalid configuration"):
        load_settings(config_path)


def test_load_settings_rejects_obsolete_verification_config(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        _base_config(
            tmp_path,
            """
verification:
  mode: strict
""",
        ),
        encoding="utf-8",
    )

    with pytest.raises(ConfigurationError, match="Obsolete verification configuration"):
        load_settings(config_path)


def test_azure_openai_endpoint_and_model_come_from_fixed_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AZURE_OPENAI_BASE_URL", "https://edgeqa-resource.cognitiveservices.azure.com/openai/responses?api-version=2025-04-01-preview")
    monkeypatch.setenv("AZURE_OPENAI_MODEL", "gpt-5.4")
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "dummy")
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        _base_config(
            tmp_path,
            """
openai_agents:
  provider: azure_openai
""",
        ),
        encoding="utf-8",
    )

    settings = load_settings(config_path)

    validate_runtime_settings(settings)
    assert settings.openai_agents.provider == "azure_openai"
    assert settings.openai_agents.base_url == "https://edgeqa-resource.cognitiveservices.azure.com/openai/v1/"
    assert settings.openai_agents.model == "gpt-5.4"
    assert settings.openai_agents.api_key_env == "AZURE_OPENAI_API_KEY"


def test_load_settings_provider_comes_from_fsq_llm_provider_env(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FSQ_LLM_PROVIDER", "azure_openai")
    monkeypatch.setenv("AZURE_OPENAI_BASE_URL", "https://edgeqa-resource.cognitiveservices.azure.com/openai/v1/")
    monkeypatch.setenv("AZURE_OPENAI_MODEL", "gpt-5.4")
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "dummy")
    config_path = tmp_path / "config.yaml"
    config_path.write_text(_base_config(tmp_path), encoding="utf-8")

    settings = load_settings(config_path)

    validate_provider_settings(settings)
    assert settings.openai_agents.provider == "azure_openai"
    assert settings.openai_agents.base_url == "https://edgeqa-resource.cognitiveservices.azure.com/openai/v1/"
    assert settings.openai_agents.model == "gpt-5.4"


def test_load_settings_provider_env_overrides_yaml_provider(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FSQ_LLM_PROVIDER", "github_copilot")
    monkeypatch.delenv("AZURE_OPENAI_API_KEY", raising=False)
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        _base_config(
            tmp_path,
            """
openai_agents:
  provider: azure_openai
""",
        ),
        encoding="utf-8",
    )

    settings = load_settings(config_path)

    validate_provider_settings(settings)
    assert settings.openai_agents.provider == "github_copilot"
    assert settings.openai_agents.model == "gpt-5.5"


def test_load_settings_rejects_invalid_fsq_llm_provider_env(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FSQ_LLM_PROVIDER", "local_llm")
    config_path = tmp_path / "config.yaml"
    config_path.write_text(_base_config(tmp_path), encoding="utf-8")

    with pytest.raises(ConfigurationError, match="FSQ_LLM_PROVIDER"):
        load_settings(config_path)


def test_validate_provider_settings_does_not_require_platform_harness_env(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FSQ_LLM_PROVIDER", "github_copilot")
    monkeypatch.delenv("FSQ_WEB_BROWSER_EXECUTABLE_PATH", raising=False)
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        _base_config(
            tmp_path,
            """
harness:
  platform: web
  web:
    backend: playwright
    channel: chrome
""",
        ),
        encoding="utf-8",
    )

    settings = load_settings(config_path)

    validate_provider_settings(settings)
    with pytest.raises(ConfigurationError, match="Web browser executable path environment variable"):
        validate_runtime_settings(settings)


def test_default_github_copilot_provider_skips_azure_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AZURE_OPENAI_API_KEY", raising=False)
    config_path = tmp_path / "config.yaml"
    config_path.write_text(_base_config(tmp_path), encoding="utf-8")

    settings = load_settings(config_path)

    validate_runtime_settings(settings)
    assert settings.openai_agents.provider == "github_copilot"
    assert settings.openai_agents.model == "gpt-5.5"


def test_load_settings_rejects_explicit_openai_model_in_yaml(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        _base_config(
            tmp_path,
            """
openai_agents:
  provider: github_copilot
  model: custom-copilot-model
""",
        ),
        encoding="utf-8",
    )

    with pytest.raises(ConfigurationError, match="Invalid configuration"):
        load_settings(config_path)


def test_load_settings_rejects_azure_endpoint_fields_in_yaml(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        _base_config(
            tmp_path,
            """
openai_agents:
  provider: azure_openai
  base_url: https://edgeqa-resource.cognitiveservices.azure.com/openai/v1/
  api_key_env: AZURE_OPENAI_API_KEY
""",
        ),
        encoding="utf-8",
    )

    with pytest.raises(ConfigurationError, match="Invalid configuration"):
        load_settings(config_path)


def test_load_settings_rejects_sensitive_tracing_in_yaml(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        _base_config(
            tmp_path,
            """
openai_agents:
  trace_include_sensitive_data: true
""",
        ),
        encoding="utf-8",
    )

    with pytest.raises(ConfigurationError, match="Invalid configuration"):
        load_settings(config_path)


def test_load_settings_rejects_invalid_provider(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        _base_config(
            tmp_path,
            """
openai_agents:
  provider: local_llm
""",
        ),
        encoding="utf-8",
    )

    with pytest.raises(ConfigurationError, match="Invalid configuration"):
        load_settings(config_path)


def test_load_settings_accepts_prompt_config(tmp_path: Path) -> None:
    agent_template = tmp_path / "agent.j2"
    task_template = tmp_path / "task.j2"
    agent_template.write_text("Agent {{ variables.voice }}", encoding="utf-8")
    task_template.write_text("Task {{ task.id }}", encoding="utf-8")
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        _base_config(
            tmp_path,
            """
openai_agents:
  prompt:
    agent_template_path: ./agent.j2
    task_template_path: ./task.j2
    variables:
      voice: concise
""",
        ),
        encoding="utf-8",
    )

    settings = load_settings(config_path)

    assert settings.openai_agents.prompt.agent_template_path == agent_template.resolve()
    assert settings.openai_agents.prompt.task_template_path == task_template.resolve()
    assert settings.openai_agents.prompt.variables == {"voice": "concise"}


def test_load_settings_rejects_obsolete_prompt_custom_instructions(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        _base_config(
            tmp_path,
            """
openai_agents:
  prompt:
    custom_instructions:
      - Prefer semantic UI assertions before visual fallback.
""",
        ),
        encoding="utf-8",
    )

    with pytest.raises(ConfigurationError, match="Obsolete custom instruction"):
        load_settings(config_path)


def test_load_settings_rejects_obsolete_prompt_custom_instructions_path(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        _base_config(
            tmp_path,
            """
openai_agents:
  prompt:
    custom_instructions_path: ./custom-instructions.md
""",
        ),
        encoding="utf-8",
    )

    with pytest.raises(ConfigurationError, match="Obsolete custom instruction"):
        load_settings(config_path)


def test_load_settings_rejects_internal_context_and_tool_output_policy(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        _base_config(
            tmp_path,
            """
openai_agents:
  context_trimming:
    recent_turns: 3
  local_tool_output:
    recent_full_output_count: 4
""",
        ),
        encoding="utf-8",
    )

    with pytest.raises(ConfigurationError, match="Invalid configuration"):
        load_settings(config_path)


def test_load_settings_uses_default_harness_without_android_env(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(_base_config(tmp_path), encoding="utf-8")

    settings = load_settings(config_path)

    assert settings.harness.platform == "android"
    assert settings.harness.android.backend == "uiautomator2"
    assert settings.harness.android.app_id is None
    assert settings.harness.android.serial is None


def test_validate_runtime_settings_requires_azure_model(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AZURE_OPENAI_BASE_URL", "https://edgeqa-resource.cognitiveservices.azure.com/openai/v1/")
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "dummy")
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        _base_config(
            tmp_path,
            """
openai_agents:
  provider: azure_openai
""",
        ),
        encoding="utf-8",
    )
    settings = load_settings(config_path)

    with pytest.raises(ConfigurationError, match="model deployment"):
        validate_runtime_settings(settings)


def test_validate_runtime_settings_requires_azure_api_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AZURE_OPENAI_BASE_URL", "https://edgeqa-resource.cognitiveservices.azure.com/openai/v1/")
    monkeypatch.setenv("AZURE_OPENAI_MODEL", "gpt-5.4")
    monkeypatch.delenv("AZURE_OPENAI_API_KEY", raising=False)
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        _base_config(
            tmp_path,
            """
openai_agents:
  provider: azure_openai
""",
        ),
        encoding="utf-8",
    )
    settings = load_settings(config_path)

    with pytest.raises(ConfigurationError, match="API key"):
        validate_runtime_settings(settings)


def test_validate_strict_core_settings_does_not_require_openai_api_key(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        _base_config(
            tmp_path,
            """
openai_agents:
  provider: azure_openai
""",
        ),
        encoding="utf-8",
    )
    settings = load_settings(config_path)

    validate_strict_core_settings(settings)


def test_load_settings_rejects_deprecated_shell_config(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        _base_config(
            tmp_path,
            """
shell:
  enabled: true
""",
        ),
        encoding="utf-8",
    )

    with pytest.raises(ConfigurationError, match="Invalid configuration"):
        load_settings(config_path)


def test_load_settings_rejects_deprecated_cli_tools_config(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        _base_config(
            tmp_path,
            """
cli_tools:
  - name: echo
    command: python
""",
        ),
        encoding="utf-8",
    )

    with pytest.raises(ConfigurationError, match="Invalid configuration"):
        load_settings(config_path)


def test_load_settings_rejects_output_runs_dir_config(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        _base_config(
            tmp_path,
            """
output:
  root_dir: output
  runs_dir: runs
""",
        ),
        encoding="utf-8",
    )

    with pytest.raises(ConfigurationError, match="Invalid configuration"):
        load_settings(config_path)


def test_load_settings_loads_dotenv_from_config_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AZURE_OPENAI_BASE_URL", raising=False)
    monkeypatch.delenv("AZURE_OPENAI_MODEL", raising=False)
    monkeypatch.delenv("AZURE_OPENAI_API_KEY", raising=False)
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        _base_config(
            tmp_path,
            """
openai_agents:
  provider: azure_openai
""",
        ),
        encoding="utf-8",
    )
    (tmp_path / ".env").write_text(
        "AZURE_OPENAI_BASE_URL=https://edgeqa-resource.cognitiveservices.azure.com/openai/v1/\n"
        "AZURE_OPENAI_MODEL=gpt-5.4\n"
        "AZURE_OPENAI_API_KEY=from-dotenv\n",
        encoding="utf-8",
    )

    settings = load_settings(config_path)

    validate_runtime_settings(settings)
    assert settings.openai_agents.base_url == "https://edgeqa-resource.cognitiveservices.azure.com/openai/v1/"
    assert settings.openai_agents.model == "gpt-5.4"


def test_load_settings_dotenv_does_not_override_existing_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "from-process")
    monkeypatch.setenv("AZURE_OPENAI_BASE_URL", "https://edgeqa-resource.cognitiveservices.azure.com/openai/v1/")
    monkeypatch.setenv("AZURE_OPENAI_MODEL", "gpt-5.4")
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        _base_config(
            tmp_path,
            """
openai_agents:
  provider: azure_openai
""",
        ),
        encoding="utf-8",
    )
    (tmp_path / ".env").write_text("AZURE_OPENAI_API_KEY=from-dotenv\n", encoding="utf-8")

    load_settings(config_path)

    assert os.environ["AZURE_OPENAI_API_KEY"] == "from-process"


def test_load_settings_rejects_invalid_dotenv_line(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(_base_config(tmp_path), encoding="utf-8")
    (tmp_path / ".env").write_text("not-a-key-value-line\n", encoding="utf-8")

    with pytest.raises(ConfigurationError, match="Invalid .env line"):
        load_settings(config_path)


def test_validate_runtime_settings_rejects_placeholder_api_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AZURE_OPENAI_BASE_URL", raising=False)
    monkeypatch.delenv("AZURE_OPENAI_MODEL", raising=False)
    monkeypatch.delenv("AZURE_OPENAI_API_KEY", raising=False)
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        _base_config(
            tmp_path,
            """
openai_agents:
  provider: azure_openai
""",
        ),
        encoding="utf-8",
    )
    (tmp_path / ".env").write_text(
        "AZURE_OPENAI_BASE_URL=https://edgeqa-resource.cognitiveservices.azure.com/openai/v1/\n"
        "AZURE_OPENAI_MODEL=gpt-5.4\n"
        "AZURE_OPENAI_API_KEY=replace-with-your-azure-openai-api-key\n",
        encoding="utf-8",
    )
    settings = load_settings(config_path)

    with pytest.raises(ConfigurationError, match="placeholder"):
        validate_runtime_settings(settings)
