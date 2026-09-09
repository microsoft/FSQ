# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from itertools import pairwise
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

from pydantic import BaseModel

from fsq_agent.drivers._ai_assertion import AIAssertionBackendToolMixin
from fsq_agent.drivers._capabilities import _macos_driver_tool
from fsq_agent.drivers.macos._elements import candidate, parse_source, query_source
from fsq_agent.models import (
    ConfigurationError,
    MacOSAssertElementsOrderParams,
    MacOSAssertVisibleParams,
    MacOSAssertWithAIParams,
    MacOSClickOnParams,
    MacOSDoubleClickOnParams,
    MacOSDragEndpoint,
    MacOSDragToParams,
    MacOSHoverOnParams,
    MacOSKillAppParams,
    MacOSLaunchAppParams,
    MacOSLocator,
    MacOSPoint,
    MacOSPressKeyParams,
    MacOSRightClickOnParams,
    MacOSTakeScreenshotParams,
    MacOSTypeTextParams,
    MacOSUiSnapshotParams,
)

DEFAULT_APPIUM_MAC2_SERVER_URL = "http://127.0.0.1:4723"
DEFAULT_MACOS_PAGE_SOURCE_MAX_DEPTH = 12
DEFAULT_MACOS_ACTION_TIMEOUT_SECONDS = 10
DEFAULT_MACOS_NEW_COMMAND_TIMEOUT_SECONDS = 300
DEFAULT_MACOS_SNAPSHOT_SOURCE_PREVIEW_CHARS = 2000
DEFAULT_MACOS_SNAPSHOT_TEXT_LIMIT_CHARS = 50
MACOS_KEY_MODIFIER_FLAGS = {
    "CAPS_LOCK": 1 << 0,
    "SHIFT": 1 << 1,
    "CONTROL": 1 << 2,
    "OPTION": 1 << 3,
    "ALT": 1 << 3,
    "COMMAND": 1 << 4,
    "FUNCTION": 1 << 5,
}
MACOS_KEY_NAMES = {
    "BACKSPACE": "XCUIKeyboardKeyDelete",
    "DELETE": "XCUIKeyboardKeyDelete",
    "DOWN": "XCUIKeyboardKeyDownArrow",
    "END": "XCUIKeyboardKeyEnd",
    "ENTER": "XCUIKeyboardKeyReturn",
    "ESC": "XCUIKeyboardKeyEscape",
    "ESCAPE": "XCUIKeyboardKeyEscape",
    "HOME": "XCUIKeyboardKeyHome",
    "LEFT": "XCUIKeyboardKeyLeftArrow",
    "PAGEDOWN": "XCUIKeyboardKeyPageDown",
    "PAGEUP": "XCUIKeyboardKeyPageUp",
    "RETURN": "XCUIKeyboardKeyReturn",
    "RIGHT": "XCUIKeyboardKeyRightArrow",
    "SPACE": " ",
    "TAB": "XCUIKeyboardKeyTab",
    "UP": "XCUIKeyboardKeyUpArrow",
}
DEFAULT_MACOS_SNAPSHOT_ATTRIBUTE_KEYS = frozenset(
    {
        "identifier",
        "name",
        "label",
        "value",
        "type",
        "role",
        "enabled",
        "visible",
        "selected",
        "x",
        "y",
        "width",
        "height",
    }
)
MACOS_SNAPSHOT_TEXT_ATTRIBUTE_KEYS = frozenset({"name", "label", "value"})
MACOS_SNAPSHOT_IDENTITY_ATTRIBUTE_KEYS = frozenset({"identifier", *MACOS_SNAPSHOT_TEXT_ATTRIBUTE_KEYS})
MACOS_SNAPSHOT_SEMANTIC_ATTRIBUTE_KEYS = frozenset({*MACOS_SNAPSHOT_IDENTITY_ATTRIBUTE_KEYS, "type", "role"})
MACOS_SNAPSHOT_STATE_DEFAULTS = {"enabled": "true", "visible": "true", "selected": "false"}


def _parse_xml_safely(source: str) -> ElementTree.Element:
    normalized = source.upper()
    if "<!DOCTYPE" in normalized or "<!ENTITY" in normalized:
        raise ElementTree.ParseError("DTD and entity declarations are not allowed.")
    # DTD and entity declarations are rejected above, preventing attacker-controlled entity expansion.
    return ElementTree.fromstring(source)  # noqa: S314


