# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from pathlib import Path
from typing import Any

from pydantic import BaseModel

from fsq_agent._capability_bootstrap import build_capability_registry
from fsq_agent.core import CapabilityRegistry, RuntimeSecretStore, StepRunner
from fsq_agent.models import (
    CapabilityDefinition,
    ExecutableStep,
    FailureCategory,
    HarnessActionResult,
    HarnessArtifactRef,
    HarnessContext,
    PostActionDelaySettings,
    RuntimeSecretSettings,
    StepPhase,
)


class NoParams(BaseModel):
    pass


class SuccessfulHarness:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def get_context(self) -> HarnessContext:
        self.calls.append("get_context")
        return HarnessContext(platform="android", session_id="session-1")

    def action_space(self) -> dict[str, Any]:
        return {"tap": {"description": "Tap an element"}}

    def before_action(self, step: ExecutableStep, context: HarnessContext) -> None:
        self.calls.append(f"before:{step.action_name}:{context.session_id}")

    def invoke_action(self, step: ExecutableStep, context: HarnessContext) -> HarnessActionResult:
        self.calls.append(f"invoke:{step.action_name}:{context.session_id}")
        return HarnessActionResult(status="passed", action_name=step.action_name, output={"ok": True})

    def after_action(
        self,
        step: ExecutableStep,
        context: HarnessContext,
        action_result: HarnessActionResult | None,
    ) -> None:
        status = action_result.status if action_result else "none"
        self.calls.append(f"after:{step.action_name}:{status}")

    def capture_artifact(
        self,
        kind: str,
        reason: str,
        context: HarnessContext,
        step_id: str,
        phase: StepPhase,
    ) -> HarnessArtifactRef:
        return HarnessArtifactRef(artifact_id=f"{kind}-1", kind="log", path=Path(f"runs/run-1/{step_id}-{phase}-{reason}.log"))

    def classify_error(self, error: BaseException, phase: StepPhase, step: ExecutableStep) -> FailureCategory:
        return "unknown"


def test_runtime_secret_store_uses_private_settings_values_only(monkeypatch) -> None:
    monkeypatch.setenv("TEST_ACCOUNT_PASSWORD", "environment-value")
    settings = RuntimeSecretSettings()
    settings.set_values({"TEST_ACCOUNT_PASSWORD": "workspace-value"})

    store = RuntimeSecretStore.from_settings(settings)

    assert store.resolve("TEST_ACCOUNT_PASSWORD") == "workspace-value"
    assert settings.private_values() == {"TEST_ACCOUNT_PASSWORD": "workspace-value"}
    assert "workspace-value" not in str(settings.model_dump())


class InvokeFailureHarness(SuccessfulHarness):
    def invoke_action(self, step: ExecutableStep, context: HarnessContext) -> HarnessActionResult:
        self.calls.append(f"invoke:{step.action_name}:{context.session_id}")
        raise RuntimeError("tap failed")

    def after_action(
        self,
        step: ExecutableStep,
        context: HarnessContext,
        action_result: HarnessActionResult | None,
    ) -> None:
        status = action_result.status if action_result else "none"
        self.calls.append(f"after:{step.action_name}:{status}")

    def classify_error(self, error: BaseException, phase: StepPhase, step: ExecutableStep) -> FailureCategory:
        self.calls.append(f"classify:{phase}:{step.action_name}:{type(error).__name__}")
        return "action_error"


def test_runner_uses_structural_public_redaction_source():
    class Resolver:
        def resolve(self, name):
            return "private-value"

        def redaction_values(self):
            return ("private-value",)

    class Harness(InvokeFailureHarness):
        def invoke_action(self, step, context):
            raise RuntimeError("failure with private-value")

    runner = StepRunner(Harness(), runtime_secret_store=Resolver())
    result = runner.run_step("run", ExecutableStep(step_id="one", action_name="tap", kind="action", params={}))
    assert result.status == "failed"
    assert "private-value" not in result.model_dump_json()
    assert all("private-value" not in event.model_dump_json() for event in runner.events)


class FileNotFoundInvokeFailureHarness(InvokeFailureHarness):
    def invoke_action(self, step: ExecutableStep, context: HarnessContext) -> HarnessActionResult:
        self.calls.append(f"invoke:{step.action_name}:{context.session_id}")
        raise FileNotFoundError(2, "No such file or directory", "C:/local/tool.exe")


