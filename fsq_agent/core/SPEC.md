# Module: core

## Purpose

Define the shared execution-core orchestration layer and platform runtime diagnostic boundary for FSQ-Agent. The core module owns `StepRunner`, harness/driver factories and protocols, evidence coordination, and `PlatformProbeFactory`, which exposes bounded side-effect-controlled readiness probes beside the backend mechanics they diagnose.

The module does not parse CLI arguments, parse FSQ YAML, construct provider sessions, construct OpenAI Agents SDK tools, own dynamic-only AgentTools, or generate reports. Entry modules build settings, providers, artifact stores, and registries, then request `HarnessInterface` and driver protocol implementations through public core factory classes instead of importing concrete platform harness or backend driver classes.

## Dependencies

- Internal project dependencies: `models` and `capabilities` only.
- External dependencies: standard library typing/time/path modules and optional platform backend imports only inside concrete backend modules with lazy import behavior.
- Forbidden dependencies: `agent`, `providers`, `tools`, `cli`, `fsq`, `report`, `observation`, `knowledge`, `skills`, and OpenAI Agents SDK runtime types.

Core may consume `CapabilityDefinition`, `CapabilityInvocation`, `CapabilityExecutionResult`, `ReplayPolicy`, `ExecutableStep`, legacy `EvidencePolicy` contracts when still present for compatibility, runner result/event models, harness result/context models, CommonTool parameter/result models, active platform parameter models and settings, AI assertion models, runtime secret settings, and project exceptions from `models`.
Core may consume shared declaration decorators, platform action catalog helpers, and side-effect-free capability discovery helpers from `capabilities`. Core must not place execution behavior in `capabilities` or import AgentTool implementations from `tools`.

## Public Interface

Current `__init__.py` exports via `__all__`:

- `CapabilityRegistry`: Validated runtime registry for decorated capabilities. It resolves canonical names and `ReplayPolicy(kind="fsq_command").alias` values, rejects duplicate names and ambiguous replay aliases, exposes serializable snapshots, and validates that every capability has a parameter model/schema and executor binding.
- `CapabilityDefinitionFactory`: Concrete factory class for PlatformTool capability definitions. It exposes `platform_definitions(platform, backend=None, include_ai_assertion=True)` so registry bootstrap can select the active platform/backend's PlatformTools without exposing function-style helper exports or private concrete backend driver classes.
- `DriverFactory`: Concrete factory class for selecting private concrete backend drivers from config-owned platform backend settings. It exposes typed `create_android_driver`, `create_web_driver`, `create_windows_driver`, and `create_macos_driver` methods, each returning the corresponding public driver protocol. It is not named `Default` because config selects the backend implementation; additional platform driver implementations belong behind this config-selected factory boundary.
- `HarnessFactory`: Concrete factory class for constructing runtime harnesses. Each supported platform has one built-in harness implementation; this factory is a convenience composition boundary that creates the configured driver through `DriverFactory` and wraps it in the private concrete platform harness. It returns `HarnessInterface` and accepts the active platform, `HarnessSettings`, optional `ArtifactStore`, optional `AIAssertionEvaluatorProtocol`, runtime secret settings, and Android app/serial overrides used by strict cases and playground device selection.
- `RuntimeSecretStore`: Process-local runtime secret allowlist and resolver built from `RuntimeSecretSettings` plus the current process environment after config loading. It exposes safe available names and warnings for configured names without values, resolves names only in memory, and never persists values.
- `HarnessInterface`: Protocol describing platform capabilities required by StepRunner. Concrete Android, Web, iOS, and fake harnesses may satisfy the protocol structurally.
- `StepRunner`: Executes one canonical `ExecutableStep` or capability invocation by looking up metadata in `CapabilityRegistry`, validating params with the declared model, applying evidence, post-action delay, and sensitivity policy, invoking the active `HarnessInterface`, normalizing backend/provider output, emitting structured safe events, and returning `RunnerStepResult`.
- `StepSequenceRunner`: Executes ordered `ExecutableStep` records with `StepRunner`, records events and step results, stops normal execution on blocking failures, and always executes supplied teardown steps. It does not own configured sleep or pacing behavior; post-action stabilization is handled inside `StepRunner`.
- `EvidenceRecorder`: Event/result sink that builds an `EvidenceBundle` and writes a JSON manifest for execution facts and artifact references.
- `ArtifactStore`: Evidence artifact path policy and writer for run-local screenshots, UI trees, harness-call JSON, logs, and raw files.
- `AIAssertionEvaluatorProtocol`: Structural protocol for provider-backed visual assertion evaluation. It accepts serializable `AIAssertionRequest` values and returns `AIAssertionResult` values without exposing provider runtime objects to `core`.
- `DriverObservationInterface`: Structural protocol for platform drivers that can supply runner evidence. It requires screenshot bytes and a serializable normalized `ui_snapshot` payload. Android, Web, Windows, and macOS driver protocols extend this contract while exposing their platform-specific explicit observation capabilities and replay aliases.
- `CommonPlatformTools`: Instantiable CommonTool provider bound to the active platform for schema generation, CommonTool capability definition discovery, and common tool invocation. Backend-specific PlatformTool bodies are still exposed by concrete drivers.
- `AndroidDriverInterface`: Protocol describing typed Android backend driver methods that Android driver capabilities may call.
- `WebDriverInterface`: Protocol describing typed Web backend driver methods that Web driver capabilities may call.
- `WindowsDriverInterface`: Protocol describing typed Windows desktop backend driver methods that Windows driver capabilities may call.
- `MacOSDriverInterface`: Protocol describing typed macOS desktop backend driver methods that macOS driver capabilities may call.
- `PlatformProbeFactory`: Stable factory that selects a private Android, Web, Windows, or macOS diagnostic probe from the active platform/backend settings. Its `create(platform, harness_settings, progress_sink=None)` result returns a probe that emits start/completion progress and returns ordered sanitized `DiagnosticProbeResult` values; factories accept injected command/process/network/import boundaries for deterministic tests. Probe construction never creates a normal harness or target session.

