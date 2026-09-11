# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from typing import Any

import pytest

from fsq_agent.core import (
    ArtifactStore,
    CapabilityDefinitionFactory,
    CapabilityRegistry,
    HarnessInterface,
    StepRunner,
)
from fsq_agent.core.harness._ai_assertion_tool import AIAssertionBackendToolMixin
from fsq_agent.core.harness._driver_tools import _windows_driver_tool
from fsq_agent.core.harness._windows import WindowsHarness
from fsq_agent.models import (
    AIAssertionRequest,
    AIAssertionResult,
    ExecutableStep,
    HarnessContext,
    WindowsAssertVisibleParams,
    WindowsAssertWithAIParams,
    WindowsClickOnParams,
    WindowsDoubleClickOnParams,
    WindowsDragToParams,
    WindowsHoverOnParams,
    WindowsKillAppParams,
    WindowsLaunchAppParams,
    WindowsPressKeyParams,
    WindowsRightClickOnParams,
    WindowsScrollOnParams,
    WindowsTypeTextParams,
    WindowsUiSnapshotParams,
)


class FakeWindowsDriver(AIAssertionBackendToolMixin):
    backend = "fake-pywinauto"

    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []

    def context(self) -> dict[str, object]:
        self.calls.append(("context", None))
        return {
            "session_id": "pywinauto:uia",
            "current_url": None,
            "screen_size": (1920, 1080),
            "metadata": {"backend_kind": "uia", "app_path_configured": True},
        }

    def _record(self, method_name: str, params: object) -> dict[str, object]:
        if hasattr(params, "model_dump"):
            recorded = params.model_dump(mode="json", exclude_none=True)
        else:
            recorded = params
        self.calls.append((method_name, recorded))
        return {method_name: True}

    @_windows_driver_tool(
        "launchApp",
        description="Launch the configured Windows desktop application.",
    )
    def launch_app(self, params: WindowsLaunchAppParams) -> dict[str, object]:
        return self._record("launch_app", params)

    @_windows_driver_tool("killApp", description="Stop the launched Windows desktop application.")
    def kill_app(self, params: WindowsKillAppParams) -> dict[str, object]:
        return self._record("kill_app", params)

    @_windows_driver_tool("clickOn", description="Click a Windows control resolved from the UI snapshot.")
    def click_on(self, params: WindowsClickOnParams) -> dict[str, object]:
        return self._record("click_on", params)

    @_windows_driver_tool("doubleClickOn", description="Double-click a Windows control.")
    def double_click_on(self, params: WindowsDoubleClickOnParams) -> dict[str, object]:
        return self._record("double_click_on", params)

    @_windows_driver_tool("rightClickOn", description="Right-click a Windows control.")
    def right_click_on(self, params: WindowsRightClickOnParams) -> dict[str, object]:
        return self._record("right_click_on", params)

    @_windows_driver_tool("typeText", description="Type text into a Windows control.")
    def type_text(self, params: WindowsTypeTextParams) -> dict[str, object]:
        return self._record("type_text", params)

    @_windows_driver_tool("pressKey", description="Send a keyboard key sequence to the active Windows window.")
    def press_key(self, params: WindowsPressKeyParams) -> dict[str, object]:
        return self._record("press_key", params)

    @_windows_driver_tool("hoverOn", description="Move the mouse over a Windows control.")
    def hover_on(self, params: WindowsHoverOnParams) -> dict[str, object]:
        return self._record("hover_on", params)

    @_windows_driver_tool("scrollOn", description="Scroll over a Windows control.")
    def scroll_on(self, params: WindowsScrollOnParams) -> dict[str, object]:
        return self._record("scroll_on", params)

    @_windows_driver_tool("dragTo", description="Drag between Windows controls or points.")
    def drag_to(self, params: WindowsDragToParams) -> dict[str, object]:
        return self._record("drag_to", params)

    @_windows_driver_tool("assertVisible", description="Assert that a Windows control is visible.")
    def assert_visible(self, params: WindowsAssertVisibleParams) -> dict[str, object]:
        return self._record("assert_visible", params)

    @_windows_driver_tool("assertWithAI", description="Evaluate an explicit Windows visual assertion with AI.")
    def assert_with_ai(self, params: WindowsAssertWithAIParams) -> dict[str, object]:
        return self._run_ai_assertion_tool(params)

    @_windows_driver_tool("uiSnapshot", description="Return the current Windows window control tree snapshot.")
    def ui_snapshot(self, params: WindowsUiSnapshotParams) -> dict[str, object]:
        if hasattr(params, "model_dump"):
            recorded = params.model_dump(mode="json", exclude_none=True)
        else:
            recorded = params
        self.calls.append(("ui_snapshot", recorded))
        return {"title": "Notepad", "snapshot_type": "control_tree"}

    def screenshot(self, params: object | None = None) -> bytes:
        self.calls.append(("screenshot", None))
        return b"fake-png"