class FailedResultHarness(SuccessfulHarness):
    def invoke_action(self, step: ExecutableStep, context: HarnessContext) -> HarnessActionResult:
        self.calls.append(f"invoke:{step.action_name}:{context.session_id}")
        return HarnessActionResult(
            status="failed",
            action_name=step.action_name,
            failure_category="target_resolution_error",
            error_message="target not found",
        )


class CapturingHarness(SuccessfulHarness):
    def capture_artifact(
        self,
        kind: str,
        reason: str,
        context: HarnessContext,
        step_id: str,
        phase: StepPhase,
    ) -> HarnessArtifactRef:
        self.calls.append(f"capture:{kind}:{reason}:{step_id}:{phase}")
        return HarnessArtifactRef(
            artifact_id=f"{step_id}-{phase}-{reason}-{kind}",
            kind=kind,
            path=Path(f"artifacts/{kind}/{step_id}-{phase}-{reason}.{kind}"),
        )


class ScreenSizeHarness(SuccessfulHarness):
    def get_context(self) -> HarnessContext:
        self.calls.append("get_context")
        return HarnessContext(platform="android", session_id="session-1", screen_size=(1080, 2400))


class FailingCaptureHarness(SuccessfulHarness):
    def capture_artifact(
        self,
        kind: str,
        reason: str,
        context: HarnessContext,
        step_id: str,
        phase: StepPhase,
    ) -> HarnessArtifactRef:
        self.calls.append(f"capture:{kind}:{reason}:{step_id}:{phase}")
        raise RuntimeError("capture failed")


class FileNotFoundCaptureHarness(FailingCaptureHarness):
    def capture_artifact(
        self,
        kind: str,
        reason: str,
        context: HarnessContext,
        step_id: str,
        phase: StepPhase,
    ) -> HarnessArtifactRef:
        self.calls.append(f"capture:{kind}:{reason}:{step_id}:{phase}")
        raise FileNotFoundError(2, "No such file or directory", "C:/local/artifact.png")


class FailedCapturingHarness(CapturingHarness):
    def invoke_action(self, step: ExecutableStep, context: HarnessContext) -> HarnessActionResult:
        self.calls.append(f"invoke:{step.action_name}:{context.session_id}")
        return HarnessActionResult(
            status="failed",
            action_name=step.action_name,
            failure_category="target_resolution_error",
            error_message="target not found",
        )


class ArtifactResultHarness(SuccessfulHarness):
    def invoke_action(self, step: ExecutableStep, context: HarnessContext) -> HarnessActionResult:
        self.calls.append(f"invoke:{step.action_name}:{context.session_id}")
        return HarnessActionResult(
            status="passed",
            action_name=step.action_name,
            artifact_refs=[
                HarnessArtifactRef(
                    artifact_id="ai-screenshot",
                    kind="screenshot",
                    path=Path("artifacts/screenshots/ai-screenshot.png"),
                )
            ],
            metadata={"assertion_engine": "ai_visual"},
        )


class RecordingHarness(SuccessfulHarness):
    def __init__(self) -> None:
        super().__init__()
        self.invoked_steps: list[ExecutableStep] = []

    def invoke_action(self, step: ExecutableStep, context: HarnessContext) -> HarnessActionResult:
        self.calls.append(f"invoke:{step.action_name}:{context.session_id}")
        self.invoked_steps.append(step)
        return HarnessActionResult(status="passed", action_name=step.action_name, output={"params": step.params})


def _tap_step() -> ExecutableStep:
    return ExecutableStep(
        step_id="step-1",
        kind="action",
        action_name="tap",
        params={"text": "Login"},
    )


def _runner(harness: Any, post_action_delay_seconds: PostActionDelaySettings | None = None) -> StepRunner:
    return StepRunner(
        harness=harness,
        capability_registry=build_capability_registry(),
        post_action_delay_seconds=post_action_delay_seconds,
    )


def _web_runner(harness: Any) -> StepRunner:
    return StepRunner(
        harness=harness,
        capability_registry=build_capability_registry(platform="web"),
    )


def _macos_runner(harness: Any) -> StepRunner:
    return StepRunner(
        harness=harness,
        capability_registry=build_capability_registry(platform="macos"),
    )


