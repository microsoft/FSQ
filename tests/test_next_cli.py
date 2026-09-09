# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import ast
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from click.testing import CliRunner

from fsq_agent.application import (
    ApplicationError,
    ApplicationErrorCategory,
    ApplicationErrorCode,
    CaseCreateResult,
    CaseTestResult,
    DoctorChecks,
    DoctorCommands,
    DoctorPlatformResult,
    DoctorPrerequisite,
    DoctorResult,
    DoctorStatusDetail,
    DoctorWorkspaceSummary,
    ListRunsResult,
    ReadRunLogsResult,
    RunLogEvent,
    RunSummary,
    WorkspaceInitializeResult,
)
from fsq_agent.cli import main
from fsq_agent.models import RunEvent


def _workspace(path: Path, monkeypatch=None) -> None:
    if monkeypatch is not None:
        monkeypatch.setattr("fsq_agent.adapters.cli._main.require_initialized_workspace", lambda _request: type("Workspace", (), {"workspace": path.resolve()})())


def test_public_command_tree() -> None:
    result = CliRunner().invoke(main, ["--help"])
    assert result.exit_code == 0
    for command in ("init", "doctor", "case", "ui", "providers", "runs"):
        assert command in result.output
    for removed in ("environments", "run", "report", "playground", "control-plane"):
        assert f"  {removed} " not in result.output


def test_main_does_not_directly_import_agent_or_coding_agent_runtime() -> None:
    source = Path("fsq_agent/adapters/cli/_main.py").read_text(encoding="utf-8")
    imports = [node for node in ast.walk(ast.parse(source)) if isinstance(node, ast.ImportFrom)]
    modules = {node.module for node in imports}
    assert "fsq_agent.agent" not in modules
    assert "fsq_agent.adapters.coding_agent" not in modules


@pytest.mark.parametrize("output", ["human", "json", "jsonl"])
def test_environments_command_is_rejected(output: str) -> None:
    result = CliRunner().invoke(main, ["--output", output, "environments"])

    assert result.exit_code == 2
    if output == "human":
        assert "No such command 'environments'" in result.output
    else:
        payload = json.loads(result.output)
        assert payload["type"] == "error"
        assert payload["error"]["category"] == "request_validation"


def test_workspace_error_is_machine_readable(tmp_path: Path) -> None:
    runner = CliRunner()
    with runner.isolated_filesystem(temp_dir=tmp_path):
        result = runner.invoke(main, ["--output", "json", "doctor"])
    assert result.exit_code == 3
    payload = json.loads(result.output)
    assert payload["type"] == "error"
    assert payload["error"]["code"] == "workspace.not_initialized"


def _doctor_result(tmp_path: Path, *, status: str = "partial") -> DoctorResult:
    ready = DoctorStatusDetail(status="ready", message="ready")
    unavailable = DoctorStatusDetail(status="unavailable", code="provider.unavailable", message="Provider unavailable.", action="Configure Provider.")
    checks = DoctorChecks(
        configuration=ready,
        runtime=ready,
        target_configuration=ready,
        target_availability=ready,
        strict_core=ready,
        provider=unavailable,
        suggestion_analyzer=unavailable,
        dynamic_agent=unavailable,
    )
    commands = DoctorCommands(case_test=ready, case_test_suggest=unavailable, case_create=unavailable)
    return DoctorResult(
        status=status,
        workspace=DoctorWorkspaceSummary(name="checkout", root=tmp_path),
        platforms=[
            DoctorPlatformResult(
                platform="web",
                status=status,
                prerequisites=[DoctorPrerequisite(identifier="appium_cli", status="unavailable", message="Appium unavailable.", action="Install Appium.")],
                checks=checks,
                commands=commands,
            )
        ],
        actions=["Configure Provider."],
    )


