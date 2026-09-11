# Module: fsq compatibility

## Purpose

Forward legacy FSQ Case DSL imports to the canonical `case_dsl` package without duplicate parsing behavior or mutable state. The behavior contracts below describe the canonical objects exposed through both package paths.

Goal-only FSQ cases may omit the command document or provide an empty command list; parsed goal-only cases produce no executable steps.

## Dependencies

- `models`: Uses `FsqCase`, `FsqCaseConfig`, FSQ lifecycle hook models, shared configuration errors, execution-core contracts such as `ExecutableStep` and `SourceRef`, capability registry snapshots, replay policy metadata, and shared capability parameter models for deterministic command payload normalization and step kind classification.

The compatibility package imports only `case_dsl`. Canonical `case_dsl` must not import `capabilities`, `core`, or `tools`; it receives a serializable `CapabilityRegistrySnapshot`.

## Public Interface

Current `__init__.py` exports via `__all__`:

- `FsqCaseLoader`: Loads canonical `*.fsq.yaml` Cases from explicit paths or the configured read-only case directory. It may accept `*.codex.yaml` for one deprecation cycle with a caller-visible warning. It accepts metadata-plus-command Cases, goal-only metadata Cases, and optional lifecycle hook metadata in the first YAML document. `*.intent.yaml` is not an FSQ Case format.
- `FsqExecutableStepAdapter`: Converts an `FsqCase` command document into ordered canonical `ExecutableStep` records for deterministic core execution using a registry snapshot.
- `is_fsq_case_file`: Detects FSQ case file names.
- `FSQ_CASE_SUFFIX`: Canonical Case filename suffix.
- `FsqCaseValidator` and `FsqCaseSerializer`: Exact forwards to the public static validation and deterministic serialization contracts in `case_dsl/SPEC.md`.

The first deterministic step adapter exposes a narrow API:

```python
adapter = FsqExecutableStepAdapter(registry_snapshot=registry.snapshot())
steps = adapter.to_executable_steps(case)
```

Lifecycle hooks are metadata on `FsqCase.config`, not command-list pseudo-commands. `FsqCaseLoader` validates and normalizes `onCaseStart` and `onCaseComplete`, while `FsqExecutableStepAdapter.to_executable_steps(case)` converts only `case.commands`. Hook execution, hook path resolution, recursion detection, shell execution, and lifecycle failure policy are owned by `execution`.

The first YAML document may contain optional lifecycle fields:

```yaml
onCaseStart:
  - runShell: ./scripts/prepare.sh
    runCase: hooks/login.fsq.yaml
onCaseComplete:
  runCase: hooks/logout.fsq.yaml
```

Each lifecycle field may be omitted, may be one hook entry mapping, or may be an ordered list of hook entry mappings. A hook entry may contain `runCase`, `runShell`, or both; it must contain at least one supported action; unknown hook action keys are invalid; and non-empty string values are required. When both actions are present in one entry, the authored YAML key order must be preserved in the normalized hook model.

`FsqExecutableStepAdapter` resolves authored FSQ action names through canonical capability names and `ReplayPolicy(kind="fsq_command").alias` values in the registry, then stores the canonical capability name in `ExecutableStep.action_name`. Authored names such as `tapOn`, `inputText`, `pressKey`, `assertVisible`, `assert`, `assertWithAI`, `startBrowser`, `closeBrowser`, `clickOn`, `typeText`, `uiSnapshot`, `assertElementsOrder`, and generated replay alias `waitMs` are preserved in `ExecutableStep.metadata["authored_action_name"]`.

The adapter normalizes each known YAML command into `ExecutableStep.params` by resolving the action alias to a `CapabilityDefinition` and validating object-shaped payloads against `capability.params_model` through shared Case DSL validation. Null/default rules are defined in `case_dsl/SPEC.md`; implicit literal text has the same effective parameters as explicit `textType: literal`. Known action payloads use the same field shape as their parameter models rather than action-specific scalar shorthand. Canonical forms are grouped by active platform PlatformTools plus inherited CommonTool commands. AgentTools are not present in strict registries and cannot appear as executable FSQ commands.

Android command block:

| FSQ command shape | canonical `action_name` | `params` |
|---|---|---|
| `launchApp` | `launch_app` | `{}` |
| `killApp` | `kill_app` | `{}` |
| `pressKey: {key: Enter}` | `press_key` | `{"key": "Enter"}` |
| `tapOn: {target: Login}` | `tap_on` | `{"target": "Login"}` |
| `inputText: {text: bing.com, ...}` | `input_text` | validated `AndroidInputTextParams` dump |
| `assertVisible: {...}` | `assert_visible` | validated `AndroidAssertVisibleParams` dump |
| `assertNotVisible: {...}` | `assert_not_visible` | validated `AndroidAssertNotVisibleParams` dump |
| `longPressOn: {...}` | `long_press_on` | validated `AndroidLongPressOnParams` dump |
| `swipe: {...}` | `swipe` | validated `AndroidSwipeParams` dump |
| `assert: {element: ..., text: ...}` | `assert_state` | validated `AndroidAssertStateParams` dump |
| `assertWithAI: {prompt: ...}` | `assert_with_ai` | validated `AndroidAssertWithAIParams` dump |
| `inputText: {text: TEST_ACCOUNT_EMAIL, textType: runtimeSecret, ...}` | `input_text` | validated `AndroidInputTextParams` dump preserving `textType: runtimeSecret` for core runtime-secret resolution |

Representative Web command block (the shared Web catalog is the complete command authority):

The compatibility facade forwards the canonical Case DSL objects and does not provide a separate legacy Web parser. Web targets require a self-contained `{page, steps}` locator; page operations and observations require explicit page/scope. String targets, ref selectors, old locator bags, and implicit Web scope are rejected, including through this facade. Ordered filter/position rules and unresolved runtime-secret references are preserved.

| FSQ command shape | canonical `action_name` | `params` |
|---|---|---|
| `startBrowser: {}` | `start_browser` | validated `WebStartBrowserParams` dump |
| `closeBrowser: {}` | `close_browser` | validated `WebCloseBrowserParams` dump |
| `navigateTo: {page: main, url: https://example.test}` | `navigate_to` | validated `WebNavigateToParams` dump |
| `navigateBack: {page: main}` | `navigate_back` | validated `WebNavigateBackParams` dump |
| `clickOn: {target: {page: main, steps: [{kind: role, role: button, name: Submit}]}}` | `click_on` | validated `WebClickOnParams` dump |
| `fillText: {target: {page: main, steps: [{kind: label, text: Email}]}, text: user@example.test}` | `fill_text` | validated `WebFillTextParams` dump; replaces editable content |
| `typeText: {target: {page: main, steps: [{kind: label, text: Email}]}, text: .test}` | `type_text` | validated `WebTypeTextParams` dump; sequential append without clearing |
| `selectOption: {target: {page: main, steps: [{kind: label, text: Country}]}, selection: {kind: value, values: [US]}}` | `select_option` | validated `WebSelectOptionParams` dump |
| `hoverOn: {target: {page: main, steps: [{kind: role, role: button, name: Menu}]}}` | `hover_on` | validated `WebHoverOnParams` dump |
| `waitFor: {condition: {kind: text, scope: {kind: page, page: main}, text: {kind: contains, value: Loaded}}}` | `wait_for` | validated `WebWaitForParams` dump |
| `takeScreenshot: {page: main, full_page: true}` | `take_screenshot` | validated `WebTakeScreenshotParams` dump |
| `assertText: {target: {page: main, steps: [{kind: role, role: status}]}, text: {kind: contains, value: Welcome}}` | `assert_text` | validated `WebAssertTextParams` dump |
| `uiSnapshot: {scope: {kind: page, page: main}}` | `ui_snapshot` | validated `WebUiSnapshotParams` dump |

macOS command block:

| FSQ command shape | canonical `action_name` | `params` |
|---|---|---|
| `launchApp: {}` | `launch_app` | validated `MacOSLaunchAppParams` dump |
| `killApp: {}` | `kill_app` | validated `MacOSKillAppParams` dump |
| `clickOn: {target: Submit}` | `click_on` | validated `MacOSClickOnParams` dump |
| `clickOn: {point: {x: 120, y: 240}}` | `click_on` | validated `MacOSClickOnParams` dump with explicit point |
| `doubleClickOn: {target: File}` | `double_click_on` | validated `MacOSDoubleClickOnParams` dump |
| `rightClickOn: {target: File}` | `right_click_on` | validated `MacOSRightClickOnParams` dump |
| `typeText: {target: Search, text: query}` | `type_text` | validated `MacOSTypeTextParams` dump |
| `pressKey: {key: Enter}` | `press_key` | validated `MacOSPressKeyParams` dump |
| `hoverOn: {target: Menu}` | `hover_on` | validated `MacOSHoverOnParams` dump |
| `dragTo: {source: {target: File}, destination: {target: Folder}}` | `drag_to` | validated `MacOSDragToParams` dump |
| `takeScreenshot: {}` | `take_screenshot` | validated `MacOSTakeScreenshotParams` dump |
| `uiSnapshot: {}` | `ui_snapshot` | validated `MacOSUiSnapshotParams` dump |
| `assertVisible: {target: Done}` | `assert_visible` | validated `MacOSAssertVisibleParams` dump |
| `assertElementsOrder: {direction: vertical, elements: [...]}` | `assert_elements_order` | validated `MacOSAssertElementsOrderParams` dump |
| `assertWithAI: {prompt: ...}` | `assert_with_ai` | validated `MacOSAssertWithAIParams` dump |

