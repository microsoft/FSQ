# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import json
import logging
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from fsq_agent.adapters.cli._capability_bootstrap import build_capability_registry
from fsq_agent.adapters.cli._case_lifecycle import _run_shell_command, run_strict_fsq_lifecycle_case
from fsq_agent.adapters.cli._core_execution import run_fsq_core_case, run_strict_fsq_core_case
from fsq_agent.case_dsl import FsqCaseLoader
from fsq_agent.config import Settings
from fsq_agent.models import (
    ConfigurationError,
    EvidenceBundle,
    ExecutableStep,
    FailureCategory,
    HarnessActionResult,
    HarnessArtifactRef,
    HarnessContext,
    PostActionDelaySettings,
    StepPhase,
)

FSQ_CASE = """
schemaVersion: fsq.ai-test/v1
name: Core CLI Case
platform: android
appId: com.microsoft.emmx
---
- launchApp
- tapOn:
    target: Search box
"""


FSQ_CASE_WITH_TEARDOWN = """
schemaVersion: fsq.ai-test/v1
name: Core CLI Teardown Case
platform: android
appId: com.microsoft.emmx
---
- launchApp
- tapOn:
    target: Search box
- inputText:
    text: skipped
    target: Search box
- killApp
"""


WEB_FSQ_CASE_WITH_TEARDOWN = """
schemaVersion: fsq.ai-test/v1
name: Core Web Teardown Case
platform: web
---
- startBrowser
- navigateTo:
    page: main
    url: https://example.com
- uiSnapshot:
    scope: {kind: page, page: main}
- closeBrowser
"""


def _python_exit_command(exit_code: int) -> str:
    executable = Path(sys.executable).as_posix()
    if sys.platform == "win32":
        return f'& "{executable}" -c "import sys; sys.exit({exit_code})"'
    return f'"{executable}" -c "import sys; sys.exit({exit_code})"'


def test_run_shell_command_uses_powershell_on_windows(monkeypatch) -> None:
    calls: list[tuple[object, dict[str, object]]] = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr("fsq_agent.adapters.cli._case_lifecycle.sys.platform", "win32")
    monkeypatch.setattr("fsq_agent.adapters.cli._case_lifecycle.subprocess.run", fake_run)
    command = "Remove-Item -LiteralPath 'C:\\temp\\test1' -Recurse -Force -Confirm:$false"

    result = _run_shell_command(command)

    assert result.returncode == 0
    assert calls == [
        (
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
            {"capture_output": True, "text": True, "check": False},
        )
    ]


@pytest.mark.skipif(sys.platform != "win32", reason="Windows PowerShell integration test")
def test_run_shell_command_executes_windows_remove_item(tmp_path: Path) -> None:
    target = tmp_path / "remove-me"
    target.mkdir()
    (target / "content.txt").write_text("content", encoding="utf-8")
    command = f"Remove-Item -LiteralPath '{target}' -Recurse -Force -Confirm:$false"

    result = _run_shell_command(command)

    assert result.returncode == 0, result.stderr
    assert not target.exists()


class CliCoreHarness:
    def __init__(self, fail_action: str | None = None) -> None:
        self.fail_action = fail_action
        self.actions: list[str] = []

    def get_context(self) -> HarnessContext:
        return HarnessContext(platform="android", session_id="session-1")

    def action_space(self) -> dict[str, Any]:
        return {}

    def before_action(self, step: ExecutableStep, context: HarnessContext) -> None:
        return None

    def invoke_action(self, step: ExecutableStep, context: HarnessContext) -> HarnessActionResult:
        self.actions.append(step.action_name)
        status = "failed" if step.action_name == self.fail_action else "passed"
        return HarnessActionResult(
            status=status,
            action_name=step.action_name,
            failure_category="target_resolution_error" if status == "failed" else None,
            error_message="Target was not found." if status == "failed" else None,
        )

    def after_action(
        self,
        step: ExecutableStep,
        context: HarnessContext,
        action_result: HarnessActionResult | None,
    ) -> None:
        return None

    def capture_artifact(
        self,
        kind: str,
        reason: str,
        context: HarnessContext,
        step_id: str,
        phase: StepPhase,
    ) -> HarnessArtifactRef:
        return HarnessArtifactRef(
            artifact_id=f"{step_id}-{phase}-{kind}",
            kind=kind,
            path=Path(f"artifacts/raw/{step_id}-{phase}-{reason}.{kind}"),
        )

    def classify_error(self, error: BaseException, phase: StepPhase, step: ExecutableStep) -> FailureCategory:
        return "unknown"


