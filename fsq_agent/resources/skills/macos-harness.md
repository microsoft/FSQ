# macOS Harness Skill

Use when `harness.platform` is macOS. This skill contains macOS-specific stability guidance; the active tool schema already defines callable names and arguments.

## Case Lifecycle

- Start each macOS case with `launch_app` and set `new_session` to true so the case uses a fresh Mac2 session.
- End each macOS case with `kill_app` and set `close_session` to true so the Mac2 session is closed.
- When the case temporarily switches to another application and must switch back, call `launch_app` with the desired `bundle_id` and keep `new_session` false. This activates the application in the current Mac2 session without replacing it.

## Snapshot-First Rules

- Start app-owned workflows with `launch_app`; do not assume a Mac2 session exists before lifecycle setup.
- Call `ui_snapshot` after launch and after state-changing actions when the next target is not already unambiguous.
- Prefer stable macOS accessibility identifiers and names over coordinates.
- Use coordinate `point` values only as an explicit fallback when the target cannot be represented by accessibility metadata.
- Do not infer that the window changed from a screenshot path alone. Use a fresh `ui_snapshot` or assertion after the action.
- If a target is stale or missing, refresh the snapshot once and retry the same semantic action with corrected schema-valid arguments.

## Locator Rules

- A macOS locator may use `accessibilityId`, `name`, `label`, `value`, `role`, `controlType`, `className`, `xpath`, `predicate`, or `point`.
- Prefer `accessibilityId` when available because it is the most stable Appium Mac2 lookup.
- Use `name`, `label`, or `value` for controls that expose user-visible accessibility metadata.
- Use `role`, `controlType`, or `className` only when the current `ui_snapshot` confirms those fields.
- Use `xpath` or `predicate` only when simpler accessibility fields are absent or ambiguous.

## Verification and Assertion Rules

- Use `search_artifact` and `read_artifact_slice` only to inspect evidence and choose the next action.
- Finish every required verification step by calling `assert_visible`, `assert_elements_order`, or `assert_with_ai`.
- Do not treat a helper search match, artifact read, screenshot path, or `ui_snapshot` narration as completed verification.
- Use `assert_elements_order` when the requirement is about visual order, such as toolbar item sequence or vertical list ordering.
- For `assert_elements_order`, keep `expected_order` as zero-based indexes of the provided `elements`; omit it when the authored element list is already the expected order.
- Use `assert_visible` for required presence or visibility of a macOS accessibility element.
- Use `assert_with_ai` when deterministic accessibility assertions cannot express the requirement or would require a brittle complex locator.

## Argument Rules

- Treat `launch_app` and `kill_app` as lifecycle actions. Do not report them as satisfying a business key action unless the case explicitly tests app lifecycle.
- Use `new_session=true` on the case-opening `launch_app`; this closes any stale active session and creates the case session from the provided or configured `bundle_id` or `app_path`.
- During the case, omit `new_session` or set it to false when activating another application or switching back by `bundle_id`. Do not pass `app_path` or `arguments` when reusing the session.
- Reserve another `new_session=true` call for an explicit session reset. It replaces the current session and should not be used for ordinary application switching.
- Use `close_session=true` on the case-ending `kill_app`. This closes and clears the Mac2 session directly because Mac2 does not implement the separate application termination endpoint.

## Correct Key Examples

### Case-opening `launchApp` with a fresh session

```json
{
  "bundle_id": "com.example.TargetApp",
  "new_session": true
}
```

### In-case `launchApp` when switching back

```json
{
  "bundle_id": "com.example.TargetApp",
  "new_session": false
}
```

### Case-ending `killApp` with session closure

```json
{
  "bundle_id": "com.example.TargetApp",
  "close_session": true
}
```

### `clickOn` with an accessibility id

```json
{
  "locator": {
    "accessibilityId": "SearchField"
  }
}
```

### `clickOn` with a coordinate fallback

Use this only when accessibility metadata is unavailable:

```json
{
  "point": {
    "x": 120,
    "y": 240
  }
}
```

### `assertElementsOrder` for a horizontal toolbar

```json
{
  "direction": "horizontal",
  "elements": [
    {"target": "Back"},
    {"target": "Forward"},
    {"target": "Share"}
  ]
}
```

### `typeText` with a runtime secret

```json
{
  "target": "Password field",
  "text": "TEST_ACCOUNT_PASSWORD",
  "textType": "runtimeSecret"
}
```

## Tool Usage Error Recovery

- If a macOS tool validation fails, rebuild the payload from the active schema and the requested semantic action.
- If an action executes but the expected state is not present, take a fresh `ui_snapshot`, then decide whether retrying the same semantic action is justified.
- If a key action returns the wrong window state, do not count it. Retry the requested action with a schema-valid payload or report the mismatch.
- Before `assert_with_ai`, keep the window at the intended visual state.
- For `assert_with_ai`, use the returned verdict rather than deciding from screenshot existence.