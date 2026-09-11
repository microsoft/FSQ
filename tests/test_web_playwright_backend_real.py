# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from fsq_agent import models as m
from fsq_agent.drivers.web._playwright import PlaywrightWebDriver


def target(*steps, page="main"):
    return {"page": page, "steps": list(steps)}


def css(selector, page="main"):
    return target({"kind": "css", "selector": selector}, page=page)


@pytest.fixture
def driver():
    executable = Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")
    if not executable.is_file():
        pytest.skip("Real backend tests require installed Edge")
    result = PlaywrightWebDriver(channel="msedge", executable_path=executable)
    assert result.start_browser(m.WebStartBrowserParams())["status"] == "passed"
    yield result
    result.close()


def content(driver, html):
    driver._run_sync(lambda: driver.page.set_content(html))


def evaluate(driver, script):
    return driver._run_sync(lambda: driver._pages["main"].evaluate(script))


def observation(value):
    return m.WebObservation.model_validate_json(json.dumps(value))


def test_authored_ordering_is_preserved_and_ambiguity_is_pre_effect(driver):
    content(
        driver,
        """<ul><li id="a"><button onclick="window.picked='a'">Add</button></li>
      <li id="b"><button onclick="window.picked='b'">Add</button></li></ul>""",
    )
    ambiguous = driver.click_on(m.WebClickOnParams(target=target({"kind": "role", "role": "button", "name": "Add"}), timeout_ms=200))
    assert ambiguous["metadata"]["error_code"] == "target_ambiguous"
    assert ambiguous["metadata"]["action_effect"] == "not_started"
    assert evaluate(driver, "window.picked") is None
    rule = target(
        {"kind": "role", "role": "listitem"},
        {"kind": "filter", "has": {"steps": [{"kind": "role", "role": "button", "name": "Add", "disabled": False}]}},
        {"kind": "first"},
        {"kind": "role", "role": "button", "name": "Add"},
    )
    params = m.WebClickOnParams(target=rule)
    assert driver.click_on(params)["metadata"]["action_effect"] == "completed"
    assert evaluate(driver, "window.picked") == "a"
    evaluate(driver, "document.querySelector('ul').prepend(document.querySelector('#b'))")
    assert driver.click_on(params)["status"] == "passed"
    assert evaluate(driver, "window.picked") == "b"
    assert params.target.model_dump(exclude_none=True)["steps"][2] == {"kind": "first"}


def test_text_state_selection_keyboard_and_absence(driver):
    content(
        driver,
        """<input id="text"><input id="check" type="checkbox">
      <select id="size"><option value="10">Ten</option><option value="20">Twenty</option></select>""",
    )
    assert driver.fill_text(m.WebFillTextParams(target=css("#text"), text="one"))["status"] == "passed"
    assert driver.type_text(m.WebTypeTextParams(target=css("#text"), text="two"))["status"] == "passed"
    assert driver.assert_value(m.WebAssertValueParams(target=css("#text"), value="onetwo"))["status"] == "passed"
    assert driver.set_checked(m.WebSetCheckedParams(target=css("#check"), checked=True))["status"] == "passed"
    assert driver.assert_state(m.WebAssertStateParams(target=css("#check"), state={"checked": True, "enabled": True}))["status"] == "passed"
    for selection, expected in [
        ({"kind": "label", "labels": ["Twenty"]}, "20"),
        ({"kind": "index", "indices": [0]}, "10"),
        ({"kind": "value", "values": ["20"]}, "20"),
    ]:
        assert driver.select_option(m.WebSelectOptionParams(target=css("#size"), selection=selection))["status"] == "passed"
        assert driver.assert_value(m.WebAssertValueParams(target=css("#size"), value=expected))["status"] == "passed"
    key = m.WebPressKeyParams(scope={"kind": "element", "target": css("#text")}, key="End")
    assert driver.press_key(key)["status"] == "passed"
    assert driver.assert_not_visible(m.WebAssertNotVisibleParams(target=css("#missing"), timeout_ms=100))["status"] == "passed"
    assert driver.wait_for(m.WebWaitForParams(condition={"kind": "element", "target": css("#missing"), "state": "detached"}, timeout_ms=100))["status"] == "passed"


