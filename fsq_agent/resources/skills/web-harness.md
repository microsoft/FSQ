# Web Harness Skill

Use when `harness.platform` is Web. The active tool schema is the argument authority; examples below are validated against that schema.

## Observe once, then act on replayable locators

- Call `start_browser` explicitly before page operations and `close_browser` at the workflow's intended end. They are idempotent; navigation never starts a browser. Do not use browser-closing keyboard shortcuts as lifecycle operations.
- Every target contains an owned `page` alias and ordered `steps`. Copy the complete returned locator, not the displayed name, observation ID, position summary, screenshot coordinate, or a ref. Old Web target strings, locator bags, refs, `clear`, `double`, and implicit keyboard/page scope are unsupported.
- Inspect the structured post-action observation already returned with evidence. Do not request another full-page snapshot merely because an action completed.
- Use the region outline and its locators to expand the relevant scope. `find_elements` searches live collections; `inspect_element` explains one control and native options. Historical `search_artifact`/`read_artifact_slice` are for archived evidence, not the default live-navigation loop.
- Read `coverage` and omissions. Full semantic evidence does not imply all node locators were generated or that virtualized items were loaded. Unavailable locators are descriptive only. Structural/positional quality is not a promise of stability.
- `full` snapshots remain bounded inline and provide artifact-backed complete available evidence. Observation bodies are capped at 12,000 characters and Web tool responses at 16,000. Smaller `max_chars` is an upper bound, not permission to truncate a locator. The current Playwright backend does not issue continuation cursors; narrow the scope instead of inventing one.
- Exact string matching is the default. Do not turn a displayed ellipsis into a prefix query. Resolve ambiguity by adding intended scope/filters; never insert `first` just to silence an error.

## Choose the intended operation

Use `fill_text` for replacement or clearing, `type_text` for sequential append, and `set_checked` for the desired checkbox/radio state. Native selects use exactly one `selection.kind`; labels, values, and zero-based indices are different. Inspect options rather than guessing. Custom dropdowns use ordinary click/query operations.

For collection intent, filter eligible items first, then apply explicit `first`, `last`, or `nth`. Preserve that rule on replay instead of freezing a product ID. Enter frames with an explicit `enter_frame` step after uniquely selecting the iframe; continue with an element selector inside it.

Pages use declared aliases: `main`, aliases introduced by `open_page`, or new popup aliases. Use `list_pages`, `activate_page`, and `close_page`, never tab indices. Declare popup/dialog `expect` on the triggering click/key. The listener precedes the trigger; a separate "click, then handle dialog" sequence can deadlock. File upload is unsupported; do not invent file-chooser or host-path workarounds.

Prefer condition waits that prove application readiness. `wait_for` is not sleep; pure elapsed time uses `wait_ms`. URL conditions use exact or explicit substring matching, not globs or regexes. Navigation/load completion alone does not prove rendered application state.

Use deterministic `assert_visible`, `assert_not_visible`, `assert_text`, `assert_state`, or `assert_value` for expressible requirements. Absence can legitimately mean zero matches. Use `assert_with_ai` only for an explicitly requested visual/interpretive assertion; trust its verdict, not screenshot existence.

## Schema-checked examples

The names, URLs, and aliases are examples, not assertions about a particular site's markup. Replace them with observed locators and intended workflow values.

### `ui_snapshot` compact page observation

```json
{"scope":{"kind":"page","page":"main"},"max_items":25,"max_chars":6000}
```

### `ui_snapshot` expand a relevant region

```json
{"scope":{"kind":"element","target":{"page":"main","steps":[{"kind":"role","role":"region","name":"Filters"}]}},"view":"scoped"}
```

### `find_elements` live collection

```json
{"target":{"page":"main","steps":[{"kind":"role","role":"button","name":"Add to cart","disabled":false}]},"max_items":10}
```

### `inspect_element` scoped unnamed native select

```json
{"target":{"page":"main","steps":[{"kind":"role","role":"region","name":"Size"},{"kind":"role","role":"combobox"}]},"max_options":30}
```

### `click_on` exact semantic target

