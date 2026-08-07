# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from fsq_agent._strict_case_recording import StrictCaseRecording
from fsq_agent.cli._main import _task_from_goal, _task_from_raw_case_source, main
from fsq_agent.models import (
    DoctorReadiness,
    DoctorReadinessItem,
    DoctorReport,
    ReportArtifact,
    Task,
    TaskResult,
    VerificationResult,
)


FSQ_CASE = """
schemaVersion: fsq.ai-test/v1
name: Strict CLI Case
platform: android
appId: com.microsoft.emmx
---
- launchApp
"""


WEB_FSQ_CASE = """
schemaVersion: fsq.ai-test/v1
name: Strict Web CLI Case
platform: web
---
- startBrowser
- navigateTo:
    url: https://www.bing.com
- pageSnapshot
- closeBrowser
"""


MACOS_FSQ_CASE = """
schemaVersion: fsq.ai-test/v1
name: Strict macOS CLI Case
platform: macos
---
- launchApp
- clickOn:
        point:
            x: 100
            y: 200
- assertElementsOrder:
        elements:
            - target: File
            - target: Edit
- killApp
"""


WINDOWS_FSQ_CASE = """
schemaVersion: fsq.ai-test/v1
name: Strict Windows CLI Case
platform: windows
---
- launchApp
- uiSnapshot
- killApp
"""


def _doctor_ready_report() -> DoctorReport:
    return DoctorReport(
        platform="android",
        platform_source="explicit",
        requested_mode="all",
        status="ready",
        exit_code=0,
        readiness=DoctorReadiness(
            dynamic_llm=DoctorReadinessItem(status="ready"),
            strict_core=DoctorReadinessItem(status="ready"),
            ai_assertion=DoctorReadinessItem(status="ready"),
        ),
        summary={"pass": 0, "warn": 0, "fail": 0, "skip": 0},
    )


def _config(tmp_path: Path, body: str = "", platform: str = "android") -> Path:
    workspace = tmp_path / "workspace"
    cases_dir = tmp_path / "cases"
    cases_dir.mkdir(exist_ok=True)
    config_path = tmp_path / f"config.{platform}.yaml"
    config_path.write_text(
        f"""
workspace:
  root_dir: {workspace.as_posix()}
cases:
  dir: {cases_dir.as_posix()}
output:
  root_dir: output
{body}
""",
        encoding="utf-8",
    )
    return config_path


def _write_fake_core_report(output_dir: Path, run_id: str, status: str = "passed") -> ReportArtifact:
    output_dir.mkdir(parents=True, exist_ok=True)
    report_path = output_dir / "core-report.md"
    json_report_path = output_dir / "core-report.json"
    manifest_path = output_dir / "evidence-manifest.json"
    report_path.write_text("report", encoding="utf-8")
    json_report_path.write_text(
        json.dumps({"summary": {"status": status, "failed_steps": 0 if status == "passed" else 1}}),
        encoding="utf-8",
    )
    manifest_path.write_text("{}", encoding="utf-8")
    return ReportArtifact(run_id=run_id, path=report_path, evidence_manifest_path=manifest_path)


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


def test_only_public_commands_are_registered() -> None:
    assert set(main.commands) == {"init", "doctor", "run", "report", "playground"}