def _step(action_name: str, params: dict[str, Any] | None = None) -> ExecutableStep:
    return ExecutableStep(step_id="step-1", kind="action", action_name=action_name, params=params or {})


def test_windows_harness_dispatches_fsq_action_names_to_driver() -> None:
    driver = FakeWindowsDriver()
    harness = WindowsHarness(driver=driver)

    context = harness.get_context()

    cases = [
        ("launchApp", {}, "launch_app"),
        ("clickOn", {"target": "Open the File menu", "locator": {"title": "File"}}, "click_on"),
        ("doubleClickOn", {"target": "Open the document", "locator": {"title": "Document", "control_type": "Edit"}}, "double_click_on"),
        ("rightClickOn", {"target": "Open the File context menu", "locator": {"title": "File"}}, "right_click_on"),
        ("typeText", {"target": "Enter document text", "locator": {"title": "Document"}, "text": "hello", "textType": "literal"}, "type_text"),
        ("pressKey", {"key": "^s"}, "press_key"),
        ("hoverOn", {"target": "Hover Save", "locator": {"title": "Save"}}, "hover_on"),
        ("scrollOn", {"target": "Scroll results", "locator": {"automation_id": "Results"}, "wheel_dist": -5}, "scroll_on"),
        (
            "dragTo",
            {
                "target": "Move item",
                "source": {"locator": {"title": "Item"}},
                "destination": {"offset": {"x": 20, "y": 0}},
                "mouse_button": "left",
            },
            "drag_to",
        ),
        ("assertVisible", {"target": "Verify Save is visible", "locator": {"title": "Save"}}, "assert_visible"),
        ("uiSnapshot", {}, "ui_snapshot"),
        ("killApp", {}, "kill_app"),
    ]

    for action_name, params, _method_name in cases:
        result = harness.invoke_action(_step(action_name, params), context)
        assert result.status == "passed"
        assert result.action_name == action_name

    assert isinstance(harness, HarnessInterface)
    assert context == HarnessContext(
        platform="windows",
        session_id="pywinauto:uia",
        current_url=None,
        screen_size=(1920, 1080),
        metadata={"backend_kind": "uia", "app_path_configured": True},
    )
    assert driver.calls == [("context", None)] + [(method_name, params) for _action_name, params, method_name in cases]


def test_windows_harness_action_space_returns_catalog_backed_schemas() -> None:
    harness = WindowsHarness(driver=FakeWindowsDriver())

    schemas = {schema.name: schema for schema in harness.action_space()}

    assert "click_on" in schemas
    assert "ui_snapshot" in schemas
    assert "assert_with_ai" not in schemas
    assert set(schemas["launch_app"].params_json_schema["properties"]) == {"extra_args"}
    assert schemas["click_on"].driver_method == "click_on"
    assert schemas["click_on"].fsq_action_name == "clickOn"
    assert schemas["click_on"].platform == "windows"
    assert schemas["click_on"].metadata["driver_class"] == "FakeWindowsDriver"
    assert schemas["click_on"].metadata["backend"] == "fake-pywinauto"
    assert schemas["click_on"].metadata["replay"] == {"kind": "fsq_command", "alias": "clickOn"}
    assert "locator" in schemas["click_on"].params_json_schema["properties"]
    assert "locator" in schemas["click_on"].params_json_schema["required"]
    assert "target" in schemas["click_on"].params_json_schema["properties"]
    assert "target" not in schemas["click_on"].params_json_schema["required"]
    assert "non-empty locator" in schemas["click_on"].params_json_schema["description"]
    assert "descriptive" in schemas["click_on"].params_json_schema["properties"]["target"]["description"]
    assert schemas["drag_to"].fsq_action_name == "dragTo"
    assert schemas["ui_snapshot"].driver_method == "ui_snapshot"
    assert schemas["ui_snapshot"].fsq_action_name == "uiSnapshot"


