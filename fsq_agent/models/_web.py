# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Annotated, Literal, TypeAlias
from urllib.parse import unquote

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, GetJsonSchemaHandler, ValidationError, ValidationInfo, field_validator, model_validator

from ._core import TEXT_TYPE_DESCRIPTION, ExecutableStepKind, TextSourceType

if TYPE_CHECKING:
    from collections.abc import Iterable

    from pydantic.json_schema import JsonSchemaValue

_TARGET_DESCRIPTION = (
    "Complete replayable locator from an observation/query or an authored rule; never a description, ref, observation ID, or session-local handle. Ordered filters and positions run again on replay."
)
_EXACT_DESCRIPTION = "Exact string matching by default. Set false explicitly for substring matching, which may increase the match count."
_TIMEOUT_DESCRIPTION = "Maximum readiness/action wait in milliseconds, not a sleep or retry count; default 10000, range 1..60000."
_PAGE_DESCRIPTION = "Declared owned-page alias, such as main or an alias introduced by open_page/popup; never a tab index. No page is started implicitly."
_EVENT_DESCRIPTION = "Install the expected-event listener before this trigger. Timeout may follow completed effects; inspect state instead of blindly repeating the trigger."
_POSITION_DESCRIPTION = "Select from the collection at this point on every replay. Place after the intended filters; out-of-range selection fails, never repairs the path."