Shared command block:

| FSQ command shape | canonical `action_name` | `params` |
|---|---|---|
| `waitMs: {duration_ms: 1000, reason: settle}` | `wait_ms` | validated `WaitMsParams` dump |

For text-entry commands omitting `textType`, `FsqExecutableStepAdapter` validates and stores the payload as literal text for YAML compatibility. For commands containing `textType: runtimeSecret`, the adapter validates the text-entry shape while preserving the environment variable name in `ExecutableStep.params`; final value resolution is owned by `core` immediately before driver invocation. The object shape `text: {runtimeSecret: NAME}` is normalized to `text: NAME` plus `textType: runtimeSecret` before parameter validation.

Runner-owned metadata such as valid `timeout` values should be extracted before driver parameter validation and stored in `ExecutableStep.timeout_ms`, not passed through to driver parameter models. The original raw command remains available in `ExecutableStep.metadata` for evidence and debugging.

Step kind mapping for known actions is owned by capability metadata:

| Authored alias | `ExecutableStep.kind` |
|---|---|
| `launchApp` | `setup` |
| `killApp` | `teardown` |
| `startBrowser` | `setup` |
| `closeBrowser` | `teardown` |
| `assert`, `assertVisible`, `assertNotVisible`, `assertText`, `assertElementsOrder`, `assertWithAI` | `assertion` |
| `takeScreenshot`, `startRecording`, `stopRecording`, `uiSnapshot` | `observation` |
| `waitMs` | `action` |
| all other commands | `action` |

Each generated step should include:

- `step_id`: stable within the case, using the case id and one-based command index, for example `fundamental_test_bing_com_website-step-001`.
- `source_ref`: `source_type="fsq"`, `source_id` set to the case path string, `step_index` set to the zero-based command index, and metadata containing the case name and platform.
- `metadata`: the original command payload, `authored_action_name`, canonical `capability_name`, replay metadata when applicable, and selected case metadata useful for evidence and debugging.
- `timeout_ms`: copied from command object `timeout` when present and valid.
- FSQ does not set default screenshot evidence policy on generated steps. During execution, `core.StepRunner` derives automatic evidence capture from the resolved capability plus `ExecutableStep.kind` and writes normalized `screenshot`/`ui_snapshot` artifacts when applicable.

Malformed command entries that cannot be reduced to one FSQ action must raise `ConfigurationError` with the case path and command index. Unknown actions, ambiguous replay aliases, actions without active `fsq_command` replay support in strict input, and payloads that fail the resolved capability parameter model validation must also raise `ConfigurationError` before execution starts, with enough context to identify the case path, command index, action name, and validation problem. A generated `inputText.text.runtimeSecret` ref is valid only as a pre-resolution replay value; other redaction markers or unresolved secret-like objects are invalid. Optional commands are still converted into executable steps; this adapter does not own optional/non-blocking execution semantics.

## Internal Structure

- `__init__.py`: Public exports only.
- `_loader.py`: Compatibility alias to canonical `case_dsl._loader`.
- `_step_adapter.py`: Compatibility alias to canonical `case_dsl._step_adapter`.
- `SPEC.md`: Module design.

## Python Architecture

- Architecture level: 2 Simple Package.
- Public API: `FSQ_CASE_SUFFIX`, `FsqCaseLoader`, `FsqCaseValidator`, `FsqCaseSerializer`, `FsqExecutableStepAdapter`, and `is_fsq_case_file` exported from `__init__.py`.
- Internal modules: `_loader.py` and `_step_adapter.py` are private implementation modules.
- Domain boundaries: canonical `case_dsl` owns deterministic YAML loading, lifecycle hook metadata validation, and conversion to shared executable-step contracts. It does not execute steps or hooks, resolve real secrets, resolve hook file paths, run shell commands, construct registries, create harnesses, or generate reports.
- Boundary models: parsed cases, lifecycle hooks, executable steps, text-entry runtime secret fields, and capability metadata models come from `models`.
- Dependency direction: imports public `models` only; registry snapshots are passed in by entry modules.
- Rationale: focused parsing/normalization behavior fits Level 2 and does not require orchestration layers.

## Error Handling

