# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import builtins
from io import BytesIO
from typing import Any
from xml.etree import ElementTree

import pytest

from fsq_agent.core import AndroidDriverInterface
from fsq_agent.core.harness._driver_tools import _discover_driver_capability_definitions
from fsq_agent.core.harness._uiautomator2_driver import UiAutomator2AndroidDriver
from fsq_agent.models import AndroidAssertWithAIParams, ConfigurationError, ReplayPolicy


class FakeSelector:
    def __init__(self, device: "FakeDevice", query: dict[str, object], exists: bool | None = None) -> None:
        self.device = device
        self.query = query
        self.exists = device.exists if exists is None else exists

    def wait(self, **kwargs: object) -> bool:
        self.device.calls.append(("wait", self.query, kwargs))
        return self.device.wait_result

    def wait_gone(self, **kwargs: object) -> bool:
        self.device.calls.append(("wait_gone", self.query, kwargs))
        return self.device.wait_gone_result

    def click(self) -> None:
        self.device.calls.append(("click", self.query))

    def long_click(self) -> None:
        self.device.calls.append(("long_click", self.query))

    def set_text(self, text: str) -> None:
        self.device.calls.append(("set_text", self.query, text))

    def clear_text(self) -> None:
        self.device.calls.append(("clear_text", self.query))

    def get_text(self) -> str:
        self.device.calls.append(("get_text", self.query))
        return self.device.text

    def info(self) -> dict[str, object]:
        self.device.calls.append(("info", self.query))
        return dict(self.device.selector_info)


class FakeXPathSelector(FakeSelector):
    def wait(self, **kwargs: object) -> bool:
        self.device.calls.append(("xpath_wait", self.query, kwargs))
        if "exists" in kwargs:
            raise TypeError("xpath wait does not accept exists")
        return self.device.wait_result


class FakeDevice:
    def __init__(
        self,
        *,
        exists: bool = True,
        text: str = "Loaded",
        wait_result: bool = True,
        wait_gone_result: bool = True,
        hierarchy: str = "<hierarchy />",
    ) -> None:
        self.exists = exists
        self.text = text
        self.wait_result = wait_result
        self.wait_gone_result = wait_gone_result
        self.hierarchy = hierarchy
        self.calls: list[tuple[Any, ...]] = []
        self.selector_info: dict[str, object] = {
            "enabled": True,
            "checked": False,
            "selected": False,
            "clickable": True,
            "focused": False,
        }
        self.info = {
            "displayWidth": 1080,
            "displayHeight": 2400,
            "currentPackageName": "com.example.app",
        }

    def __call__(self, **query: object) -> FakeSelector:
        self.calls.append(("select", query))
        return FakeSelector(self, query)

    def xpath(self, value: str) -> FakeSelector:
        self.calls.append(("xpath", value))
        return FakeXPathSelector(self, {"xpath": value})

    def app_start(self, app_id: str, **options: object) -> None:
        self.calls.append(("app_start", app_id, options))

    def app_stop(self, app_id: str) -> None:
        self.calls.append(("app_stop", app_id))

    def press(self, key: str) -> None:
        self.calls.append(("press", key))

    def click(self, x: int, y: int) -> None:
        self.calls.append(("click", x, y))

    def swipe(self, sx: int, sy: int, ex: int, ey: int, duration: float) -> None:
        self.calls.append(("swipe", sx, sy, ex, ey, duration))

    # Match uiautomator2's public keyword exactly so the fake verifies call compatibility.
    def screenshot(self, format: str = "raw") -> bytes:  # noqa: A002
        self.calls.append(("screenshot", format))
        return FakeImage()

    def dump_hierarchy(self) -> str:
        self.calls.append(("dump_hierarchy",))
        return self.hierarchy


class FakeCompressedDevice(FakeDevice):
    def __init__(self, *, compressed_hierarchy: str, hierarchy: str = "<hierarchy />") -> None:
        super().__init__(hierarchy=hierarchy)
        self.compressed_hierarchy = compressed_hierarchy

    def dump_hierarchy(self, compressed: bool = False) -> str:
        self.calls.append(("dump_hierarchy", compressed))
        if compressed:
            return self.compressed_hierarchy
        return self.hierarchy


