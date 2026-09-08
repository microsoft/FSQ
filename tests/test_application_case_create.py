# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from fsq_agent.application import ApplicationError, ApplicationErrorCode, CaseCreateRequest, create_case
from fsq_agent.config import Settings
from fsq_agent.execution import DynamicExecutionResult, RecordingResult
from fsq_agent.models import ReportArtifact, Task, TaskResult, VerificationResult


class _FakeAgent:
    def __init__(self, result: TaskResult) -> None:
        self.result = result
        self.task: Task | None = None
        self.event_sink: Any = None
        self.run_id: str | None = None

    async def run(self, task: Task, event_sink=None, *, run_id: str) -> TaskResult:
        self.task = task
        self.event_sink = event_sink
        self.run_id = run_id
        report = self.result.report.model_copy(update={"run_id": run_id, "path": self.result.report.path.parent.parent / run_id / "report.md"})
        return self.result.model_copy(update={"report": report})


def _settings(root: Path) -> Settings:
    settings = Settings()
    settings.workspace.root_dir = root
    settings.harness.platform = "web"
    settings.output.runs_dir = root / "runs"
    return settings


def _task_result(tmp_path: Path) -> TaskResult:
    return TaskResult(
        task_id="verify-product-search",
        status="success",
        steps=[],
        verification=VerificationResult(status="success", summary="Search works."),
        report=ReportArtifact(run_id="run-1", path=tmp_path / "runs" / "run-1" / "report.md"),
    )


@pytest.mark.asyncio
async def test_create_case_builds_goal_task_and_delegates_to_agent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    workspace = tmp_path
    monkeypatch.setattr("fsq_agent.application.cases.require_initialized_workspace", lambda _request: type("Workspace", (), {"workspace": workspace})())
    settings = _settings(tmp_path)
    loaded: list[tuple[str, Path]] = []
    agent = _FakeAgent(_task_result(tmp_path))

    result = await create_case(
        CaseCreateRequest(current_directory=tmp_path, platform="web", goal="  Verify   product search  "),
        settings_loader=lambda path, platform: loaded.append((platform, path)) or settings,
        agent_factory=lambda value: agent if value is settings else None,
    )

    assert loaded == [("web", workspace.resolve())]
    assert agent.task is not None
    assert agent.task.id == "verify-product-search"
    assert agent.task.name == "Verify product search"
    assert agent.task.planning_reference_kind == "goal"
    assert agent.task.planning_reference_text == "Verify product search"
    assert result.run_id == agent.run_id
    assert result.run_id.startswith("verify-product-search-")
    assert result.status == "success"
    assert result.report_path == tmp_path / "runs" / result.run_id / "report.md"
    assert result.candidate_case_path is None


@pytest.mark.asyncio
async def test_create_case_forwards_transport_neutral_event_sink(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("fsq_agent.application.cases.require_initialized_workspace", lambda _request: type("Workspace", (), {"workspace": tmp_path.resolve()})())
    agent = _FakeAgent(_task_result(tmp_path))
    events: list[object] = []
    sink = events.append

    await create_case(
        CaseCreateRequest(current_directory=tmp_path, platform="web", goal="Verify product search"),
        event_sink=sink,
        settings_loader=lambda _platform, _path: _settings(tmp_path),
        agent_factory=lambda _settings: agent,
    )

    assert agent.event_sink == sink


@pytest.mark.asyncio
async def test_create_case_rejects_blank_goal_before_loading_settings(tmp_path: Path) -> None:
    workspace = tmp_path / ".fsq-agent-workspace"
    workspace.mkdir()
    (workspace / ".fsq-agent-workspace").write_text("fsq-agent workspace\n", encoding="utf-8")
    called = False

    def load_settings(_platform: str, _path: Path) -> object:
        nonlocal called
        called = True
        return object()

    with pytest.raises(ApplicationError, match="Goal cannot be empty") as error:
        await create_case(
            CaseCreateRequest(current_directory=tmp_path, platform="web", goal="   "),
            settings_loader=load_settings,
        )

    assert called is False
    assert error.value.code == ApplicationErrorCode.CASE_GOAL_INVALID


@pytest.mark.asyncio
async def test_create_case_requires_workspace_before_settings_or_agent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def reject_workspace(_request):
        calls.append("workspace")
        raise ApplicationError(
            code=ApplicationErrorCode.WORKSPACE_NOT_INITIALIZED,
            category="workspace_configuration",
            message="not initialized",
        )

    monkeypatch.setattr("fsq_agent.application.cases.require_initialized_workspace", reject_workspace)

    with pytest.raises(ApplicationError) as error:
        await create_case(
            CaseCreateRequest(current_directory=tmp_path, platform="web", goal="Verify search"),
            settings_loader=lambda _platform, _path: calls.append("settings"),
            agent_factory=lambda _settings: calls.append("agent"),
        )

    assert error.value.code == ApplicationErrorCode.WORKSPACE_NOT_INITIALIZED
    assert calls == ["workspace"]


@pytest.mark.asyncio
async def test_create_case_settings_failure_does_not_create_agent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("fsq_agent.application.cases.require_initialized_workspace", lambda _request: type("Workspace", (), {"workspace": tmp_path.resolve()})())
    agent_created = False

    def fail_settings(_platform: str, _path: Path):
        raise ApplicationError(code=ApplicationErrorCode.PROVIDER_UNAVAILABLE, category="unavailable", message="provider unavailable")

    def create_agent(_settings):
        nonlocal agent_created
        agent_created = True

    with pytest.raises(ApplicationError) as error:
        await create_case(
            CaseCreateRequest(current_directory=tmp_path, platform="web", goal="Verify search"),
            settings_loader=fail_settings,
            agent_factory=create_agent,
        )

    assert error.value.code == ApplicationErrorCode.PROVIDER_UNAVAILABLE
    assert agent_created is False


@pytest.mark.asyncio
@pytest.mark.parametrize(("recording_status", "has_candidate"), [("recorded", True), ("skipped", False), ("failed", False)])
async def test_create_case_only_returns_recorded_run_local_candidate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, recording_status: str, has_candidate: bool) -> None:
    monkeypatch.setattr("fsq_agent.application.cases.require_initialized_workspace", lambda _request: type("Workspace", (), {"workspace": tmp_path.resolve()})())
    candidate = tmp_path / "runs" / "run-1" / "candidate.fsq.yaml"
    cases_dir = tmp_path / "cases" / "web"
    settings = SimpleNamespace(cases=SimpleNamespace(dir=cases_dir))
    recording = RecordingResult(
        status=recording_status,
        recording_path=tmp_path / "runs" / "run-1" / "recording.json",
        recorded_case_path=candidate,
        published_case_path=None,
        command_count=1,
        required_runtime_secret_names=(),
        warnings=(),
        skipped_tool_calls=(),
        errors=(),
        validation_status="valid",
        draft=False,
    )

    class FakeExecutionService:
        def __init__(self, *, agent) -> None:
            pass

        async def execute(self, request) -> DynamicExecutionResult:
            assert request.record is True
            assert request.publication_directory == cases_dir
            return DynamicExecutionResult(task_result=_task_result(tmp_path), recording=recording)

    monkeypatch.setattr("fsq_agent.application.cases.DynamicExecutionService", FakeExecutionService)
    result = await create_case(
        CaseCreateRequest(current_directory=tmp_path, platform="web", goal="Verify search"),
        settings_loader=lambda _platform, _path: settings,
        agent_factory=lambda _settings: object(),
    )

    assert (result.candidate_case_path == candidate) is has_candidate
