# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from pathlib import Path

import pytest

from fsq_agent.models import FsqAgentError, SkillConfig
from fsq_agent.skills import SkillBundle, SkillLoader


def test_skill_loader_loads_markdown_file(tmp_path: Path) -> None:
    skill_path = tmp_path / "example.md"
    skill_path.write_text("Use configured tools only.", encoding="utf-8")

    bundles = SkillLoader(tmp_path).load([SkillConfig(name="example", path=skill_path)])

    assert len(bundles) == 1
    assert isinstance(bundles[0], SkillBundle)
    assert bundles[0].name == "example"
    assert bundles[0].instructions == "Use configured tools only."
    assert bundles[0].files == [skill_path]


def test_skill_loader_skips_missing_optional_skill(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    messages: list[str] = []
    monkeypatch.setattr("fsq_agent.skills._loader.logger.warning", lambda message, *args: messages.append(message % args))

    bundles = SkillLoader(tmp_path).load([SkillConfig(name="missing", path=Path("missing.md"))])

    assert bundles == []
    assert messages == [f"Skipping optional skill missing: Optional skill file does not exist. path={tmp_path / 'missing.md'}"]


def test_skill_loader_fails_missing_required_skill(tmp_path: Path) -> None:
    with pytest.raises(FsqAgentError, match="Required skill file") as exc_info:
        SkillLoader(tmp_path).load([SkillConfig(name="missing", path=Path("missing.md"), required=True)])

    assert exc_info.value.context == {"skill": "missing", "path": str(tmp_path / "missing.md")}


def test_repository_android_harness_skill_documents_tool_usage_recovery() -> None:
    skill_path = Path(__file__).resolve().parents[1] / "fsq_agent" / "resources" / "skills" / "android-harness.md"

    bundles = SkillLoader(skill_path.parent).load([SkillConfig(name="android-harness", path=Path("android-harness.md"), required=True)])

    assert "Tool Selection" not in bundles[0].instructions
    assert "Tool Usage Error Recovery" in bundles[0].instructions
    assert "Correct Key Examples" in bundles[0].instructions
    assert "Case Lifecycle" in bundles[0].instructions
    assert "press_key" in bundles[0].instructions
    assert "active tool schema already defines callable names and arguments" in bundles[0].instructions
    assert "Start each Android case with `launch_app`" in bundles[0].instructions
    assert "End each Android case with `kill_app`" in bundles[0].instructions
    assert "Use coordinate taps only when current platform evidence" in bundles[0].instructions
    assert "ui_snapshot" in bundles[0].instructions
    assert "ui_tree" not in bundles[0].instructions
    assert "textType" in bundles[0].instructions
    assert "runtimeSecret" in bundles[0].instructions
    assert '"key": "Back"' in bundles[0].instructions
    assert '"key": "Enter"' in bundles[0].instructions
    assert '"key": "BACK"' in bundles[0].instructions
    assert '"keyCode": 66' in bundles[0].instructions
    assert '"key": "ENTER"' not in bundles[0].instructions
    assert "keyCode-only" not in bundles[0].instructions
    assert "Do not call session management" not in bundles[0].instructions
    assert "required `pressKey` action succeeded" in bundles[0].instructions
    assert "submit_visual_assertion" not in bundles[0].instructions
    assert "sessionId" not in bundles[0].instructions
    assert "pointerType" not in bundles[0].instructions


def test_repository_web_harness_skill_documents_snapshot_first_guidance() -> None:
    skill_path = Path(__file__).resolve().parents[1] / "fsq_agent" / "resources" / "skills" / "web-harness.md"

    bundles = SkillLoader(skill_path.parent).load([SkillConfig(name="web-harness", path=Path("web-harness.md"), required=True)])

    assert "Tool Selection" not in bundles[0].instructions
    assert "Snapshot-First Rules" in bundles[0].instructions
    assert "Tool Usage Error Recovery" in bundles[0].instructions
    assert "ui_snapshot" in bundles[0].instructions
    assert "assert_text" in bundles[0].instructions
    assert "textType" in bundles[0].instructions
    assert "runtimeSecret" in bundles[0].instructions
    assert "Unsupported Capability Families" not in bundles[0].instructions
    assert "raw Playwright APIs" not in bundles[0].instructions
    assert "JavaScript evaluation" not in bundles[0].instructions
    assert "active tool schema already defines callable names and arguments" in bundles[0].instructions
    assert "ui_tree" not in bundles[0].instructions


def test_repository_windows_harness_skill_uses_exposed_assertions() -> None:
    skill_path = Path(__file__).resolve().parents[1] / "fsq_agent" / "resources" / "skills" / "windows-harness.md"

    bundles = SkillLoader(skill_path.parent).load([SkillConfig(name="windows-harness", path=skill_path.name, required=True)])

    assert "ui_snapshot" in bundles[0].instructions
    assert "assert_visible" in bundles[0].instructions
    assert "assert_with_ai" in bundles[0].instructions
    assert "assert_not_visible" not in bundles[0].instructions
