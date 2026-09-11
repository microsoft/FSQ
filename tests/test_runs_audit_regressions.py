# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from click.testing import CliRunner

from fsq_agent.adapters.cli import _main as cli
from fsq_agent.application import ApplicationError, ExportRunReportRequest, ResolveRunArtifactRequest, ShowRunRequest, runs
from fsq_agent.execution import RunSource, allocate_run, transition_run


def _scope(monkeypatch, root):
    monkeypatch.setattr(runs, "list_workspace_registry", lambda *args: [SimpleNamespace(name="demo", root_path=root)])
    monkeypatch.setattr(runs, "inspect_registered_workspace", lambda *args, **kwargs: SimpleNamespace(platforms=[SimpleNamespace(platform="web")]))


def test_history_does_not_invent_events_only_mode_source_time_or_counts(tmp_path, monkeypatch):
    _scope(monkeypatch, tmp_path)
    directory = tmp_path / ".fsq/runs/web/history"
    directory.mkdir(parents=True)
    (directory / "events.jsonl").write_text('{"timestamp":"2026-09-08T00:00:00Z","message":"available"}\n')
    result = runs.show_run(ShowRunRequest(current_directory=tmp_path, run_id="history"))
    assert result.run.status is None
    assert result.run.mode is None
    assert result.run.source is None
    assert result.run.started_at is None
    assert result.run.result.steps is None
    assert result.warnings


def test_history_fallback_status_is_not_replaced_by_error_or_generation_time(tmp_path, monkeypatch):
    _scope(monkeypatch, tmp_path)
    directory = tmp_path / ".fsq/runs/web/history"
    directory.mkdir(parents=True)
    (directory / "report-fallback.json").write_text(json.dumps({"run_id": "history", "status": "success", "summary": "Completed.", "generated_at": "2026-09-08T00:00:00Z"}))
    result = runs.show_run(ShowRunRequest(current_directory=tmp_path, run_id="history"))
    assert result.run.status == "success"
    assert result.run.started_at is None
    assert result.run.result.steps is None


def test_export_ancestor_symlink_is_rejected_before_write(tmp_path, monkeypatch):
    _scope(monkeypatch, tmp_path)
    metadata = allocate_run(workspace=tmp_path, workspace_name="demo", platform="web", source_id="case", mode="strict", source=RunSource(kind="case", case_id="case"))
    directory = tmp_path / ".fsq/runs/web" / metadata.run_id
    transition_run(directory, metadata, "failed")
    outside = tmp_path / "outside"
    outside.mkdir()
    (directory / "exports").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ApplicationError):
        runs.export_run_report(ExportRunReportRequest(current_directory=tmp_path, run_id=metadata.run_id, format="html"))
    assert list(outside.iterdir()) == []


@pytest.mark.parametrize("mutation", ["schema", "platform", "size", "hash", "base_symlink"])
def test_export_manifest_complete_identity_and_containment(tmp_path, monkeypatch, mutation):
    _scope(monkeypatch, tmp_path)
    metadata = allocate_run(workspace=tmp_path, workspace_name="demo", platform="web", source_id="case", mode="strict", source=RunSource(kind="case", case_id="case"))
    directory = tmp_path / ".fsq/runs/web" / metadata.run_id
    identifier = "a" * 24
    base = directory / "exports" / identifier
    base.mkdir(parents=True)
    content = b"{}"
    import hashlib

    manifest = {
        "schema_version": "fsq.export/v1",
        "run_id": metadata.run_id,
        "platform": "web",
        "export_id": identifier,
        "files": [{"file_id": "report", "path": "report.json", "name": "report.json", "size_bytes": len(content), "mime_type": "application/json", "sha256": hashlib.sha256(content).hexdigest()}],
    }
    if mutation == "schema":
        manifest["schema_version"] = "unsupported"
    if mutation == "platform":
        manifest["platform"] = "android"
    if mutation == "size":
        manifest["files"][0]["size_bytes"] = 999
    if mutation == "hash":
        manifest["files"][0].pop("sha256")
    (base / "report.json").write_bytes(content)
    (base / "export-manifest.json").write_text(json.dumps(manifest))
    if mutation == "base_symlink":
        moved = tmp_path / "outside"
        base.rename(moved)
        base.symlink_to(moved, target_is_directory=True)
    with pytest.raises(ApplicationError):
        runs.resolve_run_artifact(ResolveRunArtifactRequest(current_directory=tmp_path, run_id=metadata.run_id, reference={"kind": "export", "export_id": identifier, "file_id": "report"}))