Core root and subpackage public exports expose only interfaces/protocols, abstract classes, stable execution-core service classes, approved factory classes, and SPEC-approved provider classes such as `CommonPlatformTools`. Concrete platform harnesses, concrete backend drivers, function-style helpers, decorators, and discovery utilities are internal unless this SPEC records a named exception. There are no public helper or concrete platform implementation exceptions.

Current subpackage exports:

- `fsq_agent.core.registry`: `CapabilityRegistry`, registry validation, alias resolution, and registry snapshots.
- `fsq_agent.core.runner`: `StepRunner`, executor binding protocols, and sequence runner orchestration.
- `fsq_agent.core.harness`: `HarnessInterface`, `AIAssertionEvaluatorProtocol`, `DriverFactory`, `HarnessFactory`, and Android/Web/Windows/macOS driver contracts. Concrete platform harnesses and concrete backend implementations are private implementation details.
- `fsq_agent.core.evidence`: `EvidenceRecorder`, `ArtifactStore`, and evidence coordination logic.

`StepRunner` exposes a narrow API:

```python
runner = StepRunner(
	registry=registry,
	harness=harness,
	runtime_secret_store=runtime_secret_store,
	post_action_delay_seconds=settings.execution.post_action_delay_seconds,
)
result = runner.run_step(run_id="run-1", step=executable_step)
events = runner.events
```

`ExecutableStep.action_name` stores the canonical capability name, such as `wait_ms`, `tap_on`, `input_text`, or `assert_with_ai`. Authored names such as `waitMs`, `tapOn`, and `assertWithAI` are preserved in step metadata by parsers and adapters.

For each invocation, `StepRunner` must:

1. Resolve the canonical capability from the registry.
2. Validate params with `capability.params_model`, treating omitted `textType` on text-entry parameters as `literal`.
3. Build safe invocation context containing run id, step id, source ref, authored action metadata, and capability metadata.
4. Resolve `textType="runtimeSecret"` text-entry parameters through `RuntimeSecretStore` before platform driver invocation, fail with `configuration_error` before side effects when a referenced name is missing, empty, or not allowlisted, and preserve safe unresolved text parameters for replay/event metadata.
5. Derive automatic evidence capture from the resolved capability plus `ExecutableStep.kind`, not from per-action capture flags or executor kind. `action` steps capture before and after; `assertion` steps capture before only; `setup` steps capture after only; `teardown` steps capture before only; `observation` and `diagnostic` steps do not receive automatic screenshot capture. This includes inherited CommonTool actions such as `wait_ms`. Each automatic capture records `screenshot` plus normalized `ui_snapshot` through the active harness observation interface. For step kinds with after capture, the after capture runs after invoke returns for passed, failed, skipped, and cancelled results; there is no extra default failure artifact phase.
6. Resolve the effective post-action delay from `CapabilityDefinition.post_action_delay_seconds` when it is not `None`; otherwise use configured `execution.post_action_delay_seconds.common` for CommonTools and `execution.post_action_delay_seconds.platform` for PlatformTools.
7. Route CommonTool and PlatformTool capabilities through `HarnessInterface.invoke_action(step, context)`. The harness executes inherited CommonTools through the active platform tool provider and delegates driver-backed PlatformTools to the concrete driver/backend. AI assertion tools are driver-backed PlatformTools that may use harness-injected evaluator and artifact services through shared backend support.
8. Normalize backend output into the shared runner result contract.
9. Apply a positive post-action delay after invoke completion or structured invoke failure conversion and before finalize begins. For driver PlatformTools this means before `after_action` and any after capture required by the step-kind policy; for CommonTools this means before the common finalize phase. Zero delay must not call `time.sleep(0)`.
10. Apply sensitivity rules before persistence.
11. Emit structured events containing safe capability metadata, replay payload fields, artifact refs, post-action delay metadata, and status.
12. Return `RunnerStepResult`.

