# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import json
import sys
import threading
import time
import types
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from fsq_agent.core.harness._playwright_driver import PlaywrightWebDriver
from fsq_agent.models import ConfigurationError, WebClickOnParams, WebCloseBrowserParams, WebNavigateToParams, WebStartBrowserParams, WebUiSnapshotParams


class _FakeResponse:
    status = 200


class _FakePage:
    def __init__(self, *, aria_snapshot: str = '- document "Example" [ref=e1]') -> None:
        self.url = "about:blank"
        self.viewport_size = {"width": 800, "height": 600}
        self._aria_snapshot = aria_snapshot
        self.thread_ids: list[int] = []
        self.aria_kwargs: dict[str, object] | None = None

    def _record_thread(self) -> None:
        self.thread_ids.append(threading.get_ident())

    def goto(self, url: str, **kwargs: object) -> _FakeResponse:
        self._record_thread()
        self.url = url
        return _FakeResponse()

    def aria_snapshot(self, **kwargs: object) -> str:
        self._record_thread()
        self.aria_kwargs = kwargs
        return self._aria_snapshot


class _ThreadedFakePlaywrightDriver(PlaywrightWebDriver):
    def _create_page(self) -> object:
        self.create_thread_id = threading.get_ident()
        self.fake_page = _FakePage()
        return self.fake_page


class _FakeLocator:
    def inner_text(self, **kwargs: object) -> str:
        return "Search box\nResults"


class _ClickFakeLocator:
    def __init__(self, count: int = 1, visible: bool = True) -> None:
        self.match_count = count
        self.visible = visible
        self.clicked = False
        self.filters: list[object] = []

    def filter(self, **kwargs: object) -> "_ClickFakeLocator":
        self.filters.append(kwargs)
        return self

    def and_(self, other: object) -> "_ClickFakeLocator":
        self.filters.append({"and": other})
        return self

    def count(self) -> int:
        return self.match_count

    def is_visible(self) -> bool:
        return self.visible

    def is_enabled(self, **kwargs: object) -> bool:
        return True

    def click(self, **kwargs: object) -> None:
        self.clicked = True


class _ClickFakePage(_FakePage):
    def __init__(self, locator: _ClickFakeLocator) -> None:
        super().__init__()
        self.result = locator
        self.role_calls: list[tuple[str, dict[str, object]]] = []

    def get_by_role(self, role: str, **kwargs: object) -> _ClickFakeLocator:
        self.role_calls.append((role, kwargs))
        return self.result

    def get_by_text(self, text: object) -> _ClickFakeLocator:
        return self.result


class _TextFallbackFakePage(_FakePage):
    def __init__(self) -> None:
        super().__init__(aria_snapshot="")
        self.url = "https://example.com"

    def title(self) -> str:
        return "Example Search"

    def locator(self, selector: str) -> _FakeLocator:
        assert selector == "body"
        return _FakeLocator()


class _LaunchFakeContext:
    def __init__(self, page: _FakePage) -> None:
        self.page = page
        self.closed = False

    def new_page(self) -> _FakePage:
        return self.page

    def close(self) -> None:
        self.closed = True


class _LaunchFakeBrowser:
    def __init__(self, page: _FakePage) -> None:
        self.context = _LaunchFakeContext(page)
        self.context_kwargs: dict[str, object] | None = None
        self.closed = False

    def new_context(self, **kwargs: object) -> _LaunchFakeContext:
        self.context_kwargs = kwargs
        return self.context

    def close(self) -> None:
        self.closed = True


class _LaunchFakeBrowserType:
    def __init__(self, browser: _LaunchFakeBrowser) -> None:
        self.browser = browser
        self.launch_kwargs: dict[str, object] | None = None

    def launch(self, **kwargs: object) -> _LaunchFakeBrowser:
        self.launch_kwargs = kwargs
        return self.browser


class _LaunchFakePlaywright:
    def __init__(self, browser_type: _LaunchFakeBrowserType) -> None:
        self.chromium = browser_type
        self.stopped = False

    def stop(self) -> None:
        self.stopped = True


class _LaunchFakeSyncPlaywright:
    def __init__(self, playwright: _LaunchFakePlaywright) -> None:
        self.playwright = playwright

    def start(self) -> _LaunchFakePlaywright:
        return self.playwright


