# Windows Harness Skill

Use when `harness.platform` is Windows. This skill contains Windows-specific stability guidance; the active tool schema already defines callable names and arguments.

## Snapshot-First Rules

- Call `ui_snapshot` after launch and after state-changing actions when the next target is not already unambiguous.
- Prefer stable control locators (`automation_id`, then `title` + `control_type`) over `index` or visual guessing.
- Use `index` (1-based) only to disambiguate when multiple controls share the same locator fields.
- Do not infer that the window changed from a screenshot path alone. Use a fresh `ui_snapshot` or assertion after the action.
- Treat screenshots as evidence artifacts. They can support debugging, but they do not replace `ui_snapshot` for action targeting.
- If a target is stale or missing, refresh the snapshot once and retry the same semantic action with corrected schema-valid arguments.

## Control Locator Rules

- A control locator may combine `title`, `control_type`, `automation_id`, `class_name`, and `index`.
- `title` matches the control name; `control_type` is the UIA control type (for example `Button`, `Edit`, `Document`, `Tab`); `automation_id` is the stable UIA automation id; `class_name` is the native class.
- Extract `control_type` from the actual `ui_snapshot` output. Do not assume a control type from the displayed text.
- Prefer `automation_id` when available because it is the most stable. Fall back to `title` + `control_type` when it is not.

## Verification and Assertion Rules

- Use `assert_visible` for required presence of a control.
- Use `assert_with_ai` for required absence, visual judgment, window interpretation, or when a deterministic presence check would require a brittle complex locator.
- Use `ui_snapshot` to inspect, locate, or collect context before an assertion.

## Argument Rules

- Use the Windows key syntax accepted by the active schema for shortcuts, such as `^s` for Ctrl+S or `{ENTER}`, only when that shortcut is the requested semantic action.
- Mouse action `target` describes the step; use `locator`, `point`, or `offset` fields from the active schema for execution coordinates.
- `scrollOn.wheel_dist` is positive for up and negative for down.

## Correct Key Examples

Use one payload from the matching semantic action. Do not combine unrelated fields.

### `clickOn` with a control locator

```json
{
  "target": "Click Save",
  "locator": {
    "title": "Save",
    "control_type": "Button"
  }
}
```

### `pressKey: {key: ^s}`

```json
{
  "key": "^s"
}
```

### `typeText` with a runtime secret

```json
{
  "target": "Enter password",
  "locator": {
    "control_type": "Edit",
    "automation_id": "PasswordBox"
  },
  "text": "TEST_ACCOUNT_PASSWORD",
  "textType": "runtimeSecret"
}
```

### Mouse actions

```json
{"target": "Hover Save", "locator": {"title": "Save", "control_type": "Button"}}
```

```json
{"target": "Scroll results", "locator": {"automation_id": "Results"}, "wheel_dist": -5}
```

```json
{
  "target": "Move item",
  "source": {"locator": {"automation_id": "Item"}},
  "destination": {"offset": {"x": 120, "y": 0}},
  "mouse_button": "left"
}
```

## Tool Usage Error Recovery

- If a Windows tool validation fails, rebuild the payload from the active schema and the requested semantic action.
- If an action executes but the expected state is not present, take a fresh `ui_snapshot`, then decide whether retrying the same semantic action is justified.
- If a key action returns the wrong window state, do not count it. Retry the requested key/action with a schema-valid payload or report the mismatch.
- Before `assert_with_ai`, keep the window at the intended visual state.
- For `assert_with_ai`, use the returned verdict rather than deciding from screenshot existence.