class FakeImage:
    # Match Pillow's public keyword exactly so the fake verifies call compatibility.
    def save(self, output: BytesIO, format: str) -> None:  # noqa: A002
        output.write(f"fake-{format.lower()}".encode())


def test_uiautomator2_driver_context_launch_kill_and_artifacts() -> None:
    device = FakeDevice()
    driver = UiAutomator2AndroidDriver(app_id="com.example.app", device=device)

    context = driver.context()

    assert isinstance(driver, AndroidDriverInterface)
    assert context["session_id"] == "uiautomator2:fake-device"
    assert context["screen_size"] == (1080, 2400)
    assert driver.launch_app({})["status"] == "passed"
    assert driver.kill_app({})["status"] == "passed"
    assert driver.screenshot() == b"fake-png"
    assert driver.ui_snapshot({})["xml"] == "<hierarchy />"
    assert device.calls == [
        ("app_start", "com.example.app", {}),
        ("app_stop", "com.example.app"),
        ("screenshot", "pillow"),
        ("dump_hierarchy",),
    ]


def test_uiautomator2_driver_actions_use_locator_selectors() -> None:
    device = FakeDevice(text="bing.com")
    driver = UiAutomator2AndroidDriver(app_id="com.example.app", device=device)

    assert driver.tap_on({"locator": {"resourceId": "login"}})["status"] == "passed"
    assert driver.tap_at({"point": {"x": 100, "y": 200}})["status"] == "passed"
    assert driver.long_press_on({"locator": {"accessibilityId": "Menu"}})["status"] == "passed"
    assert driver.input_text({"text": "hello", "locator": {"text": "Search"}})["status"] == "passed"
    assert driver.press_key({"key": "Back"})["status"] == "passed"
    assert driver.swipe({"direction": "up", "duration": 100})["status"] == "passed"
    assert driver.assert_state({"element": {"resourceId": "url"}, "text": {"contains": "bing"}})["status"] == "passed"

    assert device.calls[:13] == [
        ("select", {"resourceId": "login"}),
        ("wait", {"resourceId": "login"}, {"exists": True, "timeout": 10.0}),
        ("click", {"resourceId": "login"}),
        ("click", 100, 200),
        ("select", {"description": "Menu"}),
        ("wait", {"description": "Menu"}, {"exists": True, "timeout": 10.0}),
        ("long_click", {"description": "Menu"}),
        ("select", {"text": "Search"}),
        ("wait", {"text": "Search"}, {"exists": True, "timeout": 10.0}),
        ("click", {"text": "Search"}),
        ("clear_text", {"text": "Search"}),
        ("set_text", {"text": "Search"}, "hello"),
        ("press", "back"),
    ]


def test_uiautomator2_driver_supports_point_based_swipe() -> None:
    device = FakeDevice()
    driver = UiAutomator2AndroidDriver(app_id="com.example.app", device=device)

    result = driver.swipe(
        {
            "start": {"x": 800, "y": 1900},
            "end": {"x": 200, "y": 1900},
            "duration": 1000,
        }
    )

    assert result == {
        "status": "passed",
        "output": {"start": {"x": 800, "y": 1900}, "end": {"x": 200, "y": 1900}},
    }
    assert device.calls == [("swipe", 800, 1900, 200, 1900, 1.0)]


def test_uiautomator2_driver_supports_point_based_tap() -> None:
    device = FakeDevice()
    driver = UiAutomator2AndroidDriver(app_id="com.example.app", device=device)

    result = driver.tap_at({"point": {"x": 320, "y": 640}})

    assert result == {"status": "passed", "output": {"point": {"x": 320, "y": 640}}}
    assert device.calls == [("click", 320, 640)]


