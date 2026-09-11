# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import json
from typing import get_type_hints

import pytest
from pydantic import ValidationError

from fsq_agent import models
from fsq_agent.agent_engine._google_gemini_schema import google_schema
from fsq_agent.agent_engine._schema import ensure_strict_json_schema
from fsq_agent.core.interfaces import WebDriverInterface


def locator(*steps: dict, page: str = "main") -> dict:
    return {"page": page, "steps": list(steps or ({"kind": "role", "role": "button", "name": "Save"},))}


def test_list_first_after_filter_preserves_order_and_exact_names() -> None:
    target = locator(
        {"kind": "role", "role": "list", "name": "Search results"},
        {"kind": "role", "role": "listitem"},
        {
            "kind": "filter",
            "has": {
                "steps": [
                    {"kind": "role", "role": "button", "name": "Add to cart", "disabled": False},
                    {"kind": "filter", "visible": True},
                ]
            },
        },
        {"kind": "first"},
        {"kind": "role", "role": "button", "name": "Add to cart"},
    )
    parsed = models.WebClickOnParams(target=target)
    dumped = parsed.model_dump(mode="json", exclude_none=True)
    assert [step["kind"] for step in dumped["target"]["steps"]] == ["role", "role", "filter", "first", "role"]
    assert dumped["target"]["steps"][0]["exact"] is True
    assert dumped["target"]["steps"][2]["has"]["steps"][1]["visible"] is True
    assert models.WebClickOnParams.model_validate(dumped) == parsed


def test_nameless_combobox_and_native_select_index_zero() -> None:
    params = models.WebSelectOptionParams(
        target=locator({"kind": "role", "role": "form", "name": "Order"}, {"kind": "role", "role": "combobox", "name": ""}),
        selection={"kind": "index", "indices": [0]},
    )
    assert params.selection.indices == [0]
    assert params.target.steps[-1].name == ""
    assert params.target.steps[-1].exact is True


@pytest.mark.parametrize(
    "target",
    [
        "Save",
        {"ref": "e42"},
        {"page": "main", "steps": []},
        {"steps": [{"kind": "role", "role": "button"}]},
        locator({"kind": "first"}),
        locator({"kind": "filter", "visible": True}),
        locator({"kind": "role", "role": "button"}, {"kind": "enter_frame"}),
        locator({"kind": "role", "role": "button"}, {"kind": "nth", "index": -1}),
        locator({"kind": "role", "role": "button"}, {"kind": "nth", "index": True}),
        locator({"kind": "role", "role": "button"}, {"kind": "filter"}),
        locator({"kind": "role", "role": "button", "exact": "true"}),
        locator({"kind": "role", "role": "heading", "checked": False}),
        locator({"kind": "role", "role": "heading", "disabled": False}),
        locator({"kind": "role", "role": "not-a-role"}),
        locator({"kind": "css", "selector": "  "}),
        locator({"kind": "playwright", "selector": "aria-ref=e42"}),
        locator({"kind": "playwright", "selector": "css=main >> aria-ref=e42"}),
        locator({"kind": "css", "selector": "button >> aria-ref=e42"}),
        locator({"kind": "playwright", "selector": "*aria-ref=e42"}),
        locator({"kind": "playwright", "selector": 'internal:has="aria-ref=e42"'}),
        locator({"kind": "playwright", "selector": "javascript=alert(1)"}),
        locator({"kind": "playwright", "selector": "page.locator('button').click()"}),
        locator({"kind": "playwright", "selector": "css=iframe >> internal:control=enter-frame >> css=button"}),
        locator({"kind": "role", "role": "button", "ref": "e42"}),
        locator({"kind": "role", "role": "list"}, {"kind": "filter", "has": {"steps": [{"kind": "enter_frame"}]}}),
        locator({"kind": "role", "role": "list"}, {"kind": "filter", "has": {"steps": [{"kind": "xpath", "selector": "//button"}]}}),
        locator({"kind": "role", "role": "list"}, {"kind": "filter", "has": {"steps": [{"kind": "xpath", "selector": "(//button)[1]"}]}}),
        locator(
            {"kind": "role", "role": "list"},
            {"kind": "filter", "has": {"steps": [{"kind": "role", "role": "listitem"}, {"kind": "filter", "has": {"steps": [{"kind": "role", "role": "button"}]}}]}},
        ),
    ],
)
def test_invalid_locators_are_rejected_before_execution(target: object) -> None:
    with pytest.raises(ValidationError):
        models.WebClickOnParams(target=target)


