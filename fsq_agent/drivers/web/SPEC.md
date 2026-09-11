# Module: drivers.web

## Purpose

Implement Microsoft Edge automation through optional Playwright over CDP, including explicit borrowed-browser connection lifecycle, semantic page actions, observations, assertions, and safe backend failure normalization.

## Dependencies

- `core.interfaces.WebDriverInterface`, `capabilities`, and Web parameter/result models.
- Optional Playwright, imported lazily at runtime.
- An operator-started Microsoft Edge CDP endpoint at `http://127.0.0.1:9222`.

## Public Interface

Instances satisfy `WebDriverInterface`; the concrete backend class is private outside Drivers and composition.

`close()` is the runtime-owner disposal operation. It closes the FSQ-owned page, disconnects Playwright from the borrowed Edge browser, and shuts down the owned executor. It never launches or exits Edge, is safe to repeat after successful disposal, and does not initialize an unopened connection. This is not the recordable `close_browser` capability and produces no Case command or evidence of an authored action.

## Internal Structure

- Private Playwright backend implementation and Web capability declarations.

## Python Architecture

- Architecture level: Level 2 Simple Package.
- Dependency direction: depends on Core Interfaces; never imports Harnesses or adapters.
- Rationale: one focused backend implementation is sufficient.

## Current Invariants

- Construction and registry discovery do not import Playwright or launch a browser.
- Edge is the only supported Web browser.
- `start_browser` attaches to Edge at `http://127.0.0.1:9222` and creates one FSQ-owned page in the existing browser; it never starts a browser process.
- `close_browser` closes only the FSQ-owned page and disconnects Playwright; it never closes the borrowed Edge browser process or unrelated pages.
- `start_browser` and `close_browser` remain explicit, idempotent capabilities.