def test_uiautomator2_driver_scales_point_based_tap_from_reference_screen_size() -> None:
    device = FakeDevice()
    device.info["displayWidth"] = 540
    device.info["displayHeight"] = 1200
    driver = UiAutomator2AndroidDriver(app_id="com.example.app", device=device)

    result = driver.tap_at(
        {
            "point": {"x": 100, "y": 200},
            "reference_screen_size": {"width": 1080, "height": 2400},
        }
    )

    assert result == {"status": "passed", "output": {"point": {"x": 50, "y": 100}}}
    assert device.calls == [("click", 50, 100)]


def test_uiautomator2_driver_scales_point_based_swipe_from_reference_screen_size() -> None:
    device = FakeDevice()
    device.info["displayWidth"] = 540
    device.info["displayHeight"] = 1200
    driver = UiAutomator2AndroidDriver(app_id="com.example.app", device=device)

    result = driver.swipe(
        {
            "start": {"x": 800, "y": 1900},
            "end": {"x": 200, "y": 1900},
            "reference_screen_size": {"width": 1080, "height": 2400},
            "duration": 1000,
        }
    )

    assert result == {
        "status": "passed",
        "output": {"start": {"x": 400, "y": 950}, "end": {"x": 100, "y": 950}},
    }
    assert device.calls == [("swipe", 400, 950, 100, 950, 1.0)]


def test_uiautomator2_driver_exposes_ui_snapshot_output_with_ui_tree_alias() -> None:
    device = FakeDevice()
    driver = UiAutomator2AndroidDriver(app_id="com.example.app", device=device)

    result = driver.ui_snapshot({})

    assert result["xml"] == "<hierarchy />"
    assert device.calls == [("dump_hierarchy",)]


def test_uiautomator2_driver_uses_compressed_hierarchy_when_supported() -> None:
    device = FakeCompressedDevice(
        hierarchy='<hierarchy><node text="Raw" /></hierarchy>',
        compressed_hierarchy='<hierarchy><node text="Compressed" /></hierarchy>',
    )
    driver = UiAutomator2AndroidDriver(app_id="com.example.app", device=device)

    result = driver.ui_snapshot({})

    assert result["xml"] == '<hierarchy><node text="Compressed" /></hierarchy>'
    assert device.calls == [("dump_hierarchy", True)]


def test_uiautomator2_driver_compacts_android_ui_snapshot_xml() -> None:
    long_text = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
    device = FakeDevice(
        hierarchy=f'''
<hierarchy rotation="0">
    <node
        index="0"
        package="com.example"
        class="android.widget.FrameLayout"
        bounds="[0,0][1080,2400]"
        enabled="true"
        displayed="true"
        clickable="false">
        <node
            index="0"
            package="com.example"
            class="android.widget.LinearLayout"
            bounds="[0,0][1080,2400]"
            enabled="true"
            displayed="true"
            focusable="false">
            <node
                index="1"
                text="{long_text}"
                resource-id="com.example:id/title"
                class="android.widget.TextView"
                package="com.example"
                checkable="false"
                checked="false"
                clickable="false"
                enabled="true"
                focusable="false"
                focused="false"
                long-clickable="false"
                scrollable="false"
                password="false"
                selected="false"
                bounds="[0,0][100,50]"
                displayed="true" />
            <node
                index="2"
                text=""
                class="android.view.View"
                package="com.example"
                bounds="[0,50][100,100]"
                enabled="true"
                displayed="true" />
            <node
                index="3"
                text=""
                content-desc="Open menu"
                class="android.widget.ImageButton"
                package="com.example"
                clickable="true"
                enabled="true"
                bounds="[100,0][160,60]"
                displayed="true" />
            <node
                index="4"
                text=""
                resource-id="com.example:id/disabled"
                class="android.widget.Button"
                package="com.example"
                enabled="false"
                displayed="false"
                bounds="[0,100][100,160]" />
        </node>
    </node>
</hierarchy>
'''
    )
    driver = UiAutomator2AndroidDriver(app_id="com.example.app", device=device)

    result = driver.ui_snapshot({})

    # The XML comes from the in-process fake device and is trusted test data.
    root = ElementTree.fromstring(str(result["xml"]))  # noqa: S314
    nodes = list(root)
    assert [node.attrib for node in nodes] == [
        {
            "text": long_text[:50],
            "resource-id": "com.example:id/title",
            "class": "android.widget.TextView",
            "bounds": "[0,0][100,50]",
        },
        {
            "content-desc": "Open menu",
            "class": "android.widget.ImageButton",
            "clickable": "true",
            "bounds": "[100,0][160,60]",
        },
        {
            "resource-id": "com.example:id/disabled",
            "class": "android.widget.Button",
            "bounds": "[0,100][100,160]",
            "displayed": "false",
            "enabled": "false",
        },
    ]
    assert "package" not in result["xml"]
    assert "index" not in result["xml"]
    assert "FrameLayout" not in result["xml"]
    assert "LinearLayout" not in result["xml"]
    assert "android.view.View" not in result["xml"]
    assert device.calls == [("dump_hierarchy",)]


