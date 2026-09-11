# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, field_serializer, model_validator

StepPhase: TypeAlias = Literal["prepare", "invoke", "settle", "finalize"]
RunnerStatus: TypeAlias = Literal["pending", "running", "passed", "failed", "skipped", "cancelled", "incomplete"]
TextSourceType: TypeAlias = Literal["literal", "runtimeSecret"]
ExecutableStepKind: TypeAlias = Literal["action", "assertion", "observation", "diagnostic", "setup", "teardown"]
FailureCategory: TypeAlias = Literal[
    "configuration_error",
    "context_error",
    "target_resolution_error",
    "action_error",
    "assertion_error",
    "timeout_error",
    "observation_error",
    "artifact_error",
    "harness_error",
    "cancelled",
    "unknown",
]
RunnerEventType: TypeAlias = Literal[
    "session_start",
    "session_finish",
    "step_start",
    "phase_start",
    "harness_call_start",
    "harness_call_finish",
    "artifact_captured",
    "phase_finish",
    "step_error",
    "step_finish",
    "action_result",
    "artifact_failed",
]
EvidenceArtifactKind: TypeAlias = Literal["screenshot", "ui_tree", "ui_snapshot", "tool_call", "log", "json", "text", "other"]
HarnessPlatform: TypeAlias = Literal["android", "ios", "macos", "windows", "web"]
AndroidSwipeDirection: TypeAlias = Literal["up", "down", "left", "right"]
WindowsMouseButton: TypeAlias = Literal["left", "right", "middle"]
MacOSOrderDirection: TypeAlias = Literal["vertical", "horizontal"]

TEXT_TYPE_DESCRIPTION = "Use literal for plain text; use runtimeSecret when text names an allowlisted runtime secret."
ANDROID_TARGET_SCHEMA_DESCRIPTION = "Provide target or non-empty locator. Prefer a semantic target from the current Android UI snapshot; use locator when target text is unavailable."
WINDOWS_TARGET_SCHEMA_DESCRIPTION = "Provide a non-empty locator. The target field is descriptive only and is not used for Windows control lookup."
MACOS_TARGET_SCHEMA_DESCRIPTION = "Provide target, non-empty locator, or point. Prefer target or locator for UI elements and point only for coordinate-based actions."


class SourceRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_type: str
    source_id: str | None = None
    step_index: int | None = Field(default=None, ge=0)
    metadata: dict[str, Any] = Field(default_factory=dict)


class RetryPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_attempts: int = Field(default=1, ge=1)
    delay_ms: int = Field(default=0, ge=0)
    retry_on: list[FailureCategory] = Field(default_factory=list)


class EvidencePolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    capture_before: bool = False
    capture_after: bool = True
    capture_on_failure: bool = True
    artifact_kinds: list[EvidenceArtifactKind] = Field(default_factory=list)


class ExecutableStep(BaseModel):
    model_config = ConfigDict(extra="forbid")

    step_id: str
    source_step_id: str | None = None
    step_execution_id: str | None = None
    invocation_path: tuple[str, ...] = ()
    attempt_index: int = Field(default=1, ge=1)
    source_ref: SourceRef | None = None
    kind: ExecutableStepKind
    action_name: str
    params: dict[str, Any] = Field(default_factory=dict)
    target_ref: str | None = None
    retry_policy: RetryPolicy = Field(default_factory=RetryPolicy)
    evidence_policy: EvidencePolicy = Field(default_factory=EvidencePolicy)
    timeout_ms: int | None = Field(default=None, ge=1)
    metadata: dict[str, Any] = Field(default_factory=dict)


class HarnessArtifactRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    artifact_id: str
    kind: EvidenceArtifactKind
    path: Path | None = None
    mime_type: str | None = None
    size_bytes: int | None = Field(default=None, ge=0)
    sha256: str | None = None
    availability: Literal["available", "missing", "failed", "unavailable", "omitted", "not_applicable", "truncated", "unknown"] = "available"
    unavailable_reason: str | None = None
    capture_occurrence: int | None = Field(default=None, ge=1)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_serializer("path", when_used="json")
    def serialize_path(self, value: Path | None) -> str | None:
        return value.as_posix() if value is not None else None


class HarnessContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    platform: HarnessPlatform
    session_id: str | None = None
    capabilities: dict[str, Any] = Field(default_factory=dict)
    current_url: str | None = None
    current_activity: str | None = None
    screen_size: tuple[int, int] | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class HarnessActionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: RunnerStatus
    action_name: str
    started_at: datetime | None = None
    ended_at: datetime | None = None
    duration_ms: int = Field(default=0, ge=0)
    output: Any = None
    artifact_refs: list[HarnessArtifactRef] = Field(default_factory=list)
    error_message: str | None = None
    failure_category: FailureCategory | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class HarnessFunctionSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    description: str = ""
    params_json_schema: dict[str, Any] = Field(default_factory=dict)
    platform: HarnessPlatform
    driver_method: str
    fsq_action_name: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class AndroidLocator(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"description": "Structured Android locator. Provide at least one populated locator field."},
    )

    # Names mirror the authored Android locator payload contract.
    resourceId: str | None = Field(default=None, description="Android resource id to match.")  # noqa: N815
    accessibilityId: str | None = Field(default=None, description="Android accessibility id or content description to match.")  # noqa: N815
    text: str | None = Field(default=None, description="Visible Android text to match.")
    className: str | None = Field(default=None, description="Android class name to match.")  # noqa: N815
    xpath: str | None = Field(default=None, description="XPath expression for Android UI hierarchy lookup.")

    def has_value(self) -> bool:
        return any(isinstance(value, str) and value.strip() for value in self.model_dump().values())


