# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from __future__ import annotations

import re
import tomllib
from pathlib import Path
from typing import Any

import yaml
from packaging.version import Version

ROOT = Path(__file__).resolve().parents[1]
EXACT_REQUIREMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*(?:\[[A-Za-z0-9._,-]+\])?==[^;\s]+(?:\s*;.*)?$")


def test_python_dependencies_are_lock_free_public_and_exactly_versioned() -> None:
    pyproject_path = ROOT / "pyproject.toml"
    pyproject_text = pyproject_path.read_text(encoding="utf-8")
    project = tomllib.loads(pyproject_text)

    ignored_paths = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert "uv.lock" in ignored_paths
    assert "tool" not in project or "uv" not in project["tool"]
    assert "packagefeedproxy.microsoft.io" not in pyproject_text
    assert all(EXACT_REQUIREMENT.fullmatch(requirement) for requirement in project["build-system"]["requires"])
    assert all(EXACT_REQUIREMENT.fullmatch(requirement) for requirement in project["project"]["dependencies"])
    assert all(EXACT_REQUIREMENT.fullmatch(requirement) for requirements in project["project"]["optional-dependencies"].values() for requirement in requirements)


def test_default_distribution_contract() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    metadata = project["project"]

    assert metadata["name"] == "fsq-agent"
    version = Version(metadata["version"])
    assert version.local is None
    dependencies = metadata["dependencies"]
    for name in ("uiautomator2", "playwright", "pywinauto", "pillow", "Appium-Python-Client"):
        assert any(dependency.startswith(f"{name}==") for dependency in dependencies)
    assert set(metadata["optional-dependencies"]) == {"dev"}
    assert metadata["scripts"] == {
        "fsq": "fsq_agent.adapters.cli:main",
        "fsq-agent": "fsq_agent.adapters.cli:main",
    }
    assert metadata["license"] == "MIT"
    assert metadata["authors"] == [{"name": "Microsoft Corporation"}]
    assert metadata["urls"] == {
        "Documentation": "https://github.com/microsoft/FSQ#readme",
        "Issues": "https://github.com/microsoft/FSQ/issues",
        "Repository": "https://github.com/microsoft/FSQ",
    }
    classifiers = set(metadata["classifiers"])
    assert "Operating System :: OS Independent" in classifiers
    assert "Intended Audience :: Developers" in classifiers
    for version in ("3.11", "3.12", "3.13"):
        assert f"Programming Language :: Python :: {version}" in classifiers


def test_sdist_includes_public_release_documentation_and_example() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    includes = set(project["tool"]["hatch"]["build"]["targets"]["sdist"]["include"])

    assert {
        "/CHANGELOG.md",
        "/README.zh-CN.md",
        "/docs/README.md",
        "/docs/architecture.md",
        "/docs/assets/fsq-workflow.svg",
        "/docs/assets/social-preview.png",
        "/docs/assets/social-preview.svg",
        "/docs/case-format.md",
        "/docs/cli-reference.md",
        "/docs/getting-started.md",
        "/docs/getting-started.zh-CN.md",
        "/docs/media",
        "/docs/platform-prerequisites.md",
        "/docs/releases",
        "/docs/support-and-stability.md",
        "/examples",
    } <= includes


