# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import os
import tempfile
import tomllib
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from uuid import uuid4

from fsq_agent.application.contracts import (
    ApplicationError,
    ApplicationErrorCategory,
    ApplicationErrorCode,
    CodingAgentInstallFileResult,
    CodingAgentInstallRequest,
    CodingAgentInstallResult,
)

BEGIN_MARKER = "<!-- BEGIN FSQ CODEX WORKFLOW -->"
END_MARKER = "<!-- END FSQ CODEX WORKFLOW -->"
AGENT_FILES = ("fsq_environment_setup.toml", "fsq_test_runner.toml")


@dataclass(frozen=True)
class _PreparedFile:
    path: Path
    content: bytes
    original: bytes | None
    skipped: bool = False


def install_codex(request: CodingAgentInstallRequest) -> CodingAgentInstallResult:
    try:
        project = _resolve_project(request.project_directory)
        workspace = request.workspace_root.expanduser().resolve(strict=False)
        agents_dir = project / ".codex" / "agents"
        _validate_targets(project, agents_dir)
        templates = _load_templates()
        prepared = _prepare_files(project, agents_dir, templates, request.no_overwrite)
        _validate_agent_definitions(prepared)
        agents_dir.mkdir(parents=True, exist_ok=True)
        results = tuple(_install_file(item) for item in prepared)
        return CodingAgentInstallResult(agent="codex", project_directory=project, workspace_root=workspace, files=results)
    except ApplicationError:
        raise
    except (OSError, ValueError, tomllib.TOMLDecodeError, KeyError, TypeError, AssertionError) as exc:
        raise ApplicationError(
            code=ApplicationErrorCode.CONFIGURATION_INVALID,
            category=ApplicationErrorCategory.CONFIGURATION,
            message=str(exc).splitlines()[0] or "Codex agent installation failed.",
            action="Review the Codex project targets and retry.",
        ) from exc


def _resolve_project(path: Path) -> Path:
    project = path.expanduser().resolve(strict=True)
    if not project.is_dir():
        raise ValueError("Codex project directory must be an existing directory.")
    return project


def _resource_directory():
    packaged = files("fsq_agent").joinpath("resources", "codex")
    if packaged.joinpath("AGENTS.md").is_file():
        return packaged
    raise ValueError("Packaged Codex agent resources are unavailable.")


def _load_templates() -> dict[str, bytes]:
    root = _resource_directory()
    names = (*AGENT_FILES, "AGENTS.md")
    templates = {name: root.joinpath("agents", name).read_bytes() if name in AGENT_FILES else root.joinpath(name).read_bytes() for name in names}
    workflow = templates["AGENTS.md"].decode("utf-8")
    if BEGIN_MARKER in workflow or END_MARKER in workflow:
        raise ValueError("Codex workflow template must not contain managed markers.")
    return templates


def _prepare_files(project: Path, agents_dir: Path, templates: dict[str, bytes], no_overwrite: bool) -> tuple[_PreparedFile, ...]:
    prepared: list[_PreparedFile] = []
    for name in AGENT_FILES:
        destination = agents_dir / name
        original = destination.read_bytes() if destination.exists() else None
        prepared.append(_PreparedFile(destination, original or b"" if no_overwrite and original is not None else templates[name], original, no_overwrite and original is not None))
    destination = project / "AGENTS.md"
    if os.path.lexists(destination):
        prepared.append(_PreparedFile(destination, b"", None, True))
    else:
        prepared.append(_PreparedFile(destination, _managed_workflow(templates["AGENTS.md"]), None))
    return tuple(prepared)


def _managed_workflow(workflow: bytes) -> bytes:
    template = workflow.decode("utf-8")
    block = f"{BEGIN_MARKER}\n{template}"
    if not block.endswith("\n"):
        block += "\n"
    block += f"{END_MARKER}\n"
    return block.encode("utf-8")


def _validate_targets(project: Path, agents_dir: Path) -> None:
    for directory in (project / ".codex", agents_dir):
        if directory.is_symlink():
            raise ValueError(f"Refusing a symlinked target directory: {directory}")
        if directory.exists() and not directory.is_dir():
            raise ValueError(f"Target directory is not a directory: {directory}")
    for destination in (agents_dir / name for name in AGENT_FILES):
        if destination.is_symlink():
            raise ValueError(f"Refusing a symlinked target file: {destination}")
        if destination.exists() and not destination.is_file():
            raise ValueError(f"Target is not a regular file: {destination}")


def _validate_agent_definitions(prepared: tuple[_PreparedFile, ...]) -> None:
    expected = dict(zip(AGENT_FILES, ("fsq_environment_setup", "fsq_test_runner"), strict=True))
    for item in prepared[:2]:
        if item.skipped:
            continue
        data = tomllib.loads(item.content.decode("utf-8"))
        name = expected[item.path.name]
        if data.get("name") != name or data.get("sandbox_mode") != "workspace-write" or not str(data.get("description", "")).strip() or not str(data.get("developer_instructions", "")).strip():
            raise ValueError(f"Invalid packaged Codex agent definition: {item.path.name}")


def _install_file(item: _PreparedFile) -> CodingAgentInstallFileResult:
    if item.skipped:
        return CodingAgentInstallFileResult(path=item.path, status="skipped")
    current = item.path.read_bytes() if item.path.exists() else None
    if current != item.original:
        raise ValueError(f"Target changed during preparation: {item.path}")
    if current == item.content:
        return CodingAgentInstallFileResult(path=item.path, status="unchanged")
    backup = None
    if current is not None:
        backup = item.path.with_name(f"{item.path.name}.bak.{uuid4().hex}")
        backup.write_bytes(current)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{item.path.name}.", dir=item.path.parent)
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as temporary:
            temporary.write(item.content)
            temporary.flush()
            os.fsync(temporary.fileno())
        temporary_path.replace(item.path)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()
    return CodingAgentInstallFileResult(path=item.path, status="updated" if current is not None else "created", backup_path=backup)
