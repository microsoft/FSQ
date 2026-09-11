# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import logging
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, TypeVar
from urllib.parse import urljoin, urlsplit

from fsq_agent import models as m
from fsq_agent.drivers._ai_assertion import AIAssertionBackendToolMixin
from fsq_agent.drivers._capabilities import _web_driver_tool

from ._errors import WebBackendError, failure_result
from ._events import run_trigger
from ._locators import compile_locator, text_predicate
from ._observation import observe
from ._resolve import Deadline, resolve_unique, wait_condition

DEFAULT_WEB_WAIT_TIMEOUT_MS = 10000
SUPPORTED_WEB_CHANNELS = frozenset({"chromium", "chrome", "chrome-beta", "chrome-dev", "chrome-canary", "msedge", "msedge-beta", "msedge-dev", "msedge-canary"})
_BROWSER_NOT_STARTED_MESSAGE = "Browser is not started. Call startBrowser before Web page actions."
_T = TypeVar("_T")


class PlaywrightWebDriver(AIAssertionBackendToolMixin):
    """Owned synchronous Playwright operations, confined to one lazy worker."""

    backend = "playwright"

    def __init__(
        self,
        *,
        channel: str = "chrome",
        executable_path: str | Path | None = None,
        headless: bool = True,
        base_url: str | None = None,
        viewport: tuple[int, int] | None = None,
        page: object | None = None,
    ) -> None:
        self.channel = channel.strip() if isinstance(channel, str) else channel
        if self.channel not in SUPPORTED_WEB_CHANNELS:
            raise m.ConfigurationError("Unsupported Playwright browser channel.", context={"channel": self.channel, "supported": sorted(SUPPORTED_WEB_CHANNELS)})
        self.executable_path = str(Path(executable_path)) if executable_path else None
        self.headless = headless
        self.base_url = base_url.rstrip("/") + "/" if isinstance(base_url, str) and base_url.strip() else None
        self.viewport = viewport
        self._playwright: Any = None
        self._browser: Any = None
        self._context: Any = None
        self._executor: ThreadPoolExecutor | None = None
        self._executor_lock = threading.Lock()
        self.page: Any = page
        self._pages: dict[str, Any] = {"main": page} if page is not None else {}
        self._active_alias: str | None = "main" if page is not None else None
        self._effect = "not_started"
        self._expected_event: tuple[int, str] | None = None
        self._unexpected_dialog: set[int] = set()
        self._unexpected_popup: set[int] = set()
        self._listeners: dict[int, tuple[Any, Callable, Callable]] = {}
        self._watch(page)

    def context(self) -> dict[str, object]:
        return self._run_sync(self._context_payload)

    def _context_payload(self) -> dict[str, object]:
        size = getattr(self.page, "viewport_size", None)
        viewport = self.viewport or ((size["width"], size["height"]) if isinstance(size, dict) else None)
        return {
            "session_id": f"playwright:{self.channel}",
            "current_url": self._page_url(),
            "screen_size": viewport,
            "metadata": {
                "backend": self.backend,
                "channel": self.channel,
                "browser_executable_configured": self.executable_path is not None,
                "headless": self.headless,
                "base_url_configured": self.base_url is not None,
                "browser_started": bool(self._pages),
                "active_page": self._active_alias,
            },
        }

    @_web_driver_tool(
        "startBrowser", description="Explicitly start or reuse the configured owned browser/main page; idempotent, never attaches to another session. Subsequent operations need declared page aliases."
    )
    def start_browser(self, params: m.WebStartBrowserParams) -> dict[str, object]:
        if self.page is None:
            self._ensure_executor()
        try:
            return self._run_sync(lambda: self._start_browser(params))
        except BaseException:
            if self.page is None and all(resource is None for resource in (self._context, self._browser, self._playwright)):
                try:
                    self._shutdown_executor()
                except BaseException as cleanup_error:  # noqa: BLE001 - primary startup/cancellation retains precedence.
                    logging.getLogger(__name__).warning("Web startup worker disposal failed (%s)", type(cleanup_error).__name__)
            raise

    def _start_browser(self, params: m.WebStartBrowserParams) -> dict[str, object]:
        if self._pages:
            return self._passed({"already_started": True, "url": self._page_url()}, effect="completed")
        if any(resource is not None for resource in (self._context, self._browser, self._playwright)):
            self._close()
        try:
            self.page = self._create_page()
            self._pages = {"main": self.page}
            self._active_alias = "main"
            self._watch(self.page)
        except BaseException:
            try:
                self._close()
            except BaseException as cleanup_error:  # noqa: BLE001 - release every partial acquisition while preserving startup failure.
                logging.getLogger(__name__).warning("Web startup resource disposal failed (%s)", type(cleanup_error).__name__)
            raise
        return self._passed({"already_started": False, "url": self._page_url()}, effect="completed")

    @_web_driver_tool("closeBrowser", description="Explicitly close all owned browser/pages; idempotent and replayable. Does not start an unopened browser; runtime disposal remains separate.")
    def close_browser(self, params: m.WebCloseBrowserParams) -> dict[str, object]:
        return self._call(lambda: self._close_browser(params), check_modal=False)

    def _close_browser(self, params: m.WebCloseBrowserParams) -> dict[str, object]:
        already = not self._pages and all(resource is None for resource in (self._context, self._browser, self._playwright))
        self._perform(self._close)
        return {"already_closed": already}

    @_web_driver_tool(
        "navigateTo",
        description="Navigate a declared page to an absolute or configured application-relative URL; never starts a browser or invents URL parameters. A timeout may follow navigation effects.",
    )
    def navigate_to(self, params: m.WebNavigateToParams) -> dict[str, object]:
        return self._call(lambda: self._navigate(params, "goto"))

    @_web_driver_tool("navigateBack", description="Move one history entry back on the explicit page using bounded navigation waits; does not change tabs or establish application readiness.")
    def navigate_back(self, params: m.WebNavigateBackParams) -> dict[str, object]:
        return self._call(lambda: self._navigate(params, "go_back"))

    @_web_driver_tool(
        "navigateForward", description="Move one history entry forward on the explicit page; preserve the requested wait state and inspect after a timeout rather than retrying automatically."
    )
    def navigate_forward(self, params: m.WebNavigateForwardParams) -> dict[str, object]:
        return self._call(lambda: self._navigate(params, "go_forward"))

    @_web_driver_tool("reloadPage", description="Reload the declared page using bounded navigation completion; networkidle is not an application-specific readiness assertion.")
    def reload_page(self, params: m.WebReloadPageParams) -> dict[str, object]:
        return self._call(lambda: self._navigate(params, "reload"))

    def _navigate(self, params: Any, method: str) -> dict[str, Any]:
        timeout = Deadline(params.timeout_ms)
        page = self._page(params.page)
        args = [self._resolve_url(params.url)] if method == "goto" else []
        response = self._perform(lambda: getattr(page, method)(*args, wait_until=params.wait_until, timeout=timeout.remaining()))
        return {"page": params.page, "url": self._page_url(page), "status": getattr(response, "status", None)}

    @_web_driver_tool(
        "clickOn",
        description="Click one uniquely actionable replay locator with explicit button/count/modifiers. Bound popup/dialog listeners precede the trigger; ambiguous preparation has no effects, event timeout may.",
    )
    def click_on(self, params: m.WebClickOnParams) -> dict[str, object]:
        return self._call(lambda: self._click(params))

    def _click(self, params: m.WebClickOnParams) -> Any:
        timeout = Deadline(params.timeout_ms)
        page, locator = self._target(params.target, timeout, visible=True, enabled=True)
        self._prepare_event(params.expect)
        return self._trigger(page, lambda: locator.click(button=params.button, click_count=params.click_count, modifiers=params.modifiers, timeout=timeout.remaining()), params.expect, timeout)

    @_web_driver_tool(
        "hoverOn", description="Hover a unique visible element without clicking or coordinate fallback; explicit modifiers apply only to this hover. Refine ambiguous locators before retrying."
    )
    def hover_on(self, params: m.WebHoverOnParams) -> dict[str, object]:
        return self._call(lambda: self._element_action(params, "hover", modifiers=params.modifiers))

    @_web_driver_tool(
        "dragTo", description="Drag between two element locators on the same declared page. Both endpoints must be uniquely ready before either is touched; no coordinate or first-match fallback."
    )
    def drag_to(self, params: m.WebDragToParams) -> dict[str, object]:
        return self._call(lambda: self._drag(params))

    def _drag(self, params: m.WebDragToParams) -> None:
        timeout = Deadline(params.timeout_ms)
        _, source = self._target(params.source, timeout, visible=True)
        _, destination = self._target(params.destination, timeout, visible=True)
        self._perform(lambda: source.drag_to(destination, timeout=timeout.remaining()))

    @_web_driver_tool(
        "scroll", description="Scroll the explicitly chosen page viewport or unique element container by CSS-pixel deltas. Does not infer a root, click a target, or accept script input."
    )
    def scroll(self, params: m.WebScrollParams) -> dict[str, object]:
        return self._call(lambda: self._scroll(params))

    def _scroll(self, params: m.WebScrollParams) -> None:
        timeout = Deadline(params.timeout_ms)
        page, element = self._scope(params.scope, timeout, visible=True)
        delta = [params.delta_x, params.delta_y]
        if element is None:
            self._perform(lambda: page.evaluate("([x,y])=>window.scrollBy(x,y)", delta))
        else:
            self._perform(lambda: element.evaluate("(node,[x,y])=>node.scrollBy(x,y)", delta, timeout=timeout.remaining()))

    @_web_driver_tool(
        "scrollIntoView", description="Scroll one uniquely resolved element into view without clicking. The authored locator remains a rule; missing or ambiguous queries are never repaired."
    )
    def scroll_into_view(self, params: m.WebScrollIntoViewParams) -> dict[str, object]:
        return self._call(lambda: self._element_action(params, "scroll_into_view_if_needed", visible=False))

    @_web_driver_tool(
        "fillText",
        description="Replace uniquely targeted editable contents; empty literal text clears. Use type_text for sequential append. Runtime-secret execution text is never returned as replay data.",
    )
    def fill_text(self, params: m.WebFillTextParams) -> dict[str, object]:
        return self._call(lambda: self._element_action(params, "fill", params.text, editable=True))

    @_web_driver_tool(
        "typeText", description="Append text via sequential key events without clearing; use fill_text for replacement. Delay is between keys only, and resolved secret text is not echoed."
    )
    def type_text(self, params: m.WebTypeTextParams) -> dict[str, object]:
        return self._call(lambda: self._element_action(params, "press_sequentially", params.text, editable=True, delay=params.delay_ms))

    @_web_driver_tool(
        "setChecked", description="Set one checkbox/radio to the requested boolean state idempotently, never blind-toggle. Unsupported control types and radio unchecking fail before effects."
    )
    def set_checked(self, params: m.WebSetCheckedParams) -> dict[str, object]:
        return self._call(lambda: self._set_checked(params))

    def _set_checked(self, params: m.WebSetCheckedParams) -> None:
        timeout = Deadline(params.timeout_ms)
        _, locator = self._target(params.target, timeout, visible=True, enabled=True)
        control = locator.evaluate("(node)=>({tag:node.tagName,type:node.type,role:node.getAttribute('role')})", timeout=timeout.remaining())
        if control["type"] not in {"checkbox", "radio"} and control["role"] not in {"checkbox", "radio", "switch"}:
            raise WebBackendError("invalid_params", "set_checked requires a checkbox or radio control.")
        if (control["type"] == "radio" or control["role"] == "radio") and not params.checked:
            raise WebBackendError("invalid_params", "Radio controls cannot be unchecked directly.")
        self._perform(lambda: locator.set_checked(params.checked, timeout=timeout.remaining()))

    @_web_driver_tool(
        "selectOption",
        description="Select native select options by exactly one value/label/index mode using complete strings and zero-based indices. Multiple items require multi-select; custom menus use locator actions.",
    )
    def select_option(self, params: m.WebSelectOptionParams) -> dict[str, object]:
        return self._call(lambda: self._select(params))

    def _select(self, params: m.WebSelectOptionParams) -> dict[str, Any]:
        timeout = Deadline(params.timeout_ms)
        _, locator = self._target(params.target, timeout, visible=True, enabled=True)
        facts = locator.evaluate(
            """node=>({tag:node.tagName,multiple:node.multiple,options:node.options ?
            Array.from(node.options).map((o,index)=>({value:o.value,label:o.label,index,disabled:o.disabled ||
            (o.parentElement.tagName==='OPTGROUP' && o.parentElement.disabled)})) : []})""",
            timeout=timeout.remaining(),
        )
        if facts["tag"] != "SELECT":
            raise WebBackendError("invalid_params", "Native select_option requires an HTML select, not a custom dropdown.")
        selection = params.selection
        field = {"value": "values", "label": "labels", "index": "indices"}[selection.kind]
        values = getattr(selection, field)
        if len(values) > 1 and not facts["multiple"]:
            raise WebBackendError("invalid_params", "Multiple options require a native multi-select.")
        for value in values:
            matches = [option for option in facts["options"] if option[selection.kind] == value]
            if not matches or all(option["disabled"] for option in matches):
                raise WebBackendError("invalid_params", "A requested select option is missing, disabled, or out of range.", parameter_path=f"selection.{field}")
            if len(matches) > 1 and not facts["multiple"]:
                raise WebBackendError("target_ambiguous", "The selection matches duplicate native options; use an explicit index.", match_count=len(matches))
        selected = self._perform(lambda: locator.select_option(**{selection.kind: values}, timeout=timeout.remaining()))
        return {"selected": selected}

    @_web_driver_tool(
        "pressKey",
        description="Press a supported key/chord on an explicit page or unique element; targeted Enter is preferred to ambient focus. Expected popup/dialog listeners are bound before pressing.",
    )
    def press_key(self, params: m.WebPressKeyParams) -> dict[str, object]:
        return self._call(lambda: self._key(params))

    def _key(self, params: m.WebPressKeyParams) -> Any:
        timeout = Deadline(params.timeout_ms)
        page, element = self._scope(params.scope, timeout, visible=True)
        if element is not None and not element.is_enabled(timeout=timeout.remaining()):
            raise WebBackendError("target_not_actionable", "The element keyboard target is disabled.")
        self._prepare_event(params.expect)
        operation = (lambda: element.press(params.key, timeout=timeout.remaining())) if element is not None else (lambda: page.keyboard.press(params.key))
        return self._trigger(page, operation, params.expect, timeout)

    @_web_driver_tool(
        "waitFor",
        description="Wait for one explicit element/text/URL/load-state condition, bounded by timeout_ms; pure sleeping uses wait_ms. Absence can legitimately match zero; invalid selectors remain errors.",
    )
    def wait_for(self, params: m.WebWaitForParams) -> dict[str, object]:
        return self._call(lambda: self._wait(params))

    def _wait(self, params: m.WebWaitForParams) -> dict[str, Any]:
        condition = params.condition
        timeout = Deadline(params.timeout_ms)
        if condition.kind == "element":
            page = self._page(condition.target.page)
            locator = compile_locator(page, condition.target, timeout=timeout)
            state = condition.state
            predicate = {"visible": lambda node: node.is_visible(), "hidden": lambda node: not node.is_visible(), "attached": lambda node: True, "detached": lambda node: False}[state]
            matched = wait_condition(page, locator, predicate, timeout=timeout, allow_absent=state in {"hidden", "detached"})
        elif condition.kind == "text":
            page, element = self._scope(condition.scope, timeout)
            root = element or page
            locator = root.get_by_text(text_predicate(condition.text)).filter(visible=True)
            matched = self._poll(page, lambda: (locator.count() > 0) == condition.present, timeout)
        elif condition.kind == "url":
            page = self._page(condition.page)
            matched = self._poll(page, lambda: self._text_matches(page.url, condition.url, condition.exact), timeout)
        else:
            page = self._page(condition.page)
            page.wait_for_load_state(condition.state, timeout=timeout.remaining())
            matched = True
        if not matched:
            raise WebBackendError("timeout", "The declared condition did not hold within the bounded wait.")
        return {"condition": condition.kind}

    @_web_driver_tool("assertVisible", description="Assert a unique target becomes visible within the bounded wait. Missing/ambiguous locators are not repaired and no UI action is performed.")
    def assert_visible(self, params: m.WebAssertVisibleParams) -> dict[str, object]:
        return self._assertion(params, lambda node, _timeout: node.is_visible())

    @_web_driver_tool("assertNotVisible", description="Assert the target is hidden or absent; zero matches is valid. Multiple matches remain ambiguous and are not silently reduced to one.")
    def assert_not_visible(self, params: m.WebAssertNotVisibleParams) -> dict[str, object]:
        return self._assertion(params, lambda node, _timeout: not node.is_visible(), absent=True)

    @_web_driver_tool(
        "assertText", description="Assert normalized target text using exact equality or contains, never inferred prefix/regex semantics. Read-only and bounded; failure does not change the target."
    )
    def assert_text(self, params: m.WebAssertTextParams) -> dict[str, object]:
        return self._assertion(
            params, lambda node, timeout: self._text_matches(" ".join(node.inner_text(timeout=timeout.remaining()).split()), " ".join(params.text.value.split()), params.text.kind == "equals")
        )

    @_web_driver_tool(
        "assertState", description="Assert every supplied state predicate together on one locator, distinguishing unknown from false. Read-only; does not click or infer unsupported states."
    )
    def assert_state(self, params: m.WebAssertStateParams) -> dict[str, object]:
        return self._assertion(params, lambda node, timeout: self._matches_state(node, params.state, timeout))

    @_web_driver_tool(
        "assertValue", description="Assert exact form value or ordered multi-select values, not display labels or surrounding text. Values are compared in memory and never echoed into diagnostics."
    )
    def assert_value(self, params: m.WebAssertValueParams) -> dict[str, object]:
        return self._assertion(params, lambda node, timeout: self._matches_value(node, params.value, timeout))

    def _assertion(self, params: Any, predicate: Callable[[Any, Deadline], bool], *, absent: bool = False) -> dict[str, object]:
        def execute() -> None:
            timeout = Deadline(params.timeout_ms)
            page = self._page(params.target.page)
            locator = compile_locator(page, params.target, timeout=timeout)
            if not wait_condition(page, locator, lambda node: predicate(node, timeout), timeout=timeout, allow_absent=absent):
                raise WebBackendError("assertion_failed", "The deterministic Web assertion did not hold.")

        return self._call(execute)

    @staticmethod
    def _matches_state(node: Any, expected: m.WebExpectedState, timeout: Deadline) -> bool:
        for key, value in expected.model_dump(exclude_none=True).items():
            if key in {"visible", "enabled", "checked", "editable"}:
                actual = getattr(node, f"is_{key}")(timeout=timeout.remaining())
            elif key == "focused":
                actual = node.evaluate("(node)=>node.ownerDocument.activeElement===node", timeout=timeout.remaining())
            elif key == "selected":
                actual = node.evaluate(
                    "(node)=>'selected' in node ? node.selected : node.hasAttribute('aria-selected') ? node.getAttribute('aria-selected')==='true' : null", timeout=timeout.remaining()
                )
            else:
                actual = node.evaluate("(node)=>node.hasAttribute('aria-expanded') ? node.getAttribute('aria-expanded')==='true' : null", timeout=timeout.remaining())
            if actual is None:
                raise WebBackendError("invalid_params", "The control does not expose the requested state.", parameter_path=f"state.{key}")
            if actual != value:
                return False
        return True

    @staticmethod
    def _matches_value(node: Any, expected: str | list[str], timeout: Deadline) -> bool:
        if isinstance(expected, list):
            actual = node.evaluate("(node)=>node.tagName==='SELECT' && node.multiple ? Array.from(node.selectedOptions).map(o=>o.value) : null", timeout=timeout.remaining())
            if actual is None:
                raise WebBackendError("invalid_params", "A value list requires a native multi-select.")
        else:
            actual = node.input_value(timeout=timeout.remaining())
        return actual == expected

    @_web_driver_tool("takeScreenshot", description="Capture explicit-page PNG evidence without a caller-chosen host path. Screenshot destinations and artifact persistence belong to the Harness.")
    def take_screenshot(self, params: m.WebTakeScreenshotParams) -> dict[str, object]:
        return self._call(lambda: self._take_screenshot(params))

    def _take_screenshot(self, params: m.WebTakeScreenshotParams) -> dict[str, object]:
        png = self._screenshot(params)
        return {"page": params.page, "bytes": len(png), "png": png}

    def screenshot(self, params: m.WebTakeScreenshotParams | None = None) -> bytes:
        return self._run_sync(lambda: self._screenshot(params))

    def _screenshot(self, params: m.WebTakeScreenshotParams | None = None) -> bytes:
        params = params or m.WebTakeScreenshotParams(page=self._active_alias or "main")
        page = self._page(params.page)
        return page.screenshot(full_page=params.full_page, omit_background=params.omit_background)

    @_web_driver_tool(
        "uiSnapshot",
        description="Observe compact/scoped/full semantic page structure with checked replay locators and truthful omissions. Full source goes to evidence; inline remains bounded. Expand a region, never act by observation ID.",
    )
    def ui_snapshot(self, params: m.WebUiSnapshotParams) -> dict[str, object]:
        return self._call(lambda: self._snapshot(params), observation=True)

    def _snapshot(self, params: m.WebUiSnapshotParams) -> dict[str, Any]:
        timeout = Deadline(params.timeout_ms)
        self._check_continuation(params)
        page, element = self._scope(params.scope, timeout)
        alias = params.scope.page if params.scope.kind == "page" else params.scope.target.page
        return observe(page, alias, element or page, active=alias == self._active_alias, max_chars=params.max_chars, max_items=params.max_items, max_depth=params.max_depth, timeout=timeout)

    @_web_driver_tool(
        "findElements",
        description="Read bounded live query candidates with validated locators and parent scope. Explicit ordered selection rules remain rules; ordinary enumeration does not invent nth selections. Not recorded.",
    )
    def find_elements(self, params: m.WebFindElementsParams) -> dict[str, object]:
        return self._call(lambda: self._query(params, inspect=False), observation=True)

    @_web_driver_tool(
        "inspectElement",
        description="Read one unique control's state and native option labels/values/indices without changing it. Options and inline output are bounded; unavailable locators and omitted details are explicit. Not recorded.",
    )
    def inspect_element(self, params: m.WebInspectElementParams) -> dict[str, object]:
        return self._call(lambda: self._query(params, inspect=True), observation=True)

    def _query(self, params: Any, *, inspect: bool) -> dict[str, Any]:
        timeout = Deadline(params.timeout_ms)
        self._check_continuation(params)
        page = self._page(params.target.page)
        locator = compile_locator(page, params.target, timeout=timeout)
        if inspect:
            resolve_unique(page, locator, timeout=timeout)
        root = locator if inspect or locator.count() == 1 else page
        return observe(
            page,
            params.target.page,
            root,
            active=params.target.page == self._active_alias,
            max_chars=params.max_chars,
            max_items=params.max_items,
            timeout=timeout,
            max_options=params.max_options if inspect else 100,
            query=locator,
            authored=params.target,
        )

    @staticmethod
    def _check_continuation(params: Any) -> None:
        if params.continuation is not None:
            raise WebBackendError("invalid_continuation", "This backend does not issue continuation cursors; observe or query an explicit narrower scope.")

    @_web_driver_tool(
        "listPages", description="List only owned page aliases with URL/title and active state, without activating anything. Closed pages and transient tab indices are not usable contexts."
    )
    def list_pages(self, params: m.WebListPagesParams) -> dict[str, object]:
        return self._call(lambda: {"pages": [self._page_view(alias, page) for alias, page in self._pages.items() if not self._closed(page)]})

    @_web_driver_tool(
        "openPage",
        description="Create an owned page using a new explicit alias, optionally navigating it. Alias conflicts fail before page creation; navigation failure may leave the newly owned page present.",
    )
    def open_page(self, params: m.WebOpenPageParams) -> dict[str, object]:
        return self._call(lambda: self._open_page(params))

    def _open_page(self, params: m.WebOpenPageParams) -> dict[str, Any]:
        timeout = Deadline(params.timeout_ms)
        self._new_alias(params.page)
        url = self._resolve_url(params.url)
        if self._context is None:
            raise WebBackendError("browser_not_started", _BROWSER_NOT_STARTED_MESSAGE)

        def create() -> Any:
            page = self._context.new_page()
            self._register(params.page, page)
            if url != "about:blank":
                page.goto(url, wait_until=params.wait_until, timeout=timeout.remaining())
            return page

        page = self._perform(create)
        return self._page_view(params.page, page)

    @_web_driver_tool("activatePage", description="Bring an existing owned page alias to the foreground; aliases remain stable and no tab index or arbitrary browser attachment is accepted.")
    def activate_page(self, params: m.WebActivatePageParams) -> dict[str, object]:
        return self._call(lambda: self._activate_page(params))

    def _activate_page(self, params: m.WebActivatePageParams) -> dict[str, Any]:
        page = self._page(params.page)
        self._perform(page.bring_to_front)
        self._active_alias, self.page = params.page, page
        return self._page_view(params.page, page)

    @_web_driver_tool(
        "closePage", description="Close one declared owned page and invalidate its context, not the entire browser. Other aliases remain unchanged; use close_browser for explicit browser shutdown."
    )
    def close_page(self, params: m.WebClosePageParams) -> dict[str, object]:
        return self._call(lambda: self._close_page(params), check_modal=False)

    def _close_page(self, params: m.WebClosePageParams) -> dict[str, Any]:
        page = self._page(params.page)
        self._perform(page.close)
        self._unwatch(page)
        self._pages.pop(params.page)
        if self._active_alias == params.page:
            self._active_alias = None
            self.page = None
        return {"page": params.page, "closed": True}

    @_web_driver_tool(
        "assertWithAI",
        description="Evaluate an explicitly authored visual assertion through the injected evaluator; not a fallback for invalid locators, missing deterministic assertions, or unsupported interactions.",
    )
    def assert_with_ai(self, params: m.WebAssertWithAIParams) -> dict[str, object]:
        result = self._run_ai_assertion_tool(params)
        result.setdefault("metadata", {})["action_effect"] = "not_started"
        return result

    def _target(self, target: m.WebLocator, timeout: int | Deadline, *, visible: bool = False, enabled: bool = False, editable: bool = False) -> tuple[Any, Any]:
        timeout = Deadline.from_timeout(timeout)
        page = self._page(target.page)
        locator = compile_locator(page, target, timeout=timeout)
        try:
            return page, resolve_unique(page, locator, timeout=timeout, visible=visible, enabled=enabled, editable=editable)
        except WebBackendError as error:
            error.details["page"] = target.page
            if error.code == "target_ambiguous":
                try:
                    error.details["candidates"] = locator.evaluate_all("""nodes=>nodes.slice(0,3).map(node=>({
                        tag:node.tagName.toLowerCase(),role:node.getAttribute('role'),
                        label:(node.getAttribute('aria-label')||'').slice(0,160),
                        visible:!!node.getClientRects().length}))""")
                except Exception:  # noqa: BLE001 - best-effort safe diagnostics never replace the preparation failure.
                    error.details["candidate_diagnostics_unavailable"] = True
            raise

    def _scope(self, scope: Any, timeout: int | Deadline, *, visible: bool = False) -> tuple[Any, Any]:
        return (self._page(scope.page), None) if scope.kind == "page" else self._target(scope.target, timeout, visible=visible)

    def _element_action(self, params: Any, method: str, *args: Any, visible: bool = True, editable: bool = False, **kwargs: Any) -> None:
        timeout = Deadline(params.timeout_ms)
        _, locator = self._target(params.target, timeout, visible=visible, enabled=editable, editable=editable)
        self._perform(lambda: getattr(locator, method)(*args, timeout=timeout.remaining(), **kwargs))

    def _prepare_event(self, expectation: Any) -> None:
        if expectation is not None and expectation.kind == "popup":
            self._new_alias(expectation.page)

    def _trigger(self, page: Any, operation: Callable[[], Any], expectation: Any, timeout: Deadline) -> Any:
        if expectation is None:
            self._perform(operation)
            return None
        timeout.end = min(timeout.end, time.monotonic() + expectation.timeout_ms / 1000)
        if expectation.kind == "popup":
            popup = self._perform(lambda: self._event_call(page, operation, kind="popup", timeout=expectation.timeout_ms))
            self._register(expectation.page, popup)
            return {"event": "popup", "page": expectation.page}
        prompt = expectation.prompt_text.text if expectation.prompt_text is not None else None
        return self._perform(
            lambda: self._event_call(page, operation, kind="dialog", timeout=expectation.timeout_ms, dialog_type=expectation.dialog_type, action=expectation.action, prompt_text=prompt)
        )

    def _event_call(self, page: Any, operation: Callable[[], Any], **kwargs: Any) -> Any:
        self._expected_event = (id(page), kwargs["kind"])
        try:
            return run_trigger(page, operation, **kwargs)
        except Exception as error:
            if isinstance(error, WebBackendError) and error.code == "unexpected_dialog":
                self._unexpected_dialog.add(id(page))
            if "Timeout" in type(error).__name__:
                raise WebBackendError("event_timeout", "The trigger may have completed, but its expected event did not arrive.") from None
            raise
        finally:
            self._expected_event = None

    def _watch(self, page: Any) -> None:
        on = getattr(page, "on", None)
        if not callable(on) or id(page) in self._listeners:
            return

        def dialog_listener(dialog: Any) -> None:
            if self._expected_event != (id(page), "dialog"):
                self._unexpected_dialog.add(id(page))

        def popup_listener(popup: Any) -> None:
            if self._expected_event != (id(page), "popup"):
                self._unexpected_popup.add(id(page))

        on("dialog", dialog_listener)
        on("popup", popup_listener)
        self._listeners[id(page)] = (page, dialog_listener, popup_listener)

    def _unwatch(self, page: Any) -> None:
        registered = self._listeners.get(id(page))
        if registered is not None:
            page.remove_listener("dialog", registered[1])
            page.remove_listener("popup", registered[2])
            del self._listeners[id(page)]
        self._unexpected_dialog.discard(id(page))
        self._unexpected_popup.discard(id(page))

    def _register(self, alias: str, page: Any) -> None:
        self._pages[alias] = page
        self._watch(page)

    def _new_alias(self, alias: str) -> None:
        if alias in self._pages:
            raise WebBackendError("page_alias_conflict", "The requested page alias is already declared.", page=alias)

    def _page(self, alias: str) -> Any:
        if not self._pages:
            raise WebBackendError("browser_not_started", _BROWSER_NOT_STARTED_MESSAGE, page=alias)
        page = self._pages.get(alias)
        if page is None:
            raise WebBackendError("page_unknown", "The page alias is not declared; use an existing alias or explicitly open it.", page=alias)
        if self._closed(page):
            raise WebBackendError("page_closed", "The declared page is closed.", page=alias)
        return page

    @staticmethod
    def _closed(page: Any) -> bool:
        method = getattr(page, "is_closed", None)
        return bool(method()) if callable(method) else False

    def _page_view(self, alias: str, page: Any) -> dict[str, Any]:
        return {"page": alias, "url": self._page_url(page), "title": page.title(), "active": alias == self._active_alias}

    def _page_url(self, page: Any = None) -> str | None:
        value = getattr(page if page is not None else self.page, "url", None)
        return value if isinstance(value, str) else None

    def _resolve_url(self, url: str) -> str:
        if url == "about:blank" or url.startswith(("http://", "https://")):
            return url
        if urlsplit(url).scheme:
            raise WebBackendError("invalid_params", "Only HTTP(S), about:blank, or configured application-relative navigation is supported.")
        if self.base_url is None:
            raise WebBackendError("invalid_params", "Web navigation requires an absolute URL or configured harness.web.base_url.")
        return urljoin(self.base_url, url.lstrip("/"))

    @staticmethod
    def _text_matches(actual: str, expected: str, exact: bool) -> bool:
        return actual == expected if exact else expected in actual

    @staticmethod
    def _poll(page: Any, predicate: Callable[[], bool], timeout: int | Deadline) -> bool:
        deadline = Deadline.from_timeout(timeout)
        while True:
            if deadline.expired:
                return False
            if predicate():
                return True
            if deadline.expired:
                return False
            page.wait_for_timeout(min(25, deadline.remaining()))

    def _call(self, operation: Callable[[], Any], *, observation: bool = False, check_modal: bool = True) -> dict[str, object]:
        def execute() -> dict[str, object]:
            self._effect = "not_started"
            try:
                if check_modal:
                    self._check_unexpected()
                output = operation()
                if check_modal:
                    self._check_unexpected()
                return output if observation else self._passed(output, effect=self._effect)
            except Exception as caught:  # noqa: BLE001 - optional Playwright errors never escape with unsafe logs.
                error = caught
                if self._unexpected_dialog and check_modal:
                    error = WebBackendError("unexpected_dialog", "An unsolicited blocking dialog requires explicit browser/page recovery; it was not accepted.")
                elif self._unexpected_popup and check_modal:
                    error = WebBackendError("unexpected_popup", "An unsolicited popup has no declared replay alias; inspect or close the owned browser.")
                elif observation and not isinstance(error, WebBackendError) and failure_result(error)["metadata"]["error_code"] == "interaction_failed":
                    error = WebBackendError("observation_failed", "The requested observation could not be captured.")
                return failure_result(error, effect=self._effect)

        return self._run_sync(execute)

    def _check_unexpected(self) -> None:
        if self._unexpected_dialog:
            raise WebBackendError("unexpected_dialog", "An unsolicited blocking dialog requires explicit browser/page recovery.")
        if self._unexpected_popup:
            raise WebBackendError("unexpected_popup", "An unsolicited popup has no declared replay alias.")

    def _perform(self, operation: Callable[[], _T]) -> _T:
        self._effect = "indeterminate"
        result = operation()
        self._effect = "completed"
        return result

    @staticmethod
    def _passed(output: Any = None, *, effect: str = "not_started") -> dict[str, object]:
        return {"status": "passed", "output": output, "failure_category": None, "error_message": None, "metadata": {"action_effect": effect}}

    def close(self) -> None:
        self._run_sync(self._close)
        self._shutdown_executor()

    def _close(self) -> None:
        failure = None
        for page, _, _ in list(self._listeners.values()):
            try:
                self._unwatch(page)
            except BaseException as error:  # noqa: BLE001 - listener cleanup cannot prevent attempts to close all owned resources.
                failure = failure or error
        for attribute in ("_context", "_browser", "_playwright"):
            candidate = getattr(self, attribute)
            try:
                close = getattr(candidate, "close", None)
                stop = getattr(candidate, "stop", None)
                if callable(close):
                    close()
                elif callable(stop):
                    stop()
            except BaseException as error:  # noqa: BLE001 - attempt all owned cleanup and preserve the primary error.
                failure = failure or error
            else:
                setattr(self, attribute, None)
        self.page = None
        self._pages.clear()
        self._active_alias = None
        self._unexpected_dialog.clear()
        self._unexpected_popup.clear()
        if failure is not None:
            raise failure

    def _run_sync(self, func: Callable[[], _T]) -> _T:
        with self._executor_lock:
            executor = self._executor
            future = executor.submit(func) if executor is not None else None
        return func() if future is None else future.result()

    def _ensure_executor(self) -> None:
        with self._executor_lock:
            if self._executor is None:
                self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="fsq-playwright")

    def _shutdown_executor(self) -> None:
        with self._executor_lock:
            executor = self._executor
            self._executor = None
        if executor is not None:
            executor.shutdown(wait=True)

    def _create_page(self) -> object:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as error:
            raise m.ConfigurationError("playwright is required for PlaywrightWebDriver.", context={"action": "Reinstall or repair fsq-agent."}) from error
        self._playwright = sync_playwright().start()
        browser_factory = getattr(self._playwright, "chromium", None)
        if browser_factory is None:
            raise m.ConfigurationError("Playwright chromium browser type is unavailable.", context={"channel": self.channel})
        if self.executable_path is None:
            raise m.ConfigurationError("Web browser executable path is required for PlaywrightWebDriver.", context={"config_key": "target.browser_executable_path", "channel": self.channel})
        self._browser = browser_factory.launch(headless=self.headless, channel=self.channel, executable_path=self.executable_path)
        kwargs = {"viewport": {"width": self.viewport[0], "height": self.viewport[1]}} if self.viewport is not None else {}
        self._context = self._browser.new_context(**kwargs)
        return self._context.new_page()