@pytest.mark.parametrize("private_param", ["app_path", "wait_for"])
def test_windows_harness_rejects_internal_launch_params(private_param: str) -> None:
    driver = FakeWindowsDriver()
    harness = WindowsHarness(driver=driver)

    result = harness.invoke_action(_step("launchApp", {private_param: "not-exposed"}), harness.get_context())

    assert result.status == "failed"
    assert result.failure_category == "configuration_error"
    assert result.error_message is not None
    assert f"{private_param}: Extra inputs are not permitted" in result.error_message
    assert driver.calls == [("context", None)]


def test_windows_harness_validation_failure_does_not_call_driver_method() -> None:
    driver = FakeWindowsDriver()
    harness = WindowsHarness(driver=driver)

    result = harness.invoke_action(
        _step("clickOn", {"target": "Click Login", "locator": {"unknown": "Login"}}),
        harness.get_context(),
    )

    assert result.status == "failed"
    assert result.failure_category == "configuration_error"
    assert result.error_message == "Invalid Windows parameters for clickOn. locator.unknown: Extra inputs are not permitted"
    assert result.metadata["validation_errors"]
    assert driver.calls == [("context", None)]


def test_windows_harness_reports_invalid_locator_type() -> None:
    driver = FakeWindowsDriver()
    harness = WindowsHarness(driver=driver)

    result = harness.invoke_action(_step("clickOn", {"target": "Click Login", "locator": "Login"}), harness.get_context())

    assert result.status == "failed"
    assert result.failure_category == "configuration_error"
    assert result.error_message is not None
    assert "locator:" in result.error_message
    assert "valid dictionary or instance of WindowsLocator" in result.error_message
    assert result.metadata["validation_errors"][0]["loc"] == ("locator",)
    assert driver.calls == [("context", None)]


def test_windows_harness_reports_none_locator() -> None:
    driver = FakeWindowsDriver()
    harness = WindowsHarness(driver=driver)

    result = harness.invoke_action(_step("clickOn", {"target": "Click Login", "locator": None}), harness.get_context())

    assert result.status == "failed"
    assert result.failure_category == "configuration_error"
    assert result.error_message is not None
    assert "locator:" in result.error_message
    assert "valid dictionary or instance of WindowsLocator" in result.error_message
    assert result.metadata["validation_errors"][0]["loc"] == ("locator",)
    assert driver.calls == [("context", None)]


def test_windows_harness_allows_missing_target() -> None:
    driver = FakeWindowsDriver()
    harness = WindowsHarness(driver=driver)

    result = harness.invoke_action(_step("clickOn", {"locator": {"title": "Login"}}), harness.get_context())

    assert result.status == "passed"
    assert result.failure_category is None
    assert driver.calls == [("context", None), ("click_on", {"locator": {"title": "Login"}})]


def test_windows_mouse_parameter_models_validate_modes_and_distances() -> None:
    params = WindowsDragToParams.model_validate(
        {
            "target": "Move item",
            "source": {"point": {"x": 10, "y": 20}},
            "destination": {"locator": {"automation_id": "DropTarget"}},
        }
    )

    assert params.mouse_button == "left"
    assert params.source.point is not None
    assert params.destination.locator is not None

    with pytest.raises(ValueError, match="exactly one"):
        WindowsDragToParams.model_validate(
            {
                "target": "Move item",
                "source": {"point": {"x": 1, "y": 2}, "locator": {"title": "Item"}},
                "destination": {"point": {"x": 3, "y": 4}},
            }
        )
    with pytest.raises(ValueError, match="greater than or equal to 0"):
        WindowsDragToParams.model_validate(
            {
                "target": "Move item",
                "source": {"point": {"x": -1, "y": 2}},
                "destination": {"point": {"x": 3, "y": 4}},
            }
        )
    with pytest.raises(ValueError, match="non-zero offset"):
        WindowsDragToParams.model_validate(
            {
                "target": "Move item",
                "source": {"point": {"x": 1, "y": 2}},
                "destination": {"offset": {"x": 0, "y": 0}},
            }
        )
    with pytest.raises(ValueError, match="non-zero wheel_dist"):
        WindowsScrollOnParams.model_validate({"target": "Scroll results", "locator": {"title": "Results"}, "wheel_dist": 0})


