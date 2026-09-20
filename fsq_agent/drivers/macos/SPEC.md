# Module: drivers.macos

## Purpose

Implement macOS desktop automation through optional Appium Mac2, including explicit session/application lifecycle, actions, compact semantic observations, assertions, and safe failure normalization.

## Dependencies

- `core.interfaces.MacOSDriverInterface`, `capabilities`, and macOS parameter/result models.
- Optional Appium/Selenium, imported lazily at runtime.

## Public Interface

Instances satisfy `MacOSDriverInterface`; the concrete backend class is private outside Drivers and composition.

`ui_snapshot`/`uiSnapshot` returns the bounded compact current Mac2 control tree through `MacOSUiSnapshotParams`. It has no platform-specific element-query mode.

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
- `type_text` uses the resolved or active element input API when a readable element is available, applies requested clearing before input, and reads the element value after input. A value that does not reflect the requested text triggers one system-level `macos: keys` retry followed by another value check. A still-unsatisfied readable-element postcondition returns `action_error`; untargeted input without a readable active element retains system-level key delivery without claiming a value postcondition.

## Structured Observation

- Compact tree output preserves interactive element identity and useful state when present in the source. Display text remains bounded, with explicit clipping metadata; default-state omission is documented. Requested depth limits identify truncated children instead of implying a complete subtree. Parsing and scanning enforce resource bounds and reject DTD/entity declarations.
- Snapshot diagnostics distinguish backend source facts and compact-tree coverage without embedding unrestricted raw source. A screenshot and a tree are separate observations and are not described as an atomic capture. A compact tree is observation context rather than proof that an omitted control is absent.

## Error Handling

Existing normalized action/result and failure-category envelopes remain intact. Safe structured resolution reasons distinguish `not_found`, `ambiguous`, `invalid_locator`, `session_unavailable`, and `backend_error`. A readable-element text-entry postcondition that remains unsatisfied after fallback is an `action_error`. Syntax errors, session/transport failures, unsupported operations, and unexpected backend errors are not swallowed into ordinary target-not-found results. Diagnostics remain bounded and secret-safe, without raw backend stack traces or unrestricted request/response bodies.

## Verification Scope

- Offline verification covers literal escaping, conjunctive matching, ambiguity without side effects, safe error classification, text-entry value verification and fallback, and display-preview versus exact-locator separation.
- Snapshot verification compares sanitized source and compact output for interactive nodes, duplicate labels, offscreen nodes, missing state, depth clipping, and malformed source. Real-browser success is not inferred from offline tests or screenshots alone.

## Current Invariants

- Construction and registry discovery do not import Appium/Selenium, connect to Appium, or launch an application.
- Session creation remains explicit through application lifecycle capabilities.