For replayable PlatformTools, `StepRunner` must include capability-derived `safe_replay_params` in invoke metadata and `last_capability_execution_result` when safe replay params differ from raw tool arguments. Android `tap_at` and point-based `swipe` must record invocation coordinates plus the current `HarnessContext.screen_size` as `reference_screen_size` when a reference is not already supplied, so dynamic recording can produce strict YAML that replays proportionally on a different device resolution.

`StepRunner` must not contain action-name branches for `waitMs`, `wait_ms`, platform action names, or evidence-enabled platform mutations. A pure wait is an inherited CommonTool capability. Runtime secret lookup is not an LLM-facing CommonTool; runtime-secret text is resolved by text-entry parameter semantics through `RuntimeSecretStore`.

`StepSequenceRunner` exposes a narrow API:

```python
runner = StepSequenceRunner(step_runner=runner, evidence_recorder=recorder)
bundle = runner.run_steps(run_id="run-1", steps=steps, teardown_steps=teardown_steps)
```

It must not import `fsq`, parse YAML, construct platform drivers, resolve strict replay refs, generate reports, sleep between steps for configured pacing, or add synthetic `waitMs` steps for pacing.

`HarnessInterface` provides runner-facing behavior for context, artifact capture, and CommonTool/PlatformTool invocation. `invoke_action` is the long-term stable gateway from `StepRunner` to active platform behavior. Platform harnesses are FSQ-controlled runtime gateways; platform tool providers own inherited CommonTool bodies; concrete drivers own backend PlatformTool bodies, including `assert_with_ai`; harnesses provide invocation context and artifact/evaluator services when a backend tool needs them. Harnesses and drivers do not decide runner ordering, retry policy, event emission, evidence manifest structure, artifact directory policy, case aggregation, or report generation.

### Android Platform Block

Android diagnosis checks `adb` PATH resolution, bounded `adb version`, bounded `adb devices`, configured/unique online device selection, `uiautomator2` import and basic communication, app id presence, and package installation without starting/stopping the app. Device discovery retries once under a second bounded attempt when the first attempt times out or exits nonzero, accommodating normal ADB daemon startup without running explicit ADB service-control commands. Missing ADB and ADB command/server failures after retry block readiness. A successful `adb devices` result with no listed devices is `warn`; uiautomator2 and package checks are skipped and readiness is not blocked. Multiple online devices without a configured serial, `offline`, `unauthorized`, and configured-serial mismatch remain `fail`. Subprocess output is bounded and sanitized.

Android LLM-exposed capabilities include inherited CommonTool `wait_ms` plus driver-backed PlatformTools `launch_app`, `kill_app`, `tap_on`, `tap_at`, `long_press_on`, `input_text`, `press_key`, `swipe`, `assert_visible`, `assert_not_visible`, `assert_state`, `ui_snapshot`, and `assert_with_ai`. Authored FSQ aliases include `waitMs`, `launchApp`, `killApp`, `tapOn`, `tapAt`, `longPressOn`, `inputText`, `pressKey`, `swipe`, `assertVisible`, `assertNotVisible`, `assert`, Android `uiTree`, and `assertWithAI`. Android `input_text` accepts `textType="runtimeSecret"` to reference allowlisted runtime environment values resolved by `StepRunner`. The Android action catalog may describe `perform_actions` / `performActions`, but an unimplemented uiautomator2 backend method must not be decorated as a capability and must not appear in platform `action_space()` or SDK exposure.

