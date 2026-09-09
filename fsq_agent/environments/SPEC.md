# Module: environments

## Purpose

Own host/runtime discovery, support classification, read-only readiness checks, and target discovery. Platform automation, package installation, and Workspace persistence are outside this module.

## Dependencies

- `models`: runtime status facts and pure Web channel identity contracts.
- Python standard-library host, filesystem, import, and subprocess facilities.

The module must not import Application, adapters, Config persistence, concrete drivers or harnesses, LLM providers, or Execution.

## Public Interface

`PlatformRuntimeService` is the stable service for read-only Runtime `check`, exact-channel Web executable discovery, explicit executable validation, platform Target configuration/availability diagnosis from resolved settings, and ordered platform prerequisite diagnosis. Target and prerequisite diagnosis return safe normalized status facts without env values, unrestricted local target details, raw subprocess output, or backend objects. `PlatformRuntimeCheck` and `PlatformPrerequisiteCheck` remain owned by Models. Core's legacy export references the canonical service class.

`AndroidDeviceDiscovery` is the canonical stable discovery service, exported here and re-exported by Core for compatibility. It returns model-owned `AndroidDeviceDiscoveryResult` facts and never selects or persists a device. Device serial and allowlisted model/transport metadata are returned only as necessary transient selection identities, not interpolated into unrelated errors or remediation commands.

## Internal Structure

- `__init__.py`: public exports only.
- `_service.py`: platform-neutral dispatch and normalized result coordination.
- `_android_devices.py`: canonical Android discovery service delegating to the private existing-server-only provider.
- `providers/`: private Android, Web, Windows, and macOS runtime mechanics.

## Python Architecture

- Architecture level: Level 3 Layered Application.
- Public API: `PlatformRuntimeService` and `AndroidDeviceDiscovery`.
- Internal modules: service implementation and platform providers.
- Domain boundaries: host support/readiness and target discovery only.
- Boundary models: normalized status facts come from `models`.
- Dependency direction: Application depends on Environments; Environments depends on Models, never Application or adapters.
- Rationale: the service coordinates host-specific discovery and normalized readiness failures.

## Error Handling

Readiness checks never install or modify software. Missing Python platform dependencies identify an incomplete `fsq-agent` installation and provide safe reinstall/repair guidance. Missing external host services or system prerequisites provide safe provisioning guidance without executing it.

Target configuration diagnosis validates required identities and local path/channel shape. Target availability uses read-only discovery for current candidates, including Android online/authorized device and application discovery, without installing applications, changing device state, starting a browser/application, or creating a Driver/Appium session.

macOS prerequisite diagnosis performs bounded read-only host inspection and returns details in this order: `xcode_installation`, `xcode_developer_directory`, `appium_cli`, `appium_mac2_driver`, `appium_endpoint`, `application_path`, and `bundle_identifier`. It distinguishes a full Xcode application from Command Line Tools, verifies that the active developer directory belongs to full Xcode, resolves the Appium executable without changing `PATH`, queries installed Appium drivers with fixed non-interactive arguments and a bounded timeout, probes only the configured endpoint's status availability, validates the configured application bundle or executable path, and checks that a configured bundle identifier resolves consistently with the configured application when both are present. A prerequisite blocked by an earlier prerequisite is `not_applicable` with safe guidance rather than a fabricated ready result.

A valid active full-Xcode developer directory proves installation even outside standard application folders. Each host probe isolates unexpected errors into its own safe `error` fact and preserves other independent results. Invalid or non-dictionary application plists fail bundle identity verification without falling back to another installed application. For an executable within an application bundle, that enclosing bundle supplies its identity; path-only targets do not require a separate bundle identifier.

macOS prerequisite facts include explicit copyable repair commands only where a safe, applicable command can be supplied. Custom Xcode installations and non-default Appium endpoints receive guidance that respects their configured values; a default-path command must not be presented as the exact fix for a different configuration. Commands never contain credentials or private target values and are never executed by diagnosis. Web and Windows prerequisite checks are unchanged.

### Android diagnosis