class WaitMsParams(BaseModel):
    model_config = ConfigDict(extra="forbid", json_schema_extra={"description": "Wait without touching platform state."})

    duration_ms: int = Field(ge=1, le=60000, description="Wait duration in milliseconds, from 1 to 60000.")
    reason: str | None = Field(default=None, description="Optional short reason for the wait.")


class AndroidPoint(BaseModel):
    model_config = ConfigDict(extra="forbid", json_schema_extra={"description": "Android screen point in absolute pixels."})

    x: int = Field(description="Horizontal Android screen coordinate in pixels.")
    y: int = Field(description="Vertical Android screen coordinate in pixels.")


class AndroidScreenSize(BaseModel):
    model_config = ConfigDict(extra="forbid", json_schema_extra={"description": "Android screen size used as a coordinate reference."})

    width: int = Field(ge=1, description="Reference screen width in pixels.")
    height: int = Field(ge=1, description="Reference screen height in pixels.")


class _AndroidTargetParams(BaseModel):
    model_config = ConfigDict(extra="forbid", json_schema_extra={"description": ANDROID_TARGET_SCHEMA_DESCRIPTION})

    target: str | None = Field(default=None, description="Android semantic target from the current UI snapshot.")
    locator: AndroidLocator | None = Field(default=None, description="Optional structured Android locator. Provide at least one populated locator field.")

    @model_validator(mode="after")
    def _require_target(self) -> "_AndroidTargetParams":
        if self._has_target_value():
            return self
        raise ValueError("requires target or non-empty locator")

    def _has_target_value(self) -> bool:
        if isinstance(self.target, str) and self.target.strip():
            return True
        return self.locator is not None and self.locator.has_value()


class AndroidLaunchAppParams(BaseModel):
    model_config = ConfigDict(extra="forbid", json_schema_extra={"description": "Launch the configured Android app or a supplied package id."})

    app_id: str | None = Field(default=None, description="Optional Android package id. Omit to use the configured app id.")


class AndroidKillAppParams(BaseModel):
    model_config = ConfigDict(extra="forbid", json_schema_extra={"description": "Stop the configured Android app or a supplied package id."})

    app_id: str | None = Field(default=None, description="Optional Android package id. Omit to use the configured app id.")


class AndroidTapOnParams(_AndroidTargetParams):
    pass


class AndroidTapAtParams(BaseModel):
    model_config = ConfigDict(extra="forbid", json_schema_extra={"description": "Tap Android screen coordinates."})

    point: AndroidPoint = Field(description="Android screen point to tap.")
    reference_screen_size: AndroidScreenSize | None = Field(default=None, description="Original screen size for proportional replay of recorded coordinates.")

    def replay_params(self, context: object) -> dict[str, object]:
        return _android_coordinate_replay(self.model_dump(mode="json", exclude_none=True), context)


class AndroidLongPressOnParams(_AndroidTargetParams):
    pass


class AndroidInputTextParams(_AndroidTargetParams):
    text: str = Field(description="Literal text to enter, or a runtime secret name when textType is runtimeSecret.")
    # Name mirrors the authored text-entry payload contract.
    textType: TextSourceType = Field(default="literal", description=TEXT_TYPE_DESCRIPTION)  # noqa: N815


class AndroidPressKeyParams(BaseModel):
    model_config = ConfigDict(extra="forbid", json_schema_extra={"description": "Press one Android key."})

    key: str = Field(description="Non-empty Android key name such as Back, Home, Enter, or a backend-supported key string.")

    @model_validator(mode="after")
    def _require_key(self) -> "AndroidPressKeyParams":
        if self.key.strip():
            return self
        raise ValueError("requires non-empty key")


class AndroidSwipeParams(BaseModel):
    model_config = ConfigDict(extra="forbid", json_schema_extra={"description": "Swipe by direction or both start and end Android screen points."})

    direction: AndroidSwipeDirection | None = Field(default=None, description="Swipe direction to use instead of explicit start and end points.")
    start: AndroidPoint | None = Field(default=None, description="Start point for a coordinate swipe. Requires end when used.")
    end: AndroidPoint | None = Field(default=None, description="End point for a coordinate swipe. Requires start when used.")
    reference_screen_size: AndroidScreenSize | None = Field(default=None, description="Original screen size for proportional replay of recorded swipe coordinates.")
    duration: int | None = Field(default=None, ge=1, description="Optional swipe duration in milliseconds.")

    def replay_params(self, context: object) -> dict[str, object]:
        params = self.model_dump(mode="json", exclude_none=True)
        return _android_coordinate_replay(params, context) if self.start is not None and self.end is not None else params

    @model_validator(mode="after")
    def _require_direction_or_points(self) -> "AndroidSwipeParams":
        has_direction = self.direction is not None
        has_points = self.start is not None and self.end is not None
        if has_direction or has_points:
            return self
        raise ValueError("requires direction or both start and end points")