def test_run_fsq_core_case_writes_manifest_and_returns_bundle(tmp_path: Path) -> None:
    case_path = tmp_path / "core_cli.fsq.yaml"
    case_path.write_text(FSQ_CASE, encoding="utf-8")
    harness = CliCoreHarness()

    bundle = run_fsq_core_case(
        case_path=case_path,
        harness=harness,
        output_dir=tmp_path / "runs" / "run-1",
        run_id="run-1",
    )

    assert bundle.run_id == "run-1"
    assert bundle.manifest_path == tmp_path / "runs" / "run-1" / "evidence-manifest.json"
    assert bundle.manifest_path.exists()
    assert harness.actions == ["launch_app", "tap_on"]
    assert [step.step_id for step in bundle.steps] == ["core_cli-step-001", "core_cli-step-002"]

    manifest = json.loads(bundle.manifest_path.read_text(encoding="utf-8"))
    assert manifest["run_id"] == "run-1"
    assert manifest["manifest_path"] == bundle.manifest_path.name
    assert [step["step_id"] for step in manifest["steps"]] == ["core_cli-step-001", "core_cli-step-002"]
    assert [step["status"] for step in manifest["steps"]] == ["passed", "passed"]
    assert [event["event_type"] for event in manifest["events"]].count("step_start") == 2
    assert [artifact["kind"] for artifact in manifest["artifacts"]] == ["screenshot", "ui_snapshot"] * 3
    artifact_reasons = [event["payload"]["reason"] for event in manifest["events"] if event["event_type"] == "artifact_captured"]
    assert artifact_reasons.count("before-action") == 2
    assert artifact_reasons.count("after-action") == 4


def test_run_fsq_core_case_passes_post_action_delay_to_step_runner(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, float] = {}

    class FakeSequenceRunner:
        def __init__(self, *, step_runner, evidence_recorder, cancellation_check=None) -> None:
            captured["platform"] = step_runner.post_action_delay_seconds.platform
            captured["common"] = step_runner.post_action_delay_seconds.common

        def run_steps(self, *, run_id: str, steps, teardown_steps):
            return EvidenceBundle(bundle_id=f"{run_id}-bundle", run_id=run_id)

    monkeypatch.setattr("fsq_agent.adapters.cli._core_execution.StepSequenceRunner", FakeSequenceRunner)

    bundle = run_fsq_core_case(
        case_path=tmp_path / "unused.fsq.yaml",
        harness=CliCoreHarness(),
        output_dir=tmp_path / "runs" / "run-1",
        run_id="run-1",
        steps=[],
        post_action_delay_seconds=PostActionDelaySettings(platform=0.25, common=0.1),
    )

    assert captured == {"platform": 0.25, "common": 0.1}
    assert bundle.manifest_path == tmp_path / "runs" / "run-1" / "evidence-manifest.json"


