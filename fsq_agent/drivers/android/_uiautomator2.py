# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from io import BytesIO
from typing import Any
from xml.etree import ElementTree

from pydantic import BaseModel

from fsq_agent.drivers._ai_assertion import AIAssertionBackendToolMixin
from fsq_agent.drivers._capabilities import _android_driver_tool
from fsq_agent.models import (
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
    ConfigurationError,
)

DEFAULT_ELEMENT_WAIT_TIMEOUT_SECONDS = 10.0
ANDROID_STATE_ASSERTION_FIELDS = ("enabled", "checked", "selected", "clickable", "focused")
ANDROID_LOCATOR_FIELDS = ("resourceId", "accessibilityId", "text", "className", "xpath")
ANDROID_UI_SNAPSHOT_TEXT_LIMIT_CHARS = 50
ANDROID_UI_SNAPSHOT_TEXT_ATTRIBUTES = frozenset({"text", "content-desc", "contentDescription", "hint", "label", "name", "value"})
ANDROID_UI_SNAPSHOT_LOCATOR_ATTRIBUTES = frozenset({"id", "resource-id", "resourceId", "accessibility-id", "content-desc", "contentDescription"})
ANDROID_UI_SNAPSHOT_STRUCTURAL_ATTRIBUTES = frozenset({"class", "className", "bounds"})
ANDROID_UI_SNAPSHOT_TRUE_STATE_ATTRIBUTES = frozenset(
    {
        "checkable",
        "checked",
        "clickable",
        "focusable",
        "focused",
        "long-clickable",
        "longClickable",
        "password",
        "scrollable",
        "selected",
    }
)
ANDROID_UI_SNAPSHOT_NEGATIVE_STATE_ATTRIBUTES = frozenset({"displayed", "enabled"})
ANDROID_UI_SNAPSHOT_ALLOWED_ATTRIBUTES = (
    ANDROID_UI_SNAPSHOT_TEXT_ATTRIBUTES
    | ANDROID_UI_SNAPSHOT_LOCATOR_ATTRIBUTES
    | ANDROID_UI_SNAPSHOT_STRUCTURAL_ATTRIBUTES
    | ANDROID_UI_SNAPSHOT_TRUE_STATE_ATTRIBUTES
    | ANDROID_UI_SNAPSHOT_NEGATIVE_STATE_ATTRIBUTES
)


def _parse_xml_safely(source: str) -> ElementTree.Element:
    normalized = source.upper()
    if "<!DOCTYPE" in normalized or "<!ENTITY" in normalized:
        raise ElementTree.ParseError("DTD and entity declarations are not allowed.")
    # DTD and entity declarations are rejected above, preventing attacker-controlled entity expansion.
    return ElementTree.fromstring(source)  # noqa: S314


