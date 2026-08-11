# Module: models

## Purpose

Own all shared data structures, unified capability metadata contracts, invocation/result contracts, registry snapshot models, strict replay reference models, configuration value models, AI assertion request/result metadata, skill metadata, report metadata, and exception classes used across fsq-agent. This module is the only place where cross-module types and custom exceptions are defined.

This module does not own capability decorators, decorated-method marker attributes, reflection/discovery helpers, platform action catalogs, or catalog-backed declaration validation. Those declaration mechanics live in `capabilities` and produce the serializable contracts owned here.

## Dependencies

No project module dependencies. May depend on external libraries such as `pydantic` and standard library typing modules.

## Public Interface

Current `__init__.py` exports via `__all__`:

Platform-neutral task, run, report, knowledge, capability, and execution exports:

- `DoctorRequest`: Pydantic boundary model for platform, output format, terminal interaction capability, repair policy, working directory, and bounded probe timeout. JSON format validation rejects repair.
- `DoctorFix`: Pydantic model for one actionable remediation with description, optional command, optional verification command, optional environment-variable name, optional documentation URL, and optional closed safe-repair action id.
- `DiagnosticProbeResult`: Provider/platform-neutral sanitized probe fact with stable id, category, status, summary, optional safe metadata, prerequisite ids, and fixes.
- `DoctorCheckResult`: Pydantic model for one normalized doctor check with stable id, category, `pass|warn|fail|skip` status, summary, ordered fixes, and sanitized metadata.
- `DoctorRepairResult`: Pydantic model for one safe-repair attempt containing action id, sanitized target, `applied|declined|skipped|failed` status, optional backup path, and rerun check ids; it never stores repaired values.
- `DoctorRepairAction`: Closed repair-action discriminator limited to workspace initialization, non-secret environment update, and cached Copilot provider-token refresh.
- `DoctorReport`: Schema-versioned Pydantic aggregate containing selected platform/source, one overall status/exit code including cancellation `130`, ordered checks and repairs, and summary counts. Schema version `1` has no diagnostic-mode, target-readiness, or affected-target field.
- `DoctorProgressEvent`: Pydantic model for one operator-visible doctor lifecycle event. Event types are `phase_started`, `check_started`, `action_required`, `check_completed`, `repair_started`, `repair_completed`, and `summary_ready`; events carry stable phase/check/repair identity, safe status/summary fields, and optional check/repair/report payloads without entered values or secrets. `action_required` is a transient presentation fact carrying the safe actionable check and is never a `DoctorStatus` or persisted report check state.
- `DoctorProgressSink`: Synchronous callable type accepted by doctor/config/provider/core diagnostic boundaries to receive `DoctorProgressEvent` values as work occurs.
- `EnvironmentFileUpdate`: Pydantic model describing an atomic `.env` update by path, changed key names, optional backup path, and preservation metadata without containing values.

