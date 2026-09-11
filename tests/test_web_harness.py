# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from typing import Any

import pytest

from fsq_agent.core import ArtifactStore, HarnessInterface
from fsq_agent.core.harness._ai_assertion_tool import AIAssertionBackendToolMixin
from fsq_agent.core.harness._driver_tools import _web_driver_tool
from fsq_agent.core.harness._web import WebHarness
from fsq_agent.models import (
    AIAssertionRequest,
    AIAssertionResult,
    ExecutableStep,
    HarnessContext,
    WebAssertNotVisibleParams,
    WebAssertTextParams,
    WebAssertVisibleParams,
    WebAssertWithAIParams,
    WebClickOnParams,
    WebCloseBrowserParams,
    WebHoverOnParams,
    WebNavigateBackParams,
    WebNavigateToParams,
    WebPressKeyParams,
    WebSelectOptionParams,
    WebStartBrowserParams,
    WebTakeScreenshotParams,
    WebTypeTextParams,
    WebUiSnapshotParams,
    WebWaitForParams,
)


class FakeWebDriver(AIAssertionBackendToolMixin):
    backend = "fake-playwright"

    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []

    def context(self) -> dict[str, object]:
        self.calls.append(("context", None))
        return {
            "session_id": "web-session-1",
            "current_url": "https://www.bing.com",
            "screen_size": (1280, 720),
            "metadata": {"channel": "chrome", "browser_executable_configured": True},
        }

    def _record(self, method_name: str, params: object) -> dict[str, object]:
        if hasattr(params, "model_dump"):
            recorded = params.model_dump(mode="json", exclude_none=True)
        else:
            recorded = params
        self.calls.append((method_name, recorded))
        return {method_name: True}

    @_web_driver_tool("startBrowser", description="Start or reuse the configured Web browser.")
    def start_browser(self, params: WebStartBrowserParams) -> dict[str, object]:
        return self._record("start_browser", params)

    @_web_driver_tool("closeBrowser", description="Close the active Web browser.")
    def close_browser(self, params: WebCloseBrowserParams) -> dict[str, object]:
        return self._record("close_browser", params)

    @_web_driver_tool("navigateTo", description="Navigate the current Web page to a URL.")
    def navigate_to(self, params: WebNavigateToParams) -> dict[str, object]:
        return self._record("navigate_to", params)

    @_web_driver_tool("navigateBack", description="Navigate the current Web page back in browser history.")
    def navigate_back(self, params: WebNavigateBackParams) -> dict[str, object]:
        return self._record("navigate_back", params)

    @_web_driver_tool("clickOn", description="Click a Web page target resolved from the page snapshot.")
    def click_on(self, params: WebClickOnParams) -> dict[str, object]:
        return self._record("click_on", params)

    @_web_driver_tool("typeText", description="Type text into a Web page target resolved from the page snapshot.")
    def type_text(self, params: WebTypeTextParams) -> dict[str, object]:
        return self._record("type_text", params)

    @_web_driver_tool("selectOption", description="Select an option in a Web select target.")
    def select_option(self, params: WebSelectOptionParams) -> dict[str, object]:
        return self._record("select_option", params)

    @_web_driver_tool("hoverOn", description="Hover over a Web page target resolved from the page snapshot.")
    def hover_on(self, params: WebHoverOnParams) -> dict[str, object]:
        return self._record("hover_on", params)

    @_web_driver_tool("pressKey", description="Press a keyboard key in the current Web page.")
    def press_key(self, params: WebPressKeyParams) -> dict[str, object]:
        return self._record("press_key", params)

    @_web_driver_tool("waitFor", description="Wait for a Web page target, text, URL, or timeout condition.")
    def wait_for(self, params: WebWaitForParams) -> dict[str, object]:
        return self._record("wait_for", params)

    @_web_driver_tool("takeScreenshot", description="Capture a Web page screenshot for evidence or debugging.")
    def take_screenshot(self, params: WebTakeScreenshotParams) -> dict[str, object]:
        return self._record("take_screenshot", params)

    @_web_driver_tool("uiSnapshot", description="Return the current Web page accessibility snapshot.")
    def ui_snapshot(self, params: WebUiSnapshotParams) -> dict[str, object]:
        if hasattr(params, "model_dump"):
            recorded = params.model_dump(mode="json", exclude_none=True)
        else:
            recorded = params
        self.calls.append(("ui_snapshot", recorded))
        return {"url": "https://www.bing.com", "snapshot": {"role": "WebArea", "name": "Bing"}}

    @_web_driver_tool("assertVisible", description="Assert that a Web page target is visible.")
    def assert_visible(self, params: WebAssertVisibleParams) -> dict[str, object]:
        return self._record("assert_visible", params)

    @_web_driver_tool("assertNotVisible", description="Assert that a Web page target is not visible.")
    def assert_not_visible(self, params: WebAssertNotVisibleParams) -> dict[str, object]:
        return self._record("assert_not_visible", params)

    @_web_driver_tool("assertText", description="Assert text on a Web page target.")
    def assert_text(self, params: WebAssertTextParams) -> dict[str, object]:
        return self._record("assert_text", params)

    @_web_driver_tool("assertWithAI", description="Evaluate an explicit Web visual assertion with AI.")
    def assert_with_ai(self, params: WebAssertWithAIParams) -> dict[str, object]:
        return self._run_ai_assertion_tool(params)

    def screenshot(self, params: object | None = None) -> bytes:
        self.calls.append(("screenshot", params.model_dump(mode="json", exclude_none=True) if hasattr(params, "model_dump") else params))
        return b"fake-png"


