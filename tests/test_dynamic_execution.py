# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import ast
import asyncio
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from fsq_agent.config import Settings
from fsq_agent.execution import DynamicExecutionRequest, DynamicExecutionService, load_run_metadata
from fsq_agent.models import ReportArtifact, Task, TaskResult, VerificationResult


def _settings(root: Path) -> Settings:
    settings = Settings()
    settings.workspace.root_dir = root
    settings.output.runs_dir = root / ".fsq" / "runs" / "web"
    settings.harness.platform = "web"
    return settings


class _Agent:
    def __init__(self, settings: Settings, *, status: str = "success", error: BaseException | None = None, wrong_id: bool = False) -> None:
        self.settings = settings
        self.status = status
        self.error = error
        self.wrong_id = wrong_id
        self.run_ids: list[str] = []

    async def run(self, task: Task, event_sink=None, *, run_id: str) -> TaskResult:
        self.run_ids.append(run_id)
        directory = self.settings.output.runs_dir / run_id
        assert load_run_metadata(directory).status == "running"
        if self.error is not None:
            raise self.error
        (directory / "report.md").write_text("report", encoding="utf-8")
        return TaskResult(
            task_id=task.id,
            status=self.status,
            steps=[],
            verification=VerificationResult(status=self.status, summary="Execution evidence reviewed."),
            report=ReportArtifact(run_id="wrong-run" if self.wrong_id else run_id, path=directory / "report.md"),
        )


@pytest.mark.parametrize("status", ["success", "failed", "inconclusive"])
async def test_execution_allocates_one_identity_and_finalizes_after_agent(tmp_path: Path, status: str) -> None:
    settings = _settings(tmp_path)
    agent = _Agent(settings, status=status)
    request = DynamicExecutionRequest(task=Task(id="task", description="Task"), settings=settings)

    result = await DynamicExecutionService(agent=agent).execute(request)

    assert agent.run_ids == [result.task_result.report.run_id]
    assert re.fullmatch(r"task-\d{8}T\d{6}Z-[0-9a-f]{6}", agent.run_ids[0])
    metadata = load_run_metadata(settings.output.runs_dir / agent.run_ids[0])
    assert metadata.status == status
    assert metadata.result.summary == "Execution evidence reviewed."
    assert metadata.artifacts.report_markdown == "report.md"
    assert len(list(settings.output.runs_dir.glob("*/run.json"))) == 1


