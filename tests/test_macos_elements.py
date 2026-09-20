# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import pytest
from pydantic import ValidationError

from fsq_agent.drivers.macos._appium_mac2 import AppiumMac2Driver
from fsq_agent.models import ConfigurationError, MacOSAssertVisibleParams, MacOSClickOnParams, MacOSLocator, MacOSRightClickOnParams, MacOSUiSnapshotParams


class Element:
    def __init__(self, identifier, **attributes):
        self.id = identifier
        self.attributes = attributes

    def is_displayed(self):
        return True

    def get_attribute(self, name):
        return self.attributes.get(name)


class Session:
    def __init__(self, elements=(), source="<Application/>", error=None):
        self.elements = list(elements)
        self.page_source = source
        self.error = error
        self.calls = []

    def find_elements(self, strategy, selector):
        self.calls.append((strategy, selector))
        if self.error:
            raise self.error
        return self.elements


def test_locator_combines_value_and_type_without_first_match_fallback():
    session = Session([Element("one")])
    driver = AppiumMac2Driver(session=session)
    result = driver.assert_visible(MacOSAssertVisibleParams(locator=MacOSLocator(value="11", controlType="XCUIElementTypeStaticText")))
    assert result["status"] == "passed"
    assert len(session.calls) == 1
    assert "@value=" in session.calls[0][1]
    assert "XCUIElementTypeStaticText" in session.calls[0][1]
    assert " and " in session.calls[0][1]


def test_locator_literal_apostrophe_and_quotes_are_encoded():
    session = Session([Element("one")])
    driver = AppiumMac2Driver(session=session)
    driver.assert_visible(MacOSAssertVisibleParams(target='Men\'s "Evo" \\ shoes'))
    assert session.calls[0][0] == "xpath"
    assert "concat(" in session.calls[0][1]


def test_title_is_preserved_and_supported_by_locators():
    title = "Save Link As" + "x" * 50
    driver = AppiumMac2Driver(session=Session(source=f'<Application><MenuItem title="{title}"/></Application>'))
    assert "title" in MacOSLocator.model_json_schema()["properties"]

    tree = driver.ui_snapshot(MacOSUiSnapshotParams())
    assert tree["page_source"]["root"]["children"][0]["attributes"]["title"] == title[:50]
    assert tree["page_source"]["root"]["children"][0]["truncated_attributes"] == ["title"]

    locator_session = Session([Element("one")])
    AppiumMac2Driver(session=locator_session).assert_visible(MacOSAssertVisibleParams(locator=MacOSLocator(title=title)))
    assert f"@title='{title}'" in locator_session.calls[0][1]

    target_session = Session([Element("one")])
    AppiumMac2Driver(session=target_session).assert_visible(MacOSAssertVisibleParams(target=title))
    assert f"@title='{title}'" in target_session.calls[0][1]

    with pytest.raises(ConfigurationError) as error:
        AppiumMac2Driver(session=Session([Element("a", title=title), Element("b", title=title)])).assert_visible(MacOSAssertVisibleParams(target=title))
    assert error.value.context["candidates"][0]["display"]["title"] == title[:50]


def test_ambiguous_locator_never_claims_visible():
    driver = AppiumMac2Driver(session=Session([Element("a"), Element("b")]))
    with pytest.raises(ConfigurationError) as error:
        driver.assert_visible(MacOSAssertVisibleParams(target="same"))
    assert error.value.context["resolution_reason"] == "ambiguous"


def test_backend_error_not_disguised_as_missing_target():
    driver = AppiumMac2Driver(session=Session(error=RuntimeError("SECRET backend body")))
    with pytest.raises(ConfigurationError) as error:
        driver.assert_visible(MacOSAssertVisibleParams(target="shoe"))
    assert error.value.context["resolution_reason"] == "backend_error"
    assert "SECRET" not in str(error.value)


def test_tree_clipping_is_explicit():
    source = '<Application><Button label="' + "x" * 60 + '"/></Application>'
    driver = AppiumMac2Driver(session=Session(source=source))
    tree = driver.ui_snapshot(MacOSUiSnapshotParams())
    node = tree["page_source"]["root"]["children"][0]
    assert node["truncated_attributes"] == ["label"]