- `Task`: Pydantic model describing a dynamic LLM goal/reference task, optional metadata, optional explicit planning reference kind/text, optional execution key actions, one final `verification_goal`, retry limits, timeout, and knowledge references. Only `description` is required. `planning_reference_kind` may identify first-party planning inputs such as `goal` or `raw_case`; `planning_reference_text` stores the authoritative text the pre-planner should use before falling back to legacy goal/description behavior. Execution key actions are planning context only; final verification checks `verification_goal` against execution evidence.
- `AGENT_FINAL_OUTPUT_SCHEMA_VERSION`: Constant containing the current supported final-output schema version. The runtime supports only the current schema; compatible schema evolution may add fields, while breaking changes replace the current schema rather than exposing a user-selectable schema configuration.
- `AgentTaskInput`: Pydantic model describing the structured task envelope rendered into the model input. It includes a schema version, the task, complete key actions for execution planning, the single `verification_goal`, optional runtime policy text, and the final output contract name expected for the run.
- `AgentPlanItem`: Pydantic model for one planned or adjusted agent step in final output.
- `AgentFinalOutput`: Pydantic model for the OpenAI Agents SDK structured final output contract. It contains schema version, task status, summary, pre-plan, plan updates, goal-satisfaction claims, evidence, and errors.
- `ToolCallRecord`: Pydantic model for a normalized real tool invocation reconstructed from run events, including true tool name, origin (`agent_tool`, `common`, `platform`, `runtime`, or `unknown`), arguments, output preview, artifact reference, status, timing, and error fields.
- `FsqCaseHookAction`: Pydantic model for one normalized FSQ lifecycle hook action. It stores the authored hook action name (`runCase` or `runShell`), the non-empty string value, and any safe metadata needed to preserve order and validation context. It does not execute hooks.
- `FsqCaseHook`: Pydantic model for one FSQ lifecycle hook entry from `onCaseStart` or `onCaseComplete`. It accepts an authored YAML mapping containing `runCase`, `runShell`, or both; rejects entries with no supported hook action or unknown hook keys; rejects empty string values; and preserves the authored key order as an ordered list of `FsqCaseHookAction` values so `runShell` before `runCase` and `runCase` before `runShell` remain distinguishable.
- `CaseLifecycleSettings`: Pydantic model for config-level strict case lifecycle hooks. It exposes `on_case_start`/`onCaseStart` and `on_case_complete`/`onCaseComplete`, reuses the same `FsqCaseHook` entry model as case metadata, and defaults omitted lifecycle fields to empty lists. It validates configuration shape only and does not execute hooks, resolve hook paths, or run shell commands.
- `FsqCaseConfig`: Pydantic model describing the metadata document from an FSQ AI Test DSL `.codex.yaml` case. It includes optional lifecycle hook fields `on_case_start`/`onCaseStart` and `on_case_complete`/`onCaseComplete`, each normalized to an ordered list of `FsqCaseHook` entries and defaulting to an empty list when omitted.
- `FsqCase`: Pydantic model containing an FSQ case path, parsed metadata including lifecycle hooks, and command list.
- `ExecutionPlan`: Pydantic model containing ordered `ExecutionStep` items and planning rationale.
- `ExecutionStep`: Pydantic model for one planned tool action with expected outcome and retry policy.
- `StepResult`: Pydantic model for one executed, skipped, failed, or adjusted pre-plan step outcome, timings, evidence references, and error summary.
- `VerificationResult`: Pydantic model describing whether the task goal was achieved and why.
- `TaskResult`: Pydantic model returned by the agent after execution, verification, and report generation.
- `RunEvent`: Pydantic model for one live execution timeline event emitted during a task run, including run/task identity, sequence, timestamp, event type, title, message, optional tool call metadata, output preview, duration, and structured payload.
- `RunEventSink`: Callable type accepted by orchestration/runtime/tool code to receive `RunEvent` values synchronously or asynchronously.
- `ReportArtifact`: Pydantic model describing generated report paths and evidence bundle paths.
- `KnowledgeBundle`: Pydantic model containing loaded private knowledge and loader diagnostics for agent context. Loader diagnostics are operational metadata and must not be rendered into model-facing execution prompts.
- `PAGE_KNOWLEDGE_INDEX_SCHEMA_VERSION`: Constant containing the supported page-knowledge index schema version.
- `PAGE_KNOWLEDGE_PAGE_SCHEMA_VERSION`: Constant containing the supported page-knowledge page-node schema version.
- `GOAL_PRE_PLAN_SCHEMA_VERSION`: Constant containing the supported goal pre-plan schema version.
- `PageKnowledgeIndex`: Pydantic model for the public page-knowledge `index.md` JSON payload. It contains schema version, product, platform, page root, and concise page records used for fast goal-to-page lookup.
- `PageKnowledgeIndexEntry`: Pydantic model for one indexed page, including page id, relative file path, display name, and intent keywords.
- `PageKnowledgePage`: Pydantic model for one page graph node stored in `knowledge/pages/*.md`. It contains page id, name, semantic identifiers, optional images, and page elements.
- `PageIdentifier`: Pydantic model for one semantic page-recognition signal. It intentionally does not contain locators.
- `PageImage`: Pydantic model for an optional page image reference and description.
- `PageElement`: Pydantic model for one page element, including name, role, reference locators, and supported operations.
- `ReferenceLocator`: Pydantic model for a non-authoritative locator candidate observed for a page element, including confidence and notes.
- `ElementOperation`: Pydantic model for one supported operation on a page element and its result.
- `OperationResult`: Pydantic model for the operation result, optionally linking to a destination `page_id` when the operation is a graph transition.
- `GoalPrePlan`: Pydantic model used by internal dynamic goal planning. It contains the input goal/reference text, ordered key actions, one `verification_goal`, relevant page ids, summary, and warnings.
- `GoalKeyAction`: Pydantic model for one ordered key action generated from a goal/reference task and page knowledge.
- `ReplayPolicy`: Pydantic model describing how a capability participates in generated strict replay. It contains `kind="fsq_command"` and an `alias`, which is the primary authored strict YAML command name such as `waitMs`, `tapOn`, or `startBrowser`; the active registry resolves it to the canonical capability. Runtime-secret text references live on text-entry PlatformTool parameters through `textType: runtimeSecret`, not through replay dependency policies.
- `CapabilityDefinition`: Pydantic model describing one recordable CommonTool or PlatformTool capability. It contains canonical `name`, explicit `executor_kind` (`common` for inherited CommonTools or `driver` for driver-backed PlatformTools), parameter model/schema identity, `step_kind`, description, optional platform/backend/owner metadata, optional `post_action_delay_seconds`, `sensitivity`, optional `ReplayPolicy`, computed `fsq_command_alias`, and safe provenance metadata such as driver method for driver-backed PlatformTools. It is serializable metadata only and must not hold SDK tool objects, driver instances, function callables, accepted-alias lists, per-capability SDK schema strictness flags, or default screenshot-capture policy. `post_action_delay_seconds=None` means inherit the configured family default; `0` explicitly disables the runner-owned post-action delay for that capability; positive values override the configured default.
- `CapabilityRegistrySnapshot`: Pydantic model containing the validated serializable capability graph used by FSQ parsing, SDK tool exposure, event metadata, reports, and audit evidence. Resolution accepts canonical capability names plus `ReplayPolicy(kind="fsq_command").alias` values for active registered capabilities.
- `CapabilityInvocation`: Pydantic model describing one canonical execution request with capability name, params, run/step identity, source reference, authored action metadata, and safe invocation metadata.
- `CapabilityExecutionResult`: Pydantic model describing one normalized capability result from `StepRunner`, including status, output, error/failure category, duration, artifact refs, sensitivity/replay metadata, safe replay params, and backend provenance.
- `AgentToolDefinition`: Pydantic model describing one dynamic-only AgentTool helper exposed to the OpenAI Agents SDK runtime. AgentTool definitions are not capability definitions and do not contain replay policy.
- `AgentToolCall`: Pydantic model describing one SDK-neutral AgentTool invocation request against a canonical helper name.
- `AgentToolResult`: Pydantic model describing a normalized AgentTool invocation response before it is serialized for the SDK runtime and run events.
- `CommonToolCall` and `CommonToolResult`: Backward-compatible private aliases for AgentTool call/result models. New code uses AgentTool names.
- `ToolDefinition`: Backward-compatible diagnostic alias for serializable capability metadata. It is not an authoritative schema source.
- `ToolCall`: Backward-compatible diagnostic alias for serializable tool invocation requests.
- `ToolResult`: Backward-compatible diagnostic alias for normalized tool invocation responses.
- Execution-core contract models: Pydantic models for StepRunner, runner events, harness inputs/outputs, capability invocation/result metadata, and EvidenceBundle manifests. These include `ExecutableStep`, `SourceRef`, `RetryPolicy`, legacy `EvidencePolicy` compatibility fields when still present, `StepCallInfo`, `StepPhaseReport`, `RunnerStepResult`, `RunnerEvent`, `HarnessContext`, `HarnessActionResult`, `HarnessArtifactRef`, `EvidenceBundle`, and `EvidenceManifest`.
- `WaitMsParams`: Pydantic model for the inherited `wait_ms` CommonTool capability and strict replay `waitMs` alias. It contains a bounded `duration_ms` value and optional reason text, and represents a pure elapsed-time wait that must not touch platform state.
- `TextSourceType`: Shared text-source discriminator for recordable platform text-entry parameters. Supported serialized values are `literal` and `runtimeSecret`; omission defaults to `literal` for historical YAML compatibility.