def test_playwright_web_driver_runs_page_operations_on_one_worker_thread() -> None:
    driver = _ThreadedFakePlaywrightDriver()
    start = driver.start_browser(WebStartBrowserParams())
    external_thread_ids: set[int] = set()

    def navigate(url: str) -> dict[str, object]:
        external_thread_ids.add(threading.get_ident())
        return driver.navigate_to(WebNavigateToParams(page="main", url=url))

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(
            executor.map(
                navigate,
                ["https://example.com/one", "https://example.com/two"],
            )
        )

    snapshot = driver.ui_snapshot(WebUiSnapshotParams(scope={"kind": "page", "page": "main"}))
    context = driver.context()
    driver.close()

    assert start["status"] == "passed"
    assert start["output"] == {"already_started": False, "url": "about:blank"}
    assert [result["status"] for result in results] == ["passed", "passed"]
    assert snapshot["page"]["url"] == "https://example.com/two"
    assert snapshot["full_source"]["semantic_text"]
    assert "ref=" not in json.dumps(snapshot)
    assert driver.fake_page.aria_kwargs["mode"] == "ai"
    assert 0 < driver.fake_page.aria_kwargs["timeout"] <= 10000
    assert context["current_url"] == "https://example.com/two"
    assert set(driver.fake_page.thread_ids) == {driver.create_thread_id}
    assert driver.create_thread_id not in external_thread_ids


def test_playwright_web_driver_ui_snapshot_falls_back_when_aria_snapshot_is_empty() -> None:
    driver = PlaywrightWebDriver(page=_TextFallbackFakePage())

    snapshot = driver.ui_snapshot(WebUiSnapshotParams(scope={"kind": "page", "page": "main"}))

    assert snapshot["page"]["url"] == "https://example.com"
    assert snapshot["coverage"]["semantic"] == "text_only"
    assert snapshot["coverage"]["locators"] == "unavailable"
    assert snapshot["full_source"]["semantic_text"] == "Search box\nResults"


def test_playwright_web_driver_launches_configured_chrome_executable(monkeypatch: pytest.MonkeyPatch) -> None:
    page = _FakePage()
    browser = _LaunchFakeBrowser(page)
    browser_type = _LaunchFakeBrowserType(browser)
    playwright = _LaunchFakePlaywright(browser_type)
    sync_api = types.ModuleType("playwright.sync_api")
    sync_api.sync_playwright = lambda: _LaunchFakeSyncPlaywright(playwright)
    playwright_package = types.ModuleType("playwright")
    playwright_package.sync_api = sync_api
    monkeypatch.setitem(sys.modules, "playwright", playwright_package)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", sync_api)

    driver = PlaywrightWebDriver(channel="chrome", executable_path="C:/Chrome/chrome.exe", headless=False, viewport=(1024, 768))
    try:
        start = driver.start_browser(WebStartBrowserParams())
        context = driver.context()
    finally:
        driver.close()

    assert start["status"] == "passed"
    assert start["output"] == {"already_started": False, "url": "about:blank"}
    assert browser_type.launch_kwargs == {"headless": False, "channel": "chrome", "executable_path": str(Path("C:/Chrome/chrome.exe"))}
    assert browser.context_kwargs == {"viewport": {"width": 1024, "height": 768}}
    assert context["metadata"]["channel"] == "chrome"
    assert context["metadata"]["browser_executable_configured"] is True
    assert browser.context.closed is True
    assert browser.closed is True
    assert playwright.stopped is True


