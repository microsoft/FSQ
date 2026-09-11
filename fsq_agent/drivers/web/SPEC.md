# Module: drivers.web

## Purpose

Implement replay-first Web automation through optional Playwright: explicit owned browser/page lifecycle, self-contained locators, structured observations, ordinary interactions, assertions, and safe backend failure normalization. The backend does not own Run persistence, recording policy, model prompting, or an MCP server.

## Dependencies

- `core.interfaces.WebDriverInterface`, `capabilities`, and Web parameter/result models.
- Optional Playwright, imported lazily at runtime.

## Public Interface

Instances satisfy `WebDriverInterface`; the concrete backend class is private outside Drivers and composition.

Catalog-declared capabilities cover browser/page lifecycle, navigation/back/forward/reload, click/hover/drag/scroll, replace-style fill, sequential text input, checked state, native option selection, scoped keyboard, conditional waits, deterministic visibility/text/state/value assertions, screenshots, structured observations, and trigger-bound popup/dialog handling. File upload and file-chooser handling are not exposed. AI assertions retain the injected evaluator contract. Observation and diagnostic capabilities are not included in generated action recordings.

`close()` is the runtime-owner disposal operation. It releases an owned browser/Playwright session and shuts down the owned executor, is safe to repeat after successful disposal, and does not initialize an unopened browser. This is not the recordable `close_browser` capability and produces no Case command or evidence of an authored action.

## Internal Structure

- `_playwright.py`: private backend facade, single-worker ownership, and catalog-backed capability declarations.
- Private locator compilation/native-selector adaptation, structured observation, and browser-state/event modules keep backend objects inside this package.

## Python Architecture

- Architecture level: Level 2 Simple Package.
- Public API: `WebDriverInterface` and shared Models values; concrete backend and helpers remain private.
- Internal modules: backend facade, locator/native adaptation, observations, and browser-state/event helpers.
- Domain boundaries: browser automation, observation production, and backend failure normalization only.
- Boundary models: shared Web locators, parameters, observations, diagnostics, and effect facts come from `models`.
- Dependency direction: depends on Core Interfaces; never imports Harnesses or adapters.
- Rationale: a focused backend package isolates browser mechanisms without another application layer or dependency-injection framework.

## Locator And Replay Contract

Element operations receive a `WebLocator` containing an explicit page alias and an ordered path of semantic/selector, conjunctive filter, position, and frame-entry steps. Two-target operations use the same contract for both endpoints. There are no ref targets, free-text fallback targets, session-local target IDs, or implicit root/coordinate fallbacks.

Semantic text matching defaults to exact. Explicit first/last/nth selection is evaluated after preceding filters on every invocation; it is not replaced with a fixed element identity. Invalid paths, frame ambiguity, and multiple final action targets fail before effects. Bounded readiness waits precede final resolution and do not replace Playwright actionability checks. Hidden/detached/absence assertions and waits can succeed with zero matches according to their condition.

Locator generation uses real node/accessibility/attribute information and Playwright normalization, not parsing a displayed name into guessed DOM selectors. Each exposed generated locator is checked against its source element; quality information distinguishes structural/positional dependence from semantic signals without promising stability across arbitrary page changes. Display compaction never changes locator values or turns ellipses into prefix matching.

Locator strings are not rewritten merely because they contain credential-shaped words. Existing persistence boundaries omit a whole locator if it contains a configured private value and report it unavailable; descriptive fields retain existing sanitization.

The pinned native-selector integration is isolated in a private adapter. It uses `Locator.normalize()` and narrowly contained selector extraction where the Python public API lacks serialization. It never parses `repr()`, evaluates generated code strings, or exposes backend objects outside Drivers. Unsupported native behavior fails explicitly. Native temporary bookkeeping is not an FSQ ref registry and is not required to execute a supplied locator.

The backend prepares the complete replayable locator/options/event contract before the first side-effectful primitive. Failed preparation has no action effects. Canonical supplied query rules are preserved; secret-resolved execution text is never returned as replay parameters.

## Structured Observation

`ui_snapshot` supports compact page, locator-scoped, and full-artifact views. `find_elements` returns bounded live query results with locators and parent context. `inspect_element` exposes bounded control details, including distinct native option labels, values, indices, and state. These capabilities observe without performing UI actions.

Observation production collects semantic structure and relevant nodes, generates/checks locators for exposed regions and controls, then compacts display data. It does not merely remove ref annotations from raw ARIA text. Named and unnamed controls remain distinguishable through real scope/attribute information.

Views contain page alias/URL/title, region outline, current modal/focus context where available, element role/name/state, complete locators or explicit locator-unavailable reasons, list summaries/order, and coverage/continuation facts. Snapshot identities and cursors are evidence/pagination metadata, not action targets. Incomplete virtualized content and unknown counts remain explicit.

The inline observation body is at most 12,000 characters. A smaller request budget is supported; full views do not bypass the inline bound. Oversized display content is summarized or omitted with a reason and full-evidence reference; locators are never truncated. Only exposed controls/regions are normalized eagerly. Rendered depth limits do not claim to bound native DOM traversal cost.

Full semantic evidence and locator coverage are distinguished. Text-only fallback identifies reduced coverage and unavailable locators rather than inventing targets. Continuations remain bound to their observation and reject invalidated state. Applicable post-action observations are reusable without a redundant same-phase full capture. No incremental/diff snapshot protocol is exposed.

## Stateful Interactions

Owned pages use declared aliases, not live tab indices. Browser startup creates the `main` page; explicit page creation or popup expectations introduce other unique aliases. Closing invalidates the corresponding owned state.

Popup/dialog expectations are attached to a triggering click or key/submit operation. Listeners are installed before the trigger; dialogs specify expected type and accept/dismiss behavior. Event timeout or unexpected modal state does not prove the trigger had no effects and never causes automatic retriggering.

An unrepresentable unsolicited blocking modal is a context failure, not silently accepted. No upload operation or runtime file-resolution service is introduced.

## Error Handling

Backend failures use the shared failure categories plus precise safe Web reason codes, parameter paths, scope, match/actionability information, bounded candidates, and action-effect state (`not_started`, `completed`, or `indeterminate`). Invalid selectors, missing/ambiguous targets, closed scope, timeout, and interaction failure remain distinguishable.

Cancellation and primary action failure are preserved in Driver results. A Driver-side observation failure does not erase a completed action or justify a retry. These diagnostics use existing result metadata rather than a new framework-level execution or recording state machine. Arbitrary script execution, silent semantic repair, file upload, and generic bulk execution are not exposed.

## Verification Scope

Real pinned-Playwright fixtures establish locator generation/serialization, duplicate and unnamed controls, explicit ordering under list changes, frames, event ordering, and Driver diagnostics. Recording through the existing pipeline and deterministic replay in a fresh context need no LLM or observation cache. Structured output budgets, omission truth, unmodified locator values, and strict schema examples are executable obligations.

## Current Invariants

- Construction and registry discovery do not import Playwright or launch a browser.
- `start_browser` and `close_browser` remain explicit, idempotent capabilities.
- Other page operations require an active declared page and never start one implicitly.
- Driver-owned backend objects and transient native IDs never become Case parameters or model-facing targets.
- Tool descriptions explain operation choice, defaults, argument exclusivity, side effects, and safe recovery; shared parameter schemas reach capability consumers unchanged.
