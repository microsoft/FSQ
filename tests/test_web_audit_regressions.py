# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import json
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from fsq_agent import models as m
from fsq_agent.core.evidence._artifact_store import _credential_safe as artifact_safe
from fsq_agent.core.runner._runner import _credential_safe as runner_safe
from fsq_agent.drivers.web import _observation
from fsq_agent.drivers.web._errors import WebBackendError
from fsq_agent.drivers.web._playwright import PlaywrightWebDriver


def target(selector="#button", kind="css"):
    return {"page": "main", "steps": [{"kind": kind, "selector": selector}]}


@pytest.mark.parametrize("kind", ["css", "xpath"])
@pytest.mark.parametrize("selector", ["iframe >> internal:control=enter-frame >> css=button", 'button >> internal:has="aria-ref=e42"', "button >> xpath=.."])
def test_selector_dialects_cannot_smuggle_native_chains(kind, selector):
    with pytest.raises(ValidationError):
        m.WebLocator.model_validate(target(selector, kind))


def test_quoted_selector_text_is_not_a_chain():
    selector = """button[data-caption="a >> b"]"""
    assert m.WebLocator.model_validate(target(selector)).steps[0].selector == selector


def test_relative_native_implicit_xpath_cannot_escape():
    with pytest.raises(ValidationError):
        m.WebRelativeQuery.model_validate({"steps": [{"kind": "playwright", "selector": "(//button)"}]})


@pytest.mark.parametrize("sanitize", [artifact_safe, runner_safe])
def test_credential_shaped_public_locator_is_preserved(sanitize):
    locator = {"page": "main", "steps": [{"kind": "role", "role": "button", "name": "Display token=fixture"}]}
    result = sanitize({"locator": locator, "description": "Display token=fixture"})
    assert result["locator"] == locator
    assert result["description"] == "Display token=***"
    assert sanitize(locator, ("kind",)) == locator


@pytest.mark.parametrize("sanitize", [artifact_safe, runner_safe])
@pytest.mark.parametrize("selector", ['[data-value="private%2Bvalue"]', 'internal:role=button[name="private+value"s]'])
def test_encoded_configured_secret_does_not_survive_locator_preservation(sanitize, selector):
    assert sanitize(target(selector, "playwright"), ("private+value",)) is None


@pytest.mark.parametrize("sanitize", [artifact_safe, runner_safe])
def test_configured_secret_omits_whole_locator_with_truthful_availability(sanitize):
    locator = target('[data-value="actual-private-value"]')
    observation = m.WebObservation(
        observation_id="sample",
        page={"page": "main", "url": "", "title": ""},
        view={"elements": [{"role": "button", "locator": locator, "quality": "structural"}]},
        coverage={"semantic": "complete", "locators": "complete"},
    ).model_dump(mode="json", exclude_none=True)
    result = sanitize(observation, ("actual-private-value",))
    parsed = m.WebObservation.model_validate_json(json.dumps(result))
    assert parsed.view.elements[0].locator is None
    assert parsed.view.elements[0].locator_unavailable_reason
    assert parsed.view.elements[0].quality == "unavailable"
    assert parsed.coverage.locators == "unavailable"
    assert "actual-private-value" not in json.dumps(result)


class Page:
    url = "about:blank"

    def __init__(self):
        self.listeners = {}
        self.closed = False
        self.node = None

    def on(self, event, callback):
        self.listeners.setdefault(event, []).append(callback)

    def remove_listener(self, event, callback):
        self.listeners[event].remove(callback)

    def emit(self, event, value):
        for callback in self.listeners.get(event, []).copy():
            callback(value)

    def locator(self, selector):
        if self.node is None:
            raise RuntimeError("Selector parse error")
        return self.node

    def is_closed(self):
        return self.closed

    def close(self):
        self.closed = True

    def goto(self, url, **kwargs):
        self.url = url
        return SimpleNamespace(status=200)


