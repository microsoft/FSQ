# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from typing import Protocol, runtime_checkable

from fsq_agent.core.interfaces._harness import DriverObservationInterface
from fsq_agent.models import (
    WebActivatePageParams,
    WebAssertNotVisibleParams,
    WebAssertStateParams,
    WebAssertTextParams,
    WebAssertValueParams,
    WebAssertVisibleParams,
    WebAssertWithAIParams,
    WebClickOnParams,
    WebCloseBrowserParams,
    WebClosePageParams,
    WebDragToParams,
    WebFillTextParams,
    WebFindElementsParams,
    WebHoverOnParams,
    WebInspectElementParams,
    WebListPagesParams,
    WebNavigateBackParams,
    WebNavigateForwardParams,
    WebNavigateToParams,
    WebOpenPageParams,
    WebPressKeyParams,
    WebReloadPageParams,
    WebScrollIntoViewParams,
    WebScrollParams,
    WebSelectOptionParams,
    WebSetCheckedParams,
    WebStartBrowserParams,
    WebTakeScreenshotParams,
    WebTypeTextParams,
    WebUiSnapshotParams,
    WebWaitForParams,
)


@runtime_checkable
class WebDriverInterface(DriverObservationInterface, Protocol):
    """Models-only browser boundary. Results use normalized status/output/failure/error/metadata fields.

    Known action effects and replay unavailability live in metadata, independently of observation health.
    take_screenshot returns output.png bytes for Harness persistence, never inline model presentation.
    """

    def context(self) -> dict[str, object]: ...

    def start_browser(self, params: WebStartBrowserParams) -> dict[str, object]: ...

    def close_browser(self, params: WebCloseBrowserParams) -> dict[str, object]: ...

    def navigate_to(self, params: WebNavigateToParams) -> dict[str, object]: ...

    def navigate_back(self, params: WebNavigateBackParams) -> dict[str, object]: ...

    def navigate_forward(self, params: WebNavigateForwardParams) -> dict[str, object]: ...

    def reload_page(self, params: WebReloadPageParams) -> dict[str, object]: ...

    def click_on(self, params: WebClickOnParams) -> dict[str, object]: ...

    def type_text(self, params: WebTypeTextParams) -> dict[str, object]: ...

    def fill_text(self, params: WebFillTextParams) -> dict[str, object]: ...

    def select_option(self, params: WebSelectOptionParams) -> dict[str, object]: ...

    def set_checked(self, params: WebSetCheckedParams) -> dict[str, object]: ...

    def hover_on(self, params: WebHoverOnParams) -> dict[str, object]: ...

    def drag_to(self, params: WebDragToParams) -> dict[str, object]: ...

    def scroll(self, params: WebScrollParams) -> dict[str, object]: ...

    def scroll_into_view(self, params: WebScrollIntoViewParams) -> dict[str, object]: ...

    def press_key(self, params: WebPressKeyParams) -> dict[str, object]: ...

    def wait_for(self, params: WebWaitForParams) -> dict[str, object]: ...

    def take_screenshot(self, params: WebTakeScreenshotParams) -> dict[str, object]: ...

    def ui_snapshot(self, params: WebUiSnapshotParams) -> dict[str, object]: ...

    def find_elements(self, params: WebFindElementsParams) -> dict[str, object]: ...

    def inspect_element(self, params: WebInspectElementParams) -> dict[str, object]: ...

    def assert_visible(self, params: WebAssertVisibleParams) -> dict[str, object]: ...

    def assert_not_visible(self, params: WebAssertNotVisibleParams) -> dict[str, object]: ...

    def assert_text(self, params: WebAssertTextParams) -> dict[str, object]: ...

    def assert_state(self, params: WebAssertStateParams) -> dict[str, object]: ...

    def assert_value(self, params: WebAssertValueParams) -> dict[str, object]: ...

    def assert_with_ai(self, params: WebAssertWithAIParams) -> dict[str, object]: ...

    def list_pages(self, params: WebListPagesParams) -> dict[str, object]: ...

    def open_page(self, params: WebOpenPageParams) -> dict[str, object]: ...

    def activate_page(self, params: WebActivatePageParams) -> dict[str, object]: ...

    def close_page(self, params: WebClosePageParams) -> dict[str, object]: ...

    def screenshot(self, params: WebTakeScreenshotParams | None = None) -> bytes: ...