- Ordered details are `adb_cli`, `uiautomator2_runtime`, `adb_server`, `device_connection`, `device_selection`, `application_identifier`, and `application_installation`. Independent local checks run even when downstream checks are blocked. Blocked checks use `not_applicable` with their dependency reason and are not counted as passed. Stable optional prerequisite codes distinguish concrete failures beyond the four existing display statuses.
- ADB executable availability is checked without changing PATH. Python dependency diagnosis verifies uiautomator2 availability without constructing a device, initializing automation, or installing host/device components. Package absence identifies an incomplete FSQ installation.
- Server diagnosis and device inventory use a shared bounded ADB protocol client that only connects to an already-running server. No `adb devices`, `adb shell`, `adb start-server`, backend auto-connect, or retry helper capable of spawning/restarting a daemon runs during diagnosis, target discovery, or startup preflight. Checking a socket and subsequently invoking an auto-starting client is not sufficient: if the server disappears between requests, diagnosis fails safely without restarting it.
- The default endpoint is local IPv4 loopback port 5037. Trusted process-level `ANDROID_ADB_SERVER_HOST` and `ANDROID_ADB_SERVER_PORT` values use the same endpoint as the Android backend. Absent values use the defaults; explicitly blank or malformed values are rejected. IPv6 endpoints are unsupported while the selected Android backend uses IPv4 sockets. Invalid, conflicting, unsupported socket forms (including `ADB_SERVER_SOCKET`) or non-loopback endpoint overrides are reported unavailable with guidance; they are never silently ignored in favor of another server. Browser and Workspace payloads cannot supply ADB hosts, ports, socket paths or commands. No new persisted endpoint configuration is introduced.
- All transport reads, protocol framing, subprocess-free package queries, response sizes and total probe durations are bounded. Malformed/truncated responses and timeouts are errors, not empty successful inventories. Refused or disappeared servers are unavailable; wrong protocol or incompatible responses remain distinct safe errors. Sockets close on success, failure and cancellation/timeout.
- Inventory preserves offline, unauthorized and other non-online states with actionable guidance. A selected serial must match exactly one currently online authorized device; absence, unauthorized and offline states are distinguished. No explicit selection resolves only when there is exactly one online authorized device. Multiple online devices require selection; the presence of another offline device does not invalidate a sole usable choice. A selected device never falls back to a different online device.
- Application identity is independently validated as a bounded package identifier. A fixed, safely encoded read-only package-manager query runs through the existing-server transport only for the selected authorized device. Explicit package paths prove installation; only a successful, well-formed negative response proves absence. Timeout, permission denial, nonzero exit and malformed output are query errors and block readiness without claiming the app is uninstalled. App IDs and serials cannot inject commands.
- Prerequisite facts are reused within one diagnosis so discovery and package results are not independently re-queried for summary construction. Any required unavailable/error fact or blocked required dependency prevents the Android target from becoming ready. Provider policy remains Application-owned.
- Safe guidance covers platform-tools setup, incomplete FSQ installation, manual server startup, USB debugging/authorization, reconnection, explicit device selection and app installation/configuration repair. Commands are copy-only, host-appropriate and contain no private device/app values; parameterized instructions requiring user substitution remain explanatory text. No automated permission changes, reconnect/restart, app install, device reboot, forwarding, device-agent launch or uiautomator2 healthcheck occurs.
- A ready result confirms these observable prerequisites only. Device-side uiautomator2 startup, automation permissions and Case-specific UI success remain execution concerns, clearly disclosed rather than reported as checked.

## Current Invariants

- Current host support behavior remains unchanged.
- Windows discovery covers Chromium and Chrome/Edge stable, beta, dev, and canary through exact paths.
- Explicit Web validation delegates to the pure Models identity contract and never trusts a shared basename or generic substring.
- Candidates are normalized, deduplicated, ordered, and never selected when multiple distinct matches exist.
- The module does not provision targets, start services, authenticate, mutate Workspaces, or expose subprocess output.
- macOS checks never install Xcode, accept its license, run first-launch setup, change `xcode-select`, install npm packages or Appium drivers, grant Accessibility/Automation permissions, start Appium, launch the target application, or create an Appium session.
- Environment diagnosis does not inspect Provider readiness; Workspace Doctor composes Environment facts with other public readiness boundaries.
