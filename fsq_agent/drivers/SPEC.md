# Module: drivers

## Purpose

Own concrete platform automation backends for Android, Web, Windows, and macOS. Drivers implement public Core interfaces, perform backend actions and observations, normalize backend failures, and keep optional backend imports lazy. Drivers do not own complete execution, harness runtime context, transport behavior, workspace policy, or host runtime installation.

## Dependencies

- `core.interfaces`: public driver, observation, evaluator, and artifact-related protocols.
- `capabilities`: neutral declaration decorators and discovery contracts.
- `models`: platform parameters, capability metadata, normalized results, and errors.

Drivers must not import adapters, Application, Execution, concrete harnesses, Config persistence, environment providers, or provider session construction.

## Public Interface

The root package exports no concrete backend classes. Each platform package is a canonical implementation owner consumed through `core.interfaces` and composition factories. Driver capability metadata discovery is available through a narrow package-internal composition boundary; callers do not import concrete backend modules merely to inspect capability definitions.

Every factory-created Driver implements the public idempotent `close()` disposal contract inherited from `DriverObservationInterface`. Disposal releases only owned connections, sessions, and worker resources and never constructs an unopened backend or stops a shared host service. Web disposal closes the owned browser/Playwright session and executor; Windows disposal shuts down its owned worker; macOS disposal releases its owned Appium session. Android disposal does not stop/restart ADB, uninstall automation services, or terminate the application. Disposal is distinct from recordable `close_browser`, `kill_app`, or other lifecycle capabilities and creates no execution or replay record.

## Internal Structure

The sole external importer of `_factory._DriverFactoryImplementation` is `core.interfaces._factories`, implementing the stable lazy DriverFactory wrapper. Driver implementations consume Core protocols, not that wrapper.

Metadata-only composition has separate named importers: `core._default_capabilities` may use `_factory._driver_class_for_backend` and `_capabilities._discover_driver_capability_definitions`; private Android/Web/Windows/macOS Harness modules may use `_capability_matches`, `_discover_driver_capability_definitions`, `_schema_from_capability_definition`, and `_with_driver_metadata` against injected instances. Compatibility `core.harness._driver_tools` aliases the canonical metadata module without another implementation. These exceptions permit no arbitrary private access or backend construction/connection during metadata inspection.

- `android/`: uiautomator2 backend and Android action/observation implementation.
- `web/`: Playwright backend and browser/page lifecycle implementation.
- `windows/`: pywinauto backend and Windows application/window implementation.
- `macos/`: Appium Mac2 backend and session/control-tree implementation.
- Private shared modules: driver capability declarations/discovery and provider-neutral AI assertion backend support.

## Python Architecture

- Architecture level: Level 2 Simple Package with platform subpackages.
- Public API: public Core driver interfaces, not concrete backend classes.
- Internal modules: concrete backend classes, decorators, discovery helpers, and AI assertion mixins.
- Domain boundaries: platform automation and backend error normalization only.
- Boundary models: action params and normalized results come from `models`.
- Dependency direction: harnesses receive drivers through Core interfaces; Drivers never import Harnesses.
- Rationale: each platform backend is focused infrastructure and does not require an application or domain layer.

## Error Handling

Missing optional backend libraries and unsupported hosts produce normalized unavailable/unsupported runtime outcomes when the backend is constructed or invoked, not package import failures. Backend command output and exceptions are bounded and secret-safe.

## Current Invariants

- Registry bootstrap, strict parsing, and driver construction remain lazy and do not connect to devices, launch browsers/apps, or start sessions.
- Browser/app lifecycle remains explicit through declared capabilities.
- Concrete drivers satisfy `core.interfaces` protocols without being public cross-module types.
- Capability metadata, rather than action-name switches in Runner or Harnesses, remains the execution source of truth.