def test_delayed_target_frames_and_native_locator_roundtrip(driver):
    content(driver, "<iframe srcdoc=\"<button onclick=&quot;this.textContent='Done'&quot;>Inside</button>\"></iframe>")
    inside = target({"kind": "css", "selector": "iframe"}, {"kind": "enter_frame"}, {"kind": "role", "role": "button", "name": "Inside"})
    assert driver.click_on(m.WebClickOnParams(target=inside))["status"] == "passed"
    evaluate(driver, "setTimeout(()=>document.body.insertAdjacentHTML('beforeend','<button id=\"late\">Late</button>'),80)")
    assert driver.click_on(m.WebClickOnParams(target=css("#late"), timeout_ms=1000))["status"] == "passed"
    snapshot = driver.ui_snapshot(m.WebUiSnapshotParams(scope={"kind": "page", "page": "main"}))
    observed = observation(snapshot)
    framed = next(item for item in observed.view.elements if item.name == "Done")
    assert any(step.kind == "enter_frame" for step in framed.locator.steps)
    assert driver.click_on(m.WebClickOnParams(target=framed.locator))["status"] == "passed"


def test_observation_options_budget_source_and_query_rules(driver):
    content(
        driver,
        '<main><h1>Catalog</h1><select><option value="10">Ten</option><option value="20">Twenty</option></select>'
        + "".join(f"<article><h2>Item {i}</h2><button>Add</button></article>" for i in range(130))
        + "</main>",
    )
    result = driver.ui_snapshot(m.WebUiSnapshotParams(scope={"kind": "page", "page": "main"}, view="full", max_chars=4500))
    observed = observation(result)
    inline = {key: value for key, value in result.items() if key != "full_source"}
    assert len(json.dumps(inline)) <= 4500
    assert observed.full_source.semantic_text
    assert len(observed.full_source.semantic_text) > 4500
    assert "ref=" not in json.dumps(result)
    assert observed.coverage.locators == "partial"
    inspected = driver.inspect_element(m.WebInspectElementParams(target=target({"kind": "role", "role": "combobox", "name": ""})))
    select = observation(inspected).view.elements[0]
    assert [(option.label, option.value, option.index) for option in select.options] == [("Ten", "10", 0), ("Twenty", "20", 1)]
    rule = target({"kind": "role", "role": "button"}, {"kind": "first"})
    found = driver.find_elements(m.WebFindElementsParams(target=rule))
    assert observation(found).view.elements[0].locator == m.WebLocator.model_validate(rule)


def test_owned_pages_popup_dialog_and_event_timeout(driver):
    content(
        driver,
        """<button id="popup" onclick="window.open('about:blank')">Popup</button>
      <button id="prompt" onclick="window.answer=prompt('Question')">Prompt</button>
      <button id="once" onclick="window.count=(window.count||0)+1">Once</button>""",
    )
    popup = driver.click_on(m.WebClickOnParams(target=css("#popup"), expect={"kind": "popup", "page": "details", "timeout_ms": 1000}))
    assert popup["status"] == "passed"
    listed = driver.list_pages(m.WebListPagesParams())
    assert {page["page"] for page in listed["output"]["pages"]} == {"main", "details"}
    assert driver.activate_page(m.WebActivatePageParams(page="details"))["status"] == "passed"
    assert driver.close_page(m.WebClosePageParams(page="details"))["status"] == "passed"
    dialog = driver.click_on(
        m.WebClickOnParams(
            target=css("#prompt"),
            expect={
                "kind": "dialog",
                "dialog_type": "prompt",
                "action": "accept",
                "prompt_text": {"text": "answer"},
                "timeout_ms": 1000,
            },
        )
    )
    assert dialog["status"] == "passed"
    assert evaluate(driver, "window.answer") == "answer"
    timeout = driver.click_on(m.WebClickOnParams(target=css("#once"), expect={"kind": "popup", "page": "missing", "timeout_ms": 80}))
    assert timeout["metadata"]["action_effect"] == "indeterminate"
    assert timeout["metadata"]["error_code"] == "event_timeout"
    assert timeout["metadata"]["replay_unavailable_reason"] == "event_contract_unresolved"
    assert evaluate(driver, "window.count") == 1
    assert driver.open_page(m.WebOpenPageParams(page="extra"))["status"] == "passed"
    assert driver.close_page(m.WebClosePageParams(page="extra"))["status"] == "passed"


@pytest.mark.parametrize("budget", [256, 500, 1200, 12000])
def test_small_and_unicode_observations_are_bounded(driver, budget):
    content(driver, "<main><h1>目录" + "很长" * 300 + "</h1><button>保存</button></main>")
    result = driver.ui_snapshot(m.WebUiSnapshotParams(scope={"kind": "page", "page": "main"}, max_chars=budget))
    observation(result)
    inline = {key: value for key, value in result.items() if key != "full_source"}
    assert len(json.dumps(inline)) <= budget