def test_step_runner_runs_successful_step_through_three_phases() -> None:
    harness = SuccessfulHarness()
    runner = _runner(harness)

    result = runner.run_step(run_id="run-1", step=_tap_step())

    assert result.step_id == "step-1"
    assert result.status == "passed"
    assert [phase.phase for phase in result.phase_reports] == ["prepare", "invoke", "settle", "finalize"]
    assert [phase.status for phase in result.phase_reports] == ["passed", "passed", "passed", "passed"]
    assert harness.calls == [
        "get_context",
        "before:tap:session-1",
        "invoke:tap:session-1",
        "after:tap:passed",
    ]
    assert [event.event_type for event in runner.events] == [
        "step_start",
        "phase_start",
        "phase_finish",
        "phase_start",
        "harness_call_start",
        "action_result",
        "harness_call_finish",
        "phase_finish",
        "phase_start",
        "phase_finish",
        "phase_start",
        "phase_finish",
        "step_finish",
    ]


def test_step_runner_executes_wait_ms_through_harness_invoke_action() -> None:
    class CommonHarness(CapturingHarness):
        def invoke_action(self, step: ExecutableStep, context: HarnessContext) -> HarnessActionResult:
            self.calls.append(f"invoke:{step.action_name}:{context.session_id}")
            return HarnessActionResult(
                status="passed",
                action_name=step.action_name,
                output={"type": "wait_completed", "duration_ms": 1, "elapsed_ms": 1, "reason": "settle"},
                metadata={
                    "capability_name": "wait_ms",
                    "executor_kind": "common",
                    "duration_ms": 1,
                    "reason": "settle",
                    "replay": {"kind": "fsq_command", "alias": "waitMs"},
                    "common_output": {"type": "wait_completed", "duration_ms": 1, "elapsed_ms": 1, "reason": "settle"},
                },
            )

    harness = CommonHarness()
    runner = _runner(harness)
    step = ExecutableStep(step_id="wait-1", kind="action", action_name="waitMs", params={"duration_ms": 1, "reason": "settle"})

    result = runner.run_step(run_id="run-1", step=step)

    assert result.status == "passed"
    assert harness.calls == [
        "get_context",
        "capture:screenshot:before-action:wait-1:prepare",
        "capture:ui_snapshot:before-action:wait-1:prepare",
        "before:wait_ms:session-1",
        "invoke:wait_ms:session-1",
        "after:wait_ms:passed",
        "get_context",
        "capture:screenshot:after-action:wait-1:finalize",
        "capture:ui_snapshot:after-action:wait-1:finalize",
    ]
    assert [phase.phase for phase in result.phase_reports] == ["prepare", "invoke", "settle", "finalize"]
    metadata = result.phase_reports[1].metadata
    assert metadata["capability_name"] == "wait_ms"
    assert metadata["executor_kind"] == "common"
    assert metadata["duration_ms"] == 1
    assert metadata["reason"] == "settle"
    assert metadata["replay"] == {"kind": "fsq_command", "alias": "waitMs"}
    assert metadata["common_output"]["type"] == "wait_completed"
    assert "harness_call_start" in [event.event_type for event in runner.events]


def test_step_runner_resolves_runtime_secret_text_before_driver_invocation() -> None:
    harness = RecordingHarness()
    runner = StepRunner(
        harness=harness,
        capability_registry=build_capability_registry(),
        runtime_secret_store=RuntimeSecretStore(["TEST_ACCOUNT_EMAIL"], {"TEST_ACCOUNT_EMAIL": "user@example.com"}),
    )
    step = ExecutableStep(
        step_id="input-1",
        kind="action",
        action_name="inputText",
        params={"target": "Email", "text": "TEST_ACCOUNT_EMAIL", "textType": "runtimeSecret"},
    )

    result = runner.run_step(run_id="run-1", step=step)

    assert result.status == "passed"
    assert harness.invoked_steps[0].params == {"target": "Email", "text": "user@example.com", "textType": "literal"}
    metadata = result.phase_reports[1].metadata
    assert metadata["safe_replay_params"] == {"target": "Email", "text": "TEST_ACCOUNT_EMAIL", "textType": "runtimeSecret"}
    assert "harness_output" not in metadata