def test_release_workflow_is_manual_safe_and_uses_oidc_trusted_publishing() -> None:
    workflow_path = ROOT / ".github" / "workflows" / "release.yml"
    raw = workflow_path.read_text(encoding="utf-8")
    workflow: dict[str, Any] = yaml.safe_load(raw)
    trigger = workflow[True]  # PyYAML 1.1 parses the unquoted GitHub Actions `on` key as true.
    dispatch = trigger["workflow_dispatch"]
    assert set(trigger) == {"workflow_dispatch"}
    assert dispatch["inputs"]["publish"] == {
        "description": "Publish the verified distributions to PyPI",
        "required": True,
        "type": "boolean",
        "default": False,
    }
    assert workflow["permissions"] == {"contents": "read"}
    assert set(workflow["jobs"]) == {"verify", "install-smoke", "publish"}
    publish = workflow["jobs"]["publish"]
    assert publish["if"] == "${{ inputs.publish }}"
    assert publish["needs"] == ["verify", "install-smoke"]
    assert publish["environment"] == "pypi"
    assert publish["permissions"] == {"contents": "read", "id-token": "write"}
    assert any(step.get("uses", "").startswith("actions/download-artifact@") for step in publish["steps"])
    assert publish["steps"][-1]["run"] == "uv publish --trusted-publishing always dist/*"
    install_smoke = workflow["jobs"]["install-smoke"]
    assert install_smoke["needs"] == "verify"
    assert set(install_smoke["strategy"]["matrix"]["os"]) == {"ubuntu-latest", "macos-latest", "windows-latest"}
    assert any(step.get("uses", "").startswith("actions/download-artifact@") for step in install_smoke["steps"])
    install_smoke_commands = "\n".join(str(step.get("run", "")) for step in install_smoke["steps"])
    for command in (
        "python -m venv",
        'python -c "import fsq_agent"',
        "fsq",
        "fsq-agent",
    ):
        assert command in install_smoke_commands
    verify = workflow["jobs"]["verify"]
    assert any(step.get("uses", "").startswith("actions/upload-artifact@") for step in verify["steps"])
    commands = "\n".join(str(step.get("run", "")) for step in verify["steps"])
    for command in (
        "ruff check .",
        "ruff format --check .",
        "python -m pytest",
        "npm run typecheck",
        "npm test",
        "npm run build",
        "uv build",
        "twine check dist/*",
        "tests/test_distribution_contract.py",
        '"$RUNNER_TEMP/fsq-release-smoke/bin/fsq" --help',
        '"$RUNNER_TEMP/fsq-release-smoke/bin/fsq-agent" --help',
    ):
        assert command in commands
    assert "uv sync --extra dev --reinstall-package fsq-agent" in commands
    assert "--frozen" not in commands
    assert "--locked" not in commands
    assert "uv run --no-sync ruff check ." in commands
    assert "uv run --no-sync ruff format --check ." in commands
    assert "uv run --no-sync python -m pytest" in commands
    assert "PYPI_API_TOKEN" not in raw
    assert "password:" not in raw


def test_distribution_includes_only_control_plane_frontend_assets() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    expected = "fsq_agent/adapters/control_plane/static/**"
    retired = "fsq_agent/adapters/control_plane/playground/static/**"

    wheel = project["tool"]["hatch"]["build"]["targets"]["wheel"]
    assert set(wheel["artifacts"]) == {expected, "fsq_agent/resources/**"}
    assert retired not in wheel["artifacts"]
    assert "force-include" not in wheel
    sdist = project["tool"]["hatch"]["build"]["targets"]["sdist"]
    assert expected in sdist["artifacts"]
    assert retired not in sdist["artifacts"]
    assert "force-include" not in sdist


def test_sdist_maps_runtime_resources_to_package_paths() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    sdist = project["tool"]["hatch"]["build"]["targets"]["sdist"]
    assert set(sdist["artifacts"]) == {
        "config.android.yaml",
        "config.web.yaml",
        "config.windows.yaml",
        "config.macos.yaml",
        "knowledge/skills/**",
        "fsq_agent/resources/**",
        "fsq_agent/adapters/control_plane/static/**",
    }


def test_ci_verifies_all_runtime_package_resources() -> None:
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    for resource in (
        "fsq_agent/resources/config.android.yaml",
        "fsq_agent/resources/config.web.yaml",
        "fsq_agent/resources/config.windows.yaml",
        "fsq_agent/resources/config.macos.yaml",
        "fsq_agent/resources/knowledge/skills/android-harness.md",
        "fsq_agent/resources/knowledge/skills/web-harness.md",
        "fsq_agent/resources/knowledge/skills/windows-harness.md",
        "fsq_agent/resources/knowledge/skills/macos-harness.md",
        "fsq_agent/resources/knowledge/skills/automation-basics.md",
        "fsq_agent/agent/templates/agent_instructions.j2",
        "fsq_agent/agent/templates/task_input.j2",
    ):
        assert resource in workflow

    for contract in (
        "Verify clean checkout has no generated package resources",
        "uv.lock must not be tracked",
        "uv build --wheel dist/*.tar.gz --out-dir rebuilt-dist",
        "Wheel rebuilt from sdist has different runtime package resources",
        "Sdist is missing build input",
    ):
        assert contract in workflow
    assert "uv sync --extra dev --reinstall-package fsq-agent" in workflow
    assert "uv sync --all-extras --reinstall-package fsq-agent" in workflow
    assert "cache-dependency-glob: pyproject.toml" in workflow
    assert "cache-dependency-glob: uv.lock" not in workflow
    assert "--frozen" not in workflow
    assert "--locked" not in workflow
    assert "uv run --no-sync ruff check ." in workflow
    assert "uv run --no-sync ruff format --check ." in workflow
    assert "uv run --no-sync python -m pytest" in workflow