def test_windows_harness_captures_screenshot_and_ui_snapshot_with_artifact_store(tmp_path) -> None:
    driver = FakeWindowsDriver()
    harness = WindowsHarness(driver=driver, artifact_store=ArtifactStore(run_dir=tmp_path))
    context = harness.get_context()

    screenshot_ref = harness.capture_artifact(
        kind="screenshot",
        reason="after click",
        context=context,
        step_id="step-1",
        phase="invoke",
    )
    snapshot_ref = harness.capture_artifact(
        kind="ui_snapshot",
        reason="after click",
        context=context,
        step_id="step-1",
        phase="finalize",
    )

    assert (tmp_path / screenshot_ref.path).read_bytes() == b"fake-png"
    assert "Notepad" in (tmp_path / snapshot_ref.path).read_text(encoding="utf-8")
    assert driver.calls == [("context", None), ("screenshot", None), ("ui_snapshot", {})]


@pytest.mark.parametrize("phase", ["prepare", "finalize"])
def test_windows_harness_uses_empty_artifacts_when_evidence_window_capture_fails(tmp_path, phase) -> None:
    class UnavailableWindowDriver(FakeWindowsDriver):
        def screenshot(self, params: object | None = None) -> bytes:
            raise RuntimeError("window is unavailable")

        def ui_snapshot(self, params: WindowsUiSnapshotParams) -> dict[str, object]:
            raise RuntimeError("window is unavailable")

    harness = WindowsHarness(
        driver=UnavailableWindowDriver(),
        artifact_store=ArtifactStore(run_dir=tmp_path),
    )
    context = harness.get_context()

    for kind in ("screenshot", "ui_snapshot"):
        with pytest.raises(RuntimeError, match="window is unavailable"):
            harness.capture_artifact(kind=kind, reason="capture", context=context, step_id="step-1", phase=phase)
    assert not list(tmp_path.rglob("*.png"))


def test_windows_harness_does_not_hide_invoke_capture_failures(tmp_path) -> None:
    class UnavailableWindowDriver(FakeWindowsDriver):
        def screenshot(self, params: object | None = None) -> bytes:
            raise RuntimeError("window is unavailable")

    harness = WindowsHarness(
        driver=UnavailableWindowDriver(),
        artifact_store=ArtifactStore(run_dir=tmp_path),
    )

    with pytest.raises(RuntimeError, match="window is unavailable"):
        harness.capture_artifact(
            kind="screenshot",
            reason="ai-assertion",
            context=harness.get_context(),
            step_id="step-1",
            phase="invoke",
        )


@pytest.mark.parametrize(
    ("action_name", "params"),
    [("launchApp", {}), ("killApp", {})],
)
def test_windows_runner_lifecycle_captures_before_and_after_when_window_capture_fails(
    tmp_path,
    action_name,
    params,
) -> None:
    class UnavailableWindowDriver(FakeWindowsDriver):
        def screenshot(self, params: object | None = None) -> bytes:
            raise RuntimeError("window is unavailable")

        def ui_snapshot(self, params: WindowsUiSnapshotParams) -> dict[str, object]:
            raise RuntimeError("window is unavailable")

    definitions = CapabilityDefinitionFactory().platform_definitions(
        platform="windows",
        backend="pywinauto",
        include_ai_assertion=False,
    )
    harness = WindowsHarness(
        driver=UnavailableWindowDriver(),
        artifact_store=ArtifactStore(run_dir=tmp_path),
    )
    runner = StepRunner(
        harness=harness,
        capability_registry=CapabilityRegistry.from_definitions(definitions),
    )

    result = runner.run_step(
        run_id="run-1",
        step=_step(action_name, params),
    )

    assert result.status == "failed"
    assert result.failure_category == "artifact_error"
    assert result.action_status == "passed"
    assert len(result.evidence_errors) == 4
    assert all(ref.availability == "failed" for phase in result.phase_reports for ref in phase.artifact_refs)