def test_uiautomator2_driver_returns_raw_snapshot_when_xml_is_malformed() -> None:
    device = FakeDevice(hierarchy="<hierarchy><node")
    driver = UiAutomator2AndroidDriver(app_id="com.example.app", device=device)

    result = driver.ui_snapshot({})

    assert result["xml"] == "<hierarchy><node"
    assert device.calls == [("dump_hierarchy",)]


def test_uiautomator2_driver_falls_back_to_raw_hierarchy_when_compaction_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    device = FakeCompressedDevice(
        hierarchy='<hierarchy><node text="Raw" /></hierarchy>',
        compressed_hierarchy='<hierarchy><node text="Compressed" /></hierarchy>',
    )
    driver = UiAutomator2AndroidDriver(app_id="com.example.app", device=device)

    def fail_compaction(source_xml: str) -> str:
        raise RuntimeError(f"cannot compact {source_xml}")

    monkeypatch.setattr(driver, "_compact_ui_snapshot_xml", fail_compaction)

    result = driver.ui_snapshot({})

    assert result["xml"] == '<hierarchy><node text="Raw" /></hierarchy>'
    assert device.calls == [("dump_hierarchy", True), ("dump_hierarchy", False)]


def test_uiautomator2_driver_rejects_malformed_point_based_swipe() -> None:
    driver = UiAutomator2AndroidDriver(app_id="com.example.app", device=FakeDevice())

    result = driver.swipe({"start": {"x": 800}, "end": {"x": 200, "y": 1900}})

    assert result["status"] == "failed"
    assert result["failure_category"] == "configuration_error"
    assert "start.x, start.y, end.x, and end.y" in str(result["error_message"])


def test_uiautomator2_driver_rejects_malformed_point_based_tap() -> None:
    driver = UiAutomator2AndroidDriver(app_id="com.example.app", device=FakeDevice())

    result = driver.tap_at({"point": {"x": 320}})

    assert result["status"] == "failed"
    assert result["failure_category"] == "configuration_error"
    assert "point.x and point.y" in str(result["error_message"])


def test_uiautomator2_driver_waits_for_targets_before_actions() -> None:
    device = FakeDevice(wait_result=True)
    driver = UiAutomator2AndroidDriver(app_id="com.example.app", device=device)

    assert driver.tap_on({"locator": {"resourceId": "login"}})["status"] == "passed"
    assert driver.long_press_on({"locator": {"accessibilityId": "Menu"}})["status"] == "passed"
    assert driver.input_text({"text": "hello", "locator": {"text": "Search"}})["status"] == "passed"

    assert device.calls[:6] == [
        ("select", {"resourceId": "login"}),
        ("wait", {"resourceId": "login"}, {"exists": True, "timeout": 10.0}),
        ("click", {"resourceId": "login"}),
        ("select", {"description": "Menu"}),
        ("wait", {"description": "Menu"}, {"exists": True, "timeout": 10.0}),
        ("long_click", {"description": "Menu"}),
    ]
    assert ("wait", {"text": "Search"}, {"exists": True, "timeout": 10.0}) in device.calls