def test_step_runner_fails_runtime_secret_text_before_driver_invocation_when_missing() -> None:
    harness = RecordingHarness()
    runner = StepRunner(
        harness=harness,
        capability_registry=build_capability_registry(),
        runtime_secret_store=RuntimeSecretStore(["TEST_ACCOUNT_EMAIL"], {}),
    )
    step = ExecutableStep(
        step_id="input-1",
        kind="action",
        action_name="inputText",
        params={"target": "Email", "text": "TEST_ACCOUNT_EMAIL", "textType": "runtimeSecret"},
    )

    result = runner.run_step(run_id="run-1", step=step)

    assert result.status == "failed"
    assert result.failure_category == "configuration_error"
    assert "Runtime secret is not set" in (result.error_message or "")
    assert harness.invoked_steps == []


def test_step_runner_validates_capability_params_before_driver_invocation() -> None:
    harness = RecordingHarness()
    runner = StepRunner(harness=harness, capability_registry=build_capability_registry())
    step = ExecutableStep(
        step_id="input-1",
        kind="action",
        action_name="inputText",
        params={"text": "hello"},
    )

    result = runner.run_step(run_id="run-1", step=step)

    assert result.status == "failed"
    assert result.failure_category == "configuration_error"
    assert "Invalid capability parameters" in (result.error_message or "")
    assert harness.invoked_steps == []


def test_step_runner_applies_platform_delay_after_invoke_before_finalize(monkeypatch) -> None:
    harness = SuccessfulHarness()

    def fake_sleep(seconds: float) -> None:
        harness.calls.append(f"sleep:{seconds}")

    monkeypatch.setattr("fsq_agent.core.runner._runner.time.sleep", fake_sleep)
    runner = _runner(harness, PostActionDelaySettings(platform=0.25, common=0.0))
    step = ExecutableStep(step_id="step-1", kind="action", action_name="tapOn", params={"target": "Login"})

    result = runner.run_step(run_id="run-1", step=step)

    assert result.status == "passed"
    assert harness.calls == [
        "get_context",
        "before:tap_on:session-1",
        "invoke:tap_on:session-1",
        "sleep:0.25",
        "after:tap_on:passed",
        "get_context",
    ]
    assert result.phase_reports[1].metadata["post_action_delay_seconds"] == 0.25
    invoke_finish_events = [event for event in runner.events if event.event_type == "phase_finish" and event.phase == "invoke"]
    assert invoke_finish_events[0].payload["status"] == "passed"
    assert invoke_finish_events[0].payload["post_action_delay_seconds"] == 0.25
    assert invoke_finish_events[0].payload["phase_report"]["duration_ms"] is not None


def test_step_runner_adds_reference_screen_size_for_tap_at_replay() -> None:
    harness = ScreenSizeHarness()
    runner = _runner(harness)
    step = ExecutableStep(step_id="tap-at-1", kind="action", action_name="tapAt", params={"point": {"x": 100, "y": 200}})

    result = runner.run_step(run_id="run-1", step=step)

    expected = {"point": {"x": 100, "y": 200}, "reference_screen_size": {"width": 1080, "height": 2400}}
    assert result.status == "passed"
    assert result.phase_reports[1].metadata["safe_replay_params"] == expected
    assert runner.last_capability_execution_result is not None
    assert runner.last_capability_execution_result.safe_replay_params == expected


def test_step_runner_adds_reference_screen_size_for_point_based_swipe_replay() -> None:
    harness = ScreenSizeHarness()
    runner = _runner(harness)
    step = ExecutableStep(
        step_id="swipe-1",
        kind="action",
        action_name="swipe",
        params={"start": {"x": 800, "y": 1900}, "end": {"x": 200, "y": 1900}, "duration": 1000},
    )

    result = runner.run_step(run_id="run-1", step=step)

    expected = {
        "start": {"x": 800, "y": 1900},
        "end": {"x": 200, "y": 1900},
        "duration": 1000,
        "reference_screen_size": {"width": 1080, "height": 2400},
    }
    assert result.status == "passed"
    assert result.phase_reports[1].metadata["safe_replay_params"] == expected
    assert runner.last_capability_execution_result is not None
    assert runner.last_capability_execution_result.safe_replay_params == expected