@pytest.mark.parametrize(
    "step",
    [
        {"kind": "text", "text": "Reference e42 and ref=ordinary text"},
        {"kind": "role", "role": "link", "name": "https://example.test/?ref=home"},
        {"kind": "css", "selector": '[href*="?ref=home"]'},
        {"kind": "playwright", "selector": 'internal:text="aria-ref=e42 is documentation"i'},
        {"kind": "playwright", "selector": 'css=a[href="https://example.test/?ref=home"]'},
        {"kind": "playwright", "selector": 'internal:role=link[name="reference >> aria-ref=e42"s]'},
    ],
)
def test_ordinary_reference_text_and_urls_are_not_session_refs(step: dict) -> None:
    assert models.WebLocator.model_validate(locator(step)).steps[0].kind == step["kind"]


@pytest.mark.parametrize(
    "name,payload",
    [
        ("WebStartBrowserParams", {"page": "main"}),
        ("WebCloseBrowserParams", {"timeout_ms": 10}),
        ("WebNavigateToParams", {"url": "https://example.test/"}),
        ("WebNavigateToParams", {"page": "main", "url": " "}),
        ("WebClickOnParams", {"target": locator(), "locator": {"css": "button"}}),
        ("WebClickOnParams", {"target": locator(), "double": True}),
        ("WebClickOnParams", {"target": locator(), "timeout_ms": 0}),
        ("WebClickOnParams", {"target": locator(), "timeout_ms": 60001}),
        ("WebClickOnParams", {"target": locator(), "click_count": True}),
        ("WebTypeTextParams", {"target": locator(), "text": "abc", "clear": True}),
        ("WebSetCheckedParams", {"target": locator(), "checked": "false"}),
        ("WebSelectOptionParams", {"target": locator(), "selection": {"kind": "index", "indices": [-1]}}),
        ("WebSelectOptionParams", {"target": locator(), "selection": {"kind": "index", "indices": [True]}}),
        ("WebSelectOptionParams", {"target": locator(), "selection": {"kind": "value", "values": []}}),
        ("WebSelectOptionParams", {"target": locator(), "selection": {"kind": "value", "values": ["a"], "labels": ["A"]}}),
        ("WebPressKeyParams", {"key": "Enter"}),
        ("WebPressKeyParams", {"scope": {"kind": "page", "page": "main"}, "key": "SomethingInvented"}),
        ("WebWaitForParams", {"timeout_ms": 1000}),
        ("WebWaitForParams", {"condition": {"kind": "element", "target": locator(), "state": "sleep"}}),
        ("WebWaitForParams", {"condition": {"kind": "url", "page": "main", "url": " ", "exact": True}}),
        ("WebAssertStateParams", {"target": locator(), "state": {}}),
        ("WebAssertStateParams", {"target": locator(), "state": {"visible": "false"}}),
        ("WebAssertTextParams", {"target": locator(), "text": {"contains": "x", "equals": "x"}}),
        ("WebScrollParams", {"scope": {"kind": "page", "page": "main"}, "delta_x": 0, "delta_y": 0}),
        ("WebUiSnapshotParams", {"scope": {"kind": "page", "page": "main"}, "max_chars": 12001}),
        ("WebUiSnapshotParams", {"scope": {"kind": "page", "page": "main"}, "continuation": {"cursor": "next"}}),
    ],
)
def test_invalid_operation_combinations_have_validation_diagnostics(name: str, payload: dict) -> None:
    with pytest.raises(ValidationError) as failure:
        getattr(models, name).model_validate(payload)
    diagnostic = failure.value.errors()[0]
    assert diagnostic["loc"] or diagnostic["type"] == "value_error"
    assert diagnostic["msg"]


@pytest.mark.parametrize(
    "selection",
    [
        {"kind": "value", "values": ["", "second"]},
        {"kind": "label", "labels": ["First", "Second"]},
        {"kind": "index", "indices": [0, 2]},
    ],
)
def test_multiple_native_selection_modes(selection: dict) -> None:
    assert models.WebSelectOptionParams(target=locator(), selection=selection).selection.kind == selection["kind"]


def test_event_expectation_keeps_prompt_secret_as_a_typed_value() -> None:
    params = models.WebPressKeyParams(
        key="Control+Enter",
        scope={"kind": "element", "target": locator()},
        expect={"kind": "dialog", "dialog_type": "prompt", "action": "accept", "prompt_text": {"text": "approval_code", "textType": "runtimeSecret"}},
    )
    assert params.expect.prompt_text.textType == "runtimeSecret"
    assert params.expect.prompt_text.text == "approval_code"
    with pytest.raises(ValidationError):
        models.WebClickOnParams(
            target=locator(),
            expect={"kind": "dialog", "dialog_type": "alert", "action": "accept", "prompt_text": {"text": "not a prompt"}},
        )