def test_observation_preserves_invalid_selector_diagnostics():
    driver = PlaywrightWebDriver(page=Page())
    locator = target("[")
    click = driver.click_on(m.WebClickOnParams(target=locator))
    snapshot = driver.ui_snapshot(m.WebUiSnapshotParams(scope={"kind": "element", "target": locator}))
    assert click["metadata"]["error_code"] == snapshot["metadata"]["error_code"] == "invalid_selector"
    assert snapshot["failure_category"] == "target_resolution_error"


@pytest.mark.parametrize("event", ["dialog", "popup"])
def test_event_expectations_do_not_suppress_another_pages_event(monkeypatch, event):
    main, other = Page(), Page()
    driver = PlaywrightWebDriver(page=main)
    driver._register("other", other)
    monkeypatch.setattr("fsq_agent.drivers.web._playwright.run_trigger", lambda page, operation, **kwargs: operation())
    result = driver._call(lambda: driver._event_call(main, lambda: other.emit(event, object()), kind=event, timeout=100))
    assert result["status"] == "failed"
    assert result["metadata"]["error_code"] == f"unexpected_{event}"
    assert driver.close_page(m.WebClosePageParams(page="other"))["status"] == "passed"
    assert not any(other.listeners.values())
    assert driver.navigate_to(m.WebNavigateToParams(page="main", url="about:blank"))["status"] == "passed"


def test_closing_one_page_does_not_clear_another_pages_dialog():
    main, other = Page(), Page()
    driver = PlaywrightWebDriver(page=main)
    driver._register("other", other)
    main.emit("dialog", object())
    other.emit("dialog", object())
    assert driver.close_page(m.WebClosePageParams(page="other"))["status"] == "passed"
    assert driver.navigate_to(m.WebNavigateToParams(page="main", url="about:blank"))["metadata"]["error_code"] == "unexpected_dialog"


def test_action_does_not_restart_timeout_after_readiness(monkeypatch):
    clock = SimpleNamespace(now=0.0, primitive_timeout=None)
    page = Page()

    class Node:
        def count(self):
            return 1

        def is_visible(self, **kwargs):
            return clock.now >= 0.075

        def is_enabled(self, **kwargs):
            return True

        def click(self, *, timeout, **kwargs):
            clock.primitive_timeout = timeout
            clock.now += timeout / 1000
            raise TimeoutError("fixture")

    def pause(milliseconds):
        clock.now += milliseconds / 1000

    page.node = Node()
    page.wait_for_timeout = pause
    monkeypatch.setattr("fsq_agent.drivers.web._playwright.time.monotonic", lambda: clock.now)
    result = PlaywrightWebDriver(page=page).click_on(m.WebClickOnParams(target=target(), timeout_ms=100))
    assert result["status"] == "failed"
    assert 0 < clock.primitive_timeout <= 26
    assert clock.now <= 0.101


@pytest.mark.parametrize("alias", ["main", "a" * 64])
def test_small_budget_never_returns_an_oversized_envelope(alias):
    observation = m.WebObservation(
        observation_id="0123456789ab",
        page={"page": alias, "url": "", "title": ""},
        view={},
        coverage={"semantic": "partial", "locators": "unavailable"},
    )
    if len(alias) == 64:
        with pytest.raises(WebBackendError) as failure:
            _observation._bound(observation, 256)
        assert failure.value.code == "observation_budget_too_small"
    else:
        result = _observation._bound(observation, 256)
        assert len(json.dumps(result)) <= 256


@pytest.mark.parametrize(
    ("selector", "expected"),
    [("css=li >> nth=0 >> css=button", "positional"), ("css=section >> css=button", "structural")],
)
def test_authored_native_quality_describes_the_returned_rule(monkeypatch, selector, expected):
    authored = m.WebLocator.model_validate(target(selector, "playwright"))
    generated = m.WebLocator.model_validate({"page": "main", "steps": [{"kind": "role", "role": "button", "name": "Unique"}]})
    monkeypatch.setattr(_observation, "_generated", lambda *args: (generated, "semantic", {"structural": False, "positional": False}))
    source = SimpleNamespace(evaluate=lambda *args, **kwargs: {"state": {}, "attributes": {}, "tag": "button", "options": []})
    element, _ = _observation._element(Page(), source, {"role": "button", "name": "Unique"}, "main", authored=authored)
    assert element.locator == authored
    assert element.quality == expected
