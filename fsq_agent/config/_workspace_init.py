# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import yaml

SUPPORTED_PLATFORMS = ("android", "web", "windows", "macos")


class WorkspaceInitError(Exception):
    """A stable, presentation-neutral Workspace operation error."""

    def __init__(self, code: str, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}


@dataclass(frozen=True)
class ProjectLayout:
    root: Path
    project_id: str
    platforms: tuple[str, ...]
    workspace: Path
    cases: Path
    runs: Path
    cache: Path
    temp: Path
    environments: Path
    case_directories: dict[str, Path]
    run_directories: dict[str, Path]


@dataclass(frozen=True)
class WorkspaceInitResult:
    status: str
    project_id: str
    requested_platforms: list[str]
    added_platforms: list[str] = field(default_factory=list)
    case_directories: dict[str, Path] = field(default_factory=dict)
    run_directories: dict[str, Path] = field(default_factory=dict)
    project_root: Path | None = None
    workspace: Path | None = None
    created_paths: list[Path] = field(default_factory=list)
    warnings: list[str] = field(default_factory=lambda: ["workspace.gitignore_recommended"])


def _fail(code: str, message: str, **details: Any) -> WorkspaceInitError:
    return WorkspaceInitError(code, message, details=details)


def _read_yaml(path: Path) -> dict[str, Any]:
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise _fail("workspace.project_config_invalid", f"Cannot read valid YAML from {path.name}.", path=str(path)) from exc
    if not isinstance(value, dict):
        raise _fail("workspace.project_config_invalid", f"{path.name} must contain a YAML mapping.", path=str(path))
    return value


def _relative_path(root: Path, value: Any, field_name: str) -> Path:
    if not isinstance(value, str) or not value or Path(value).is_absolute():
        raise _fail("workspace.path_outside_project", f"{field_name} must be a non-empty relative path.", field=field_name)
    candidate = root / value
    resolved = candidate.resolve(strict=False)
    if root != resolved and root not in resolved.parents:
        raise _fail("workspace.path_outside_project", f"{field_name} must remain inside the project.", field=field_name)
    _reject_symlink_components(candidate, root)
    return resolved


def _reject_symlink_components(path: Path, stop: Path) -> None:
    current = path
    while current != stop:
        if current.is_symlink():
            raise _fail("workspace.symlink_not_allowed", "Managed Workspace paths cannot contain symbolic links.", path=str(current))
        if current.parent == current:
            break
        current = current.parent


def _normalize_platforms(platforms: list[str] | tuple[str, ...]) -> list[str]:
    requested = set(platforms)
    invalid = sorted(requested.difference(SUPPORTED_PLATFORMS))
    if invalid:
        raise _fail("workspace.platform_invalid", "One or more platforms are unsupported.", platforms=invalid)
    if not requested:
        raise _fail("workspace.platform_invalid", "At least one platform is required.")
    return [item for item in SUPPORTED_PLATFORMS if item in requested]


def _validate_keys(value: dict[str, Any], allowed: set[str], source: str) -> None:
    unknown = sorted(set(value).difference(allowed))
    if unknown:
        raise _fail("workspace.project_config_invalid", f"{source} contains unknown fields.", fields=unknown)


def _load_project(root: Path) -> tuple[dict[str, Any], list[str]]:
    project = _read_yaml(root / "fsq.yaml")
    _validate_keys(project, {"schemaVersion", "projectId", "paths", "platforms"}, "fsq.yaml")
    if project.get("schemaVersion") != "fsq.project/v1":
        raise _fail("workspace.project_config_invalid", "Unsupported fsq.yaml schemaVersion.")
    try:
        UUID(str(project["projectId"]), version=4)
    except (KeyError, ValueError, TypeError) as exc:
        raise _fail("workspace.project_config_invalid", "projectId must be a UUID v4.") from exc
    paths = project.get("paths")
    platforms = project.get("platforms")
    if not isinstance(paths, dict) or not isinstance(platforms, dict):
        raise _fail("workspace.project_config_invalid", "paths and platforms must be mappings.")
    _validate_keys(paths, {"workspace", "cases", "runs", "cache", "temp", "environments"}, "paths")
    ordered = _normalize_platforms(list(platforms))
    for name, overrides in platforms.items():
        if not isinstance(overrides, dict):
            raise _fail("workspace.project_config_invalid", f"Platform {name} must be a mapping.")
        _validate_keys(overrides, {"casesDir", "runsDir"}, f"platforms.{name}")
    return project, ordered


