# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from __future__ import annotations

from pathlib import Path

from fsq_agent.doctor import DoctorService, render_doctor_json, render_doctor_text
from fsq_agent.models import DiagnosticProbeResult, DoctorRequest


class _FakeProbe:
    def probe(self, timeout_seconds: float = 5.0):
        return [
            DiagnosticProbeResult(
                id="web.ready",
                category="Web",
                status="pass",
                summary="Web is ready.",
                affected_targets=["dynamic", "strict", "ai_assertion"],
            )
        ]


class _FakePlatformFactory:
    def create(self, platform, harness_settings):
        return _FakeProbe()


class _FakeProvider:
    def probe(self, settings, timeout_seconds: float = 5.0):
        return [
            DiagnosticProbeResult(
                id="provider.ready",
                category="Provider",
                status="pass",
                summary="Provider is ready.",
                affected_targets=["dynamic", "ai_assertion"],
            )
        ]

    def refresh_cached_copilot_provider_token(self, settings):
        raise AssertionError("refresh was not requested")


def _write_web_setup(root: Path) -> None:
    (root / "config.web.yaml").write_text(
        """
harness:
  platform: web
  web:
    backend: playwright
    channel: chrome
output:
  root_dir: output
cases:
  dir: cases
""",
        encoding="utf-8",
    )
    workspace = root / ".fsq-agent-workspace"
    workspace.mkdir()
    (workspace / ".fsq-agent-workspace").write_text("fsq-agent workspace\n", encoding="utf-8")