def test_readme_uses_current_installation_and_cli_contract() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert "pip install fsq-agent" in readme
    assert "README.zh-CN.md" in readme
    assert "docs/getting-started.zh-CN.md" in readme
    assert "https://github.com/user-attachments/assets/aa9d0a12-2f93-4894-8349-52a013424939" in readme
    assert "https://youtu.be/QqCahxGDdS0" in readme
    hero = readme.split('<p align="center">\n  <img src="docs/assets/fsq-workflow.svg"', 1)[0]
    assert "Autoplay demo page" not in hero
    assert "English captions" not in hero
    assert "docs/demo.html" not in readme
    assert "Bilibili" not in readme
    for command in ("fsq init", "fsq doctor", "fsq providers", "fsq case create", "fsq case test", "fsq runs", "fsq ui"):
        assert command in readme
    for obsolete in (
        "fsq-agent run",
        "run --strict",
        "fsq-agent control-plane",
        "fsq-agent report",
        "fsq-agent playground",
        "init --name",
        "fsq-agent[web]",
        "fsq-agent[android]",
        "fsq-agent[windows]",
        "fsq-agent[macos]",
    ):
        assert obsolete not in readme


def test_chinese_readme_uses_current_installation_and_cli_contract() -> None:
    readme = (ROOT / "README.zh-CN.md").read_text(encoding="utf-8")

    assert "pip install fsq-agent" in readme
    assert "Workspace Root Strategy" in readme
    assert "https://github.com/user-attachments/assets/aa9d0a12-2f93-4894-8349-52a013424939" in readme
    assert "https://youtu.be/QqCahxGDdS0" in readme
    hero = readme.split('<p align="center">\n  <img src="docs/assets/fsq-workflow.svg"', 1)[0]
    assert "自动播放演示页" not in readme
    assert "中文字幕" not in hero
    assert "Bilibili" not in readme
    for command in ("fsq init", "fsq doctor", "fsq providers", "fsq case create", "fsq case test", "fsq runs", "fsq ui"):
        assert command in readme
    for obsolete in (
        "fsq-agent run",
        "run --strict",
        "fsq-agent control-plane",
        "fsq-agent report",
        "fsq-agent playground",
        "init --name",
        "fsq-agent[web]",
        "fsq-agent[android]",
        "fsq-agent[windows]",
        "fsq-agent[macos]",
    ):
        assert obsolete not in readme


def test_release_demo_gif_is_lightweight_and_committed() -> None:
    demo_gif = ROOT / "docs" / "media" / "fsq-v0.1.0-android-demo-preview.gif"

    assert demo_gif.is_file()
    assert demo_gif.stat().st_size < 1_000_000


def test_v010_release_materials_cover_public_launch_contract() -> None:
    notes = (ROOT / "docs" / "releases" / "v0.1.0.md").read_text(encoding="utf-8")
    release_copy = (ROOT / "docs" / "releases" / "v0.1.0-github-release.md").read_text(encoding="utf-8")

    for text in (notes, release_copy):
        assert "python -m pip install fsq-agent" in text
        assert "https://github.com/user-attachments/assets/aa9d0a12-2f93-4894-8349-52a013424939" in text
        assert "https://youtu.be/QqCahxGDdS0" in text
        assert "alpha" in text.lower()
        assert "Playwright" in text
        assert "uiautomator2" in text
        assert "pywinauto" in text
        assert "Appium" in text
        assert "token" in text.lower()
        assert "API key" in text
        assert "/Users/" not in text
        assert "production ready" not in text.lower()


def test_release_acceptance_checklist_uses_current_public_commands() -> None:
    checklist = (ROOT / "docs" / "release-acceptance-checklist.md").read_text(encoding="utf-8")

    for command in (
        "pip install fsq-agent",
        "fsq init",
        "fsq doctor",
        "fsq providers configure",
        "fsq providers status",
        "fsq case create",
        "fsq case test",
        "fsq runs list",
        "fsq runs show",
        "fsq runs logs",
        "fsq ui",
    ):
        assert command in checklist
    for obsolete in (
        "fsq-agent run",
        "run --strict",
        "fsq-agent control-plane",
        "fsq-agent report",
        "fsq-agent playground",
        "init --name",
        "fsq-agent[web]",
        "fsq-agent[android]",
        "fsq-agent[windows]",
        "fsq-agent[macos]",
    ):
        assert obsolete not in checklist
