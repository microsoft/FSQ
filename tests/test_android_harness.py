# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from typing import Any

import pytest

from fsq_agent.core import ArtifactStore, HarnessInterface
from fsq_agent.core.harness._ai_assertion_tool import AIAssertionBackendToolMixin
from fsq_agent.core.harness._android import AndroidHarness
from fsq_agent.core.harness._uiautomator2_driver import UiAutomator2AndroidDriver
from fsq_agent.drivers._capabilities import _android_driver_tool
from fsq_agent.models import (
    AIAssertionRequest,
    AIAssertionResult,
    AndroidAssertNotVisibleParams,
    AndroidAssertStateParams,
    AndroidAssertVisibleParams,
    AndroidAssertWithAIParams,
    AndroidInputTextParams,
    AndroidKillAppParams,
    AndroidLaunchAppParams,
    AndroidLongPressOnParams,
    AndroidPerformActionsParams,
    AndroidPressKeyParams,
    AndroidSwipeParams,
    AndroidTapAtParams,
    AndroidTapOnParams,
    AndroidUiTreeParams,
    ExecutableStep,
    HarnessContext,
)


class FakeAndroidDriver(AIAssertionBackendToolMixin):
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []

    def context(self) -> dict[str, object]:
        self.calls.append(("context", None))
        return {
            "session_id": "android-session-1",
            "current_activity": "MainActivity",
            "screen_size": (1080, 2400),
        }

    def _record(self, method_name: str, params: object) -> dict[str, object]:
        if hasattr(params, "model_dump"):
            recorded = params.model_dump(mode="json", exclude_none=True)
        else:
            recorded = params
        self.calls.append((method_name, recorded))
        return {method_name: True}

    @_android_driver_tool("launchApp", description="Test Android capability launch_app")
    def launch_app(self, params: AndroidLaunchAppParams) -> dict[str, object]:
        return self._record("launch_app", params)

    @_android_driver_tool("killApp", description="Test Android capability kill_app")
    def kill_app(self, params: AndroidKillAppParams) -> dict[str, object]:
        return self._record("kill_app", params)

    @_android_driver_tool("tapOn", description="Test Android capability tap_on")
    def tap_on(self, params: AndroidTapOnParams) -> dict[str, object]:
        return self._record("tap_on", params)

    @_android_driver_tool("tapAt", description="Test Android capability tap_at")
    def tap_at(self, params: AndroidTapAtParams) -> dict[str, object]:
        return self._record("tap_at", params)

    @_android_driver_tool("longPressOn", description="Test Android capability long_press_on")
    def long_press_on(self, params: AndroidLongPressOnParams) -> dict[str, object]:
        return self._record("long_press_on", params)

    @_android_driver_tool("inputText", description="Test Android capability input_text")
    def input_text(self, params: AndroidInputTextParams) -> dict[str, object]:
        return self._record("input_text", params)

    @_android_driver_tool("pressKey", description="Test Android capability press_key")
    def press_key(self, params: AndroidPressKeyParams) -> dict[str, object]:
        return self._record("press_key", params)

    @_android_driver_tool("swipe", description="Test Android capability swipe")
    def swipe(self, params: AndroidSwipeParams) -> dict[str, object]:
        return self._record("swipe", params)

    def perform_actions(self, params: AndroidPerformActionsParams) -> dict[str, object]:
        return self._record("perform_actions", params)

    @_android_driver_tool("assertVisible", description="Test Android capability assert_visible")
    def assert_visible(self, params: AndroidAssertVisibleParams) -> dict[str, object]:
        return self._record("assert_visible", params)

    @_android_driver_tool("assertNotVisible", description="Test Android capability assert_not_visible")
    def assert_not_visible(self, params: AndroidAssertNotVisibleParams) -> dict[str, object]:
        return self._record("assert_not_visible", params)

    @_android_driver_tool("assert", description="Test Android capability assert_state")
    def assert_state(self, params: AndroidAssertStateParams) -> dict[str, object]:
        return self._record("assert_state", params)

    @_android_driver_tool("assertWithAI", description="Test Android capability assert_with_ai")
    def assert_with_ai(self, params: AndroidAssertWithAIParams) -> dict[str, object]:
        return self._run_ai_assertion_tool(params)

    def screenshot(self, params: object | None = None) -> bytes:
        self.calls.append(("screenshot", None))
        return b"fake-png"

    @_android_driver_tool("uiTree", description="Test Android capability ui_snapshot")
    def ui_snapshot(self, params: AndroidUiTreeParams) -> dict[str, object]:
        if hasattr(params, "model_dump"):
            recorded = params.model_dump(mode="json", exclude_none=True)
        else:
            recorded = params
        self.calls.append(("ui_snapshot", recorded))
        return {"nodes": [{"text": "Login"}]}


