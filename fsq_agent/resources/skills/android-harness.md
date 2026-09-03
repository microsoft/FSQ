# Android Harness Skill

Use when `harness.platform` is Android. This skill contains Android-specific stability guidance; the active tool schema already defines callable names and arguments.

## Case Lifecycle

- Start each Android case with `launch_app` before the main business path.
- Collect the final required verification before teardown.
- End each Android case with `kill_app` after final evidence is collected.
- Do not report lifecycle actions as satisfying a business key action unless the case explicitly tests launch or kill behavior.

## Observation and Locator Rules

- Use `ui_snapshot` as the Android structural observation for locating current elements and resolving target ambiguity.
- Prefer locator fields confirmed in current output in this order: `resourceId`, `accessibilityId`, exact visible `text`, then `className` or `xpath` when simpler fields are absent or ambiguous.
- Use coordinate taps only when current platform evidence or the user explicitly supplies the point and no reliable locator is available; prefer locator-based actions for normal UI elements.
- Re-evaluate stale or missing targets with a fresh `ui_snapshot` before retrying the same semantic action.
- Do not invent abstract Android targets such as an outside blank area. If a menu or dialog must be dismissed and no concrete target is exposed, use the requested semantic key action such as `Back`, then verify the UI state.
- For Android key actions, use the requested semantic key string only. Do not mix key names with backend-native key codes.

## Verification and Assertion Rules

- Use `assert_state` for deterministic element state or text checks, such as verifying that `com.microsoft.emmx:id/url_bar` contains or equals a required URL or keyword.
- Use `assert_visible` or `assert_not_visible` for required presence or absence of visible UI elements.
- Use `assert_with_ai` for visual/page-content assertions or when the only deterministic option is a brittle complex locator, such as a long XPath through repeated generic controls or reused switch ids.
- Use `ui_snapshot` to inspect, locate, or collect evidence before an assertion. Before teardown, collect the final required verification with an assertion tool when an assertion-capable locator or text condition is available.

## Argument Rules

- Do not use gestures, close buttons, or app lifecycle cleanup as proof that a required `pressKey` action succeeded.
- Use `wait_ms` for FSQ pauses or page-load delays so waiting does not change UI state.

## Correct Key Examples

Use one payload from the matching semantic action. Do not combine semantic keys with backend-native key codes.

### `pressKey: {key: Back}`

```json
{
  "key": "Back"
}
```

### `pressKey: {key: Enter}`

```json
{
  "key": "Enter"
}
```

### `inputText` with a runtime secret

```json
{
  "target": "Password field",
  "text": "TEST_ACCOUNT_PASSWORD",
  "textType": "runtimeSecret"
}
```

### Invalid mixed key call

Do not call `press_key` with conflicting identities:

```json
{
  "key": "BACK",
  "keyCode": 66
}
```

## Tool Usage Error Recovery

- If `press_key` validation fails, rebuild the payload from the active schema and requested semantic key.
- If a `pressKey` action returns the wrong key result, do not count it. Retry the requested key with the schema-valid payload.
- After retrying a key action, verify UI state with fresh page source, visible text, or screenshot evidence.
- Before `assertWithAI`, use `wait_ms` for required pauses and keep the page at the intended visual state.
- For `assertWithAI`, do not decide from a screenshot path alone; call `assert_with_ai` and use its verdict.
