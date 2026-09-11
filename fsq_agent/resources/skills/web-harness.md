# Web Harness Skill

Use when `harness.platform` is Web. This skill contains Web-specific stability guidance; the active tool schema already defines callable names and arguments.

## Snapshot-First Rules

- The operator owns the Microsoft Edge process and starts it with remote debugging enabled on `127.0.0.1:9222`.
- Start Web workflows with `start_browser`, which only attaches to the existing Edge process and creates an FSQ-owned tab. Never launch, restart, or replace Edge.
- Close Web workflows with `close_browser` as the final lifecycle action. It closes only the FSQ-owned tab and disconnects automation; it never exits Edge. For multi-cycle workflows, call `close_browser` before the next `start_browser` cycle.
- Call `ui_snapshot` after navigation and after state-changing actions when the next target is not already unambiguous.
- Prefer replayable semantic locators and stable selectors over snapshot `ref` values, coordinates, or visual guessing.
- Do not infer that a page changed from a screenshot path alone. Use a fresh snapshot or assertion after the action.
- Treat screenshots as evidence artifacts. They can support debugging, but they do not replace `ui_snapshot` for action targeting.
- If a target is stale or missing, refresh the snapshot once and retry the same semantic action with corrected schema-valid arguments.

## Verification and Assertion Rules

- Use `assert_text` for deterministic page text or field text requirements.
- Use `assert_visible` or `assert_not_visible` for required presence or absence of page elements.
- Use `assert_with_ai` when the assertion requires visual judgment, page interpretation, or a deterministic check would require a brittle complex selector.
- Use `ui_snapshot` to inspect, locate, or collect context before an assertion.

## Argument Rules

- Use `wait_for` for waits so waiting does not change page state.
- Prefer waiting for a semantic page element that proves readiness. A URL wait confirms navigation only, not that page content has rendered.
- URL waits use Playwright glob syntax: `*` does not cross `/`; use `**` when the match must span URL path separators.
- Do not use shell commands, `Alt+F4`, `Control+W`, or other mechanisms to launch or close Edge. Use `start_browser` and `close_browser` only for the FSQ attachment lifecycle.

## Correct Key Examples

Use one payload from the matching semantic action. Do not combine unrelated fields.

### `pressKey: {key: Enter}`

```json
{
  "key": "Enter"
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

### `waitFor` for rendered content (preferred)

```json
{
  "locator": {"role": "main", "name": "Search Results"},
  "state": "visible",
  "timeout_ms": 15000
}
```

### `waitFor` for URL navigation

```json
{
  "url": "**/search**",
  "timeout_ms": 15000
}
```

Do not use `*example.com/search*` to match a full URL; the single `*` cannot cross the `/` characters in `https://example.com/search`.

## Tool Usage Error Recovery

- If a Web tool validation fails, rebuild the payload from the active schema and the requested semantic action.
- If an action executes but the expected state is not present, take a fresh `ui_snapshot`, then decide whether retrying the same semantic action is justified.
- If a key action returns the wrong page state, do not count it. Retry the requested key/action with schema-valid payload or report the mismatch.
- Before `assert_with_ai`, use `wait_for` for required waits and keep the page at the intended visual state.
- For `assert_with_ai`, use the returned verdict rather than deciding from screenshot existence.