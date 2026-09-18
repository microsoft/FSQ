# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import json
import shlex
import tomllib
from pathlib import Path

import pytest
from click.testing import CliRunner

from fsq_agent.adapters.cli import main
from fsq_agent.application import (
    ApplicationError,
    ApplicationErrorCategory,
    ApplicationErrorCode,
    CaseCreateResult,
    CaseTestResult,
    DoctorChecks,
    DoctorCommands,
    DoctorPlatformResult,
    DoctorResult,
    DoctorStatusDetail,
    DoctorWorkspaceSummary,
    ReadRunLogsResult,
    RunDetail,
    ShowRunResult,
)
from fsq_agent.execution import RunSource, allocate_run, transition_run

ROOT = Path(__file__).resolve().parents[1]
AGENT_PATH = ROOT / ".codex" / "agents" / "fsq_test_runner.toml"
CLI_MODULE = "fsq_agent.adapters.cli._main"
COMMAND_NAMES = ("doctor", "case.format", "case.create", "case.create.named", "case.test", "case.test.suggest", "runs.show", "runs.logs")


@pytest.fixture
def agent_definition() -> dict[str, object]:
    return tomllib.loads(AGENT_PATH.read_text(encoding="utf-8"))


@pytest.fixture
def commands(agent_definition: dict[str, object]) -> dict[str, list[str]]:
    instructions = agent_definition["developer_instructions"]
    assert isinstance(instructions, str)
    templates: dict[str, list[str]] = {}
    for line in instructions.splitlines():
        if not line.startswith("fsq "):
            continue
        tokens = shlex.split(line)
        assert tokens[:4] == ["fsq", "--output", "json", "--non-interactive"]
        name = ".".join(tokens[4:6]) if tokens[4] in {"case", "runs"} else tokens[4]
        if "--name" in tokens:
            name += ".named"
        if "--suggest" in tokens:
            name += ".suggest"
        assert name not in templates
        templates[name] = tokens[1:]
    assert set(templates) == set(COMMAND_NAMES)
    return templates


def _arguments(commands: dict[str, list[str]], name: str, tmp_path: Path, **overrides: str) -> list[str]:
    values = {
        "platform": "web",
        "goal": 'Verify the "Done" label; treat $(anything) as goal text.',
        "case_name": "verify-done",
        "case_path": str(tmp_path / "case with spaces.fsq.yaml"),
        "run_id": "verify-done-20260909T000000Z-123abc",
    }
    values.update(overrides)
    return [argument.format_map(values) for argument in commands[name]]


def test_codex_definition_is_portable_and_task_scoped(agent_definition: dict[str, object]) -> None:
    assert set(agent_definition) == {"name", "description", "sandbox_mode", "developer_instructions"}
    assert agent_definition["name"] == AGENT_PATH.stem == "fsq_test_runner"
    assert agent_definition["sandbox_mode"] == "workspace-write"
    assert isinstance(agent_definition["description"], str)
    assert agent_definition["description"].strip()
    instructions = agent_definition["developer_instructions"]
    assert isinstance(instructions, str)
    for marker in ("fsq.machine/v1", "publication_outcome", "case.suggestion_failed", "result.run.status", "unknown", "not_started"):
        assert marker in instructions
    assert str(ROOT) not in instructions
    assert "/Users/" not in instructions
    assert "danger-full-access" not in instructions


def test_definition_explicitly_reserves_case_yaml_generation_for_fsq(agent_definition: dict[str, object]) -> None:
    # Structural prompt regression only; this does not prove live model compliance.
    instructions = " ".join(str(agent_definition["developer_instructions"]).split())
    assert instructions.index("## Case YAML ownership") < instructions.index("## Required delegation context")
    for rule in (
        "Never author test Cases or generate Case YAML yourself.",
        "Use the actual FSQ-generated YAML file unchanged.",
        "Do not reconstruct YAML from code, screenshots, snapshots, logs, summaries, or examples.",
        'If FSQ produces no YAML file, report "no Case generated"; never fabricate a replacement.',
        "Do not ask the parent or another agent to write the YAML instead.",
    ):
        assert rule in instructions
    assert "ask FSQ to generate" in str(agent_definition["description"])


@pytest.mark.parametrize("name", COMMAND_NAMES)
def test_embedded_command_templates_match_public_cli(commands: dict[str, list[str]], tmp_path: Path, name: str) -> None:
    result = CliRunner().invoke(main, [*_arguments(commands, name, tmp_path), "--help"])
    assert result.exit_code == 0, result.output
    assert "Usage:" in result.output