def test_doctor_reports_ready_for_injected_platform_and_provider(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    _write_web_setup(tmp_path)
    chrome = tmp_path / "chrome.exe"
    chrome.write_text("", encoding="utf-8")
    service = DoctorService(
        provider_diagnostics=_FakeProvider(),
        platform_probe_factory=_FakePlatformFactory(),
        environ={"FSQ_WEB_BROWSER_EXECUTABLE_PATH": str(chrome)},
    )

    report = service.run(DoctorRequest(platform="web", mode="all", working_directory=tmp_path))

    assert report.exit_code == 0
    assert report.readiness.dynamic_llm.status == "ready"
    assert report.readiness.strict_core.status == "ready"
    assert report.readiness.ai_assertion.status == "ready"
    assert "Web is ready" in render_doctor_text(report)
    assert '"schema_version": 1' in render_doctor_json(report)


def test_doctor_noninteractive_ambiguous_platform_returns_usage_error(tmp_path: Path) -> None:
    service = DoctorService(
        provider_diagnostics=_FakeProvider(),
        platform_probe_factory=_FakePlatformFactory(),
        environ={"FSQ_ANDROID_APP_ID": "com.example", "FSQ_WEB_BROWSER_EXECUTABLE_PATH": "chrome.exe"},
    )

    report = service.run(DoctorRequest(working_directory=tmp_path, interactive=False))

    assert report.exit_code == 2
    assert report.platform is None
    assert report.checks[0].id == "doctor.platform_selection"
    output = render_doctor_json(report)
    assert "com.example" not in output


def test_doctor_repair_initializes_missing_workspace(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config.web.yaml").write_text("harness:\n  platform: web\n", encoding="utf-8")
    chrome = tmp_path / "chrome.exe"
    chrome.write_text("", encoding="utf-8")
    calls = {"platform": 0}

    class CountingProbe(_FakeProbe):
        def probe(self, timeout_seconds: float = 5.0):
            calls["platform"] += 1
            return super().probe(timeout_seconds)

    class CountingFactory:
        def create(self, platform, harness_settings):
            return CountingProbe()

    service = DoctorService(
        provider_diagnostics=_FakeProvider(),
        platform_probe_factory=CountingFactory(),
        environ={"FSQ_WEB_BROWSER_EXECUTABLE_PATH": str(chrome)},
    )

    report = service.run(
        DoctorRequest(platform="web", mode="strict", repair=True, working_directory=tmp_path)
    )

    assert (tmp_path / ".fsq-agent-workspace" / ".fsq-agent-workspace").is_file()
    assert any(repair.action_id == "workspace.initialize" and repair.status == "applied" for repair in report.repairs)
    repair = next(repair for repair in report.repairs if repair.action_id == "workspace.initialize")
    assert repair.rerun_check_ids == ["workspace.initialized"]
    assert calls["platform"] == 1
    assert report.readiness.strict_core.status == "ready"


def test_strict_mode_does_not_call_provider(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    _write_web_setup(tmp_path)
    chrome = tmp_path / "chrome.exe"
    chrome.write_text("", encoding="utf-8")

    class FailingProvider(_FakeProvider):
        def probe(self, settings, timeout_seconds: float = 5.0):
            raise AssertionError("strict mode must not probe provider")

    report = DoctorService(
        provider_diagnostics=FailingProvider(),
        platform_probe_factory=_FakePlatformFactory(),
        environ={"FSQ_WEB_BROWSER_EXECUTABLE_PATH": str(chrome)},
    ).run(DoctorRequest(platform="web", mode="strict", working_directory=tmp_path))

    assert report.readiness.strict_core.status == "ready"
    assert report.readiness.dynamic_llm.status == "not_checked"
    assert report.readiness.ai_assertion.status == "not_checked"


def test_report_boundary_redacts_credentials(tmp_path: Path) -> None:
    class SecretProbe:
        def probe(self, timeout_seconds: float = 5.0):
            return [
                DiagnosticProbeResult(
                    id="web.secret",
                    category="Web",
                    status="fail",
                    summary="authorization=Bearer-secret https://user:pass@example.test/path?token=secret",
                    affected_targets=["strict"],
                    fixes=[],
                    metadata={"api_key": "secret-value", "cookie": "session-secret"},
                )
            ]

    class SecretFactory:
        def create(self, platform, harness_settings):
            return SecretProbe()

    (tmp_path / "config.web.yaml").write_text("harness:\n  platform: web\n", encoding="utf-8")
    workspace = tmp_path / ".fsq-agent-workspace"
    workspace.mkdir()
    (workspace / ".fsq-agent-workspace").write_text("ok", encoding="utf-8")
    report = DoctorService(
        provider_diagnostics=_FakeProvider(),
        platform_probe_factory=SecretFactory(),
        environ={"FSQ_WEB_BROWSER_EXECUTABLE_PATH": "chrome.exe"},
    ).run(DoctorRequest(platform="web", mode="strict", working_directory=tmp_path))

    output = render_doctor_json(report)
    assert "Bearer-secret" not in output
    assert "user:pass" not in output
    assert "secret-value" not in output
    assert "session-secret" not in output


def test_noninteractive_repair_records_input_required_environment_fix_as_skipped(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config.web.yaml").write_text("harness:\n  platform: web\n", encoding="utf-8")
    workspace = tmp_path / ".fsq-agent-workspace"
    workspace.mkdir()
    (workspace / ".fsq-agent-workspace").write_text("ok", encoding="utf-8")

    class MissingPathProbe:
        def probe(self, timeout_seconds: float = 5.0):
            from fsq_agent.models import DoctorFix

            return [
                DiagnosticProbeResult(
                    id="web.browser.executable",
                    category="Web",
                    status="fail",
                    summary="Browser path is missing.",
                    affected_targets=["strict"],
                    fixes=[
                        DoctorFix(
                            description="Set the Chrome path.",
                            environment_variable="FSQ_WEB_BROWSER_EXECUTABLE_PATH",
                        )
                    ],
                )
            ]

    class MissingPathFactory:
        def create(self, platform, harness_settings):
            return MissingPathProbe()

    report = DoctorService(
        provider_diagnostics=_FakeProvider(),
        platform_probe_factory=MissingPathFactory(),
        environ={},
    ).run(DoctorRequest(platform="web", mode="strict", repair=True, working_directory=tmp_path))

    assert any(
        repair.action_id == "environment.update" and repair.status == "skipped"
        for repair in report.repairs
    )


def test_runtime_secret_present_only_in_dotenv_is_not_warned(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config.web.yaml").write_text(
        "harness:\n  platform: web\nruntime_secrets:\n  allowed_env_names:\n    - TEST_PASSWORD\n",
        encoding="utf-8",
    )
    (tmp_path / ".env").write_text("TEST_PASSWORD=secret\nFSQ_WEB_BROWSER_EXECUTABLE_PATH=chrome.exe\n", encoding="utf-8")
    workspace = tmp_path / ".fsq-agent-workspace"
    workspace.mkdir()
    (workspace / ".fsq-agent-workspace").write_text("ok", encoding="utf-8")

    report = DoctorService(
        provider_diagnostics=_FakeProvider(),
        platform_probe_factory=_FakePlatformFactory(),
        environ={},
    ).run(DoctorRequest(platform="web", mode="strict", working_directory=tmp_path))

    assert all(check.id != "runtime_secrets.presence" for check in report.checks)


def test_interrupted_repair_returns_final_report_with_completed_repairs(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config.web.yaml").write_text("harness:\n  platform: web\n", encoding="utf-8")

    class MissingPathProbe:
        def probe(self, timeout_seconds: float = 5.0):
            from fsq_agent.models import DoctorFix

            return [
                DiagnosticProbeResult(
                    id="web.browser.executable",
                    category="Web",
                    status="fail",
                    summary="Browser path is missing.",
                    affected_targets=["strict"],
                    fixes=[DoctorFix(description="Set Chrome.", environment_variable="FSQ_WEB_BROWSER_EXECUTABLE_PATH")],
                )
            ]

    class Factory:
        def create(self, platform, harness_settings):
            return MissingPathProbe()

    class Prompter:
        def choose(self, message, options, default):
            return default

        def confirm(self, message, default=True):
            return True

        def text(self, message):
            raise KeyboardInterrupt()

    report = DoctorService(
        provider_diagnostics=_FakeProvider(),
        platform_probe_factory=Factory(),
        prompter=Prompter(),
        environ={},
    ).run(DoctorRequest(platform="web", mode="strict", interactive=True, working_directory=tmp_path))

    assert report.exit_code == 130
    assert report.status == "cancelled"
    assert report.repairs[0].action_id == "workspace.initialize"
    assert report.repairs[0].status == "applied"


def test_malformed_config_still_runs_independent_platform_checks(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config.web.yaml").write_text("harness: [invalid", encoding="utf-8")
    workspace = tmp_path / ".fsq-agent-workspace"
    workspace.mkdir()
    (workspace / ".fsq-agent-workspace").write_text("ok", encoding="utf-8")

    report = DoctorService(
        provider_diagnostics=_FakeProvider(),
        platform_probe_factory=_FakePlatformFactory(),
        environ={"FSQ_WEB_BROWSER_EXECUTABLE_PATH": "chrome.exe"},
    ).run(DoctorRequest(platform="web", mode="strict", working_directory=tmp_path))

    assert next(check for check in report.checks if check.id == "config.valid").status == "fail"
    assert next(check for check in report.checks if check.id == "web.ready").status == "pass"


def test_environment_and_provider_repairs_both_rerun(tmp_path: Path) -> None:
    from fsq_agent.config import Settings
    from fsq_agent.models import DoctorFix, EnvironmentFileUpdate, HarnessSettings

    class Provider:
        def __init__(self):
            self.probes = 0

        def probe(self, settings, timeout_seconds=5.0):
            self.probes += 1
            return [
                DiagnosticProbeResult(
                    id="provider.github_copilot.credentials",
                    category="Provider",
                    status="pass" if self.probes > 1 else "fail",
                    summary="provider",
                    affected_targets=["dynamic", "ai_assertion"],
                    fixes=[] if self.probes > 1 else [DoctorFix(description="refresh", repair_action="provider.refresh_copilot_token")],
                )
            ]

        def refresh_cached_copilot_provider_token(self, settings):
            return None

    class PlatformProbe:
        def probe(self, timeout_seconds=5.0):
            return [
                DiagnosticProbeResult(
                    id="web.browser.executable",
                    category="Web",
                    status="fail",
                    summary="path",
                    affected_targets=["dynamic"],
                    fixes=[DoctorFix(description="set", environment_variable="FSQ_WEB_BROWSER_EXECUTABLE_PATH")],
                )
            ]

    class Factory:
        def create(self, platform, harness_settings):
            return PlatformProbe()

    class Prompter:
        def choose(self, message, options, default):
            return default

        def confirm(self, message, default=True):
            return True

        def text(self, message):
            return str(tmp_path / "chrome.exe")

    (tmp_path / "chrome.exe").write_text("", encoding="utf-8")
    workspace = tmp_path / ".fsq-agent-workspace"
    workspace.mkdir()
    (workspace / ".fsq-agent-workspace").write_text("ok", encoding="utf-8")
    provider = Provider()

    def inspector(platform, workspace, environ):
        settings = Settings(harness=HarnessSettings(platform="web"))
        settings.workspace.root_dir = workspace
        return settings, []

    report = DoctorService(
        provider_diagnostics=provider,
        platform_probe_factory=Factory(),
        prompter=Prompter(),
        config_inspector=inspector,
        environment_updater=lambda path, values, backup=True: EnvironmentFileUpdate(path=path, keys=tuple(values)),
        environ={},
    ).run(DoctorRequest(platform="web", mode="all", interactive=True, working_directory=tmp_path))

    assert provider.probes == 2
    assert {repair.action_id for repair in report.repairs if repair.status == "applied"} == {
        "environment.update",
        "provider.refresh_copilot_token",
    }


def test_effective_environment_loader_is_injected(tmp_path: Path) -> None:
    calls: list[Path] = []

    def loader(working_directory: Path, process_environment):
        calls.append(working_directory)
        return {"FSQ_WEB_BROWSER_EXECUTABLE_PATH": "chrome.exe"}

    inspected: list[dict[str, str]] = []

    def inspector(platform, workspace, environ):
        inspected.append(dict(environ))
        from fsq_agent.config import Settings
        from fsq_agent.models import DiagnosticProbeResult, HarnessSettings

        settings = Settings(harness=HarnessSettings(platform="web"))
        settings.workspace.root_dir = workspace
        return settings, [
            DiagnosticProbeResult(
                id="config.valid",
                category="Configuration",
                status="pass",
                summary="valid",
                affected_targets=["strict"],
            )
        ]

    service = DoctorService(
        provider_diagnostics=_FakeProvider(),
        platform_probe_factory=_FakePlatformFactory(),
        effective_environment_loader=loader,
        config_inspector=inspector,
        environ={},
    )
    (tmp_path / "config.web.yaml").write_text("harness:\n  platform: web\n", encoding="utf-8")
    workspace = tmp_path / ".fsq-agent-workspace"
    workspace.mkdir()
    (workspace / ".fsq-agent-workspace").write_text("ok", encoding="utf-8")

    report = service.run(DoctorRequest(mode="strict", working_directory=tmp_path))

    assert report.platform == "web"
    assert calls and all(path == tmp_path for path in calls)
    assert inspected[0]["FSQ_WEB_BROWSER_EXECUTABLE_PATH"] == "chrome.exe"


def test_empty_android_device_warning_does_not_block_readiness(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config.android.yaml").write_text("harness:\n  platform: android\n", encoding="utf-8")
    workspace = tmp_path / ".fsq-agent-workspace"
    workspace.mkdir()
    (workspace / ".fsq-agent-workspace").write_text("ok", encoding="utf-8")

    class Probe:
        def probe(self, timeout_seconds=5.0):
            return [
                DiagnosticProbeResult(
                    id="android.adb.devices",
                    category="Android",
                    status="warn",
                    summary="adb is ready, but no Android device is connected.",
                    affected_targets=["strict"],
                    fixes=[],
                ),
                DiagnosticProbeResult(
                    id="android.uiautomator2",
                    category="Android",
                    status="skip",
                    summary="skipped",
                    affected_targets=["strict"],
                ),
            ]

    class Factory:
        def create(self, platform, harness_settings, progress_sink=None):
            return Probe()

    report = DoctorService(
        provider_diagnostics=_FakeProvider(),
        platform_probe_factory=Factory(),
        environ={},
    ).run(DoctorRequest(platform="android", mode="strict", working_directory=tmp_path))

    assert report.exit_code == 0
    assert report.readiness.strict_core.status == "ready"