def test_windows_harness_assert_with_ai_uses_injected_evaluator(tmp_path) -> None:
    class FakeEvaluator:
        def __init__(self) -> None:
            self.requests: list[AIAssertionRequest] = []

        def evaluate(self, request: AIAssertionRequest) -> AIAssertionResult:
            self.requests.append(request)
            return AIAssertionResult(
                status="passed",
                passed=True,
                explanation="The expected window is visible.",
                provider="fake",
                model="fake-model",
                artifact_refs=[request.screenshot_artifact_ref] if request.screenshot_artifact_ref else [],
            )

    evaluator = FakeEvaluator()
    harness = WindowsHarness(
        driver=FakeWindowsDriver(),
        artifact_store=ArtifactStore(run_dir=tmp_path),
        ai_assertion_evaluator=evaluator,
    )

    result = harness.invoke_action(_step("assertWithAI", {"prompt": "The Save dialog is visible."}), harness.get_context())

    assert result.status == "passed"
    assert result.action_name == "assertWithAI"
    assert len(evaluator.requests) == 1
    assert evaluator.requests[0].platform == "windows"
    assert evaluator.requests[0].prompt == "The Save dialog is visible."


def test_windows_harness_assert_with_ai_requires_evaluator() -> None:
    harness = WindowsHarness(driver=FakeWindowsDriver())

    result = harness.invoke_action(_step("assertWithAI", {"prompt": "anything"}), harness.get_context())

    assert result.status == "failed"
    assert result.failure_category == "configuration_error"


def test_windows_harness_rejects_unknown_action() -> None:
    harness = WindowsHarness(driver=FakeWindowsDriver())

    result = harness.invoke_action(_step("unsupportedAction", {}), harness.get_context())

    assert result.status == "failed"
    assert result.failure_category == "configuration_error"
    assert "Unsupported Windows action" in (result.error_message or "")


def test_windows_harness_classifies_main_window_timeout() -> None:
    harness = WindowsHarness(driver=FakeWindowsDriver())

    category = harness.classify_error(TimeoutError("main window timeout"), "invoke", _step("launchApp"))

    assert category == "timeout_error"


def test_pywinauto_driver_launch_app_uses_launch_args_and_window_title_re(monkeypatch) -> None:
    from fsq_agent.core.harness._pywinauto_driver import PywinautoWindowsDriver
    from fsq_agent.models import WindowsLaunchAppParams

    class FakeWindow:
        def __init__(self) -> None:
            self.waited: list[str] = []

        def wait(self, state: str, timeout: float | None = None) -> "FakeWindow":
            self.waited.append(state)
            return self

    class FakeApp:
        def __init__(self, backend: str) -> None:
            self.backend = backend
            self.started_cmd: str | None = None
            self.connected_title_re: str | None = None
            self.window_title_re: str | None = None
            self.window_control_type: str | None = None
            self.window_obj = FakeWindow()

        def start(self, cmd: str) -> "FakeApp":
            self.started_cmd = cmd
            return self

        def connect(self, title_re: str) -> "FakeApp":
            self.connected_title_re = title_re
            return self

        def window(self, title_re: str, control_type: str | None = None) -> FakeWindow:
            self.window_title_re = title_re
            self.window_control_type = control_type
            return self.window_obj

        def top_window(self) -> FakeWindow:
            return self.window_obj

    fake_app = FakeApp(backend="uia")

    driver = PywinautoWindowsDriver(
        app_path="msedge.exe",
        window_title_re=".*Microsoft.*Edge Beta",
        launch_args=["--no-first-run", "--window-size=1280,920"],
    )
    monkeypatch.setattr(driver, "_application_cls", lambda: lambda backend: fake_app)

    result = driver.launch_app(WindowsLaunchAppParams(extra_args=["--incognito"]))

    assert result["status"] == "passed"
    assert fake_app.started_cmd == "msedge.exe --no-first-run --window-size=1280,920 --incognito"
    assert fake_app.connected_title_re == ".*Microsoft.*Edge Beta"
    assert fake_app.window_title_re == ".*Microsoft.*Edge Beta"
    assert fake_app.window_control_type == "Window"
    assert fake_app.window_obj.waited == ["exists visible enabled"]