def test_run_fsq_core_case_runs_trailing_teardown_after_failure(tmp_path: Path) -> None:
    case_path = tmp_path / "core_cli_teardown.fsq.yaml"
    case_path.write_text(FSQ_CASE_WITH_TEARDOWN, encoding="utf-8")
    harness = CliCoreHarness(fail_action="tap_on")

    bundle = run_fsq_core_case(
        case_path=case_path,
        harness=harness,
        output_dir=tmp_path / "runs" / "run-1",
        run_id="run-1",
    )

    assert harness.actions == ["launch_app", "tap_on", "kill_app"]
    assert [step.step_id for step in bundle.steps] == [
        "core_cli_teardown-step-001",
        "core_cli_teardown-step-002",
        "core_cli_teardown-step-003",
        "core_cli_teardown-step-004",
    ]
    assert [step.status for step in bundle.steps] == ["passed", "failed", "skipped", "passed"]
    assert bundle.steps[2].step_execution_id is None

    manifest = json.loads(bundle.manifest_path.read_text(encoding="utf-8"))
    assert [step["step_id"] for step in manifest["steps"]] == [
        "core_cli_teardown-step-001",
        "core_cli_teardown-step-002",
        "core_cli_teardown-step-003",
        "core_cli_teardown-step-004",
    ]
    artifact_reasons = [event["payload"]["reason"] for event in manifest["events"] if event["event_type"] == "artifact_captured"]
    assert artifact_reasons.count("failure") == 0
    assert artifact_reasons.count("after-action") == 4
    assert artifact_reasons.count("before-action") == 4


def test_run_fsq_core_case_runs_trailing_web_close_browser_after_failure(tmp_path: Path) -> None:
    case_path = tmp_path / "core_web_teardown.fsq.yaml"
    case_path.write_text(WEB_FSQ_CASE_WITH_TEARDOWN, encoding="utf-8")
    harness = CliCoreHarness(fail_action="navigate_to")

    bundle = run_fsq_core_case(
        case_path=case_path,
        harness=harness,
        output_dir=tmp_path / "runs" / "run-1",
        run_id="run-1",
        registry=build_capability_registry(platform="web"),
    )

    assert harness.actions == ["start_browser", "navigate_to", "close_browser"]
    assert [step.step_id for step in bundle.steps] == [
        "core_web_teardown-step-001",
        "core_web_teardown-step-002",
        "core_web_teardown-step-003",
        "core_web_teardown-step-004",
    ]
    assert [step.status for step in bundle.steps] == ["passed", "failed", "skipped", "passed"]


def test_run_strict_fsq_core_case_writes_evidence_and_core_report(tmp_path: Path) -> None:
    case_path = tmp_path / "strict_core.fsq.yaml"
    case_path.write_text(FSQ_CASE, encoding="utf-8")
    run_dir = tmp_path / "runs" / "strict-run-1"

    artifact = run_strict_fsq_core_case(
        case_path=case_path,
        harness=CliCoreHarness(),
        output_dir=run_dir,
        run_id="strict-run-1",
    )

    assert artifact.run_id == "strict-run-1"
    assert artifact.path == run_dir / "core-report.md"
    assert artifact.evidence_manifest_path == run_dir / "evidence-manifest.json"
    assert artifact.path.exists()
    assert (run_dir / "core-report.json").exists()

    report = artifact.path.read_text(encoding="utf-8")
    assert "# Core Evidence Report: strict-run-1" in report
    assert "Status: `passed`" in report

    manifest = json.loads((run_dir / "evidence-manifest.json").read_text(encoding="utf-8"))
    assert [step["status"] for step in manifest["steps"]] == ["passed", "passed"]