Android platform exports:

- `AndroidLocator`: Pydantic model for Android target locators with optional `resourceId`, `accessibilityId`, `text`, `className`, and `xpath` fields.
- `AndroidPoint`: Pydantic model for integer Android screen coordinates used by point-based taps and swipes.
- `AndroidScreenSize`: Pydantic model for positive integer Android screen dimensions used as the coordinate reference for recorded point-based strict replay.
- `AndroidLaunchAppParams`: Pydantic model for `launch_app` driver parameters, including optional `app_id`.
- `AndroidKillAppParams`: Pydantic model for `kill_app` driver parameters, including optional `app_id`.
- `AndroidTapOnParams`: Pydantic model for `tap_on` parameters. It requires either a `target` string or a non-empty `locator`.
- `AndroidTapAtParams`: Pydantic model for `tap_at` parameters. It requires a `point` with integer `x` and `y` Android screen coordinates and accepts an optional `reference_screen_size` so strict replay can scale recorded coordinates to the current device size.
- `AndroidLongPressOnParams`: Pydantic model for `long_press_on` parameters. It uses the same target contract as `AndroidTapOnParams`.
- `AndroidInputTextParams`: Pydantic model for `input_text` parameters. It requires string `text`, optional serialized `textType` defaulting to `literal`, and either a `target` or non-empty `locator`. When `textType="runtimeSecret"`, `text` is an allowed environment variable name resolved by `core` immediately before driver invocation; concrete Android drivers receive only resolved literal strings.
- `AndroidPressKeyParams`: Pydantic model for `press_key` parameters with one normalized required key string.
- `AndroidSwipeParams`: Pydantic model for `swipe` parameters. It accepts either a direction string or both `start` and `end` points, with optional `reference_screen_size` for point-based strict replay scaling and optional duration in milliseconds.
- `AndroidUiTreeParams`: Pydantic model for the Android `uiTree` replay alias of the canonical `ui_snapshot` driver observation capability. It accepts no fields and exists so dynamic agents and strict Android cases can request a compact current Android UI hierarchy through the normal harness action schema path. The model must remain fieldless in the Android compact-snapshot cycle; compaction options are not user-configurable parameters. Android compact UI snapshots preserve the `{"xml": ...}` payload shape, may omit layout-only/default data, and clip long text-like attributes to the first 50 characters.
- `AndroidPerformActionsParams`: Pydantic model for non-exposed `perform_actions` parameters that wraps a W3C actions array as `actions`. The model existing in `models` is not sufficient to expose the action to dynamic LLM runs; concrete backend exposure requires an implemented, decorated driver capability that appears in harness `action_space()`.
- `AndroidAssertVisibleParams`: Pydantic model for `assert_visible` parameters. It uses the Android target contract plus optional assertion metadata.
- `AndroidAssertNotVisibleParams`: Pydantic model for `assert_not_visible` parameters. It uses the Android target contract plus optional assertion metadata.
- `AndroidTextAssertion`: Pydantic model for text assertion predicates, supporting `contains` and `equals`.
- `AndroidElementState`: Pydantic model for element locators plus expected boolean Android state fields `enabled`, `checked`, `selected`, `clickable`, and `focused`.
- `AndroidAssertStateParams`: Pydantic model for FSQ `assert` driver parameters. It supports `element` existence/state assertions and optional `text` assertions.
- `AndroidAssertWithAIParams`: Pydantic model for authored Android visual assertion parameters with a required prompt and optional assertion metadata. This parameter model is consumed by decorated Android backend driver tools such as `UiAutomator2AndroidDriver.assert_with_ai`.

Web platform exports:

- `WebLocator`: Pydantic model for Web target locators with optional `role`, `name`, `text`, `label`, `placeholder`, `testId`, `css`, and `xpath` fields. Web action parameter models that accept either a semantic `target` or `locator` require at least one populated target signal.
- `WebStartBrowserParams`: Pydantic model for the explicit `start_browser` Web lifecycle capability. It accepts no fields in the first lifecycle batch.
- `WebCloseBrowserParams`: Pydantic model for the explicit `close_browser` Web lifecycle capability. It accepts no fields in the first lifecycle batch.
- `WebNavigateToParams`: Pydantic model for `navigate_to` parameters, including required `url` and optional Playwright-safe `wait_until` lifecycle state.
- `WebNavigateBackParams`: Pydantic model for `navigate_back` parameters. It accepts no fields.
- `WebClickOnParams`: Pydantic model for `click_on` parameters. It requires either an exact snapshot `target` or non-empty `locator`, with optional human-readable `element`, `double_click`, `button`, and `modifiers` fields.
- `WebTypeTextParams`: Pydantic model for `type_text` parameters. It requires string `text`, optional serialized `textType` defaulting to `literal`, and either an exact snapshot `target` or non-empty `locator`, with optional human-readable `element`, `submit`, and `slowly` fields. When `textType="runtimeSecret"`, `text` is resolved by `core` before the Web driver receives the command.
- `WebSelectOptionParams`: Pydantic model for `select_option` parameters. It requires one or more `values` plus either an exact snapshot `target` or non-empty `locator`, with optional human-readable `element`.
- `WebHoverOnParams`: Pydantic model for `hover_on` parameters. It requires either an exact snapshot `target` or non-empty `locator`, with optional human-readable `element`.
- `WebPressKeyParams`: Pydantic model for `press_key` parameters with one normalized required key string.
- `WebWaitForParams`: Pydantic model for `wait_for` parameters. It requires exactly one bounded wait condition: `time`, `text`, or `text_gone`.
- `WebTakeScreenshotParams`: Pydantic model for `take_screenshot` parameters. It accepts optional exact snapshot `target` or non-empty `locator`, optional human-readable `element`, optional image `type`, and optional `full_page`; artifact filenames are not user-controlled through the parameter model.
- `WebAssertVisibleParams`: Pydantic model for Web `assert_visible` parameters. It requires either `target` or non-empty `locator` plus optional assertion metadata.
- `WebAssertNotVisibleParams`: Pydantic model for Web `assert_not_visible` parameters. It requires either `target` or non-empty `locator` plus optional assertion metadata.
- `WebTextAssertion`: Pydantic model for Web text assertion predicates, supporting `contains` and `equals`.
- `WebAssertTextParams`: Pydantic model for `assert_text` parameters. It supports optional `target` or `locator` plus a text predicate.
- `WebPageSnapshotParams`: Pydantic model for the read-only `page_snapshot` Web observation capability. It accepts optional exact snapshot `target` or non-empty `locator`, optional `depth`, and optional `boxes` so dynamic agents and strict Web cases can request a current accessibility/DOM-oriented page snapshot through the normal harness action schema path.
- `WebAssertWithAIParams`: Pydantic model for authored Web visual/page assertion parameters with a required prompt and optional assertion metadata. This parameter model is consumed by decorated Web backend driver tools such as `PlaywrightWebDriver.assert_with_ai`.

