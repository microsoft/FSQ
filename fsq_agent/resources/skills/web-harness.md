# Web Harness Skill

Use when `harness.platform` is Web. This skill contains Web-specific stability guidance; the active tool schema already defines callable names and arguments.

## Snapshot-First Rules

- Start Web workflows with `start_browser`, then navigate with `navigate_to`. Do not treat navigation as browser startup.
- Close Web workflows with `close_browser` as the final lifecycle action. For multi-cycle workflows, call `close_browser` before the next `start_browser` cycle.
- Use `ui_snapshot` for semantic page inspection. It returns ref-free role/name hierarchy and does not create state required by later actions.
- Construct element actions only from schema-valid role/name locators. Prefer a unique role/name, then a unique `within` scope, and use `index` only as an order-sensitive last resort.
- Refresh the snapshot after relevant state changes or when a target is stale or missing. Do not retry an ambiguous locator unchanged.
- Do not infer that a page changed from a screenshot path alone. Use a fresh snapshot or assertion after the action.
- Treat screenshots as evidence artifacts. They can support debugging, but they do not replace `ui_snapshot` for action targeting.

## Verification and Assertion Rules

- Use locatorless `assert_text` for literal page-body text and a semantic locator for element text. Text is not an element-interaction locator.
- Use `assert_visible` or `assert_not_visible` for required presence or absence of page elements.
- Use `assert_with_ai` when the assertion requires visual judgment, page interpretation, or a deterministic check would require a brittle complex selector.
- Use `ui_snapshot` to inspect, locate, or collect context before an assertion.

## Argument Rules

- Use `wait_for` for URL or semantic-element conditions and `wait_ms` only for deliberate elapsed time.
- Prefer waiting for a semantic page element that proves readiness. A URL condition confirms navigation only, not that page content has rendered.
- URL waits use Playwright glob syntax: `*` does not cross `/`; use `**` when the match must span URL path separators.
- Use visible option labels for `select_option`.
- Do not use `Alt+F4`, `Control+W`, or other key presses as browser lifecycle controls. Use `close_browser`.

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
  "locator": {"role": "textbox", "name": "Password"},
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
- If an action executes but the expected state is not present, take a fresh `ui_snapshot`, then decide whether a corrected semantic action is justified.
- If a key action returns the wrong page state, do not count it. Retry the requested key/action with schema-valid payload or report the mismatch.
- Before `assert_with_ai`, use `wait_for` for required waits and keep the page at the intended visual state.
- For `assert_with_ai`, use the returned verdict rather than deciding from screenshot existence.