def test_step_runner_uses_common_delay_and_skips_zero_sleep(monkeypatch) -> None:
    sleep_calls: list[float] = []
    monkeypatch.setattr("fsq_agent.core.runner._runner.time.sleep", sleep_calls.append)
    registry = CapabilityRegistry.from_definitions([CapabilityDefinition(name="custom_common", executor_kind="common", params_model=NoParams)])
    runner = StepRunner(
        harness=SuccessfulHarness(),
        capability_registry=registry,
        post_action_delay_seconds=PostActionDelaySettings(platform=0.0, common=0.1),
    )

    result = runner.run_step(run_id="run-1", step=ExecutableStep(step_id="common-1", kind="action", action_name="custom_common"))

    assert result.status == "passed"
    assert sleep_calls == [0.1]
    assert result.phase_reports[1].metadata["post_action_delay_seconds"] == 0.1

    sleep_calls.clear()
    zero_delay_registry = CapabilityRegistry.from_definitions([CapabilityDefinition(name="zero_common", executor_kind="common", params_model=NoParams, post_action_delay_seconds=0)])
    zero_delay_runner = StepRunner(
        harness=SuccessfulHarness(),
        capability_registry=zero_delay_registry,
        post_action_delay_seconds=PostActionDelaySettings(platform=0.0, common=0.1),
    )

    zero_delay_result = zero_delay_runner.run_step(
        run_id="run-1",
        step=ExecutableStep(step_id="common-2", kind="action", action_name="zero_common"),
    )

    assert zero_delay_result.phase_reports[1].metadata["post_action_delay_seconds"] == 0
    assert sleep_calls == []


def test_step_runner_wraps_invoke_exception_and_still_finalizes() -> None:
    harness = InvokeFailureHarness()
    runner = StepRunner(harness=harness)

    result = runner.run_step(run_id="run-1", step=_tap_step())

    assert result.status == "failed"
    assert result.failure_category == "action_error"
    assert result.error_message == "RuntimeError: tap failed"
    assert [phase.phase for phase in result.phase_reports] == ["prepare", "invoke", "settle", "finalize"]
    assert [phase.status for phase in result.phase_reports] == ["passed", "failed", "passed", "passed"]
    invoke_report = result.phase_reports[1]
    assert invoke_report.failure_category == "action_error"
    assert invoke_report.error_message == "RuntimeError: tap failed"
    assert harness.calls == [
        "get_context",
        "before:tap:session-1",
        "invoke:tap:session-1",
        "classify:invoke:tap:RuntimeError",
        "after:tap:none",
    ]
    assert "step_error" in [event.event_type for event in runner.events]
    assert runner.events[-1].event_type == "step_finish"


def test_step_runner_includes_exception_type_for_filesystem_invoke_failures() -> None:
    runner = StepRunner(harness=FileNotFoundInvokeFailureHarness())

    result = runner.run_step(run_id="run-1", step=_tap_step())

    assert result.status == "failed"
    assert result.error_message.startswith("FileNotFoundError: [Errno 2] No such file or directory")
    assert "C:/local/tool.exe" in result.error_message


def test_step_runner_preserves_failed_harness_action_result() -> None:
    harness = FailedResultHarness()
    runner = StepRunner(harness=harness)

    result = runner.run_step(run_id="run-1", step=_tap_step())

    assert result.status == "failed"
    assert result.failure_category == "target_resolution_error"
    assert result.error_message == "target not found"
    assert [phase.status for phase in result.phase_reports] == ["passed", "failed", "passed", "passed"]
    assert "step_error" in [event.event_type for event in runner.events]


def test_step_runner_attaches_action_result_artifacts_to_invoke_phase() -> None:
    harness = ArtifactResultHarness()
    runner = StepRunner(harness=harness)

    result = runner.run_step(run_id="run-1", step=_tap_step())

    invoke_report = result.phase_reports[1]
    assert result.status == "passed"
    assert [artifact.artifact_id for artifact in invoke_report.artifact_refs] == ["ai-screenshot"]
    assert invoke_report.artifact_refs[0].path == Path("artifacts/screenshots/ai-screenshot.png")


def test_step_runner_derives_action_evidence_from_driver_step_kind() -> None:
    harness = CapturingHarness()
    runner = _runner(harness)
    step = ExecutableStep(
        step_id="step-1",
        kind="action",
        action_name="tapOn",
        params={"target": "Login"},
    )

    result = runner.run_step(run_id="run-1", step=step)

    prepare_report = result.phase_reports[0]
    finalize_report = result.phase_reports[3]
    assert result.status == "passed"
    assert [artifact.kind for artifact in prepare_report.artifact_refs] == ["screenshot", "ui_snapshot"]
    assert [artifact.kind for artifact in finalize_report.artifact_refs] == ["screenshot", "ui_snapshot"]
    assert "before:tap_on:session-1" in harness.calls
    assert "invoke:tap_on:session-1" in harness.calls
    assert "capture:screenshot:before-action:step-1:prepare" in harness.calls
    assert "capture:ui_snapshot:after-action:step-1:finalize" in harness.calls