Windows platform exports:

- `WindowsLocator`: Pydantic model for Windows control locators. Windows element actions require a non-empty `locator`; optional `target` values are descriptive only and are not used for lookup.
- `WindowsPoint` and `WindowsOffset`: Pydantic coordinate models for non-negative absolute points and non-zero signed offsets.
- `WindowsMouseSource` and `WindowsMouseDestination`: Pydantic models selecting one supported locator, point, or destination-offset mode.
- `WindowsHoverOnParams`, `WindowsScrollOnParams`, and `WindowsDragToParams`: Pydantic models for Windows mouse actions, including non-zero `wheel_dist` and default-left mouse button behavior where applicable.
- `WindowsTypeTextParams`: Pydantic model for `type_text` parameters. It requires string `text`, optional serialized `textType` defaulting to `literal`, and a Windows target/locator. When `textType="runtimeSecret"`, `text` is resolved by `core` before the Windows driver receives the command.

macOS platform exports:

- `MacOSLocator`: Pydantic model for macOS target locators with optional serialized fields `accessibilityId`, `name`, `label`, `value`, `role`, `controlType`, `className`, `xpath`, `predicate`, and `point`. macOS action parameter models that accept a locator require at least one populated locator signal when no semantic `target` or explicit `point` is supplied.
- `MacOSPoint`: Pydantic model for integer macOS screen coordinates with `x` and `y` fields.
- `MacOSLaunchAppParams`: Pydantic model for `launch_app` driver parameters, including optional `bundle_id`, `app_path`, `arguments`, and `environment`. Runtime defaults for target app identity come from environment-backed settings rather than shareable YAML.
- `MacOSKillAppParams`: Pydantic model for `kill_app` driver parameters, including optional `bundle_id` and optional session-close behavior.
- `MacOSClickOnParams`: Pydantic model for `click_on` parameters. It requires a semantic `target`, non-empty `locator`, or explicit `point`, with optional modifier metadata.
- `MacOSDoubleClickOnParams`: Pydantic model for `double_click_on` parameters. It uses the same target/locator/point contract as `MacOSClickOnParams`.
- `MacOSRightClickOnParams`: Pydantic model for `right_click_on` parameters. It uses the same target/locator/point contract as `MacOSClickOnParams`.
- `MacOSHoverOnParams`: Pydantic model for `hover_on` parameters. It uses the same target/locator/point contract as `MacOSClickOnParams`.
- `MacOSTypeTextParams`: Pydantic model for `type_text` parameters. It requires string `text`, optional serialized `textType` defaulting to `literal`, and accepts an optional semantic `target`, non-empty `locator`, or explicit `point` for focusing before input. When `textType="runtimeSecret"`, `text` is resolved by `core` before the macOS driver receives the command.
- `MacOSPressKeyParams`: Pydantic model for `press_key` parameters with one required key or key sequence and an optional modifier list.
- `MacOSDragToParams`: Pydantic model for `drag_to` parameters. It requires source and destination values, each expressed as a semantic target, non-empty locator, or explicit point, with optional duration metadata.
- `MacOSTakeScreenshotParams`: Pydantic model for `take_screenshot` parameters. It accepts optional full-screen/window metadata; artifact paths are owned by `ArtifactStore`, not by user payloads.
- `MacOSUiSnapshotParams`: Pydantic model for the read-only `ui_snapshot` macOS observation capability. It accepts optional maximum depth and simplification flags and returns a bounded Appium Mac2 page-source/control-tree snapshot.
- `MacOSAssertVisibleParams`: Pydantic model for macOS `assert_visible` parameters. It requires a semantic `target`, non-empty `locator`, or explicit `point` plus optional assertion metadata.
- `MacOSAssertElementsOrderParams`: Pydantic model for `assert_elements_order` parameters. It requires an ordered `elements` list whose items contain a semantic target or macOS locator, accepts `direction` constrained to `vertical` or `horizontal`, optional zero-based `expected_order`, optional pixel `tolerance`, and `require_all` defaulting to true. Driver output for this assertion includes `direction`, `elements_found`, `elements_total`, `actual_order`, `expected_order`, and per-element center positions.
- `MacOSAssertWithAIParams`: Pydantic model for authored macOS visual assertion parameters with a required prompt and optional assertion metadata. This parameter model is consumed by decorated Appium Mac2 backend driver tools such as `AppiumMac2Driver.assert_with_ai`.

Provider-backed assertion exports:

- `AIAssertionRequest`: Pydantic model describing one provider-backed platform visual assertion request. It includes platform, prompt, screenshot artifact reference or screenshot path, optional UI/context metadata, run/step metadata, and provider/model metadata fields safe for reports.
- `AIAssertionResult`: Pydantic model describing one provider-backed platform visual assertion verdict. It includes status/pass boolean, explanation, confidence when available, provider/model metadata, token/latency diagnostics when safe, and evidence artifact references. It must not contain raw secret values or hidden model reasoning.

Shared settings exports:

- `OpenAIAgentsSettings`: Pydantic model for OpenAI Agents SDK provider configuration, including effective provider selection (`github_copilot` by default, or `azure_openai` when selected by `FSQ_LLM_PROVIDER` or YAML compatibility input), tracing policy, turn limits, file-based prompt template customization, internal context trimming policy, internal AgentTool output artifact policy, and resolved provider runtime values. GitHub Copilot uses fixed model `gpt-5.5`; Azure OpenAI endpoint, deployment/model, and API key are sourced from fixed environment variable names by configuration loading rather than from YAML fields. GitHub Copilot OAuth token storage is runtime-owned under the configured workspace and is not exposed as a YAML token setting. The agent runtime uses the Responses API for configured model providers.
- `OpenAIAgentPromptConfig`: Pydantic model containing optional Jinja template file paths and scalar prompt variables. It does not contain inline or file-backed custom instruction fields; project-specific guidance belongs in project knowledge, and reusable guidance belongs in configured skills.
- `ContextTrimmingSettings`: Pydantic model controlling SDK model-input trimming for older large tool outputs, including recent turn retention, maximum inline tool output size, preview size, and optional trimmable tool names. These values are internal runtime defaults and are not part of the default YAML surface.
- `LocalToolOutputSettings`: Pydantic model controlling how local SDK function tools write full outputs to per-run artifacts and decide whether model-facing responses contain full output or artifact references. These values are internal runtime defaults and one-option policy fields should not be exposed as YAML knobs.
- `RuntimeSecretSettings`: Pydantic model listing environment variable names that runtime-secret text input may reference. Values are loaded through normal environment or `.env` loading, validated for presence by runtime initialization, resolved only in memory, and never stored in YAML case files.
- `ExecutionSettings`: Pydantic model grouping runner-owned execution policy that applies across dynamic and strict execution.
- `PostActionDelaySettings`: Pydantic model containing non-negative post-action delay defaults in seconds. `platform` defaults to `1.0` and applies to PlatformTool capabilities when capability metadata does not override it. `common` defaults to `0.0` and applies to CommonTool capabilities when capability metadata does not override it.

Platform settings exports:

- `CaseLifecycleSettings`: Pydantic model selecting config-level strict case lifecycle hooks used by strict execution. It reuses FSQ hook entry models so config-level hooks and case-level hooks share validation and ordering semantics.
- `HarnessSettings`: Pydantic model selecting the platform harness configuration used by goal-driven task execution. It contains platform-specific harness settings only; runner-owned execution pacing belongs to `ExecutionSettings`.
- `AndroidHarnessSettings`: Pydantic model for the built-in Android harness runtime construction. YAML selects the Android backend; configuration loading fills optional `app_id` and device `serial` from `FSQ_ANDROID_APP_ID` and `FSQ_ANDROID_SERIAL`. Strict-core execution does not enable AI assertion evaluators through this settings model.
- `WebHarnessSettings`: Pydantic model for the built-in Web harness runtime construction. YAML selects the Playwright backend, local browser channel, headless mode, optional base URL, and optional viewport settings; configuration loading fills the required local browser executable path from `FSQ_WEB_BROWSER_EXECUTABLE_PATH`. Strict-core execution does not enable AI assertion evaluators through this settings model.
- `WindowsHarnessSettings`: Pydantic model for the built-in Windows harness runtime construction. YAML selects backend `pywinauto`; configuration loading fills local executable path, pywinauto backend kind, optional window title regex, and optional default launch arguments from `FSQ_WINDOWS_APP_PATH`, `FSQ_WINDOWS_BACKEND_KIND`, `FSQ_WINDOWS_WINDOW_TITLE_RE`, and `FSQ_WINDOWS_LAUNCH_ARGS`. Legacy YAML fields for those local values remain compatibility inputs for older configs, but environment values take precedence. Strict-core execution does not enable AI assertion evaluators through this settings model.
- `MacOSHarnessSettings`: Pydantic model for the built-in macOS harness runtime construction. YAML selects backend `appium_mac2` and stable non-sensitive defaults such as page-source depth and action timeout seconds; configuration loading fills operator-local Appium server URL, bundle id, and app path from `FSQ_MACOS_APPIUM_SERVER_URL`, `FSQ_MACOS_BUNDLE_ID`, and `FSQ_MACOS_APP_PATH`. Strict-core execution does not enable AI assertion evaluators through this settings model.
- `AgentContextSettings`: Pydantic model grouping knowledge-root resources used to build agent context.
- `AgentKnowledgeSettings`: Pydantic model containing the configured private knowledge `root_dir`, nested skill resource configuration, and optional pre-plan page-knowledge configuration.
- `KnowledgeSkillSettings`: Pydantic model containing the skill directory under the knowledge root and the configured `SkillConfig` items loaded from that directory.
- `PrePlanKnowledgeSettings`: Pydantic model containing the optional page-knowledge graph directory under the knowledge root. When omitted, internal dynamic pre-plan uses the normal knowledge root.
- `SkillConfig`: Pydantic model for one configured automation skill source.
- `SkillBundle`: Pydantic model containing successfully loaded skill instructions, optional source files, and descriptions. Broken optional skills are skipped by the skills module instead of being represented as warning-only bundles for model context.
- `AgentSettings`: Pydantic model for agent-level execution defaults such as step timeout. Model selection belongs to `OpenAIAgentsSettings`, and inactive loop/retry knobs are not part of the public YAML surface.
- `WorkspaceSettings`: Pydantic model for the managed fsq-agent workspace root. Marker file name and auto-initialization behavior are internal workspace policy rather than YAML settings.
- `CaseSettings`: Pydantic model for the read-only FSQ case directory.
- `OutputSettings`: Pydantic model for the managed output root. The per-run report/artifact layout under the output root is internal policy. All logs, reports, tool artifacts, and generated files must live under the output root.

Exception exports:

- `FsqAgentError`: Base exception for all project errors.
- `ConfigurationError`: Raised when configuration is missing or invalid.
- `PlanningError`: Raised when a task cannot be converted into an executable plan.
- `ToolExecutionError`: Raised when a tool call fails after retries or returns invalid output.
- `VerificationError`: Raised when verification cannot complete.
- `ReportGenerationError`: Raised when report generation fails.

## Platform Contract Blocks

Shared platform contracts:

- `HarnessPlatform`, `HarnessSettings`, `HarnessContext`, `HarnessActionResult`, `HarnessArtifactRef`, capability metadata, invocation/result contracts, and evidence manifest models are platform-neutral contracts.
- Platform-specific action parameter models live in this module only when they are consumed across module boundaries by `fsq`, `core`, `cli`, `agent`, or tests.
- Platform parameter models forbid unexpected fields and produce canonical `model_dump(mode="json", exclude_none=True)` output.