def test_pywinauto_driver_main_window_timeout_includes_resolution_context(monkeypatch) -> None:
    from fsq_agent.core.harness._pywinauto_driver import PywinautoWindowsDriver

    class FailingApp:
        def connect(self, **kwargs: object) -> object:
            raise LookupError("window not found")

    driver = PywinautoWindowsDriver(window_title_re=".*Edge Beta")
    monkeypatch.setattr(driver, "_application_cls", lambda: lambda backend: FailingApp())
    monotonic_values = iter([100.0, 131.0])
    monkeypatch.setattr("fsq_agent.core.harness._pywinauto_driver.time.monotonic", lambda: next(monotonic_values))

    with pytest.raises(
        TimeoutError,
        match=r"Timed out after 30\.0 seconds resolving Windows main window.*title_re='\.\*Edge Beta'.*wait_for='exists visible'",
    ) as error:
        driver._resolve_main_window(wait=True)  # type: ignore[attr-defined]

    assert isinstance(error.value.__cause__, LookupError)
    assert "window not found" in str(error.value)


def test_pywinauto_driver_main_window_immediate_failure_includes_resolution_context(monkeypatch) -> None:
    from fsq_agent.core.harness._pywinauto_driver import PywinautoWindowsDriver

    class FailingApp:
        def connect(self, **kwargs: object) -> object:
            raise LookupError("window not found")

    driver = PywinautoWindowsDriver(window_title_re=".*Edge Beta")
    monkeypatch.setattr(driver, "_application_cls", lambda: lambda backend: FailingApp())

    with pytest.raises(
        RuntimeError,
        match=r"Failed to resolve Windows main window.*title_re='\.\*Edge Beta'.*wait_for='exists visible'",
    ) as error:
        driver._resolve_main_window()  # type: ignore[attr-defined]

    assert isinstance(error.value.__cause__, LookupError)


def test_pywinauto_driver_resolves_window_on_every_use() -> None:
    from fsq_agent.core.harness._pywinauto_driver import PywinautoWindowsDriver

    windows = iter([object(), object()])
    driver = PywinautoWindowsDriver()
    driver._resolve_main_window = lambda **kwargs: next(windows)  # type: ignore[method-assign]

    first = driver._require_window()  # type: ignore[attr-defined]
    second = driver._require_window()  # type: ignore[attr-defined]

    assert first is not second
    assert not hasattr(driver, "_window")


def test_pywinauto_driver_context_tolerates_window_closed_after_resolution() -> None:
    from fsq_agent.core.harness._pywinauto_driver import PywinautoWindowsDriver

    class ClosedWindow:
        def rectangle(self) -> object:
            raise RuntimeError("window is closed")

    driver = PywinautoWindowsDriver()
    driver._resolve_main_window = lambda **kwargs: ClosedWindow()  # type: ignore[method-assign]

    context = driver.context()

    assert context["screen_size"] is None


def test_pywinauto_driver_control_falls_back_from_exact_title_to_title_regex() -> None:
    from fsq_agent.core.harness._pywinauto_driver import PywinautoWindowsDriver

    class FakeWrapper:
        pass

    class FakeControl:
        def __init__(self, exists: bool) -> None:
            self._exists = exists
            self.wrapper = FakeWrapper()

        def exists(self) -> bool:
            return self._exists

        def wrapper_object(self) -> FakeWrapper:
            return self.wrapper

    class FakeWindow:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        def child_window(self, **kwargs: object) -> FakeControl:
            self.calls.append(kwargs)
            return FakeControl(exists="title_re" in kwargs)

    window = FakeWindow()
    driver = PywinautoWindowsDriver()
    driver._resolve_main_window = lambda **kwargs: window  # type: ignore[method-assign]

    control = driver._control_from_kwargs(  # type: ignore[attr-defined]
        {"title": "Document.*Notepad", "control_type": "Edit", "automation_id": "15", "index": 2}
    )

    assert isinstance(control, FakeWrapper)
    assert window.calls == [
        {"title": "Document.*Notepad", "control_type": "Edit", "auto_id": "15", "found_index": 1},
        {"control_type": "Edit", "auto_id": "15", "found_index": 1, "title_re": "Document.*Notepad"},
    ]


def test_pywinauto_driver_control_returns_wrapper_for_exact_match() -> None:
    from fsq_agent.core.harness._pywinauto_driver import PywinautoWindowsDriver

    wrapper = object()

    class ExactControl:
        def exists(self) -> bool:
            return True

        def wrapper_object(self) -> object:
            return wrapper

    class FakeWindow:
        def child_window(self, **kwargs: object) -> ExactControl:
            return ExactControl()

    driver = PywinautoWindowsDriver()
    driver._resolve_main_window = lambda **kwargs: FakeWindow()  # type: ignore[method-assign]

    control = driver._control_from_kwargs({"automation_id": "15"})  # type: ignore[attr-defined]

    assert control is wrapper