def _layout(root: Path, project: dict[str, Any], platforms: list[str]) -> ProjectLayout:
    paths = project["paths"]
    required = {"workspace", "cases", "runs", "cache", "temp", "environments"}
    if set(paths) != required:
        raise _fail("workspace.project_config_invalid", "paths must define every required Workspace path.")
    resolved = {name: _relative_path(root, paths[name], f"paths.{name}") for name in required}
    external_runs = os.environ.get("FSQ_RUNS_DIR")
    runs_root = Path(external_runs).expanduser().resolve(strict=False) if external_runs else resolved["runs"]
    if external_runs:
        _reject_symlink_components(runs_root, Path(runs_root.anchor))
    case_dirs: dict[str, Path] = {}
    run_dirs: dict[str, Path] = {}
    for platform in platforms:
        overrides = project["platforms"][platform]
        case_dirs[platform] = _relative_path(root, overrides["casesDir"], f"platforms.{platform}.casesDir") if "casesDir" in overrides else resolved["cases"] / platform
        run_dirs[platform] = _relative_path(root, overrides["runsDir"], f"platforms.{platform}.runsDir") if "runsDir" in overrides else runs_root / platform
    return ProjectLayout(
        root=root,
        project_id=str(project["projectId"]),
        platforms=tuple(platforms),
        workspace=resolved["workspace"],
        cases=resolved["cases"],
        runs=runs_root,
        cache=resolved["cache"],
        temp=resolved["temp"],
        environments=resolved["environments"],
        case_directories=case_dirs,
        run_directories=run_dirs,
    )


def load_project_layout(directory: str | Path) -> ProjectLayout:
    root = Path(directory).expanduser().resolve()
    if not (root / "fsq.yaml").is_file():
        raise _fail("workspace.not_initialized", "No fsq.yaml was found in this directory.", path=str(root))
    project, platforms = _load_project(root)
    layout = _layout(root, project, platforms)
    metadata_path = layout.workspace / "workspace.yaml"
    if not metadata_path.is_file():
        raise _fail("workspace.layout_incomplete", "The initialized Workspace layout is incomplete.")
    metadata = _read_yaml(metadata_path)
    if metadata != {"schemaVersion": "fsq.workspace/v1", "layoutVersion": 1}:
        raise _fail("workspace.metadata_invalid", "workspace.yaml has an invalid schema.")
    required = [layout.workspace, layout.cache, layout.temp, layout.environments, *layout.case_directories.values(), *layout.run_directories.values()]
    if any(not path.exists() for path in required):
        raise _fail("workspace.layout_incomplete", "The initialized Workspace layout is incomplete.")
    return layout