@pytest.mark.parametrize("failure_stage", ["launch", "context", "page"])
@pytest.mark.parametrize("cleanup_fails", [False, True])
@pytest.mark.parametrize("cancelled", [False, True])
def test_partial_browser_startup_releases_acquired_resources_on_owner_worker(monkeypatch, failure_stage, cleanup_fails, cancelled):
    import asyncio
    import threading
    from types import SimpleNamespace

    primary = asyncio.CancelledError("startup cancelled") if cancelled else RuntimeError("startup failed")
    acquired, disposed, workers = [], [], []
    attempts = {}

    def dispose(name):
        workers.append(threading.get_ident())
        attempts[name] = attempts.get(name, 0) + 1
        if cleanup_fails and attempts[name] == 1:
            raise OSError("secondary disposal failure")
        disposed.append(name)

    def new_page():
        workers.append(threading.get_ident())
        raise primary

    context = SimpleNamespace(new_page=new_page, close=lambda: dispose("context"))

    def new_context(**kwargs):
        workers.append(threading.get_ident())
        if failure_stage == "context":
            raise primary
        acquired.append("context")
        return context

    browser = SimpleNamespace(new_context=new_context, close=lambda: dispose("browser"))

    def launch(**kwargs):
        workers.append(threading.get_ident())
        if failure_stage == "launch":
            raise primary
        acquired.append("browser")
        return browser

    playwright = SimpleNamespace(chromium=SimpleNamespace(launch=launch), stop=lambda: dispose("playwright"))

    def start():
        workers.append(threading.get_ident())
        acquired.append("playwright")
        return playwright

    sync_api = types.ModuleType("playwright.sync_api")
    sync_api.sync_playwright = lambda: SimpleNamespace(start=start)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", sync_api)
    driver = PlaywrightWebDriver(executable_path="C:/synthetic/chrome.exe")
    executor = None
    try:
        with pytest.raises(type(primary)) as failure:
            driver.start_browser(WebStartBrowserParams())
        assert failure.value is primary
        assert set(attempts) == set(acquired)
        if not cleanup_fails:
            assert disposed == list(reversed(acquired))
            assert driver._executor is None
        else:
            executor = driver._executor
            assert executor is not None
    finally:
        driver.close()
    assert disposed == list(reversed(acquired))
    assert driver._executor is None
    assert driver.page is driver._context is driver._browser is driver._playwright is None
    assert len(set(workers)) == 1
    assert workers[0] != threading.get_ident()
    if executor is not None:
        assert all(not thread.is_alive() for thread in executor._threads)
    driver.close()
    assert len(disposed) == len(acquired)


@pytest.mark.parametrize("retry_cleanup_fails", [False, True])
def test_repeated_start_preserves_pending_resources_before_replacement(monkeypatch, retry_cleanup_fails):
    import threading
    from types import SimpleNamespace

    startup_error = RuntimeError("first page creation failed")
    cleanup_error = OSError("first generation cleanup failed")
    acquired, disposed, workers = [], [], []
    resources = {}
    generation = 0
    cleanup_allowed = False

    def dispose(owner, kind):
        workers.append(threading.get_ident())
        if owner == 1 and not cleanup_allowed:
            raise cleanup_error
        disposed.append((owner, kind))

    def start():
        nonlocal generation
        workers.append(threading.get_ident())
        generation += 1
        owner = generation
        if owner > 1:
            assert set(disposed) == {(1, "context"), (1, "browser"), (1, "playwright")}

        def new_page():
            if owner == 1:
                raise startup_error
            return _FakePage()

        context = SimpleNamespace(new_page=new_page, close=lambda: dispose(owner, "context"))
        browser = SimpleNamespace(new_context=lambda **kwargs: context, close=lambda: dispose(owner, "browser"))
        playwright = SimpleNamespace(chromium=SimpleNamespace(launch=lambda **kwargs: browser), stop=lambda: dispose(owner, "playwright"))
        resources[owner] = (context, browser, playwright)
        acquired.append(owner)
        return playwright

    sync_api = types.ModuleType("playwright.sync_api")
    sync_api.sync_playwright = lambda: SimpleNamespace(start=start)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", sync_api)
    driver = PlaywrightWebDriver(executable_path="C:/synthetic/chrome.exe")
    executor = None
    try:
        with pytest.raises(RuntimeError, match="first page creation failed") as failure:
            driver.start_browser(WebStartBrowserParams())
        assert failure.value is startup_error
        executor = driver._executor
        assert executor is not None
        assert (driver._context, driver._browser, driver._playwright) == resources[1]
        cleanup_allowed = not retry_cleanup_fails
        if retry_cleanup_fails:
            with pytest.raises(OSError, match="first generation cleanup failed") as failure:
                driver.start_browser(WebStartBrowserParams())
            assert failure.value is cleanup_error
            assert acquired == [1]
            assert (driver._context, driver._browser, driver._playwright) == resources[1]
            assert driver._executor is executor
            cleanup_allowed = True
        assert driver.start_browser(WebStartBrowserParams())["status"] == "passed"
        assert acquired == [1, 2]
        assert driver._executor is executor
    finally:
        cleanup_allowed = True
        driver.close()
    assert disposed == [(owner, kind) for owner in (1, 2) for kind in ("context", "browser", "playwright")]
    assert len(set(workers)) == 1
    assert workers[0] != threading.get_ident()
    assert driver._executor is None
    assert all(not thread.is_alive() for thread in executor._threads)
    driver.close()
    assert len(disposed) == 6