class UiAutomator2AndroidDriver(AIAssertionBackendToolMixin):
    backend = "uiautomator2"

    def __init__(self, *, app_id: str, serial: str | None = None, device: object | None = None) -> None:
        self.app_id = app_id
        self.serial = serial
        self.device = device if device is not None else self._connect(serial)

    def close(self) -> None:
        return None

    def context(self) -> dict[str, object]:
        info = self._device_info()
        width = info.get("displayWidth")
        height = info.get("displayHeight")
        return {
            "session_id": f"uiautomator2:{self.serial or 'fake-device'}",
            "current_activity": self._current_activity(),
            "screen_size": (width, height) if isinstance(width, int) and isinstance(height, int) else None,
            "metadata": {
                "backend": "uiautomator2",
                "current_package": info.get("currentPackageName"),
            },
        }

    @_android_driver_tool("launchApp", description="Launch the configured Android app.")
    def launch_app(self, params: AndroidLaunchAppParams) -> dict[str, object]:
        data = self._param_data(params)
        app_id = str(data.get("app_id") or self.app_id)
        options = {key: value for key, value in data.items() if key != "app_id"}
        self.device.app_start(app_id, **options)
        return self._passed({"app_id": app_id})

    @_android_driver_tool("killApp", description="Stop the configured Android app.")
    def kill_app(self, params: AndroidKillAppParams) -> dict[str, object]:
        data = self._param_data(params)
        app_id = str(data.get("app_id") or self.app_id)
        self.device.app_stop(app_id)
        return self._passed({"app_id": app_id})

    @_android_driver_tool("tapOn", description="Tap an Android UI target.")
    def tap_on(self, params: AndroidTapOnParams) -> dict[str, object]:
        data = self._param_data(params)
        selector = self._selector(data)
        if not self._wait_for_exists(selector):
            return self._target_missing(data)
        selector.click()
        return self._passed()

    @_android_driver_tool("tapAt", description="Tap Android screen coordinates.")
    def tap_at(self, params: AndroidTapAtParams) -> dict[str, object]:
        data = self._param_data(params)
        point = self._point_payload(data)
        if point is None:
            return self._configuration_error("tapAt requires integer point.x and point.y parameters.")
        x, y = self._scaled_point(point, data.get("reference_screen_size"))
        self.device.click(x, y)
        return self._passed({"point": {"x": x, "y": y}})

    @_android_driver_tool("longPressOn", description="Long press an Android UI target.")
    def long_press_on(self, params: AndroidLongPressOnParams) -> dict[str, object]:
        data = self._param_data(params)
        selector = self._selector(data)
        if not self._wait_for_exists(selector):
            return self._target_missing(data)
        selector.long_click()
        return self._passed()

    @_android_driver_tool("inputText", description="Enter text into a focused Android UI target.")
    def input_text(self, params: AndroidInputTextParams) -> dict[str, object]:
        data = self._param_data(params)
        text = data.get("text")
        if not isinstance(text, str):
            return self._configuration_error("inputText requires a string text parameter.")
        selector = self._selector(data)
        if not self._wait_for_exists(selector):
            return self._target_missing(data)
        selector.click()
        clear_text = getattr(selector, "clear_text", None)
        if callable(clear_text):
            clear_text()
        selector.set_text(text)
        return self._passed()

    @_android_driver_tool("pressKey", description="Press an Android key.")
    def press_key(self, params: AndroidPressKeyParams) -> dict[str, object]:
        data = self._param_data(params)
        key = data.get("key")
        if not isinstance(key, str) or not key.strip():
            return self._configuration_error("pressKey requires a key parameter.")
        self.device.press(key.strip().lower())
        return self._passed({"key": key.strip()})

    @_android_driver_tool("swipe", description="Swipe by direction or explicit Android screen coordinates.")
    def swipe(self, params: AndroidSwipeParams) -> dict[str, object]:
        data = self._param_data(params)
        direction = data.get("direction")
        if "start" in data or "end" in data:
            points = self._swipe_point_payload(data)
            if points is None:
                return self._configuration_error("swipe point payload requires integer start.x, start.y, end.x, and end.y parameters.")
            sx, sy, ex, ey = points
            sx, sy = self._scaled_point((sx, sy), data.get("reference_screen_size"))
            ex, ey = self._scaled_point((ex, ey), data.get("reference_screen_size"))
            duration = self._duration_seconds(data)
            self.device.swipe(sx, sy, ex, ey, duration)
            return self._passed({"start": {"x": sx, "y": sy}, "end": {"x": ex, "y": ey}})
        if not isinstance(direction, str):
            return self._configuration_error("swipe requires a direction parameter.")
        width, height = self._screen_size()
        duration = self._duration_seconds(data)
        sx, sy, ex, ey = self._swipe_points(direction, width, height)
        self.device.swipe(sx, sy, ex, ey, duration)
        return self._passed({"direction": direction})

    def perform_actions(self, params: AndroidPerformActionsParams) -> dict[str, object]:
        return self._configuration_error("performActions is not implemented for the uiautomator2 backend yet.")

    @_android_driver_tool("assertVisible", description="Assert that an Android UI target is visible.")
    def assert_visible(self, params: AndroidAssertVisibleParams) -> dict[str, object]:
        data = self._param_data(params)
        selector = self._selector(data)
        if self._wait_for_exists(selector):
            return self._passed()
        return self._target_missing(data)

    @_android_driver_tool("assertNotVisible", description="Assert that an Android UI target is not visible.")
    def assert_not_visible(self, params: AndroidAssertNotVisibleParams) -> dict[str, object]:
        data = self._param_data(params)
        selector = self._selector(data)
        if not self._exists(selector):
            return self._passed()
        if self._wait_for_not_exists(selector):
            return self._passed()
        return self._failed("assertion_error", "Target is visible.")

    @_android_driver_tool("assert", description="Assert Android element existence, text, or state.")
    def assert_state(self, params: AndroidAssertStateParams) -> dict[str, object]:
        data = self._param_data(params)
        selector = self._selector(data, locator_key="element")
        if not self._wait_for_exists(selector):
            return self._target_missing(data)
        expected = data.get("text")
        if isinstance(expected, dict):
            return self._assert_text_state(selector, expected)
        element = data.get("element")
        if isinstance(element, dict):
            expected_states = self._expected_element_states(element)
            if expected_states:
                return self._assert_element_states(selector, expected_states)
            if self._has_locator(element):
                return self._passed({"exists": True})
        return self._configuration_error("assert requires a text or supported element state assertion.")

    @_android_driver_tool("assertWithAI", description="Evaluate an explicit Android visual assertion with AI.")
    def assert_with_ai(self, params: AndroidAssertWithAIParams) -> dict[str, object]:
        return self._run_ai_assertion_tool(params)

    def screenshot(self, params: object | None = None) -> bytes:
        image = self.device.screenshot(format="pillow")
        if isinstance(image, bytes):
            return image
        if isinstance(image, bytearray):
            return bytes(image)
        output = BytesIO()
        image.save(output, format="PNG")
        return output.getvalue()

    @_android_driver_tool(
        "uiTree",
        description=(
            "Return a compact Android UI hierarchy XML snapshot. Layout-only wrapper nodes and unused/default "
            "attributes may be removed, and long text-like attributes are clipped to the first 50 characters."
        ),
    )
    def ui_snapshot(self, params: AndroidUiTreeParams) -> dict[str, object]:
        source_xml = self._dump_hierarchy_xml()
        compacted = True
        source_nodes = None
        retained_nodes = None
        clipped_attributes = None
        try:
            source_root = _parse_xml_safely(source_xml)
            compact_xml = self._compact_ui_snapshot_xml(source_xml)
            source_nodes = sum(1 for _ in source_root.iter())
            retained_nodes = sum(1 for _ in _parse_xml_safely(compact_xml).iter())
            clipped_attributes = sum(len(value) > 50 for node in source_root.iter() for key, value in node.attrib.items() if key in {"text", "content-desc", "hint"})
        except ElementTree.ParseError:
            compact_xml = source_xml
            compacted = False
        # Snapshot compaction must fall back for any local or optional-backend failure by contract.
        except Exception:  # noqa: BLE001
            compact_xml = self._raw_hierarchy_xml_or(source_xml)
            compacted = False
        return {
            "xml": compact_xml,
            "coverage": {
                "status": "partial" if compacted and compact_xml != source_xml else "unknown",
                "reason": "semantic_compaction" if compacted else "raw_fallback",
                "source_characters": len(source_xml),
                "retained_characters": len(compact_xml),
            },
            "compaction": {
                "applied": compacted,
                "text_attribute_limit": 50 if compacted else None,
                "layout_nodes_removed": max(0, source_nodes - retained_nodes) if source_nodes is not None and retained_nodes is not None else None,
                "clipped_attributes": clipped_attributes,
            },
        }

    def _dump_hierarchy_xml(self) -> str:
        try:
            return str(self.device.dump_hierarchy(compressed=True))
        # uiautomator2 versions and injected devices expose no shared hierarchy-dump exception type.
        except Exception as compressed_error:  # noqa: BLE001
            try:
                return str(self.device.dump_hierarchy())
            # Preserve the preferred compressed-call failure while retaining fallback failure context.
            except Exception as fallback_error:
                raise compressed_error from fallback_error

    def _raw_hierarchy_xml_or(self, fallback_xml: str) -> str:
        try:
            return str(self.device.dump_hierarchy())
        # Raw hierarchy fallback must preserve the already captured XML across optional-backend failures.
        except Exception:  # noqa: BLE001
            return fallback_xml

    def _compact_ui_snapshot_xml(self, source_xml: str) -> str:
        try:
            source_root = _parse_xml_safely(source_xml)
        except ElementTree.ParseError:
            return source_xml

        compact_root = ElementTree.Element(source_root.tag, self._compact_ui_snapshot_attributes(source_root.attrib))
        for child in source_root:
            compact_root.extend(self._compact_ui_snapshot_node(child))
        return ElementTree.tostring(compact_root, encoding="unicode", short_empty_elements=True)

    def _compact_ui_snapshot_node(self, source_node: ElementTree.Element) -> list[ElementTree.Element]:
        attributes = self._compact_ui_snapshot_attributes(source_node.attrib)
        children: list[ElementTree.Element] = []
        for child in source_node:
            children.extend(self._compact_ui_snapshot_node(child))

        if not self._has_ui_snapshot_signal(attributes):
            return children

        compact_node = ElementTree.Element(source_node.tag, attributes)
        compact_node.extend(children)
        return [compact_node]

    def _compact_ui_snapshot_attributes(self, attributes: dict[str, str]) -> dict[str, str]:
        compact: dict[str, str] = {}
        for key, raw_value in attributes.items():
            value = str(raw_value)
            if not value or key not in ANDROID_UI_SNAPSHOT_ALLOWED_ATTRIBUTES:
                continue
            if key in ANDROID_UI_SNAPSHOT_TEXT_ATTRIBUTES:
                value = value[:ANDROID_UI_SNAPSHOT_TEXT_LIMIT_CHARS]
                if not value:
                    continue
            if key in ANDROID_UI_SNAPSHOT_TRUE_STATE_ATTRIBUTES and value.lower() != "true":
                continue
            if key in ANDROID_UI_SNAPSHOT_NEGATIVE_STATE_ATTRIBUTES and value.lower() == "true":
                continue
            compact[key] = value
        return compact

    def _has_ui_snapshot_signal(self, attributes: dict[str, str]) -> bool:
        return any(key not in ANDROID_UI_SNAPSHOT_STRUCTURAL_ATTRIBUTES for key in attributes)

    def _connect(self, serial: str | None) -> object:
        try:
            import uiautomator2 as u2
        except ImportError as exc:
            raise ConfigurationError(
                "uiautomator2 is required for UiAutomator2AndroidDriver.",
                context={"action": "Reinstall or repair fsq-agent."},
            ) from exc
        return u2.connect(serial)

    def _param_data(self, params: BaseModel | dict[str, object]) -> dict[str, object]:
        if isinstance(params, BaseModel):
            return params.model_dump(mode="json", exclude_none=True)
        return dict(params)

    def _selector(self, params: dict[str, object], *, locator_key: str = "locator") -> object:
        locator = params.get(locator_key)
        if not isinstance(locator, dict):
            locator = params.get("locator")
        if isinstance(locator, dict):
            if isinstance(locator.get("xpath"), str):
                return self.device.xpath(locator["xpath"])
            query = self._selector_query(locator)
            if query:
                return self.device(**query)
        fallback = params.get("target")
        if isinstance(fallback, str) and fallback.strip():
            return self.device(text=fallback.strip())
        return self.device()

    def _selector_query(self, locator: dict[str, object]) -> dict[str, object]:
        query: dict[str, object] = {}
        if isinstance(locator.get("resourceId"), str):
            query["resourceId"] = locator["resourceId"]
        if isinstance(locator.get("accessibilityId"), str):
            query["description"] = locator["accessibilityId"]
        if isinstance(locator.get("text"), str):
            query["text"] = locator["text"]
        if isinstance(locator.get("className"), str):
            query["className"] = locator["className"]
        return query

    def _wait_for_exists(self, selector: object) -> bool:
        wait = getattr(selector, "wait", None)
        if callable(wait):
            try:
                return bool(wait(exists=True, timeout=DEFAULT_ELEMENT_WAIT_TIMEOUT_SECONDS))
            except TypeError:
                return bool(wait(timeout=DEFAULT_ELEMENT_WAIT_TIMEOUT_SECONDS))
        return self._exists(selector)

    def _wait_for_not_exists(self, selector: object) -> bool:
        wait_gone = getattr(selector, "wait_gone", None)
        if callable(wait_gone):
            return bool(wait_gone(timeout=DEFAULT_ELEMENT_WAIT_TIMEOUT_SECONDS))

        wait = getattr(selector, "wait", None)
        if callable(wait):
            try:
                return bool(wait(exists=False, timeout=DEFAULT_ELEMENT_WAIT_TIMEOUT_SECONDS))
            except TypeError:
                pass
        return not self._exists(selector)

    def _exists(self, selector: object) -> bool:
        exists = getattr(selector, "exists", False)
        return bool(exists() if callable(exists) else exists)

    def _device_info(self) -> dict[str, object]:
        info = getattr(self.device, "info", {})
        return info if isinstance(info, dict) else {}

    def _current_activity(self) -> str | None:
        app_current = getattr(self.device, "app_current", None)
        if not callable(app_current):
            return None
        current = app_current()
        if not isinstance(current, dict):
            return None
        activity = current.get("activity")
        return activity if isinstance(activity, str) else None

    def _screen_size(self) -> tuple[int, int]:
        info = self._device_info()
        width = info.get("displayWidth")
        height = info.get("displayHeight")
        if isinstance(width, int) and isinstance(height, int):
            return width, height
        return 1080, 1920

    def _swipe_points(self, direction: str, width: int, height: int) -> tuple[int, int, int, int]:
        normalized = direction.strip().lower()
        mid_x = width // 2
        mid_y = height // 2
        if normalized == "up":
            return mid_x, int(height * 0.75), mid_x, int(height * 0.25)
        if normalized == "down":
            return mid_x, int(height * 0.25), mid_x, int(height * 0.75)
        if normalized == "left":
            return int(width * 0.75), mid_y, int(width * 0.25), mid_y
        if normalized == "right":
            return int(width * 0.25), mid_y, int(width * 0.75), mid_y
        return mid_x, int(height * 0.75), mid_x, int(height * 0.25)

    def _swipe_point_payload(self, params: dict[str, object]) -> tuple[int, int, int, int] | None:
        start = params.get("start")
        end = params.get("end")
        if not isinstance(start, dict) or not isinstance(end, dict):
            return None
        sx = start.get("x")
        sy = start.get("y")
        ex = end.get("x")
        ey = end.get("y")
        if not all(isinstance(value, int) for value in [sx, sy, ex, ey]):
            return None
        return sx, sy, ex, ey

    def _point_payload(self, params: dict[str, object]) -> tuple[int, int] | None:
        point = params.get("point")
        if not isinstance(point, dict):
            return None
        x = point.get("x")
        y = point.get("y")
        if not isinstance(x, int) or not isinstance(y, int):
            return None
        return x, y

    def _scaled_point(self, point: tuple[int, int], reference_screen_size: object) -> tuple[int, int]:
        if not isinstance(reference_screen_size, dict):
            return point
        reference_width = reference_screen_size.get("width")
        reference_height = reference_screen_size.get("height")
        if not isinstance(reference_width, int) or not isinstance(reference_height, int) or reference_width < 1 or reference_height < 1:
            return point
        width, height = self._screen_size()
        x = round(point[0] * width / reference_width)
        y = round(point[1] * height / reference_height)
        return self._clamp_coordinate(x, width), self._clamp_coordinate(y, height)

    def _clamp_coordinate(self, value: int, limit: int) -> int:
        return max(0, min(value, max(limit - 1, 0)))

    def _duration_seconds(self, params: dict[str, object]) -> float:
        duration_ms = params.get("duration") if isinstance(params.get("duration"), int) else 200
        return max(duration_ms, 1) / 1000

    def _assert_text_state(self, selector: object, expected: dict[str, object]) -> dict[str, object]:
        actual = selector.get_text()
        contains = expected.get("contains")
        if isinstance(contains, str) and contains in actual:
            return self._passed({"text": actual})
        equals = expected.get("equals")
        if isinstance(equals, str) and equals == actual:
            return self._passed({"text": actual})
        return self._failed("assertion_error", "Text assertion failed.", output={"text": actual})

    def _expected_element_states(self, element: dict[str, object]) -> dict[str, bool]:
        return {field: value for field in ANDROID_STATE_ASSERTION_FIELDS if isinstance((value := element.get(field)), bool)}

    def _has_locator(self, element: dict[str, object]) -> bool:
        return any(isinstance(element.get(field), str) and element[field].strip() for field in ANDROID_LOCATOR_FIELDS)

    def _assert_element_states(self, selector: object, expected_states: dict[str, bool]) -> dict[str, object]:
        actual_states = self._selector_info(selector)
        passed: dict[str, bool] = {}
        for field, expected in expected_states.items():
            actual = actual_states.get(field)
            if actual != expected:
                return self._failed(
                    "assertion_error",
                    "Element state assertion failed.",
                    output={"field": field, "expected": expected, "actual": actual},
                )
            passed[field] = expected
        return self._passed(passed)

    def _selector_info(self, selector: object) -> dict[str, object]:
        info = getattr(selector, "info", {})
        if callable(info):
            info = info()
        return info if isinstance(info, dict) else {}

    def _target_missing(self, params: dict[str, object]) -> dict[str, object]:
        return self._failed("target_resolution_error", "Target was not found.", metadata={"params": params})

    def _configuration_error(self, message: str) -> dict[str, object]:
        return self._failed("configuration_error", message)

    def _passed(self, output: object | None = None) -> dict[str, object]:
        return {"status": "passed", "output": output}

    def _failed(
        self,
        failure_category: str,
        error_message: str,
        *,
        output: object | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, object]:
        return {
            "status": "failed",
            "failure_category": failure_category,
            "error_message": error_message,
            "output": output,
            "metadata": metadata or {},
        }