@pytest.mark.parametrize("platform", ["android", "web", "windows", "macos"])
@pytest.mark.parametrize("named", [False, True])
def test_create_template_passes_exact_request_and_published_result(commands, tmp_path, monkeypatch, platform, named) -> None:
    requests = []
    report = tmp_path / ".fsq" / "runs" / platform / "run-1" / "report.md"
    published = tmp_path / "cases" / platform / "verify-done.fsq.yaml"

    async def create(request, *, event_sink=None, agent_factory=None):
        requests.append(request)
        return CaseCreateResult(
            run_id="run-1",
            task_id="task-1",
            status="success",
            summary="Goal verified.",
            report_path=report,
            candidate_case_path=report.parent / "recorded.fsq.yaml",
            case_name="verify-done",
            published_case_path=published,
            publication_outcome="created",
        )

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(f"{CLI_MODULE}.create_case", create)
    arguments = _arguments(commands, "case.create.named" if named else "case.create", tmp_path, platform=platform)
    result = CliRunner().invoke(main, arguments)
    payload = json.loads(result.stdout)

    assert result.exit_code == 0, result.output
    assert len(requests) == 1
    assert requests[0].current_directory == tmp_path
    assert requests[0].platform == platform
    assert requests[0].goal == arguments[arguments.index("--goal") + 1]
    assert requests[0].case_name == ("verify-done" if named else None)
    assert payload["schema_version"] == "fsq.machine/v1"
    assert payload["type"] == "result"
    assert payload["operation"] == "case.create"
    assert payload["result"]["run_id"] == "run-1"
    assert payload["result"]["published_case_path"] == str(published)


@pytest.mark.parametrize(
    ("status", "publication", "exit_code"),
    [("success", "conflict", 1), ("success", "failed", 0), ("failed", "not_requested", 1), ("inconclusive", "not_requested", 1)],
)
def test_create_execution_and_publication_remain_separate(commands, tmp_path, monkeypatch, status, publication, exit_code) -> None:
    async def create(_request, *, event_sink=None, agent_factory=None):
        return CaseCreateResult(run_id="run-1", task_id="task-1", status=status, summary="Execution result.", report_path=tmp_path / "report.md", publication_outcome=publication)

    monkeypatch.setattr(f"{CLI_MODULE}.create_case", create)
    result = CliRunner().invoke(main, _arguments(commands, "case.create", tmp_path))
    payload = json.loads(result.stdout)

    assert result.exit_code == exit_code
    assert payload["result"]["status"] == status
    assert payload["result"]["publication_outcome"] == publication
    assert payload["result"]["published_case_path"] is None


@pytest.mark.parametrize("suggest", [False, True])
def test_test_template_invokes_once_and_retains_failure_and_suggestion_paths(commands, tmp_path, monkeypatch, suggest) -> None:
    requests = []

    def test(request):
        requests.append(request)
        return CaseTestResult(
            run_id="run-1",
            status="failed",
            summary="Expected heading was absent.",
            report_path=tmp_path / "core-report.md",
            evidence_manifest_path=tmp_path / "evidence-manifest.json",
            suggestion_path=tmp_path / "case-suggestions.json" if suggest else None,
            candidate_case_path=tmp_path / "candidate.fsq.yaml" if suggest else None,
        )

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(f"{CLI_MODULE}.test_case", test)
    result = CliRunner().invoke(main, _arguments(commands, "case.test.suggest" if suggest else "case.test", tmp_path))
    payload = json.loads(result.stdout)

    assert result.exit_code == 1
    assert len(requests) == 1
    assert requests[0].current_directory == tmp_path
    assert requests[0].case_path == tmp_path / "case with spaces.fsq.yaml"
    assert requests[0].suggest is suggest
    assert payload["operation"] == "case.test"
    assert payload["result"]["status"] == "failed"
    assert bool(payload["result"]["suggestion_path"]) is suggest
    assert bool(payload["result"]["candidate_case_path"]) is suggest


def test_suggestion_error_retains_completed_run_references(commands, tmp_path, monkeypatch) -> None:
    def test(_request):
        raise ApplicationError(
            code=ApplicationErrorCode.CASE_SUGGESTION_FAILED,
            category=ApplicationErrorCategory.UNAVAILABLE,
            message="Suggestion analysis unavailable; the execution report is preserved.",
            details={"run_id": "run-1", "report_path": str(tmp_path / "core-report.md")},
        )

    monkeypatch.setattr(f"{CLI_MODULE}.test_case", test)
    result = CliRunner().invoke(main, _arguments(commands, "case.test.suggest", tmp_path))
    payload = json.loads(result.stdout)

    assert result.exit_code == 4
    assert payload["type"] == "error"
    assert payload["error"]["code"] == "case.suggestion_failed"
    assert payload["error"]["details"]["run_id"] == "run-1"
    assert payload["error"]["details"]["report_path"].endswith("core-report.md")


