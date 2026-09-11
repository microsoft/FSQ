# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from fsq_agent.application import ListRunsRequest, ReadRunLogsRequest, ShowRunRequest, list_runs, read_run_logs, show_run
from fsq_agent.execution import RunArtifactIndex, RunSource, allocate_run, load_run_metadata, transition_run
from fsq_agent.report import generate_static_run_report


def _workspace(monkeypatch: pytest.MonkeyPatch, root: Path, platforms=("web", "android")) -> None:
    monkeypatch.setattr("fsq_agent.application.runs.list_workspace_registry", lambda: [type("Entry", (), {"name": "demo", "root_path": root})()])
    values = [type("Platform", (), {"platform": item, "status": "available"})() for item in platforms]
    monkeypatch.setattr("fsq_agent.application.runs.inspect_registered_workspace", lambda _name, *args, **kwargs: type("Status", (), {"platforms": values})())


def test_allocate_and_finalize_run_metadata(tmp_path: Path) -> None:
    metadata = allocate_run(
        workspace=tmp_path, workspace_name="demo", platform="web", source_id="Search Case", mode="strict", source=RunSource(kind="case", case_id="search"), now=datetime(2026, 8, 28, tzinfo=UTC)
    )
    run_dir = tmp_path / ".fsq" / "runs" / "web" / metadata.run_id
    assert metadata.run_id.startswith("search-case-20260828T000000Z-")
    assert load_run_metadata(run_dir).status == "preparing"
    running = transition_run(run_dir, metadata, "running")
    finalizing = transition_run(run_dir, running, "finalizing")
    completed = transition_run(run_dir, finalizing, "success", completed_at=datetime(2026, 8, 28, 0, 0, 2, tzinfo=UTC))
    assert completed.duration_ms is not None
    assert completed.duration_unavailable_reason is None
    with pytest.raises(ValueError, match="immutable"):
        transition_run(run_dir, completed, "failed")


def test_list_aggregates_filters_and_detects_conflicts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _workspace(monkeypatch, tmp_path)
    for platform, status in (("web", "success"), ("android", "failed")):
        metadata = allocate_run(workspace=tmp_path, workspace_name="demo", platform=platform, source_id=platform, mode="strict", source=RunSource(kind="case", case_id="login"))
        transition_run(tmp_path / ".fsq" / "runs" / platform / metadata.run_id, metadata, status)
    result = list_runs(ListRunsRequest(current_directory=tmp_path, statuses=("failed",), case_id="login"))
    assert result.matched_count == 1
    assert result.runs[0].platform == "android"


def test_logs_redact_and_invalid_line_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _workspace(monkeypatch, tmp_path, ("web",))
    run = tmp_path / ".fsq" / "runs" / "web" / "run-1"
    run.mkdir(parents=True)
    (run / "events.jsonl").write_text('{"sequence":1,"level":"error","message":"token=secret-value"}\n', encoding="utf-8")
    result = read_run_logs(ReadRunLogsRequest(current_directory=tmp_path, run_id="run-1"))
    assert "secret-value" not in result.model_dump_json()
    (run / "events.jsonl").write_text("not-json\n", encoding="utf-8")
    with pytest.raises(Exception, match="Run logs are invalid"):
        read_run_logs(ReadRunLogsRequest(current_directory=tmp_path, run_id="run-1"))


def test_show_conflict_requires_platform(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _workspace(monkeypatch, tmp_path)
    for platform in ("web", "android"):
        run = tmp_path / ".fsq" / "runs" / platform / "same"
        run.mkdir(parents=True)
        (run / "events.jsonl").write_text("{}\n", encoding="utf-8")
    with pytest.raises(Exception, match="multiple platforms"):
        show_run(ShowRunRequest(current_directory=tmp_path, run_id="same"))
    assert show_run(ShowRunRequest(current_directory=tmp_path, run_id="same", platform="web")).run.platform == "web"


def test_static_html_escapes_facts_and_does_not_change_metadata(tmp_path: Path) -> None:
    run = tmp_path / "run"
    run.mkdir()
    metadata = run / "run.json"
    metadata.write_text('{"truth":true}', encoding="utf-8")
    before = metadata.read_bytes()
    with pytest.raises(Exception, match="source identity"):
        generate_static_run_report(run, {"run_id": "<script>alert(1)</script>"})
    assert not (run / "report.html").exists()
    assert metadata.read_bytes() == before


def test_run_metadata_rejects_interrupted_and_unsafe_updates(tmp_path: Path) -> None:
    metadata = allocate_run(workspace=tmp_path, workspace_name="demo", platform="web", source_id="safe", mode="strict", source=RunSource(kind="case", case_id="safe"))
    run_dir = tmp_path / ".fsq" / "runs" / "web" / metadata.run_id
    with pytest.raises(ValueError, match="move forward"):
        transition_run(run_dir, metadata, "interrupted")
    with pytest.raises(ValidationError, match="contained relative"):
        transition_run(run_dir, metadata, "success", artifacts=RunArtifactIndex(report="../secret"))


def test_terminal_metadata_cannot_be_overwritten_by_stale_object(tmp_path: Path) -> None:
    metadata = allocate_run(workspace=tmp_path, workspace_name="demo", platform="web", source_id="safe", mode="strict", source=RunSource(kind="case", case_id="safe"))
    run_dir = tmp_path / ".fsq" / "runs" / "web" / metadata.run_id
    transition_run(run_dir, metadata, "success")
    with pytest.raises(ValueError, match="immutable"):
        transition_run(run_dir, metadata, "failed")


def test_static_html_redacts_log_secrets(tmp_path: Path) -> None:
    run = tmp_path / "run"
    run.mkdir()
    (run / "events.jsonl").write_text('{"message":"token=secret-value cookie=session-value"}\n', encoding="utf-8")
    with pytest.raises(Exception, match="source identity") as error:
        generate_static_run_report(run, {"run_id": "safe"})
    assert "secret-value" not in str(error.value)
    assert "session-value" not in str(error.value)
    assert not (run / "report.html").exists()


def test_static_html_redacts_overview_secrets(tmp_path: Path) -> None:
    run = tmp_path / "run"
    run.mkdir()
    with pytest.raises(Exception, match="source identity") as error:
        generate_static_run_report(
            run,
            {"run_id": "safe", "authorization": "Bearer secret-token", "result": {"summary": "Cookie: session-value password=hunter2"}},
        )
    assert all(value not in str(error.value) for value in ("secret-token", "session-value", "hunter2"))
    assert not (run / "report.html").exists()


def test_run_lookup_rejects_symlink_directory_escape(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _workspace(monkeypatch, tmp_path, ("web",))
    external = tmp_path.parent / f"{tmp_path.name}-external"
    external.mkdir()
    (external / "events.jsonl").write_text("{}\n", encoding="utf-8")
    platform_root = tmp_path / ".fsq" / "runs" / "web"
    platform_root.mkdir(parents=True)
    (platform_root / "escaped").symlink_to(external, target_is_directory=True)
    with pytest.raises(Exception, match="not found"):
        read_run_logs(ReadRunLogsRequest(current_directory=tmp_path, run_id="escaped"))