def _android_coordinate_replay(params: dict[str, object], context: object) -> dict[str, object]:
    screen_size = getattr(context, "screen_size", None)
    if "reference_screen_size" not in params and isinstance(screen_size, tuple) and len(screen_size) == 2:
        width, height = screen_size
        if isinstance(width, int) and isinstance(height, int) and width > 0 and height > 0:
            params["reference_screen_size"] = {"width": width, "height": height}
    return params


class AndroidUiTreeParams(BaseModel):
    model_config = ConfigDict(extra="forbid", json_schema_extra={"description": "Read the current compact Android UI hierarchy. No parameters are accepted."})


class AndroidPerformActionsParams(BaseModel):
    model_config = ConfigDict(extra="forbid", json_schema_extra={"description": "W3C action payload for non-exposed Android driver actions."})

    actions: list[dict[str, Any]] = Field(description="W3C actions array.")


class AndroidAssertVisibleParams(_AndroidTargetParams):
    optional: bool | None = Field(default=None, description="When true, treat a missing target as an optional assertion outcome.")


class AndroidAssertNotVisibleParams(_AndroidTargetParams):
    optional: bool | None = Field(default=None, description="When true, treat assertion uncertainty as optional.")


class AndroidTextAssertion(BaseModel):
    model_config = ConfigDict(extra="forbid", json_schema_extra={"description": "Android text assertion. Provide contains or equals."})

    contains: str | None = Field(default=None, description="Expected substring in visible Android text.")
    equals: str | None = Field(default=None, description="Expected exact visible Android text.")

    @model_validator(mode="after")
    def _require_text_assertion(self) -> "AndroidTextAssertion":
        if isinstance(self.contains, str) or isinstance(self.equals, str):
            return self
        raise ValueError("requires contains or equals")


class AndroidElementState(AndroidLocator):
    enabled: bool | None = Field(default=None, description="Expected enabled state.")
    checked: bool | None = Field(default=None, description="Expected checked state.")
    selected: bool | None = Field(default=None, description="Expected selected state.")
    clickable: bool | None = Field(default=None, description="Expected clickable state.")
    focused: bool | None = Field(default=None, description="Expected focused state.")

    def has_state_assertion(self) -> bool:
        return any(value is not None for value in [self.enabled, self.checked, self.selected, self.clickable, self.focused])


class AndroidAssertStateParams(BaseModel):
    model_config = ConfigDict(extra="forbid", json_schema_extra={"description": "Assert Android text or element locator/state. Provide text or element."})

    element: AndroidElementState | None = Field(default=None, description="Android locator plus optional expected state values.")
    text: AndroidTextAssertion | None = Field(default=None, description="Visible text assertion.")
    optional: bool | None = Field(default=None, description="When true, treat assertion uncertainty as optional.")

    @model_validator(mode="after")
    def _require_assertion(self) -> "AndroidAssertStateParams":
        if self.text is not None:
            return self
        if self.element is not None and (self.element.has_value() or self.element.has_state_assertion()):
            return self
        raise ValueError("requires text assertion or element locator/state assertion")


class AndroidAssertWithAIParams(BaseModel):
    model_config = ConfigDict(extra="forbid", json_schema_extra={"description": "Evaluate an explicit Android visual assertion with AI."})

    prompt: str = Field(description="Non-empty visual assertion prompt to evaluate against current evidence.")
    optional: bool | None = Field(default=None, description="When true, treat assertion uncertainty as optional.")

    @model_validator(mode="after")
    def _require_prompt(self) -> "AndroidAssertWithAIParams":
        if self.prompt.strip():
            return self
        raise ValueError("requires non-empty prompt")


@dataclass(frozen=True)
class AndroidActionDefinition:
    fsq_action_name: str
    driver_method: str
    params_model: type[BaseModel]
    step_kind: ExecutableStepKind
    owner: Literal["driver", "platform", "harness"] = "driver"


ANDROID_ACTION_DEFINITIONS: tuple[AndroidActionDefinition, ...] = (
    AndroidActionDefinition("launchApp", "launch_app", AndroidLaunchAppParams, "setup"),
    AndroidActionDefinition("killApp", "kill_app", AndroidKillAppParams, "teardown"),
    AndroidActionDefinition("tapOn", "tap_on", AndroidTapOnParams, "action"),
    AndroidActionDefinition("tapAt", "tap_at", AndroidTapAtParams, "action"),
    AndroidActionDefinition("assertVisible", "assert_visible", AndroidAssertVisibleParams, "assertion"),
    AndroidActionDefinition("performActions", "perform_actions", AndroidPerformActionsParams, "action"),
    AndroidActionDefinition("assert", "assert_state", AndroidAssertStateParams, "assertion"),
    AndroidActionDefinition("pressKey", "press_key", AndroidPressKeyParams, "action"),
    AndroidActionDefinition("inputText", "input_text", AndroidInputTextParams, "action"),
    AndroidActionDefinition("assertNotVisible", "assert_not_visible", AndroidAssertNotVisibleParams, "assertion"),
    AndroidActionDefinition("longPressOn", "long_press_on", AndroidLongPressOnParams, "action"),
    AndroidActionDefinition("swipe", "swipe", AndroidSwipeParams, "action"),
    AndroidActionDefinition("uiTree", "ui_snapshot", AndroidUiTreeParams, "observation"),
    AndroidActionDefinition("assertWithAI", "assert_with_ai", AndroidAssertWithAIParams, "assertion"),
)
ANDROID_ACTION_DEFINITIONS_BY_NAME: dict[str, AndroidActionDefinition] = {definition.fsq_action_name: definition for definition in ANDROID_ACTION_DEFINITIONS}