def test_playwright_web_driver_rejects_unsupported_channel() -> None:
    with pytest.raises(ConfigurationError, match="Unsupported Playwright browser channel"):
        PlaywrightWebDriver(channel="firefox", page=_FakePage())


@pytest.mark.parametrize(
    "channel",
    ["chromium", "chrome", "chrome-beta", "chrome-dev", "chrome-canary", "msedge", "msedge-beta", "msedge-dev", "msedge-canary"],
)
def test_playwright_web_driver_accepts_all_supported_channels(channel: str) -> None:
    driver = PlaywrightWebDriver(channel=channel, page=_FakePage())
    try:
        assert driver.context()["metadata"]["channel"] == channel
    finally:
        driver.close()


def test_click_on_keeps_authored_ellipsis_exact() -> None:
    locator = _ClickFakeLocator()
    page = _ClickFakePage(locator)
    driver = PlaywrightWebDriver(page=page)

    result = driver.click_on(WebClickOnParams(target={"page": "main", "steps": [{"kind": "role", "role": "link", "name": "GitHub ..."}]}))

    assert result["status"] == "passed"
    role, kwargs = page.role_calls[0]
    assert role == "link"
    assert kwargs["name"] == "GitHub ..."
    assert kwargs["exact"] is True
    assert locator.clicked is True


def test_click_on_composes_role_and_conjunctive_filter() -> None:
    locator = _ClickFakeLocator()
    page = _ClickFakePage(locator)
    driver = PlaywrightWebDriver(page=page)

    result = driver.click_on(WebClickOnParams(target={"page": "main", "steps": [{"kind": "role", "role": "link"}, {"kind": "filter", "text": {"kind": "contains", "value": "microsoft/FSQ"}}]}))

    assert result["status"] == "passed"
    assert len(locator.filters) == 1


def test_click_on_reports_ambiguous_target() -> None:
    driver = PlaywrightWebDriver(page=_ClickFakePage(_ClickFakeLocator(count=2)))

    result = driver.click_on(WebClickOnParams(target={"page": "main", "steps": [{"kind": "role", "role": "link"}]}))

    assert result["failure_category"] == "target_resolution_error"
    assert result["metadata"]["error_code"] == "target_ambiguous"
    assert result["metadata"]["match_count"] == 2


def test_playwright_web_driver_does_not_launch_until_start_browser(monkeypatch: pytest.MonkeyPatch) -> None:
    sync_api = types.ModuleType("playwright.sync_api")
    sync_api.sync_playwright = lambda: pytest.fail("Playwright must not be imported or started during construction")
    playwright_package = types.ModuleType("playwright")
    playwright_package.sync_api = sync_api
    monkeypatch.setitem(sys.modules, "playwright", playwright_package)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", sync_api)

    driver = PlaywrightWebDriver(channel="chrome", executable_path="C:/Chrome/chrome.exe")

    try:
        context = driver.context()
        result = driver.navigate_to(WebNavigateToParams(page="main", url="https://example.com"))
    finally:
        driver.close()

    assert context["current_url"] is None
    assert context["metadata"]["browser_started"] is False
    assert result["status"] == "failed"
    assert result["failure_category"] == "context_error"
    assert result["error_message"] == "Browser is not started. Call startBrowser before Web page actions."


def test_playwright_web_driver_start_and_close_browser_are_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    page = _FakePage()
    browser = _LaunchFakeBrowser(page)
    browser_type = _LaunchFakeBrowserType(browser)
    playwright = _LaunchFakePlaywright(browser_type)
    sync_api = types.ModuleType("playwright.sync_api")
    sync_api.sync_playwright = lambda: _LaunchFakeSyncPlaywright(playwright)
    playwright_package = types.ModuleType("playwright")
    playwright_package.sync_api = sync_api
    monkeypatch.setitem(sys.modules, "playwright", playwright_package)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", sync_api)

    driver = PlaywrightWebDriver(channel="chrome", executable_path="C:/Chrome/chrome.exe")
    first_start = driver.start_browser(WebStartBrowserParams())
    second_start = driver.start_browser(WebStartBrowserParams())
    first_close = driver.close_browser(WebCloseBrowserParams())
    second_close = driver.close_browser(WebCloseBrowserParams())
    driver.close()

    assert first_start["output"] == {"already_started": False, "url": "about:blank"}
    assert second_start["output"] == {"already_started": True, "url": "about:blank"}
    assert first_close["output"] == {"already_closed": False}
    assert second_close["output"] == {"already_closed": True}
    assert all(result["metadata"]["action_effect"] == "completed" for result in (first_start, second_start, first_close, second_close))
    assert browser.context.closed is True
    assert browser.closed is True
    assert playwright.stopped is True


