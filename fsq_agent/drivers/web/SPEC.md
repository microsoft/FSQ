# Module: drivers.web

## Purpose

Implement Web automation through optional Playwright, including explicit browser lifecycle, semantic page actions, observations, assertions, and safe backend failure normalization.

## Dependencies

- `core.interfaces.WebDriverInterface`, `capabilities`, and Web parameter/result models.
- Optional Playwright, imported lazily at runtime.

## Public Interface

Instances satisfy `WebDriverInterface`; the concrete backend class is private outside Drivers and composition.

`close()` is the runtime-owner disposal operation. It releases an owned browser/Playwright session and shuts down the owned executor, is safe to repeat after successful disposal, and does not initialize an unopened browser. This is not the recordable `close_browser` capability and produces no Case command or evidence of an authored action.

## Internal Structure

- `_playwright.py`: Private Playwright lifecycle, semantic locator compilation, action execution, default ARIA snapshot normalization, text fallback, and safe failure normalization.
- `__init__.py`: Private package exports used by Drivers composition.
- `SPEC.md`: Module contract.

## Python Architecture

- Architecture level: Level 2 Simple Package.
- Public API: instances satisfy the Core-owned `WebDriverInterface`; the concrete driver remains private.
- Domain boundaries: Models owns parameter validation, Core owns the driver protocol and platform-neutral execution, and this module owns Playwright calls, semantic target resolution, and snapshot normalization.
- Boundary models: Web Pydantic parameter models enter through decorated driver methods; results are serializable action or observation mappings.
- Dependency direction: depends on Core Interfaces; never imports Harnesses or adapters.
- Rationale: one focused backend implementation is sufficient.

## Error Handling

Page-dependent operations fail clearly when the browser is not started. Semantic target resolution distinguishes missing targets, ambiguous parent or final matches, out-of-range indexes, hidden targets, detached targets, assertion failures, timeouts, and backend interaction failures. Diagnostics contain bounded backend messages, normalized locator parameters, and useful match counts without exposing snapshot refs, full DOM content, unbounded body text, credentials, or arbitrary page state.

## Current Invariants

- Construction and registry discovery do not import Playwright or launch a browser.
- `start_browser` and `close_browser` remain explicit, idempotent capabilities.
- Element interactions compile model-validated `WebLocator` values directly to Playwright role locators. An optional `within` scope is resolved first and must identify exactly one visible semantic container; the target then resolves by role and optional name, and an optional zero-based index is range-checked and applied to the final result set.
- Complete accessible names use whole-string case-sensitive Playwright matching. A name ending in literal `...` is matched as an escaped, anchored, case-sensitive prefix after removing the suffix.
- The driver stores no snapshot reference map, element handle, or observation state needed by a later action. A validated locator executes in a fresh browser session without a preceding `ui_snapshot` call.
- `select_option` resolves exact visible labels in authored order. One label supports single selection; multiple labels require a multiple-select target.
- `wait_for` executes exactly one URL or semantic-element condition. Hidden and detached waits treat no current match as satisfied, reject indexed locators, and fail on ambiguous parent or target matches.
- `assert_not_visible` passes when no visible target exists, including when its unique parent scope is absent, and fails if any target match is visible. It rejects indexes and treats multiple visible parent scopes as ambiguous.
- `assert_text` reads Playwright `inner_text()` from one semantic target or the current page body and applies exactly one literal substring or complete-string comparison without additional normalization. Failure output is bounded.
- `ui_snapshot` calls Playwright `aria_snapshot(mode="default")` and never emits or caches snapshot refs. It preserves role tokens, hierarchy, indentation, state markers, quoting, and escaping while compacting decoded accessible names and semantic text values longer than 100 Unicode characters to the first 100 characters plus literal `...`.
- Snapshot compaction uses a quote-aware scalar scanner for the supported Playwright snapshot grammar and re-encodes compacted values without blindly slicing serialized text. Empty ARIA output or a normalization result that cannot preserve valid syntax returns the existing ref-free URL/title/body-text fallback instead of malformed ARIA content.
- Normalized ARIA results retain `url`, `snapshot_type`, `snapshot`, `coverage`, and `truncated`; `truncated` reports semantic compaction and coverage explains when the emitted text is not byte-for-byte backend output.
