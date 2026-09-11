# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from pathlib import Path

import pytest

from fsq_agent.application import ListRunsRequest, ReadRunLogsRequest, ShowRunRequest, configure_azure_openai, list_runs, read_run_logs, show_run
from fsq_agent.config import load_user_provider_config


def _workspace(root: Path) -> None:
    workspace = root / ".fsq-agent-workspace"
    workspace.mkdir()
    (workspace / ".fsq-agent-workspace").write_text("fsq-agent workspace\n", encoding="utf-8")


def test_run_queries_list_show_and_stream_logs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    run = tmp_path / ".fsq" / "runs" / "web" / "run-1"
    run.mkdir(parents=True)
    (run / "report.json").write_text('{"summary":{"status":"passed"}}', encoding="utf-8")
    (run / "events.jsonl").write_text('{"type":"started"}\n{"type":"completed"}\n', encoding="utf-8")
    _mock_run_workspace(monkeypatch, tmp_path)

    assert [item.run_id for item in list_runs(ListRunsRequest(current_directory=tmp_path)).runs] == ["run-1"]
    assert show_run(ShowRunRequest(current_directory=tmp_path, run_id="run-1")).run.artifacts.report == "report.json"
    assert len(read_run_logs(ReadRunLogsRequest(current_directory=tmp_path, run_id="run-1")).events) == 2


@pytest.mark.parametrize("run_id", ["../escape", "missing"])
def test_run_queries_reject_escape_and_missing_runs(tmp_path: Path, run_id: str, monkeypatch: pytest.MonkeyPatch) -> None:
    _mock_run_workspace(monkeypatch, tmp_path)
    with pytest.raises(Exception, match="Run was not found"):
        show_run(ShowRunRequest(current_directory=tmp_path, run_id=run_id))


def _mock_run_workspace(monkeypatch: pytest.MonkeyPatch, root: Path) -> None:
    monkeypatch.setattr("fsq_agent.application.runs.list_workspace_registry", lambda: [type("Entry", (), {"name": "test", "root_path": root})()])
    platform = type("Platform", (), {"platform": "web", "status": "available"})()
    monkeypatch.setattr("fsq_agent.application.runs.inspect_registered_workspace", lambda _name, *args, **kwargs: type("Status", (), {"platforms": [platform]})())


def test_configure_provider_uses_user_config_without_workspace(tmp_path: Path) -> None:
    result = configure_azure_openai(base_url="https://example.openai.azure.com", model="gpt-5", api_key="secret", user_config_root=tmp_path)

    saved = load_user_provider_config(tmp_path)
    assert result.provider == "azure_openai"
    assert result.model == "gpt-5"
    assert saved.provider is not None
    assert saved.provider.type == "azure_openai"
    assert not (tmp_path / ".env").exists()