def test_long_locator_and_option_strings_remain_exact_or_explicitly_unavailable(driver):
    name = "Long control " + "x" * 14000
    content(driver, f'<button aria-label="{name}">Visible</button><button>Short</button><select><option value="{name}">Short label</option></select>')
    result = driver.ui_snapshot(m.WebUiSnapshotParams(scope={"kind": "page", "page": "main"}, max_chars=4000))
    parsed = observation(result)
    assert "Inline budget" in " ".join(parsed.coverage.omissions)
    for element in parsed.view.elements:
        if element.locator is not None:
            for step in element.locator.steps:
                if step.kind == "playwright":
                    assert "…" not in step.selector
        for option in element.options:
            assert option.value == name
    assert "…" not in parsed.full_source.semantic_text


def test_focus_context_prioritized_and_password_values_never_exposed(driver):
    content(driver, "<main>" + "".join(f"<button>Button {i}</button>" for i in range(60)) + '<input aria-label="Current" type="password" value="private-password"></main>')
    evaluate(driver, "document.querySelector('input').focus()")
    result = driver.ui_snapshot(m.WebUiSnapshotParams(scope={"kind": "page", "page": "main"}, max_items=3))
    parsed = observation(result)
    assert parsed.view.focused is not None
    assert parsed.view.focused.name == "Current"
    assert "private-password" not in json.dumps(result)


def test_frame_filters_are_relative_and_scope_observation_is_read_only(driver):
    content(driver, """<iframe srcdoc="<ul><li><button>First</button></li><li><button>Second</button></li></ul>"></iframe>""")
    rule = target(
        {"kind": "css", "selector": "iframe"},
        {"kind": "enter_frame"},
        {"kind": "role", "role": "listitem"},
        {"kind": "filter", "has": {"steps": [{"kind": "role", "role": "button", "name": "Second"}]}},
        {"kind": "first"},
        {"kind": "role", "role": "button"},
    )
    assert driver.click_on(m.WebClickOnParams(target=rule, timeout_ms=1000))["status"] == "passed"
    scoped = driver.ui_snapshot(m.WebUiSnapshotParams(scope={"kind": "element", "target": rule}, view="scoped"))
    parsed = observation(scoped)
    assert [element.name for element in parsed.view.elements] == ["Second"]
    assert "First" not in parsed.full_source.semantic_text


def test_invalid_select_and_second_drag_endpoint_fail_before_effects(driver):
    content(driver, '<input id="text" value="unchanged"><select><option>One</option></select><div id="drag" draggable="true">Drag</div>')
    selected = driver.select_option(m.WebSelectOptionParams(target=css("select"), selection={"kind": "index", "indices": [8]}))
    assert selected["metadata"]["action_effect"] == "not_started"
    dragged = driver.drag_to(m.WebDragToParams(source=css("#drag"), destination=css("#absent"), timeout_ms=60))
    assert dragged["metadata"]["action_effect"] == "not_started"
    invalid = driver.click_on(m.WebClickOnParams(target=css("["), timeout_ms=60))
    assert invalid["metadata"]["error_code"] == "invalid_selector"
    assert invalid["metadata"]["action_effect"] == "not_started"


def test_mouse_scroll_text_waits_and_no_implicit_post_action_capture(driver):
    content(
        driver,
        """<style>#box{height:40px;overflow:auto}#deep{margin-top:1200px}</style>
        <div id="box"><p style="height:400px">Scrollable</p></div><input id="text"><button id="deep">Deep</button>""",
    )
    assert driver.hover_on(m.WebHoverOnParams(target=css("#box")))["status"] == "passed"
    assert driver.scroll(m.WebScrollParams(scope={"kind": "element", "target": css("#box")}, delta_y=80))["status"] == "passed"
    assert evaluate(driver, "document.querySelector('#box').scrollTop") > 0
    assert driver.scroll_into_view(m.WebScrollIntoViewParams(target=css("#deep")))["status"] == "passed"
    assert evaluate(driver, "window.scrollY") > 0
    assert driver.scroll(m.WebScrollParams(scope={"kind": "page", "page": "main"}, delta_y=-100))["status"] == "passed"
    assert driver.assert_text(m.WebAssertTextParams(target=css("#box"), text={"kind": "equals", "value": "Scrollable"}))["status"] == "passed"
    assert (
        driver.wait_for(m.WebWaitForParams(condition={"kind": "text", "scope": {"kind": "page", "page": "main"}, "text": {"kind": "contains", "value": "Not here"}, "present": False}, timeout_ms=100))[
            "status"
        ]
        == "passed"
    )
    original = driver._pages["main"].aria_snapshot
    driver._run_sync(lambda: setattr(driver._pages["main"], "aria_snapshot", lambda **kwargs: (_ for _ in ()).throw(RuntimeError("capture failed"))))
    try:
        assert driver.click_on(m.WebClickOnParams(target=css("#deep")))["metadata"]["action_effect"] == "completed"
    finally:
        driver._run_sync(lambda: setattr(driver._pages["main"], "aria_snapshot", original))