class AppiumMac2Driver(AIAssertionBackendToolMixin):
    backend = "appium_mac2"

    def __init__(
        self,
        *,
        server_url: str = DEFAULT_APPIUM_MAC2_SERVER_URL,
        bundle_id: str | None = None,
        app_path: str | Path | None = None,
        page_source_max_depth: int = DEFAULT_MACOS_PAGE_SOURCE_MAX_DEPTH,
        action_timeout_seconds: int = DEFAULT_MACOS_ACTION_TIMEOUT_SECONDS,
        new_command_timeout_seconds: int = DEFAULT_MACOS_NEW_COMMAND_TIMEOUT_SECONDS,
        session: object | None = None,
    ) -> None:
        self.server_url = server_url.rstrip("/")
        self.bundle_id = bundle_id.strip() if isinstance(bundle_id, str) and bundle_id.strip() else None
        self.app_path = str(Path(app_path)) if app_path else None
        self.page_source_max_depth = page_source_max_depth
        self.action_timeout_seconds = action_timeout_seconds
        self.new_command_timeout_seconds = new_command_timeout_seconds
        self._session = session

    def context(self) -> dict[str, object]:
        session_id = self._safe_attr(self._session, "session_id")
        return {
            "session_id": session_id if isinstance(session_id, str) else None,
            "current_url": None,
            "screen_size": self._window_size(),
            "capabilities": self._safe_attr(self._session, "capabilities") or {},
            "metadata": {
                "backend": self.backend,
                "server_url_configured": bool(self.server_url),
                "bundle_id_configured": self.bundle_id is not None,
                "app_path_configured": self.app_path is not None,
            },
        }

    @_macos_driver_tool(
        "launchApp",
        description="Create or reuse an Appium Mac2 session for the configured macOS application.",
    )
    def launch_app(self, params: MacOSLaunchAppParams) -> dict[str, object]:
        session = self._ensure_session(params)
        return self._passed(
            {
                "session_id": self._safe_attr(session, "session_id"),
                "bundle_id": self._effective_bundle_id(params.bundle_id),
                "app_path": self._effective_app_path(params.app_path),
            }
        )

    @_macos_driver_tool("killApp", description="Terminate the active Appium Mac2 application or close the session.")
    def kill_app(self, params: MacOSKillAppParams) -> dict[str, object]:
        session = self._session
        if session is None:
            return self._passed()
        bundle_id = self._effective_bundle_id(params.bundle_id)
        if params.close_session:
            self.close()
            return self._passed({"bundle_id": bundle_id, "close_session": True})
        if bundle_id:
            terminate = getattr(session, "terminate_app", None)
            if not callable(terminate):
                raise ConfigurationError("Active Appium Mac2 session cannot terminate applications.")
            terminate(bundle_id)
        else:
            raise ConfigurationError("macOS application termination requires bundle_id or a configured bundle id.")
        return self._passed({"bundle_id": bundle_id, "close_session": False})

    @_macos_driver_tool("clickOn", description="Click a macOS element or point resolved through Appium Mac2.")
    def click_on(self, params: MacOSClickOnParams) -> dict[str, object]:
        element = self._resolve_element_or_none(params)
        if element is not None:
            click = getattr(element, "click", None)
            if callable(click):
                try:
                    click()
                except Exception as exc:  # noqa: BLE001 -- normalize backend failure without raw details.
                    self._raise_resolution_error(exc)
                return self._passed()
        point = self._point_from_params(params)
        if point is None:
            return self._target_missing(params)
        self._perform_pointer_click(point)
        return self._passed({"point": point.model_dump(mode="json")})

    @_macos_driver_tool("doubleClickOn", description="Double-click a macOS element or point.")
    def double_click_on(self, params: MacOSDoubleClickOnParams) -> dict[str, object]:
        return self._click_count(params, count=2)

    @_macos_driver_tool("rightClickOn", description="Right-click a macOS element or point.")
    def right_click_on(self, params: MacOSRightClickOnParams) -> dict[str, object]:
        point = self._point_from_params(params)
        if point is None:
            element = self._resolve_element_or_none(params)
            point = self._element_center(element) if element is not None else None
        if point is None:
            return self._target_missing(params)
        self._perform_pointer_click(point, button="right")
        return self._passed({"point": point.model_dump(mode="json")})

    @_macos_driver_tool("typeText", description="Type text into the active or resolved macOS element.")
    def type_text(self, params: MacOSTypeTextParams) -> dict[str, object]:
        element = self._resolve_element_or_none(params)
        if element is not None:
            if params.clear:
                clear = getattr(element, "clear", None)
                if not callable(clear):
                    return self._failed("action_error", "Resolved Appium element cannot clear existing text.")
                clear()
            self._send_macos_text(params.text, element=element)
            return self._passed()

        point = self._point_from_params(params)
        if point is not None:
            self._perform_pointer_click(point)
        elif params.target is not None or params.locator is not None:
            return self._target_missing(params)

        if params.clear:
            session = self._require_session()
            switch_to = getattr(session, "switch_to", None)
            active_element = getattr(switch_to, "active_element", None)
            clear = getattr(active_element, "clear", None)
            if not callable(clear):
                return self._failed("action_error", "Appium session has no active element that can clear existing text.")
            clear()
        self._send_macos_text(params.text)
        if point is not None:
            return self._passed({"point": point.model_dump(mode="json")})
        return self._passed()

    def _send_macos_text(self, text: str, *, element: object | None = None) -> None:
        normalized_text = text.replace("\r\n", "\n").replace("\r", "\n")
        keys = [MACOS_KEY_NAMES["ENTER"] if character == "\n" else character for character in normalized_text]
        self._execute_macos_keys(keys, element=element)

    def _execute_macos_keys(self, keys: list[object], *, element: object | None = None) -> None:
        session = self._require_session()
        execute_script = getattr(session, "execute_script", None)
        if not callable(execute_script):
            raise ConfigurationError("Appium Mac2 session cannot execute the macos: keys command.")
        arguments: dict[str, object] = {"keys": keys}
        element_id = self._safe_attr(element, "id")
        if isinstance(element_id, str) and element_id:
            arguments["elementId"] = element_id
        execute_script("macos: keys", arguments)

    @_macos_driver_tool("pressKey", description="Send a keyboard shortcut or key to macOS.")
    def press_key(self, params: MacOSPressKeyParams) -> dict[str, object]:
        modifier_flags = 0
        for modifier in params.modifiers or []:
            normalized = modifier.strip().upper()
            if normalized not in MACOS_KEY_MODIFIER_FLAGS:
                raise ConfigurationError(f"Unsupported macOS key modifier: {modifier}.")
            modifier_flags |= MACOS_KEY_MODIFIER_FLAGS[normalized]
        key = MACOS_KEY_NAMES.get(params.key.strip().upper(), params.key)
        key_payload: object = key if modifier_flags == 0 else {"key": key, "modifierFlags": modifier_flags}
        self._execute_macos_keys([key_payload])
        return self._passed({"key": params.key, "modifiers": params.modifiers or []})

    @_macos_driver_tool("hoverOn", description="Move the pointer over a macOS element or point.")
    def hover_on(self, params: MacOSHoverOnParams) -> dict[str, object]:
        point = self._point_from_params(params)
        if point is None:
            element = self._resolve_element_or_none(params)
            point = self._element_center(element) if element is not None else None
        if point is None:
            return self._target_missing(params)
        self._perform_pointer_move(point)
        return self._passed({"point": point.model_dump(mode="json")})

    @_macos_driver_tool("dragTo", description="Drag from one macOS element or point to another.")
    def drag_to(self, params: MacOSDragToParams) -> dict[str, object]:
        source = self._point_from_endpoint(params.source)
        destination = self._point_from_endpoint(params.destination)
        if source is None or destination is None:
            return self._failed(
                "target_resolution_error",
                "Drag source or destination was not found.",
                metadata={"params": params.model_dump(mode="json", exclude_none=True)},
            )
        self._perform_pointer_drag(source, destination, duration_ms=params.duration_ms)
        return self._passed({"source": source.model_dump(mode="json"), "destination": destination.model_dump(mode="json")})

    @_macos_driver_tool("takeScreenshot", description="Capture a macOS screenshot through Appium Mac2.")
    def take_screenshot(self, params: MacOSTakeScreenshotParams) -> bytes:
        return self.screenshot(params)

    @_macos_driver_tool(
        "uiSnapshot",
        description=(
            "Read macOS accessibility evidence. Use query for bounded semantic element discovery before text/depth clipping; otherwise return a compact tree with explicit clipping markers. No-match is not proof of invisibility."
        ),
    )
    def ui_snapshot(self, params: MacOSUiSnapshotParams) -> dict[str, object]:
        session = self._require_session()
        try:
            source = getattr(session, "page_source", None)
        except Exception as exc:  # noqa: BLE001 -- normalize optional backend errors without exposing their bodies.
            self._raise_resolution_error(exc)
        if not isinstance(source, str):
            raise ConfigurationError("Mac2 did not return a page source.", context={"resolution_reason": "backend_error"})
        if params.query is not None:
            return query_source(source, params.query)
        max_depth = params.max_depth or self.page_source_max_depth
        include_attributes = bool(params.include_attributes)
        return {
            "snapshot_type": "macos_accessibility_tree",
            "page_source": self._simplify_page_source(
                source if isinstance(source, str) else "",
                max_depth=max_depth,
                include_attributes=include_attributes,
            ),
            "max_depth": max_depth,
            "include_attributes": include_attributes,
            "diagnostics": {"source": "appium_mac2_page_source", "atomic_with_screenshot": False, "absence_proves_invisibility": False},
        }

    @_macos_driver_tool("assertVisible", description="Assert that a macOS element is visible.")
    def assert_visible(self, params: MacOSAssertVisibleParams) -> dict[str, object]:
        element = self._resolve_element_or_none(params)
        if element is None:
            return self._target_missing(params)
        if self._element_displayed(element):
            return self._passed()
        return self._failed("assertion_error", "Target is present but not visible.")

    @_macos_driver_tool("assertElementsOrder", description="Assert that macOS elements appear in the expected visual order.")
    def assert_elements_order(self, params: MacOSAssertElementsOrderParams) -> dict[str, object]:
        entries: list[dict[str, object]] = []
        missing: list[int] = []
        for index, element_ref in enumerate(params.elements):
            element = self._resolve_element_or_none(element_ref)
            if element is None:
                missing.append(index)
                continue
            rect = self._element_rect(element)
            if rect is None:
                missing.append(index)
                continue
            entries.append(
                {
                    "index": index,
                    "coordinate": self._order_coordinate(rect, params.direction),
                    "rect": rect,
                }
            )
        actual_order = [int(entry["index"]) for entry in sorted(entries, key=lambda entry: float(entry["coordinate"]))]
        expected_order = params.expected_order or list(range(len(params.elements)))
        if not params.require_all:
            expected_order = [index for index in expected_order if index in actual_order]
        output = {
            "direction": params.direction,
            "elements_found": len(entries),
            "elements_total": len(params.elements),
            "actual_order": actual_order,
            "expected_order": expected_order,
            "positions": entries,
        }
        if missing and params.require_all:
            return self._failed(
                "target_resolution_error",
                "One or more ordered elements were not found.",
                output=output,
                metadata={"missing_indexes": missing},
            )
        if self._order_matches(entries, expected_order, params.tolerance or 0.0):
            return self._passed(output)
        return self._failed(
            "assertion_error",
            "Elements are not in the expected order.",
            output=output,
        )

    @_macos_driver_tool("assertWithAI", description="Evaluate an explicit macOS visual assertion with AI.")
    def assert_with_ai(self, params: MacOSAssertWithAIParams) -> dict[str, object]:
        return self._run_ai_assertion_tool(params)

    def screenshot(self, params: object | None = None) -> bytes:
        session = self._require_session()
        screenshot = getattr(session, "get_screenshot_as_png", None)
        if callable(screenshot):
            data = screenshot()
            if isinstance(data, bytes):
                return data
        raise ConfigurationError("Appium Mac2 screenshot capture returned no image.")

    def close(self) -> None:
        session = self._session
        if session is None:
            return
        quit_session = getattr(session, "quit", None)
        if callable(quit_session):
            quit_session()
        self._session = None

    def _ensure_session(self, params: MacOSLaunchAppParams | None = None) -> object:
        params = params or MacOSLaunchAppParams()
        if self._session is None:
            return self._create_session(params)
        if params.new_session:
            self._require_creation_identity(params)
            self.close()
            return self._create_session(params)
        if params.app_path is not None or params.arguments is not None:
            raise ConfigurationError("app_path and arguments require new_session=true when a Mac2 session already exists.")
        bundle_id = self._effective_bundle_id(params.bundle_id)
        if bundle_id is None:
            raise ConfigurationError("macOS application activation requires bundle_id or a configured bundle id.")
        activate = getattr(self._session, "activate_app", None)
        if not callable(activate):
            raise ConfigurationError("Active Appium Mac2 session cannot activate applications.")
        activate(bundle_id)
        return self._session

    def _create_session(self, params: MacOSLaunchAppParams) -> object:
        capabilities: dict[str, object] = {
            "platformName": "Mac",
            "appium:automationName": "Mac2",
            "appium:newCommandTimeout": self.new_command_timeout_seconds,
        }
        bundle_id, app_path = self._require_creation_identity(params)
        if bundle_id:
            capabilities["appium:bundleId"] = bundle_id
        if app_path:
            capabilities["appium:app"] = app_path
        if params.arguments:
            capabilities["appium:arguments"] = params.arguments
        try:
            from appium import webdriver
            from appium.options.mac import Mac2Options
        except ImportError as exc:
            raise ConfigurationError(
                "Appium Python client is required for AppiumMac2Driver.",
                context={"action": "Reinstall or repair fsq-agent."},
            ) from exc
        options = Mac2Options().load_capabilities(capabilities)
        self._session = webdriver.Remote(self.server_url, options=options)
        return self._session

    def _require_creation_identity(self, params: MacOSLaunchAppParams) -> tuple[str | None, str | None]:
        bundle_id = self._effective_bundle_id(params.bundle_id)
        app_path = self._effective_app_path(params.app_path)
        if bundle_id is None and app_path is None:
            raise ConfigurationError("macOS Appium Mac2 session requires bundle_id or app_path.")
        return bundle_id, app_path

    def _effective_bundle_id(self, bundle_id: str | None) -> str | None:
        if isinstance(bundle_id, str) and bundle_id.strip():
            return bundle_id.strip()
        return self.bundle_id

    def _effective_app_path(self, app_path: str | None) -> str | None:
        if isinstance(app_path, str) and app_path.strip():
            return app_path.strip()
        return self.app_path

    def _require_session(self) -> object:
        if self._session is None:
            raise ConfigurationError("Appium Mac2 session is not available. Call launchApp before macOS Appium actions.", context={"resolution_reason": "session_unavailable"})
        return self._session

    def _resolve_element_or_none(self, params: BaseModel) -> object | None:
        data = params.model_dump(mode="python", exclude_none=True)
        locator = data.get("locator")
        if isinstance(locator, dict):
            return self._element_from_locator(MacOSLocator.model_validate(locator))
        target = data.get("target")
        if isinstance(target, str) and target.strip():
            return self._element_by_accessibility_or_name(target.strip())
        return None

    def _element_from_locator(self, locator: MacOSLocator) -> object | None:
        session = self._require_session()
        constraints = self._locator_constraints(locator)
        if not constraints and not locator.xpath and not locator.predicate:
            return None
        selector = f"({locator.xpath})" if locator.xpath else "//*"
        if constraints:
            selector += "[" + " and ".join(constraints) + "]"
        elements = self._find_elements(session, "xpath", selector)
        if locator.predicate:
            predicate_matches = self._find_elements(session, "-ios predicate string", locator.predicate)
            allowed = {self._element_identity(element) for element in predicate_matches}
            elements = [element for element in elements if self._element_identity(element) in allowed]
        unique = {self._element_identity(element): element for element in elements}
        if len(unique) > 1:
            raise ConfigurationError(
                "macOS locator matches multiple elements; refine the locator.",
                context={"resolution_reason": "ambiguous", "match_count": len(unique), "candidates": [self._candidate_summary(element) for element in list(unique.values())[:5]]},
            )
        return next(iter(unique.values()), None)

    def _locator_constraints(self, locator: MacOSLocator) -> list[str]:
        constraints = []
        for field, attribute in (("accessibilityId", "identifier"), ("label", "label"), ("value", "value"), ("role", "role")):
            value = getattr(locator, field)
            if value:
                constraints.append(f"@{attribute}={self._xpath_literal(value)}")
        if locator.name:
            literal = self._xpath_literal(locator.name)
            constraints.append("(" + " or ".join(f"@{key}={literal}" for key in ("identifier", "name", "label", "value")) + ")")
        for value in (locator.controlType, locator.className):
            if value:
                literal = self._xpath_literal(value)
                constraints.append(f"(name()={literal} or @type={literal})")
        return constraints

    def _element_identity(self, element: object) -> str:
        identifier = getattr(element, "id", None)
        if not isinstance(identifier, str) or not identifier:
            raise ConfigurationError("Cannot establish unique macOS element identity.", context={"resolution_reason": "backend_error"})
        return identifier

    def _candidate_summary(self, element: object) -> dict[str, object]:
        getter = getattr(element, "get_attribute", None)
        attrs = {}
        if callable(getter):
            for key in ("identifier", "label", "value", "type", "enabled", "visible", "selected"):
                try:
                    value = getter(key)
                except Exception:  # noqa: BLE001 -- optional diagnostic attributes must not expose backend errors.
                    value = None
                if value is not None:
                    attrs[key] = str(value)
        rect = self._element_rect(element)
        if rect:
            attrs.update({key: str(value) for key, value in rect.items()})
        return candidate(ElementTree.Element(attrs.get("type", "unknown"), attrs))

    def _element_by_accessibility_or_name(self, value: str) -> object | None:
        return self._element_from_locator(MacOSLocator(name=value))

    def _find_elements(self, session: object, strategy: str, locator: str) -> list[object]:
        find_elements = getattr(session, "find_elements", None)
        if not callable(find_elements):
            raise ConfigurationError("Mac2 session cannot enumerate element matches.", context={"resolution_reason": "backend_error"})
        try:
            result = find_elements(strategy, locator)
        except Exception as exc:  # noqa: BLE001 -- optional backend exception types are loaded lazily.
            self._raise_resolution_error(exc)
        if not isinstance(result, list) or len(result) > 10000:
            raise ConfigurationError("Mac2 element enumeration is invalid or exceeds safe limits.", context={"resolution_reason": "backend_error"})
        return result

    def _raise_resolution_error(self, error: Exception) -> None:
        from selenium.common.exceptions import InvalidSelectorException, InvalidSessionIdException, NoSuchWindowException

        reason = "backend_error"
        if isinstance(error, InvalidSelectorException):
            reason = "invalid_locator"
        elif isinstance(error, (InvalidSessionIdException, NoSuchWindowException)):
            reason = "session_unavailable"
        raise ConfigurationError(f"macOS element lookup failed: {reason}.", context={"resolution_reason": reason}) from None

    def _click_count(self, params: BaseModel, *, count: int) -> dict[str, object]:
        point = self._point_from_params(params)
        if point is None:
            element = self._resolve_element_or_none(params)
            point = self._element_center(element) if element is not None else None
        if point is None:
            return self._target_missing(params)
        self._perform_pointer_click(point, click_count=count)
        return self._passed({"point": point.model_dump(mode="json"), "click_count": count})

    def _point_from_endpoint(self, endpoint: MacOSDragEndpoint) -> MacOSPoint | None:
        point = self._point_from_params(endpoint)
        if point is not None:
            return point
        element = self._resolve_element_or_none(endpoint)
        return self._element_center(element) if element is not None else None

    def _point_from_params(self, params: BaseModel) -> MacOSPoint | None:
        data = params.model_dump(mode="python", exclude_none=True)
        locator_data = data.get("locator", {})
        if data.get("target") or (isinstance(locator_data, dict) and any(value for key, value in locator_data.items() if key != "point")):
            element = self._resolve_element_or_none(params)
            return self._element_center(element) if element is not None else None
        point = data.get("point")
        if isinstance(point, dict):
            return MacOSPoint.model_validate(point)
        locator = data.get("locator")
        if isinstance(locator, dict) and isinstance(locator.get("point"), dict):
            return MacOSPoint.model_validate(locator["point"])
        return None

    def _perform_pointer_click(self, point: MacOSPoint, *, button: str = "left", click_count: int = 1) -> None:
        session = self._require_session()
        try:
            from selenium.webdriver.common.actions.action_builder import ActionBuilder
        except ImportError as exc:
            raise ConfigurationError(
                "Selenium action builder is required for Appium Mac2 pointer actions.",
                context={"action": "Reinstall or repair fsq-agent."},
            ) from exc
        actions = ActionBuilder(session)
        pointer = actions.pointer_action
        pointer.move_to_location(point.x, point.y)
        button_index = 2 if button == "right" else 0
        for _ in range(click_count):
            pointer.pointer_down(button=button_index)
            pointer.pointer_up(button=button_index)
        actions.perform()

    def _perform_pointer_move(self, point: MacOSPoint) -> None:
        session = self._require_session()
        try:
            from selenium.webdriver.common.actions.action_builder import ActionBuilder
        except ImportError as exc:
            raise ConfigurationError(
                "Selenium action builder is required for Appium Mac2 pointer actions.",
                context={"action": "Reinstall or repair fsq-agent."},
            ) from exc
        actions = ActionBuilder(session)
        actions.pointer_action.move_to_location(point.x, point.y)
        actions.perform()

    def _perform_pointer_drag(self, source: MacOSPoint, destination: MacOSPoint, *, duration_ms: int | None) -> None:
        session = self._require_session()
        try:
            from selenium.webdriver.common.actions.action_builder import ActionBuilder
        except ImportError as exc:
            raise ConfigurationError(
                "Selenium action builder is required for Appium Mac2 pointer actions.",
                context={"action": "Reinstall or repair fsq-agent."},
            ) from exc
        actions = ActionBuilder(session)
        pointer = actions.pointer_action
        pointer.move_to_location(source.x, source.y)
        pointer.pointer_down()
        pointer.pause((duration_ms or 250) / 1000)
        pointer.move_to_location(destination.x, destination.y)
        pointer.pointer_up()
        actions.perform()

    def _element_displayed(self, element: object) -> bool:
        try:
            displayed = getattr(element, "is_displayed", None)
            if callable(displayed):
                return bool(displayed())
        except Exception as exc:  # noqa: BLE001 -- a failed state probe is not an invisibility verdict.
            self._raise_resolution_error(exc)
        raise ConfigurationError("Mac2 element cannot report visibility.", context={"resolution_reason": "backend_error"})

    def _element_center(self, element: object | None) -> MacOSPoint | None:
        rect = self._element_rect(element)
        if rect is None:
            return None
        x = rect.get("x")
        y = rect.get("y")
        width = rect.get("width")
        height = rect.get("height")
        if all(isinstance(value, (int, float)) for value in (x, y, width, height)):
            return MacOSPoint(x=int(float(x) + float(width) / 2), y=int(float(y) + float(height) / 2))
        return None

    def _element_rect(self, element: object | None) -> dict[str, float] | None:
        if element is None:
            return None
        try:
            rect = getattr(element, "rect", None)
        except Exception as exc:  # noqa: BLE001 -- preserve state/geometry backend failure distinctions.
            self._raise_resolution_error(exc)
        if isinstance(rect, dict):
            try:
                return {key: float(rect[key]) for key in ("x", "y", "width", "height")}
            except (KeyError, TypeError, ValueError):
                return None
        return None

    def _order_coordinate(self, rect: dict[str, float], direction: str) -> float:
        if direction == "horizontal":
            return rect["x"] + rect["width"] / 2
        return rect["y"] + rect["height"] / 2

    def _order_matches(self, entries: list[dict[str, object]], expected_order: list[int], tolerance: float) -> bool:
        coordinates_by_index = {int(entry["index"]): float(entry["coordinate"]) for entry in entries}
        if any(index not in coordinates_by_index for index in expected_order):
            return False
        expected_coordinates = [coordinates_by_index[index] for index in expected_order]
        return all(left <= right + tolerance for left, right in pairwise(expected_coordinates))

    def _simplify_page_source(self, source: str, *, max_depth: int, include_attributes: bool) -> dict[str, object]:
        if not source.strip():
            return {"format": "xml", "root": None, "source_length": 0}
        try:
            root = parse_source(source)
        except (ElementTree.ParseError, ValueError) as exc:
            return self._unparsed_page_source(source, exc)
        try:
            return {
                "format": "xml",
                "source_length": len(source),
                "node_count": self._element_count(root),
                "root": self._element_snapshot(
                    root,
                    depth=1,
                    max_depth=max_depth,
                    include_attributes=include_attributes,
                ),
            }
        # Snapshot capture must remain available if local compaction encounters unexpected backend data.
        except Exception as exc:  # noqa: BLE001
            return self._unparsed_page_source(source, exc)

    def _unparsed_page_source(self, source: str, error: Exception) -> dict[str, object]:
        preview = source[:DEFAULT_MACOS_SNAPSHOT_SOURCE_PREVIEW_CHARS]
        return {
            "format": "unparsed_xml",
            "source_preview": preview,
            "source_length": len(source),
            "truncated": len(source) > len(preview),
            "parse_error": str(error),
        }

    def _element_snapshot(
        self,
        element: ElementTree.Element,
        *,
        depth: int,
        max_depth: int,
        include_attributes: bool,
    ) -> dict[str, object]:
        snapshot = self._compact_element_snapshots(element, include_attributes=include_attributes, preserve_node=True)[0]
        self._bound_snapshot_depth(snapshot, depth=depth, max_depth=max_depth)
        return snapshot

    def _compact_element_snapshots(
        self,
        element: ElementTree.Element,
        *,
        include_attributes: bool,
        preserve_node: bool = False,
    ) -> list[dict[str, object]]:
        children = [child_snapshot for child in element for child_snapshot in self._compact_element_snapshots(child, include_attributes=include_attributes)]
        attributes = self._snapshot_attributes(element.attrib, include_attributes=include_attributes)
        text = (element.text or "").strip()[:DEFAULT_MACOS_SNAPSHOT_TEXT_LIMIT_CHARS]
        interactive = self._xml_name(element.tag).removeprefix("XCUIElementType") in {
            "Button",
            "CheckBox",
            "RadioButton",
            "PopUpButton",
            "ComboBox",
            "TextField",
            "SecureTextField",
            "TextArea",
            "Slider",
            "Switch",
            "Link",
        }
        if not preserve_node and not interactive and not self._has_snapshot_signal(attributes, text=text):
            return children

        snapshot: dict[str, object] = {"type": self._xml_name(element.tag)}
        if attributes:
            snapshot["attributes"] = attributes
        clipped = [key for key, value in element.attrib.items() if self._xml_name(key) in MACOS_SNAPSHOT_TEXT_ATTRIBUTE_KEYS and len(value.strip()) > DEFAULT_MACOS_SNAPSHOT_TEXT_LIMIT_CHARS]
        if clipped:
            snapshot["truncated_attributes"] = clipped
        if len((element.text or "").strip()) > DEFAULT_MACOS_SNAPSHOT_TEXT_LIMIT_CHARS:
            snapshot["text_truncated"] = True
        if text:
            snapshot["text"] = text
        if children:
            snapshot["children"] = children
        return [snapshot]

    def _bound_snapshot_depth(self, snapshot: dict[str, object], *, depth: int, max_depth: int) -> None:
        children = snapshot.get("children")
        if not isinstance(children, list) or not children:
            return
        if depth >= max_depth:
            snapshot.pop("children")
            snapshot["children_truncated"] = len(children)
            return
        for child in children:
            if isinstance(child, dict):
                self._bound_snapshot_depth(child, depth=depth + 1, max_depth=max_depth)

    def _snapshot_attributes(self, attributes: dict[str, str], *, include_attributes: bool) -> dict[str, object]:
        selected: dict[str, object] = {}
        for key, raw_value in attributes.items():
            normalized_key = self._xml_name(key)
            value = str(raw_value).strip()
            if not value or (not include_attributes and normalized_key not in DEFAULT_MACOS_SNAPSHOT_ATTRIBUTE_KEYS):
                continue
            default_state = MACOS_SNAPSHOT_STATE_DEFAULTS.get(normalized_key)
            if default_state is not None and value.lower() == default_state:
                continue
            if normalized_key in MACOS_SNAPSHOT_TEXT_ATTRIBUTE_KEYS:
                value = value[:DEFAULT_MACOS_SNAPSHOT_TEXT_LIMIT_CHARS]
            selected[normalized_key] = value
        return selected

    def _has_snapshot_signal(self, attributes: dict[str, object], *, text: str) -> bool:
        non_default_state = any(key in MACOS_SNAPSHOT_STATE_DEFAULTS for key in attributes)
        identity_or_text = bool(text) or any(key in MACOS_SNAPSHOT_IDENTITY_ATTRIBUTE_KEYS for key in attributes)
        if self._is_zero_size(attributes) or str(attributes.get("visible", "")).lower() == "false":
            return identity_or_text or non_default_state
        return identity_or_text or non_default_state or any(key in MACOS_SNAPSHOT_SEMANTIC_ATTRIBUTE_KEYS for key in attributes)

    def _is_zero_size(self, attributes: dict[str, object]) -> bool:
        dimensions = [attributes.get(key) for key in ("width", "height")]
        for value in dimensions:
            if value is None:
                continue
            try:
                if float(str(value)) == 0:
                    return True
            except ValueError:
                continue
        return False

    def _element_count(self, element: ElementTree.Element) -> int:
        return 1 + sum(self._element_count(child) for child in list(element))

    def _xml_name(self, value: str) -> str:
        return value.rsplit("}", 1)[-1] if "}" in value else value

    def _window_size(self) -> tuple[int, int] | None:
        session = self._session
        if session is None:
            return None
        get_window_size = getattr(session, "get_window_size", None)
        if not callable(get_window_size):
            return None
        try:
            size = get_window_size()
        # Appium window probes use optional-backend exception classes outside the core contract.
        except Exception:  # noqa: BLE001
            return None
        if not isinstance(size, dict):
            return None
        width = size.get("width")
        height = size.get("height")
        if isinstance(width, int) and isinstance(height, int):
            return width, height
        return None

    def _safe_attr(self, obj: object | None, name: str) -> object | None:
        if obj is None:
            return None
        try:
            return getattr(obj, name, None)
        # WebDriver properties may execute remote calls and raise optional-backend exception classes.
        except Exception:  # noqa: BLE001
            return None

    def _xpath_literal(self, value: str) -> str:
        if "'" not in value:
            return f"'{value}'"
        if '"' not in value:
            return f'"{value}"'
        parts = value.split("'")
        return "concat(" + ', "\'", '.join(f"'{part}'" for part in parts) + ")"

    def _target_missing(self, params: BaseModel) -> dict[str, object]:
        return self._failed(
            "target_resolution_error",
            "Target was not found.",
            metadata={"resolution_reason": "not_found"},
        )

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