def test_step_runner_uses_normalized_ui_snapshot_for_web_driver_steps() -> None:
    class WebCapturingHarness(CapturingHarness):
        def get_context(self) -> HarnessContext:
            self.calls.append("get_context")
            return HarnessContext(platform="web", session_id="session-1")

    harness = WebCapturingHarness()
    runner = _web_runner(harness)
    step = ExecutableStep(
        step_id="step-1",
        kind="action",
        action_name="clickOn",
        params={"target": "Search"},
    )

    result = runner.run_step(run_id="run-1", step=step)

    prepare_report = result.phase_reports[0]
    finalize_report = result.phase_reports[3]
    assert result.status == "passed"
    assert [artifact.kind for artifact in prepare_report.artifact_refs] == ["screenshot", "ui_snapshot"]
    assert [artifact.kind for artifact in finalize_report.artifact_refs] == ["screenshot", "ui_snapshot"]
    assert "before:click_on:session-1" in harness.calls
    assert "invoke:click_on:session-1" in harness.calls
    assert "capture:screenshot:before-action:step-1:prepare" in harness.calls
    assert "capture:ui_snapshot:after-action:step-1:finalize" in harness.calls


def test_step_runner_uses_normalized_ui_snapshot_for_macos_driver_steps() -> None:
    class MacOSCapturingHarness(CapturingHarness):
        def get_context(self) -> HarnessContext:
            self.calls.append("get_context")
            return HarnessContext(platform="macos", session_id="session-1")

    harness = MacOSCapturingHarness()
    runner = _macos_runner(harness)
    step = ExecutableStep(
        step_id="step-1",
        kind="action",
        action_name="clickOn",
        params={"target": "Search"},
    )

    result = runner.run_step(run_id="run-1", step=step)

    prepare_report = result.phase_reports[0]
    finalize_report = result.phase_reports[3]
    assert result.status == "passed"
    assert [artifact.kind for artifact in prepare_report.artifact_refs] == ["screenshot", "ui_snapshot"]
    assert [artifact.kind for artifact in finalize_report.artifact_refs] == ["screenshot", "ui_snapshot"]
    assert "before:click_on:session-1" in harness.calls
    assert "invoke:click_on:session-1" in harness.calls
    assert "capture:screenshot:before-action:step-1:prepare" in harness.calls
    assert "capture:ui_snapshot:after-action:step-1:finalize" in harness.calls


def test_step_runner_derives_assertion_evidence_from_driver_step_kind() -> None:
    harness = CapturingHarness()
    runner = _runner(harness)
    step = ExecutableStep(
        step_id="assert-1",
        kind="assertion",
        action_name="assertVisible",
        params={"target": "Login"},
    )

    result = runner.run_step(run_id="run-1", step=step)

    assert result.status == "passed"
    assert [artifact.kind for artifact in result.phase_reports[0].artifact_refs] == ["screenshot", "ui_snapshot"]
    assert result.phase_reports[3].artifact_refs == []
    assert "capture:screenshot:before-action:assert-1:prepare" in harness.calls
    assert not any("after-action:assert-1" in call for call in harness.calls)


def test_step_runner_derives_setup_evidence_from_driver_step_kind() -> None:
    harness = CapturingHarness()
    runner = _runner(harness)
    step = ExecutableStep(
        step_id="setup-1",
        kind="setup",
        action_name="launchApp",
        params={},
    )

    result = runner.run_step(run_id="run-1", step=step)

    assert result.status == "passed"
    assert result.phase_reports[0].artifact_refs == []
    assert [artifact.kind for artifact in result.phase_reports[3].artifact_refs] == ["screenshot", "ui_snapshot"]
    assert "capture:screenshot:after-action:setup-1:finalize" in harness.calls


