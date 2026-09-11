# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import json
from pathlib import Path

import pytest

from fsq_agent.application import ApplicationError, GetRunReportRequest, ReadRunLogsRequest, ShowRunRequest, runs
from fsq_agent.execution import RunLifecycleService, load_run_metadata
from tests.test_runs_audit_regressions import _scope


@pytest.mark.parametrize("name", ["run.json", "report.json", "core-report.json", "events.jsonl"])
def test_oversized_history_rejected_before_unbounded_read(tmp_path, monkeypatch, name):
    _scope(monkeypatch, tmp_path)
    directory = tmp_path / ".fsq/runs/web/history"
    directory.mkdir(parents=True)
    path = directory / name
    with path.open("wb") as stream:
        stream.truncate(33 * 1024 * 1024)
    original = Path.open
    reads = []

    def checked_open(self, *args, **kwargs):
        if self == path:
            reads.append(self)
            raise AssertionError("oversized input was opened")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", checked_open)
    request = {"current_directory": tmp_path, "run_id": "history"}
    operation, argument = (runs.read_run_logs, ReadRunLogsRequest(**request)) if name == "events.jsonl" else (runs.show_run, ShowRunRequest(**request))
    with pytest.raises(ApplicationError):
        operation(argument)
    assert not reads


@pytest.mark.parametrize("name", ["run.json", "execution-result.json", "owner.json"])
def test_execution_readers_bound_metadata(tmp_path, monkeypatch, name):
    path = tmp_path / name
    with path.open("wb") as stream:
        stream.truncate(33 * 1024 * 1024)
    reads = []
    monkeypatch.setattr(Path, "read_text", lambda *a, **k: reads.append(True) or "{}")
    if name == "owner.json":
        assert RunLifecycleService.inspect_owner(tmp_path)["status"] == "unknown"
    else:
        with pytest.raises(ValueError, match="limit"):
            (load_run_metadata if name == "run.json" else RunLifecycleService.load_result)(tmp_path)
    assert not reads


def test_history_conflicting_source_and_times_remain_unknown_through_report(tmp_path, monkeypatch):
    _scope(monkeypatch, tmp_path)
    directory = tmp_path / ".fsq/runs/web/history"
    directory.mkdir(parents=True)
    for filename, source, start, end, duration in [
        ("report.json", "alpha", "00:00:00", "00:00:01", 10),
        ("core-report.json", "beta", "00:00:02", "00:00:03", 900),
    ]:
        (directory / filename).write_text(
            json.dumps(
                {
                    "run_id": "history",
                    "mode": "strict",
                    "status": "failed",
                    "source": {"kind": "case", "case_id": source},
                    "started_at": f"2026-09-08T{start}Z",
                    "completed_at": f"2026-09-08T{end}Z",
                    "duration_ms": duration,
                    "duration_unavailable_reason": None,
                }
            )
        )
    before = {p.name: p.read_bytes() for p in directory.iterdir()}
    shown = runs.show_run(ShowRunRequest(current_directory=tmp_path, run_id="history"))
    for key in ("source", "started_at", "completed_at", "duration_ms"):
        assert getattr(shown.run, key) is None
        assert shown.run.availability[key] == "conflicting_sources"
    projected = runs.get_run_report(GetRunReportRequest(current_directory=tmp_path, run_id="history")).report
    assert projected.metrics["run_duration"]["value"] is None
    assert projected.source.get("case_id") is None
    assert any("source" in warning and "conflict" in warning for warning in projected.warnings)
    assert {p.name: p.read_bytes() for p in directory.iterdir()} == before


def test_history_equivalent_timestamps_and_measured_zero_are_preserved(tmp_path, monkeypatch):
    _scope(monkeypatch, tmp_path)
    directory = tmp_path / ".fsq/runs/web/history"
    directory.mkdir(parents=True)
    for filename, timestamp in [("report.json", "2026-09-08T00:00:00Z"), ("core-report.json", "2026-09-08T08:00:00+08:00")]:
        (directory / filename).write_text(json.dumps({"mode": "strict", "status": "failed", "started_at": timestamp, "duration_ms": 0, "duration_unavailable_reason": None}))
    shown = runs.show_run(ShowRunRequest(current_directory=tmp_path, run_id="history"))
    assert shown.run.started_at is not None
    assert shown.run.duration_ms == 0
    assert shown.run.duration_unavailable_reason is None