Android contracts:

- Android parameter models include locator, point, lifecycle, gesture, input, assertion, UI-tree observation, W3C-action placeholder, and Android AI assertion models.
- Android settings are grouped under `AndroidHarnessSettings` and are selected by `HarnessSettings.platform == "android"`.
- Android explicit observation command is represented as alias `uiTree` resolving to canonical `ui_snapshot`; automatic runner evidence captures normalized `ui_snapshot` content sourced from the Android UI hierarchy.

Web contracts:

- Web parameter models include browser lifecycle, locator, navigation, click, text typing, select, hover, key, wait, screenshot, page snapshot, deterministic assertions, and Web AI assertion models.
- Web settings are grouped under `WebHarnessSettings` and are selected by `HarnessSettings.platform == "web"`.
- Web explicit observation command is represented as `page_snapshot`/`pageSnapshot`; automatic runner evidence captures normalized `ui_snapshot` content sourced from Web page/accessibility snapshot data.
- Web action parameter design follows Playwright MCP's LLM-facing core automation conventions where appropriate: action targets are replayable semantic locators or stable unique selectors, optional `element` fields are human-readable descriptions for interaction permission/auditing, screenshots are evidence/debugging observations rather than the normal action-selection substrate, and unsafe/opt-in capability families are not exposed.

Windows contracts:

- Windows parameter models include locator, lifecycle, desktop click variants, text input, key input, UI snapshot, deterministic visibility assertions, and Windows AI assertion models.
- Windows settings are grouped under `WindowsHarnessSettings` and are selected by `HarnessSettings.platform == "windows"`.
- Windows explicit observation command is represented as `ui_snapshot`/`uiSnapshot`; automatic runner evidence captures normalized `ui_snapshot` content through the same driver observation contract.
- Windows launch defaults such as app path, pywinauto backend kind, window title regex, and configured launch arguments are runtime settings filled from environment variables by `config`. `WindowsLaunchAppParams.extra_args` remains a per-step append-only override rather than a replacement for settings ownership.
- Windows action parameter design follows desktop conventions shared with macOS where possible: public replay aliases use `clickOn`, `doubleClickOn`, `rightClickOn`, `typeText`, `pressKey`, and `uiSnapshot`; coordinate-only actions are not exposed in the first Windows batch.

macOS contracts:

- macOS parameter models include locator, point, lifecycle, desktop click variants, hover, drag/drop, text input, key input, screenshot, UI snapshot, deterministic visibility/order assertions, and macOS AI assertion models.
- macOS settings are grouped under `MacOSHarnessSettings` and are selected by `HarnessSettings.platform == "macos"`.
- macOS explicit observation command is represented as `ui_snapshot`/`uiSnapshot`; automatic runner evidence captures normalized `ui_snapshot` content through the same driver observation contract.
- macOS action parameter design follows desktop conventions shared with Windows where possible: public replay aliases use `clickOn`, `typeText`, `pressKey`, and `uiSnapshot`; coordinate actions are represented as explicit point payloads inside semantic actions rather than as separate public replay aliases.

## Internal Structure

- `__init__.py`: Public exports only.
- `_task.py`: Task, plan, step, result, and verification models.
- `_agent_io.py`: Structured agent task input, final output, plan item, schema version, and normalized tool-call record models.
- `_events.py`: Live run event model and event sink type alias.
- `_doctor.py`: Doctor request/check/fix/repair/report models plus generic sanitized probe, settings-inspection, and environment-update boundary models.
- `_fsq.py`: FSQ AI Test DSL case metadata, reusable lifecycle hook models, config lifecycle hook settings, and case models.
- `_tools.py`: Unified capability metadata, replay policy, invocation/result contracts, registry snapshot models, AgentTool definition/call/result models, and temporary backward-compatible diagnostic aliases.
- `_ai_assertion.py`: Provider-backed platform AI assertion request/result models.
- `_core.py`: Shared execution-core contract models for executable steps, strict replay refs, pure wait params, runner phases/events, harness context/results, artifact references, evidence manifests, and active platform parameter models used across `fsq`, `cli`, and `core`.
- `_settings.py`: Settings value models.
- `_skills.py`: Skill configuration and loaded skill bundle models.
- `_report.py`: Report artifact and evidence models.
- `_knowledge.py`: Knowledge bundle model.
- `_page_knowledge.py`: Public page-knowledge graph schema models and goal pre-plan output models.
- `_exceptions.py`: Shared exception hierarchy.
- `SPEC.md`: Module design.

## Python Architecture

- Architecture level: 2 Simple Package.
- Public API: shared Pydantic models, dataclasses when serialization is not required, constants, callable type aliases, and project exception classes exported from `__init__.py`.
- Internal modules: all `_*.py` files are private implementation files and must not be imported directly outside `models`.
- Domain boundaries: this module owns contracts and validation only; decorator declaration/discovery, execution, IO, provider construction, parsing, and reporting behavior belong to their owning modules.
- Boundary models: Pydantic models validate external inputs, config files, tool/capability invocations, run events, reports, and evidence manifests. Capability metadata models are serializable contracts, not runtime objects.
- Dependency direction: `models` imports no project modules. All other modules may import public symbols from `models` through `fsq_agent.models`.
- Rationale: shared contracts need central ownership to prevent cycles, while no persistence or framework orchestration justifies a higher architecture level.

## Error Handling

All custom exceptions inherit from `FsqAgentError`. Exceptions carry concise human-readable messages and optional structured context fields where useful. Other modules must import exception classes from this module rather than defining their own.

## Current Invariants

