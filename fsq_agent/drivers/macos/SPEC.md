# Module: drivers.macos

## Purpose

Implement macOS desktop automation through optional Appium Mac2, including explicit session/application lifecycle, actions, compact semantic observations, assertions, and safe failure normalization.

## Dependencies

- `core.interfaces.MacOSDriverInterface`, `capabilities`, and macOS parameter/result models.
- Optional Appium/Selenium, imported lazily at runtime.

## Public Interface

Instances satisfy `MacOSDriverInterface`; the concrete backend class is private outside Drivers and composition.

`ui_snapshot`/`uiSnapshot` accepts an optional structured element query through `MacOSUiSnapshotParams`. Querying is a read-only observation in an explicitly started Mac2 session, not an AgentTool, a model call, or an implicit action. Omitting the query preserves the established tree response fields.

## Internal Structure

- Private Appium Mac2 backend implementation and macOS capability declarations.

## Python Architecture

- Architecture level: Level 2 Simple Package.
- Dependency direction: depends on Core Interfaces; never imports Harnesses or adapters.
- Rationale: one focused backend implementation is sufficient.

## Element Resolution

- Structured locator fields constrain the same element conjunctively. `name` retains semantic-name compatibility across backend identity/name/label/value signals, while explicit `label` and `value` constrain their respective attributes. A successful text lookup must not bypass an accompanying type, role, or identity constraint. Failed combined lookup never degrades to type-only or another weaker target. Explicit XPath and predicate selectors do not bypass additional semantic constraints.
- Literal values are safely encoded for each backend query language, including apostrophes, quotes, and backslashes. Backend-owned selector syntax is not assembled by interpolating unescaped user text.
- Element-targeted actions and assertions require a unique live match. Multiple distinct matching elements produce an ambiguity failure with bounded candidate summaries, not a first-match action. Candidate descriptions include available type, text, state, geometry, and complete usable locator signals; uncertain uniqueness never permits an action.
- Explicit coordinate-only targeting remains supported. Coordinates must not silently replace a failed semantic locator or bypass additional supplied element constraints. An element action may use the resolved element's current center; screenshot-inferred coordinates are not element evidence.
- Display-clipped labels and values are never advertised as complete exact-match locators. Complete locator values remain separate from bounded display previews; values exceeding response limits are explicitly marked unavailable rather than silently shortened or converted to broad prefix matches. Returned locator candidates are revalidated against live state before actions and do not promise cross-session stability.

## Structured Observation

- The optional query matches only decoded semantic text fields (identifier, name, label, value, and node text), with explicit exact/contains matching, case sensitivity, type and state filters. It does not search JSON keys, `XCUIElementType` strings as text, paths, or serialized envelope metadata. Filter fields combine conjunctively. Missing backend state is unknown, not affirmative evidence for a requested state.
- Queries scan the current backend tree before text clipping and display-depth truncation. They return a bounded flat candidate page rather than repeating the entire tree. Pagination identifies its snapshot revision; a continuation for a changed snapshot is rejected so pages from different UI states are not silently combined.
- Results include candidate type, bounded display text, available state and geometry, complete usable locator signals, snapshot identity, match count or an explicit lower bound, continuation information, and text/scan/response truncation indicators. Exhausted processing bounds produce incomplete coverage, never a false exhaustive zero-match claim. Queries do not click, scroll, change selection, infer prices, call Providers, or capture screenshots implicitly.
- Compact tree output preserves interactive element identity and useful state when present in the source. Display text remains bounded, with explicit clipping metadata; default-state omission is documented. Requested depth limits identify truncated children instead of implying a complete subtree. Parsing and scanning enforce resource bounds and reject DTD/entity declarations.
- Snapshot diagnostics distinguish backend source facts, compact-tree coverage, and structured-query coverage without embedding unrestricted raw source. A screenshot and a tree are separate observations and are not described as an atomic capture. No-match in a tree does not establish invisibility or non-purchasability; missing controls require a fresh bounded observation or an explicit limitation report.

## Error Handling

Existing normalized action/result and failure-category envelopes remain intact. Safe structured resolution reasons distinguish `not_found`, `ambiguous`, `invalid_locator`, `session_unavailable`, and `backend_error`; query continuations additionally distinguish stale snapshots and incomplete coverage. Syntax errors, session/transport failures, unsupported operations, and unexpected backend errors are not swallowed into ordinary target-not-found results. Diagnostics remain bounded and secret-safe, without raw backend stack traces or unrestricted request/response bodies.

## Verification Scope

- Offline verification covers literal escaping, conjunctive matching, ambiguity without side effects, safe error classification, bounded semantic querying, incomplete/stale observations, and display-preview versus exact-locator separation.
- Snapshot verification compares sanitized source, compact output, and query results for interactive nodes, duplicate labels, offscreen nodes, missing state, depth clipping, and malformed source. Real-browser success is not inferred from offline tests or screenshots alone.

## Current Invariants

- Construction and registry discovery do not import Appium/Selenium, connect to Appium, or launch an application.
- Session creation remains explicit through application lifecycle capabilities.