class WindowsLocator(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"description": "Windows control locator. Provide at least one populated lookup field."},
    )

    title: str | None = Field(default=None, description="Window or control title to match.")
    control_type: str | None = Field(default=None, description="Windows control type to match.")
    automation_id: str | None = Field(default=None, description="Automation id to match.")
    class_name: str | None = Field(default=None, description="Control class name to match.")
    index: int | None = Field(default=None, ge=1, description="One-based match index when multiple controls match.")
    parent_title: str | None = Field(default=None, description="Optional parent title constraint.")
    parent_control_type: str | None = Field(default=None, description="Optional parent control type constraint.")
    parent_automation_id: str | None = Field(default=None, description="Optional parent automation id constraint.")

    def has_value(self) -> bool:
        return any(isinstance(value, str) and value.strip() for value in (self.title, self.control_type, self.automation_id, self.class_name))


class _WindowsTargetParams(BaseModel):
    model_config = ConfigDict(extra="forbid", json_schema_extra={"description": WINDOWS_TARGET_SCHEMA_DESCRIPTION})

    target: str | None = Field(default=None, description="Human-readable descriptive target. Windows lookup uses locator, not this field.")
    locator: WindowsLocator = Field(description="Windows control locator used for lookup. Provide at least one populated lookup field.")

    @model_validator(mode="after")
    def _require_target(self) -> "_WindowsTargetParams":
        if not self.locator.has_value():
            raise ValueError("requires non-empty locator")
        return self


class WindowsPoint(BaseModel):
    model_config = ConfigDict(extra="forbid", json_schema_extra={"description": "Windows screen point in absolute pixels."})

    x: int = Field(ge=0, description="Horizontal Windows screen coordinate in pixels.")
    y: int = Field(ge=0, description="Vertical Windows screen coordinate in pixels.")


class WindowsOffset(BaseModel):
    model_config = ConfigDict(extra="forbid", json_schema_extra={"description": "Non-zero relative Windows mouse offset."})

    x: int = Field(default=0, description="Horizontal offset in pixels.")
    y: int = Field(default=0, description="Vertical offset in pixels.")

    @model_validator(mode="after")
    def _require_non_zero_offset(self) -> "WindowsOffset":
        if self.x != 0 or self.y != 0:
            return self
        raise ValueError("requires non-zero offset")


class WindowsMouseSource(BaseModel):
    model_config = ConfigDict(extra="forbid", json_schema_extra={"description": "Windows mouse source. Provide exactly one of locator or point."})

    locator: WindowsLocator | None = Field(default=None, description="Source Windows control locator.")
    point: WindowsPoint | None = Field(default=None, description="Source Windows screen point.")

    @model_validator(mode="after")
    def _require_exactly_one_mode(self) -> "WindowsMouseSource":
        if sum(value is not None for value in (self.locator, self.point)) != 1:
            raise ValueError("requires exactly one of locator or point")
        if self.locator is not None and not self.locator.has_value():
            raise ValueError("requires non-empty locator")
        return self


class WindowsMouseDestination(BaseModel):
    model_config = ConfigDict(extra="forbid", json_schema_extra={"description": "Windows mouse destination. Provide exactly one of locator, point, or offset."})

    locator: WindowsLocator | None = Field(default=None, description="Destination Windows control locator.")
    point: WindowsPoint | None = Field(default=None, description="Destination Windows screen point.")
    offset: WindowsOffset | None = Field(default=None, description="Destination offset relative to the source.")

    @model_validator(mode="after")
    def _require_exactly_one_mode(self) -> "WindowsMouseDestination":
        if sum(value is not None for value in (self.locator, self.point, self.offset)) != 1:
            raise ValueError("requires exactly one of locator, point, or offset")
        if self.locator is not None and not self.locator.has_value():
            raise ValueError("requires non-empty locator")
        return self


class WindowsLaunchAppParams(BaseModel):
    model_config = ConfigDict(extra="forbid", json_schema_extra={"description": "Launch the configured Windows desktop application."})

    extra_args: list[str] | None = Field(default=None, description="Optional additional launch arguments appended to configured arguments.")


class WindowsKillAppParams(BaseModel):
    model_config = ConfigDict(extra="forbid", json_schema_extra={"description": "Stop the launched Windows desktop application. No parameters are accepted."})


class WindowsClickOnParams(_WindowsTargetParams):
    button: WindowsMouseButton | None = Field(default=None, description="Mouse button to click. Defaults to backend left-button behavior.")
    double: bool | None = Field(default=None, description="When true, perform a double click.")


class WindowsDoubleClickOnParams(_WindowsTargetParams):
    button: WindowsMouseButton | None = Field(default=None, description="Mouse button to double-click. Defaults to backend left-button behavior.")