Invalid FSQ YAML raises `ConfigurationError` with the failing path. Unsupported schema versions, missing platform values, malformed hook metadata, and malformed command documents are rejected before strict-core execution starts. Hook entries with unknown action keys, no supported action, empty `runCase` paths, or empty `runShell` commands are invalid. A missing command document or empty command list is valid only as a goal-only case and is normalized to `commands=[]`.

## Verification Scope

- Verification covers lifecycle hook metadata normalization, malformed hook rejection, registry-backed command alias resolution, parameter-model validation, and deterministic `ExecutableStep` conversion.
- Boundary verification ensures lifecycle hooks remain metadata and are not emitted as command pseudo-steps by `FsqExecutableStepAdapter`.

## Current Invariants

- `*.fsq.yaml` is the canonical Case input format; `*.codex.yaml` is accepted for one deprecation cycle only.
- Single-document `*.fsq.yaml` files containing only valid Case metadata are supported as goal-only Cases. Two-document Cases with `[]` or an empty command list are also goal-only Cases.
- Configured `cases.dir` is treated as read-only input. Strict-core execution may parse FSQ case files from it, while dynamic LLM execution may read case files from it as raw text. Generated candidates and evidence remain under the output root; contained publication and explicit formatting writes are coordinated outside the DSL package.
- Markdown conversion reports are intentionally ignored and are not loaded as task inputs.
- FSQ commands are deterministic ordered input for the strict-core execution path when converted by `FsqExecutableStepAdapter`. Generated recorded cases may include strict replay refs and pure wait commands, but those are still deterministic authored input by the time strict execution begins.
- FSQ lifecycle hooks are deterministic metadata around Case execution, not commands in `case.commands`. FSQ validates hook syntax and preserves order; Execution coordinates owning modules so FSQ stays independent of path resolution, shell execution, harnesses, evidence, and reports.
- Deterministic command payload normalization uses the platform-selected capability registry snapshot. Authored command payloads use the same object field names as the capability parameter models, including `textType` on text-entry commands, which keeps case parsing, case generation, harness dispatch, and SDK schemas aligned to one payload contract while preserving authored names in metadata. Missing `textType` is interpreted as `literal` for YAML compatibility.
- Capability decorators and platform action catalogs are declaration-time inputs only. FSQ parsing consumes resolved `CapabilityDefinition` data from the registry snapshot and must not inspect decorated functions or platform catalog objects directly.
- `waitMs` is a generated strict replay alias for the inherited `wait_ms` CommonTool capability. It is validated by `WaitMsParams`, converted into an `ExecutableStep(action_name="wait_ms")`, and later handled by `StepRunner` through the normal registry path without invoking Android gesture or Web page actions.
- `assertWithAI` is parsed and validated like any other authored assertion command. This module does not evaluate AI assertions, build provider-backed evaluators, capture screenshots, or decide assertion verdicts.
- Web replay aliases such as `startBrowser`, `closeBrowser`, `navigateTo`, `navigateBack`, `clickOn`, `typeText`, `selectOption`, `hoverOn`, `waitFor`, `takeScreenshot`, `assertText`, and `uiSnapshot` are accepted only when the supplied registry snapshot contains the corresponding Web capabilities. Android registries must not accept Web-only replay aliases, and Web registries must not accept Android-only replay aliases.
- macOS replay aliases such as `launchApp`, `killApp`, `clickOn`, `doubleClickOn`, `rightClickOn`, `typeText`, `pressKey`, `hoverOn`, `dragTo`, `takeScreenshot`, `uiSnapshot`, `assertVisible`, `assertElementsOrder`, and `assertWithAI` are accepted only when the supplied registry snapshot contains the corresponding macOS capabilities. Shared replay aliases resolve to the active platform's capability definition from the registry snapshot; replay aliases unique to another platform remain invalid.
- `launchApp`/`killApp` and `startBrowser`/`closeBrowser` are treated as setup and teardown step kinds for strict-core execution. A trailing `closeBrowser` command should be passed to `StepSequenceRunner` as teardown so it still executes after an earlier normal-step failure.
- Commands marked `optional: true` are still converted into executable steps; optional/non-blocking execution semantics do not belong to this adapter.
- Parsed FSQ cases are not converted into LLM `Task` descriptions. For normal LLM `run --case-yaml` and `run --case-dir`, the CLI reads raw file text and builds goal/reference tasks without calling this module or executing lifecycle hooks.
- Config-level lifecycle hooks are outside this module's ownership. FSQ loads only Case-level lifecycle metadata; `config` loads config-level `caseLifecycle`, and Execution coordinates both through owning public contracts.
- `FsqExecutableStepAdapter` must not import or call `core`; it produces shared model contracts only. Higher-level entry code is responsible for passing those steps into core runners.