- Centralizing types prevents circular imports and inconsistent result schemas.
- Doctor and probe contracts are serializable, secret-free boundary data. They do not execute checks, perform repairs, prompt users, open network connections, or render terminal output.
- Progress events are presentation-neutral facts. Doctor emits `action_required` while it owns repair control flow; presentation consumers only render events and must not change diagnostic control flow or final report data.
- New cross-module execution contracts must be added to this module rather than to `fsq_agent.core`, because cross-module data structures live only in `models`.
- Capability metadata is the authoritative executable contract for recordable CommonTools and PlatformTools. `CapabilityDefinition`, `ReplayPolicy`, step kind, post-action delay overrides, and registry snapshots are the runtime authority instead of separate harness function or static Android action schemas. Live `CapabilityExecutorKind` values are `common` and `driver`; `harness` is not a live executor kind. Decorator declarations and platform catalog validation live in `capabilities`; `models` owns only the serializable contracts they produce. AgentTools use separate dynamic-only definition/call/result models and do not enter strict capability registries.
- Shared platform parameter contracts and strict replay reference contracts must live in this module when they are consumed by more than one project module. Android parameter models are shared by `fsq` for YAML normalization, by `core` for dispatch validation, and by concrete drivers for typed backend calls. Strict replay references are shared by `fsq` parsing and `cli` strict replay resolution.
- FSQ lifecycle hook metadata is a shared case contract. The models preserve operator-authored hook action order but do not execute hooks, resolve hook paths, run shell commands, construct registries, or generate evidence. Hook execution belongs to the strict CLI entry layer, while hook YAML validation belongs to the `fsq`/model boundary.
- Config-level case lifecycle hook settings reuse the same `FsqCaseHook` entry models as case metadata. This keeps `caseLifecycle` configuration and `.codex.yaml` lifecycle fields aligned while preserving module boundaries: `models` validates shape, `config` loads settings, and `cli` executes strict lifecycle behavior.
- `ReplayPolicy(kind="fsq_command").alias` preserves primary authored FSQ command names such as `tapOn`, `inputText`, `assertWithAI`, and `waitMs`, while canonical capability names such as `tap_on`, `input_text`, `assert_with_ai`, and `wait_ms` are stored in executable invocations. `CapabilityDefinition` does not carry a duplicate `aliases` list for these primary replay command names.
- Android `uiTree` is a replay alias for the driver-owned, read-only canonical `ui_snapshot` observation capability. It may be exposed to dynamic agents through registry metadata and returns the current backend UI hierarchy. Dynamic recording skips observation capabilities even when their replay aliases remain valid for authored strict YAML cases. Automatic Android runner evidence uses the same normalized `ui_snapshot` artifact naming and content source.
- Capability definitions are deliberately serializable. They do not import or wrap OpenAI Agents SDK tool objects, driver instances, Python function callables, decorator marker objects, platform catalog helper objects, default screenshot-capture policy, or SDK schema strictness knobs. Runtime bindings live in `tools`, `core`, and `agent`; declaration marker metadata lives in `capabilities`. Active SDK capability tools use strict JSON schema by default.
- Default automatic evidence capture is a core runner policy derived from the resolved capability plus `ExecutableStep.kind`, not from capability metadata flags or executor kind. Any retained `EvidencePolicy` fields such as `capture_before`, `capture_after`, `capture_on_failure`, or `artifact_kinds` are legacy compatibility fields only and must not create a second default capture path.
- Android driver parameter models forbid unexpected fields and provide canonical `model_dump(mode="json", exclude_none=True)` output. Runtime-only step metadata such as evidence policy, timeout fields, source references, retry policy, replay-source metadata, and step identifiers stays on `ExecutableStep` rather than inside driver parameter models.
- Web driver parameter models forbid unexpected fields and provide canonical `model_dump(mode="json", exclude_none=True)` output. Runtime-only step metadata such as evidence policy, timeout fields, source references, retry policy, replay-source metadata, and step identifiers stays on `ExecutableStep` rather than inside Web driver parameter models.
- Windows driver parameter models forbid unexpected fields and provide canonical `model_dump(mode="json", exclude_none=True)` output. Runtime-only step metadata such as evidence policy, timeout fields, source references, retry policy, replay-source metadata, redaction state, and step identifiers stays on `ExecutableStep` rather than inside Windows driver parameter models.
- Windows mouse parameter models enforce endpoint and coordinate invariants at validation time.
- macOS driver parameter models forbid unexpected fields and provide canonical `model_dump(mode="json", exclude_none=True)` output. Runtime-only step metadata such as evidence policy, timeout fields, source references, retry policy, replay-source metadata, redaction state, and step identifiers stays on `ExecutableStep` rather than inside macOS driver parameter models.
- Runtime-secret text references are represented by text-entry parameter fields, not by a separate pre-resolution `RuntimeSecretRef` object. Omitted `textType` means literal text for YAML compatibility; `textType="runtimeSecret"` means `text` is an environment variable name resolved by `core` before driver invocation.
- `WaitMsParams` belongs to the inherited `wait_ms` CommonTool capability and its strict replay alias `waitMs`. It lets recorded strict cases replay pure waits without routing through Android gesture or driver APIs.
- Web browser lifecycle is represented by explicit no-field parameter models `WebStartBrowserParams` and `WebCloseBrowserParams`; `navigate_to` is navigation on an already-started browser/page, not an implicit startup contract.
- Web `page_snapshot` is a driver-owned, read-only explicit observation capability with canonical alias `pageSnapshot`. It returns a Web page snapshot, remains valid for authored strict YAML cases, is skipped by dynamic recording, and must not reuse Android-oriented `ui_tree` or `uiTree` naming. Automatic Web runner evidence uses normalized `ui_snapshot` artifact naming even when the underlying content comes from page snapshot data.
- Windows `ui_snapshot` is a driver-owned, read-only observation capability with canonical alias `uiSnapshot`. It returns a bounded pywinauto control-tree snapshot, remains valid for authored strict YAML cases, is skipped by dynamic recording, and also satisfies the normalized automatic `ui_snapshot` evidence contract.
- macOS `ui_snapshot` is a driver-owned, read-only observation capability with canonical alias `uiSnapshot`. It returns a bounded Appium Mac2 page-source/control-tree snapshot, remains valid for authored strict YAML cases, is skipped by dynamic recording, and also satisfies the normalized automatic `ui_snapshot` evidence contract.
- macOS `assert_elements_order` is a deterministic assertion contract. It compares resolved element center positions on the requested axis, returns assertion-oriented structured order metadata, and is distinct from AI visual assertions or raw `ui_snapshot` narration.
- Pydantic is used at boundaries where external inputs, config files, agent output, and tool output enter the system.
- The agent final output contract is model-owned. The runtime always uses the current `AgentFinalOutput` schema through OpenAI Agents SDK structured output. The schema version is emitted in the final output for traceability, but schema selection is not a user-facing configuration.
- Task verification data is split from execution planning data. `key_actions` preserves caller-supplied or internally generated execution guidance, while `verification_goal` records the single final outcome the evidence-based verifier must check. Dynamic CLI inputs do not use typed assertion/operation verifier contracts or configurable verification modes.
- Task planning references are distinct from task descriptions and verification requirements. `planning_reference_kind` and `planning_reference_text` are optional compatibility fields for pre-plan input selection; first-party dynamic CLI tasks should populate them so pre-planning does not infer execution flow from final verification text. Raw case planning references preserve authored file content as text and must not imply parsed strict execution.
- Goal/reference tasks may start with no `key_actions` and no final verification goal. The agent orchestrator must run pre-plan before external UI actions, then copy the returned `GoalPrePlan.key_actions` into `Task.key_actions` and the returned `GoalPrePlan.verification_goal` into `Task.verification_goal`. Generated key actions are execution planning data only and must not become additional final-verifier requirements.
- Dynamic final verification is goal-only. The verifier checks one `verification_goal` string and returns success only when execution evidence supports that goal, failed when evidence proves the goal unmet, and inconclusive when evidence is insufficient or ambiguous.
- Agent output schema evolution follows one of two policies: compatible evolution may only add fields without removing or changing existing field meaning; breaking evolution replaces the current schema and does not preserve a runtime switch for old formats.
- Tool-call reporting uses `ToolCallRecord` for real AgentTool, CommonTool, and PlatformTool invocations. Runtime/provenance records such as progress events, pre-plan reconstruction, SDK runner summaries, and provider session setup are not represented as real tool calls.
- Result models store evidence paths rather than binary evidence to keep logs and reports lightweight.
- Live run events are serializable and intentionally store user-visible summaries rather than hidden model chain-of-thought. Tool inputs and outputs may be redacted or preview-truncated by emitters before display or persistence.
- Context and AgentTool output settings are internal runtime defaults: recent small or moderate helper outputs remain inline for fewer extra tool turns, while older or very large outputs are written to artifacts and represented by bounded previews.
- Runtime prompt text is template-owned through `OpenAIAgentPromptConfig`. The agent runtime assembles prompt models for project knowledge, successfully loaded skills, task input, and variables, then renders Jinja template files. Static behavioral text, headings, loops, and formatting should live in template files instead of hidden code paths or ad hoc string concatenation. The `AgentFinalOutput` schema is supplied to the SDK through `output_type`, not duplicated as prompt text. There is no custom-instruction prompt channel; project-specific guidance belongs in `knowledge/project.md`, and reusable execution guidance belongs in configured skills.
- Runtime secrets are model-owned as an allowlist of environment variable names. This keeps credential values out of cases and config YAML while allowing text-entry parameter models to reference approved names through `textType="runtimeSecret"`. Secret values are resolved only in memory by execution core and must be redacted from user-visible events, artifact output, model-facing previews, strict evidence, recording manifests, and final reports.
- AgentTool request/result models are serializable and SDK-neutral. The tools module adapts AgentTools to OpenAI Agents SDK `FunctionTool` objects, but shared models must not import SDK types.
- AI assertion request/result models are serializable execution evidence. They describe explicit authored platform assertions and provider-backed verdicts; they do not represent locator fallback, testcase mutation, recovery, or hidden model reasoning.
- Harness and driver selection is model-owned through `HarnessSettings` and platform-specific nested settings for Android, Web, Windows, and macOS. Runner-owned post-action delay defaults are model-owned through `ExecutionSettings.post_action_delay_seconds` because they apply to both dynamic and strict `StepRunner` execution, not provider behavior, dynamic recording logic, or FSQ command semantics. Concrete platform behavior is implemented by the entry/runtime layer and the `core` harness/driver modules so configuration parsing does not own execution logic.
- fsq-agent does not expose MCP as a runtime capability path. Screenshots, UI trees, page sources, and other platform observations are represented by platform evidence artifacts or AgentTool artifact references rather than by MCP tool output.
- Page knowledge is represented as a compact graph-like Markdown/JSON format owned by shared models so external generators can produce compatible files. `index.md` is a concise JSON index for page lookup; each `pages/*.md` file contains one JSON page node. Page identifiers are semantic descriptions without locators. Element locators are explicitly reference locators, not authoritative runtime truth.
- Internal dynamic goal planning is represented separately from execution results. It produces ordered key actions from a goal/reference task and loaded page knowledge, but it does not execute UI actions or verify runtime state.
- OpenAI Agents SDK runtime objects are not stored directly in shared models. Models hold serializable configuration, AgentTool definitions, CommonTool/PlatformTool capability metadata, AI assertion request/results, and platform function schemas that `agent`, `tools`, `core`, and `providers` adapt into runtime objects. `OpenAIAgentsSettings.provider` stores the effective serialized switch for choosing GitHub Copilot or Azure OpenAI provider construction at runtime after `config` applies `FSQ_LLM_PROVIDER` or YAML compatibility input. Provider endpoint/key/model values that are local to a user or deployment are resolved by `config` from fixed environment variable names instead of stored as YAML fields.
- Skills are descriptive instruction bundles stored under the configured agent knowledge root. `agent_context.knowledge.skills.dir` locates the skill files relative to `agent_context.knowledge.root_dir` by default, and `agent_context.knowledge.skills.items` lists the configured bundles. Skills do not grant CLI or shell execution. Model-facing skill context should contain only successfully loaded skill instructions and not loader warnings.
- Agent context configuration groups related context resources under `agent_context.knowledge`: the normal private knowledge root, the skills subdirectory and items under that root, and optional pre-plan page knowledge under the same root. Top-level `skills`, top-level `knowledge_dir`, and top-level `pre_plan` are not part of the public YAML surface.
- Parsed FSQ `.codex.yaml` models are used for strict-core execution, including lifecycle hook metadata. The public CLI's default LLM `--case-yaml` and `--case-dir` paths read YAML files as raw text and build goal/reference `Task` values without parsed FSQ models or hook execution.
- Platform action parameter schemas come from decorated capability metadata and shared parameter models registered in the capability registry. There is no MCP schema validation fallback or MCP-derived tool schema source.