def _step(action_name: str, params: dict[str, Any] | None = None) -> ExecutableStep:
    return ExecutableStep(step_id="step-1", kind="action", action_name=action_name, params=params or {})


def test_web_harness_dispatches_fsq_action_names_to_driver() -> None:
    driver = FakeWebDriver()
    harness = WebHarness(driver=driver)

    context = harness.get_context()

    cases = [
        ("startBrowser", {}, "start_browser"),
        ("navigateTo", {"url": "https://www.bing.com"}, "navigate_to"),
        ("navigateBack", {}, "navigate_back"),
        ("clickOn", {"target": "Search box"}, "click_on"),
        ("typeText", {"target": "Search box", "text": "playwright", "textType": "literal"}, "type_text"),
        ("selectOption", {"target": "Region", "label": "United States"}, "select_option"),
        ("hoverOn", {"target": "Menu"}, "hover_on"),
        ("pressKey", {"key": "Enter"}, "press_key"),
        ("waitFor", {"text": "Results", "timeout_ms": 5000}, "wait_for"),
        ("takeScreenshot", {}, "take_screenshot"),
        ("uiSnapshot", {}, "ui_snapshot"),
        ("assertVisible", {"target": "Results"}, "assert_visible"),
        ("assertNotVisible", {"target": "Dialog"}, "assert_not_visible"),
        ("assertText", {"target": "Results", "text": {"contains": "playwright"}}, "assert_text"),
        ("closeBrowser", {}, "close_browser"),
    ]

    for action_name, params, _method_name in cases:
        result = harness.invoke_action(_step(action_name, params), context)
        assert result.status == "passed"
        assert result.action_name == action_name

    assert isinstance(harness, HarnessInterface)
    assert context == HarnessContext(
        platform="web",
        session_id="web-session-1",
        current_url="https://www.bing.com",
        screen_size=(1280, 720),
        metadata={"channel": "chrome", "browser_executable_configured": True},
    )
    assert driver.calls == [("context", None)] + [(method_name, params) for _action_name, params, method_name in cases]