Android owns public `AndroidDriverInterface`, private `AndroidHarness`, private `UiAutomator2AndroidDriver`, Android catalog-backed platform declarations, and Android default capability definitions selected through public factories. `AndroidDriverInterface` extends the shared driver observation contract; automatic runner capture writes `screenshot` and normalized `ui_snapshot` artifacts, with Android `ui_snapshot` content sourced from a compact Android UI hierarchy XML snapshot produced by `UiAutomator2AndroidDriver.ui_snapshot`. The compact snapshot keeps the existing `{"xml": ...}` output contract, may use uiautomator2 source-level hierarchy compression when the installed backend supports it, removes layout-only wrapper nodes by lifting meaningful descendants, removes empty nodes and low-value/default attributes, and clips long text-like attributes to the first 50 characters. Snapshot capture must prefer availability over perfect compaction: unsupported source compression, XML parse failures, or local compaction failures must fall back to raw hierarchy XML rather than failing evidence capture. `UiAutomator2AndroidDriver.assert_with_ai` is a decorated backend tool that calls shared AI assertion support. The explicit Android `uiTree` replay alias resolves to the canonical `ui_snapshot` observation capability for authored dynamic and strict commands, and its capability description must disclose compact XML behavior and 50-character text clipping.

Android UI snapshot compaction is Android-only. It must not add fields to `AndroidUiTreeParams`, change artifact kinds or MIME types, replace XML with locator JSON, introduce user-facing YAML/environment settings, or change Web, Windows, or macOS snapshot behavior.

`UiAutomator2AndroidDriver.tap_at` and point-based `swipe` accept absolute Android screen coordinates for the current invocation. When params include `reference_screen_size`, the driver must scale supplied points from that reference width/height to the current device screen width/height, clamp computed coordinates inside the current screen bounds, and execute the scaled coordinate action. This scaling exists for generated strict replay artifacts; dynamic agents should still prefer locator-based actions for normal UI elements.

### Web Platform Block

Web diagnosis checks the Playwright Python dependency, configured Chrome executable path/channel, and bounded isolated Chrome process startup using a temporary profile. It does not navigate, use a real profile, create an FSQ page, or invoke `startBrowser`, and it terminates child processes and removes temporary data best-effort on every path.

Web LLM-exposed Playwright capabilities are inspired by Playwright MCP core automation and include inherited CommonTool `wait_ms` plus driver-backed PlatformTools `start_browser`, `close_browser`, `navigate_to`, `navigate_back`, `click_on`, `type_text`, `select_option`, `hover_on`, `press_key`, `wait_for`, `take_screenshot`, `page_snapshot`, `assert_visible`, `assert_not_visible`, `assert_text`, and `assert_with_ai`. Authored FSQ aliases include `waitMs`, `startBrowser`, `closeBrowser`, `navigateTo`, `navigateBack`, `clickOn`, `typeText`, `selectOption`, `hoverOn`, `pressKey`, `waitFor`, `takeScreenshot`, `pageSnapshot`, `assertVisible`, `assertNotVisible`, `assertText`, and `assertWithAI`. Web `type_text` accepts `textType="runtimeSecret"` to reference allowlisted runtime environment values resolved by `StepRunner`. Web observation uses `page_snapshot`/`pageSnapshot` and must not reuse Android `ui_tree`/`uiTree` naming. Unsafe JavaScript/evaluate, generated Playwright test code, network/storage/devtools, tabs, drag/drop, file upload, PDF, and coordinate/vision capabilities are not exposed.

Web browser lifecycle is explicit. `start_browser` is a setup-kind driver capability that starts or reuses the configured browser/page and returns success when already started. `close_browser` is a teardown-kind driver capability that closes owned Playwright state, returns success when already closed, and leaves the driver reusable for a later `start_browser` in the same task. Lifecycle capabilities must not rely on default screenshot plus page-snapshot evidence capture because there may be no page before startup or after shutdown.

Web page-dependent capabilities such as `navigate_to`, `navigate_back`, clicks, typing, waits, screenshots, page snapshots, and deterministic assertions require an active page. If invoked before `start_browser`, the Web driver must return a structured failure with a clear startup-required message instead of launching implicitly. `WebHarness.get_context()` must tolerate the not-started state and return safe metadata such as `browser_started: false` so `StepRunner` prepare can run before browser startup.

Web owns public `WebDriverInterface`, private `WebHarness`, private `PlaywrightWebDriver`, Web catalog-backed platform declarations, and Web default capability definitions selected through public factories. `WebDriverInterface` extends the shared driver observation contract; automatic runner capture writes `screenshot` and normalized `ui_snapshot` artifacts, with Web `ui_snapshot` content sourced from Playwright page/accessibility snapshot data when a page is active. `PlaywrightWebDriver.assert_with_ai` is a decorated backend tool that calls shared AI assertion support. The explicit `page_snapshot`/`pageSnapshot` observation capability remains available for authored dynamic and strict commands. Playwright import and configured channel/executable launch are explicit `start_browser` runtime/backend concerns, not driver-construction or registry-bootstrap concerns. `PlaywrightWebDriver.close()` remains a final resource cleanup hook and must use the same close implementation without turning cleanup into a task-visible `closeBrowser` event.

