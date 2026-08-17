# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import platform
import subprocess
from pathlib import Path

from fsq_agent.config._settings import Settings
from fsq_agent.models import ConfigurationError


def _default_workspace_root(base_dir: Path) -> Path:
    return base_dir / ".fsq-agent-workspace"


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


def _ensure_workspace(settings: Settings, base_dir: Path) -> Path:
    root = settings.workspace.root_dir
    workspace_root = _default_workspace_root(base_dir) if root is None else _resolve_path(root, base_dir)
    marker_path = workspace_root / settings.workspace.marker_file

    if workspace_root.exists() and not workspace_root.is_dir():
        raise ConfigurationError("Workspace root must be a directory.", context={"workspace": str(workspace_root)})
    if workspace_root.exists() and marker_path.exists():
        settings.workspace.root_dir = workspace_root
        return workspace_root
    if workspace_root.exists() and any(workspace_root.iterdir()):
        raise ConfigurationError(
            "Workspace directory is not marked as an fsq-agent workspace.",
            context={"workspace": str(workspace_root), "marker_file": settings.workspace.marker_file},
        )
    if not settings.workspace.auto_init:
        raise ConfigurationError(
            "Workspace directory is not initialized.",
            context={"workspace": str(workspace_root), "marker_file": settings.workspace.marker_file},
        )

    workspace_root.mkdir(parents=True, exist_ok=True)
    marker_path.write_text("fsq-agent workspace\n", encoding="utf-8")
    _set_hidden_best_effort(marker_path)
    settings.workspace.root_dir = workspace_root
    return workspace_root


def _ensure_inside(path: Path, root: Path, message: str) -> None:
    if root not in (path, *path.parents):
        raise ConfigurationError(message, context={"path": str(path), "root": str(root)})


def resolve_runtime_paths(settings: Settings, base_dir: Path | None = None) -> None:
    config_base = (base_dir or Path.cwd()).expanduser().resolve()
    configured_workspace = settings.workspace.root_dir
    project_base = _resolve_path(configured_workspace, config_base) if configured_workspace is not None and (_resolve_path(configured_workspace, config_base) / "fsq.yaml").is_file() else config_base
    project_config = project_base / "fsq.yaml"
    if project_config.is_file():
        from fsq_agent.config._workspace_init import load_project_layout

        layout = load_project_layout(project_base)
        platform_id = settings.harness.platform
        if platform_id not in layout.platforms:
            raise ConfigurationError(
                "Requested platform is not enabled in this FSQ project.",
                context={"platform": platform_id, "enabled_platforms": list(layout.platforms)},
            )
        settings.workspace.root_dir = layout.workspace
        settings.cases.dir = layout.case_directories[platform_id]
        settings.output.root_dir = layout.run_directories[platform_id]
        settings.output.runs_dir = layout.run_directories[platform_id]
        knowledge = settings.agent_context.knowledge
        knowledge.root_dir = _resolve_path(knowledge.root_dir, project_base)
        knowledge.skills.dir = _resolve_path(knowledge.skills.dir, knowledge.root_dir)
        if knowledge.pre_plan.dir is not None:
            knowledge.pre_plan.dir = _resolve_path(knowledge.pre_plan.dir, knowledge.root_dir)
        prompt = settings.openai_agents.prompt
        if prompt.agent_template_path is not None:
            prompt.agent_template_path = _resolve_path(prompt.agent_template_path, project_base)
        if prompt.task_template_path is not None:
            prompt.task_template_path = _resolve_path(prompt.task_template_path, project_base)
        return
    workspace_root = _ensure_workspace(settings, config_base)

    output_root = _resolve_path(settings.output.root_dir, workspace_root)
    _ensure_inside(output_root, workspace_root, "Output root must be inside the fsq-agent workspace.")
    output_root.mkdir(parents=True, exist_ok=True)
    settings.output.root_dir = output_root

    runs_dir = settings.output.runs_dir
    settings.output.runs_dir = (output_root / runs_dir).resolve() if not runs_dir.is_absolute() else runs_dir.expanduser().resolve()
    _ensure_inside(settings.output.runs_dir, output_root, "Output runs directory must be inside output root.")
    settings.output.runs_dir.mkdir(parents=True, exist_ok=True)

    settings.cases.dir = _resolve_path(settings.cases.dir, config_base)
    knowledge = settings.agent_context.knowledge
    knowledge.root_dir = _resolve_path(knowledge.root_dir, config_base)
    knowledge.skills.dir = _resolve_path(knowledge.skills.dir, knowledge.root_dir)
    if knowledge.pre_plan.dir is not None:
        knowledge.pre_plan.dir = _resolve_path(knowledge.pre_plan.dir, knowledge.root_dir)

    prompt = settings.openai_agents.prompt
    if prompt.agent_template_path is not None:
        prompt.agent_template_path = _resolve_path(prompt.agent_template_path, config_base)
    if prompt.task_template_path is not None:
        prompt.task_template_path = _resolve_path(prompt.task_template_path, config_base)
