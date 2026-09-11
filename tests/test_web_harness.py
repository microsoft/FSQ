# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import json
from pathlib import Path
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
    WebFillTextParams,
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

    @_web_driver_tool("fillText", description="Replace the text in one editable target.")
    def fill_text(self, params: WebFillTextParams) -> dict[str, object]:
        return self._record("fill_text", params)

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
        self._record("take_screenshot", params)
        return {"status": "passed", "output": {"png": b"fake-png", "bytes": 8, "page": params.page}}

    @_web_driver_tool("uiSnapshot", description="Return the current Web page accessibility snapshot.")
    def ui_snapshot(self, params: WebUiSnapshotParams) -> dict[str, object]:
        if hasattr(params, "model_dump"):
            recorded = params.model_dump(mode="json", exclude_none=True)
        else:
            recorded = params
        self.calls.append(("ui_snapshot", recorded))
        page = params.scope.page if params.scope.kind == "page" else params.scope.target.page
        return {
            "status": "passed",
            "output": {
                "schema_version": "fsq.web-observation/v1",
                "observation_id": "snapshot-1",
                "page": {"page": page, "url": "https://www.bing.com", "title": "Bing", "active": True},
                "view": {"regions": [], "elements": [], "lists": [], "dialogs": []},
                "coverage": {"semantic": "complete", "locators": "unavailable", "omissions": []},
                "full_source": {"semantic_text": "WebArea: Bing", "coverage": {"semantic": "complete", "locators": "unavailable"}},
            },
            "metadata": {"action_effect": "not_started"},
        }

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


def _target(name: str, role: str = "button") -> dict[str, object]:
    return {"page": "main", "steps": [{"kind": "role", "role": role, "name": name}]}


def test_web_harness_preserves_precise_failure_and_action_effect(monkeypatch: pytest.MonkeyPatch) -> None:
    driver = FakeWebDriver()
    monkeypatch.setattr(
        driver,
        "_record",
        lambda *_args: {
            "status": "failed",
            "failure_category": "target_ambiguous",
            "error_message": "Two matching controls.",
            "metadata": {"action_effect": "not_started", "match_count": 2},
        },
    )
    harness = WebHarness(driver=driver)

    result = harness.invoke_action(_step("startBrowser"), harness.get_context())

    assert result.status == "failed"
    assert result.failure_category == "target_resolution_error"
    assert result.metadata["error_code"] == "target_ambiguous"
    assert result.metadata["action_effect"] == "not_started"
    assert result.metadata["match_count"] == 2


def test_web_harness_does_not_accept_invalid_driver_status(monkeypatch: pytest.MonkeyPatch) -> None:
    driver = FakeWebDriver()
    monkeypatch.setattr(driver, "_record", lambda *_args: {"status": "broken"})
    harness = WebHarness(driver=driver)

    result = harness.invoke_action(_step("startBrowser"), harness.get_context())

    assert result.status == "failed"
    assert result.failure_category == "harness_error"
    assert result.metadata["action_effect"] == "indeterminate"


def test_web_harness_failed_observation_is_not_successful_capture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    driver = FakeWebDriver()
    monkeypatch.setattr(driver, "ui_snapshot", lambda _params: {"status": "failed", "error_message": "Semantic observation unavailable."})
    harness = WebHarness(driver=driver, artifact_store=ArtifactStore(run_dir=tmp_path))
    step = _step("startBrowser")

    with pytest.raises(RuntimeError, match="Semantic observation unavailable") as failure:
        harness.capture_artifact("ui_snapshot", "after-action", harness.get_context(), step.step_id, "finalize")
    assert harness.classify_error(failure.value, "finalize", step) == "observation_error"