def test_installed_argv_machine_error_is_json(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("sys.argv", ["fsq", "--output", "json", "runs", "export", "missing", "--format", "html", "--output", "x.html"])
    with pytest.raises(SystemExit):
        cli.main.main()
    assert json.loads(capsys.readouterr().out)["type"] == "error"


def test_export_destination_option_does_not_select_machine_error_mode(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(cli.main, ["runs", "export", "missing", "--format", "html", "--output", "x.json"])
    assert result.exit_code != 0
    assert result.output.startswith("Error:")


@pytest.mark.parametrize(("status", "exit_code"), [("success", 0), ("failed", 1), ("inconclusive", 1), ("cancelled", 130), ("error", 5)])
def test_case_exit_categories_preserve_actual_outcome(monkeypatch, status, exit_code):
    from fsq_agent.application import CaseTestResult

    monkeypatch.setattr(cli, "test_case", lambda request: CaseTestResult(run_id="run", status=status, summary="Result", report_path=Path("report.json")))
    result = CliRunner().invoke(cli.main, ["--output", "json", "case", "test", "case.fsq.yaml", "--platform", "web"])
    assert result.exit_code == exit_code


@pytest.mark.parametrize("name", ["run.json", "events.jsonl"])
def test_historical_fact_symlinks_never_read_outside(tmp_path, monkeypatch, name):
    from fsq_agent.application import ReadRunLogsRequest

    _scope(monkeypatch, tmp_path)
    directory = tmp_path / ".fsq/runs/web/history"
    directory.mkdir(parents=True)
    external = tmp_path / "outside.json"
    external.write_text('{"message":"outside content"}\n')
    (directory / name).symlink_to(external)
    operation = (
        (lambda: runs.show_run(ShowRunRequest(current_directory=tmp_path, run_id="history")))
        if name == "run.json"
        else (lambda: runs.read_run_logs(ReadRunLogsRequest(current_directory=tmp_path, run_id="history")))
    )
    with pytest.raises(ApplicationError):
        operation()


def test_contradictory_history_is_unknown_with_specific_warning(tmp_path, monkeypatch):
    _scope(monkeypatch, tmp_path)
    directory = tmp_path / ".fsq/runs/web/history"
    directory.mkdir(parents=True)
    (directory / "report.json").write_text(json.dumps({"run_id": "history", "status": "success", "task": {"name": "Goal"}}))
    (directory / "core-report.json").write_text(json.dumps({"run_id": "history", "summary": {"status": "failed"}}))
    result = runs.show_run(ShowRunRequest(current_directory=tmp_path, run_id="history"))
    assert result.run.status is None
    assert result.run.mode is None
    assert any("conflict" in item.casefold() for item in result.warnings)


def test_human_list_keeps_measured_zero_and_local_time(monkeypatch):
    from datetime import UTC, datetime

    from fsq_agent.application import ListRunsResult, RunSummary

    value = datetime(2026, 9, 8, tzinfo=UTC)
    monkeypatch.setattr(
        cli,
        "list_runs",
        lambda request: ListRunsResult(
            workspace="demo",
            platforms=("web",),
            filters={},
            matched_count=1,
            returned_count=1,
            truncated=False,
            runs=(RunSummary(run_id="run", platform="web", status="success", started_at=value, duration_ms=0),),
        ),
    )
    result = CliRunner().invoke(cli.main, ["runs", "list"])
    assert "  0  " in result.output
    assert str(value.astimezone()) in result.output


def test_list_show_sanitize_persisted_display_without_rewriting(tmp_path, monkeypatch):
    from fsq_agent.application import ListRunsRequest
    from fsq_agent.execution import RunResultSummary

    _scope(monkeypatch, tmp_path)
    metadata = allocate_run(workspace=tmp_path, workspace_name="demo", platform="web", source_id="r", mode="explore", source=RunSource(kind="goal", goal_summary="token=CANARY_GOAL"))
    directory = tmp_path / ".fsq/runs/web" / metadata.run_id
    transition_run(directory, metadata, "failed", result=RunResultSummary(summary="token=CANARY_SUMMARY /opt/company/private.log"))
    before = (directory / "run.json").read_bytes()
    shown = runs.show_run(ShowRunRequest(current_directory=tmp_path, run_id=metadata.run_id))
    listed = runs.list_runs(ListRunsRequest(current_directory=tmp_path))
    text = shown.model_dump_json() + listed.model_dump_json()
    assert "CANARY" not in text
    assert "/opt/company" not in text
    assert (directory / "run.json").read_bytes() == before


def test_log_query_uses_url_and_host_path_sanitization(tmp_path, monkeypatch):
    from fsq_agent.application import ReadRunLogsRequest

    _scope(monkeypatch, tmp_path)
    directory = tmp_path / ".fsq/runs/web/history"
    directory.mkdir(parents=True)
    (directory / "events.jsonl").write_text(json.dumps({"message": "https://account:CANARY_URL@example.test/page?token=CANARY_QUERY /opt/company/private.log"}) + "\n")
    result = runs.read_run_logs(ReadRunLogsRequest(current_directory=tmp_path, run_id="history"))
    assert "CANARY" not in result.model_dump_json()
    assert "/opt/company" not in result.model_dump_json()


def test_malformed_historical_entry_is_isolated_and_bad_scope_rejected(tmp_path, monkeypatch):
    from fsq_agent.application import ListRunsRequest

    _scope(monkeypatch, tmp_path)
    for name, value in [("bad", {"status": {"wrong": "shape"}}), ("good", {"status": "failed"})]:
        directory = tmp_path / ".fsq/runs/web" / name
        directory.mkdir(parents=True)
        (directory / "report.json").write_text(json.dumps(value))
    result = runs.list_runs(ListRunsRequest(current_directory=tmp_path))
    assert len(result.runs) == 2
    assert next(item for item in result.runs if item.run_id == "bad").warnings
    monkeypatch.setattr(runs, "inspect_registered_workspace", lambda *a, **k: (_ for _ in ()).throw(ValueError("bad scope")))
    with pytest.raises(ApplicationError):
        runs.list_runs(ListRunsRequest(current_directory=tmp_path))


def test_cli_request_limit_and_duration_overflow_are_usage_errors(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    args = ["--output", "json", "runs", "export", "run", "--format", "json"]
    for index in range(9):
        args += ["--related-run", f"r{index}"]
    result = CliRunner().invoke(cli.main, args)
    assert result.exit_code == 2
    assert json.loads(result.output)["error"]["category"] == "request_validation"
    _scope(monkeypatch, tmp_path)
    result = CliRunner().invoke(cli.main, ["--output", "json", "runs", "list", "--since", "9" * 100 + "d"])
    assert result.exit_code == 2


def test_human_history_keeps_liveness_warnings_and_evidence(tmp_path, monkeypatch):
    from fsq_agent.application import ListRunsResult, RunSummary
    from fsq_agent.execution import RunResultSummary

    item = RunSummary(
        run_id="run",
        platform="web",
        status="running",
        persisted_status="running",
        liveness="unknown",
        result=RunResultSummary(summary="Prior failure", failed_step="step-1"),
        evidence={"status": "partial"},
        warnings=("Run ID conflict.",),
    )
    monkeypatch.setattr(cli, "list_runs", lambda request: ListRunsResult(workspace="demo", platforms=("web",), filters={}, matched_count=2, returned_count=1, truncated=True, runs=(item,)))
    result = CliRunner().invoke(cli.main, ["runs", "list"])
    assert "unknown" in result.output
    assert "Run ID conflict" in result.output
    assert "truncated" in result.output.lower()
    assert "partial" in result.output


@pytest.mark.parametrize(
    "message",
    [
        "Authorization: Bearer CANARY_AUTH",
        "Cookie: first=CANARY_ONE; second=CANARY_TWO",
        "Proxy-Authorization: Basic CANARY_BASIC",
        "Set-Cookie: sid=CANARY_SID; Path=/; HttpOnly",
        '{"headers":{"Authorization":"Bearer CANARY_JSON"}}',
    ],
)
def test_log_redaction_removes_entire_credential_headers(tmp_path, monkeypatch, message):
    from fsq_agent.application import ReadRunLogsRequest

    _scope(monkeypatch, tmp_path)
    directory = tmp_path / ".fsq/runs/web/header"
    directory.mkdir(parents=True)
    (directory / "events.jsonl").write_text(json.dumps({"message": message}) + "\n")
    result = runs.read_run_logs(ReadRunLogsRequest(current_directory=tmp_path, run_id="header"))
    assert "CANARY" not in result.model_dump_json()


@pytest.mark.parametrize("facts", [{"platform": "android"}, {"metadata": {"platform": "android"}}, {"workspace": {"name": "elsewhere"}}, {"metadata": {"workspace_name": "elsewhere"}}])
def test_historical_present_scope_identity_cannot_be_reassigned(tmp_path, monkeypatch, facts):
    _scope(monkeypatch, tmp_path)
    directory = tmp_path / ".fsq/runs/web/history"
    directory.mkdir(parents=True)
    (directory / "core-report.json").write_text(json.dumps({"run_id": "history", "summary": {"status": "success"}, **facts}))
    with pytest.raises(ApplicationError):
        runs.show_run(ShowRunRequest(current_directory=tmp_path, run_id="history"))


@pytest.mark.parametrize("key", ["clientSecret", "accessToken", "proxyAuthorization", "setCookie", "client%53ecret", "api-key"])
def test_query_display_credential_key_normalization(key):
    assert runs._safe_display_value({key: "CANARY"})[key] == "[REDACTED]"


@pytest.mark.parametrize("message", ["Bearer CANARY_SECRET", "Basic CANARY_BASIC", "id_token=CANARY_ID", "accessToken=CANARY_ACCESS", "client_secret=CANARY_CLIENT"])
def test_textual_credential_forms_share_query_redaction(message):
    assert "CANARY" not in runs._safe_display_value(message)


@pytest.mark.parametrize("counts", [{}, {"total": 1, "passed": 1}])
def test_historical_explicit_count_missing_dimensions_remain_unknown(counts):
    result = runs._historical_counts({"counts": counts})
    if not counts:
        assert result is None
    else:
        assert result.total == 1
        assert result.attempt_count is None
        assert result.unavailable_reason