def test_pywinauto_driver_control_raises_without_title_match() -> None:
    from fsq_agent.core.harness._pywinauto_driver import PywinautoWindowsDriver

    class MissingControl:
        def exists(self) -> bool:
            return False

    class FakeWindow:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        def child_window(self, **kwargs: object) -> MissingControl:
            self.calls.append(kwargs)
            return MissingControl()

    window = FakeWindow()
    driver = PywinautoWindowsDriver()
    driver._resolve_main_window = lambda **kwargs: window  # type: ignore[method-assign]

    with pytest.raises(
        LookupError,
        match=r'Windows control was not found\. query_dict=\{"auto_id": "15", "found_index": 0\}',
    ):
        driver._control_from_kwargs({"automation_id": "15"})  # type: ignore[attr-defined]

    assert window.calls == [{"auto_id": "15", "found_index": 0}]


def test_pywinauto_driver_hover_and_scroll_use_control_center(monkeypatch) -> None:
    from fsq_agent.core.harness._pywinauto_driver import PywinautoWindowsDriver

    class Rectangle:
        def mid_point(self) -> tuple[int, int]:
            return (120, 80)

    class Wrapper:
        def rectangle(self) -> Rectangle:
            return Rectangle()

    class FakeMouse:
        def __init__(self) -> None:
            self.calls: list[tuple[str, object]] = []

        def move(self, *, coords: tuple[int, int]) -> None:
            self.calls.append(("move", coords))

        def scroll(self, *, coords: tuple[int, int], wheel_dist: int) -> None:
            self.calls.append(("scroll", {"coords": coords, "wheel_dist": wheel_dist}))

    mouse = FakeMouse()
    driver = PywinautoWindowsDriver()
    monkeypatch.setattr(driver, "_control", lambda params: Wrapper())
    monkeypatch.setattr(driver, "_mouse_module", lambda: mouse)

    hover_result = driver.hover_on(WindowsHoverOnParams(target="Hover Save", locator={"title": "Save"}))
    scroll_result = driver.scroll_on(WindowsScrollOnParams(target="Scroll results", locator={"title": "Results"}, wheel_dist=-5))

    assert hover_result["status"] == "passed"
    assert scroll_result["status"] == "passed"
    assert mouse.calls == [
        ("move", (120, 80)),
        ("scroll", {"coords": (120, 80), "wheel_dist": -5}),
    ]


def test_pywinauto_driver_mouse_target_does_not_participate_in_lookup(monkeypatch) -> None:
    from fsq_agent.core.harness._pywinauto_driver import PywinautoWindowsDriver

    class Rectangle:
        def mid_point(self) -> tuple[int, int]:
            return (10, 20)

    class Wrapper:
        def rectangle(self) -> Rectangle:
            return Rectangle()

    class FakeMouse:
        def move(self, *, coords: tuple[int, int]) -> None:
            pass

        def scroll(self, *, coords: tuple[int, int], wheel_dist: int) -> None:
            pass

    queries: list[dict[str, object]] = []
    driver = PywinautoWindowsDriver()

    def resolve(locator: dict[str, object]) -> Wrapper:
        queries.append(locator)
        return Wrapper()

    monkeypatch.setattr(driver, "_control_from_kwargs", resolve)
    monkeypatch.setattr(driver, "_mouse_module", FakeMouse)

    driver.hover_on(WindowsHoverOnParams(target="Do not search this text", locator={"automation_id": "Save"}))
    driver.scroll_on(WindowsScrollOnParams(target="Also not a query", locator={"control_type": "List"}, wheel_dist=-1))

    assert queries == [{"automation_id": "Save"}, {"control_type": "List"}]