@pytest.mark.parametrize("error_name,reason", [("InvalidSelectorException", "invalid_locator"), ("InvalidSessionIdException", "session_unavailable"), ("WebDriverException", "backend_error")])
def test_backend_failure_classification(error_name, reason):
    from selenium.common import exceptions

    session = Session(error=getattr(exceptions, error_name)("private response"))
    with pytest.raises(ConfigurationError) as error:
        AppiumMac2Driver(session=session).assert_visible(MacOSAssertVisibleParams(target="shoe"))
    assert error.value.context == {"resolution_reason": reason}
    assert "private response" not in str(error.value)


def test_no_session_is_distinct():
    with pytest.raises(ConfigurationError) as error:
        AppiumMac2Driver().ui_snapshot(MacOSUiSnapshotParams())
    assert error.value.context["resolution_reason"] == "session_unavailable"


def test_explicit_selector_and_semantic_constraints_intersect():
    session = Session([Element("same")])
    locator = MacOSLocator(xpath="//Button", predicate="enabled == true", label="Submit", controlType="Button")
    result = AppiumMac2Driver(session=session).assert_visible(MacOSAssertVisibleParams(locator=locator))
    assert result["status"] == "passed"
    assert session.calls[0][1].startswith("(//Button)[")
    assert "@label='Submit'" in session.calls[0][1]
    assert session.calls[1] == ("-ios predicate string", "enabled == true")


@pytest.mark.parametrize("method,params_type", [("click_on", MacOSClickOnParams), ("right_click_on", MacOSRightClickOnParams)])
def test_coordinates_cannot_override_missing_semantic_target(method, params_type, monkeypatch):
    driver = AppiumMac2Driver(session=Session())

    def forbidden(*args, **kwargs):
        pytest.fail("Must not click on missing target's supplied coordinates")

    monkeypatch.setattr(driver, "_perform_pointer_click", forbidden)
    result = getattr(driver, method)(params_type.model_validate({"locator": {"label": "missing"}, "point": {"x": 10, "y": 10}}))
    assert result["status"] == "failed"
    assert result["metadata"]["resolution_reason"] == "not_found"


def test_interactive_unlabelled_node_preserved():
    result = AppiumMac2Driver(session=Session(source='<Application><Wrapper><XCUIElementTypeButton enabled="true"/></Wrapper></Application>')).ui_snapshot(MacOSUiSnapshotParams())
    assert result["page_source"]["root"]["children"][0]["type"] == "XCUIElementTypeButton"


def test_ui_snapshot_schema_rejects_query():
    from fsq_agent.harnesses._macos import MacOSHarness

    with pytest.raises(ValidationError):
        MacOSUiSnapshotParams.model_validate({"query": {"text": "Save"}})
    schema = next(item for item in MacOSHarness(driver=AppiumMac2Driver()).action_space() if item.name == "ui_snapshot")
    assert "query" not in schema.params_json_schema["properties"]


@pytest.mark.parametrize("stage", ["visibility", "geometry", "click"])
def test_live_element_failures_keep_session_reason(stage):
    from selenium.common.exceptions import InvalidSessionIdException

    class BrokenElement(Element):
        def is_displayed(self):
            raise InvalidSessionIdException("private state")

        @property
        def rect(self):
            raise InvalidSessionIdException("private geometry")

        def click(self):
            raise InvalidSessionIdException("private click")

    driver = AppiumMac2Driver(session=Session([BrokenElement("one")]))
    method, params = {
        "visibility": (driver.assert_visible, MacOSAssertVisibleParams(target="one")),
        "geometry": (driver.right_click_on, MacOSRightClickOnParams(target="one")),
        "click": (driver.click_on, MacOSClickOnParams(target="one")),
    }[stage]
    with pytest.raises(ConfigurationError) as error:
        method(params)
    assert error.value.context["resolution_reason"] == "session_unavailable"
    assert "private" not in str(error.value)
