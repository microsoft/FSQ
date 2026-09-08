# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import base64
import json
from pathlib import Path

import httpx
import pytest
import yaml
from click.testing import CliRunner

from fsq_agent.adapters.cli import main
from fsq_agent.application import GenerateRunHtmlRequest, ListRunsRequest, ReadRunLogsRequest, ShowRunRequest, generate_run_html, list_runs, read_run_logs, show_run
from fsq_agent.config import load_workspace_platform_settings, refresh_provider_settings, validate_provider_settings
from fsq_agent.providers import check_provider_readiness
from fsq_agent.report import resolve_report_path
from fsq_agent.tools import ToolArtifactStore


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def _snapshot(root: Path) -> dict[Path, bytes]:
    process_lock = root / "home" / ".fsq" / ".config.lock"
    return {path.relative_to(root): path.read_bytes() for path in root.rglob("*") if path.is_file() and path != process_lock}


def _reject_network(*_args: object, **_kwargs: object) -> None:
    raise AssertionError("Local-data compatibility checks must not send network requests.")


@pytest.fixture(params=("azure_openai", "github_copilot"))
def released_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest) -> Path:
    home = tmp_path / "home"
    user_root = home / ".fsq"
    workspace = tmp_path / "workspace"
    config_dir = workspace / ".fsq" / "config"
    config_dir.mkdir(parents=True)
    provider = {"type": request.param, "model": "fixture-model"}
    if request.param == "azure_openai":
        provider["base_url"] = "https://fixture.openai.azure.com/openai/v1/"
        _write_json(user_root / "auth" / "azure-openai.json", {"api_key": "fixture-api-key"})
    else:
        _write_json(user_root / "auth" / "github-copilot-token.json", {"access_token": "fixture-oauth-token", "expires_at": 4102444800})
        _write_json(user_root / "auth" / "github-copilot-provider-token.json", {"token": "fixture-provider-token", "expires_at": 4102444800, "plan": "individual"})
    (user_root / "config.yaml").write_text(
        yaml.safe_dump({"version": 3, "provider": provider, "workspaces": [{"name": "demo", "root_path": workspace.as_posix()}]}),
        encoding="utf-8",
    )
    (user_root / ".config.lock").write_bytes(b"\0")
    (config_dir / "config.android.yaml").write_text(
        yaml.safe_dump(
            {"version": 2, "name": "demo", "root_path": workspace.as_posix(), "platform": "android", "target": {"app_id": "com.example.demo"}, "env": {"ACCOUNT_PASSWORD": "fixture-secret"}}
        ),
        encoding="utf-8",
    )
    authored_files = {
        "cases/android/saved.fsq.yaml": "appId: com.example.demo\nname: saved\n---\n- waitMs: 1\n",
        "knowledge/android/project.md": "# Project\nUser note mentioning OpenAI Agents SDK.\n",
        "knowledge/android/custom.j2": "User template text: openai_agents.runner\n",
    }
    for relative_path, content in authored_files.items():
        path = workspace / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.setattr(httpx.Client, "send", _reject_network)
    monkeypatch.setattr(httpx.AsyncClient, "send", _reject_network)
    return workspace


def test_released_workspace_and_provider_load_without_data_migration(released_workspace: Path, tmp_path: Path) -> None:
    before = _snapshot(tmp_path)

    settings = load_workspace_platform_settings(released_workspace, "android")
    validate_provider_settings(settings)
    assert check_provider_readiness(settings)[0] is True
    refreshed = refresh_provider_settings(settings)

    assert settings.agent_runtime.model == refreshed.agent_runtime.model == "fixture-model"
    assert settings.agent_runtime.provider == refreshed.agent_runtime.provider
    assert settings.agent_runtime.max_turns == 100
    assert settings.harness.android.app_id == "com.example.demo"
    assert settings.runtime_secrets.resolve("ACCOUNT_PASSWORD") == "fixture-secret"
    assert settings.output.runs_dir == released_workspace / ".fsq" / "runs" / "android"
    assert _snapshot(tmp_path) == before