class WindowsRightClickOnParams(_WindowsTargetParams):
    pass


class WindowsTypeTextParams(_WindowsTargetParams):
    text: str = Field(description="Literal text to type, or a runtime secret name when textType is runtimeSecret.")
    # Name mirrors the authored text-entry payload contract.
    textType: TextSourceType = Field(default="literal", description=TEXT_TYPE_DESCRIPTION)  # noqa: N815
    clear: bool | None = Field(default=None, description="When true, clear existing target text before typing.")

    @model_validator(mode="after")
    def _require_text(self) -> "WindowsTypeTextParams":
        if isinstance(self.text, str):
            return self
        raise ValueError("requires text")


class WindowsPressKeyParams(BaseModel):
    model_config = ConfigDict(extra="forbid", json_schema_extra={"description": "Press one key or key sequence in the active Windows window."})

    key: str = Field(description="Non-empty key or shortcut string supported by the Windows backend.")

    @model_validator(mode="after")
    def _require_key(self) -> "WindowsPressKeyParams":
        if self.key.strip():
            return self
        raise ValueError("requires non-empty key")


class WindowsHoverOnParams(_WindowsTargetParams):
    pass


class WindowsScrollOnParams(_WindowsTargetParams):
    wheel_dist: int = Field(description="Non-zero mouse wheel distance. Positive and negative values scroll opposite directions.")

    @model_validator(mode="after")
    def _require_wheel_distance(self) -> "WindowsScrollOnParams":
        if self.wheel_dist != 0:
            return self
        raise ValueError("requires non-zero wheel_dist")


class WindowsDragToParams(BaseModel):
    model_config = ConfigDict(extra="forbid", json_schema_extra={"description": "Drag from one Windows source to one destination."})

    target: str = Field(description="Human-readable description of the drag action.")
    source: WindowsMouseSource = Field(description="Drag source as a locator or screen point.")
    destination: WindowsMouseDestination = Field(description="Drag destination as a locator, screen point, or relative offset.")
    mouse_button: WindowsMouseButton = Field(default="left", description="Mouse button to hold during the drag.")

    @model_validator(mode="after")
    def _require_target(self) -> "WindowsDragToParams":
        if self.target.strip():
            return self
        raise ValueError("requires non-empty target")


class WindowsAssertVisibleParams(_WindowsTargetParams):
    optional: bool | None = Field(default=None, description="When true, treat assertion uncertainty as optional.")


class WindowsUiSnapshotParams(BaseModel):
    model_config = ConfigDict(extra="forbid", json_schema_extra={"description": "Read the current Windows control tree snapshot. No parameters are accepted."})


class WindowsAssertWithAIParams(BaseModel):
    model_config = ConfigDict(extra="forbid", json_schema_extra={"description": "Evaluate an explicit Windows visual assertion with AI."})

    prompt: str = Field(description="Non-empty visual assertion prompt to evaluate against current evidence.")
    optional: bool | None = Field(default=None, description="When true, treat assertion uncertainty as optional.")

    @model_validator(mode="after")
    def _require_prompt(self) -> "WindowsAssertWithAIParams":
        if self.prompt.strip():
            return self
        raise ValueError("requires non-empty prompt")


class MacOSPoint(BaseModel):
    model_config = ConfigDict(extra="forbid", json_schema_extra={"description": "macOS screen point in absolute pixels."})

    x: int = Field(description="Horizontal macOS screen coordinate in pixels.")
    y: int = Field(description="Vertical macOS screen coordinate in pixels.")


class MacOSLocator(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "description": "Structured macOS locator. All populated element fields constrain one unique element (AND). Use complete literal values, not clipped display text. Coordinates never bypass element constraints."
        },
    )

    # Names mirror the authored macOS locator payload contract.
    accessibilityId: str | None = Field(default=None, description="macOS accessibility id to match.")  # noqa: N815
    name: str | None = Field(default=None, description="Exact semantic name matching identity/name/label/value for compatibility. All additional fields still constrain the same element.")
    label: str | None = Field(default=None, description="macOS accessibility label to match.")
    value: str | None = Field(default=None, description="macOS accessibility value to match.")
    role: str | None = Field(default=None, description="macOS accessibility role to match.")
    controlType: str | None = Field(default=None, description="macOS control type to match.")  # noqa: N815
    className: str | None = Field(default=None, description="macOS class name to match.")  # noqa: N815
    xpath: str | None = Field(default=None, description="XPath expression for Appium Mac2 lookup.")
    predicate: str | None = Field(default=None, description="Appium predicate string for macOS lookup.")
    point: MacOSPoint | None = Field(default=None, description="macOS screen point used as a locator signal.")

    def has_value(self) -> bool:
        if self.point is not None:
            return True
        return any(
            isinstance(value, str) and value.strip()
            for value in (
                self.accessibilityId,
                self.name,
                self.label,
                self.value,
                self.role,
                self.controlType,
                self.className,
                self.xpath,
                self.predicate,
            )
        )


