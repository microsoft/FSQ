# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import json
import logging
import re
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, TypeVar
from urllib.parse import urljoin

from pydantic import BaseModel

from fsq_agent.drivers._ai_assertion import AIAssertionBackendToolMixin
from fsq_agent.drivers._capabilities import _web_driver_tool
from fsq_agent.models import (
    ConfigurationError,
    WebAssertNotVisibleParams,
    WebAssertTextParams,
    WebAssertVisibleParams,
    WebAssertWithAIParams,
    WebClickOnParams,
    WebCloseBrowserParams,
    WebHoverOnParams,
    WebLocator,
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

DEFAULT_WEB_WAIT_TIMEOUT_MS = 10000
DEFAULT_WEB_ATTACH_ENDPOINT = "http://127.0.0.1:9222"
SUPPORTED_WEB_CHANNELS = frozenset({"chromium", "chrome", "chrome-beta", "chrome-dev", "chrome-canary", "msedge", "msedge-beta", "msedge-dev", "msedge-canary"})
_BROWSER_NOT_STARTED_MESSAGE = "Browser is not started. Call startBrowser before Web page actions."
_T = TypeVar("_T")


class PlaywrightWebDriver(AIAssertionBackendToolMixin):
    backend = "playwright"

    def __init__(
        self,
        *,
        channel: str = "chrome",
        executable_path: str | Path | None = None,
        headless: bool = True,
        base_url: str | None = None,
        viewport: tuple[int, int] | None = None,
        attach: bool = False,
        attach_endpoint: str = DEFAULT_WEB_ATTACH_ENDPOINT,
        page: object | None = None,
    ) -> None:
        self.channel = channel.strip() if isinstance(channel, str) else channel
        if self.channel not in SUPPORTED_WEB_CHANNELS:
            raise ConfigurationError(
                "Unsupported Playwright browser channel.",
                context={"channel": self.channel, "supported": sorted(SUPPORTED_WEB_CHANNELS)},
            )
        self.executable_path = str(Path(executable_path)) if executable_path else None
        self.headless = headless
        self.base_url = base_url.rstrip("/") + "/" if isinstance(base_url, str) and base_url.strip() else None
        self.viewport = viewport
        self.attach = attach
        self.attach_endpoint = attach_endpoint.strip() if isinstance(attach_endpoint, str) else ""
        if not self.attach_endpoint:
            raise ConfigurationError(
                "Playwright CDP attach endpoint must not be blank.",
                context={"config_key": "harness.web.attach_endpoint"},
            )
        self._playwright: object | None = None
        self._browser: object | None = None
        self._context: object | None = None
        self._attached_page: object | None = None
        self._executor: ThreadPoolExecutor | None = None
        self.page: object | None = page

    def context(self) -> dict[str, object]:
        return self._run_sync(self._context_payload)

    def _context_payload(self) -> dict[str, object]:
        viewport = None if self.attach else self.viewport
        if viewport is None:
            viewport = self._page_viewport()
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
                "browser_started": self.page is not None,
                "attach": self.attach,
            },
        }

    @_web_driver_tool("startBrowser", description="Start or reuse the configured Web browser.")
    def start_browser(self, params: WebStartBrowserParams) -> dict[str, object]:
        if self.page is None:
            self._ensure_executor()
        try:
            return self._run_sync(lambda: self._start_browser(params))
        except BaseException:
            if self.page is None and self._attached_page is None and all(resource is None for resource in (self._context, self._browser, self._playwright)):
                try:
                    self._shutdown_executor()
                except BaseException as cleanup_error:  # noqa: BLE001 - startup failure retains precedence.
                    logging.getLogger(__name__).warning("Web startup worker disposal failed (%s)", type(cleanup_error).__name__)
            raise

    def _start_browser(self, params: WebStartBrowserParams) -> dict[str, object]:
        if self.page is not None:
            return self._passed({"already_started": True, "url": self._page_url()})
        if self._attached_page is not None or any(resource is not None for resource in (self._context, self._browser, self._playwright)):
            self._close()
        try:
            self.page = self._create_page()
        except BaseException:
            try:
                self._close()
            except BaseException as cleanup_error:  # noqa: BLE001 - clean every acquired resource before propagating startup failure.
                logging.getLogger(__name__).warning("Web startup resource disposal failed (%s)", type(cleanup_error).__name__)
            raise
        return self._passed({"already_started": False, "url": self._page_url()})

    @_web_driver_tool("closeBrowser", description="Close the active Web browser.")
    def close_browser(self, params: WebCloseBrowserParams) -> dict[str, object]:
        return self._run_sync(lambda: self._close_browser(params))

    def _close_browser(self, params: WebCloseBrowserParams) -> dict[str, object]:
        if self.page is None and self._attached_page is None and self._context is None and self._browser is None and self._playwright is None:
            return self._passed({"already_closed": True})
        self._close()
        return self._passed({"already_closed": False})

    @_web_driver_tool("navigateTo", description="Navigate the current Web page to a URL.")
    def navigate_to(self, params: WebNavigateToParams) -> dict[str, object]:
        return self._run_sync(lambda: self._navigate_to(params))

    def _navigate_to(self, params: WebNavigateToParams) -> dict[str, object]:
        if self.page is None:
            return self._browser_not_started()
        url = self._resolve_url(params.url)
        kwargs: dict[str, object] = {}
        if params.waitUntil is not None:
            kwargs["wait_until"] = params.waitUntil
        response = self.page.goto(url, **kwargs)
        status = getattr(response, "status", None)
        return self._passed({"url": self._page_url() or url, "status": status})

    @_web_driver_tool("navigateBack", description="Navigate the current Web page back in browser history.")
    def navigate_back(self, params: WebNavigateBackParams) -> dict[str, object]:
        return self._run_sync(lambda: self._navigate_back(params))

    def _navigate_back(self, params: WebNavigateBackParams) -> dict[str, object]:
        if self.page is None:
            return self._browser_not_started()
        kwargs: dict[str, object] = {}
        if params.waitUntil is not None:
            kwargs["wait_until"] = params.waitUntil
        response = self.page.go_back(**kwargs)
        status = getattr(response, "status", None)
        return self._passed({"url": self._page_url(), "status": status})

    @_web_driver_tool("clickOn", description="Click a Web page target resolved from the page snapshot.")
    def click_on(self, params: WebClickOnParams) -> dict[str, object]:
        return self._run_sync(lambda: self._click_on(params))

    def _click_on(self, params: WebClickOnParams) -> dict[str, object]:
        if self.page is None:
            return self._browser_not_started()
        locator, resolution_failure = self._resolve_target(params.locator, params=params)
        if resolution_failure is not None:
            return resolution_failure
        kwargs: dict[str, object] = {}
        if params.button is not None:
            kwargs["button"] = params.button
        try:
            if params.double:
                locator.dblclick(**kwargs)
            else:
                locator.click(**kwargs)
        except Exception as exc:  # noqa: BLE001
            return self._failed(
                "action_error",
                "Web target click failed.",
                metadata={"params": params.model_dump(mode="json", exclude_none=True), "diagnostic": self._safe_exception_message(exc)},
            )
        return self._passed()

    @_web_driver_tool("typeText", description="Type text into a Web page target resolved from the page snapshot.")
    def type_text(self, params: WebTypeTextParams) -> dict[str, object]:
        return self._run_sync(lambda: self._type_text(params))

    def _type_text(self, params: WebTypeTextParams) -> dict[str, object]:
        if self.page is None:
            return self._browser_not_started()
        locator, resolution_failure = self._resolve_target(params.locator, params=params)
        if resolution_failure is not None:
            return resolution_failure
        try:
            if params.clear:
                locator.fill(params.text)
            else:
                locator.click()
                locator.type(params.text)
        except Exception as exc:  # noqa: BLE001
            return self._interaction_failure("Web text entry failed.", params, exc)
        return self._passed()

    @_web_driver_tool("selectOption", description="Select an option in a Web select target.")
    def select_option(self, params: WebSelectOptionParams) -> dict[str, object]:
        return self._run_sync(lambda: self._select_option(params))

    def _select_option(self, params: WebSelectOptionParams) -> dict[str, object]:
        if self.page is None:
            return self._browser_not_started()
        locator, resolution_failure = self._resolve_target(params.locator, params=params)
        if resolution_failure is not None:
            return resolution_failure
        try:
            if len(params.labels) > 1 and not locator.evaluate("element => element instanceof HTMLSelectElement && element.multiple"):
                return self._failed(
                    "action_error",
                    "Multiple labels require a multiple-select Web target.",
                    metadata={"params": params.model_dump(mode="json", exclude_none=True)},
                )
            selected = locator.select_option(label=params.labels)
        except Exception as exc:  # noqa: BLE001
            return self._interaction_failure("Web option selection failed.", params, exc)
        return self._passed({"selected": selected})

    @_web_driver_tool("hoverOn", description="Hover over a Web page target resolved from the page snapshot.")
    def hover_on(self, params: WebHoverOnParams) -> dict[str, object]:
        return self._run_sync(lambda: self._hover_on(params))

    def _hover_on(self, params: WebHoverOnParams) -> dict[str, object]:
        if self.page is None:
            return self._browser_not_started()
        locator, resolution_failure = self._resolve_target(params.locator, params=params)
        if resolution_failure is not None:
            return resolution_failure
        try:
            locator.hover()
        except Exception as exc:  # noqa: BLE001
            return self._interaction_failure("Web hover failed.", params, exc)
        return self._passed()

    @_web_driver_tool("pressKey", description="Press a keyboard key in the current Web page.")
    def press_key(self, params: WebPressKeyParams) -> dict[str, object]:
        return self._run_sync(lambda: self._press_key(params))

    def _press_key(self, params: WebPressKeyParams) -> dict[str, object]:
        if self.page is None:
            return self._browser_not_started()
        self.page.keyboard.press(params.key)
        return self._passed({"key": params.key})

    @_web_driver_tool("waitFor", description="Wait for a semantic Web target or URL condition.")
    def wait_for(self, params: WebWaitForParams) -> dict[str, object]:
        return self._run_sync(lambda: self._wait_for(params))

    def _wait_for(self, params: WebWaitForParams) -> dict[str, object]:
        if self.page is None:
            return self._browser_not_started()
        timeout = params.timeout_ms or DEFAULT_WEB_WAIT_TIMEOUT_MS
        if params.locator is not None:
            state = params.state or "visible"
            return self._wait_for_semantic_locator(params, state=state, timeout=timeout)
        try:
            self.page.wait_for_url(params.url, timeout=timeout)
        except Exception as exc:  # noqa: BLE001
            return self._failed(
                "timeout_error",
                "Timed out waiting for Web URL.",
                metadata={"params": params.model_dump(mode="json", exclude_none=True), "diagnostic": self._safe_exception_message(exc)},
            )
        return self._passed({"url": self._page_url()})

    @_web_driver_tool("takeScreenshot", description="Capture a Web page screenshot for evidence or debugging.")
    def take_screenshot(self, params: WebTakeScreenshotParams) -> dict[str, object]:
        return self._run_sync(lambda: self._take_screenshot(params))

    def _take_screenshot(self, params: WebTakeScreenshotParams) -> dict[str, object]:
        if self.page is None:
            return self._browser_not_started()
        return self._passed({"bytes": len(self._screenshot(params))})

    @_web_driver_tool("uiSnapshot", description="Return the current Web page accessibility snapshot.")
    def ui_snapshot(self, params: WebUiSnapshotParams) -> dict[str, object]:
        return self._run_sync(lambda: self._ui_snapshot(params))

    def _ui_snapshot(self, params: WebUiSnapshotParams) -> dict[str, object]:
        if self.page is None:
            return self._browser_not_started()
        aria_snapshot = getattr(self.page, "aria_snapshot", None)
        if callable(aria_snapshot):
            snapshot = aria_snapshot(mode="default")
            if isinstance(snapshot, str) and snapshot.strip():
                normalized = self._normalize_aria_snapshot(snapshot)
                if normalized is None:
                    return self._text_ui_snapshot(reason="aria_snapshot_normalization_failed")
                normalized_snapshot, truncated = normalized
                return {
                    "url": self._page_url(),
                    "snapshot_type": "aria",
                    "snapshot": normalized_snapshot,
                    "coverage": {
                        "status": "complete",
                        "scope": "observed_aria_snapshot",
                        "reason": "semantic_values_compacted_to_100_characters" if truncated else "backend_observation_retained_without_clipping",
                    },
                    "truncated": truncated,
                }
            reason = "backend_aria_snapshot_empty" if isinstance(snapshot, str) else "backend_snapshot_shape_unknown"
            return self._text_ui_snapshot(reason=reason)
        return self._text_ui_snapshot(reason="backend_aria_snapshot_unavailable")

    def _text_ui_snapshot(self, *, reason: str) -> dict[str, object]:
        return {
            "url": self._page_url(),
            "snapshot_type": "text",
            "title": self._safe_page_title(),
            "text": self._safe_body_text(),
            "coverage": {"status": "partial", "scope": "visible_body_text", "reason": reason},
            "truncated": False,
        }

    @_web_driver_tool("assertVisible", description="Assert that a Web page target is visible.")
    def assert_visible(self, params: WebAssertVisibleParams) -> dict[str, object]:
        return self._run_sync(lambda: self._assert_visible(params))

    def _assert_visible(self, params: WebAssertVisibleParams) -> dict[str, object]:
        if self.page is None:
            return self._browser_not_started()
        _, resolution_failure = self._resolve_target(params.locator, params=params)
        return resolution_failure or self._passed()

    @_web_driver_tool("assertNotVisible", description="Assert that a Web page target is not visible.")
    def assert_not_visible(self, params: WebAssertNotVisibleParams) -> dict[str, object]:
        return self._run_sync(lambda: self._assert_not_visible(params))

    def _assert_not_visible(self, params: WebAssertNotVisibleParams) -> dict[str, object]:
        if self.page is None:
            return self._browser_not_started()
        target, scope_failure, scope_absent = self._semantic_target(params.locator, params=params, scope_policy="not_visible")
        if scope_failure is not None:
            return scope_failure
        if scope_absent:
            return self._passed()
        count, count_failure = self._locator_count(target, params=params)
        if count_failure is not None:
            return count_failure
        visible_count = self._visible_match_count(target, count)
        if visible_count == 0:
            return self._passed()
        return self._failed(
            "assertion_error",
            "Web target is visible.",
            metadata={
                "params": params.model_dump(mode="json", exclude_none=True),
                "match_count": count,
                "visible_match_count": visible_count,
            },
        )

    @_web_driver_tool("assertText", description="Assert text on a Web page target.")
    def assert_text(self, params: WebAssertTextParams) -> dict[str, object]:
        return self._run_sync(lambda: self._assert_text(params))

    def _assert_text(self, params: WebAssertTextParams) -> dict[str, object]:
        if self.page is None:
            return self._browser_not_started()
        if params.locator is None:
            locator = self.page.locator("body")
        else:
            locator, resolution_failure = self._resolve_target(params.locator, params=params)
            if resolution_failure is not None:
                return resolution_failure
        try:
            actual = locator.inner_text()
        except Exception as exc:  # noqa: BLE001
            return self._failed(
                "assertion_error",
                "Web text could not be read.",
                metadata={"params": params.model_dump(mode="json", exclude_none=True), "diagnostic": self._safe_exception_message(exc)},
            )
        contains = params.text.contains
        if isinstance(contains, str) and contains in actual:
            return self._passed(self._bounded_text_output(actual))
        equals = params.text.equals
        if isinstance(equals, str) and equals == actual:
            return self._passed(self._bounded_text_output(actual))
        return self._failed("assertion_error", "Text assertion failed.", output=self._bounded_text_output(actual))

    @_web_driver_tool("assertWithAI", description="Evaluate an explicit Web visual assertion with AI.")
    def assert_with_ai(self, params: WebAssertWithAIParams) -> dict[str, object]:
        return self._run_ai_assertion_tool(params)

    def screenshot(self, params: WebTakeScreenshotParams | None = None) -> bytes:
        return self._run_sync(lambda: self._screenshot(params))

    def _screenshot(self, params: WebTakeScreenshotParams | None = None) -> bytes:
        if self.page is None:
            raise RuntimeError(_BROWSER_NOT_STARTED_MESSAGE)
        params = params or WebTakeScreenshotParams()
        return self.page.screenshot(full_page=bool(params.fullPage), omit_background=bool(params.omitBackground))

    def close(self) -> None:
        try:
            self._run_sync(self._close)
        finally:
            self._shutdown_executor()

    def _close(self) -> None:
        failure = None
        if self.attach:
            self.page = None
            attached_page = self._attached_page
            if attached_page is not None:
                try:
                    close = getattr(attached_page, "close", None)
                    if callable(close):
                        close()
                except BaseException as exc:  # noqa: BLE001 - continue disconnecting after page cleanup failure.
                    failure = exc
                finally:
                    self._attached_page = None
            self._context = None
            self._browser = None
            resource_attributes = ("_playwright",)
        else:
            resource_attributes = ("_context", "_browser", "_playwright")
        for attribute in resource_attributes:
            candidate = getattr(self, attribute)
            try:
                close = getattr(candidate, "close", None)
                stop = getattr(candidate, "stop", None)
                if callable(close):
                    close()
                elif callable(stop):
                    stop()
            except BaseException as exc:  # noqa: BLE001 - attempt all owned backend cleanup.
                failure = failure or exc
            else:
                setattr(self, attribute, None)
        self.page = None
        if failure is not None:
            raise failure

    def _run_sync(self, func: Callable[[], _T]) -> _T:
        if self._executor is None:
            return func()
        return self._executor.submit(func).result()

    def _ensure_executor(self) -> None:
        if self._executor is None:
            self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="fsq-playwright")

    def _shutdown_executor(self) -> None:
        executor = self._executor
        self._executor = None
        if executor is not None:
            executor.shutdown(wait=True)

    def _create_page(self) -> object:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise ConfigurationError(
                "playwright is required for PlaywrightWebDriver.",
                context={"action": "Reinstall or repair fsq-agent."},
            ) from exc
        self._playwright = sync_playwright().start()
        browser_factory = getattr(self._playwright, "chromium", None)
        if browser_factory is None:
            raise ConfigurationError(
                "Playwright chromium browser type is unavailable.",
                context={"channel": self.channel},
            )
        if self.attach:
            self._browser = browser_factory.connect_over_cdp(self.attach_endpoint)
            contexts = getattr(self._browser, "contexts", None)
            if not contexts:
                raise ConfigurationError(
                    "The Playwright CDP connection has no browser context.",
                    context={"endpoint": self.attach_endpoint},
                )
            self._context = contexts[0]
            self._attached_page = self._context.new_page()
            return self._attached_page
        if self.executable_path is None:
            raise ConfigurationError(
                "Web browser executable path is required for PlaywrightWebDriver.",
                context={"config_key": "target.browser_executable_path", "channel": self.channel},
            )
        launch_kwargs: dict[str, object] = {"headless": self.headless, "channel": self.channel, "executable_path": self.executable_path}
        self._browser = browser_factory.launch(**launch_kwargs)
        context_kwargs: dict[str, object] = {}
        if self.viewport is not None:
            width, height = self.viewport
            context_kwargs["viewport"] = {"width": width, "height": height}
        self._context = self._browser.new_context(**context_kwargs)
        return self._context.new_page()

    def _resolve_url(self, url: str) -> str:
        if url.startswith(("http://", "https://")):
            return url
        if self.base_url is None:
            raise ConfigurationError("Web navigation requires an absolute URL or configured harness.web.base_url.")
        return urljoin(self.base_url, url.lstrip("/"))

    def _resolve_target(self, locator: WebLocator, *, params: BaseModel) -> tuple[object | None, dict[str, object] | None]:
        target, scope_failure, _ = self._semantic_target(locator, params=params, scope_policy="positive")
        if scope_failure is not None:
            return None, scope_failure
        count, count_failure = self._locator_count(target, params=params)
        if count_failure is not None:
            return None, count_failure
        metadata = {"params": params.model_dump(mode="json", exclude_none=True), "match_count": count}
        if count == 0:
            return None, self._failed("target_not_found", "Web target was not found.", metadata=metadata)
        if locator.index is None and count > 1:
            return None, self._failed("target_ambiguous", "Web target matched multiple elements.", metadata=metadata)
        if locator.index is not None:
            if locator.index >= count:
                return None, self._failed("target_not_found", "Web target index is out of range.", metadata=metadata)
            target = target.nth(locator.index)
        try:
            if not target.is_visible():
                return None, self._failed("target_not_visible", "Web target is not visible.", metadata=metadata)
        except Exception as exc:  # noqa: BLE001
            return None, self._failed(
                "target_detached",
                "Web target became unavailable.",
                metadata={**metadata, "diagnostic": self._safe_exception_message(exc)},
            )
        return target, None

    def _semantic_target(
        self,
        locator: WebLocator,
        *,
        params: BaseModel,
        scope_policy: str,
    ) -> tuple[object | None, dict[str, object] | None, bool]:
        owner = self.page
        if locator.within is not None:
            scope = self._role_locator(owner, locator.within.role, locator.within.name)
            scope_count, count_failure = self._locator_count(scope, params=params, count_key="scope_match_count")
            if count_failure is not None:
                return None, count_failure, False
            if scope_policy == "not_visible":
                visible_indexes = self._visible_match_indexes(scope, scope_count)
                if not visible_indexes:
                    return None, None, True
                if len(visible_indexes) > 1:
                    return (
                        None,
                        self._failed(
                            "target_ambiguous",
                            "Web parent scope matched multiple visible elements.",
                            metadata={
                                "params": params.model_dump(mode="json", exclude_none=True),
                                "scope_match_count": scope_count,
                                "visible_scope_match_count": len(visible_indexes),
                            },
                        ),
                        False,
                    )
                owner = scope if scope_count == 1 else scope.nth(visible_indexes[0])
            else:
                if scope_count == 0:
                    if scope_policy == "disappearance":
                        return None, None, True
                    return (
                        None,
                        self._failed(
                            "target_not_found",
                            "Web parent scope was not found.",
                            metadata={"params": params.model_dump(mode="json", exclude_none=True), "scope_match_count": 0},
                        ),
                        False,
                    )
                if scope_count > 1:
                    return (
                        None,
                        self._failed(
                            "target_ambiguous",
                            "Web parent scope matched multiple elements.",
                            metadata={"params": params.model_dump(mode="json", exclude_none=True), "scope_match_count": scope_count},
                        ),
                        False,
                    )
                if scope_policy == "positive":
                    try:
                        if not scope.is_visible():
                            return (
                                None,
                                self._failed(
                                    "target_not_visible",
                                    "Web parent scope is not visible.",
                                    metadata={"params": params.model_dump(mode="json", exclude_none=True), "scope_match_count": 1},
                                ),
                                False,
                            )
                    except Exception as exc:  # noqa: BLE001
                        return (
                            None,
                            self._failed(
                                "target_detached",
                                "Web parent scope became unavailable.",
                                metadata={
                                    "params": params.model_dump(mode="json", exclude_none=True),
                                    "scope_match_count": 1,
                                    "diagnostic": self._safe_exception_message(exc),
                                },
                            ),
                            False,
                        )
                owner = scope
        return self._role_locator(owner, locator.role, locator.name), None, False

    def _role_locator(self, owner: object, role: str, name: str | None) -> object:
        kwargs: dict[str, object] = {}
        if name is not None:
            matcher = self._snapshot_text_matcher(name)
            kwargs["name"] = matcher
            if isinstance(matcher, str):
                kwargs["exact"] = True
        return owner.get_by_role(role, **kwargs)

    def _locator_count(
        self,
        locator: object,
        *,
        params: BaseModel,
        count_key: str = "match_count",
    ) -> tuple[int, dict[str, object] | None]:
        try:
            return locator.count(), None
        except Exception as exc:  # noqa: BLE001
            return 0, self._failed(
                "target_resolution_error",
                "Web target resolution failed.",
                metadata={
                    "params": params.model_dump(mode="json", exclude_none=True),
                    count_key: None,
                    "diagnostic": self._safe_exception_message(exc),
                },
            )

    @staticmethod
    def _visible_match_indexes(locator: object, count: int) -> list[int]:
        visible_indexes: list[int] = []
        for index in range(count):
            candidate = locator if count == 1 else locator.nth(index)
            try:
                if candidate.is_visible():
                    visible_indexes.append(index)
            except Exception as exc:  # noqa: BLE001 - detached candidates are not visible.
                logging.getLogger(__name__).debug("Web visibility probe failed (%s)", type(exc).__name__)
                continue
        return visible_indexes

    def _visible_match_count(self, locator: object, count: int) -> int:
        return len(self._visible_match_indexes(locator, count))

    def _wait_for_semantic_locator(self, params: WebWaitForParams, *, state: str, timeout: int) -> dict[str, object]:
        disappearance = state in {"hidden", "detached"}
        target, scope_failure, scope_absent = self._semantic_target(
            params.locator,
            params=params,
            scope_policy="disappearance" if disappearance else "positive",
        )
        if scope_failure is not None:
            return scope_failure
        if scope_absent:
            return self._passed({"state": state})
        count, count_failure = self._locator_count(target, params=params)
        if count_failure is not None:
            return count_failure
        if disappearance and count == 0:
            return self._passed({"state": state})
        if count > 1 and params.locator.index is None:
            return self._failed(
                "target_ambiguous",
                "Web target matched multiple elements.",
                metadata={"params": params.model_dump(mode="json", exclude_none=True), "match_count": count},
            )
        if params.locator.index is not None:
            if params.locator.index >= count:
                return self._failed(
                    "target_not_found",
                    "Web target index is out of range.",
                    metadata={"params": params.model_dump(mode="json", exclude_none=True), "match_count": count},
                )
            target = target.nth(params.locator.index)
        if self._wait_for_locator(target, state=state, timeout=timeout):
            return self._passed({"state": state})
        return self._failed(
            "timeout_error",
            "Timed out waiting for Web target.",
            metadata={"params": params.model_dump(mode="json", exclude_none=True), "match_count": count},
        )

    @classmethod
    def _normalize_aria_snapshot(cls, snapshot: str) -> tuple[str, bool] | None:
        if re.search(r"\[ref=[^]]+\]", snapshot):
            return None
        normalized_lines: list[str] = []
        truncated = False
        for line in snapshot.splitlines(keepends=True):
            ending = line[len(line.rstrip("\r\n")) :]
            content = line[: len(line) - len(ending)] if ending else line
            normalized = cls._normalize_aria_line(content)
            if normalized is None:
                return None
            normalized_line, line_truncated = normalized
            normalized_lines.append(normalized_line + ending)
            truncated = truncated or line_truncated
        return "".join(normalized_lines), truncated

    @classmethod
    def _normalize_aria_line(cls, line: str) -> tuple[str, bool] | None:
        role_match = re.match(r"^(\s*-\s+)([A-Za-z][\w-]*)(.*)$", line)
        if role_match is None:
            return line, False
        prefix, role, remainder = role_match.groups()
        quoted_match = re.match(r'^(\s+)(".*)$', remainder)
        if quoted_match is not None:
            whitespace, scalar_and_suffix = quoted_match.groups()
            decoded = cls._decode_quoted_scalar(scalar_and_suffix)
            if decoded is None:
                return None
            value, suffix, _ = decoded
            if len(value) <= 100:
                return line, False
            compacted = json.dumps(value[:100] + "...", ensure_ascii=False)
            return f"{prefix}{role}{whitespace}{compacted}{suffix}", True
        payload_match = re.match(r"^(\s*:\s*)(.*)$", remainder)
        if payload_match is None:
            return line, False
        separator, scalar = payload_match.groups()
        if not scalar:
            return line, False
        if scalar.startswith('"'):
            decoded = cls._decode_quoted_scalar(scalar)
            if decoded is None:
                return None
            value, suffix, _ = decoded
            if suffix:
                return None
            if len(value) <= 100:
                return line, False
            compacted = json.dumps(value[:100] + "...", ensure_ascii=False)
            return f"{prefix}{role}{separator}{compacted}", True
        if len(scalar) <= 100:
            return line, False
        return f"{prefix}{role}{separator}{scalar[:100]}...", True

    @staticmethod
    def _decode_quoted_scalar(source: str) -> tuple[str, str, str] | None:
        try:
            value, consumed = json.JSONDecoder().raw_decode(source)
        except (TypeError, ValueError, json.JSONDecodeError):
            return None
        if not isinstance(value, str):
            return None
        return value, source[consumed:], source[:consumed]

    def _interaction_failure(self, message: str, params: BaseModel, exc: Exception) -> dict[str, object]:
        return self._failed(
            "action_error",
            message,
            metadata={
                "params": params.model_dump(mode="json", exclude_none=True),
                "diagnostic": self._safe_exception_message(exc),
            },
        )

    @staticmethod
    def _bounded_text_output(actual: str) -> dict[str, object]:
        return {"text": actual[:1000], "truncated": len(actual) > 1000}

    @staticmethod
    def _snapshot_text_matcher(value: str) -> str | re.Pattern[str]:
        normalized = value.strip()
        if normalized.endswith("..."):
            return re.compile(rf"^{re.escape(normalized[:-3])}")
        return normalized

    @staticmethod
    def _safe_exception_message(exc: Exception) -> str:
        message = str(exc).strip().splitlines()[0] if str(exc).strip() else exc.__class__.__name__
        return message[:500]

    def _wait_for_locator(self, locator: object, *, state: str, timeout: int = DEFAULT_WEB_WAIT_TIMEOUT_MS) -> bool:
        try:
            locator.wait_for(state=state, timeout=timeout)
        # Playwright locator failures use optional-backend exception classes outside the core contract.
        except Exception:  # noqa: BLE001
            return False
        else:
            return True

    def _page_url(self) -> str | None:
        url = getattr(self.page, "url", None)
        return url if isinstance(url, str) else None

    def _page_viewport(self) -> tuple[int, int] | None:
        if self.page is None:
            return None
        viewport_size = getattr(self.page, "viewport_size", None)
        if not isinstance(viewport_size, dict):
            return None
        width = viewport_size.get("width")
        height = viewport_size.get("height")
        if isinstance(width, int) and isinstance(height, int):
            return width, height
        return None

    def _safe_page_title(self) -> str | None:
        title = getattr(self.page, "title", None)
        if not callable(title):
            return None
        try:
            value = title()
        # Playwright page probes must tolerate closed pages and optional-backend errors.
        except Exception:  # noqa: BLE001
            return None
        return value if isinstance(value, str) else None

    def _safe_body_text(self) -> str | None:
        try:
            locator = self.page.locator("body")
            inner_text = getattr(locator, "inner_text", None)
            if not callable(inner_text):
                return None
            return inner_text(timeout=1000)
        # Playwright page probes must tolerate closed pages and optional-backend errors.
        except Exception:  # noqa: BLE001
            return None

    def _browser_not_started(self) -> dict[str, object]:
        return self._failed("context_error", _BROWSER_NOT_STARTED_MESSAGE)

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