### Windows Platform Block

Windows diagnosis checks the host OS, pywinauto/Pillow imports, app path readability, backend kind, title-regex compilation, and launch-argument parsing without launching the app. A successful static check warns that window/control-tree automation remains unproven.

Windows LLM-exposed pywinauto capabilities include inherited CommonTool `wait_ms` plus driver-backed PlatformTools `launch_app`, `kill_app`, `click_on`, `double_click_on`, `right_click_on`, `type_text`, `press_key`, `hover_on`, `scroll_on`, `drag_to`, `assert_visible`, `ui_snapshot`, and `assert_with_ai`. Authored FSQ aliases include `waitMs`, `launchApp`, `killApp`, `clickOn`, `doubleClickOn`, `rightClickOn`, `typeText`, `pressKey`, `hoverOn`, `scrollOn`, `dragTo`, `assertVisible`, `uiSnapshot`, and `assertWithAI`. Windows `type_text` accepts `textType="runtimeSecret"` to reference allowlisted runtime environment values resolved by `StepRunner`. Windows observation uses `ui_snapshot`/`uiSnapshot` and must not reuse Android `ui_tree`/`uiTree` or Web `page_snapshot`/`pageSnapshot` naming. Windows element resolution uses typed control locators and allows title-regex fallback after exact title matching fails. Semantic `target` fields do not participate in control lookup.

Windows owns public `WindowsDriverInterface`, private `WindowsHarness`, private `PywinautoWindowsDriver`, Windows catalog-backed platform declarations, and Windows default capability definitions selected through public factories. `WindowsDriverInterface` extends the shared driver observation contract; automatic runner capture writes `screenshot` and normalized `ui_snapshot` artifacts. `PywinautoWindowsDriver.assert_with_ai` is a decorated backend tool that calls shared AI assertion support. The explicit `ui_snapshot`/`uiSnapshot` observation capability remains available for authored dynamic and strict commands. pywinauto application and mouse imports are lazy runtime/backend concerns, not registry-bootstrap concerns. Normalized Windows runtime settings supply the local app path, pywinauto backend kind, optional window title regex, and configured launch arguments; `backend_kind` selects pywinauto's UI automation mode (`uia` or `win32`) and is not a second FSQ Windows backend. An optional configured window title regex resolves the launched application main window by title instead of the process top window, and configured launch arguments are prepended before per-step `launchApp.extra_args`.

`hover_on` moves to a located control, `scroll_on` scrolls at a located control using non-zero `wheel_dist`, and `drag_to` supports locator or point sources and locator, point, or relative-offset destinations. These actions receive default evidence through the centralized driver step-kind policy.

### macOS Platform Block

macOS diagnosis checks the host OS, Appium Python Client import, Appium URL shape, bounded `/status` reachability and exposed Mac2 availability, and bundle-id/app-path target configuration without creating a session or launching the app. A successful static target check warns that session creation/accessibility automation remains unproven.

macOS LLM-exposed Appium Mac2 capabilities include inherited CommonTool `wait_ms` plus driver-backed PlatformTools `launch_app`, `kill_app`, `click_on`, `double_click_on`, `right_click_on`, `type_text`, `press_key`, `hover_on`, `drag_to`, `take_screenshot`, `ui_snapshot`, `assert_visible`, `assert_elements_order`, and `assert_with_ai`. Authored FSQ aliases include `waitMs`, `launchApp`, `killApp`, `clickOn`, `doubleClickOn`, `rightClickOn`, `typeText`, `pressKey`, `hoverOn`, `dragTo`, `takeScreenshot`, `uiSnapshot`, `assertVisible`, `assertElementsOrder`, and `assertWithAI`. macOS `type_text` accepts `textType="runtimeSecret"` to reference allowlisted runtime environment values resolved by `StepRunner`. macOS observation uses `ui_snapshot`/`uiSnapshot` and must not reuse Android `ui_tree`/`uiTree` or Web `page_snapshot`/`pageSnapshot` naming. macOS target resolution uses a locator built from accessibility id, name, label, value, role/control type, class name, XPath, predicate string, semantic target text, and explicit coordinates.

