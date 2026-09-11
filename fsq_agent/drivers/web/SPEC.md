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

- Private Playwright backend implementation and Web capability declarations.

## Python Architecture

- Architecture level: Level 2 Simple Package.
- Dependency direction: depends on Core Interfaces; never imports Harnesses or adapters.
- Rationale: one focused backend implementation is sufficient.

## Current Invariants

- Construction and registry discovery do not import Playwright or launch a browser.
- `start_browser` and `close_browser` remain explicit, idempotent capabilities.