def _write_run(workspace: Path, run_id: str, status: str, *, historical: bool) -> Path:
    run_dir = workspace / ".fsq" / "runs" / "android" / run_id
    prefix = "openai_agents" if historical else "agent_runtime"
    error_label = "sdk_error" if historical else "agent_runtime_error"
    tool_label = "sdk_tool" if historical else "runtime_tool"
    artifact_path = f"artifacts/tools/000001-{tool_label}.json"
    summary = f"Recorded {status} result."
    final_output = {
        "schema_version": "task_run_v1",
        "status": status,
        "summary": summary,
        "pre_plan": [],
        "plan_updates": [],
        "satisfied_criteria": [],
        "unmet_criteria": [],
        "evidence": [artifact_path],
        "errors": [],
    }
    failure = {"failure_category": error_label, "failure_reason": error_label, "failure_summary": "Recorded runtime failure."}
    _write_json(
        run_dir / "run.json",
        {
            "schema_version": "fsq.run/v1",
            "run_id": run_id,
            "workspace": {"name": "demo"},
            "platform": "android",
            "mode": "explore",
            "status": status,
            "started_at": "2026-09-03T00:00:00Z",
            "completed_at": "2026-09-03T00:00:02Z",
            "duration_ms": 2000,
            "source": {"kind": "goal", "goal_summary": "Saved checkout run"},
            "result": {"summary": summary},
            "runtime": {"provider": "azure_openai", "model": "saved-model"},
            "artifacts": {"report": "report.json", "report_markdown": "report.md", "events": "events.jsonl", "evidence_manifest": "evidence-manifest.json"},
        },
    )
    runtime_steps = [
        {
            "step_id": 1,
            "source": f"{prefix}.runner",
            "status": "failed" if status == "failed" else "success",
            "outcome": summary,
            "error": "Recorded runtime failure." if status == "failed" else None,
            "duration_ms": 1000,
            "screenshot_path": None,
            "tool_output": failure if status == "failed" else final_output,
        },
        {"step_id": 2, "source": f"{prefix}.verifier", "status": "success", "outcome": summary, "error": None, "duration_ms": 1000, "screenshot_path": None, "tool_output": final_output},
    ]
    _write_json(
        run_dir / "report.json",
        {
            "task": {"id": "checkout", "description": "Saved checkout run"},
            "agent_output": None if status == "failed" else final_output,
            "execution": {"runtime_steps": runtime_steps, "tool_calls": []},
            "verification": {"verification_goal": "Checkout completes.", "status": status, "summary": summary},
            "failure_classification": "execution issue" if status == "failed" else status,
        },
    )
    (run_dir / "report.md").write_text(f"# Saved run\n{summary}\n{prefix}.runner\n{prefix}.verifier\n", encoding="utf-8")
    event = {
        "run_id": run_id,
        "task_id": "checkout",
        "sequence": 1,
        "timestamp": "2026-09-03T00:00:02Z",
        "type": "run_failed" if status == "failed" else "run_completed",
        "title": "SDK run failed" if historical and status == "failed" else "Run completed",
        "message": f"{prefix}.runner: {summary}",
        "payload": failure if status == "failed" else {"status": status, "report_path": str(run_dir / "report.md")},
    }
    (run_dir / "events.jsonl").write_text(json.dumps(event) + "\n", encoding="utf-8")
    _write_json(run_dir / artifact_path, {"tool_name": tool_label, "run_id": run_id, "call_index": 1, "content_chars": 13, "metadata": {"source": "model_input_filter"}, "content": "saved context"})
    screenshot = run_dir / "artifacts" / "screenshots" / "before.png"
    screenshot.parent.mkdir(parents=True)
    screenshot.write_bytes(base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jHgsAAAAASUVORK5CYII="))
    _write_json(run_dir / "evidence-manifest.json", {"artifacts": [{"kind": "screenshot", "path": "artifacts/screenshots/before.png"}]})
    return run_dir


@pytest.mark.parametrize("status", ["success", "failed", "inconclusive"])
def test_released_and_new_runs_coexist_without_rewriting_history(released_workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, status: str) -> None:
    old_run = _write_run(released_workspace, f"historical-{status}", status, historical=True)
    new_run = _write_run(released_workspace, f"current-{status}", status, historical=False)
    before = _snapshot(tmp_path)

    listed = list_runs(ListRunsRequest(current_directory=released_workspace))
    assert {run.run_id for run in listed.runs} == {old_run.name, new_run.name}
    settings = load_workspace_platform_settings(released_workspace, "android")
    for run_dir in (old_run, new_run):
        shown = show_run(ShowRunRequest(current_directory=released_workspace, run_id=run_dir.name))
        assert shown.run.status == status
        assert shown.run.runtime.model == "saved-model"
        assert shown.run.result.summary == f"Recorded {status} result."
        assert shown.run.artifacts.report == "report.json"
        assert resolve_report_path(run_dir.parent, run_dir.name, "json") == run_dir / "report.json"
        logs = read_run_logs(ReadRunLogsRequest(current_directory=released_workspace, run_id=run_dir.name))
        expected_prefix = "openai_agents" if run_dir == old_run else "agent_runtime"
        assert logs.events[0].message == f"{expected_prefix}.runner: Recorded {status} result."

    store = ToolArtifactStore(old_run.parent, old_run.name, settings.agent_runtime.local_tool_output)
    saved_path = "artifacts/tools/000001-sdk_tool.json"
    assert json.loads(store.read_text(saved_path))["tool_name"] == "sdk_tool"
    assert store.search(saved_path, "saved context", case_sensitive=True, max_matches=5, context_chars=20)["matches"]
    assert store.read_slice(saved_path, 0, 40)["content"] == store.read_text(saved_path)[:40]
    monkeypatch.chdir(released_workspace)
    command = CliRunner().invoke(main, ["--output", "json", "runs", "show", old_run.name])
    assert command.exit_code == 0, command.output
    assert old_run.name in command.output
    assert _snapshot(tmp_path) == before

    generated = generate_run_html(GenerateRunHtmlRequest(current_directory=released_workspace, run_id=old_run.name))
    html_path = released_workspace / generated.html_path
    document = html_path.read_text(encoding="utf-8")
    assert "openai_agents.runner" in document
    assert "artifacts/screenshots/before.png" in document
    after = _snapshot(tmp_path)
    assert after.pop(html_path.relative_to(tmp_path))
    assert after == before