@pytest.mark.parametrize("output", ["json", "jsonl"])
def test_doctor_machine_output_is_one_terminal_record(tmp_path: Path, monkeypatch, output: str) -> None:
    monkeypatch.setattr("fsq_agent.adapters.cli._main.diagnose_workspace", lambda _request: _doctor_result(tmp_path))
    result = CliRunner().invoke(main, ["--output", output, "doctor"])

    assert result.exit_code == 0
    records = result.output.splitlines()
    assert len(records) == 1
    payload = json.loads(records[0])
    assert payload["type"] == "result"
    assert payload["status"] == "partial"
    assert payload["result"]["platforms"][0]["commands"]["case_test"]["status"] == "ready"


def test_doctor_human_output_shows_command_matrix_and_actions(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("fsq_agent.adapters.cli._main.diagnose_workspace", lambda _request: _doctor_result(tmp_path))
    result = CliRunner().invoke(main, ["doctor"])

    assert result.exit_code == 0
    assert "Workspace: checkout" in result.output
    assert "Prerequisites" in result.output
    assert "Appium CLI" in result.output
    assert "fsq case test --suggest" in result.output
    assert "Provider unavailable." in result.output
    assert "Action: Configure Provider." in result.output
    assert "Actions" in result.output


def test_doctor_unavailable_result_exits_four(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("fsq_agent.adapters.cli._main.diagnose_workspace", lambda _request: _doctor_result(tmp_path, status="unavailable"))
    result = CliRunner().invoke(main, ["--output", "json", "doctor"])

    assert result.exit_code == 4
    assert json.loads(result.output)["status"] == "unavailable"


def test_case_test_exposes_suggest_option() -> None:
    result = CliRunner().invoke(main, ["case", "test", "--help"])
    assert result.exit_code == 0
    assert "--suggest" in result.output


def test_providers_list_is_not_public() -> None:
    result = CliRunner().invoke(main, ["providers", "list"])
    assert result.exit_code == 2
    assert "No such command" in result.output


def test_case_create_json_maps_application_result(tmp_path: Path, monkeypatch) -> None:
    async def fake_create(_request, *, event_sink=None, agent_factory=None):
        return CaseCreateResult(run_id="run-1", task_id="task-1", status="success", summary="passed", report_path=tmp_path / "report.md", candidate_case_path=tmp_path / "recorded.fsq.yaml")

    monkeypatch.setattr("fsq_agent.adapters.cli._main.create_case", fake_create)
    runner = CliRunner()
    with runner.isolated_filesystem(temp_dir=tmp_path):
        result = runner.invoke(main, ["--output", "json", "case", "create", "--platform", "web", "--goal", "Verify search"])
    assert result.exit_code == 0
    assert json.loads(result.output)["result"]["candidate_case_path"].endswith("recorded.fsq.yaml")


@pytest.mark.parametrize("platform", ["android", "web", "windows", "macos"])
def test_case_create_maps_each_platform_and_current_directory(tmp_path: Path, monkeypatch, platform: str) -> None:
    captured = {}

    async def fake_create(request, *, event_sink=None, agent_factory=None):
        captured["request"] = request
        return CaseCreateResult(run_id="run-1", task_id="task-1", status="success", summary="passed", report_path=tmp_path / "report.md")

    monkeypatch.setattr("fsq_agent.adapters.cli._main.create_case", fake_create)
    with CliRunner().isolated_filesystem(temp_dir=tmp_path):
        result = CliRunner().invoke(main, ["case", "create", "--platform", platform, "--goal", "Verify search"])
        expected_directory = Path.cwd()

    assert result.exit_code == 0
    assert captured["request"].platform == platform
    assert captured["request"].goal == "Verify search"
    assert captured["request"].current_directory == expected_directory


@pytest.mark.parametrize("arguments", [["--goal", "Verify search"], ["--platform", "web"]])
def test_case_create_requires_platform_and_goal(arguments: list[str]) -> None:
    result = CliRunner().invoke(main, ["--output", "json", "case", "create", *arguments])
    assert result.exit_code == 2
    payload = json.loads(result.output)
    assert payload["type"] == "error"
    assert payload["operation"] == "case.create"


def test_case_create_jsonl_emits_events_then_one_terminal_result(tmp_path: Path, monkeypatch) -> None:
    async def fake_create(_request, *, event_sink=None, agent_factory=None):
        assert event_sink is not None
        event_sink(RunEvent(run_id="run-1", task_id="task-1", type="run_started", title="Started", sequence=1, timestamp=datetime(2026, 1, 1, tzinfo=UTC)))
        event_sink(RunEvent(run_id="run-1", task_id="task-1", type="run_completed", title="Completed", sequence=2, timestamp=datetime(2026, 1, 1, tzinfo=UTC)))
        return CaseCreateResult(run_id="run-1", task_id="task-1", status="success", summary="passed", report_path=tmp_path / "report.md")

    monkeypatch.setattr("fsq_agent.adapters.cli._main.create_case", fake_create)
    result = CliRunner().invoke(main, ["--output", "jsonl", "case", "create", "--platform", "web", "--goal", "Verify search"])
    records = [json.loads(line) for line in result.output.splitlines()]

    assert result.exit_code == 0
    assert [record["type"] for record in records] == ["event", "event", "result"]
    assert all(record["operation"] == "case.create" for record in records)
    assert records[-1]["result"]["candidate_case_path"] is None


def test_case_create_failed_result_uses_exit_one(tmp_path: Path, monkeypatch) -> None:
    async def fake_create(_request, *, event_sink=None, agent_factory=None):
        return CaseCreateResult(run_id="run-1", task_id="task-1", status="failed", summary="failed", report_path=tmp_path / "report.md")

    monkeypatch.setattr("fsq_agent.adapters.cli._main.create_case", fake_create)
    result = CliRunner().invoke(main, ["--output", "json", "case", "create", "--platform", "web", "--goal", "Verify search"])
    assert result.exit_code == 1
    assert json.loads(result.output)["result"]["status"] == "failed"


@pytest.mark.parametrize(
    ("category", "expected_exit"),
    [
        (ApplicationErrorCategory.REQUEST_VALIDATION, 2),
        (ApplicationErrorCategory.WORKSPACE_CONFIGURATION, 3),
        (ApplicationErrorCategory.CONFIGURATION, 3),
        (ApplicationErrorCategory.UNAVAILABLE, 4),
        (ApplicationErrorCategory.INTERNAL, 5),
    ],
)
def test_case_create_maps_application_error_categories(monkeypatch, category: ApplicationErrorCategory, expected_exit: int) -> None:
    async def fail(_request, *, event_sink=None, agent_factory=None):
        raise ApplicationError(code=ApplicationErrorCode.INTERNAL_ERROR, category=category, message="safe failure")

    monkeypatch.setattr("fsq_agent.adapters.cli._main.create_case", fail)
    result = CliRunner().invoke(main, ["--output", "json", "case", "create", "--platform", "web", "--goal", "Verify search"])
    assert result.exit_code == expected_exit
    assert json.loads(result.output)["type"] == "error"


def test_case_create_unexpected_error_is_safe(monkeypatch) -> None:
    async def fail(_request, *, event_sink=None, agent_factory=None):
        raise RuntimeError("secret backend detail")

    monkeypatch.setattr("fsq_agent.adapters.cli._main.create_case", fail)
    result = CliRunner().invoke(main, ["--output", "json", "case", "create", "--platform", "web", "--goal", "Verify search"])
    assert result.exit_code == 5
    assert "RuntimeError" in result.output
    assert "secret backend detail" not in result.output


def test_case_test_failure_uses_exit_one_and_jsonl_terminal_record(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("fsq_agent.adapters.cli._main.test_case", lambda _request: CaseTestResult(run_id="run-1", status="failed", summary="failed", report_path=tmp_path / "core-report.md"))
    runner = CliRunner()
    with runner.isolated_filesystem(temp_dir=tmp_path):
        result = runner.invoke(main, ["--output", "jsonl", "case", "test", "--platform", "web", "search.fsq.yaml"])
    assert result.exit_code == 1
    terminal = json.loads(result.output)
    assert terminal["type"] == "result"
    assert terminal["result"]["status"] == "failed"


@pytest.mark.parametrize("platform", ["android", "web", "windows", "macos"])
@pytest.mark.parametrize("suggest", [False, True])
def test_case_test_maps_platform_path_suggest_and_current_directory(tmp_path: Path, monkeypatch, platform: str, suggest: bool) -> None:
    captured = {}

    def fake_test(request):
        captured["request"] = request
        return CaseTestResult(run_id="run-1", status="success", summary="passed", report_path=tmp_path / "report.md")

    monkeypatch.setattr("fsq_agent.adapters.cli._main.test_case", fake_test)
    arguments = ["case", "test", "--platform", platform, "search.fsq.yaml"]
    if suggest:
        arguments.append("--suggest")
    with CliRunner().isolated_filesystem(temp_dir=tmp_path):
        result = CliRunner().invoke(main, arguments)
        expected_directory = Path.cwd()

    assert result.exit_code == 0
    assert captured["request"].platform == platform
    assert captured["request"].case_path == Path("search.fsq.yaml")
    assert captured["request"].suggest is suggest
    assert captured["request"].current_directory == expected_directory


@pytest.mark.parametrize("arguments", [["search.fsq.yaml"], ["--platform", "web"]])
def test_case_test_requires_platform_and_case_path(arguments: list[str]) -> None:
    result = CliRunner().invoke(main, ["--output", "json", "case", "test", *arguments])
    assert result.exit_code == 2
    payload = json.loads(result.output)
    assert payload["type"] == "error"
    assert payload["operation"] == "case.test"


def test_case_test_json_exposes_run_local_artifacts_and_warning(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        "fsq_agent.adapters.cli._main.test_case",
        lambda _request: CaseTestResult(
            run_id="run-1",
            status="success",
            summary="passed",
            report_path=tmp_path / "report.md",
            evidence_manifest_path=tmp_path / "evidence.json",
            suggestion_path=tmp_path / "case-suggestions.json",
            candidate_case_path=tmp_path / "candidate.fsq.yaml",
            warnings=["case.suffix_deprecated: rename this Case to *.fsq.yaml"],
        ),
    )
    result = CliRunner().invoke(main, ["--output", "json", "case", "test", "--platform", "web", "legacy.codex.yaml", "--suggest"])
    payload = json.loads(result.output)

    assert result.exit_code == 0
    assert payload["warnings"] == ["case.suffix_deprecated: rename this Case to *.fsq.yaml"]
    assert payload["result"]["suggestion_path"].endswith("case-suggestions.json")
    assert payload["result"]["candidate_case_path"].endswith("candidate.fsq.yaml")


def test_case_test_human_suggest_reports_artifacts_and_source_immutability(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        "fsq_agent.adapters.cli._main.test_case",
        lambda _request: CaseTestResult(
            run_id="run-1",
            status="success",
            summary="passed",
            report_path=tmp_path / "report.md",
            suggestion_path=tmp_path / "case-suggestions.json",
            candidate_case_path=tmp_path / "candidate.fsq.yaml",
        ),
    )
    result = CliRunner().invoke(main, ["case", "test", "--platform", "web", "search.fsq.yaml", "--suggest"])

    assert result.exit_code == 0
    assert f"Suggestions: {tmp_path / 'case-suggestions.json'}" in result.output
    assert f"Candidate Case: {tmp_path / 'candidate.fsq.yaml'}" in result.output
    assert "Source Case was not modified." in result.output


@pytest.mark.parametrize(
    ("category", "expected_exit"),
    [
        (ApplicationErrorCategory.REQUEST_VALIDATION, 2),
        (ApplicationErrorCategory.WORKSPACE_CONFIGURATION, 3),
        (ApplicationErrorCategory.CONFIGURATION, 3),
        (ApplicationErrorCategory.UNAVAILABLE, 4),
        (ApplicationErrorCategory.INTERNAL, 5),
    ],
)
def test_case_test_maps_application_error_categories(monkeypatch, category: ApplicationErrorCategory, expected_exit: int) -> None:
    def fail(_request):
        raise ApplicationError(code=ApplicationErrorCode.INTERNAL_ERROR, category=category, message="safe failure")

    monkeypatch.setattr("fsq_agent.adapters.cli._main.test_case", fail)
    result = CliRunner().invoke(main, ["--output", "json", "case", "test", "--platform", "web", "search.fsq.yaml"])
    assert result.exit_code == expected_exit
    assert json.loads(result.output)["type"] == "error"


def test_case_test_unexpected_error_is_safe(monkeypatch) -> None:
    def fail(_request):
        raise RuntimeError("secret backend detail")

    monkeypatch.setattr("fsq_agent.adapters.cli._main.test_case", fail)
    result = CliRunner().invoke(main, ["--output", "json", "case", "test", "--platform", "web", "search.fsq.yaml"])
    assert result.exit_code == 5
    assert "RuntimeError" in result.output
    assert "secret backend detail" not in result.output


def test_legacy_commands_are_rejected_as_usage_errors() -> None:
    for command in ("run", "replay", "report", "playground", "control-plane"):
        result = CliRunner().invoke(main, [command])
        assert result.exit_code == 2
        assert "No such command" in result.output


def test_non_interactive_provider_configuration_is_rejected(tmp_path: Path, monkeypatch) -> None:
    runner = CliRunner()
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _workspace(Path.cwd(), monkeypatch)
        result = runner.invoke(main, ["--non-interactive", "providers", "configure", "github_copilot"])
    assert result.exit_code == 2
    assert "Human interactive mode" in result.output


def test_ui_starts_control_plane_from_non_workspace_directory(tmp_path: Path, monkeypatch) -> None:
    captured = {}
    monkeypatch.setattr("fsq_agent.adapters.cli._main.run_control_plane", lambda options: captured.update(options=options))

    def fail_if_workspace_required(_request) -> None:
        raise AssertionError("fsq ui must not require the current directory to be a workspace")

    monkeypatch.setattr("fsq_agent.adapters.cli._main.require_initialized_workspace", fail_if_workspace_required)
    runner = CliRunner()
    with runner.isolated_filesystem(temp_dir=tmp_path):
        assert not (Path.cwd() / ".fsq").exists()
        result = runner.invoke(main, ["ui", "--host", "127.0.0.2", "--port", "9000", "--no-open-browser"])
    assert result.exit_code == 0
    options = captured["options"]
    assert options.host == "127.0.0.2"
    assert options.port == 9000
    assert options.open_browser is False


def test_internal_error_human_output_exposes_only_safe_exception_type(monkeypatch) -> None:
    def fail() -> None:
        raise RuntimeError("secret backend detail")

    monkeypatch.setattr("fsq_agent.adapters.cli._main.provider_status", fail)
    result = CliRunner().invoke(main, ["providers", "status"])
    assert result.exit_code == 5
    assert "Diagnostic: RuntimeError" in result.output
    assert "secret backend detail" not in result.output
    assert "Traceback" not in result.output


def test_internal_error_machine_output_contains_only_safe_exception_type(monkeypatch) -> None:
    def fail() -> None:
        raise RuntimeError("secret backend detail")

    monkeypatch.setattr("fsq_agent.adapters.cli._main.provider_status", fail)
    for output in ("json", "jsonl"):
        result = CliRunner().invoke(main, ["--output", output, "providers", "status"])
        assert result.exit_code == 5
        payload = json.loads(result.output)
        assert payload["error"]["details"] == {"exception_type": "RuntimeError"}
        assert "secret backend detail" not in result.output
        assert "Traceback" not in result.output


def test_runs_logs_jsonl_emits_one_record_per_event(tmp_path: Path, monkeypatch) -> None:
    runner = CliRunner()
    with runner.isolated_filesystem(temp_dir=tmp_path):
        _workspace(Path.cwd(), monkeypatch)
        monkeypatch.setattr(
            "fsq_agent.adapters.cli._main.read_run_logs",
            lambda _request: ReadRunLogsResult(
                run_id="run-1",
                platform="web",
                filters={},
                matched_count=2,
                returned_count=2,
                truncated=False,
                events=(RunLogEvent(sequence=1, label="started"), RunLogEvent(sequence=2, label="completed")),
            ),
        )
        result = runner.invoke(main, ["--output", "jsonl", "runs", "logs", "run-1"])
    assert result.exit_code == 0
    records = [json.loads(line) for line in result.output.splitlines()]
    assert [record["type"] for record in records] == ["event", "event", "result"]
    assert [record["event"]["label"] for record in records[:-1]] == ["started", "completed"]
    assert records[-1]["result"]["returned_count"] == 2


def test_runs_list_rejects_invalid_status_as_usage_error() -> None:
    result = CliRunner().invoke(main, ["--output", "json", "runs", "list", "--status", "not-a-status"])
    assert result.exit_code == 2
    payload = json.loads(result.output)
    assert payload["error"]["category"] == "request_validation"


def test_runs_use_canonical_platform_directory(tmp_path: Path, monkeypatch) -> None:
    runner = CliRunner()
    with runner.isolated_filesystem(temp_dir=tmp_path):
        workspace = Path.cwd()
        _workspace(workspace, monkeypatch)
        monkeypatch.setattr(
            "fsq_agent.adapters.cli._main.list_runs",
            lambda _request: ListRunsResult(
                workspace="test",
                platforms=("web",),
                filters={},
                matched_count=1,
                returned_count=1,
                truncated=False,
                runs=(RunSummary(run_id="run-1", platform="web", mode="strict", status="success"),),
            ),
        )

        result = runner.invoke(main, ["--output", "json", "runs", "list"])

    assert result.exit_code == 0
    assert json.loads(result.output)["result"]["runs"][0]["run_id"] == "run-1"


def test_init_maps_web_options_to_application(monkeypatch, tmp_path: Path) -> None:
    captured = {}
    monkeypatch.setattr(
        "fsq_agent.adapters.cli._main.initialize_workspace",
        lambda request: (
            captured.update(request=request)
            or WorkspaceInitializeResult(status="initialized", name="project", root_path=tmp_path, platform="web", driver_status="ready", browser_executable_path=tmp_path / "chrome")
        ),
    )
    runner = CliRunner()
    with runner.isolated_filesystem(temp_dir=tmp_path):
        result = runner.invoke(main, ["init", "--platform", "web", "--browser-channel", "chrome"])
    assert result.exit_code == 0
    assert captured["request"].browser_channel == "chrome"
    assert captured["request"].browser_executable_path is None


def test_init_rejects_install_driver_option() -> None:
    result = CliRunner().invoke(main, ["init", "--platform", "web", "--browser-channel", "chrome", "--install-driver"])

    assert result.exit_code == 2
    assert "No such option: --install-driver" in result.output


@pytest.mark.parametrize(
    ("arguments", "platform", "expected"),
    [
        (["--platform", "android", "--app-id", "com.example.app"], "android", {"app_id": "com.example.app"}),
        (["--platform", "web", "--browser-channel", "msedge-canary", "--browser-executable-path", "browser"], "web", {"browser_channel": "msedge-canary"}),
        (
            ["--platform", "windows", "--app-path", "application.exe", "--window-title-re", "Example.*", "--launch-args", "--safe-mode"],
            "windows",
            {"window_title_re": "Example.*", "launch_args": "--safe-mode"},
        ),
        (["--platform", "macos", "--bundle-id", "com.example.app"], "macos", {"bundle_id": "com.example.app"}),
    ],
)
def test_init_maps_each_platform_request(monkeypatch, tmp_path: Path, arguments: list[str], platform: str, expected: dict[str, object]) -> None:
    captured = {}
    monkeypatch.setattr(
        "fsq_agent.adapters.cli._main.initialize_workspace",
        lambda request: captured.update(request=request) or WorkspaceInitializeResult(status="initialized", name="project", root_path=tmp_path, platform=platform, driver_status="ready"),
    )
    runner = CliRunner()
    with runner.isolated_filesystem(temp_dir=tmp_path):
        Path("browser").write_text("", encoding="utf-8")
        Path("application.exe").write_text("", encoding="utf-8")
        result = runner.invoke(main, ["init", *arguments, "--name", "custom", "--env", "TOKEN=secret=value", "--update-existing"])

    assert result.exit_code == 0
    request = captured["request"]
    assert request.platform == platform
    assert request.name == "custom"
    assert request.env == {"TOKEN": "secret=value"}
    assert request.update_existing is True
    for field, value in expected.items():
        assert getattr(request, field) == value


@pytest.mark.parametrize(
    "arguments",
    [
        ["--platform", "android"],
        ["--platform", "web"],
        ["--platform", "windows"],
        ["--platform", "macos"],
        ["--platform", "android", "--app-id", "com.example", "--browser-channel", "chrome"],
    ],
)
def test_init_invalid_platform_target_fails_before_workspace_mutation(tmp_path: Path, monkeypatch, arguments: list[str]) -> None:
    monkeypatch.setattr("fsq_agent.application._workspace_init.persist_workspace", lambda **kwargs: pytest.fail("workspace must not mutate"))
    with CliRunner().isolated_filesystem(temp_dir=tmp_path):
        result = CliRunner().invoke(main, ["init", *arguments])

    assert result.exit_code == 3


@pytest.mark.parametrize(
    "env_arguments",
    [
        ["--env", "MISSING_EQUALS"],
        ["--env", "=value"],
        ["--env", "EMPTY="],
        ["--env", "TOKEN=one", "--env", "TOKEN=two"],
    ],
)
def test_init_rejects_invalid_env_syntax_before_application(monkeypatch, env_arguments: list[str]) -> None:
    monkeypatch.setattr("fsq_agent.adapters.cli._main.initialize_workspace", lambda _request: pytest.fail("application must not run"))

    result = CliRunner().invoke(main, ["init", "--platform", "android", "--app-id", "com.example", *env_arguments])

    assert result.exit_code == 2
    assert "TOKEN=one" not in result.output
    assert "TOKEN=two" not in result.output


@pytest.mark.parametrize("output", ["json", "jsonl"])
def test_init_machine_success_is_one_terminal_result(tmp_path: Path, monkeypatch, output: str) -> None:
    monkeypatch.setattr(
        "fsq_agent.adapters.cli._main.initialize_workspace",
        lambda _request: WorkspaceInitializeResult(status="initialized", name="project", root_path=tmp_path, platform="android", driver_status="ready"),
    )

    result = CliRunner().invoke(main, ["--output", output, "init", "--platform", "android", "--app-id", "com.example"])

    assert result.exit_code == 0
    records = [json.loads(line) for line in result.output.splitlines()]
    assert len(records) == 1
    assert records[0]["type"] == "result"
    assert records[0]["operation"] == "init"
    assert records[0]["result"]["driver_status"] == "ready"


@pytest.mark.parametrize("output", ["json", "jsonl"])
def test_init_readiness_failure_is_safe_machine_error_without_workspace_mutation(tmp_path: Path, monkeypatch, output: str) -> None:
    def fail(_request):
        raise ApplicationError(
            code=ApplicationErrorCode.ENVIRONMENT_UNAVAILABLE,
            category=ApplicationErrorCategory.UNAVAILABLE,
            message="web Python runtime dependency is missing",
            action="Reinstall or repair fsq-agent.",
        )

    monkeypatch.setattr("fsq_agent.adapters.cli._main.initialize_workspace", fail)

    result = CliRunner().invoke(main, ["--output", output, "init", "--platform", "web", "--browser-channel", "chrome"])

    assert result.exit_code == 4
    record = json.loads(result.output)
    assert record["type"] == "error"
    assert record["operation"] == "init"
    assert record["error"]["code"] == "environment.unavailable"
    assert "pip" not in result.output
    assert not (tmp_path / ".fsq").exists()