def test_doctor_command_delegates_options(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_run_doctor_command(**kwargs) -> int:
        captured.update(kwargs)
        return 0

    monkeypatch.setattr("fsq_agent.cli._main.run_doctor_command", fake_run_doctor_command)

    result = CliRunner().invoke(
        main,
        ["doctor", "--platform", "web", "--mode", "strict", "--non-interactive"],
    )

    assert result.exit_code == 0
    assert captured == {
        "platform": "web",
        "mode": "strict",
        "output_format": "text",
        "color": "auto",
        "non_interactive": True,
        "repair": False,
    }


def test_doctor_json_repair_error_is_valid_json() -> None:
    result = CliRunner().invoke(main, ["doctor", "--format", "json", "--repair"])

    assert result.exit_code == 2
    payload = json.loads(result.output)
    assert payload["status"] == "usage_error"
    assert payload["repairs"] == []


def test_doctor_json_ignores_forced_color_without_progress_or_ansi(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "fsq_agent.cli._doctor.DoctorService.run",
        lambda *_args, **_kwargs: _doctor_ready_report(),
    )
    result = CliRunner().invoke(main, ["doctor", "--format", "json", "--color", "always"])

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["status"] == "ready"
    assert "RUNNING" not in result.output
    assert "\x1b[" not in result.output


def test_doctor_keyboard_interrupt_exits_130(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "fsq_agent.cli._doctor.DoctorService.run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(KeyboardInterrupt()),
    )

    result = CliRunner().invoke(main, ["doctor", "--platform", "web", "--non-interactive"])

    assert result.exit_code == 130


def test_doctor_mode_prompt_keyboard_interrupt_exits_130(monkeypatch: pytest.MonkeyPatch) -> None:
    class TtyStream:
        def isatty(self) -> bool:
            return True

    monkeypatch.setattr("fsq_agent.cli._doctor.click.get_text_stream", lambda _name: TtyStream())
    monkeypatch.setattr(
        "fsq_agent.cli._doctor.click.prompt",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(KeyboardInterrupt()),
    )

    result = CliRunner().invoke(main, ["doctor", "--platform", "web"])

    assert result.exit_code == 130


def test_init_provider_copilot_writes_env_and_prepares_interactive_session(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _config(tmp_path)
    monkeypatch.delenv("FSQ_LLM_PROVIDER", raising=False)
    captured: dict[str, object] = {}

    class FakeSession:
        def close_sync(self) -> None:
            captured["closed"] = True

    def fake_prepare_model_provider_session(settings, *, interactive_auth: bool = True):
        captured["provider"] = settings.openai_agents.provider
        captured["workspace"] = settings.workspace.root_dir
        captured["interactive_auth"] = interactive_auth
        return FakeSession()

    monkeypatch.setattr(
        "fsq_agent.cli._llm_setup.prepare_model_provider_session",
        fake_prepare_model_provider_session,
    )

    result = CliRunner().invoke(main, ["init", "--platform", "android", "--provider", "github_copilot"])

    assert result.exit_code == 0, result.output
    assert (tmp_path / ".env").read_text(encoding="utf-8") == "FSQ_LLM_PROVIDER=github_copilot\n"
    assert (tmp_path / ".fsq-agent-workspace" / ".fsq-agent-workspace").exists()
    assert captured == {
        "provider": "github_copilot",
        "workspace": tmp_path / ".fsq-agent-workspace",
        "interactive_auth": True,
        "closed": True,
    }


def test_init_without_provider_does_not_update_env_or_use_interactive_auth(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _config(tmp_path)
    monkeypatch.delenv("FSQ_LLM_PROVIDER", raising=False)
    captured: dict[str, object] = {}

    def fake_prepare_model_provider_session(settings, *, interactive_auth: bool = True):
        captured["provider"] = settings.openai_agents.provider
        captured["interactive_auth"] = interactive_auth
        raise AssertionError("init without --provider must not prepare a provider session")

    monkeypatch.setattr(
        "fsq_agent.cli._llm_setup.prepare_model_provider_session",
        fake_prepare_model_provider_session,
    )

    result = CliRunner().invoke(main, ["init", "--platform", "android"])

    assert result.exit_code == 0, result.output
    assert not (tmp_path / ".env").exists()
    assert (tmp_path / ".fsq-agent-workspace" / ".fsq-agent-workspace").exists()
    assert captured == {}


def test_init_provider_azure_prompts_writes_env_and_does_not_echo_secret(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _config(tmp_path)
    for name in ("FSQ_LLM_PROVIDER", "AZURE_OPENAI_BASE_URL", "AZURE_OPENAI_MODEL", "AZURE_OPENAI_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    captured: dict[str, object] = {}

    class FakeSession:
        def close_sync(self) -> None:
            captured["closed"] = True

    def fake_prepare_model_provider_session(settings, *, interactive_auth: bool = True):
        captured["provider"] = settings.openai_agents.provider
        captured["base_url"] = settings.openai_agents.base_url
        captured["model"] = settings.openai_agents.model
        captured["interactive_auth"] = interactive_auth
        return FakeSession()

    monkeypatch.setattr(
        "fsq_agent.cli._llm_setup.prepare_model_provider_session",
        fake_prepare_model_provider_session,
    )

    result = CliRunner().invoke(
        main,
        ["init", "--platform", "android", "--provider", "azure_openai"],
        input="https://edgeqa-resource.cognitiveservices.azure.com\ngpt-5.4\nsecret-key\n",
    )

    assert result.exit_code == 0, result.output
    assert "secret-key" not in result.output
    assert (tmp_path / ".env").read_text(encoding="utf-8") == (
        "FSQ_LLM_PROVIDER=azure_openai\n"
        "AZURE_OPENAI_BASE_URL=https://edgeqa-resource.cognitiveservices.azure.com\n"
        "AZURE_OPENAI_MODEL=gpt-5.4\n"
        "AZURE_OPENAI_API_KEY=secret-key\n"
    )
    assert captured == {
        "provider": "azure_openai",
        "base_url": "https://edgeqa-resource.cognitiveservices.azure.com/openai/v1/",
        "model": "gpt-5.4",
        "interactive_auth": True,
        "closed": True,
    }


def test_init_provider_reports_env_io_errors_concisely(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _config(tmp_path)

    def fake_setup_llm_provider(*, provider: str) -> None:
        raise OSError("Unable to write .env file: .env")

    monkeypatch.setattr("fsq_agent.cli._main.setup_llm_provider", fake_setup_llm_provider)

    result = CliRunner().invoke(main, ["init", "--platform", "android", "--provider", "github_copilot"])

    assert result.exit_code != 0
    assert "Error: Unable to write .env file: .env" in result.output


def test_removed_setup_command_fails() -> None:
    result = CliRunner().invoke(main, ["setup", "llm", "--provider", "github_copilot"])

    assert result.exit_code != 0
    assert "No such command" in result.output


def test_run_rejects_missing_or_conflicting_sources(tmp_path: Path) -> None:
    _config(tmp_path)
    runner = CliRunner()

    missing = runner.invoke(main, ["run", "--platform", "android"])
    conflicting = runner.invoke(main, ["run", "--platform", "android", "--goal", "Do it", "--case-yaml", "case.codex.yaml"])
    strict_goal = runner.invoke(main, ["run", "--platform", "android", "--strict", "--goal", "Do it"])
    record_on_failure_without_record = runner.invoke(main, ["run", "--platform", "android", "--goal", "Do it", "--record-on-failure"])
    strict_record = runner.invoke(main, ["run", "--platform", "android", "--strict", "--case-yaml", "case.codex.yaml", "--record"])

    assert missing.exit_code != 0
    assert "Exactly one" in missing.output
    assert conflicting.exit_code != 0
    assert strict_goal.exit_code != 0
    assert record_on_failure_without_record.exit_code != 0
    assert strict_record.exit_code != 0


def test_run_rejects_removed_config_option() -> None:
    result = CliRunner().invoke(main, ["run", "--config", "config.yaml", "--goal", "Do it"])

    assert result.exit_code != 0
    assert "No such option: --config" in result.output


def test_run_rejects_removed_workspace_option() -> None:
    result = CliRunner().invoke(main, ["run", "--workspace", "workspace", "--platform", "android", "--goal", "Do it"])

    assert result.exit_code != 0
    assert "No such option: --workspace" in result.output


def test_run_help_stream_format_defaults_to_concise_without_rich_alias() -> None:
    result = CliRunner().invoke(main, ["run", "--help"])

    assert result.exit_code == 0, result.output
    assert "[default: concise]" in result.output
    assert "[concise|jsonl]" in result.output
    assert "rich" not in result.output


def test_run_case_yaml_uses_raw_file_content_without_fsq_parsing(tmp_path: Path, monkeypatch) -> None:
    _config(tmp_path)
    case_path = tmp_path / "cases" / "raw.codex.yaml"
    raw_content = "not: [valid yaml"
    case_path.write_text(raw_content, encoding="utf-8")
    captured: dict[str, object] = {}

    class FakeAgent:
        async def run(self, task: Task, event_sink=None) -> TaskResult:
            captured["task"] = task
            return TaskResult(
                task_id=task.id,
                status="success",
                steps=[],
                verification=VerificationResult(status="success", summary="ok"),
                report=ReportArtifact(run_id="raw-run", path=tmp_path / "report.md"),
            )

    class RaisingLoader:
        def __init__(self) -> None:
            raise AssertionError("dynamic case-yaml must not construct FsqCaseLoader")

    def fake_agent_from_settings(settings):
        captured["tracing_enabled"] = settings.openai_agents.tracing_enabled
        return FakeAgent()

    monkeypatch.setattr("fsq_agent.cli._main.FsqAgent.from_settings", fake_agent_from_settings)
    monkeypatch.setattr("fsq_agent.cli._main.FsqCaseLoader", RaisingLoader)

    result = CliRunner().invoke(main, ["run", "--platform", "android", "--case-yaml", "raw.codex.yaml", "--no-stream", "--no-tracing"])

    assert result.exit_code == 0, result.output
    assert captured["tracing_enabled"] is False
    task = captured["task"]
    assert isinstance(task, Task)
    assert task.name == "Case reference: raw.codex.yaml"
    assert raw_content in task.description
    assert "The CLI has not parsed" in task.description
    assert task.key_actions == []


def test_run_goal_record_invokes_strict_case_recorder(tmp_path: Path, monkeypatch) -> None:
    _config(tmp_path)
    captured: dict[str, object] = {}

    class FakeAgent:
        async def run(self, task: Task, event_sink=None) -> TaskResult:
            return TaskResult(
                task_id=task.id,
                status="success",
                steps=[],
                verification=VerificationResult(status="success", summary="ok"),
                report=ReportArtifact(run_id="recorded-run", path=tmp_path / "report.md"),
            )

    def fake_record_dynamic_run_as_strict_case(**kwargs):
        captured.update(kwargs)
        recording_path = kwargs["run_dir"] / "recording.json"
        recorded_path = kwargs["run_dir"] / "recorded.codex.yaml"
        return StrictCaseRecording(status="recorded", recording_path=recording_path, recorded_case_path=recorded_path)

    monkeypatch.setattr("fsq_agent.cli._main.FsqAgent.from_settings", lambda _settings: FakeAgent())
    monkeypatch.setattr("fsq_agent.cli._main.record_dynamic_run_as_strict_case", fake_record_dynamic_run_as_strict_case)

    result = CliRunner().invoke(main, ["run", "--platform", "android", "--goal", "Do it", "--record", "--no-stream"])

    assert result.exit_code == 0, result.output
    assert captured["run_dir"] == tmp_path / ".fsq-agent-workspace" / "output" / "runs" / "recorded-run"
    assert captured["allow_failure"] is False
    assert "Recorded strict case" in result.output


def test_run_strict_case_builds_android_harness_from_env_and_reports_paths(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("FSQ_ANDROID_APP_ID", "com.example.config")
    monkeypatch.setenv("FSQ_ANDROID_SERIAL", "device-1")
    _config(
        tmp_path,
                """
harness:
    platform: android
    android:
        backend: uiautomator2
execution:
    post_action_delay_seconds:
        platform: 0.25
        common: 0
""",
    )
    case_path = tmp_path / "cases" / "strict_cli.codex.yaml"
    case_path.write_text(FSQ_CASE, encoding="utf-8")
    calls = {}

    class FakeDriver:
        def __init__(self, *, app_id: str, serial: str | None) -> None:
            self.app_id = app_id
            self.serial = serial
            calls["driver"] = {"app_id": app_id, "serial": serial}

    def fake_run_strict_fsq_core_case(**kwargs):
        calls["strict"] = kwargs
        return _write_fake_core_report(kwargs["output_dir"], kwargs["run_id"])

    monkeypatch.setattr("fsq_agent.core.harness._factory.UiAutomator2AndroidDriver", FakeDriver)
    monkeypatch.setattr("fsq_agent.cli._main.run_strict_fsq_core_case", fake_run_strict_fsq_core_case)

    result = CliRunner().invoke(main, ["run", "--platform", "android", "--strict", "--case-yaml", "strict_cli.codex.yaml"])

    assert result.exit_code == 0, result.output
    assert calls["driver"] == {"app_id": "com.example.config", "serial": "device-1"}
    assert calls["strict"]["case_path"] == case_path.resolve()
    assert calls["strict"]["run_id"] == "strict_cli"
    assert calls["strict"]["output_dir"] == tmp_path / ".fsq-agent-workspace" / "output" / "runs" / "strict_cli"
    assert calls["strict"]["post_action_delay_seconds"].platform == 0.25
    assert calls["strict"]["post_action_delay_seconds"].common == 0
    assert "core-report.md" in result.output
    assert "evidence-manifest.json" in result.output


def test_run_strict_case_with_lifecycle_hooks_uses_lifecycle_helper(tmp_path: Path, monkeypatch) -> None:
    _config(
        tmp_path,
        """
harness:
  platform: android
  android:
    backend: uiautomator2
""",
    )
    hook_path = tmp_path / "cases" / "hooks" / "setup.codex.yaml"
    hook_path.parent.mkdir(parents=True)
    hook_path.write_text(
        """
schemaVersion: fsq.ai-test/v1
name: Setup Hook
platform: android
---
- tapOn:
    target: Setup
""",
        encoding="utf-8",
    )
    case_path = tmp_path / "cases" / "strict_hooked.codex.yaml"
    case_path.write_text(
        """
schemaVersion: fsq.ai-test/v1
name: Strict Hooked Case
platform: android
appId: com.example.root
onCaseStart:
  runCase: hooks/setup.codex.yaml
---
- launchApp
""",
        encoding="utf-8",
    )
    calls = {}

    class FakeDriver:
        def __init__(self, *, app_id: str, serial: str | None) -> None:
            calls["driver"] = {"app_id": app_id, "serial": serial}

    def fake_run_strict_fsq_core_case(**_kwargs):
        raise AssertionError("plain strict core helper should not run lifecycle cases")

    def fake_run_strict_fsq_lifecycle_case(**kwargs):
        calls["lifecycle"] = kwargs
        return _write_fake_core_report(kwargs["output_dir"], kwargs["run_id"])

    monkeypatch.setattr("fsq_agent.core.harness._factory.UiAutomator2AndroidDriver", FakeDriver)
    monkeypatch.setattr("fsq_agent.cli._main.run_strict_fsq_core_case", fake_run_strict_fsq_core_case)
    monkeypatch.setattr("fsq_agent.cli._main.run_strict_fsq_lifecycle_case", fake_run_strict_fsq_lifecycle_case)

    result = CliRunner().invoke(main, ["run", "--platform", "android", "--strict", "--case-yaml", "strict_hooked.codex.yaml"])

    assert result.exit_code == 0, result.output
    assert calls["driver"] == {"app_id": "com.example.root", "serial": None}
    assert calls["lifecycle"]["case_path"] == case_path.resolve()
    assert calls["lifecycle"]["case"].config.on_case_start
    assert calls["lifecycle"]["run_id"] == "strict_hooked"
    assert "core-report.md" in result.output


def test_run_strict_single_case_exits_nonzero_when_report_fails(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("FSQ_ANDROID_APP_ID", "com.example.config")
    _config(
        tmp_path,
        """
harness:
  platform: android
  android:
    backend: uiautomator2
""",
    )
    case_path = tmp_path / "cases" / "strict_fail.codex.yaml"
    case_path.write_text(FSQ_CASE, encoding="utf-8")

    class FakeDriver:
        def __init__(self, *, app_id: str, serial: str | None) -> None:
            self.app_id = app_id
            self.serial = serial

    def fake_run_strict_fsq_core_case(**kwargs):
        kwargs["output_dir"].mkdir(parents=True, exist_ok=True)
        report_path = kwargs["output_dir"] / "core-report.md"
        json_report_path = kwargs["output_dir"] / "core-report.json"
        manifest_path = kwargs["output_dir"] / "evidence-manifest.json"
        report_path.write_text("report", encoding="utf-8")
        json_report_path.write_text(json.dumps({"summary": {"status": "failed", "failed_steps": 1}}), encoding="utf-8")
        manifest_path.write_text("{}", encoding="utf-8")
        return ReportArtifact(run_id=kwargs["run_id"], path=report_path, evidence_manifest_path=manifest_path)

    monkeypatch.setattr("fsq_agent.core.harness._factory.UiAutomator2AndroidDriver", FakeDriver)
    monkeypatch.setattr("fsq_agent.cli._main.run_strict_fsq_core_case", fake_run_strict_fsq_core_case)

    result = CliRunner().invoke(main, ["run", "--platform", "android", "--strict", "--case-yaml", str(case_path)])

    assert result.exit_code == 1, result.output
    assert "core-report.md" in result.output


def test_run_strict_case_falls_back_to_case_app_id(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("FSQ_ANDROID_SERIAL", "device-1")
    _config(
        tmp_path,
        """
harness:
  platform: android
  android:
    backend: uiautomator2
""",
    )
    case_path = tmp_path / "cases" / "strict_cli.codex.yaml"
    case_path.write_text(FSQ_CASE, encoding="utf-8")
    calls = {}

    class FakeDriver:
        def __init__(self, *, app_id: str, serial: str | None) -> None:
            calls["driver"] = {"app_id": app_id, "serial": serial}

    def fake_run_strict_fsq_core_case(**kwargs):
        return _write_fake_core_report(kwargs["output_dir"], kwargs["run_id"])

    monkeypatch.setattr("fsq_agent.core.harness._factory.UiAutomator2AndroidDriver", FakeDriver)
    monkeypatch.setattr("fsq_agent.cli._main.run_strict_fsq_core_case", fake_run_strict_fsq_core_case)

    result = CliRunner().invoke(main, ["run", "--platform", "android", "--strict", "--case-yaml", str(case_path)])

    assert result.exit_code == 0, result.output
    assert calls["driver"] == {"app_id": "com.microsoft.emmx", "serial": "device-1"}


def test_run_strict_case_requires_config_or_case_app_id_before_driver_construction(tmp_path: Path, monkeypatch) -> None:
    _config(tmp_path)
    case_path = tmp_path / "cases" / "missing_app.codex.yaml"
    case_path.write_text(FSQ_CASE.replace("appId: com.microsoft.emmx\n", ""), encoding="utf-8")

    def fail_driver(**_kwargs):
        raise AssertionError("driver should not be constructed")

    monkeypatch.setattr("fsq_agent.core.harness._factory.UiAutomator2AndroidDriver", fail_driver)

    result = CliRunner().invoke(main, ["run", "--platform", "android", "--strict", "--case-yaml", str(case_path)])

    assert result.exit_code != 0


def test_run_strict_web_case_builds_web_harness_without_android_app_id(tmp_path: Path, monkeypatch) -> None:
    chrome_path = tmp_path / "chrome.exe"
    chrome_path.write_text("", encoding="utf-8")
    chrome_path.chmod(0o755)
    monkeypatch.setenv("FSQ_WEB_BROWSER_EXECUTABLE_PATH", str(chrome_path))
    _config(
        tmp_path,
        """
harness:
  platform: web
  web:
    backend: playwright
    channel: chrome
    headless: true
    base_url: https://www.bing.com
    viewport_width: 1280
    viewport_height: 720
""",
    platform="web",
    )
    case_path = tmp_path / "cases" / "strict_web.codex.yaml"
    case_path.write_text(WEB_FSQ_CASE, encoding="utf-8")
    calls = {}

    class FakeWebDriver:
        def __init__(self, **kwargs) -> None:
            calls["driver"] = kwargs

    def fake_run_strict_fsq_core_case(**kwargs):
        calls["strict"] = kwargs
        return _write_fake_core_report(kwargs["output_dir"], kwargs["run_id"])

    monkeypatch.setattr("fsq_agent.core.harness._factory.PlaywrightWebDriver", FakeWebDriver)
    monkeypatch.setattr("fsq_agent.cli._main.run_strict_fsq_core_case", fake_run_strict_fsq_core_case)

    result = CliRunner().invoke(main, ["run", "--platform", "web", "--strict", "--case-yaml", "strict_web.codex.yaml"])

    assert result.exit_code == 0, result.output
    assert calls["driver"] == {
        "channel": "chrome",
        "executable_path": chrome_path,
        "headless": True,
        "base_url": "https://www.bing.com",
        "viewport": (1280, 720),
    }
    assert calls["strict"]["case_path"] == case_path.resolve()
    assert calls["strict"]["run_id"] == "strict_web"
    assert [step.action_name for step in calls["strict"]["steps"]] == ["start_browser", "navigate_to", "page_snapshot", "close_browser"]
    assert calls["strict"]["registry"].resolve("pageSnapshot") is not None
    assert calls["strict"]["registry"].resolve("startBrowser") is not None
    assert calls["strict"]["registry"].resolve("tapOn") is None


def test_run_strict_macos_case_builds_macos_harness_from_env_without_android_app_id(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("FSQ_MACOS_APPIUM_SERVER_URL", "http://127.0.0.1:4723")
    monkeypatch.setenv("FSQ_MACOS_BUNDLE_ID", "com.example.MacApp")
    _config(
        tmp_path,
        """
harness:
  platform: macos
  macos:
    backend: appium_mac2
    page_source_max_depth: 7
    action_timeout_seconds: 11
""",
    platform="macos",
    )
    case_path = tmp_path / "cases" / "strict_macos.codex.yaml"
    case_path.write_text(MACOS_FSQ_CASE, encoding="utf-8")
    calls = {}

    class FakeMacOSDriver:
        def __init__(self, **kwargs) -> None:
            calls["driver"] = kwargs

    def fake_run_strict_fsq_core_case(**kwargs):
        calls["strict"] = kwargs
        return _write_fake_core_report(kwargs["output_dir"], kwargs["run_id"])

    monkeypatch.setattr("fsq_agent.core.harness._factory.AppiumMac2Driver", FakeMacOSDriver)
    monkeypatch.setattr("fsq_agent.cli._main.run_strict_fsq_core_case", fake_run_strict_fsq_core_case)

    result = CliRunner().invoke(main, ["run", "--platform", "macos", "--strict", "--case-yaml", "strict_macos.codex.yaml"])

    assert result.exit_code == 0, result.output
    assert calls["driver"] == {
        "server_url": "http://127.0.0.1:4723",
        "bundle_id": "com.example.MacApp",
        "app_path": None,
        "page_source_max_depth": 7,
        "action_timeout_seconds": 11,
    }
    assert calls["strict"]["case_path"] == case_path.resolve()
    assert calls["strict"]["run_id"] == "strict_macos"
    assert [step.action_name for step in calls["strict"]["steps"]] == [
        "launch_app",
        "click_on",
        "assert_elements_order",
        "kill_app",
    ]
    assert calls["strict"]["registry"].resolve("assertElementsOrder") is not None
    assert calls["strict"]["registry"].resolve("tapOn") is None


def test_run_strict_windows_case_builds_windows_harness_from_env_without_android_app_id(
    tmp_path: Path, monkeypatch
) -> None:
    app_path = tmp_path / "windows-app.exe"
    app_path.write_text("", encoding="utf-8")
    monkeypatch.setenv("FSQ_WINDOWS_APP_PATH", str(app_path))
    monkeypatch.setenv("FSQ_WINDOWS_BACKEND_KIND", "win32")
    monkeypatch.setenv("FSQ_WINDOWS_WINDOW_TITLE_RE", ".*Legacy App")
    monkeypatch.setenv("FSQ_WINDOWS_LAUNCH_ARGS", '--flag "two words"')
    _config(
        tmp_path,
        """
harness:
  platform: windows
  windows:
    backend: pywinauto
""",
    platform="windows",
    )
    case_path = tmp_path / "cases" / "strict_windows.codex.yaml"
    case_path.write_text(WINDOWS_FSQ_CASE, encoding="utf-8")
    calls = {}

    class FakeWindowsDriver:
        def __init__(self, **kwargs) -> None:
            calls["driver"] = kwargs

    def fake_run_strict_fsq_core_case(**kwargs):
        calls["strict"] = kwargs
        return _write_fake_core_report(kwargs["output_dir"], kwargs["run_id"])

    monkeypatch.setattr("fsq_agent.core.harness._factory.PywinautoWindowsDriver", FakeWindowsDriver)
    monkeypatch.setattr("fsq_agent.cli._main.run_strict_fsq_core_case", fake_run_strict_fsq_core_case)

    result = CliRunner().invoke(main, ["run", "--platform", "windows", "--strict", "--case-yaml", "strict_windows.codex.yaml"])

    assert result.exit_code == 0, result.output
    assert calls["driver"] == {
        "app_path": app_path,
        "backend_kind": "win32",
        "window_title_re": ".*Legacy App",
        "launch_args": ["--flag", "two words"],
    }
    assert calls["strict"]["case_path"] == case_path.resolve()
    assert calls["strict"]["run_id"] == "strict_windows"
    assert [step.action_name for step in calls["strict"]["steps"]] == ["launch_app", "ui_snapshot", "kill_app"]
    assert calls["strict"]["registry"].resolve("uiSnapshot") is not None
    assert calls["strict"]["registry"].resolve("pageSnapshot") is None


def test_run_strict_rejects_case_platform_mismatch_before_driver_construction(tmp_path: Path, monkeypatch) -> None:
    _config(
        tmp_path,
        """
harness:
  platform: web
""",
    platform="web",
    )
    case_path = tmp_path / "cases" / "android_case.codex.yaml"
    case_path.write_text(FSQ_CASE, encoding="utf-8")
    constructed = False

    def fail_driver(**_kwargs):
        nonlocal constructed
        constructed = True
        raise AssertionError("driver should not be constructed")

    monkeypatch.setattr("fsq_agent.core.harness._factory.PlaywrightWebDriver", fail_driver)

    result = CliRunner().invoke(main, ["run", "--platform", "web", "--strict", "--case-yaml", "android_case.codex.yaml"])

    assert result.exit_code != 0
    assert constructed is False


def test_run_strict_case_dir_continues_and_writes_summary(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("FSQ_ANDROID_APP_ID", "com.example.config")
    monkeypatch.setenv("FSQ_ANDROID_SERIAL", "device-1")
    _config(
        tmp_path,
        """
harness:
  platform: android
  android:
    backend: uiautomator2
""",
    )
    cases_dir = tmp_path / "cases"
    (cases_dir / "first.codex.yaml").write_text(FSQ_CASE.replace("Strict CLI Case", "First Case"), encoding="utf-8")
    (cases_dir / "second.codex.yaml").write_text(FSQ_CASE.replace("Strict CLI Case", "Second Case"), encoding="utf-8")
    calls = []

    class FakeDriver:
        def __init__(self, *, app_id: str, serial: str | None) -> None:
            self.app_id = app_id
            self.serial = serial

    def fake_run_strict_fsq_core_case(**kwargs):
        calls.append(kwargs)
        kwargs["output_dir"].mkdir(parents=True, exist_ok=True)
        report_path = kwargs["output_dir"] / "core-report.md"
        json_report_path = kwargs["output_dir"] / "core-report.json"
        manifest_path = kwargs["output_dir"] / "evidence-manifest.json"
        case_status = "failed" if kwargs["case_path"].name == "second.codex.yaml" else "passed"
        report_path.write_text("report", encoding="utf-8")
        json_report_path.write_text(
            json.dumps({"summary": {"status": case_status, "failed_steps": 1 if case_status == "failed" else 0}}),
            encoding="utf-8",
        )
        manifest_path.write_text("{}", encoding="utf-8")
        return ReportArtifact(run_id=kwargs["run_id"], path=report_path, evidence_manifest_path=manifest_path)

    monkeypatch.setattr("fsq_agent.core.harness._factory.UiAutomator2AndroidDriver", FakeDriver)
    monkeypatch.setattr("fsq_agent.cli._main.run_strict_fsq_core_case", fake_run_strict_fsq_core_case)

    result = CliRunner().invoke(main, ["run", "--platform", "android", "--strict", "--case-dir", str(cases_dir)])

    assert result.exit_code == 1, result.output
    assert [call["case_path"].name for call in calls] == ["first.codex.yaml", "second.codex.yaml"]
    summary_paths = list((tmp_path / ".fsq-agent-workspace" / "output" / "runs").glob("strict-core-batch-*/strict-core-batch-summary.json"))
    assert len(summary_paths) == 1
    summary_path = summary_paths[0]
    markdown_path = summary_path.with_suffix(".md")
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    assert summary["total"] == 2
    assert summary["passed"] == 1
    assert summary["failed"] == 1
    assert [case["status"] for case in summary["cases"]] == ["passed", "failed"]
    assert "failed_steps=1" in summary["cases"][1]["error"]
    assert "first.codex.yaml" in markdown_path.read_text(encoding="utf-8")


def test_run_strict_case_dir_excludes_hook_dependencies_from_top_level_summary(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("FSQ_ANDROID_APP_ID", "com.example.config")
    _config(
        tmp_path,
        """
harness:
  platform: android
  android:
    backend: uiautomator2
""",
    )
    cases_dir = tmp_path / "cases"
    (cases_dir / "root.codex.yaml").write_text(
        """
schemaVersion: fsq.ai-test/v1
name: Root Case
platform: android
onCaseStart:
  runCase: setup.codex.yaml
---
- launchApp
""",
        encoding="utf-8",
    )
    (cases_dir / "setup.codex.yaml").write_text(
        """
schemaVersion: fsq.ai-test/v1
name: Setup Case
platform: android
---
- tapOn:
    target: Setup
""",
        encoding="utf-8",
    )
    calls = []

    class FakeDriver:
        def __init__(self, *, app_id: str, serial: str | None) -> None:
            self.app_id = app_id
            self.serial = serial

    def fake_run_strict_fsq_lifecycle_case(**kwargs):
        calls.append(kwargs)
        kwargs["output_dir"].mkdir(parents=True, exist_ok=True)
        report_path = kwargs["output_dir"] / "core-report.md"
        json_report_path = kwargs["output_dir"] / "core-report.json"
        manifest_path = kwargs["output_dir"] / "evidence-manifest.json"
        report_path.write_text("report", encoding="utf-8")
        json_report_path.write_text(json.dumps({"summary": {"status": "passed", "failed_steps": 0}}), encoding="utf-8")
        manifest_path.write_text("{}", encoding="utf-8")
        return ReportArtifact(run_id=kwargs["run_id"], path=report_path, evidence_manifest_path=manifest_path)

    monkeypatch.setattr("fsq_agent.core.harness._factory.UiAutomator2AndroidDriver", FakeDriver)
    monkeypatch.setattr("fsq_agent.cli._main.run_strict_fsq_lifecycle_case", fake_run_strict_fsq_lifecycle_case)

    result = CliRunner().invoke(main, ["run", "--platform", "android", "--strict", "--case-dir", str(cases_dir)])

    assert result.exit_code == 0, result.output
    assert [call["case_path"].name for call in calls] == ["root.codex.yaml"]
    summary_paths = list((tmp_path / ".fsq-agent-workspace" / "output" / "runs").glob("strict-core-batch-*/strict-core-batch-summary.json"))
    assert len(summary_paths) == 1
    summary = json.loads(summary_paths[0].read_text(encoding="utf-8"))
    assert summary["total"] == 1
    assert summary["cases"][0]["case_path"].endswith("root.codex.yaml")


def test_run_strict_case_dir_excludes_config_hook_dependencies_from_top_level_summary(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("FSQ_ANDROID_APP_ID", "com.example.config")
    _config(
        tmp_path,
        """
harness:
  platform: android
  android:
    backend: uiautomator2
caseLifecycle:
  onCaseStart:
    runCase: setup.codex.yaml
""",
    )
    cases_dir = tmp_path / "cases"
    (cases_dir / "root.codex.yaml").write_text(FSQ_CASE.replace("Strict CLI Case", "Root Case"), encoding="utf-8")
    (cases_dir / "setup.codex.yaml").write_text(
        """
schemaVersion: fsq.ai-test/v1
name: Setup Case
platform: android
---
- tapOn:
    target: Setup
""",
        encoding="utf-8",
    )
    calls = []

    class FakeDriver:
        def __init__(self, *, app_id: str, serial: str | None) -> None:
            self.app_id = app_id
            self.serial = serial

    def fake_run_strict_fsq_lifecycle_case(**kwargs):
        calls.append(kwargs)
        return _write_fake_core_report(kwargs["output_dir"], kwargs["run_id"])

    monkeypatch.setattr("fsq_agent.core.harness._factory.UiAutomator2AndroidDriver", FakeDriver)
    monkeypatch.setattr("fsq_agent.cli._main.run_strict_fsq_lifecycle_case", fake_run_strict_fsq_lifecycle_case)

    result = CliRunner().invoke(main, ["run", "--platform", "android", "--strict", "--case-dir", str(cases_dir)])

    assert result.exit_code == 0, result.output
    assert [call["case_path"].name for call in calls] == ["root.codex.yaml"]


def test_report_command_resolves_llm_and_strict_reports(tmp_path: Path) -> None:
    _config(tmp_path)
    workspace = tmp_path / ".fsq-agent-workspace"
    workspace.mkdir(parents=True)
    (workspace / ".fsq-agent-workspace").write_text("fsq-agent workspace\n", encoding="utf-8")
    runs_dir = tmp_path / ".fsq-agent-workspace" / "output" / "runs"
    llm_dir = runs_dir / "llm-run"
    strict_dir = runs_dir / "strict-run"
    llm_dir.mkdir(parents=True)
    strict_dir.mkdir(parents=True)
    (llm_dir / "report.md").write_text("llm report", encoding="utf-8")
    (strict_dir / "core-report.md").write_text("strict report", encoding="utf-8")
    runner = CliRunner()

    llm_result = runner.invoke(main, ["report", "--platform", "android", "--run-id", "llm-run"])
    strict_result = runner.invoke(main, ["report", "--platform", "android", "--run-id", "strict-run"])

    assert llm_result.exit_code == 0, llm_result.output
    assert "llm report" in llm_result.output
    assert strict_result.exit_code == 0, strict_result.output
    assert "strict report" in strict_result.output


def test_task_from_goal_creates_goal_only_task() -> None:
    task = _task_from_goal("  Access Downloads through the overflow menu.  ")

    assert task.id == "access-downloads-through-the-overflow-menu"
    assert task.name == "Access Downloads through the overflow menu."
    assert task.planning_reference_kind == "goal"
    assert task.planning_reference_text == "Access Downloads through the overflow menu."
    assert task.key_actions == []
    assert task.verification_goal is None


def test_task_from_raw_case_source_preserves_full_content_as_planning_reference(tmp_path: Path) -> None:
    case_path = tmp_path / "verify_settings.codex.yaml"
    content = """schemaVersion: fsq.ai-test/v1
name: Verify Settings
---
- launchApp
- tapOn: Microsoft services
"""

    task = _task_from_raw_case_source(case_path, content)

    assert task.planning_reference_kind == "raw_case"
    assert task.planning_reference_text is not None
    assert f"Source path: {case_path}" in task.planning_reference_text
    assert content in task.planning_reference_text
    assert "Microsoft services" in task.planning_reference_text
    assert task.verification_goal is None