async def test_execution_repeated_tasks_allocate_distinct_runs(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    agent = _Agent(settings)
    service = DynamicExecutionService(agent=agent)
    request = DynamicExecutionRequest(task=Task(id="same-task", description="Task"), settings=settings)
    first = await service.execute(request)
    second = await service.execute(request)
    assert first.task_result.report.run_id != second.task_result.report.run_id
    assert len(list(settings.output.runs_dir.glob("*/run.json"))) == 2


@pytest.mark.parametrize("cancelled", [False, True])
async def test_execution_finalizes_agent_error_or_cancellation_and_preserves_exception(tmp_path: Path, cancelled: bool) -> None:
    settings = _settings(tmp_path)
    error = asyncio.CancelledError() if cancelled else RuntimeError("primary failure")
    agent = _Agent(settings, error=error)
    with pytest.raises(type(error)) as failure:
        await DynamicExecutionService(agent=agent).execute(DynamicExecutionRequest(task=Task(description="Task"), settings=settings))
    assert failure.value is error
    assert load_run_metadata(settings.output.runs_dir / agent.run_ids[0]).status == ("cancelled" if cancelled else "error")


@pytest.mark.parametrize("before_allocation", [False, True])
async def test_execution_callback_cancellation_has_owned_lifecycle(tmp_path: Path, before_allocation: bool) -> None:
    settings = _settings(tmp_path)
    agent = _Agent(settings)
    calls = []

    def check_cancelled() -> None:
        calls.append(True)
        if before_allocation or len(calls) == 2:
            raise RuntimeError("transport cancellation")

    with pytest.raises(RuntimeError, match="transport cancellation"):
        await DynamicExecutionService(agent=agent).execute(DynamicExecutionRequest(task=Task(description="Task"), settings=settings, cancellation_check=check_cancelled))
    if before_allocation:
        assert not agent.run_ids
        assert not settings.output.runs_dir.exists()
    else:
        assert load_run_metadata(settings.output.runs_dir / agent.run_ids[0]).status == "cancelled"


async def test_execution_rejects_mismatched_report_identity(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    agent = _Agent(settings, wrong_id=True)
    with pytest.raises(ValueError, match="Run ID"):
        await DynamicExecutionService(agent=agent).execute(DynamicExecutionRequest(task=Task(description="Task"), settings=settings))
    assert load_run_metadata(settings.output.runs_dir / agent.run_ids[0]).status == "error"


async def test_execution_initial_metadata_failure_prevents_agent_call(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(tmp_path)
    agent = _Agent(settings)

    def fail_allocation(**kwargs):
        raise OSError("initial metadata failure")

    monkeypatch.setattr("fsq_agent.execution.dynamic.allocate_run", fail_allocation)
    with pytest.raises(OSError, match="initial metadata failure"):
        await DynamicExecutionService(agent=agent).execute(DynamicExecutionRequest(task=Task(description="Task"), settings=settings))
    assert agent.run_ids == []


async def test_execution_failure_finalization_does_not_mask_original_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from fsq_agent.execution import dynamic

    settings = _settings(tmp_path)
    primary = RuntimeError("original agent failure")
    agent = _Agent(settings, error=primary)
    transition = dynamic.transition_run

    def fail_terminal(directory, metadata, status, **updates):
        if status == "error":
            raise OSError("secondary metadata failure")
        return transition(directory, metadata, status, **updates)

    monkeypatch.setattr(dynamic, "transition_run", fail_terminal)
    with pytest.raises(RuntimeError) as failure:
        await DynamicExecutionService(agent=agent).execute(DynamicExecutionRequest(task=Task(description="Task"), settings=settings))
    assert failure.value is primary


async def test_execution_terminal_metadata_failure_preserves_report_and_reports_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from fsq_agent.execution import dynamic

    settings = _settings(tmp_path)
    agent = _Agent(settings)
    transition = dynamic.transition_run

    def fail_success(directory, metadata, status, **updates):
        if status == "success":
            raise OSError("terminal metadata failure")
        return transition(directory, metadata, status, **updates)

    monkeypatch.setattr(dynamic, "transition_run", fail_success)
    with pytest.raises(OSError, match="terminal metadata failure"):
        await DynamicExecutionService(agent=agent).execute(DynamicExecutionRequest(task=Task(description="Task"), settings=settings))
    directory = settings.output.runs_dir / agent.run_ids[0]
    assert (directory / "report.md").read_text(encoding="utf-8") == "report"
    assert load_run_metadata(directory).status == "error"


def test_agent_has_no_execution_import_even_inside_functions() -> None:
    package = Path(__file__).parents[1] / "fsq_agent" / "agent"
    imports = []
    for path in package.glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("fsq_agent.execution"):
                imports.append(f"{path.name}:{node.lineno}")
            if isinstance(node, ast.Import) and any(alias.name.startswith("fsq_agent.execution") for alias in node.names):
                imports.append(f"{path.name}:{node.lineno}")
    assert imports == []


async def test_dynamic_metadata_redacts_configured_values_before_truncation_and_slugging(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    private_value = "Private_Value_2026"
    settings.runtime_secrets.set_values({"TEST_ACCOUNT_PASSWORD": private_value})

    class SensitiveSummaryAgent(_Agent):
        async def run(self, task: Task, event_sink=None, *, run_id: str) -> TaskResult:
            result = await super().run(task, event_sink, run_id=run_id)
            result.verification.summary = f"Observed {private_value} during the task."
            return result

    agent = SensitiveSummaryAgent(settings)
    source_id = "login-with-private-value-2026"
    task = Task(id=source_id, name="x" * 192 + private_value, description="Task")
    result = await DynamicExecutionService(agent=agent).execute(DynamicExecutionRequest(task=task, settings=settings))
    directory = settings.output.runs_dir / result.task_result.report.run_id
    serialized = (directory / "run.json").read_text(encoding="utf-8")
    metadata = load_run_metadata(directory)
    assert private_value not in serialized
    assert "Private_" not in metadata.source.goal_summary
    assert "private-value-2026" not in directory.name
    assert metadata.source.goal_summary == "x" * 192 + "***"
    assert metadata.result.summary == "Observed *** during the task."
    assert task.id == source_id


@pytest.mark.parametrize("path_style", ["native", "posix", "escaped", "case_variant"])
async def test_dynamic_metadata_removes_absolute_workspace_paths(tmp_path: Path, path_style: str) -> None:
    workspace = tmp_path / "workspace folder"
    settings = _settings(workspace)
    root_text = str(workspace.resolve())
    if path_style == "posix":
        root_text = workspace.resolve().as_posix()
    elif path_style == "escaped":
        root_text = root_text.replace("\\", "\\\\")
    elif path_style == "case_variant" and workspace.drive:
        root_text = root_text.upper()

    class PathSummaryAgent(_Agent):
        async def run(self, task: Task, event_sink=None, *, run_id: str) -> TaskResult:
            result = await super().run(task, event_sink, run_id=run_id)
            result.verification.summary = f"Read {root_text}/cases/input.yaml."
            return result

    agent = PathSummaryAgent(settings)
    task = Task(id="workspace-task", name=f"Inspect {root_text}", description="Task")
    result = await DynamicExecutionService(agent=agent).execute(DynamicExecutionRequest(task=task, settings=settings))
    metadata = load_run_metadata(settings.output.runs_dir / result.task_result.report.run_id)
    assert metadata.source.goal_summary == "Inspect <workspace>"
    assert metadata.result.summary == "Read <workspace>/cases/input.yaml."
    assert result.task_result.verification.summary == f"Read {root_text}/cases/input.yaml."


@pytest.mark.parametrize(("report_format", "filename"), [("markdown", "report.md"), ("json", "report-fallback.json"), ("html", "report.html")])
async def test_dynamic_report_index_respects_declared_format(tmp_path: Path, report_format: str, filename: str) -> None:
    settings = _settings(tmp_path)

    class FormattedReportAgent(_Agent):
        async def run(self, task: Task, event_sink=None, *, run_id: str) -> TaskResult:
            result = await super().run(task, event_sink, run_id=run_id)
            report = self.settings.output.runs_dir / run_id / filename
            report.write_text("report content", encoding="utf-8")
            return result.model_copy(update={"report": ReportArtifact(run_id=run_id, path=report, format=report_format)})

    result = await DynamicExecutionService(agent=FormattedReportAgent(settings)).execute(DynamicExecutionRequest(task=Task(description="Task"), settings=settings))
    index = load_run_metadata(settings.output.runs_dir / result.task_result.report.run_id).artifacts
    assert index.report_markdown == (filename if report_format == "markdown" else None)
    assert index.html_report == (filename if report_format == "html" else None)
    assert index.report == ("report.json" if report_format == "markdown" else filename if report_format == "json" else None)


@pytest.mark.parametrize("status", ["success", "failed", "inconclusive"])
@pytest.mark.parametrize("summary_length", [2000, 2001, 5000])
async def test_dynamic_metadata_bounds_summary_without_changing_business_result(tmp_path: Path, status: str, summary_length: int) -> None:
    settings = _settings(tmp_path)
    summary = "v" * summary_length

    class LongSummaryAgent(_Agent):
        async def run(self, task: Task, event_sink=None, *, run_id: str) -> TaskResult:
            result = await super().run(task, event_sink, run_id=run_id)
            result.verification.summary = summary
            result.report.path.write_text(summary, encoding="utf-8")
            return result

    result = await DynamicExecutionService(agent=LongSummaryAgent(settings, status=status)).execute(DynamicExecutionRequest(task=Task(description="Task"), settings=settings))
    metadata = load_run_metadata(settings.output.runs_dir / result.task_result.report.run_id)
    assert metadata.status == status
    assert metadata.result.summary == summary[:2000]
    assert result.task_result.verification.summary == summary
    assert result.task_result.report.path.read_text(encoding="utf-8") == summary


@pytest.mark.parametrize("sensitive_kind", ["secret", "workspace"])
async def test_dynamic_summary_redacts_before_metadata_length_limit(tmp_path: Path, sensitive_kind: str) -> None:
    settings = _settings(tmp_path)
    private_value = "private-value-across-metadata-boundary"
    settings.runtime_secrets.set_values({"TEST_ACCOUNT_PASSWORD": private_value})
    sensitive_value = private_value if sensitive_kind == "secret" else str(tmp_path)
    replacement = "***" if sensitive_kind == "secret" else "<workspace>"
    summary = "v" * 1990 + sensitive_value + " result" * 100

    class BoundarySummaryAgent(_Agent):
        async def run(self, task: Task, event_sink=None, *, run_id: str) -> TaskResult:
            result = await super().run(task, event_sink, run_id=run_id)
            result.verification.summary = summary
            return result

    result = await DynamicExecutionService(agent=BoundarySummaryAgent(settings)).execute(DynamicExecutionRequest(task=Task(description="Task"), settings=settings))
    metadata = load_run_metadata(settings.output.runs_dir / result.task_result.report.run_id)
    assert metadata.status == "success"
    assert metadata.result.summary == ("v" * 1990 + replacement + " result" * 100)[:2000]
    assert result.task_result.verification.summary == summary


async def test_dynamic_workspace_identity_uses_configured_user_registry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(tmp_path / "different-directory-name")
    selected_root = tmp_path / "selected-config"
    settings.openai_agents.user_config_root = selected_root
    observed_roots = []

    def list_registry(user_config_root=None):
        observed_roots.append(user_config_root)
        return [SimpleNamespace(root_path=settings.workspace.root_dir, name="checkout" if user_config_root == selected_root else "wrong-default-name")]

    monkeypatch.setattr("fsq_agent.execution.dynamic.list_workspace_registry", list_registry)
    result = await DynamicExecutionService(agent=_Agent(settings)).execute(DynamicExecutionRequest(task=Task(description="Task"), settings=settings))
    metadata = load_run_metadata(settings.output.runs_dir / result.task_result.report.run_id)
    assert metadata.workspace == {"name": "checkout"}
    assert observed_roots == [selected_root]