def test_web_harness_dispatches_fsq_action_names_to_driver(tmp_path: Path) -> None:
    driver = FakeWebDriver()
    harness = WebHarness(driver=driver, artifact_store=ArtifactStore(run_dir=tmp_path))

    context = harness.get_context()

    cases = [
        ("startBrowser", {}, "start_browser"),
        ("navigateTo", {"page": "main", "url": "https://www.bing.com"}, "navigate_to"),
        ("navigateBack", {"page": "main"}, "navigate_back"),
        ("clickOn", {"target": _target("Search box", "textbox")}, "click_on"),
        ("fillText", {"target": _target("Search box", "textbox"), "text": "replace"}, "fill_text"),
        ("typeText", {"target": _target("Search box", "textbox"), "text": "playwright", "textType": "literal"}, "type_text"),
        ("selectOption", {"target": _target("Region", "combobox"), "selection": {"kind": "label", "labels": ["United States"]}}, "select_option"),
        ("hoverOn", {"target": _target("Menu")}, "hover_on"),
        ("pressKey", {"scope": {"kind": "page", "page": "main"}, "key": "Enter"}, "press_key"),
        ("waitFor", {"condition": {"kind": "element", "target": _target("Results")}, "timeout_ms": 5000}, "wait_for"),
        ("takeScreenshot", {"page": "main"}, "take_screenshot"),
        ("uiSnapshot", {"scope": {"kind": "page", "page": "main"}}, "ui_snapshot"),
        ("assertVisible", {"target": _target("Results")}, "assert_visible"),
        ("assertNotVisible", {"target": _target("Dialog", "dialog")}, "assert_not_visible"),
        ("assertText", {"target": _target("Results"), "text": {"kind": "contains", "value": "playwright"}}, "assert_text"),
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
    from fsq_agent.models import WEB_ACTION_DEFINITIONS

    models = {definition.driver_method: definition.params_model for definition in WEB_ACTION_DEFINITIONS}
    assert driver.calls == [("context", None)] + [(method_name, models[method_name].model_validate(params).model_dump(mode="json", exclude_none=True)) for _action_name, params, method_name in cases]


def test_explicit_screenshot_persists_the_single_capture(tmp_path: Path) -> None:
    driver = FakeWebDriver()
    harness = WebHarness(driver=driver, artifact_store=ArtifactStore(run_dir=tmp_path))
    result = harness.invoke_action(_step("takeScreenshot", {"page": "main"}), harness.get_context())

    assert result.status == "passed"
    assert result.output == {"bytes": 8, "page": "main"}
    assert len(result.artifact_refs) == 1
    assert result.artifact_refs[0].kind == "screenshot"
    assert (tmp_path / result.artifact_refs[0].path).read_bytes() == b"fake-png"
    assert [name for name, _ in driver.calls] == ["context", "take_screenshot"]
    assert "fake-png" not in result.model_dump_json()


def test_explicit_screenshot_requires_storage_before_capture() -> None:
    driver = FakeWebDriver()
    harness = WebHarness(driver=driver)
    result = harness.invoke_action(_step("takeScreenshot", {"page": "main"}), harness.get_context())

    assert result.status == "failed"
    assert result.failure_category == "configuration_error"
    assert [name for name, _ in driver.calls] == ["context"]


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
    assert "replay preparation" in schemas["click_on"].params_json_schema["description"]
    assert "replayable locator" in schemas["click_on"].params_json_schema["properties"]["target"]["description"]
    assert set(click_locator_schema["properties"]) == {"page", "steps"}
    assert schemas["ui_snapshot"].driver_method == "ui_snapshot"
    assert schemas["ui_snapshot"].fsq_action_name == "uiSnapshot"
    assert "scope" in schemas["ui_snapshot"].params_json_schema["required"]


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
    assert ui_snapshot_ref.metadata["snapshot"]["observation_id"] == "snapshot-1"
    assert "full_source" not in ui_snapshot_ref.metadata["snapshot"]
    assert driver.calls == [
        ("context", None),
        ("screenshot", {"page": "main", "full_page": False, "omit_background": False}),
        ("ui_snapshot", WebUiSnapshotParams(scope={"kind": "page", "page": "main"}).model_dump(mode="json", exclude_none=True)),
    ]


def test_web_harness_persists_full_observation_before_exposing_compact_view(tmp_path: Path) -> None:
    driver = FakeWebDriver()
    harness = WebHarness(driver=driver, artifact_store=ArtifactStore(run_dir=tmp_path))

    result = harness.invoke_action(_step("uiSnapshot", {"scope": {"kind": "page", "page": "main"}, "view": "full"}), harness.get_context())

    assert result.status == "passed"
    assert "full_source" not in result.output
    assert len(json.dumps(result.output, ensure_ascii=False)) <= 12_000
    assert len(result.artifact_refs) == 1
    assert result.output["full_artifact_ref"] == str(result.artifact_refs[0].path)
    saved = json.loads((tmp_path / result.artifact_refs[0].path).read_text(encoding="utf-8"))
    assert saved["full_source"]["semantic_text"] == "WebArea: Bing"
    assert saved["snapshot"]["observation_id"] == result.output["observation_id"]
    assert [name for name, _params in driver.calls].count("ui_snapshot") == 1


def test_web_harness_full_observation_requires_artifact_storage() -> None:
    harness = WebHarness(driver=FakeWebDriver())

    result = harness.invoke_action(_step("uiSnapshot", {"scope": {"kind": "page", "page": "main"}, "view": "full"}), harness.get_context())

    assert result.status == "failed"
    assert result.failure_category == "configuration_error"
    assert result.metadata["action_effect"] == "not_started"


def test_web_harness_capture_uses_declared_active_page(tmp_path: Path) -> None:
    driver = FakeWebDriver()
    harness = WebHarness(driver=driver, artifact_store=ArtifactStore(run_dir=tmp_path))
    context = HarnessContext(platform="web", metadata={"active_page": "details"})

    ref = harness.capture_artifact("ui_snapshot", "after-action", context, "step-2", "finalize")

    assert driver.calls[-1][1]["scope"] == {"kind": "page", "page": "details"}
    assert ref.metadata["snapshot"]["page"]["page"] == "details"


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
    assert driver.calls == [("context", None), ("screenshot", {"page": "main", "full_page": False, "omit_background": False})]


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