macOS owns public `MacOSDriverInterface`, private `MacOSHarness`, private `AppiumMac2Driver`, macOS catalog-backed platform declarations, and macOS default capability definitions selected through public factories. `MacOSDriverInterface` extends the shared driver observation contract; automatic runner capture writes `screenshot` and normalized `ui_snapshot` artifacts. `AppiumMac2Driver.assert_with_ai` is a decorated backend tool that calls shared AI assertion support. The explicit `ui_snapshot`/`uiSnapshot` observation capability remains available for authored dynamic and strict commands. Appium imports, Mac2 option construction, Appium server connection, and application launch are lazy runtime/backend concerns, not registry-bootstrap concerns. FSQ platform/backend names are `macos` and `appium_mac2`; the driver maps those to Appium native `platformName: Mac` and `automationName: Mac2` internally.

`AppiumMac2Driver.assert_elements_order` resolves each requested element through the same macOS locator pipeline used by actions and `assert_visible`, reads Appium element `location` and `size`, compares element centers on the requested axis, and returns structured assertion details containing `direction`, `elements_found`, `elements_total`, `actual_order`, `expected_order`, and per-element positions. Missing required elements are target-resolution failures when `require_all` is true. Found elements in the wrong order are assertion failures, not configuration errors.

Capability metadata, not a static Android action table, is the runtime source of truth for platform method name, parameter model, step kind, owner, platform/backend metadata, and replay alias. Default evidence capture is not declared per action; it is derived by `StepRunner` from live `driver` executor metadata and step kind. Platform action catalog entries are declaration-time validation inputs that generate capability metadata; they are not an execution path or parser fallback. Capability metadata does not include per-tool SDK schema strictness; SDK adapters expose active capabilities with strict JSON schema by default.

## Internal Structure

- `__init__.py`: Public exports only.
- `_capabilities.py`: Capability registry, alias resolution, duplicate validation, and snapshot creation.
- `_default_capabilities.py`: `CapabilityDefinitionFactory` plus internal platform/backend PlatformTool capability definition helpers used by entry-layer registry bootstrap without constructing a real backend connection.
- `harness/_factory.py`: `DriverFactory`, `HarnessFactory`, private factory typing protocols, and private platform/backend dispatch tables for current built-in harnesses and drivers.
- `runner/__init__.py`: Runner subpackage exports only.
- `runner/_runner.py`: `StepRunner` implementation for single-step capability execution.
- `runner/_sequence.py`: `StepSequenceRunner` implementation for ordered execution and evidence recording.
- `_platform_tools.py`: CommonPlatformTools and platform-default `wait_ms` implementation.
- `_runtime_secrets.py`: RuntimeSecretStore implementation for allowlisted runtime text-secret names, presence warnings, and in-memory resolution used by `StepRunner`.
- `diagnostics/_factory.py`: PlatformProbeFactory and private platform/backend probe selection.
- `diagnostics/_android.py`, `_web.py`, `_windows.py`, `_macos.py`: Private bounded backend readiness probes and cleanup logic.
- `diagnostics/__init__.py`: Diagnostic subpackage public export boundary for `PlatformProbeFactory`.
- `diagnostics/_progress.py`: Shared safe start/completion event emission for private platform probes.
- `harness/_ai_assertion_tool.py`: Shared backend support for decorated platform `assert_with_ai` driver tools, including evaluator invocation, screenshot artifact capture, and backend-shaped result conversion.
- `harness/__init__.py`: Harness subpackage exports only. It exports harness and driver protocols plus public factory classes, not concrete platform harnesses or backend drivers.
- `harness/_interface.py`: `HarnessInterface` and `AIAssertionEvaluatorProtocol` protocols.
- `harness/_android.py`: Built-in `AndroidHarness` implementation and Android runtime-service delegation.
- `harness/_android_driver.py`: `AndroidDriverInterface` protocol and driver-owned contracts.
- `harness/_web.py`: Built-in `WebHarness` implementation and Web runtime-service delegation.
- `harness/_web_driver.py`: `WebDriverInterface` protocol and driver-owned contracts.
- `harness/_windows.py`: Built-in `WindowsHarness` implementation and Windows runtime-service delegation.
- `harness/_windows_driver.py`: `WindowsDriverInterface` protocol and driver-owned contracts.
- `harness/_macos.py`: Built-in `MacOSHarness` implementation and macOS runtime-service delegation.
- `harness/_macos_driver.py`: `MacOSDriverInterface` protocol and driver-owned contracts.
- `harness/_driver_tools.py`: Internal platform declaration helpers, Android/Web/Windows/macOS action catalog wiring, shared driver capability matching/schema/metadata helpers, and function schema/capability discovery wrappers backed by `capabilities`.
- `harness/_uiautomator2_driver.py`: Optional uiautomator2 backend implementation with lazy dependency import and fake-device injection for tests.
- `harness/_playwright_driver.py`: Optional Playwright backend implementation with lazy dependency import, browser/page lifecycle management, and fake-page injection for tests.
- `harness/_pywinauto_driver.py`: Optional pywinauto backend implementation with lazy dependency import, application/window lifecycle management, and fake-window injection for tests.
- `harness/_appium_mac2_driver.py`: Optional Appium Mac2 backend implementation with lazy dependency import, Mac2 session lifecycle management, page-source simplification, macOS command execution, and fake-client injection for tests.
- `evidence/__init__.py`: Evidence subpackage exports only.
- `evidence/_recorder.py`: `EvidenceRecorder` implementation.
- `evidence/_artifact_store.py`: `ArtifactStore` implementation for run-local artifact paths and file writing.
- `SPEC.md`: Module design.