def test_failed_disposal_retries_remaining_resources_on_original_worker() -> None:
    driver = _ThreadedFakePlaywrightDriver()
    driver.start_browser(WebStartBrowserParams())
    calls = []

    def close_context():
        calls.append(threading.get_ident())
        if len(calls) == 1:
            raise OSError("dispose failed")

    driver._run_sync(lambda: setattr(driver, "_context", types.SimpleNamespace(close=close_context)))
    try:
        with pytest.raises(OSError, match="dispose failed"):
            driver.close()
        assert driver._executor is not None
    finally:
        driver.close()
    assert calls == [driver.create_thread_id, driver.create_thread_id]
    assert driver._executor is None


def test_unavailable_semantic_and_text_observation_is_a_normalized_failure() -> None:
    class Unobservable(_FakePage):
        def aria_snapshot(self, **kwargs):
            raise RuntimeError("backend private failure")

        def locator(self, selector):
            raise RuntimeError("text backend private failure")

    driver = PlaywrightWebDriver(page=Unobservable())
    result = driver.ui_snapshot(WebUiSnapshotParams(scope={"kind": "page", "page": "main"}))
    assert result["status"] == "failed"
    assert result["failure_category"] == "observation_error"
    assert result["metadata"]["action_effect"] == "not_started"
    assert "private failure" not in str(result)


def test_simultaneous_start_owns_only_one_worker(monkeypatch) -> None:
    from fsq_agent.drivers.web import _playwright

    created = []

    def executor_factory(**kwargs):
        time.sleep(0.05)
        executor = ThreadPoolExecutor(**kwargs)
        created.append(executor)
        return executor

    monkeypatch.setattr(_playwright, "ThreadPoolExecutor", executor_factory)
    driver = _ThreadedFakePlaywrightDriver()
    try:
        with ThreadPoolExecutor(max_workers=2) as callers:
            results = list(callers.map(lambda _: driver.start_browser(WebStartBrowserParams()), range(2)))
        assert all(result["status"] == "passed" for result in results)
        assert len(created) == 1
    finally:
        driver.close()
        for executor in created:
            executor.shutdown()


def test_facade_implements_exactly_the_shared_action_catalog() -> None:
    from fsq_agent.capabilities import discover_capability_definitions
    from fsq_agent.models import WEB_ACTION_DEFINITIONS

    definitions = discover_capability_definitions(PlaywrightWebDriver)
    assert {definition.name for definition in definitions} == {definition.driver_method for definition in WEB_ACTION_DEFINITIONS}
    assert {definition.fsq_command_alias for definition in definitions} == {definition.fsq_action_name for definition in WEB_ACTION_DEFINITIONS}


def test_disposal_removes_its_listeners_without_closing_a_borrowed_page() -> None:
    class BorrowedPage(_FakePage):
        def __init__(self):
            super().__init__()
            self.listeners = {}

        def on(self, event, callback):
            self.listeners[event] = callback

        def remove_listener(self, event, callback):
            assert self.listeners[event] == callback
            self.listeners.pop(event)

        def close(self):
            pytest.fail("Disposal must not close the borrowed page")

    page = BorrowedPage()
    driver = PlaywrightWebDriver(page=page)
    assert len(page.listeners) == 2
    driver.close()
    assert page.listeners == {}
    driver.close()


def test_open_page_failure_between_creation_and_navigation_is_partial() -> None:
    from fsq_agent.models import WebOpenPageParams

    class RegistrationFailure(_FakePage):
        def on(self, event, callback):
            raise RuntimeError("listener registration failed")

        def goto(self, *args, **kwargs):
            pytest.fail("Navigation must not start after registration failure")

    driver = PlaywrightWebDriver(page=_FakePage())
    driver._context = types.SimpleNamespace(new_page=RegistrationFailure, close=lambda: None)
    try:
        result = driver.open_page(WebOpenPageParams(page="extra", url="https://example.com"))
        assert result["status"] == "failed"
        assert result["metadata"]["action_effect"] == "indeterminate"
        assert result["metadata"]["replay_unavailable_reason"] == "action_outcome_indeterminate"
    finally:
        driver.close()
