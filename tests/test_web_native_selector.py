# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from importlib.metadata import version
from pathlib import Path

import pytest


@pytest.fixture
def native_page():
    from playwright.sync_api import sync_playwright

    executable = Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")
    if not executable.is_file():
        pytest.skip("Pinned native-selector tests require an installed Edge browser")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=str(executable), headless=True)
        page = browser.new_page()
        yield page
        browser.close()


def test_pinned_selector_normalization_round_trips_real_source_nodes(native_page):
    from fsq_agent.drivers.web._native import normalize_selector

    assert version("playwright") == "1.60.0"
    native_page.set_content("""
        <select id="size"><option value="10">Ten</option></select>
        <section aria-label="First"><button>Add to cart</button></section>
        <section aria-label="Second"><button>Add to cart</button></section>
        <iframe srcdoc="<button>Inside</button>"></iframe>
        <div><button></button><button></button></div>
    """)
    sources = [
        native_page.locator("#size"),
        native_page.get_by_label("Second").get_by_role("button"),
        native_page.frame_locator("iframe").get_by_role("button"),
        native_page.locator("div > button").nth(1),
    ]
    for source in sources:
        selector, quality = normalize_selector(native_page, source)
        assert selector
        assert "aria-ref" not in selector
        replay = native_page.locator(selector)
        assert replay.count() == 1
        handle = source.element_handle()
        try:
            assert replay.evaluate("(node, source) => node === source", handle)
        finally:
            handle.dispose()
        assert quality["validated_unique"] is True
        assert quality["source_identity_verified"] is True
        assert isinstance(quality["structural"], bool)
        assert isinstance(quality["positional"], bool)
    _, semantic_quality = normalize_selector(native_page, sources[1])
    assert semantic_quality["structural"] is False
    _, positional_quality = normalize_selector(native_page, sources[3])
    assert positional_quality["positional"] is True


def test_native_adapter_explicitly_rejects_missing_serialization_and_ambiguity():
    from fsq_agent.drivers.web._native import NativeSelectorError, normalize_selector

    class Unsupported:
        def count(self):
            return 1

        def normalize(self):
            return object()

    with pytest.raises(NativeSelectorError, match="selector"):
        normalize_selector(object(), Unsupported())

    class Ambiguous(Unsupported):
        def count(self):
            return 2

    with pytest.raises(NativeSelectorError, match="unique"):
        normalize_selector(object(), Ambiguous())


def test_native_semantic_nodes_include_unnamed_options_and_frame_identity(native_page):
    from fsq_agent.drivers.web._native import normalize_selector
    from fsq_agent.drivers.web._semantic import DOM_FACTS, parse_semantics

    native_page.set_content("""
      <main><select><option value="10">Ten</option><option value="20">Twenty</option></select>
      <input type="password" value="private-token">
      <iframe srcdoc="<button>Inside</button>"></iframe></main>
    """)
    tree, candidates = parse_semantics(native_page.aria_snapshot(mode="ai"))
    assert "private-token" not in str(tree)
    select = next(item for item in candidates if item["role"] == "combobox")
    node = native_page.locator(f"aria-ref={select['native_ref']}")
    facts = node.evaluate(DOM_FACTS)
    assert facts["options"] == [
        {"label": "Ten", "value": "10", "index": 0, "selected": True, "disabled": False},
        {"label": "Twenty", "value": "20", "index": 1, "selected": False, "disabled": False},
    ]
    inside = next(item for item in candidates if item["name"] == "Inside")
    selector, _ = normalize_selector(native_page, native_page.locator(f"aria-ref={inside['native_ref']}"))
    assert "enter-frame" in selector
    assert native_page.locator(selector).inner_text() == "Inside"


def test_unique_resolution_waits_for_rendering_without_implicit_first(native_page):
    from fsq_agent.drivers.web._errors import WebBackendError
    from fsq_agent.drivers.web._resolve import resolve_unique

    native_page.set_content("<main></main>")
    native_page.evaluate("setTimeout(()=>document.querySelector('main').innerHTML='<button>Ready</button>', 80)")
    target = native_page.get_by_role("button", name="Ready", exact=True)
    assert resolve_unique(native_page, target, timeout=1000, visible=True) is target
    native_page.evaluate("document.querySelector('main').append(document.querySelector('button').cloneNode(true))")
    with pytest.raises(WebBackendError) as failure:
        resolve_unique(native_page, target, timeout=1000, visible=True)
    assert failure.value.code == "target_ambiguous"
    assert failure.value.details["match_count"] == 2


def test_absence_condition_allows_zero_but_not_multiple_matches(native_page):
    from fsq_agent.drivers.web._errors import WebBackendError
    from fsq_agent.drivers.web._resolve import wait_condition

    native_page.set_content("<button>Other</button>")
    assert wait_condition(native_page, native_page.get_by_text("Missing", exact=True), lambda node: not node.is_visible(), timeout=100, allow_absent=True)
    native_page.set_content("<button>A</button><button>A</button>")
    with pytest.raises(WebBackendError) as failure:
        wait_condition(native_page, native_page.get_by_role("button"), lambda node: not node.is_visible(), timeout=100, allow_absent=True)
    assert failure.value.code == "target_ambiguous"


def test_real_trigger_bound_popup_and_dialog(native_page):
    from fsq_agent.drivers.web._events import run_trigger

    native_page.set_content("""
      <button onclick="window.open('about:blank')">Popup</button>
      <button onclick="document.querySelector('output').textContent=prompt('Name')">Prompt</button>
      <output></output>
    """)
    popup = run_trigger(native_page, lambda: native_page.get_by_text("Popup", exact=True).click(), kind="popup", timeout=1000)
    assert popup.url == "about:blank"
    popup.close()
    event = run_trigger(
        native_page,
        lambda: native_page.get_by_text("Prompt", exact=True).click(),
        kind="dialog",
        dialog_type="prompt",
        action="accept",
        prompt_text="Public fixture",
        timeout=1000,
    )
    assert event["dialog_type"] == "prompt"
    assert native_page.locator("output").inner_text() == "Public fixture"


def test_real_missing_expected_event_never_retriggers(native_page):
    from fsq_agent.drivers.web._errors import WebBackendError
    from fsq_agent.drivers.web._events import run_trigger

    native_page.set_content('<button onclick="window.count=(window.count||0)+1">Once</button>')
    with pytest.raises(WebBackendError) as failure:
        run_trigger(
            native_page,
            lambda: native_page.get_by_role("button").click(),
            kind="dialog",
            dialog_type="alert",
            action="dismiss",
            timeout=60,
        )
    assert failure.value.code == "event_timeout"
    assert native_page.evaluate("window.count") == 1


def test_literal_selector_like_names_are_not_native_reference_engines(native_page):
    from fsq_agent.drivers.web._native import normalize_selector, selector_steps

    native_page.set_content('<button aria-label="Use aria-ref=e7 &gt;&gt; example">Visible</button>')
    selector, _ = normalize_selector(native_page, native_page.get_by_role("button"))
    steps = selector_steps(selector)
    assert len(steps) == 1
    assert steps[0]["selector"] == selector
    assert native_page.locator(selector).count() == 1