def test_wrong_dialog_type_is_not_silently_dismissed_or_accepted(driver):
    content(driver, "<button onclick=\"window.answer=prompt('Question')\">Prompt</button>")
    failed = driver.click_on(m.WebClickOnParams(target=css("button"), timeout_ms=1000, expect={"kind": "dialog", "dialog_type": "confirm", "action": "accept", "timeout_ms": 1000}))
    assert failed["metadata"]["error_code"] == "unexpected_dialog"
    assert failed["metadata"]["action_effect"] == "indeterminate"
    blocked = driver.click_on(m.WebClickOnParams(target=css("button"), timeout_ms=100))
    assert blocked["metadata"]["action_effect"] == "not_started"
    assert driver.close_browser(m.WebCloseBrowserParams())["status"] == "passed"


def test_navigation_history_reload_and_load_url_waits(driver):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(f"<main><h1>{self.path}</h1></main>".encode())

        def log_message(self, *args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{server.server_port}"
        for route in ("/one", "/two"):
            assert driver.navigate_to(m.WebNavigateToParams(page="main", url=base + route))["status"] == "passed"
        assert driver.navigate_back(m.WebNavigateBackParams(page="main"))["output"]["url"] == base + "/one"
        assert driver.navigate_forward(m.WebNavigateForwardParams(page="main"))["output"]["url"] == base + "/two"
        assert driver.reload_page(m.WebReloadPageParams(page="main"))["status"] == "passed"
        assert driver.wait_for(m.WebWaitForParams(condition={"kind": "url", "page": "main", "url": base + "/two"}))["status"] == "passed"
        assert driver.wait_for(m.WebWaitForParams(condition={"kind": "load_state", "page": "main", "state": "load"}))["status"] == "passed"
        assert driver.take_screenshot(m.WebTakeScreenshotParams(page="main"))["output"]["bytes"] > 0
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_drag_click_options_and_key_bound_events(driver):
    content(
        driver,
        """<div draggable="true" id="drag" ondragstart="event.dataTransfer.setData('text/plain','fixture')">Drag</div>
      <div id="drop" style="height:80px" ondragover="event.preventDefault()" ondrop="event.preventDefault();window.dropped=event.dataTransfer.getData('text/plain')">Drop</div>
      <button id="double" ondblclick="window.doubleShift=event.shiftKey">Double</button>
      <button id="popup" onclick="window.open('about:blank')">Popup</button>
      <button id="alert" onclick="alert('Expected')">Alert</button>""",
    )
    assert driver.drag_to(m.WebDragToParams(source=css("#drag"), destination=css("#drop")))["status"] == "passed"
    assert evaluate(driver, "window.dropped") == "fixture"
    assert driver.click_on(m.WebClickOnParams(target=css("#double"), click_count=2, modifiers=["Shift"]))["status"] == "passed"
    assert evaluate(driver, "window.doubleShift") is True
    popup = driver.press_key(m.WebPressKeyParams(scope={"kind": "element", "target": css("#popup")}, key="Enter", expect={"kind": "popup", "page": "key_popup", "timeout_ms": 1000}))
    assert popup["status"] == "passed"
    assert driver.close_page(m.WebClosePageParams(page="key_popup"))["status"] == "passed"
    assert (
        driver.press_key(
            m.WebPressKeyParams(scope={"kind": "element", "target": css("#alert")}, key="Enter", expect={"kind": "dialog", "dialog_type": "alert", "action": "dismiss", "timeout_ms": 1000})
        )["status"]
        == "passed"
    )


def test_text_predicates_share_normalized_literal_matching(driver):
    content(driver, '<div class="row" onclick="window.picked=\'upper\'">One    two</div><div class="row" onclick="window.picked=\'lower\'">one two</div>')
    equals = target({"kind": "css", "selector": ".row"}, {"kind": "filter", "text": {"kind": "equals", "value": "One two"}})
    assert driver.click_on(m.WebClickOnParams(target=equals, timeout_ms=1000))["status"] == "passed"
    assert evaluate(driver, "window.picked") == "upper"
    contains = target({"kind": "css", "selector": ".row"}, {"kind": "filter", "text": {"kind": "contains", "value": "one"}})
    assert driver.click_on(m.WebClickOnParams(target=contains, timeout_ms=1000))["status"] == "passed"
    assert evaluate(driver, "window.picked") == "lower"