def test_upload_is_not_exposed_by_web_contracts() -> None:
    assert not hasattr(models, "WebUploadFilesParams")
    assert not hasattr(WebDriverInterface, "upload_files")
    assert not hasattr(WebDriverInterface, "configure_runtime")
    assert all(definition.driver_method != "upload_files" for definition in models.WEB_ACTION_DEFINITIONS)


def test_observations_separate_compact_view_from_complete_semantic_source() -> None:
    payload = {
        "observation_id": "snapshot-1",
        "page": {"page": "main", "url": "https://example.test/", "title": "Order"},
        "view": {
            "elements": [
                {
                    "role": "combobox",
                    "name": "",
                    "locator": locator({"kind": "role", "role": "combobox", "name": ""}),
                    "options": [{"label": "First", "value": "", "index": 0, "disabled": False, "selected": True}],
                }
            ]
        },
        "coverage": {"semantic": "partial", "locators": "complete", "observed_count": 1, "total_count": None, "omissions": ["Other regions omitted."]},
        "full_source": {"semantic_text": "full semantic evidence", "coverage": {"semantic": "complete", "locators": "partial"}},
    }
    observation = models.WebObservation.model_validate(payload)
    assert observation.view.elements[0].options[0].index == 0
    assert observation.coverage.total_count is None
    assert observation.full_source.coverage.locators == "partial"
    assert models.WebObservation.model_validate_json(observation.model_dump_json()) == observation
    assert observation.observed_at is None
    payload["view"]["elements"][0].pop("locator")
    with pytest.raises(ValidationError):
        models.WebObservation.model_validate(payload)


def test_all_catalog_entries_have_typed_driver_methods_and_safe_schema() -> None:
    required_methods = {
        "start_browser",
        "close_browser",
        "navigate_to",
        "navigate_back",
        "navigate_forward",
        "reload_page",
        "click_on",
        "hover_on",
        "drag_to",
        "scroll",
        "scroll_into_view",
        "fill_text",
        "type_text",
        "set_checked",
        "select_option",
        "press_key",
        "wait_for",
        "take_screenshot",
        "ui_snapshot",
        "find_elements",
        "inspect_element",
        "assert_visible",
        "assert_not_visible",
        "assert_text",
        "assert_state",
        "assert_value",
        "assert_with_ai",
        "list_pages",
        "open_page",
        "activate_page",
        "close_page",
    }
    assert {definition.driver_method for definition in models.WEB_ACTION_DEFINITIONS} == required_methods
    for definition in models.WEB_ACTION_DEFINITIONS:
        assert definition.owner == "driver"
        assert models.WEB_ACTION_DEFINITIONS_BY_NAME[definition.fsq_action_name] is definition
        assert get_type_hints(getattr(WebDriverInterface, definition.driver_method))["params"] is definition.params_model
        schema = definition.params_model.model_json_schema()
        assert schema["additionalProperties"] is False
        encoded = json.dumps(schema)
        assert '"ref":' not in encoded
        assert '"oneOf":' not in encoded
        assert '"discriminator":' not in encoded
        assert ensure_strict_json_schema(schema)
        assert google_schema(schema)


def test_lifecycle_stays_fieldless_and_web_models_are_extracted() -> None:
    assert models.WebStartBrowserParams.model_fields == {}
    assert models.WebCloseBrowserParams.model_fields == {}
    assert models.WebLocator.__module__ == "fsq_agent.models._web"
    assert models.WebTypeTextParams(target=locator(), text="x").textType == "literal"
    assert models.WebFillTextParams(target=locator(), text="").text == ""