def test_run_strict_fsq_lifecycle_case_preserves_combined_hook_action_order(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    hook_path = tmp_path / "hook.fsq.yaml"
    hook_path.write_text(
        """
schemaVersion: fsq.ai-test/v1
name: Hook Case
platform: android
---
- tapOn:
    target: Hook target
""",
        encoding="utf-8",
    )
    case_path = tmp_path / "root.fsq.yaml"
    case_path.write_text(
        f"""
schemaVersion: fsq.ai-test/v1
name: Root Case
platform: android
appId: com.microsoft.emmx
onCaseStart:
  runShell: '{_python_exit_command(0)}'
  runCase: hook.fsq.yaml
---
- launchApp
""",
        encoding="utf-8",
    )
    case = FsqCaseLoader().load_case(case_path)
    registry = build_capability_registry()
    run_dir = tmp_path / "runs" / "root"
    harness = CliCoreHarness()
    logging.getLogger("fsq_agent").propagate = True
    caplog.set_level("INFO", logger="fsq_agent.adapters.cli._case_lifecycle")

    artifact = run_strict_fsq_lifecycle_case(
        case_path=case_path,
        case=case,
        settings=Settings(cases={"dir": tmp_path}),
        harness=harness,
        output_dir=run_dir,
        run_id="root",
        registry=registry,
        post_action_delay_seconds=PostActionDelaySettings(platform=0, common=0),
    )

    assert artifact.path == run_dir / "core-report.md"
    assert harness.actions == ["tap_on", "launch_app"]
    manifest = json.loads((run_dir / "evidence-manifest.json").read_text(encoding="utf-8"))
    assert [step["status"] for step in manifest["steps"]] == ["passed", "passed", "passed", "passed"]
    assert manifest["steps"][0]["metadata"]["hook_action_name"] == "runShell"
    assert manifest["steps"][1]["source_ref"]["metadata"]["lifecycle_phase"] == "case"
    assert manifest["steps"][1]["source_ref"]["source_id"] == str(hook_path.resolve())
    assert manifest["steps"][1]["source_ref"]["metadata"]["parent_hook_action"]["hook_action_name"] == "runCase"
    assert manifest["steps"][2]["metadata"]["hook_action_name"] == "runCase"
    assert manifest["steps"][2]["metadata"]["target"] == "hook.fsq.yaml"
    messages = [record.getMessage() for record in caplog.records]
    assert sum("Strict phase before case: start" in message for message in messages) == 1
    assert any("Strict before case action runCase: hook.fsq.yaml" in message and "passed" in message for message in messages)


def test_run_strict_fsq_lifecycle_case_logs_phase_and_action_status(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    case_path = tmp_path / "root_logs.fsq.yaml"
    case_path.write_text(
        f"""
schemaVersion: fsq.ai-test/v1
name: Root Logs Case
platform: android
appId: com.microsoft.emmx
onCaseStart:
    runShell: '{_python_exit_command(0)}'
onCaseComplete:
    runShell: '{_python_exit_command(0)}'
---
- launchApp
""",
        encoding="utf-8",
    )
    case = FsqCaseLoader().load_case(case_path)
    logging.getLogger("fsq_agent").propagate = True
    caplog.set_level("INFO", logger="fsq_agent.adapters.cli._case_lifecycle")

    run_strict_fsq_lifecycle_case(
        case_path=case_path,
        case=case,
        settings=Settings(cases={"dir": tmp_path}),
        harness=CliCoreHarness(),
        output_dir=tmp_path / "runs" / "root-logs",
        run_id="root-logs",
        registry=build_capability_registry(),
        post_action_delay_seconds=PostActionDelaySettings(platform=0, common=0),
    )

    messages = [record.getMessage() for record in caplog.records]
    assert any("Strict phase before case: start" in message for message in messages)
    assert any("Strict before case action runShell" in message and "passed" in message for message in messages)
    assert any("Strict phase main case: start" in message for message in messages)
    assert any("Strict main case action launchApp" in message and "passed" in message for message in messages)
    assert any("Strict phase after case: start" in message for message in messages)
    assert any("Strict after case action runShell" in message and "passed" in message for message in messages)


def test_run_strict_fsq_lifecycle_case_runs_config_hooks_around_case_hooks(tmp_path: Path) -> None:
    case_before = tmp_path / "case_before.fsq.yaml"
    case_before.write_text(
        """
schemaVersion: fsq.ai-test/v1
name: Case Before
platform: android
---
- tapOn:
    target: Case before
""",
        encoding="utf-8",
    )
    case_after = tmp_path / "case_after.fsq.yaml"
    case_after.write_text(
        """
schemaVersion: fsq.ai-test/v1
name: Case After
platform: android
---
- tapOn:
    target: Case after
""",
        encoding="utf-8",
    )
    config_before = tmp_path / "config_before.fsq.yaml"
    config_before.write_text(
        """
schemaVersion: fsq.ai-test/v1
name: Config Before
platform: android
---
- tapOn:
    target: Config before
""",
        encoding="utf-8",
    )
    config_after = tmp_path / "config_after.fsq.yaml"
    config_after.write_text(
        """
schemaVersion: fsq.ai-test/v1
name: Config After
platform: android
---
- tapOn:
    target: Config after
""",
        encoding="utf-8",
    )
    case_path = tmp_path / "root_config_hooks.fsq.yaml"
    case_path.write_text(
        """
schemaVersion: fsq.ai-test/v1
name: Root Config Hooks
platform: android
appId: com.microsoft.emmx
onCaseStart:
  runCase: case_before.fsq.yaml
onCaseComplete:
  runCase: case_after.fsq.yaml
---
- launchApp
""",
        encoding="utf-8",
    )
    case = FsqCaseLoader().load_case(case_path)
    settings = Settings(
        cases={"dir": tmp_path},
        case_lifecycle={
            "on_case_start": [{"runCase": "config_before.fsq.yaml"}],
            "on_case_complete": [{"runCase": "config_after.fsq.yaml"}],
        },
    )
    harness = CliCoreHarness()

    run_strict_fsq_lifecycle_case(
        case_path=case_path,
        case=case,
        settings=settings,
        harness=harness,
        output_dir=tmp_path / "runs" / "root-config-hooks",
        run_id="root-config-hooks",
        registry=build_capability_registry(),
        post_action_delay_seconds=PostActionDelaySettings(platform=0, common=0),
    )

    assert harness.actions == ["tap_on", "tap_on", "launch_app", "tap_on", "tap_on"]
    report = json.loads((tmp_path / "runs" / "root-config-hooks" / "core-report.json").read_text(encoding="utf-8"))
    sources = [step["source_ref"]["metadata"].get("hook_origin") for step in report["steps"] if step["step_id"].endswith("step-001")]
    assert sources[:2] == ["config", "case"]
    assert sources[-2:] == ["case", "config"]


def test_run_strict_fsq_lifecycle_case_config_before_failure_skips_case_before_and_main(tmp_path: Path) -> None:
    case_before = tmp_path / "case_before.fsq.yaml"
    case_before.write_text(
        """
schemaVersion: fsq.ai-test/v1
name: Case Before
platform: android
---
- tapOn:
    target: Case before
""",
        encoding="utf-8",
    )
    case_after = tmp_path / "case_after.fsq.yaml"
    case_after.write_text(
        """
schemaVersion: fsq.ai-test/v1
name: Case After
platform: android
---
- killApp
""",
        encoding="utf-8",
    )
    case_path = tmp_path / "root_config_before_failure.fsq.yaml"
    case_path.write_text(
        """
schemaVersion: fsq.ai-test/v1
name: Root Config Before Failure
platform: android
appId: com.microsoft.emmx
onCaseStart:
  runCase: case_before.fsq.yaml
onCaseComplete:
  runCase: case_after.fsq.yaml
---
- launchApp
""",
        encoding="utf-8",
    )
    case = FsqCaseLoader().load_case(case_path)
    settings = Settings(
        cases={"dir": tmp_path},
        case_lifecycle={
            "on_case_start": [{"runShell": _python_exit_command(9)}],
            "on_case_complete": [{"runShell": _python_exit_command(0)}],
        },
    )
    harness = CliCoreHarness()

    run_strict_fsq_lifecycle_case(
        case_path=case_path,
        case=case,
        settings=settings,
        harness=harness,
        output_dir=tmp_path / "runs" / "root-config-before-failure",
        run_id="root-config-before-failure",
        registry=build_capability_registry(),
        post_action_delay_seconds=PostActionDelaySettings(platform=0, common=0),
    )

    assert harness.actions == ["kill_app"]
    report = json.loads((tmp_path / "runs" / "root-config-before-failure" / "core-report.json").read_text(encoding="utf-8"))
    assert report["summary"]["status"] == "failed"
    assert [step["status"] for step in report["steps"]] == ["failed", "passed", "passed", "passed"]


def test_run_strict_fsq_lifecycle_case_config_after_failure_fails_overall(tmp_path: Path) -> None:
    case_path = tmp_path / "root_config_after_failure.fsq.yaml"
    case_path.write_text(
        """
schemaVersion: fsq.ai-test/v1
name: Root Config After Failure
platform: android
appId: com.microsoft.emmx
---
- launchApp
""",
        encoding="utf-8",
    )
    case = FsqCaseLoader().load_case(case_path)
    settings = Settings(
        cases={"dir": tmp_path},
        case_lifecycle={"on_case_complete": [{"runShell": _python_exit_command(3)}]},
    )
    harness = CliCoreHarness()

    run_strict_fsq_lifecycle_case(
        case_path=case_path,
        case=case,
        settings=settings,
        harness=harness,
        output_dir=tmp_path / "runs" / "root-config-after-failure",
        run_id="root-config-after-failure",
        registry=build_capability_registry(),
        post_action_delay_seconds=PostActionDelaySettings(platform=0, common=0),
    )

    assert harness.actions == ["launch_app"]
    report = json.loads((tmp_path / "runs" / "root-config-after-failure" / "core-report.json").read_text(encoding="utf-8"))
    assert report["summary"]["status"] == "failed"
    assert [step["status"] for step in report["steps"]] == ["passed", "failed"]
    assert report["steps"][1]["metadata"]["hook_origin"] == "config"


def test_run_strict_fsq_lifecycle_case_rejects_config_hook_recursion(tmp_path: Path) -> None:
    case_path = tmp_path / "root_config_recursion.fsq.yaml"
    case_path.write_text(
        """
schemaVersion: fsq.ai-test/v1
name: Root Config Recursion
platform: android
appId: com.microsoft.emmx
---
- launchApp
""",
        encoding="utf-8",
    )
    case = FsqCaseLoader().load_case(case_path)
    settings = Settings(
        cases={"dir": tmp_path},
        case_lifecycle={"on_case_start": [{"runCase": "root_config_recursion.fsq.yaml"}]},
    )

    with pytest.raises(ConfigurationError, match="Recursive lifecycle hook runCase"):
        run_strict_fsq_lifecycle_case(
            case_path=case_path,
            case=case,
            settings=settings,
            harness=CliCoreHarness(),
            output_dir=tmp_path / "runs" / "root-config-recursion",
            run_id="root-config-recursion",
            registry=build_capability_registry(),
            post_action_delay_seconds=PostActionDelaySettings(platform=0, common=0),
        )


def test_run_strict_fsq_lifecycle_case_runs_complete_hook_after_start_failure(tmp_path: Path) -> None:
    cleanup_path = tmp_path / "cleanup.fsq.yaml"
    cleanup_path.write_text(
        """
schemaVersion: fsq.ai-test/v1
name: Cleanup Case
platform: android
---
- killApp
""",
        encoding="utf-8",
    )
    case_path = tmp_path / "root_failure.fsq.yaml"
    case_path.write_text(
        f"""
schemaVersion: fsq.ai-test/v1
name: Root Failure Case
platform: android
appId: com.microsoft.emmx
onCaseStart:
  runShell: '{_python_exit_command(7)}'
onCaseComplete:
  runCase: cleanup.fsq.yaml
---
- launchApp
""",
        encoding="utf-8",
    )
    case = FsqCaseLoader().load_case(case_path)
    registry = build_capability_registry()
    run_dir = tmp_path / "runs" / "root-failure"
    harness = CliCoreHarness()

    artifact = run_strict_fsq_lifecycle_case(
        case_path=case_path,
        case=case,
        settings=Settings(cases={"dir": tmp_path}),
        harness=harness,
        output_dir=run_dir,
        run_id="root-failure",
        registry=registry,
        post_action_delay_seconds=PostActionDelaySettings(platform=0, common=0),
    )

    assert artifact.path == run_dir / "core-report.md"
    assert harness.actions == ["kill_app"]
    report = json.loads((run_dir / "core-report.json").read_text(encoding="utf-8"))
    assert report["summary"]["status"] == "failed"
    assert [step["status"] for step in report["steps"]] == ["failed", "passed", "passed"]
    assert report["steps"][0]["failure_category"] == "action_error"
