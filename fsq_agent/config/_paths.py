# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import platform
import subprocess
from pathlib import Path

from fsq_agent.config._settings import Settings
from fsq_agent.models import ConfigurationError


def _resolve_path(path: Path, base_dir: Path) -> Path:
    expanded = path.expanduser()
    if expanded.is_absolute():
        return expanded.resolve()
    return (base_dir / expanded).resolve()


def _set_hidden_best_effort(path: Path) -> None:
    if platform.system() != "Windows":
        return
    try:
        # The Windows utility and flags are fixed; only the workspace marker path varies.
        subprocess.run(  # noqa: S603
            ["attrib", "+h", str(path)],  # noqa: S607
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError:
        return


def _ensure_inside(path: Path, root: Path, message: str) -> None:
    if root not in (path, *path.parents):
        raise ConfigurationError(message, context={"path": str(path), "root": str(root)})


def resolve_runtime_paths(settings: Settings, base_dir: Path | None = None) -> None:
    config_base = (base_dir or Path.cwd()).expanduser().resolve()
    root = settings.workspace.root_dir
    workspace_root = (config_base / ".fsq-agent-workspace").resolve() if root is None else _resolve_path(root, config_base)
    if workspace_root.exists() and not workspace_root.is_dir():
        raise ConfigurationError("Workspace root must be a directory.", context={"workspace": str(workspace_root)})
    settings.workspace.root_dir = workspace_root

    output_root = _resolve_path(settings.output.root_dir, workspace_root)
    _ensure_inside(output_root, workspace_root, "Output root must be inside the fsq-agent workspace.")
    settings.output.root_dir = output_root

    runs_dir = settings.output.runs_dir
    settings.output.runs_dir = (output_root / runs_dir).resolve() if not runs_dir.is_absolute() else runs_dir.expanduser().resolve()
    _ensure_inside(settings.output.runs_dir, output_root, "Output runs directory must be inside output root.")

    settings.cases.dir = _resolve_path(settings.cases.dir, workspace_root)
    knowledge = settings.agent_context.knowledge
    knowledge.root_dir = _resolve_path(knowledge.root_dir, config_base)
    knowledge.skills.dir = _resolve_path(knowledge.skills.dir, knowledge.root_dir)
    if knowledge.pre_plan.dir is not None:
        knowledge.pre_plan.dir = _resolve_path(knowledge.pre_plan.dir, knowledge.root_dir)

    prompt = settings.agent_runtime.prompt
    if prompt.agent_template_path is not None:
        prompt.agent_template_path = _resolve_path(prompt.agent_template_path, config_base)
    if prompt.task_template_path is not None:
        prompt.task_template_path = _resolve_path(prompt.task_template_path, config_base)


def resolve_workspace_runtime_paths(settings: Settings, workspace_root: Path, preset_base: Path, platform: str) -> None:
    workspace_root = workspace_root.expanduser().resolve()
    settings.workspace.root_dir = workspace_root
    settings.cases.dir = workspace_root / "cases" / platform
    settings.output.root_dir = workspace_root / ".fsq" / "runs" / platform
    settings.output.runs_dir = workspace_root / ".fsq" / "runs" / platform

    knowledge = settings.agent_context.knowledge
    knowledge.root_dir = workspace_root / "knowledge" / platform
    knowledge.skills.dir = _resolve_path(knowledge.skills.dir, preset_base)
    if knowledge.pre_plan.dir is not None:
        knowledge.pre_plan.dir = _resolve_path(knowledge.pre_plan.dir, knowledge.root_dir)

    prompt = settings.agent_runtime.prompt
    if prompt.agent_template_path is not None:
        prompt.agent_template_path = _resolve_path(prompt.agent_template_path, preset_base)
    if prompt.task_template_path is not None:
        prompt.task_template_path = _resolve_path(prompt.task_template_path, preset_base)