def _dump(path: Path, value: dict[str, Any]) -> None:
    content = yaml.safe_dump(value, sort_keys=False)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        if yaml.safe_load(temporary.read_text(encoding="utf-8")) != value:
            raise _fail("workspace.write_failed", "Temporary Workspace configuration validation failed.")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def initialize_project(directory: str | Path, platforms: list[str] | tuple[str, ...]) -> WorkspaceInitResult:
    root = Path(directory).expanduser().resolve()
    requested = _normalize_platforms(platforms)
    legacy = root / ".fsq-agent-workspace"
    if legacy.exists():
        raise _fail("workspace.legacy_layout_detected", "Legacy .fsq-agent-workspace layout detected.", path=str(legacy))
    config_path = root / "fsq.yaml"
    existing = config_path.exists()
    if existing and not config_path.is_file():
        raise _fail("workspace.path_conflict", "fsq.yaml is not a regular file.", path=str(config_path))
    if existing:
        project, current = _load_project(root)
    else:
        current = []
        project = {
            "schemaVersion": "fsq.project/v1",
            "projectId": str(uuid4()),
            "paths": {
                "workspace": ".fsq-workspace",
                "cases": "fsq-cases",
                "runs": ".fsq-workspace/runs",
                "cache": ".fsq-workspace/cache",
                "temp": ".fsq-workspace/tmp",
                "environments": ".fsq-workspace/envs.yaml",
            },
            "platforms": {},
        }
    added = [name for name in requested if name not in current]
    all_platforms = [name for name in SUPPORTED_PLATFORMS if name in set(current + requested)]
    project["platforms"] = {name: project["platforms"].get(name, {}) for name in all_platforms}
    layout = _layout(root, project, all_platforms)
    workspace_yaml = layout.workspace / "workspace.yaml"
    envs_yaml = layout.environments
    envs = _read_yaml(envs_yaml) if envs_yaml.exists() else {"schemaVersion": "fsq.environments/v1", "environments": {}}
    _validate_keys(envs, {"schemaVersion", "environments"}, "envs.yaml")
    if envs.get("schemaVersion") != "fsq.environments/v1" or not isinstance(envs.get("environments"), dict):
        raise _fail("workspace.environments_invalid", "envs.yaml has an invalid schema.")
    for name in added:
        key = f"local-{name}"
        expected = {"platform": name, "environmentProvider": "local"}
        if key in envs["environments"] and envs["environments"][key] != expected:
            raise _fail("workspace.environments_invalid", f"Environment {key} conflicts with the generated profile.")
        envs["environments"][key] = expected
    files = [config_path, workspace_yaml, envs_yaml]
    directories = [layout.workspace, layout.cases, layout.cache, layout.temp, layout.runs, *layout.case_directories.values(), *layout.run_directories.values()]
    for path in directories:
        _reject_symlink_components(path, root if root == path or root in path.parents else Path(path.anchor))
        if path.exists() and not path.is_dir():
            raise _fail("workspace.path_conflict", "A managed directory path is occupied by a file.", path=str(path))
    for path in files:
        if path.exists() and not path.is_file():
            raise _fail("workspace.path_conflict", "A managed file path is occupied by a directory.", path=str(path))
    if existing and not added:
        load_project_layout(root)
        return WorkspaceInitResult(
            "already_initialized",
            layout.project_id,
            requested,
            run_directories=layout.run_directories,
            case_directories=layout.case_directories,
            project_root=root,
            workspace=layout.workspace,
        )
    created_dirs: list[Path] = []
    created_files: list[Path] = []
    backups: dict[Path, bytes] = {}
    try:
        for path in directories:
            if not path.exists():
                path.mkdir(parents=True, exist_ok=True)
                created_dirs.append(path)
        for path, payload in ((config_path, project), (workspace_yaml, {"schemaVersion": "fsq.workspace/v1", "layoutVersion": 1}), (envs_yaml, envs)):
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.exists():
                backups[path] = path.read_bytes()
            else:
                created_files.append(path)
            _dump(path, payload)
    except Exception:
        for path, content in backups.items():
            path.write_bytes(content)
        for path in created_files:
            path.unlink(missing_ok=True)
        for path in sorted(created_dirs, key=lambda item: len(item.parts), reverse=True):
            if path.exists() and not any(path.iterdir()):
                path.rmdir()
        raise
    status = "initialized" if not existing else "platforms_added"
    return WorkspaceInitResult(
        status,
        layout.project_id,
        requested,
        added,
        layout.case_directories,
        layout.run_directories,
        root,
        layout.workspace,
        [*created_dirs, *created_files],
    )