WebPageAlias: TypeAlias = Annotated[str, Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")]
WebTimeout: TypeAlias = Annotated[int, Field(ge=1, le=60000)]
WebMouseButton: TypeAlias = Literal["left", "right", "middle"]
WebModifier: TypeAlias = Literal["Alt", "Control", "ControlOrMeta", "Meta", "Shift"]
WebWaitUntil: TypeAlias = Literal["commit", "domcontentloaded", "load", "networkidle"]
WebWaitForState: TypeAlias = Literal["visible", "hidden", "attached", "detached"]
WebRole: TypeAlias = Literal[
    "alert",
    "alertdialog",
    "application",
    "article",
    "banner",
    "blockquote",
    "button",
    "caption",
    "cell",
    "checkbox",
    "code",
    "columnheader",
    "combobox",
    "complementary",
    "contentinfo",
    "definition",
    "deletion",
    "dialog",
    "directory",
    "document",
    "emphasis",
    "feed",
    "figure",
    "form",
    "generic",
    "grid",
    "gridcell",
    "group",
    "heading",
    "img",
    "insertion",
    "link",
    "list",
    "listbox",
    "listitem",
    "log",
    "main",
    "marquee",
    "math",
    "meter",
    "menu",
    "menubar",
    "menuitem",
    "menuitemcheckbox",
    "menuitemradio",
    "navigation",
    "none",
    "note",
    "option",
    "paragraph",
    "presentation",
    "progressbar",
    "radio",
    "radiogroup",
    "region",
    "row",
    "rowgroup",
    "rowheader",
    "scrollbar",
    "search",
    "searchbox",
    "separator",
    "slider",
    "spinbutton",
    "status",
    "strong",
    "subscript",
    "superscript",
    "switch",
    "tab",
    "table",
    "tablist",
    "tabpanel",
    "term",
    "textbox",
    "time",
    "timer",
    "toolbar",
    "tooltip",
    "tree",
    "treegrid",
    "treeitem",
]


def _tagged_schema(schema: JsonSchemaValue) -> None:
    """Disjoint required literal tags make anyOf equivalent to discriminator oneOf."""
    if "discriminator" in schema and "oneOf" in schema:
        schema["anyOf"] = schema.pop("oneOf")
        schema.pop("discriminator")
    for value in schema.values():
        if isinstance(value, dict):
            _tagged_schema(value)
        elif isinstance(value, list):
            for child in value:
                if isinstance(child, dict):
                    _tagged_schema(child)


class _WebModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    @model_validator(mode="after")
    def _require_safe_replay(self, info: ValidationInfo) -> _WebModel:
        context = info.context
        if isinstance(context, dict) and context.get("params_type") is type(self):
            safe_value = context.get("safe_value")
            params = self.model_dump(mode="json", exclude_none=True)
            if callable(safe_value) and safe_value(params) != params:
                raise ValueError("Replay parameters cannot be preserved safely; use an unresolved runtime-secret reference for private text.")
        return self

    @classmethod
    def __get_pydantic_json_schema__(cls, core_schema, handler: GetJsonSchemaHandler) -> JsonSchemaValue:
        schema = handler(core_schema)
        _tagged_schema(schema)
        return schema


def _nonempty(value: str) -> str:
    if not value.strip():
        raise ValueError("must contain non-whitespace text")
    return value


def _selector_parts(value: str) -> list[str]:
    parts: list[str] = []
    start = 0
    quote = ""
    escaped = False
    index = 0
    while index < len(value):
        character = value[index]
        if escaped:
            escaped = False
        elif character == "\\":
            escaped = True
        elif quote:
            if character == quote:
                quote = ""
        elif character in "\"'`":
            quote = character
        elif value[index : index + 2] == ">>":
            parts.append(value[start:index].strip())
            index += 1
            start = index + 1
        index += 1
    parts.append(value[start:].strip())
    return parts


def _validate_native_selector(value: str, *, relative: bool = False, depth: int = 0) -> None:
    if depth > 8:
        raise ValueError("native selector nesting exceeds the finite query limit")
    allowed = {
        "css",
        "css:light",
        "xpath",
        "xpath:light",
        "id",
        "id:light",
        "text",
        "text:light",
        "nth",
        "data-testid",
        "data-test-id",
        "data-test",
        "internal:role",
        "internal:text",
        "internal:label",
        "internal:attr",
        "internal:testid",
        "internal:has-text",
        "internal:has-not-text",
        "internal:has",
        "internal:has-not",
        "internal:and",
        "internal:or",
        "visible",
    }
    for part in _selector_parts(value):
        _nonempty(part)
        match = re.match(r"^\*?([A-Za-z_][\w:-]*)\s*=", part)
        if match is None:
            if part.startswith(("javascript:", "python:", "function ", "async ", "(()", "()")):
                raise ValueError("selector data cannot contain executable code")
            if relative:
                _validate_relative_xpath(part)
            continue
        engine = match.group(1)
        if engine == "aria-ref":
            raise ValueError("session-local aria-ref selectors are unsupported; use a complete replayable locator")
        if engine not in allowed:
            raise ValueError("unsupported native selector engine; code and implicit frame entry are forbidden")
        body = part[match.end() :]
        _nonempty(body)
        if engine in {"internal:has", "internal:has-not", "internal:and", "internal:or"}:
            try:
                nested = json.loads(body)
            except (ValueError, TypeError):
                raise ValueError("nested native selector must contain a serialized selector string") from None
            if not isinstance(nested, str):
                raise ValueError("nested native selector must contain a selector string")
            _validate_native_selector(nested, relative=True, depth=depth + 1)
        if relative and engine.startswith("xpath"):
            _validate_relative_xpath(body)


def _validate_relative_xpath(value: str) -> None:
    expression = re.sub(r"'[^']*'|\"[^\"]*\"", "", value)
    if re.search(r"(?:^|[|(])\s*/|(?:ancestor|parent|following|preceding)(?:-sibling)?\s*::|(?:^|[/(\s])\.\.(?:[/)\s]|$)", expression):
        raise ValueError("relative descendant XPath cannot escape its candidate")


class WebRoleStep(_WebModel):
    """Query by a supported ARIA role and optional conjunctive accessible-name/state predicates."""

    kind: Literal["role"]
    role: WebRole = Field(description="ARIA role supported by Playwright. A nameless control can use name='' inside a precise container scope.")
    name: str | None = Field(default=None, description="Accessible name; omit to match any name, or use an empty string to match unnamed controls exactly.")
    exact: bool = Field(default=True, description=_EXACT_DESCRIPTION)
    disabled: bool | None = Field(default=None, description="Constrain effective disabled state, including inherited ARIA disabled semantics.")
    checked: bool | None = Field(default=None, description="Checked state for checkbox, radio, switch, or checkable menu roles only.")
    selected: bool | None = Field(default=None, description="Selected state for option/tab/row/grid selection roles only.")
    expanded: bool | None = Field(default=None, description="Expanded state for a role supporting expansion; omit when the control has no expansion state.")
    pressed: bool | None = Field(default=None, description="Pressed state for toggle buttons only.")
    level: int | None = Field(default=None, ge=1, description="Positive heading/listitem/row/treeitem hierarchy level, not a DOM depth limit.")
    include_hidden: bool = Field(default=False, description="False excludes ARIA-hidden matches by default; explicitly include hidden nodes for hidden-state queries.")

    @model_validator(mode="after")
    def _validate_role_states(self) -> WebRoleStep:
        supported = {
            "disabled": {
                "button",
                "checkbox",
                "columnheader",
                "combobox",
                "gridcell",
                "group",
                "link",
                "listbox",
                "menu",
                "menubar",
                "menuitem",
                "menuitemcheckbox",
                "menuitemradio",
                "option",
                "radio",
                "radiogroup",
                "row",
                "rowheader",
                "scrollbar",
                "searchbox",
                "separator",
                "slider",
                "spinbutton",
                "switch",
                "tab",
                "tablist",
                "textbox",
                "toolbar",
                "tree",
                "treegrid",
                "treeitem",
            },
            "checked": {"checkbox", "menuitemcheckbox", "menuitemradio", "radio", "switch"},
            "selected": {"gridcell", "option", "row", "tab", "rowheader", "columnheader", "treeitem"},
            "expanded": {"application", "button", "checkbox", "columnheader", "combobox", "gridcell", "link", "listbox", "menuitem", "row", "rowheader", "tab", "treeitem"},
            "pressed": {"button"},
            "level": {"heading", "listitem", "row", "treeitem"},
        }
        for field, roles in supported.items():
            if getattr(self, field) is not None and self.role not in roles:
                raise ValueError(f"{field} is not supported for role {self.role}")
        return self


class WebTextStep(_WebModel):
    """Query actual text, label, placeholder, test-id, alt text, or title; never parse a display synopsis."""

    kind: Literal["text", "label", "placeholder", "test_id", "alt", "title"]
    text: str = Field(description="Complete matching string, preserved without clipping or inferred prefix matching. Test IDs always match exactly.")
    exact: bool = Field(default=True, description=_EXACT_DESCRIPTION)

    @field_validator("text")
    @classmethod
    def _text_required(cls, value: str) -> str:
        return _nonempty(value)

    @model_validator(mode="after")
    def _test_id_is_exact(self) -> WebTextStep:
        if self.kind == "test_id" and not self.exact:
            raise ValueError("test_id only supports exact matching")
        return self


class WebSelectorStep(_WebModel):
    """Declarative CSS/XPath or a normalized Playwright selector, never JavaScript or generated code."""

    kind: Literal["css", "xpath", "playwright"]
    selector: str = Field(description="Complete selector data. Native selectors may use built-in engines only; aria-ref and implicit frame entry are forbidden.")

    @field_validator("selector")
    @classmethod
    def _selector_required(cls, value: str) -> str:
        return _nonempty(value)

    @model_validator(mode="after")
    def _safe_selector(self) -> WebSelectorStep:
        if "\x00" in self.selector:
            raise ValueError("selectors cannot contain NUL")
        for part in _selector_parts(self.selector):
            if re.match(r"^\*?aria-ref\s*=", part):
                raise ValueError("session-local aria-ref selectors are unsupported; use a complete replayable locator")
        if re.match(r"^\s*(?:page|document|window|locator)\.[\w.]+\s*\(", self.selector):
            raise ValueError("selector data cannot contain executable browser code")
        if self.kind == "playwright":
            _validate_native_selector(self.selector)
        elif len(_selector_parts(self.selector)) != 1:
            raise ValueError("CSS and XPath steps contain one selector only; use explicit locator steps for chaining or frame entry")
        return self


class WebTextAssertion(_WebModel):
    """One exact-equality or substring predicate; competing modes cannot be supplied together."""

    kind: Literal["equals", "contains"] = Field(description="equals compares complete normalized text; contains requires the specified substring.")
    value: str = Field(description="Unclipped expected text. Empty equals asserts empty text; empty contains is rejected as ineffective.")

    @model_validator(mode="after")
    def _effective_predicate(self) -> WebTextAssertion:
        if self.kind == "contains" and not self.value:
            raise ValueError("contains requires nonempty text")
        return self


class WebRelativeFilterStep(_WebModel):
    """Conjunctive visibility/text filtering inside a finite descendant query; no further has or frames."""

    kind: Literal["filter"]
    visible: bool | None = Field(default=None, description="Filter the current collection by actual visibility; false selects hidden matches.")
    text: WebTextAssertion | None = Field(default=None, description="Conjunctive candidate text predicate applied before any following position step.")

    @model_validator(mode="after")
    def _effective_filter(self) -> WebRelativeFilterStep:
        if self.visible is None and self.text is None:
            raise ValueError("filter requires visible or text")
        return self


class WebFirstStep(_WebModel):
    """Explicit first-match rule, evaluated after all preceding filters."""

    kind: Literal["first"] = Field(description=_POSITION_DESCRIPTION)


class WebLastStep(_WebModel):
    """Explicit last-match rule, evaluated after all preceding filters."""

    kind: Literal["last"] = Field(description=_POSITION_DESCRIPTION)


class WebNthStep(_WebModel):
    """Explicit zero-based positional rule, not the identity of the element seen during recording."""

    kind: Literal["nth"]
    index: int = Field(ge=0, description="Zero-based match index. " + _POSITION_DESCRIPTION)


WebRelativeStep: TypeAlias = Annotated[
    WebRoleStep | WebTextStep | WebSelectorStep | WebRelativeFilterStep | WebFirstStep | WebLastStep | WebNthStep,
    Field(discriminator="kind"),
]


class WebRelativeQuery(_WebModel):
    """Finite query relative to each has-filter candidate. No page, nested has, or frame crossing."""

    steps: list[WebRelativeStep] = Field(min_length=1, max_length=32, description="Ordered relative selectors and conjunctive filters; starts with a selector and never enters or escapes a frame.")

    @field_validator("steps")
    @classmethod
    def _relative_path(cls, steps: list[WebRelativeStep]) -> list[WebRelativeStep]:
        _validate_path(steps)
        for step in steps:
            if isinstance(step, WebSelectorStep):
                if step.kind == "xpath":
                    _validate_relative_xpath(step.selector)
                elif step.kind == "playwright":
                    _validate_native_selector(step.selector, relative=True)
        return steps


class WebFilterStep(_WebModel):
    """Apply every supplied predicate together, before a later explicit first/last/nth selection."""

    kind: Literal["filter"]
    visible: bool | None = Field(default=None, description="Conjunctive current-candidate visibility predicate; false means hidden rather than unspecified.")
    text: WebTextAssertion | None = Field(default=None, description="Conjunctive exact/contains text predicate against the current candidate.")
    has: WebRelativeQuery | None = Field(default=None, description="Require a matching descendant relative to each candidate. Finite query; cannot cross a frame or contain another has.")

    @model_validator(mode="after")
    def _effective_filter(self) -> WebFilterStep:
        if self.visible is None and self.text is None and self.has is None:
            raise ValueError("filter requires visible, text, or has")
        return self


class WebEnterFrameStep(_WebModel):
    """Enter the uniquely resolved iframe from the preceding path and continue with an inner selector."""

    kind: Literal["enter_frame"] = Field(description="Requires one iframe at runtime; the next step must be an element selector. Never accepts backend frame IDs.")


WebLocatorStep: TypeAlias = Annotated[
    WebRoleStep | WebTextStep | WebSelectorStep | WebFilterStep | WebFirstStep | WebLastStep | WebNthStep | WebEnterFrameStep,
    Field(discriminator="kind"),
]


def _validate_path(steps: list) -> None:
    selectors = (WebRoleStep, WebTextStep, WebSelectorStep)
    if steps[0].kind not in {"role", "text", "label", "placeholder", "test_id", "alt", "title", "css", "xpath", "playwright"}:
        raise ValueError("locator path must start with an element selector")
    for index, step in enumerate(steps):
        if isinstance(step, WebEnterFrameStep) and (index + 1 == len(steps) or not isinstance(steps[index + 1], selectors)):
            raise ValueError("enter_frame must be followed by an element selector")


class WebLocator(_WebModel):
    """Self-contained page and ordered replay locator. No implicit root, text target, locator bag, or ref."""

    page: WebPageAlias = Field(description=_PAGE_DESCRIPTION)
    steps: list[WebLocatorStep] = Field(min_length=1, max_length=64, description="Ordered selector/filter/position/frame path. Preserve authored order verbatim; no implicit first-match repair.")

    @field_validator("steps")
    @classmethod
    def _valid_path(cls, steps: list[WebLocatorStep]) -> list[WebLocatorStep]:
        _validate_path(steps)
        return steps

    @classmethod
    def preserve_for_redaction(cls, value: dict, secret_values: Iterable[str]) -> tuple[bool, dict | None]:
        """Recognize complete locator data; omit private locators, never rewrite them."""
        if set(value) != {"page", "steps"}:
            return False, None
        try:
            cls.model_validate(value)
        except ValidationError:
            return False, None
        pending = list(value.values())
        texts = []
        while pending:
            item = pending.pop()
            if isinstance(item, dict):
                pending.extend(item.values())
            elif isinstance(item, list):
                pending.extend(item)
            elif isinstance(item, str):
                texts.extend((item, unquote(item)))
        for secret in secret_values:
            if not secret:
                continue
            encoded = secret
            for _ in range(12):
                if any(encoded in text for text in texts):
                    return True, None
                encoded = json.dumps(encoded, ensure_ascii=False)[1:-1]
        return True, value

    @staticmethod
    def finish_redaction(original: dict, safe: dict) -> dict:
        if isinstance(original.get("locator"), dict) and safe.get("locator") is None:
            safe.pop("locator", None)
            safe["locator_unavailable_reason"] = "configured_private_value_in_locator"
            safe["quality"] = "unavailable"
        view, coverage = safe.get("view"), safe.get("coverage")
        if isinstance(view, dict) and isinstance(coverage, dict):
            entries = [item for field in ("elements", "regions") if isinstance(view.get(field), list) for item in view[field] if isinstance(item, dict)]
            if any(item.get("locator_unavailable_reason") == "configured_private_value_in_locator" for item in entries):
                coverage["locators"] = "partial" if any(item.get("locator") for item in entries) else "unavailable"
        return safe


class WebPageScope(_WebModel):
    """Operate explicitly on an existing declared page."""

    kind: Literal["page"]
    page: WebPageAlias = Field(description=_PAGE_DESCRIPTION)


class WebElementScope(_WebModel):
    """Operate explicitly on the unique element/container resolved by this locator."""

    kind: Literal["element"]
    target: WebLocator = Field(description=_TARGET_DESCRIPTION)


WebScope: TypeAlias = Annotated[WebPageScope | WebElementScope, Field(discriminator="kind")]


class WebPromptText(_WebModel):
    """Prompt-dialog input uses the same unresolved runtime-secret contract as text-entry actions."""

    text: str = Field(description="Literal prompt input or an allowlisted runtime-secret name. Secret values are execution-only, never replay parameters.")
    textType: TextSourceType = Field(default="literal", description=TEXT_TYPE_DESCRIPTION)  # noqa: N815


class WebPopupExpectation(_WebModel):
    """Bind a popup listener before the trigger and assign the resulting owned page a unique alias."""

    kind: Literal["popup"]
    page: WebPageAlias = Field(description="New unique page alias for the expected popup; never a tab number or an already declared page.")
    timeout_ms: WebTimeout = Field(default=10000, description=_TIMEOUT_DESCRIPTION)


class WebDialogExpectation(_WebModel):
    """Bind dialog type and accept/dismiss handling before a trigger that could otherwise block."""

    kind: Literal["dialog"]
    dialog_type: Literal["alert", "confirm", "prompt", "beforeunload"] = Field(description="Exact expected browser-dialog type; unexpected types are context failures, not silently accepted.")
    action: Literal["accept", "dismiss"] = Field(description="Deterministic handling of the expected dialog; prompt input is allowed only for accept on a prompt.")
    prompt_text: WebPromptText | None = Field(default=None, description="Optional typed prompt input, only for an accepted prompt. Core resolves runtimeSecret before the trigger.")
    timeout_ms: WebTimeout = Field(default=10000, description=_TIMEOUT_DESCRIPTION)

    @model_validator(mode="after")
    def _prompt_scope(self) -> WebDialogExpectation:
        if self.prompt_text is not None and (self.dialog_type != "prompt" or self.action != "accept"):
            raise ValueError("prompt_text is only valid when accepting a prompt dialog")
        return self


WebExpectedEvent: TypeAlias = Annotated[WebPopupExpectation | WebDialogExpectation, Field(discriminator="kind")]


class _WebTimedParams(_WebModel):
    timeout_ms: WebTimeout = Field(default=10000, description=_TIMEOUT_DESCRIPTION)


class _WebTargetParams(_WebTimedParams):
    target: WebLocator = Field(description=_TARGET_DESCRIPTION)


class WebStartBrowserParams(_WebModel):
    """Start or reuse the configured owned browser and main page. Fieldless, idempotent lifecycle action."""


class WebCloseBrowserParams(_WebModel):
    """Close the owned browser if present. Fieldless, idempotent action; runtime disposal remains separate."""


class _WebNavigationParams(_WebTimedParams):
    page: WebPageAlias = Field(description=_PAGE_DESCRIPTION)
    wait_until: WebWaitUntil = Field(default="load", description="Navigation completion state; default load. networkidle may time out on streaming pages and is not an application-ready assertion.")


class WebNavigateToParams(_WebNavigationParams):
    """Navigate a declared page; preserve the requested UI workflow rather than inventing filter URLs."""

    url: str = Field(description="Nonempty absolute/application-relative URL. Navigation does not imply successful application state or permit executable javascript: URLs.")

    @field_validator("url")
    @classmethod
    def _valid_url(cls, value: str) -> str:
        _nonempty(value)
        if re.sub(r"[\x00-\x20]", "", value).lower().startswith(("javascript:", "vbscript:")):
            raise ValueError("executable navigation URLs are forbidden")
        return value


class WebNavigateBackParams(_WebNavigationParams):
    """Navigate one history entry back on the declared page, bounded by the requested navigation wait."""


class WebNavigateForwardParams(_WebNavigationParams):
    """Navigate one history entry forward on the declared page; never selects another tab."""


class WebReloadPageParams(_WebNavigationParams):
    """Reload the declared page with explicit bounded navigation completion semantics."""


class _WebClickOptions(_WebModel):
    button: WebMouseButton = Field(default="left", description="Mouse button; default left. Right/middle clicks retain their actual browser semantics.")
    click_count: int = Field(default=1, ge=1, le=3, description="Number of consecutive clicks, default 1; use 2 for a double click, not a competing double flag.")
    modifiers: list[WebModifier] = Field(default_factory=list, description="Modifiers held only during this click; empty by default. Duplicate modifiers are invalid.")

    @field_validator("modifiers")
    @classmethod
    def _unique_modifiers(cls, value: list[WebModifier]) -> list[WebModifier]:
        if len(value) != len(set(value)):
            raise ValueError("modifiers must be unique")
        return value


class WebClickOnParams(_WebTargetParams, _WebClickOptions):
    """Click one actionable target after replay preparation; optional event handling is installed before the trigger."""

    expect: WebExpectedEvent | None = Field(default=None, description=_EVENT_DESCRIPTION)

    @model_validator(mode="after")
    def _new_popup_alias(self) -> WebClickOnParams:
        if isinstance(self.expect, WebPopupExpectation) and self.expect.page == self.target.page:
            raise ValueError("popup alias must differ from the triggering page")
        return self


class WebHoverOnParams(_WebTargetParams):
    """Move the pointer over one actionable element, without clicking or using a coordinate fallback."""

    modifiers: list[WebModifier] = Field(default_factory=list, description="Modifiers held during hover only; empty by default.")


class WebDragToParams(_WebTimedParams):
    """Drag between two uniquely resolved element locators; prepare both endpoints before effects."""

    source: WebLocator = Field(description="Replayable drag source. " + _TARGET_DESCRIPTION)
    destination: WebLocator = Field(description="Replayable drop destination. " + _TARGET_DESCRIPTION)

    @model_validator(mode="after")
    def _same_page(self) -> WebDragToParams:
        if self.source.page != self.destination.page:
            raise ValueError("drag endpoints must belong to the same page")
        return self


class WebScrollParams(_WebTimedParams):
    """Scroll an explicit page or unique container by CSS-pixel deltas; no hidden root or target inference."""

    scope: WebScope = Field(description="Explicit page viewport or locator-resolved scroll container; element scope must uniquely resolve before scrolling.")
    delta_x: int = Field(default=0, ge=-100000, le=100000, description="Horizontal CSS-pixel delta, positive right and negative left; default 0.")
    delta_y: int = Field(default=0, ge=-100000, le=100000, description="Vertical CSS-pixel delta, positive down and negative up; at least one delta must be nonzero.")

    @model_validator(mode="after")
    def _nonzero_scroll(self) -> WebScrollParams:
        if self.delta_x == self.delta_y == 0:
            raise ValueError("scroll requires at least one nonzero delta")
        return self


class WebScrollIntoViewParams(_WebTargetParams):
    """Scroll one uniquely resolved element into view without clicking it or changing the locator rule."""


class WebFillTextParams(_WebTargetParams):
    """Replace editable content, including clearing with empty literal text. Not sequential key entry."""

    text: str = Field(description="Replacement literal text or an allowlisted runtime-secret name. Empty literal text clears; unresolved names are preserved for replay.")
    textType: TextSourceType = Field(default="literal", description=TEXT_TYPE_DESCRIPTION)  # noqa: N815


class WebTypeTextParams(_WebTargetParams):
    """Append text using sequential key events. Never clears; use fill_text for replacement."""

    text: str = Field(description="Sequential text to append, or an allowlisted runtime-secret name. Core resolves secrets only in memory before effects.")
    textType: TextSourceType = Field(default="literal", description=TEXT_TYPE_DESCRIPTION)  # noqa: N815
    delay_ms: int = Field(default=0, ge=0, le=1000, description="Delay between sequential key events in milliseconds, default 0; not a post-action wait.")


class WebSetCheckedParams(_WebTargetParams):
    """Set checkbox/radio state idempotently rather than toggling blindly."""

    checked: bool = Field(description="Desired checked state as a strict boolean. Unsupported radio unchecking fails; it is never converted to a click toggle.")


class WebValueSelection(_WebModel):
    kind: Literal["value"]
    values: list[str] = Field(min_length=1, max_length=100, description="One or more exact native option values, including an empty-string value. Mutually exclusive with labels and indices.")


class WebLabelSelection(_WebModel):
    kind: Literal["label"]
    labels: list[str] = Field(min_length=1, max_length=100, description="One or more exact displayed native option labels; not option values. Mutually exclusive with values and indices.")


class WebIndexSelection(_WebModel):
    kind: Literal["index"]
    indices: list[Annotated[int, Field(ge=0)]] = Field(
        min_length=1, max_length=100, description="Zero-based DOM option indices, including 0; mutually exclusive with label/value selection. Empty or out-of-range selections fail."
    )


WebSelection: TypeAlias = Annotated[WebValueSelection | WebLabelSelection | WebIndexSelection, Field(discriminator="kind")]


class WebSelectOptionParams(_WebTargetParams):
    """Select native HTML select options by exactly one mode. Custom dropdowns use ordinary locator actions."""

    selection: WebSelection = Field(description="Exactly one discriminated value/label/index mode. Multiple items require a native multi-select; no mode silently takes precedence.")


_NAMED_KEYS = {
    "Enter",
    "Tab",
    "Escape",
    "Backspace",
    "Delete",
    "Insert",
    "Home",
    "End",
    "PageUp",
    "PageDown",
    "ArrowUp",
    "ArrowDown",
    "ArrowLeft",
    "ArrowRight",
    "Space",
    "CapsLock",
    "NumLock",
    "ScrollLock",
    "Pause",
    "PrintScreen",
    "ContextMenu",
    "Clear",
    "Help",
    "Shift",
    "Control",
    "Alt",
    "Meta",
}
_NAMED_KEYS.update(f"F{index}" for index in range(1, 25))
_NAMED_KEYS.update(f"Key{letter}" for letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ")
_NAMED_KEYS.update(f"Digit{index}" for index in range(10))
_NAMED_KEYS.update(f"Numpad{index}" for index in range(10))
_NAMED_KEYS.update({"NumpadEnter", "NumpadAdd", "NumpadSubtract", "NumpadMultiply", "NumpadDivide", "NumpadDecimal"})


def _validate_key(value: str) -> str:
    _nonempty(value)
    if len(value) == 1:
        return value
    parts = value.split("+")
    modifiers = parts[:-1]
    key = parts[-1]
    if not key and value.endswith("++"):
        key = "+"
        modifiers = parts[:-2]
    if len(modifiers) != len(set(modifiers)) or any(part not in {"Alt", "Control", "ControlOrMeta", "Meta", "Shift"} for part in modifiers):
        raise ValueError("key chord modifiers must be unique Alt/Control/ControlOrMeta/Meta/Shift names")
    if key not in _NAMED_KEYS and len(key) != 1:
        raise ValueError("unsupported key; use a character, Enter/Tab/Escape/arrows, F1..F24, or a supported key chord")
    return value


class WebPressKeyParams(_WebTimedParams):
    """Press a supported key/chord in explicit page or element scope; targeted Enter is preferred for submission."""

    scope: WebScope = Field(description="Element scope focuses the unique target before pressing; page scope deliberately uses the page's current focus.")
    key: str = Field(description="Character or supported key (Enter, Tab, Escape, arrows, F1..F24); chords use unique Alt/Control/ControlOrMeta/Meta/Shift plus key, e.g. Control+Enter.")
    expect: WebExpectedEvent | None = Field(default=None, description=_EVENT_DESCRIPTION)

    @field_validator("key")
    @classmethod
    def _supported_key(cls, value: str) -> str:
        return _validate_key(value)

    @model_validator(mode="after")
    def _new_popup_alias(self) -> WebPressKeyParams:
        page = self.scope.page if isinstance(self.scope, WebPageScope) else self.scope.target.page
        if isinstance(self.expect, WebPopupExpectation) and self.expect.page == page:
            raise ValueError("popup alias must differ from the triggering page")
        return self


class WebElementWait(_WebModel):
    kind: Literal["element"]
    target: WebLocator = Field(description=_TARGET_DESCRIPTION)
    state: WebWaitForState = Field(default="visible", description="visible by default; hidden/detached may succeed with zero matches. attached checks DOM presence, not visibility.")


class WebTextWait(_WebModel):
    kind: Literal["text"]
    scope: WebScope = Field(description="Page or uniquely resolved container in which visible text presence/absence is checked.")
    text: WebTextAssertion = Field(description="Exact/contains visible-text predicate, not a selector or a regular expression.")
    present: bool = Field(default=True, description="True waits for visible matching text; false waits until no visible matching text remains.")


class WebUrlWait(_WebModel):
    kind: Literal["url"]
    page: WebPageAlias = Field(description=_PAGE_DESCRIPTION)
    url: str = Field(description="Nonempty URL string to compare; never executable code, a regex, or an implicit glob.")
    exact: bool = Field(default=True, description="True requires the complete URL; false explicitly requests substring matching.")

    @field_validator("url")
    @classmethod
    def _url_required(cls, value: str) -> str:
        return _nonempty(value)


class WebLoadStateWait(_WebModel):
    kind: Literal["load_state"]
    page: WebPageAlias = Field(description=_PAGE_DESCRIPTION)
    state: Literal["domcontentloaded", "load", "networkidle"] = Field(
        description="Supported Playwright load state; no commit or arbitrary state. This is not an application-specific readiness assertion."
    )


WebWaitCondition: TypeAlias = Annotated[WebElementWait | WebTextWait | WebUrlWait | WebLoadStateWait, Field(discriminator="kind")]


class WebWaitForParams(_WebTimedParams):
    """Wait for one explicit condition, not a sleep. Pure elapsed-time waits use the inherited wait_ms capability."""

    condition: WebWaitCondition = Field(description="One discriminated element/text/URL/load-state condition; a timeout alone is not a condition.")


class WebTakeScreenshotParams(_WebModel):
    """Capture declared-page PNG evidence. Artifact destinations are storage-owned, never caller-supplied paths."""

    page: WebPageAlias = Field(description=_PAGE_DESCRIPTION)
    full_page: bool = Field(default=False, description="False captures the viewport; true captures the complete scrollable page.")
    omit_background: bool = Field(default=False, description="True permits transparent background in the PNG; false preserves the normal background.")


class WebAssertVisibleParams(_WebTargetParams):
    """Assert one visible target with bounded retry; never repair an ambiguous query by selecting the first match."""


class WebAssertNotVisibleParams(_WebTargetParams):
    """Assert no visible matches. Zero matches is valid absence, not a target-resolution failure."""


class WebAssertTextParams(_WebTargetParams):
    """Assert one target's normalized text using one explicit exact/contains predicate."""

    text: WebTextAssertion = Field(description="Exact equality or substring predicate. Competing predicate modes and implicit prefix matching are forbidden.")


class WebExpectedState(_WebModel):
    """Conjunctive expected element state; provide at least one strict boolean, with no unknown/coerced states."""

    visible: bool | None = Field(default=None, description="Expected visibility; false includes absence only where the operation explicitly permits it.")
    enabled: bool | None = Field(default=None, description="Expected enabled state, not clickability or successful business completion.")
    checked: bool | None = Field(default=None, description="Expected checked state on a checkbox/radio-compatible control.")
    selected: bool | None = Field(default=None, description="Expected selected state on an option or ARIA-selectable control.")
    focused: bool | None = Field(default=None, description="Whether this exact target has focus.")
    expanded: bool | None = Field(default=None, description="Expected explicit expanded state; a missing expansion state is not false.")
    editable: bool | None = Field(default=None, description="Expected editability of this element, distinct from enabled state.")

    @model_validator(mode="after")
    def _nonempty_state(self) -> WebExpectedState:
        if all(value is None for value in self.model_dump().values()):
            raise ValueError("state requires at least one expected boolean")
        return self


class WebAssertStateParams(_WebTargetParams):
    """Assert all supplied element-state predicates together; no silent precedence or inferred states."""

    state: WebExpectedState = Field(description="Conjunctive strict boolean expectations. Unsupported control states fail explicitly.")


class WebAssertValueParams(_WebTargetParams):
    """Assert exact form value(s), not displayed label or surrounding text."""

    value: str | Annotated[list[str], Field(min_length=1, max_length=100)] = Field(
        description="Exact input/select value, or ordered selected values for a native multi-select. Empty string asserts an empty value."
    )


class WebAssertWithAIParams(_WebModel):
    """Evaluate an explicitly authored visual/page assertion using the injected AI evaluator; never a locator-repair fallback."""

    prompt: str = Field(description="Nonempty explicit assertion goal against current page evidence, not instructions to perform an action.")
    optional: bool | None = Field(default=None, description="Existing assertion metadata; does not turn an evaluator failure into a positive deterministic verdict.")

    @field_validator("prompt")
    @classmethod
    def _prompt_required(cls, value: str) -> str:
        return _nonempty(value)


class WebObservationContinuation(_WebModel):
    """Observation-bound cursor for evidence pagination, never an element selector or persistent replay handle."""

    observation_id: str = Field(min_length=1, description="Exact source observation identity; stale/invalidated page state must fail rather than switch observations.")
    cursor: str = Field(min_length=1, description="Opaque continuation token valid only with the named observation.")


class _WebObservationParams(_WebTimedParams):
    max_chars: int = Field(
        default=12000,
        ge=256,
        le=12000,
        description="Maximum inline observation-body characters, including locators; default/cap 12000. Whole locators are omitted with a reason rather than truncated.",
    )
    max_items: int = Field(default=50, ge=1, le=200, description="Maximum exposed controls/items per view, default 50; not a promise that all virtualized content was observed.")
    continuation: WebObservationContinuation | None = Field(
        default=None,
        description="Use only a cursor supplied for the same unchanged observation, never an action target. The current Playwright backend issues no cursors; narrow scope instead.",
    )


class WebUiSnapshotParams(_WebObservationParams):
    """Read structured compact/scoped/full-artifact evidence; full mode never bypasses the inline character cap."""

    scope: WebScope = Field(description="Explicit page or locator-resolved region. Scope limits observation content; an observation ID never selects a region.")
    view: Literal["compact", "scoped", "full"] = Field(
        default="compact", description="compact summarizes; scoped requires element scope; full requests durable full evidence with a bounded inline synopsis."
    )
    max_depth: int = Field(default=8, ge=1, le=20, description="Maximum rendered region depth, default 8; does not bound native DOM traversal cost.")

    @model_validator(mode="after")
    def _scoped_view(self) -> WebUiSnapshotParams:
        if self.view == "scoped" and not isinstance(self.scope, WebElementScope):
            raise ValueError("scoped view requires element scope")
        return self


class WebFindElementsParams(_WebObservationParams):
    """Query live candidates with checked replay locators and parent context; collection rules remain ordered rules."""

    target: WebLocator = Field(description="Replayable collection query; may match multiple elements. Explicit ordering is preserved, not silently replaced with positional candidate IDs.")


class WebInspectElementParams(_WebObservationParams):
    """Inspect one uniquely resolved control, including distinct native option label/value/index details."""

    target: WebLocator = Field(description=_TARGET_DESCRIPTION)
    max_options: int = Field(default=100, ge=1, le=500, description="Maximum native select options exposed, default 100; omitted options and unknown totals remain explicit.")


class WebListPagesParams(_WebModel):
    """List owned page aliases, URLs/titles, and active state without switching pages. No fields."""


class WebOpenPageParams(_WebNavigationParams):
    """Create a new uniquely named owned page and optionally navigate it; never reuse or silently replace an alias."""

    url: str = Field(default="about:blank", description="Initial URL; default about:blank. The alias must be new and remains stable for later replay commands.")

    @field_validator("url")
    @classmethod
    def _valid_url(cls, value: str) -> str:
        return WebNavigateToParams._valid_url(value)


class WebActivatePageParams(_WebModel):
    """Bring one declared owned page to the foreground without changing other page aliases."""

    page: WebPageAlias = Field(description=_PAGE_DESCRIPTION)


class WebClosePageParams(_WebModel):
    """Close a declared owned page and invalidate its state; does not close the entire browser."""

    page: WebPageAlias = Field(description=_PAGE_DESCRIPTION)


class WebPageView(_WebModel):
    """Safe page context. Alias is executable context; URL/title are observed display facts."""

    page: WebPageAlias
    url: str
    title: str
    active: bool = False


class WebObservedState(_WebModel):
    """Observed state; null means unknown/not applicable, never an inferred false."""

    visible: bool | None = None
    enabled: bool | None = None
    checked: bool | Literal["mixed"] | None = None
    selected: bool | None = None
    focused: bool | None = None
    expanded: bool | None = None
    editable: bool | None = None
    value: str | list[str] | None = Field(default=None, description="Safe relevant form value only; never password/credential contents or unbounded form dumps.")


class WebSelectOptionView(_WebModel):
    """Native option fields stay distinct: displayed label is not a value, and index is zero-based DOM order."""

    label: str
    value: str
    index: int = Field(ge=0)
    disabled: bool
    selected: bool


class WebElementView(_WebModel):
    """An actionable entry has a complete checked locator; descriptive-only entries explain unavailability."""

    role: str
    name: str = ""
    locator: WebLocator | None = None
    locator_unavailable_reason: str | None = None
    quality: Literal["semantic", "structural", "positional", "unavailable"] = "semantic"
    state: WebObservedState = Field(default_factory=WebObservedState)
    options: list[WebSelectOptionView] = Field(default_factory=list)
    option_count: int | None = Field(default=None, ge=0)
    parent: WebLocator | None = None
    order: int | None = Field(default=None, ge=0, description="Observed order only, not a replacement action selector.")

    @model_validator(mode="after")
    def _locator_availability(self) -> WebElementView:
        if (self.locator is None) == (self.locator_unavailable_reason is None):
            raise ValueError("provide a complete locator or an explicit locator_unavailable_reason, exclusively")
        if self.locator_unavailable_reason is not None:
            _nonempty(self.locator_unavailable_reason)
            self.quality = "unavailable"
        elif self.quality == "unavailable":
            raise ValueError("available locators cannot have unavailable quality")
        if self.option_count is not None and self.option_count < len(self.options):
            raise ValueError("option_count cannot be less than exposed options")
        return self


class WebRegionView(WebElementView):
    """Region outline entry with a complete locator for scope expansion or explicit unavailability."""

    depth: int = Field(default=0, ge=0, description="Rendered outline depth only; not DOM traversal cost.")


class WebListView(WebElementView):
    """Observed list/order summary without promising complete virtualized content or inventing item identities."""

    observed_count: int = Field(default=0, ge=0)
    total_count: int | None = Field(default=None, ge=0)
    complete: bool = False
    item_names: list[str] = Field(default_factory=list, description="Observed names in display order; descriptive, not selectors.")

    @model_validator(mode="after")
    def _truthful_count(self) -> WebListView:
        if self.total_count is not None and self.total_count < self.observed_count:
            raise ValueError("total_count cannot be less than observed_count")
        if self.complete and self.total_count != self.observed_count:
            raise ValueError("complete lists require a known total equal to observed_count")
        return self


class WebObservationView(_WebModel):
    """Compact display projection; locator values are whole and unchanged even when display text is summarized."""

    regions: list[WebRegionView] = Field(default_factory=list)
    elements: list[WebElementView] = Field(default_factory=list)
    lists: list[WebListView] = Field(default_factory=list)
    focused: WebElementView | None = None
    dialogs: list[WebElementView] = Field(default_factory=list)


class WebObservationCoverage(_WebModel):
    """Semantic completeness and checked-locator coverage are independent facts."""

    semantic: Literal["complete", "partial", "text_only", "unavailable"]
    locators: Literal["complete", "partial", "unavailable"]
    observed_count: int | None = Field(default=None, ge=0)
    total_count: int | None = Field(default=None, ge=0, description="Null when unknown; do not infer complete virtualized content from currently rendered nodes.")
    omitted_count: int | None = Field(default=None, ge=0)
    virtualized: bool | None = Field(default=None, description="Null when unknown; true discloses potentially unloaded content.")
    omissions: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _truthful_coverage(self) -> WebObservationCoverage:
        if self.total_count is not None and self.observed_count is not None and self.total_count < self.observed_count:
            raise ValueError("total_count cannot be less than observed_count")
        if self.semantic in {"text_only", "unavailable"} and self.locators == "complete":
            raise ValueError("text-only/unavailable semantics cannot advertise complete locator coverage")
        return self


class WebObservationSource(_WebModel):
    """Full safe source for Harness persistence, not the inline model response. Contains no browser handles."""

    semantic_text: str | None = Field(default=None, description="Complete observed safe semantic evidence where available; never raw credential values.")
    view: WebObservationView | None = None
    coverage: WebObservationCoverage


class WebObservation(_WebModel):
    """Replay-oriented observation with separate compact view and full artifact source; identities are evidence-only."""

    schema_version: Literal["fsq.web-observation/v1"] = "fsq.web-observation/v1"
    observation_id: str = Field(min_length=1)
    observed_at: AwareDatetime | None = Field(default=None, description="Timezone-aware observation timestamp for evidence correlation; null means unavailable, not an invented capture time.")
    page: WebPageView
    view: WebObservationView
    coverage: WebObservationCoverage
    continuation: WebObservationContinuation | None = None
    full_artifact_ref: str | None = Field(default=None, description="Run-contained evidence reference assigned by storage, never an absolute host path or action selector.")
    full_source: WebObservationSource | None = Field(default=None, description="Safe complete source for persistence; adapter omits it from bounded inline projection.")

    @model_validator(mode="after")
    def _bound_continuation(self) -> WebObservation:
        if self.continuation is not None and self.continuation.observation_id != self.observation_id:
            raise ValueError("continuation must name this observation")
        return self


@dataclass(frozen=True)
class WebActionDefinition:
    fsq_action_name: str
    driver_method: str
    params_model: type[BaseModel]
    step_kind: ExecutableStepKind
    owner: Literal["driver", "platform", "harness"] = "driver"


WEB_ACTION_DEFINITIONS: tuple[WebActionDefinition, ...] = (
    WebActionDefinition("startBrowser", "start_browser", WebStartBrowserParams, "setup"),
    WebActionDefinition("closeBrowser", "close_browser", WebCloseBrowserParams, "teardown"),
    WebActionDefinition("navigateTo", "navigate_to", WebNavigateToParams, "action"),
    WebActionDefinition("navigateBack", "navigate_back", WebNavigateBackParams, "action"),
    WebActionDefinition("navigateForward", "navigate_forward", WebNavigateForwardParams, "action"),
    WebActionDefinition("reloadPage", "reload_page", WebReloadPageParams, "action"),
    WebActionDefinition("clickOn", "click_on", WebClickOnParams, "action"),
    WebActionDefinition("hoverOn", "hover_on", WebHoverOnParams, "action"),
    WebActionDefinition("dragTo", "drag_to", WebDragToParams, "action"),
    WebActionDefinition("scroll", "scroll", WebScrollParams, "action"),
    WebActionDefinition("scrollIntoView", "scroll_into_view", WebScrollIntoViewParams, "action"),
    WebActionDefinition("fillText", "fill_text", WebFillTextParams, "action"),
    WebActionDefinition("typeText", "type_text", WebTypeTextParams, "action"),
    WebActionDefinition("setChecked", "set_checked", WebSetCheckedParams, "action"),
    WebActionDefinition("selectOption", "select_option", WebSelectOptionParams, "action"),
    WebActionDefinition("pressKey", "press_key", WebPressKeyParams, "action"),
    WebActionDefinition("waitFor", "wait_for", WebWaitForParams, "action"),
    WebActionDefinition("takeScreenshot", "take_screenshot", WebTakeScreenshotParams, "observation"),
    WebActionDefinition("uiSnapshot", "ui_snapshot", WebUiSnapshotParams, "observation"),
    WebActionDefinition("findElements", "find_elements", WebFindElementsParams, "observation"),
    WebActionDefinition("inspectElement", "inspect_element", WebInspectElementParams, "diagnostic"),
    WebActionDefinition("assertVisible", "assert_visible", WebAssertVisibleParams, "assertion"),
    WebActionDefinition("assertNotVisible", "assert_not_visible", WebAssertNotVisibleParams, "assertion"),
    WebActionDefinition("assertText", "assert_text", WebAssertTextParams, "assertion"),
    WebActionDefinition("assertState", "assert_state", WebAssertStateParams, "assertion"),
    WebActionDefinition("assertValue", "assert_value", WebAssertValueParams, "assertion"),
    WebActionDefinition("assertWithAI", "assert_with_ai", WebAssertWithAIParams, "assertion"),
    WebActionDefinition("listPages", "list_pages", WebListPagesParams, "observation"),
    WebActionDefinition("openPage", "open_page", WebOpenPageParams, "action"),
    WebActionDefinition("activatePage", "activate_page", WebActivatePageParams, "action"),
    WebActionDefinition("closePage", "close_page", WebClosePageParams, "action"),
)
WEB_ACTION_DEFINITIONS_BY_NAME: dict[str, WebActionDefinition] = {definition.fsq_action_name: definition for definition in WEB_ACTION_DEFINITIONS}