class _MacOSTargetParams(BaseModel):
    model_config = ConfigDict(extra="forbid", json_schema_extra={"description": MACOS_TARGET_SCHEMA_DESCRIPTION})

    target: str | None = Field(default=None, description="macOS semantic target name from the current accessibility snapshot.")
    locator: MacOSLocator | None = Field(default=None, description="Optional structured macOS locator. Provide at least one populated locator field or point.")
    point: MacOSPoint | None = Field(default=None, description="macOS screen point for coordinate-based action targeting.")

    @model_validator(mode="after")
    def _require_target(self) -> "_MacOSTargetParams":
        if self._has_target_value():
            return self
        raise ValueError("requires target, non-empty locator, or point")

    def _has_target_value(self) -> bool:
        if isinstance(self.target, str) and self.target.strip():
            return True
        if self.locator is not None and self.locator.has_value():
            return True
        return self.point is not None


class _MacOSElementRef(BaseModel):
    model_config = ConfigDict(extra="forbid", json_schema_extra={"description": "macOS element reference. Provide target or non-empty locator."})

    target: str | None = Field(default=None, description="macOS semantic target name for an element.")
    locator: MacOSLocator | None = Field(default=None, description="Structured macOS locator for an element.")

    @model_validator(mode="after")
    def _require_element_ref(self) -> "_MacOSElementRef":
        if isinstance(self.target, str) and self.target.strip():
            return self
        if self.locator is not None and self.locator.has_value():
            return self
        raise ValueError("requires target or non-empty locator")


class MacOSLaunchAppParams(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"description": "Launch a macOS application. Reuse an existing Mac2 session by bundle id, or set new_session to replace it."},
    )

    bundle_id: str | None = Field(default=None, description="macOS bundle id to create or activate. Omit to use the configured bundle id.")
    app_path: str | None = Field(default=None, description="Local app path for session creation. Omit to use the configured app path.")
    arguments: list[str] | None = Field(default=None, description="Application arguments used only during session creation.")
    new_session: bool = Field(
        default=False,
        description="When true, close any existing Mac2 session and create a new one. When false, activate the bundle id in the existing session.",
    )


class MacOSKillAppParams(BaseModel):
    model_config = ConfigDict(extra="forbid", json_schema_extra={"description": "Stop the active macOS application or close the Appium session."})

    bundle_id: str | None = Field(default=None, description="macOS bundle id to terminate. Omit to use the configured bundle id.")
    close_session: bool | None = Field(
        default=None,
        description="When true, close the Mac2 session directly; otherwise terminate the bundle id and retain the session.",
    )


class MacOSClickOnParams(_MacOSTargetParams):
    modifiers: list[str] | None = Field(default=None, description="Optional keyboard modifiers to hold while clicking.")


class MacOSDoubleClickOnParams(_MacOSTargetParams):
    modifiers: list[str] | None = Field(default=None, description="Optional keyboard modifiers to hold while double-clicking.")


class MacOSRightClickOnParams(_MacOSTargetParams):
    modifiers: list[str] | None = Field(default=None, description="Optional keyboard modifiers to hold while right-clicking.")


class MacOSHoverOnParams(_MacOSTargetParams):
    duration_ms: int | None = Field(default=None, ge=1, description="Optional hover duration in milliseconds.")


class MacOSTypeTextParams(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"description": "Type text into macOS. Optional target, locator, or point may focus a destination before typing."},
    )

    text: str = Field(description="Literal text to type, or a runtime secret name when textType is runtimeSecret.")
    # Name mirrors the authored text-entry payload contract.
    textType: TextSourceType = Field(default="literal", description=TEXT_TYPE_DESCRIPTION)  # noqa: N815
    target: str | None = Field(default=None, description="Optional macOS semantic target to focus before typing.")
    locator: MacOSLocator | None = Field(default=None, description="Optional structured macOS locator to focus before typing.")
    point: MacOSPoint | None = Field(default=None, description="Optional macOS screen point to focus before typing.")
    clear: bool | None = Field(default=None, description="When true, clear existing target text before typing.")

    @model_validator(mode="after")
    def _require_text(self) -> "MacOSTypeTextParams":
        if isinstance(self.text, str):
            return self
        raise ValueError("requires text")


class MacOSPressKeyParams(BaseModel):
    model_config = ConfigDict(extra="forbid", json_schema_extra={"description": "Press one character or named key in macOS, optionally with modifiers."})

    key: str = Field(description="Character or named key to press. Named keys include Enter, Return, Escape, Tab, Space, Delete, Backspace, and arrow/navigation keys.")
    modifiers: list[str] | None = Field(
        default=None,
        description="Modifiers applied only to this key. Supported values: COMMAND, CONTROL, OPTION or ALT, SHIFT, CAPS_LOCK, and FUNCTION.",
    )

    @model_validator(mode="after")
    def _require_key(self) -> "MacOSPressKeyParams":
        if self.key.strip():
            return self
        raise ValueError("requires non-empty key")


class MacOSDragEndpoint(_MacOSTargetParams):
    pass


class MacOSDragToParams(BaseModel):
    model_config = ConfigDict(extra="forbid", json_schema_extra={"description": "Drag from one macOS source endpoint to one destination endpoint."})

    source: MacOSDragEndpoint = Field(description="Drag source expressed as target, locator, or point.")
    destination: MacOSDragEndpoint = Field(description="Drag destination expressed as target, locator, or point.")
    duration_ms: int | None = Field(default=None, ge=1, description="Optional drag duration in milliseconds.")