def _step(action_name: str, params: dict[str, Any] | None = None) -> ExecutableStep:
    return ExecutableStep(
        step_id="step-1",
        kind="action",
        action_name=action_name,
        params=params or {},
    )


def test_android_harness_dispatches_fsq_action_names_to_driver() -> None:
    driver = FakeAndroidDriver()
    harness = AndroidHarness(driver=driver)

    context = harness.get_context()

    cases = [
        ("launchApp", {}, "launch_app"),
        ("killApp", {}, "kill_app"),
        ("tapOn", {"target": "Menu"}, "tap_on"),
        ("tapAt", {"point": {"x": 100, "y": 200}}, "tap_at"),
        ("assertVisible", {"target": "Menu"}, "assert_visible"),
        ("inputText", {"text": "bing.com", "textType": "literal", "target": "Search box"}, "input_text"),
        ("longPressOn", {"target": "Address bar"}, "long_press_on"),
        ("swipe", {"direction": "up", "duration": 1000}, "swipe"),
        ("uiTree", {}, "ui_snapshot"),
        ("assertNotVisible", {"target": "Dialog"}, "assert_not_visible"),
        ("assert", {"text": {"contains": "bing.com"}}, "assert_state"),
    ]

    for action_name, params, _method_name in cases:
        result = harness.invoke_action(_step(action_name, params), context)
        assert result.status == "passed"
        assert result.action_name == action_name

    assert isinstance(harness, HarnessInterface)
    assert context == HarnessContext(
        platform="android",
        session_id="android-session-1",
        current_activity="MainActivity",
        screen_size=(1080, 2400),
    )
    assert driver.calls == [("context", None)] + [(method_name, params) for _action_name, params, method_name in cases]


def test_android_harness_accepts_structured_press_key_params() -> None:
    driver = FakeAndroidDriver()
    harness = AndroidHarness(driver=driver)

    result = harness.invoke_action(_step("pressKey", {"key": "Back"}), harness.get_context())

    assert result.status == "passed"
    assert driver.calls[-1] == ("press_key", {"key": "Back"})


def test_android_harness_does_not_expose_unimplemented_perform_actions() -> None:
    driver = FakeAndroidDriver()
    harness = AndroidHarness(driver=driver)
    actions = [{"type": "none", "id": "wait", "actions": [{"type": "pause", "duration": 1}]}]

    result = harness.invoke_action(_step("performActions", {"actions": actions}), harness.get_context())

    assert result.status == "failed"
    assert result.failure_category == "configuration_error"
    assert result.error_message == "Unsupported Android action: performActions"
    assert driver.calls == [("context", None)]


def test_android_harness_rejects_legacy_value_wrapped_known_params() -> None:
    driver = FakeAndroidDriver()
    harness = AndroidHarness(driver=driver)

    result = harness.invoke_action(_step("pressKey", {"value": "Back"}), harness.get_context())

    assert result.status == "failed"
    assert result.failure_category == "configuration_error"
    assert result.error_message == "Invalid Android parameters for pressKey."
    assert result.metadata["validation_errors"]
    assert driver.calls == [("context", None)]