def test_step_runner_derives_teardown_evidence_from_driver_step_kind() -> None:
    harness = CapturingHarness()
    runner = _runner(harness)
    step = ExecutableStep(
        step_id="teardown-1",
        kind="teardown",
        action_name="killApp",
        params={},
    )

    result = runner.run_step(run_id="run-1", step=step)

    assert result.status == "passed"
    assert [artifact.kind for artifact in result.phase_reports[0].artifact_refs] == ["screenshot", "ui_snapshot"]
    assert result.phase_reports[3].artifact_refs == []
    assert "capture:screenshot:before-action:teardown-1:prepare" in harness.calls
    assert not any("after-action:teardown-1" in call for call in harness.calls)


def test_step_runner_captures_common_actions_but_not_observation_or_diagnostic_steps() -> None:
    harness = CapturingHarness()
    registry = CapabilityRegistry.from_definitions(
        [
            CapabilityDefinition(name="custom_common", executor_kind="common", params_model=NoParams),
            CapabilityDefinition(name="custom_observation", executor_kind="driver", params_model=NoParams, step_kind="observation"),
            CapabilityDefinition(name="custom_diagnostic", executor_kind="driver", params_model=NoParams, step_kind="diagnostic"),
        ]
    )
    runner = StepRunner(harness=harness, capability_registry=registry)

    common_result = runner.run_step(
        run_id="run-1",
        step=ExecutableStep(step_id="common-1", kind="action", action_name="custom_common"),
    )

    assert common_result.status == "passed"
    assert [artifact.kind for artifact in common_result.phase_reports[0].artifact_refs] == ["screenshot", "ui_snapshot"]
    assert common_result.phase_reports[1].artifact_refs == []
    assert [artifact.kind for artifact in common_result.phase_reports[3].artifact_refs] == ["screenshot", "ui_snapshot"]

    for step in (
        ExecutableStep(step_id="observation-1", kind="observation", action_name="custom_observation"),
        ExecutableStep(step_id="diagnostic-1", kind="diagnostic", action_name="custom_diagnostic"),
    ):
        result = runner.run_step(run_id="run-1", step=step)
        assert result.status == "passed"
        assert [phase.artifact_refs for phase in result.phase_reports] == [[], [], [], []]

    assert "capture:screenshot:before-action:common-1:prepare" in harness.calls
    assert "capture:ui_snapshot:after-action:common-1:finalize" in harness.calls
    assert not any(call.startswith("capture:") and "observation-1" in call for call in harness.calls)
    assert not any(call.startswith("capture:") and "diagnostic-1" in call for call in harness.calls)


def test_step_runner_after_capture_includes_failed_action_without_extra_failure_artifacts() -> None:
    harness = FailedCapturingHarness()
    runner = _runner(harness)
    step = ExecutableStep(
        step_id="step-1",
        kind="action",
        action_name="tapOn",
        params={"target": "Login"},
    )

    result = runner.run_step(run_id="run-1", step=step)

    assert result.status == "failed"
    assert [artifact.kind for artifact in result.phase_reports[0].artifact_refs] == ["screenshot", "ui_snapshot"]
    assert [artifact.kind for artifact in result.phase_reports[3].artifact_refs] == [
        "screenshot",
        "ui_snapshot",
    ]
    assert "capture:screenshot:after-action:step-1:finalize" in harness.calls
    assert not any(":failure:" in call for call in harness.calls)


def test_step_runner_reports_artifact_capture_errors_without_crashing() -> None:
    harness = FailingCaptureHarness()
    runner = _runner(harness)
    step = ExecutableStep(
        step_id="step-1",
        kind="action",
        action_name="tapOn",
        params={"target": "Login"},
    )

    result = runner.run_step(run_id="run-1", step=step)

    finalize_report = result.phase_reports[3]
    assert result.status == "failed"
    assert finalize_report.status == "failed"
    assert finalize_report.failure_category == "artifact_error"
    assert finalize_report.error_message == "RuntimeError: capture failed"


def test_step_runner_includes_exception_type_for_artifact_capture_failures() -> None:
    runner = _runner(FileNotFoundCaptureHarness())
    step = ExecutableStep(
        step_id="step-1",
        kind="action",
        action_name="tapOn",
        params={"target": "Login"},
    )

    result = runner.run_step(run_id="run-1", step=step)

    assert result.status == "failed"
    assert result.failure_category == "artifact_error"
    assert result.error_message.startswith("FileNotFoundError: [Errno 2] No such file or directory")
    assert "C:/local/artifact.png" in result.error_message
    assert "local path length: 21" in result.error_message