```json
{"target":{"page":"main","steps":[{"kind":"role","role":"button","name":"Save"}]}}
```

### `click_on` first eligible result, evaluated again on every replay

```json
{
  "target": {
    "page": "main",
    "steps": [
      {"kind":"role","role":"list","name":"Search results"},
      {"kind":"role","role":"listitem"},
      {"kind":"filter","has":{"steps":[
        {"kind":"role","role":"button","name":"Add to cart","disabled":false},
        {"kind":"filter","visible":true}
      ]}},
      {"kind":"first"},
      {"kind":"role","role":"button","name":"Add to cart","disabled":false}
    ]
  }
}
```

### `fill_text` replace editable content

```json
{"target":{"page":"main","steps":[{"kind":"role","role":"textbox","name":"Search"}]},"text":"shoes"}
```

### `fill_text` unresolved runtime secret

```json
{"target":{"page":"main","steps":[{"kind":"label","text":"Password"}]},"text":"TEST_ACCOUNT_PASSWORD","textType":"runtimeSecret"}
```

### `type_text` append with sequential key events

```json
{"target":{"page":"main","steps":[{"kind":"role","role":"textbox","name":"Search"}]},"text":" blue","delay_ms":20}
```

### `set_checked` idempotent desired state

```json
{"target":{"page":"main","steps":[{"kind":"role","role":"checkbox","name":"In stock"}]},"checked":true}
```

### `select_option` displayed labels

```json
{"target":{"page":"main","steps":[{"kind":"label","text":"Size"}]},"selection":{"kind":"label","labels":["Size 10"]}}
```

### `select_option` actual native values

```json
{"target":{"page":"main","steps":[{"kind":"label","text":"Size"}]},"selection":{"kind":"value","values":["size-10"]}}
```

### `select_option` explicit zero-based index

```json
{"target":{"page":"main","steps":[{"kind":"label","text":"Size"}]},"selection":{"kind":"index","indices":[0]}}
```

### `press_key` submit from an explicit element

```json
{"scope":{"kind":"element","target":{"page":"main","steps":[{"kind":"role","role":"textbox","name":"Search"}]}},"key":"Enter"}
```

### `press_key` intentional current-focus page key

```json
{"scope":{"kind":"page","page":"main"},"key":"Escape"}
```

### `wait_for` rendered readiness

```json
{"condition":{"kind":"element","target":{"page":"main","steps":[{"kind":"role","role":"heading","name":"Results"}]},"state":"visible"},"timeout_ms":15000}
```

### `wait_for` explicit URL substring

```json
{"condition":{"kind":"url","page":"main","url":"/search","exact":false},"timeout_ms":15000}
```

### `click_on` trigger-bound popup alias

```json
{"target":{"page":"main","steps":[{"kind":"role","role":"link","name":"Details"}]},"expect":{"kind":"popup","page":"details","timeout_ms":10000}}
```

### `click_on` trigger-bound prompt with a secret reference

```json
{"target":{"page":"main","steps":[{"kind":"role","role":"button","name":"Approve"}]},"expect":{"kind":"dialog","dialog_type":"prompt","action":"accept","prompt_text":{"text":"APPROVAL_CODE","textType":"runtimeSecret"}}}
```

### `assert_text` deterministic result requirement

```json
{"target":{"page":"main","steps":[{"kind":"role","role":"status","name":"Search status"}]},"text":{"kind":"contains","value":"results"}}
```

## Recover without duplicating effects

Read the failure category, precise reason, parameter path, candidates, and `action_effect`. For a parameter error, rebuild the request from the active schema. For a not-started missing/ambiguous target, refresh or scope the relevant observation and correct the locator; keep the requested semantics.

`completed` means the interaction happened even if later capture failed. `indeterminate`, event timeout, unexpected dialog/popup, or a partial operation does not mean "safe to repeat." Observe the relevant state before deciding anything further; do not blindly retrigger or fabricate cleanup. Missing effect information is unknown.

Do not remove required assertions, invent fallback parameters, or silently replace the requested UI flow with direct URL manipulation. Review generated Cases against the requested workflow and preserve complete locators and authored selection rules. Static Case validation alone is not evidence of successful replay.