def test_uiautomator2_driver_input_text_focuses_clears_and_sets_text() -> None:
    device = FakeDevice()
    driver = UiAutomator2AndroidDriver(app_id="com.example.app", device=device)

    result = driver.input_text({"text": "machine learning tutorials", "locator": {"resourceId": "url_bar"}})

    assert result["status"] == "passed"
    assert device.calls == [
        ("select", {"resourceId": "url_bar"}),
        ("wait", {"resourceId": "url_bar"}, {"exists": True, "timeout": 10.0}),
        ("click", {"resourceId": "url_bar"}),
        ("clear_text", {"resourceId": "url_bar"}),
        ("set_text", {"resourceId": "url_bar"}, "machine learning tutorials"),
    ]


def test_uiautomator2_driver_assert_visible_waits_before_missing_target() -> None:
    device = FakeDevice(exists=False, wait_result=False)
    driver = UiAutomator2AndroidDriver(app_id="com.example.app", device=device)

    result = driver.assert_visible({"locator": {"text": "Missing"}})

    assert result["status"] == "failed"
    assert result["failure_category"] == "target_resolution_error"
    assert device.calls == [
        ("select", {"text": "Missing"}),
        ("wait", {"text": "Missing"}, {"exists": True, "timeout": 10.0}),
    ]


def test_uiautomator2_driver_assert_not_visible_waits_for_visible_target_to_disappear() -> None:
    device = FakeDevice(exists=True, wait_gone_result=True)
    driver = UiAutomator2AndroidDriver(app_id="com.example.app", device=device)

    result = driver.assert_not_visible({"locator": {"text": "Dialog"}})

    assert result["status"] == "passed"
    assert device.calls == [
        ("select", {"text": "Dialog"}),
        ("wait_gone", {"text": "Dialog"}, {"timeout": 10.0}),
    ]


def test_uiautomator2_driver_xpath_wait_retries_without_exists_argument() -> None:
    device = FakeDevice(wait_result=True)
    driver = UiAutomator2AndroidDriver(app_id="com.example.app", device=device)

    result = driver.assert_visible({"locator": {"xpath": "//android.widget.TextView[@text='Browse InPrivate']"}})

    assert result["status"] == "passed"
    assert device.calls == [
        ("xpath", "//android.widget.TextView[@text='Browse InPrivate']"),
        (
            "xpath_wait",
            {"xpath": "//android.widget.TextView[@text='Browse InPrivate']"},
            {"exists": True, "timeout": 10.0},
        ),
        (
            "xpath_wait",
            {"xpath": "//android.widget.TextView[@text='Browse InPrivate']"},
            {"timeout": 10.0},
        ),
    ]


def test_uiautomator2_driver_assertion_and_missing_target_results() -> None:
    device = FakeDevice(exists=False, wait_result=False)
    driver = UiAutomator2AndroidDriver(app_id="com.example.app", device=device)

    missing = driver.assert_visible({"locator": {"text": "Missing"}})
    absent = driver.assert_not_visible({"locator": {"text": "Missing"}})

    assert missing["status"] == "failed"
    assert missing["failure_category"] == "target_resolution_error"
    assert absent["status"] == "passed"


def test_uiautomator2_driver_asserts_android_element_state_fields() -> None:
    device = FakeDevice()
    device.selector_info = {
        "enabled": False,
        "checked": True,
        "selected": True,
        "clickable": False,
        "focused": True,
    }
    driver = UiAutomator2AndroidDriver(app_id="com.example.app", device=device)

    result = driver.assert_state(
        {
            "element": {
                "xpath": "//android.widget.FrameLayout[1]",
                "enabled": False,
                "checked": True,
                "selected": True,
                "clickable": False,
                "focused": True,
            }
        }
    )

    assert result["status"] == "passed"
    assert result["output"] == {
        "enabled": False,
        "checked": True,
        "selected": True,
        "clickable": False,
        "focused": True,
    }
    assert device.calls == [
        ("xpath", "//android.widget.FrameLayout[1]"),
        ("xpath_wait", {"xpath": "//android.widget.FrameLayout[1]"}, {"exists": True, "timeout": 10.0}),
        ("xpath_wait", {"xpath": "//android.widget.FrameLayout[1]"}, {"timeout": 10.0}),
        ("info", {"xpath": "//android.widget.FrameLayout[1]"}),
    ]


