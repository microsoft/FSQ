# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import pytest

from fsq_agent.drivers.macos._appium_mac2 import AppiumMac2Driver
from fsq_agent.models import ConfigurationError, MacOSAssertVisibleParams, MacOSClickOnParams, MacOSElementQuery, MacOSLocator, MacOSRightClickOnParams, MacOSUiSnapshotParams


class Element:
    def __init__(self, identifier):
        self.id = identifier

    def is_displayed(self):
        return True


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


def test_query_ignores_element_type_names_and_preserves_full_locator():
    title = "Apply Men filter " + "x" * 70
    source = f'<Application><XCUIElementTypeButton label="Unrelated"/><Group><Link label="{title}" selected="true"/></Group></Application>'
    driver = AppiumMac2Driver(session=Session(source=source))
    result = driver.ui_snapshot(MacOSUiSnapshotParams.model_validate({"max_depth": 1, "query": {"text": "Men"}}))
    assert result["match_count"] == 1
    candidate = result["candidates"][0]
    assert candidate["locator"]["label"] == title
    assert candidate["text_truncated"] is True
    assert len(candidate["display"]["label"]) == 50
    assert "page_source" not in result


def test_query_missing_state_is_not_false_and_pagination_is_revision_bound():
    session = Session(source='<Application><Button label="shoe"/><Button label="shoe" visible="false"/></Application>')
    driver = AppiumMac2Driver(session=session)
    filtered = driver.ui_snapshot(MacOSUiSnapshotParams.model_validate({"query": {"text": "shoe", "visible": False}}))
    assert filtered["match_count"] == 1
    first = driver.ui_snapshot(MacOSUiSnapshotParams.model_validate({"query": {"text": "shoe", "limit": 1}}))
    assert first["next_offset"] == 1
    second = driver.ui_snapshot(MacOSUiSnapshotParams.model_validate({"query": {"text": "shoe", "offset": 1, "snapshot_revision": first["snapshot_revision"]}}))
    assert len(second["candidates"]) == 1
    session.page_source = "<Application/>"
    stale = driver.ui_snapshot(MacOSUiSnapshotParams.model_validate({"query": {"text": "shoe", "offset": 1, "snapshot_revision": first["snapshot_revision"]}}))
    assert stale["metadata"]["resolution_reason"] == "stale_snapshot"


def test_tree_clipping_explicit_and_query_does_not_invent_absent_button():
    source = '<Application><Button label="' + "x" * 60 + '"/></Application>'
    driver = AppiumMac2Driver(session=Session(source=source))
    tree = driver.ui_snapshot(MacOSUiSnapshotParams())
    node = tree["page_source"]["root"]["children"][0]
    assert node["truncated_attributes"] == ["label"]
    result = driver.ui_snapshot(MacOSUiSnapshotParams.model_validate({"query": {"text": "Add to cart"}}))
    assert result["match_count"] == 0
    assert result["coverage"] == "complete"
    assert result["absence_proves_invisibility"] is False


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


def test_query_semantics_and_button_recovery_from_depth_clipping():
    source = '<Application><Group label="wrapper"><Group label="nested"><Button label="Add to cart" enabled="true"/><Link label="Apply Men filter to narrow results"/><Link label="Apply adidas filter to narrow results"/></Group></Group></Application>'
    driver = AppiumMac2Driver(session=Session(source=source))
    tree = driver.ui_snapshot(MacOSUiSnapshotParams(max_depth=1))
    assert tree["page_source"]["root"]["children_truncated"] == 1
    result = driver.ui_snapshot(MacOSUiSnapshotParams(query=MacOSElementQuery(text="add to cart", match="exact", control_type="Button", enabled=True)))
    assert result["match_count"] == 1
    assert result["candidates"][0]["locator"]["label"] == "Add to cart"
    result = driver.ui_snapshot(MacOSUiSnapshotParams(query=MacOSElementQuery(text="Men")))
    assert result["match_count"] == 1
    result = driver.ui_snapshot(MacOSUiSnapshotParams(query=MacOSElementQuery(text="ADD", case_sensitive=True)))
    assert result["match_count"] == 0


@pytest.mark.parametrize(
    "source",
    ['<!DOCTYPE x [<!ENTITY a "xx">]><x>&a;</x>', "<bad", "<x>" * 130 + "</x>" * 130, "<x>" + "<Button/>" * 10001 + "</x>"],
    ids=["dtd", "malformed", "depth-limit", "node-limit"],
)
def test_query_invalid_or_excessive_source_is_incomplete(source):
    result = AppiumMac2Driver(session=Session(source=source)).ui_snapshot(MacOSUiSnapshotParams(query=MacOSElementQuery(text="button")))
    assert result["coverage"] == "incomplete"
    assert result["count_is_lower_bound"] is True
    assert result["candidates"] == []


def test_oversized_locator_is_unavailable_not_a_prefix():
    source = '<Application><Button label="' + "q" * 1025 + '"/></Application>'
    result = AppiumMac2Driver(session=Session(source=source)).ui_snapshot(MacOSUiSnapshotParams(query=MacOSElementQuery(text="q")))
    item = result["candidates"][0]
    assert item["locator"] is None
    assert item["locator_unavailable_fields"] == ["label"]


def test_response_bound_has_continuation():
    source = "<Application>" + "".join('<Button label="' + str(i) + "q" * 1000 + '"/>' for i in range(50)) + "</Application>"
    result = AppiumMac2Driver(session=Session(source=source)).ui_snapshot(MacOSUiSnapshotParams(query=MacOSElementQuery(text="q", limit=50)))
    assert result["match_count"] == 50
    assert result["response_truncated"] is True
    assert 0 < result["next_offset"] < 50


def test_interactive_unlabelled_node_preserved():
    result = AppiumMac2Driver(session=Session(source='<Application><Wrapper><XCUIElementTypeButton enabled="true"/></Wrapper></Application>')).ui_snapshot(MacOSUiSnapshotParams())
    assert result["page_source"]["root"]["children"][0]["type"] == "XCUIElementTypeButton"


def test_query_continuation_validation_and_normal_registry_exposure():
    from pydantic import ValidationError

    from fsq_agent.harnesses._macos import MacOSHarness

    with pytest.raises(ValidationError):
        MacOSElementQuery(offset=1)
    schema = next(item for item in MacOSHarness(driver=AppiumMac2Driver()).action_space() if item.name == "ui_snapshot")
    assert "query" in schema.params_json_schema["properties"]


def test_type_and_state_queries_return_unlabelled_controls():
    driver = AppiumMac2Driver(session=Session(source='<Application><Button enabled="true" visible="true"/></Application>'))
    for query in (MacOSElementQuery(control_type="Button"), MacOSElementQuery(enabled=True), MacOSElementQuery(control_type="Button", visible=True)):
        result = driver.ui_snapshot(MacOSUiSnapshotParams(query=query))
        assert result["match_count"] == 1
        assert result["candidates"][0]["type"] == "Button"


def test_oversized_type_does_not_stall_pagination():
    tag = "X" * 40000
    driver = AppiumMac2Driver(session=Session(source=f'<{tag} label="Match"/>'))
    result = driver.ui_snapshot(MacOSUiSnapshotParams(query=MacOSElementQuery(text="Match")))
    assert len(result["candidates"]) == 1
    assert result["next_offset"] is None
    assert "controlType" not in result["candidates"][0]["locator"]
    assert "controlType" in result["candidates"][0]["locator_unavailable_fields"]


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