def test_android_harness_action_space_returns_decorated_driver_method_schemas() -> None:
    driver = UiAutomator2AndroidDriver(app_id="com.example.app", device=object())
    harness = AndroidHarness(driver=driver)

    schemas = {schema.name: schema for schema in harness.action_space()}

    assert "tap_on" in schemas
    assert "tap_at" in schemas
    assert "ui_snapshot" in schemas
    assert "perform_actions" not in schemas
    assert "assert_with_ai" not in schemas
    assert schemas["tap_on"].driver_method == "tap_on"
    assert schemas["tap_on"].fsq_action_name == "tapOn"
    assert schemas["tap_on"].platform == "android"
    assert schemas["tap_on"].metadata["driver_class"] == "UiAutomator2AndroidDriver"
    assert schemas["tap_on"].metadata["backend"] == "uiautomator2"
    assert schemas["tap_on"].metadata["capability_name"] == "tap_on"
    assert schemas["tap_on"].metadata["executor_kind"] == "driver"
    assert schemas["tap_on"].metadata["replay"] == {"kind": "fsq_command", "alias": "tapOn"}
    assert "target" in schemas["tap_on"].params_json_schema["properties"]
    assert "target or non-empty locator" in schemas["tap_on"].params_json_schema["description"]
    assert "semantic target" in schemas["tap_on"].params_json_schema["properties"]["target"]["description"]
    assert schemas["tap_at"].driver_method == "tap_at"
    assert schemas["tap_at"].fsq_action_name == "tapAt"
    assert "point" in schemas["tap_at"].params_json_schema["properties"]
    assert schemas["ui_snapshot"].driver_method == "ui_snapshot"
    assert schemas["ui_snapshot"].fsq_action_name == "uiTree"
    assert schemas["ui_snapshot"].params_json_schema.get("properties") == {}


def test_android_harness_validation_failure_does_not_call_driver_method() -> None:
    driver = FakeAndroidDriver()
    harness = AndroidHarness(driver=driver)

    result = harness.invoke_action(_step("tapOn", {"locator": {"unknown": "Login"}}), harness.get_context())

    assert result.status == "failed"
    assert result.failure_category == "configuration_error"
    assert result.error_message == "Invalid Android parameters for tapOn."
    assert result.metadata["validation_errors"]
    assert driver.calls == [("context", None)]


def test_android_harness_converts_driver_failure_result() -> None:
    class FailingDriver(FakeAndroidDriver):
        @_android_driver_tool("tapOn", description="Test failed Android tap")
        def tap_on(self, params: AndroidTapOnParams) -> dict[str, object]:
            self.calls.append(("tap_on", params))
            return {
                "status": "failed",
                "output": {"matched": False},
                "error_message": "Target was not found.",
                "failure_category": "target_resolution_error",
                "metadata": {"backend": "fake"},
            }

    driver = FailingDriver()
    harness = AndroidHarness(driver=driver)

    result = harness.invoke_action(_step("tapOn", {"target": "Missing"}), harness.get_context())

    assert result.status == "failed"
    assert result.action_name == "tapOn"
    assert result.output == {"matched": False}
    assert result.error_message == "Target was not found."
    assert result.failure_category == "target_resolution_error"
    assert result.metadata == {"backend": "fake"}


def test_android_harness_returns_failed_result_for_unsupported_action() -> None:
    driver = FakeAndroidDriver()
    harness = AndroidHarness(driver=driver)

    result = harness.invoke_action(_step("doubleTapOn", {"target": "Menu"}), harness.get_context())

    assert result.status == "failed"
    assert result.failure_category == "configuration_error"
    assert result.error_message == "Unsupported Android action: doubleTapOn"
    assert driver.calls == [("context", None)]