def test_uiautomator2_driver_reports_android_element_state_mismatch() -> None:
    device = FakeDevice()
    device.selector_info = {"enabled": True}
    driver = UiAutomator2AndroidDriver(app_id="com.example.app", device=device)

    result = driver.assert_state({"element": {"resourceId": "item", "enabled": False}})

    assert result["status"] == "failed"
    assert result["failure_category"] == "assertion_error"
    assert result["error_message"] == "Element state assertion failed."
    assert result["output"] == {"field": "enabled", "expected": False, "actual": True}


def test_uiautomator2_driver_reports_unsupported_assertion_shape() -> None:
    driver = UiAutomator2AndroidDriver(app_id="com.example.app", device=FakeDevice())

    result = driver.assert_state({"element": {}})

    assert result["status"] == "failed"
    assert result["failure_category"] == "configuration_error"
    assert "text or supported element state assertion" in str(result["error_message"])


def test_uiautomator2_driver_asserts_element_existence_with_locator_only() -> None:
    device = FakeDevice()
    driver = UiAutomator2AndroidDriver(app_id="com.example.app", device=device)

    result = driver.assert_state({"element": {"xpath": "//android.widget.EditText[@text='chinatravel.com']"}})

    assert result == {"status": "passed", "output": {"exists": True}}
    assert device.calls == [
        ("xpath", "//android.widget.EditText[@text='chinatravel.com']"),
        (
            "xpath_wait",
            {"xpath": "//android.widget.EditText[@text='chinatravel.com']"},
            {"exists": True, "timeout": 10.0},
        ),
        (
            "xpath_wait",
            {"xpath": "//android.widget.EditText[@text='chinatravel.com']"},
            {"timeout": 10.0},
        ),
    ]


def test_uiautomator2_driver_reports_unimplemented_backend_operations() -> None:
    driver = UiAutomator2AndroidDriver(app_id="com.example.app", device=FakeDevice())

    perform_result = driver.perform_actions({"actions": []})

    assert perform_result["status"] == "failed"
    assert perform_result["failure_category"] == "configuration_error"


def test_uiautomator2_driver_owns_ai_assertion_backend_tool() -> None:
    driver = UiAutomator2AndroidDriver(app_id="com.example.app", device=FakeDevice())

    definitions = {
        definition.name: definition
        for definition in _discover_driver_capability_definitions(
            UiAutomator2AndroidDriver,
            platform="android",
            metadata={"driver_class": "UiAutomator2AndroidDriver", "backend": "uiautomator2"},
        )
    }
    result = driver.assert_with_ai(AndroidAssertWithAIParams(prompt="Verify the screen."))

    assert definitions["assert_with_ai"].replay == ReplayPolicy(kind="fsq_command", alias="assertWithAI")
    assert definitions["assert_with_ai"].executor_kind == "driver"
    assert definitions["assert_with_ai"].owner == "driver"
    assert result["status"] == "failed"
    assert result["failure_category"] == "configuration_error"


def test_uiautomator2_driver_raises_configuration_error_when_dependency_missing(monkeypatch) -> None:
    real_import = builtins.__import__

    def fake_import(name: str, *args: object, **kwargs: object) -> object:
        if name == "uiautomator2":
            raise ImportError("missing uiautomator2")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    with pytest.raises(ConfigurationError) as exc_info:
        UiAutomator2AndroidDriver(app_id="com.example.app")

    assert exc_info.value.context == {"action": "Reinstall or repair fsq-agent."}
