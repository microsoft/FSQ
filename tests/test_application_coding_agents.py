# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import tomllib
from pathlib import Path

import pytest

from fsq_agent.application import ApplicationError, CodingAgentInstallRequest, install_coding_agent

ROOT = Path(__file__).resolve().parents[1]


def _install(project: Path, *, workspace: Path | None = None, no_overwrite: bool = False):
    return install_coding_agent(
        CodingAgentInstallRequest(
            project_directory=project,
            agent="codex",
            workspace_root=workspace or project / "workspace",
            no_overwrite=no_overwrite,
        )
    )


def test_install_codex_agents_creates_missing_agents_md_without_path_rendering(tmp_path: Path) -> None:
    project = tmp_path / "project"
    workspace = tmp_path / "actual-workspace"
    project.mkdir()

    result = _install(project, workspace=workspace)

    assert [item.status for item in result.files] == ["created", "created", "created"]
    for filename, expected_name in (
        ("fsq_environment_setup.toml", "fsq_environment_setup"),
        ("fsq_test_runner.toml", "fsq_test_runner"),
    ):
        path = project / ".codex" / "agents" / filename
        source = ROOT / "fsq_agent" / "resources" / "codex" / "agents" / filename
        assert path.read_bytes() == source.read_bytes()
        definition = tomllib.loads(path.read_text(encoding="utf-8"))
        assert definition["name"] == expected_name
        assert definition["sandbox_mode"] == "workspace-write"
    workflow = (project / "AGENTS.md").read_text(encoding="utf-8")
    source_workflow = (ROOT / "fsq_agent/resources/codex/AGENTS.md").read_text(encoding="utf-8")
    assert workflow.count("<!-- BEGIN FSQ CODEX WORKFLOW -->") == 1
    assert workflow.count("<!-- END FSQ CODEX WORKFLOW -->") == 1
    assert source_workflow in workflow
    assert str(project.resolve()) not in workflow
    assert str(workspace.resolve()) not in workflow
    assert result.files[-1].backup_path is None


def test_install_codex_agents_is_idempotent(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    _install(project)
    unchanged = _install(project, workspace=project / "other-workspace")

    assert [item.status for item in unchanged.files] == ["unchanged", "unchanged", "skipped"]


def test_install_codex_agents_no_overwrite_skips_existing_targets(tmp_path: Path) -> None:
    project = tmp_path / "project"
    agents_dir = project / ".codex" / "agents"
    agents_dir.mkdir(parents=True)
    existing = agents_dir / "fsq_test_runner.toml"
    existing.write_text("keep", encoding="utf-8")

    result = _install(project, no_overwrite=True)

    statuses = {item.path.name: item.status for item in result.files}
    assert statuses["fsq_test_runner.toml"] == "skipped"
    assert existing.read_text(encoding="utf-8") == "keep"
    assert statuses["fsq_environment_setup.toml"] == "created"
    assert statuses["AGENTS.md"] == "created"


@pytest.mark.parametrize("entry_kind", ["file", "directory", "symlink", "broken_symlink"])
def test_install_codex_agents_skips_any_existing_agents_md_entry(tmp_path: Path, entry_kind: str) -> None:
    project = tmp_path / "project"
    project.mkdir()
    agents_md = project / "AGENTS.md"
    if entry_kind == "file":
        agents_md.write_text("<!-- BEGIN FSQ CODEX WORKFLOW -->\nmalformed", encoding="utf-8")
    elif entry_kind == "directory":
        agents_md.mkdir()
    elif entry_kind == "symlink":
        target = project / "actual-agents.md"
        target.write_text("keep", encoding="utf-8")
        agents_md.symlink_to(target)
    else:
        agents_md.symlink_to(project / "missing-agents.md")

    result = _install(project)

    assert [item.status for item in result.files] == ["created", "created", "skipped"]
    assert agents_md.is_symlink() or agents_md.exists()
    assert result.files[-1].backup_path is None


def test_install_codex_agents_rejects_symlinked_target_directory(tmp_path: Path) -> None:
    project = tmp_path / "project"
    outside = tmp_path / "outside"
    project.mkdir()
    outside.mkdir()
    (project / ".codex").symlink_to(outside, target_is_directory=True)

    with pytest.raises(ApplicationError) as error:
        _install(project)

    assert error.value.code.value == "configuration.invalid"
    assert list(outside.iterdir()) == []


def test_packaged_codex_resources_may_retain_placeholders() -> None:
    resources = ROOT / "fsq_agent/resources/codex"
    assert "{{FSQ_WORKSPACE}}" in (resources / "AGENTS.md").read_text(encoding="utf-8")
    assert "{{FSQ_WORKSPACE}}" in (resources / "agents/fsq_environment_setup.toml").read_text(encoding="utf-8")