def test_partial_doctor_exposes_command_specific_readiness(commands, tmp_path, monkeypatch) -> None:
    ready = DoctorStatusDetail(status="ready")
    unavailable = DoctorStatusDetail(status="unavailable", code="provider.unavailable", message="Provider unavailable.")
    diagnosis = DoctorResult(
        status="partial",
        workspace=DoctorWorkspaceSummary(name="checkout", root=tmp_path),
        platforms=(
            DoctorPlatformResult(
                platform="web",
                status="partial",
                checks=DoctorChecks(
                    configuration=ready,
                    runtime=ready,
                    target_configuration=ready,
                    target_availability=ready,
                    strict_core=ready,
                    provider=unavailable,
                    suggestion_analyzer=unavailable,
                    dynamic_agent=unavailable,
                ),
                commands=DoctorCommands(case_test=ready, case_test_suggest=unavailable, case_create=unavailable),
            ),
        ),
        actions=(),
    )
    monkeypatch.setattr(f"{CLI_MODULE}.diagnose_workspace", lambda _request: diagnosis)
    result = CliRunner().invoke(main, _arguments(commands, "doctor", tmp_path))
    payload = json.loads(result.stdout)

    assert result.exit_code == 0
    assert payload["status"] == "partial"
    readiness = payload["result"]["platforms"][0]["commands"]
    assert readiness["case_test"]["status"] == "ready"
    assert readiness["case_create"]["status"] == "unavailable"
    assert readiness["case_test_suggest"]["status"] == "unavailable"


@pytest.mark.parametrize(
    ("code", "category", "exit_code"),
    [(ApplicationErrorCode.WORKSPACE_NOT_INITIALIZED, ApplicationErrorCategory.WORKSPACE_CONFIGURATION, 3), (ApplicationErrorCode.ENVIRONMENT_UNAVAILABLE, ApplicationErrorCategory.UNAVAILABLE, 4)],
)
def test_doctor_blockers_are_machine_readable(commands, tmp_path, monkeypatch, code, category, exit_code) -> None:
    def diagnose(_request):
        raise ApplicationError(code=code, category=category, message="Readiness blocked.", action="Ask the operator to resolve the prerequisite.")

    monkeypatch.setattr(f"{CLI_MODULE}.diagnose_workspace", diagnose)
    result = CliRunner().invoke(main, _arguments(commands, "doctor", tmp_path))
    payload = json.loads(result.stdout)
    assert result.exit_code == exit_code
    assert payload["operation"] == "doctor"
    assert payload["type"] == "error"
    assert payload["error"]["code"] == code


def test_format_template_is_static_and_preserves_source_bytes(commands, tmp_path) -> None:
    case_path = tmp_path / "case with spaces.fsq.yaml"
    original = (ROOT / "examples" / "web" / "example-domain.fsq.yaml").read_bytes()
    case_path.write_bytes(original)
    result = CliRunner().invoke(main, _arguments(commands, "case.format", tmp_path))
    payload = json.loads(result.stdout)

    assert result.exit_code in (0, 1), result.output
    assert payload["operation"] == "case.format"
    assert payload["result"]["valid"] is True
    assert payload["result"]["changed"] is False
    assert payload["result"]["mode"] == "check"
    assert case_path.read_bytes() == original


def test_show_success_is_not_run_success(commands, tmp_path, monkeypatch) -> None:
    metadata = allocate_run(workspace=tmp_path, workspace_name="checkout", platform="web", source_id="case", mode="strict", source=RunSource(kind="case", case_id="case"))
    run_dir = tmp_path / ".fsq" / "runs" / "web" / metadata.run_id
    failed = transition_run(run_dir, metadata, "failed")
    requests = []

    def show(request):
        requests.append(request)
        return ShowRunResult(workspace="checkout", run=RunDetail(**failed.model_dump()))

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(f"{CLI_MODULE}.show_run", show)
    result = CliRunner().invoke(main, _arguments(commands, "runs.show", tmp_path, run_id=metadata.run_id))
    payload = json.loads(result.stdout)

    assert result.exit_code == 0
    assert requests[0].run_id == metadata.run_id
    assert requests[0].platform == "web"
    assert payload["status"] == "success"
    assert payload["result"]["run"]["status"] == "failed"


def test_logs_template_uses_exact_run_and_bounded_limit(commands, tmp_path, monkeypatch) -> None:
    requests = []

    def logs(request):
        requests.append(request)
        return ReadRunLogsResult(run_id=request.run_id, platform=request.platform, filters={}, matched_count=0, returned_count=0, truncated=False, events=())

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(f"{CLI_MODULE}.read_run_logs", logs)
    result = CliRunner().invoke(main, _arguments(commands, "runs.logs", tmp_path, run_id="exact-run"))
    payload = json.loads(result.stdout)

    assert result.exit_code == 0
    assert requests[0].current_directory == tmp_path
    assert requests[0].run_id == "exact-run"
    assert requests[0].platform == "web"
    assert requests[0].limit == 50
    assert payload["operation"] == "runs.logs"
    assert payload["result"]["events"] == []


def test_integration_guide_is_discoverable_and_links_the_shipped_definition() -> None:
    guide = ROOT / "docs" / "codex-integration.md"
    assert "../.codex/agents/fsq_test_runner.toml" in guide.read_text(encoding="utf-8")
    for readme in ("README.md", "README.zh-CN.md"):
        assert "docs/codex-integration.md" in (ROOT / readme).read_text(encoding="utf-8")