Core must not define Pydantic models shared across modules. Shared models belong in `fsq_agent.models`.

## Python Architecture

- Architecture level: 3 Layered Application.
- Public API: capability registry, capability definition factory class, driver factory class, harness factory class, PlatformProbeFactory, CommonTool provider class, runner, sequence runner, harness protocol, Android/Web/Windows/macOS driver protocols, evidence recorder/store, and provider-neutral AI assertion evaluator protocol exported from package/subpackage `__init__.py` files. Public exports are limited to interfaces/protocols, abstract classes, stable execution-core service classes, approved provider classes, and approved factory classes; there are no helper-function, decorator, concrete platform harness, concrete backend driver, or concrete probe exceptions.
- Internal modules: all `_*.py` files and implementation subpackages remain private outside documented exports.
- Domain boundaries: core owns execution orchestration and provider-neutral platform coordination. Provider construction, SDK tool creation, CLI parsing, FSQ parsing, and report generation live outside core.
- Boundary models: all serializable contracts come from `models`; core protocols and concrete runners operate on those contracts.
- Dependency direction: core imports `models` and `capabilities` only among project modules. Entry modules inject providers, artifact stores, runtime settings, and optional fake `HarnessInterface` instances; default runtime construction goes through public core factories instead of direct concrete harness or backend imports.
- Rationale: execution routing coordinates multiple side-effecting components and evidence flow, so Level 3 is warranted; no persistence/domain complexity justifies Clean Architecture or DDD.

## Error Handling

Registry bootstrap failures are configuration errors and must occur before YAML parsing or SDK tool exposure. Duplicate names, duplicate or ambiguous `fsq_command` replay aliases, replay aliases that conflict with another canonical name, missing parameter models, unsupported executor kinds, invalid sensitivity result shapes, and eager backend connections during registry build fail fast.

Runner phases preserve failure boundaries:

- prepare failures: registry lookup, context, setup, validation, or before-action observation failures
- invoke failures: action, target, timeout, CommonTool, PlatformTool, provider-backed assertion, or backend failures
- finalize failures: after-action observation, artifact capture, stabilization, cleanup, or event persistence failures

Harness action payload validation errors are configuration failures and must be returned as structured failed results before any backend side effect. Driver target misses, assertion failures, action errors, artifact errors, and backend exceptions become structured runner results. Strict mode rejects silent recovery; target misses remain failures.

Platform probe dependency, command, connectivity, and timeout failures become sanitized `DiagnosticProbeResult` values so independent checks continue. Probes do not persist artifacts, expose raw external output, restart services, install dependencies, launch target applications, or create normal platform sessions.

Platform probes emit `check_started` immediately before each dependency, command, process, device, or service operation and `check_completed` immediately when its result is known, including prerequisite skips. Progress emission does not change returned result ordering.

Sensitive capabilities must return values in the standard normalized shape `output.value`. Persisted events, manifests, reports, artifacts, previews, and historical tool-output trimming must redact or omit sensitive values. A sensitive capability result that does not use the standard shape fails as a capability implementation/configuration error and raw output must not be persisted.

## Verification Scope

- Verification covers registry validation and alias resolution, factory platform/backend selection, `StepRunner` routing through `HarnessInterface.invoke_action`, centralized evidence capture, post-action delay, runtime-secret resolution, sensitivity redaction, structured events, sequence teardown, CommonTool/PlatformTool dispatch, platform probe ordering/timeout/cleanup/sanitization, and Android empty-device warning versus ADB/server failure semantics.
- Boundary verification ensures registry/bootstrap does not connect to real devices or launch apps/browsers, strict registries exclude AgentTools, public exports exclude concrete harness/backend implementations, and non-core modules do not import core internals.

## Current Invariants