@pytest.mark.parametrize(
    "name,payload",
    [
        ("WebStartBrowserParams", {}),
        ("WebCloseBrowserParams", {}),
        ("WebNavigateToParams", {"page": "main", "url": "https://example.test/?ref=home"}),
        ("WebNavigateBackParams", {"page": "main", "wait_until": "domcontentloaded"}),
        ("WebNavigateForwardParams", {"page": "main"}),
        ("WebReloadPageParams", {"page": "main"}),
        ("WebClickOnParams", {"target": locator(), "click_count": 2, "modifiers": ["Shift"]}),
        ("WebHoverOnParams", {"target": locator()}),
        ("WebDragToParams", {"source": locator(), "destination": locator({"kind": "role", "role": "region", "name": "Drop here"})}),
        ("WebScrollParams", {"scope": {"kind": "page", "page": "main"}, "delta_y": 500}),
        ("WebScrollIntoViewParams", {"target": locator()}),
        ("WebFillTextParams", {"target": locator(), "text": "", "textType": "literal"}),
        ("WebTypeTextParams", {"target": locator(), "text": "password_name", "textType": "runtimeSecret", "delay_ms": 20}),
        ("WebSetCheckedParams", {"target": locator({"kind": "role", "role": "checkbox"}), "checked": False}),
        ("WebSelectOptionParams", {"target": locator({"kind": "role", "role": "combobox"}), "selection": {"kind": "index", "indices": [0]}}),
        ("WebPressKeyParams", {"scope": {"kind": "element", "target": locator()}, "key": "Enter", "expect": {"kind": "popup", "page": "receipt"}}),
        ("WebWaitForParams", {"condition": {"kind": "element", "target": locator(), "state": "detached"}}),
        ("WebWaitForParams", {"condition": {"kind": "text", "scope": {"kind": "page", "page": "main"}, "text": {"kind": "equals", "value": "Loading"}, "present": False}}),
        ("WebWaitForParams", {"condition": {"kind": "url", "page": "main", "url": "/success", "exact": False}}),
        ("WebWaitForParams", {"condition": {"kind": "load_state", "page": "main", "state": "domcontentloaded"}}),
        ("WebTakeScreenshotParams", {"page": "main", "full_page": True}),
        ("WebUiSnapshotParams", {"scope": {"kind": "page", "page": "main"}, "view": "full"}),
        ("WebUiSnapshotParams", {"scope": {"kind": "element", "target": locator()}, "view": "scoped", "max_chars": 4000}),
        ("WebFindElementsParams", {"target": locator({"kind": "role", "role": "button"})}),
        ("WebInspectElementParams", {"target": locator({"kind": "role", "role": "combobox", "name": ""}), "max_options": 25}),
        ("WebAssertVisibleParams", {"target": locator()}),
        ("WebAssertNotVisibleParams", {"target": locator()}),
        ("WebAssertTextParams", {"target": locator(), "text": {"kind": "contains", "value": "Done"}}),
        ("WebAssertStateParams", {"target": locator(), "state": {"enabled": True, "focused": False}}),
        ("WebAssertValueParams", {"target": locator(), "value": ["", "second"]}),
        ("WebAssertWithAIParams", {"prompt": "The receipt is readable."}),
        ("WebListPagesParams", {}),
        ("WebOpenPageParams", {"page": "help"}),
        ("WebActivatePageParams", {"page": "help"}),
        ("WebClosePageParams", {"page": "help"}),
    ],
)
def test_operation_examples_validate_and_round_trip(name: str, payload: dict) -> None:
    model = getattr(models, name)
    params = model.model_validate(payload)
    assert model.model_validate_json(params.model_dump_json()) == params


def test_json_schema_keeps_disjoint_required_tags_without_weakening_union() -> None:
    schema = models.WebSelectOptionParams.model_json_schema()
    variants = schema["properties"]["selection"]["anyOf"]
    tags = []
    for variant in variants:
        resolved = schema["$defs"][variant["$ref"].split("/")[-1]]
        assert "kind" in resolved["required"]
        assert resolved["additionalProperties"] is False
        tags.append(resolved["properties"]["kind"]["const"])
    assert tags == ["value", "label", "index"]


def test_invalid_ref_diagnostic_identifies_locator_field_path() -> None:
    with pytest.raises(ValidationError) as failure:
        models.WebClickOnParams(target=locator({"kind": "role", "role": "button", "ref": "e42"}))
    assert failure.value.errors()[0]["loc"] == ("target", "steps", 0, "role", "ref")


def test_observation_rejects_handles_and_cross_observation_continuations() -> None:
    payload = {
        "observation_id": "one",
        "page": {"page": "main", "url": "about:blank", "title": ""},
        "view": {},
        "coverage": {"semantic": "partial", "locators": "partial"},
    }
    with pytest.raises(ValidationError):
        models.WebObservation.model_validate({**payload, "handle": object()})
    with pytest.raises(ValidationError):
        models.WebObservation.model_validate({**payload, "continuation": {"observation_id": "other", "cursor": "next"}})


def test_relative_xpath_keeps_quoted_application_text_literal() -> None:
    query = models.WebRelativeQuery(steps=[{"kind": "xpath", "selector": './/a[@title="(/reference)"]'}])
    assert query.steps[0].selector == './/a[@title="(/reference)"]'
