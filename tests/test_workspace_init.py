# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from pathlib import Path
from uuid import UUID

import pytest
import yaml

from fsq_agent.config import WorkspaceInitError, initialize_project, load_project_layout


def test_initialize_project_creates_multiplatform_layout_in_fixed_order(tmp_path: Path) -> None:
    result = initialize_project(tmp_path, ["web", "android", "web"])

    assert result.status == "initialized"
    assert result.requested_platforms == ["android", "web"]
    UUID(result.project_id, version=4)
    project = yaml.safe_load((tmp_path / "fsq.yaml").read_text(encoding="utf-8"))
    assert list(project["platforms"]) == ["android", "web"]
    assert project["projectId"] == result.project_id
    assert yaml.safe_load((tmp_path / ".fsq-workspace" / "workspace.yaml").read_text(encoding="utf-8")) == {
        "schemaVersion": "fsq.workspace/v1",
        "layoutVersion": 1,
    }
    environments = yaml.safe_load((tmp_path / ".fsq-workspace" / "envs.yaml").read_text(encoding="utf-8"))
    assert list(environments["environments"]) == ["local-android", "local-web"]
    for path in (
        tmp_path / "fsq-cases" / "android",
        tmp_path / "fsq-cases" / "web",
        tmp_path / ".fsq-workspace" / "runs" / "android",
        tmp_path / ".fsq-workspace" / "runs" / "web",
        tmp_path / ".fsq-workspace" / "cache",
        tmp_path / ".fsq-workspace" / "tmp",
    ):
        assert path.is_dir()


def test_initialize_project_is_idempotent_and_adds_platform(tmp_path: Path) -> None:
    first = initialize_project(tmp_path, ["web"])
    unchanged = initialize_project(tmp_path, ["web"])
    added = initialize_project(tmp_path, ["android"])

    assert unchanged.status == "already_initialized"
    assert added.status == "platforms_added"
    assert added.added_platforms == ["android"]
    assert added.project_id == first.project_id
    project = yaml.safe_load((tmp_path / "fsq.yaml").read_text(encoding="utf-8"))
    assert list(project["platforms"]) == ["android", "web"]


def test_initialize_project_preserves_custom_environment(tmp_path: Path) -> None:
    initialize_project(tmp_path, ["web"])
    env_path = tmp_path / ".fsq-workspace" / "envs.yaml"
    payload = yaml.safe_load(env_path.read_text(encoding="utf-8"))
    payload["environments"]["macos-tart"] = {"platform": "macos", "environmentProvider": "tart"}
    env_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    initialize_project(tmp_path, ["android"])

    environments = yaml.safe_load(env_path.read_text(encoding="utf-8"))["environments"]
    assert environments["macos-tart"]["environmentProvider"] == "tart"


def test_initialize_project_rejects_legacy_layout_without_writes(tmp_path: Path) -> None:
    (tmp_path / ".fsq-agent-workspace").mkdir()

    with pytest.raises(WorkspaceInitError) as error:
        initialize_project(tmp_path, ["web"])

    assert error.value.code == "workspace.legacy_layout_detected"
    assert not (tmp_path / "fsq.yaml").exists()


def test_initialize_project_rejects_conflict_before_writes(tmp_path: Path) -> None:
    (tmp_path / "fsq-cases").write_text("occupied", encoding="utf-8")

    with pytest.raises(WorkspaceInitError) as error:
        initialize_project(tmp_path, ["web"])

    assert error.value.code == "workspace.path_conflict"
    assert not (tmp_path / "fsq.yaml").exists()
    assert not (tmp_path / ".fsq-workspace").exists()


def test_initialize_project_uses_external_runs_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    runs_root = tmp_path.parent / f"{tmp_path.name}-runs"
    monkeypatch.setenv("FSQ_RUNS_DIR", str(runs_root))

    result = initialize_project(tmp_path, ["web"])

    assert result.run_directories["web"] == runs_root / "web"
    assert (runs_root / "web").is_dir()


def test_load_project_layout_rejects_missing_platform_directory(tmp_path: Path) -> None:
    initialize_project(tmp_path, ["web"])
    (tmp_path / "fsq-cases" / "web").rmdir()

    with pytest.raises(WorkspaceInitError) as error:
        load_project_layout(tmp_path)

    assert error.value.code == "workspace.layout_incomplete"


@pytest.mark.parametrize("configured", ["/outside", "../outside"])
def test_initialize_project_rejects_unsafe_yaml_paths(tmp_path: Path, configured: str) -> None:
    initialize_project(tmp_path, ["web"])
    project_path = tmp_path / "fsq.yaml"
    project = yaml.safe_load(project_path.read_text(encoding="utf-8"))
    project["paths"]["cases"] = configured
    project_path.write_text(yaml.safe_dump(project, sort_keys=False), encoding="utf-8")

    with pytest.raises(WorkspaceInitError) as error:
        initialize_project(tmp_path, ["android"])

    assert error.value.code == "workspace.path_outside_project"


def test_platform_run_override_takes_precedence_over_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    initialize_project(tmp_path, ["web"])
    project_path = tmp_path / "fsq.yaml"
    project = yaml.safe_load(project_path.read_text(encoding="utf-8"))
    project["platforms"]["web"]["runsDir"] = "artifacts/web"
    project_path.write_text(yaml.safe_dump(project, sort_keys=False), encoding="utf-8")
    monkeypatch.setenv("FSQ_RUNS_DIR", str(tmp_path.parent / "external-runs"))
    (tmp_path / "artifacts" / "web").mkdir(parents=True)

    layout = load_project_layout(tmp_path)

    assert layout.run_directories["web"] == tmp_path / "artifacts" / "web"


def test_initialize_project_rejects_conflicting_generated_environment(tmp_path: Path) -> None:
    initialize_project(tmp_path, ["web"])
    env_path = tmp_path / ".fsq-workspace" / "envs.yaml"
    payload = yaml.safe_load(env_path.read_text(encoding="utf-8"))
    payload["environments"]["local-android"] = {"platform": "android", "environmentProvider": "cloud"}
    env_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    with pytest.raises(WorkspaceInitError) as error:
        initialize_project(tmp_path, ["android"])

    assert error.value.code == "workspace.environments_invalid"
    assert "android" not in yaml.safe_load((tmp_path / "fsq.yaml").read_text(encoding="utf-8"))["platforms"]