- Decorator metadata produced through the shared `capabilities` declaration layer is the source of truth for executable capabilities. Android action catalog entries validate declarations and prevent platform drift, but static Android action tables, separate harness schemas, and separate CommonTool definitions are not runtime authorities.
- `CommonPlatformTools` remains a public CommonTool provider because entry-layer registry bootstrap and tests need direct access to inherited CommonTool capability definitions and invocation behavior. This is not a concrete platform implementation-selection class.
- `CapabilityDefinitionFactory` is the public class-based boundary for PlatformTool capability definition discovery. Function-style capability definition helpers, concrete backend classes, and driver declaration helpers may remain internal implementation details, but they are not exported public API.
- `DriverFactory` is the public class-based boundary for selecting private concrete backend drivers behind public platform driver protocols. It dispatches on current config-owned platform backend settings and creates only the selected backend implementation; it is not named `Default` because config, not the factory name, identifies the selected implementation.
- `HarnessFactory` is the public class-based boundary for constructing runtime harnesses. It returns `HarnessInterface`, delegates driver selection to `DriverFactory`, and keeps concrete platform harness classes private to `core`; it is not named `Default` because each platform currently has exactly one harness implementation and the factory is only a composition convenience.
- `PlatformProbeFactory` is the public class-based boundary for readiness probes. It shares backend ownership with runtime drivers but is separate from harness construction, capability registration, and target-workflow execution.
- `StepRunner` owns execution control, metadata-driven routing, evidence policy application, result normalization, sensitivity handling, and structured event emission.
- `StepRunner` owns post-action stabilization delay. It applies `time.sleep` only through a runner-local private helper after invoke and before finalize when the effective delay is positive. Entry layers pass loaded delay settings into `StepRunner`; `core` must not import `config`.
- Post-action delay is metadata/config-driven timing only. Capability metadata can override the configured executor-kind default, including explicit zero to disable delay. The delay must not become a `waitMs` command, replay entry, evidence step, or action result.
- CommonTools are inherited platform-default execution capabilities owned by platform tool providers. They are not AgentTools and are not dynamic-only helpers.
- `wait_ms` is a decorated inherited CommonTool capability with replay alias `waitMs`, not a core-owned special command.
- Runtime-secret text input is not a decorated CommonTool capability. `RuntimeSecretStore` validates configured names, reports safe warnings for missing values, and resolves only text-entry params with `textType="runtimeSecret"` before driver invocation. `get_runtime_secret` must not be exposed as an active LLM/CommonTool path.
- Platform `assert_with_ai` tools use the same catalog-backed capability metadata path as other driver-backed PlatformTools. Their public tool decorators live on concrete backend drivers, while shared backend support handles evaluator invocation and artifact/result shaping.
- Concrete drivers control dynamic exposure by decorating implemented methods with shared capability metadata. A protocol method existing on `AndroidDriverInterface` or `WebDriverInterface`, a Pydantic parameter model, or an action catalog entry is not enough to expose it to the registry or to the LLM. Web, desktop, and iOS platforms should add platform action catalogs and reuse catalog-backed declaration helpers rather than creating platform-specific decorator implementations.
- Android backend construction must be lazy enough that registry bootstrap and strict YAML parsing never require a real device connection.
- Web backend construction must be lazy enough that registry bootstrap, strict YAML parsing, and `PlaywrightWebDriver` construction never require importing Playwright or launching a browser. Browser startup is the explicit `start_browser` capability; browser shutdown is the explicit `close_browser` capability or final driver cleanup when entry layers dispose resources after execution.
- macOS backend construction must be lazy enough that registry bootstrap, strict YAML parsing, and `AppiumMac2Driver` construction never require importing Appium/Selenium, connecting to an Appium server, or launching a macOS app. Appium Mac2 session creation is an explicit lifecycle/runtime concern reached through `launch_app`; other macOS actions require an active session and fail clearly when invoked before launch.
- macOS order assertions are deterministic driver-backed PlatformTools. They borrow the Appium MCP reference's geometry idea, but expose FSQ typed locators and assertion result metadata instead of MCP XPath-only payloads or MCP server calls.
- AI assertion is explicit assertion execution. It may call an injected evaluator only because the authored capability requested AI assertion; it must not be used for locator fallback, action repair, screenshot reinspection of unrelated steps, or testcase mutation.
- Locator self-healing is not part of strict execution. Any deterministic fallback or AI-assisted repair must be represented as recovery execution so reports can compare strict truth with recovery outcome.
- Evidence artifacts use run-relative paths. `ArtifactStore` owns directory layout and artifact writing; runners and harnesses do not construct artifact paths manually.