def test_web_harness_action_space_returns_catalog_backed_schemas() -> None:
    harness = WebHarness(driver=FakeWebDriver())

    schemas = {schema.name: schema for schema in harness.action_space()}
    click_locator_schema = schemas["click_on"].params_json_schema["$defs"]["WebLocator"]

    assert "click_on" in schemas
    assert "start_browser" in schemas
    assert "close_browser" in schemas
    assert "ui_snapshot" in schemas
    assert "assert_with_ai" not in schemas
    assert schemas["start_browser"].driver_method == "start_browser"
    assert schemas["start_browser"].fsq_action_name == "startBrowser"
    assert schemas["start_browser"].metadata["replay"] == {"kind": "fsq_command", "alias": "startBrowser"}
    assert schemas["close_browser"].driver_method == "close_browser"
    assert schemas["close_browser"].fsq_action_name == "closeBrowser"
    assert schemas["click_on"].driver_method == "click_on"
    assert schemas["click_on"].fsq_action_name == "clickOn"
    assert schemas["click_on"].platform == "web"
    assert schemas["click_on"].metadata["driver_class"] == "FakeWebDriver"
    assert schemas["click_on"].metadata["backend"] == "fake-playwright"
    assert schemas["click_on"].metadata["replay"] == {"kind": "fsq_command", "alias": "clickOn"}
    assert "target" in schemas["click_on"].params_json_schema["properties"]
    assert "target or non-empty locator" in schemas["click_on"].params_json_schema["description"]
    assert "exact snapshot target" in schemas["click_on"].params_json_schema["properties"]["target"]["description"]
    assert "ref" in click_locator_schema["properties"]
    assert schemas["ui_snapshot"].driver_method == "ui_snapshot"
    assert schemas["ui_snapshot"].fsq_action_name == "uiSnapshot"
    assert schemas["ui_snapshot"].params_json_schema.get("properties") == {}


def test_web_harness_validation_failure_does_not_call_driver_method() -> None:
    driver = FakeWebDriver()
    harness = WebHarness(driver=driver)

    result = harness.invoke_action(_step("clickOn", {"locator": {"unknown": "Login"}}), harness.get_context())

    assert result.status == "failed"
    assert result.failure_category == "configuration_error"
    assert result.error_message == "Invalid Web parameters for clickOn."
    assert result.metadata["validation_errors"]
    assert driver.calls == [("context", None)]


def test_web_harness_captures_screenshot_and_ui_snapshot_with_artifact_store(tmp_path) -> None:
    driver = FakeWebDriver()
    harness = WebHarness(driver=driver, artifact_store=ArtifactStore(run_dir=tmp_path))
    context = harness.get_context()

    screenshot_ref = harness.capture_artifact(
        kind="screenshot",
        reason="after click",
        context=context,
        step_id="step-1",
        phase="invoke",
    )
    ui_snapshot_ref = harness.capture_artifact(
        kind="ui_snapshot",
        reason="after click",
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
    assert "WebArea" in (tmp_path / ui_snapshot_ref.path).read_text(encoding="utf-8")
    assert driver.calls == [("context", None), ("screenshot", {}), ("ui_snapshot", {})]


def test_web_harness_assert_with_ai_uses_injected_evaluator(tmp_path) -> None:
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

    driver = FakeWebDriver()
    evaluator = FakeEvaluator()
    harness = WebHarness(driver=driver, artifact_store=ArtifactStore(run_dir=tmp_path), ai_assertion_evaluator=evaluator)
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
    assert evaluator.requests[0].platform == "web"
    assert evaluator.requests[0].prompt == "Verify Bing homepage"
    assert evaluator.requests[0].screenshot_artifact_ref == result.artifact_refs[0]
    assert driver.calls == [("context", None), ("screenshot", {})]


def test_web_harness_requires_artifact_store_for_capture() -> None:
    harness = WebHarness(driver=FakeWebDriver())

    with pytest.raises(RuntimeError, match="Artifact capture requires an ArtifactStore"):
        harness.capture_artifact(
            kind="ui_snapshot",
            reason="after click",
            context=harness.get_context(),
            step_id="step-1",
            phase="finalize",
        )