def test_android_harness_captures_screenshot_and_ui_snapshot_with_artifact_store(tmp_path) -> None:
    driver = FakeAndroidDriver()
    harness = AndroidHarness(driver=driver, artifact_store=ArtifactStore(run_dir=tmp_path))
    context = harness.get_context()

    screenshot_ref = harness.capture_artifact(
        kind="screenshot",
        reason="after tap",
        context=context,
        step_id="step-1",
        phase="invoke",
    )
    ui_snapshot_ref = harness.capture_artifact(
        kind="ui_snapshot",
        reason="after tap",
        context=context,
        step_id="step-1",
        phase="finalize",
    )

    assert screenshot_ref.path.parent.as_posix() == "artifacts/screenshots"
    assert (tmp_path / screenshot_ref.path).is_file()
    assert screenshot_ref.sha256 is not None
    assert (tmp_path / screenshot_ref.path).read_bytes() == b"fake-png"
    assert ui_snapshot_ref.path.parent.as_posix() == "artifacts/ui-snapshots"
    assert (tmp_path / ui_snapshot_ref.path).is_file()
    assert ui_snapshot_ref.sha256 is not None
    assert "Login" in (tmp_path / ui_snapshot_ref.path).read_text(encoding="utf-8")
    assert driver.calls == [
        ("context", None),
        ("screenshot", None),
        ("ui_snapshot", {}),
    ]


def test_android_harness_assert_with_ai_fails_in_deterministic_core(tmp_path) -> None:
    driver = FakeAndroidDriver()
    harness = AndroidHarness(driver=driver, artifact_store=ArtifactStore(run_dir=tmp_path))
    context = harness.get_context()

    result = harness.invoke_action(_step("assertWithAI", {"prompt": "Verify Bing homepage"}), context)

    assert result.status == "failed"
    assert result.action_name == "assertWithAI"
    assert result.failure_category == "configuration_error"
    assert "AI assertion evaluator" in result.error_message
    assert result.artifact_refs == []
    assert driver.calls == [("context", None)]


def test_android_harness_assert_with_ai_uses_injected_evaluator(tmp_path) -> None:
    class FakeEvaluator:
        def __init__(self) -> None:
            self.requests: list[AIAssertionRequest] = []

        def evaluate(self, request: AIAssertionRequest) -> AIAssertionResult:
            self.requests.append(request)
            return AIAssertionResult(
                status="passed",
                passed=True,
                explanation="The expected page is visible.",
                provider="fake",
                model="fake-model",
                artifact_refs=[request.screenshot_artifact_ref] if request.screenshot_artifact_ref else [],
            )

    driver = FakeAndroidDriver()
    evaluator = FakeEvaluator()
    harness = AndroidHarness(
        driver=driver,
        artifact_store=ArtifactStore(run_dir=tmp_path),
        ai_assertion_evaluator=evaluator,
    )
    context = harness.get_context()

    schemas = {schema.name: schema for schema in harness.action_space()}
    result = harness.invoke_action(_step("assertWithAI", {"prompt": "Verify Bing homepage"}), context)

    assert "assert_with_ai" in schemas
    assert schemas["assert_with_ai"].metadata["owner"] == "driver"
    assert result.status == "passed"
    assert result.output["passed"] is True
    assert result.metadata["ai_assertion"]["provider"] == "fake"
    assert result.artifact_refs[0].kind == "screenshot"
    assert (tmp_path / result.artifact_refs[0].path).read_bytes() == b"fake-png"
    assert evaluator.requests[0].prompt == "Verify Bing homepage"
    assert evaluator.requests[0].screenshot_artifact_ref == result.artifact_refs[0]
    assert driver.calls == [("context", None), ("screenshot", None)]


def test_android_harness_requires_artifact_store_for_capture() -> None:
    harness = AndroidHarness(driver=FakeAndroidDriver())

    with pytest.raises(RuntimeError, match="Artifact capture requires an ArtifactStore"):
        harness.capture_artifact(
            kind="screenshot",
            reason="after tap",
            context=harness.get_context(),
            step_id="step-1",
            phase="finalize",
        )