def test_pywinauto_driver_drag_to_offset_moves_and_releases(monkeypatch) -> None:
    from fsq_agent.core.harness._pywinauto_driver import PywinautoWindowsDriver

    class FakeMouse:
        def __init__(self) -> None:
            self.calls: list[tuple[str, object]] = []

        def press(self, *, coords: tuple[int, int], button: str) -> None:
            self.calls.append(("press", {"coords": coords, "button": button}))

        def move(self, *, coords: tuple[int, int]) -> None:
            self.calls.append(("move", coords))

        def release(self, *, coords: tuple[int, int], button: str) -> None:
            self.calls.append(("release", {"coords": coords, "button": button}))

    mouse = FakeMouse()
    driver = PywinautoWindowsDriver()
    monkeypatch.setattr(driver, "_mouse_module", lambda: mouse)

    result = driver.drag_to(
        WindowsDragToParams(
            target="Move item",
            source={"point": {"x": 10, "y": 20}},
            destination={"offset": {"x": 30, "y": 0}},
            mouse_button="right",
        )
    )

    assert result["status"] == "passed"
    assert mouse.calls[0] == ("press", {"coords": (10, 20), "button": "right"})
    assert mouse.calls[-1] == ("release", {"coords": (40, 20), "button": "right"})
    assert ("move", (40, 20)) in mouse.calls


@pytest.mark.parametrize(
    ("source", "destination", "expected_start", "expected_end"),
    [
        ({"locator": {"automation_id": "Source"}}, {"locator": {"automation_id": "Target"}}, (10, 20), (70, 80)),
        ({"locator": {"automation_id": "Source"}}, {"point": {"x": 50, "y": 60}}, (10, 20), (50, 60)),
        ({"point": {"x": 30, "y": 40}}, {"locator": {"automation_id": "Target"}}, (30, 40), (70, 80)),
        ({"point": {"x": 30, "y": 40}}, {"point": {"x": 50, "y": 60}}, (30, 40), (50, 60)),
    ],
)
def test_pywinauto_driver_drag_supports_locator_and_point_endpoints(
    source: dict[str, object],
    destination: dict[str, object],
    expected_start: tuple[int, int],
    expected_end: tuple[int, int],
    monkeypatch,
) -> None:
    from fsq_agent.core.harness._pywinauto_driver import PywinautoWindowsDriver

    class Rectangle:
        def __init__(self, point: tuple[int, int]) -> None:
            self.point = point

        def mid_point(self) -> tuple[int, int]:
            return self.point

    class Wrapper:
        def __init__(self, point: tuple[int, int]) -> None:
            self.point = point

        def rectangle(self) -> Rectangle:
            return Rectangle(self.point)

    class FakeMouse:
        def __init__(self) -> None:
            self.press_point: tuple[int, int] | None = None
            self.release_point: tuple[int, int] | None = None

        def press(self, *, coords: tuple[int, int], button: str) -> None:
            self.press_point = coords

        def move(self, *, coords: tuple[int, int]) -> None:
            pass

        def release(self, *, coords: tuple[int, int], button: str) -> None:
            self.release_point = coords

    wrappers = {"Source": Wrapper((10, 20)), "Target": Wrapper((70, 80))}
    mouse = FakeMouse()
    driver = PywinautoWindowsDriver()
    monkeypatch.setattr(driver, "_control_from_kwargs", lambda locator: wrappers[locator["automation_id"]])
    monkeypatch.setattr(driver, "_mouse_module", lambda: mouse)

    result = driver.drag_to(WindowsDragToParams(target="Move item", source=source, destination=destination))

    assert result["status"] == "passed"
    assert mouse.press_point == expected_start
    assert mouse.release_point == expected_end


def test_pywinauto_driver_drag_releases_mouse_after_move_failure(monkeypatch) -> None:
    from fsq_agent.core.harness._pywinauto_driver import PywinautoWindowsDriver

    class FailingMouse:
        def __init__(self) -> None:
            self.releases: list[tuple[tuple[int, int], str]] = []

        def press(self, *, coords: tuple[int, int], button: str) -> None:
            pass

        def move(self, *, coords: tuple[int, int]) -> None:
            raise RuntimeError("move failed")

        def release(self, *, coords: tuple[int, int], button: str) -> None:
            self.releases.append((coords, button))
            raise RuntimeError("release failed")

    mouse = FailingMouse()
    driver = PywinautoWindowsDriver()
    monkeypatch.setattr(driver, "_mouse_module", lambda: mouse)

    with pytest.raises(RuntimeError, match="move failed"):
        driver.drag_to(
            WindowsDragToParams(
                target="Move item",
                source={"point": {"x": 10, "y": 20}},
                destination={"point": {"x": 40, "y": 20}},
            )
        )

    assert mouse.releases == [((40, 20), "left")]