class MacOSTakeScreenshotParams(BaseModel):
    model_config = ConfigDict(extra="forbid", json_schema_extra={"description": "Capture a macOS screenshot for evidence or debugging."})

    full_screen: bool | None = Field(default=None, description="When true, request a full-screen screenshot where supported.")


class MacOSElementQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str | None = Field(default=None, max_length=1024, description="Match semantic element text only, not type names or JSON keys.")
    match: Literal["exact", "contains"] = Field(default="contains", description="Text comparison mode.")
    case_sensitive: bool = Field(default=False, description="Whether text matching is case-sensitive.")
    control_type: str | None = Field(default=None, max_length=128, description="Exact backend element type.")
    enabled: bool | None = Field(default=None, description="Required known enabled state.")
    visible: bool | None = Field(default=None, description="Required known visibility state; missing state does not match.")
    selected: bool | None = Field(default=None, description="Required known selection state.")
    limit: int = Field(default=20, ge=1, le=50, description="Maximum candidates returned.")
    offset: int = Field(default=0, ge=0, le=10000, description="Candidate offset within the same snapshot.")
    snapshot_revision: str | None = Field(default=None, max_length=64, description="Revision returned by the initial query; required for continuation.")

    @model_validator(mode="after")
    def _require_revision(self) -> "MacOSElementQuery":
        if self.offset and not self.snapshot_revision:
            raise ValueError("Query continuation requires snapshot_revision.")
        return self


class MacOSUiSnapshotParams(BaseModel):
    model_config = ConfigDict(extra="forbid", json_schema_extra={"description": "Read the current macOS accessibility tree snapshot."})

    max_depth: int | None = Field(default=None, ge=1, description="Optional maximum tree depth to include.")
    include_attributes: bool | None = Field(default=None, description="When true, include additional Appium element attributes where supported.")
    query: MacOSElementQuery | None = Field(default=None, description="Return bounded semantic candidates from the full current tree instead of a depth-clipped tree.")


class MacOSAssertVisibleParams(_MacOSTargetParams):
    optional: bool | None = Field(default=None, description="When true, treat assertion uncertainty as optional.")


class MacOSAssertElementsOrderParams(BaseModel):
    model_config = ConfigDict(extra="forbid", json_schema_extra={"description": "Assert that macOS elements appear in the requested vertical or horizontal order."})

    elements: list[_MacOSElementRef] = Field(min_length=2, description="Ordered macOS element references to compare.")
    direction: MacOSOrderDirection = Field(default="vertical", description="Axis used to compare element centers.")
    expected_order: list[int] | None = Field(default=None, description="Optional zero-based expected order. Omit to use the authored element order.")
    tolerance: float | None = Field(default=None, ge=0, description="Optional pixel tolerance for order comparison.")
    require_all: bool = Field(default=True, description="When true, missing elements fail the assertion.")

    @model_validator(mode="after")
    def _validate_expected_order(self) -> "MacOSAssertElementsOrderParams":
        if self.expected_order is None:
            return self
        expected = self.expected_order
        if len(expected) != len(self.elements):
            raise ValueError("expected_order length must match elements length")
        if sorted(expected) != list(range(len(self.elements))):
            raise ValueError("expected_order must contain each zero-based element index exactly once")
        return self


class MacOSAssertWithAIParams(BaseModel):
    model_config = ConfigDict(extra="forbid", json_schema_extra={"description": "Evaluate an explicit macOS visual assertion with AI."})

    prompt: str = Field(description="Non-empty visual assertion prompt to evaluate against current evidence.")
    optional: bool | None = Field(default=None, description="When true, treat assertion uncertainty as optional.")

    @model_validator(mode="after")
    def _require_prompt(self) -> "MacOSAssertWithAIParams":
        if self.prompt.strip():
            return self
        raise ValueError("requires non-empty prompt")


@dataclass(frozen=True)
class MacOSActionDefinition:
    fsq_action_name: str
    driver_method: str
    params_model: type[BaseModel]
    step_kind: ExecutableStepKind
    owner: Literal["driver", "platform", "harness"] = "driver"


MACOS_ACTION_DEFINITIONS: tuple[MacOSActionDefinition, ...] = (
    MacOSActionDefinition("launchApp", "launch_app", MacOSLaunchAppParams, "setup"),
    MacOSActionDefinition("killApp", "kill_app", MacOSKillAppParams, "teardown"),
    MacOSActionDefinition("clickOn", "click_on", MacOSClickOnParams, "action"),
    MacOSActionDefinition("doubleClickOn", "double_click_on", MacOSDoubleClickOnParams, "action"),
    MacOSActionDefinition("rightClickOn", "right_click_on", MacOSRightClickOnParams, "action"),
    MacOSActionDefinition("typeText", "type_text", MacOSTypeTextParams, "action"),
    MacOSActionDefinition("pressKey", "press_key", MacOSPressKeyParams, "action"),
    MacOSActionDefinition("hoverOn", "hover_on", MacOSHoverOnParams, "action"),
    MacOSActionDefinition("dragTo", "drag_to", MacOSDragToParams, "action"),
    MacOSActionDefinition("takeScreenshot", "take_screenshot", MacOSTakeScreenshotParams, "observation"),
    MacOSActionDefinition("uiSnapshot", "ui_snapshot", MacOSUiSnapshotParams, "observation"),
    MacOSActionDefinition("assertVisible", "assert_visible", MacOSAssertVisibleParams, "assertion"),
    MacOSActionDefinition("assertElementsOrder", "assert_elements_order", MacOSAssertElementsOrderParams, "assertion"),
    MacOSActionDefinition("assertWithAI", "assert_with_ai", MacOSAssertWithAIParams, "assertion"),
)
MACOS_ACTION_DEFINITIONS_BY_NAME: dict[str, MacOSActionDefinition] = {definition.fsq_action_name: definition for definition in MACOS_ACTION_DEFINITIONS}


class StepCallInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    phase: StepPhase
    started_at: datetime | None = None
    ended_at: datetime | None = None
    duration_ms: int = Field(default=0, ge=0)
    status: RunnerStatus
    return_value: Any = None
    exception_type: str | None = None
    exception_message: str | None = None
    failure_category: FailureCategory | None = None


class EvidenceArtifactRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    artifact_id: str
    kind: EvidenceArtifactKind
    path: Path | None = None
    mime_type: str | None = None
    size_bytes: int | None = Field(default=None, ge=0)
    sha256: str | None = None
    availability: Literal["available", "missing", "failed", "unavailable", "omitted", "not_applicable", "truncated", "unknown"] = "available"
    unavailable_reason: str | None = None
    capture_occurrence: int | None = Field(default=None, ge=1)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    step_id: str | None = None
    step_execution_id: str | None = None
    phase: StepPhase | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_serializer("path", when_used="json")
    def serialize_path(self, value: Path | None) -> str | None:
        return value.as_posix() if value is not None else None


class StepPhaseReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    step_id: str
    phase: StepPhase
    status: RunnerStatus
    duration_ms: int | None = Field(default=None, ge=0)
    started_at: datetime | None = None
    ended_at: datetime | None = None
    unavailable_reason: str | None = "unmeasured"
    failure_category: FailureCategory | None = None
    error_message: str | None = None
    artifact_refs: list[EvidenceArtifactRef] = Field(default_factory=list)
    harness_call_refs: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class RunnerStepResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    step_id: str
    source_step_id: str | None = None
    step_execution_id: str | None = None
    invocation_path: tuple[str, ...] = ()
    source_ref: SourceRef | None = None
    status: RunnerStatus
    action_status: RunnerStatus | None = None
    action_name: str | None = None
    kind: ExecutableStepKind | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None
    duration_ms: int | None = Field(default=None, ge=0)
    unavailable_reason: str | None = "unmeasured"
    phase_reports: list[StepPhaseReport] = Field(default_factory=list)
    attempt_index: int = Field(default=1, ge=1)
    max_attempts: int = Field(default=1, ge=1)
    failure_category: FailureCategory | None = None
    error_message: str | None = None
    evidence_errors: list[dict[str, Any]] = Field(default_factory=list)
    skip_reason: str | None = None
    blocked_by_step: str | None = None
    evidence_refs: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class RunnerEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: str | None = None
    sequence: int | None = Field(default=None, ge=1)
    event_type: RunnerEventType
    run_id: str
    step_id: str | None = None
    source_step_id: str | None = None
    step_execution_id: str | None = None
    invocation_path: tuple[str, ...] = ()
    attempt_index: int = Field(default=1, ge=1)
    phase: StepPhase | None = None
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    payload: dict[str, Any] = Field(default_factory=dict)


class EvidenceBundle(BaseModel):
    model_config = ConfigDict(extra="forbid")

    bundle_id: str
    run_id: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    schema_version: Literal["1.0", "fsq.evidence/v2"] = "fsq.evidence/v2"
    checkpoint_sequence: int = Field(default=0, ge=0)
    completeness: Literal["complete", "partial", "unavailable", "not_applicable"] = "partial"
    warnings: list[str] = Field(default_factory=list)
    manifest_path: Path | None = None
    events: list[RunnerEvent] = Field(default_factory=list)
    steps: list[RunnerStepResult] = Field(default_factory=list)
    planned_steps: list[ExecutableStep] = Field(default_factory=list)
    artifacts: list[EvidenceArtifactRef] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class EvidenceManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    bundle: EvidenceBundle


class EvidenceJournalRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["fsq.evidence-event/v1"] = "fsq.evidence-event/v1"
    sequence: int = Field(ge=1)
    event_id: str
    run_id: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    event: RunnerEvent | None = None
    step_result: RunnerStepResult | None = None
    planned_step: ExecutableStep | None = None

    @model_validator(mode="after")
    def validate_fact(self) -> "EvidenceJournalRecord":
        if sum(item is not None for item in (self.event, self.step_result, self.planned_step)) != 1:
            raise ValueError("Evidence journal record requires exactly one fact.")
        if self.event is not None and self.event.run_id != self.run_id:
            raise ValueError("Evidence journal Run identity mismatch.")
        if self.timestamp.tzinfo is None or self.timestamp.utcoffset() != UTC.utcoffset(self.timestamp):
            raise ValueError("Evidence journal timestamps must be UTC.")
        return self
