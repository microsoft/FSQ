# Module: cli

## Purpose

Provide the public command line surface for fsq-agent: initialize/check workspace and runtime readiness, bootstrap lightweight platform-selected capability registries, run either dynamic LLM goal/reference execution or strict-core YAML execution for the active platform with optional case lifecycle hook orchestration and explicit provider-backed `assertWithAI`, optionally record dynamic LLM runs into strict-replay FSQ YAML artifacts from capability replay metadata, print stored reports from prior runs, start the local browser playground, and start the platform-selectable local Control Plane.

## Dependencies

- `models`: Uses `Task`, `TaskResult`, FSQ case and lifecycle hook models, capability registry snapshots, replay policy metadata, strict replay refs, wait parameter models, report artifacts, and shared exceptions.
- `config`: Owns Project/Workspace initialization and layout validation, loads settings, and validates provider-only, LLM runtime, or strict-core readiness.
- `providers`: Builds shared provider sessions and AI assertion evaluators for dynamic runs and strict runs that contain explicit `assertWithAI` steps.
- `core`: Composes capability registry bootstrap, deterministic `ExecutableStep` execution through `StepRunner`/`StepSequenceRunner`, runner events, and evidence manifest writing at the entry boundary.
- `fsq`: Owns the exact `.fsq.yaml` suffix contract, loads FSQ cases, and converts parsed cases into canonical strict-core executable steps using a registry snapshot.
- `agent`: Runs dynamic LLM goal/reference task workflows and persists recordable safe event metadata.
- `playground`: Starts the local browser playground server from loaded settings and CLI host/port/browser options.
- `control_plane`: Starts the local Control Plane server from the current-directory workspace and CLI host/port/browser options. Control Plane owns dynamic platform selection and does not receive one CLI-selected platform.
- `report`: Generates strict-core reports and resolves stored LLM or strict-core reports by run id.
- `tools`: Provides dynamic-only AgentTool hosts for default LLM execution.

The CLI module composes strict registry bootstrap from public `core` platform tool APIs and dynamic execution from `agent`/`tools` APIs. It must not import `capabilities` or decorator internals directly; declaration discovery happens inside the owning capability host modules.

## Public Interface

Current `__init__.py` exports via `__all__`:

- `main`: CLI entry point for package scripts.

Current commands:

- `fsq init --platform PLATFORM [--platform PLATFORM ...]`: Initialize the current empty or valid FSQ project without reading platform presets or checking runtime readiness. The required repeatable option accepts Android, Web, Windows, and macOS; values are deduplicated and persisted in fixed order. Init creates or updates Git-shared `fsq.yaml`, local `.fsq-workspace/workspace.yaml` and `envs.yaml`, common cache/temp directories, and platform-separated `fsq-cases/<platform>` and effective Run directories. It is idempotent, may add platforms, never overwrites user content, and does not configure Providers, Devices, Drivers, or authentication.
- `fsq-agent run --platform android|web|windows|macos --goal TEXT --tracing/--no-tracing --stream/--no-stream --stream-format concise|jsonl --record --record-on-failure`: Run one dynamic LLM task from a natural-language goal using the committed config preset for the selected platform and the current directory `.fsq-agent-workspace`. This is the default run mode. CLI task construction must set the explicit planning reference kind to `goal` and the planning reference text to the normalized goal. Internal pre-planning produces both ordered execution key actions and the final `verification_goal` before external UI actions begin. Streaming is enabled by default for LLM run events, and `concise` is the default human-readable stream format. `--tracing` and `--no-tracing` optionally override `openai_agents.tracing_enabled` for this run after settings load and before runtime validation; when omitted, config/default tracing applies. SDK trace export still requires `OPENAI_API_KEY`, and the runtime disables SDK tracing for the run when that variable is absent or blank. `--record` optionally records a strict-replay `.fsq.yaml` artifact under the completed run directory when the final status is `success`; `--record-on-failure` permits draft recording for `failed` or `inconclusive` runs and is valid only with `--record`.
- `fsq-agent run --platform android|web|windows|macos --case-yaml PATH --tracing/--no-tracing --stream/--no-stream --stream-format concise|jsonl --record --record-on-failure`: In default mode, read the file as complete UTF-8 text and run one dynamic LLM task using that raw content as reference material. CLI task construction must set the explicit planning reference kind to `raw_case` and the planning reference text to a stable envelope containing the source path and complete raw file content. The CLI must not parse the input YAML for execution, normalize FSQ commands, extract key actions, derive local operation/assertion verifier requirements, or create a file-name-only final verification goal for this path. Pre-plan owns deriving ordered key actions and one `verification_goal` from the raw case reference before UI execution. If `--record` is enabled, post-run recording may parse only the persisted event log and write a generated `recorded.fsq.yaml` under the run directory.
- `fsq-agent run --platform android|web|windows|macos --case-dir PATH --tracing/--no-tracing --stream/--no-stream --stream-format concise|jsonl --record --record-on-failure`: In default mode, discover `*.fsq.yaml` files recursively, sort them, read each file as complete UTF-8 text, and run one dynamic LLM task per file serially. Each task must receive its own `raw_case` planning reference containing that file's source path and complete raw content; pre-plan derives per-case key actions and `verification_goal`. Execution continues after failed cases and prints a final operational summary. When recording is enabled, each case run independently attempts to write `recorded.fsq.yaml` and `recording.json`; recording failures do not stop later dynamic cases.
- `fsq-agent run --platform android|web|windows|macos --strict --case-yaml PATH --tracing/--no-tracing`: Run one `.fsq.yaml` FSQ case through the strict-core path. CLI loads settings from the committed config preset for the selected platform and the current directory `.fsq-agent-workspace`, constructs the selected platform harness, driver, and platform tool provider without connecting to a real Android device or launching a Playwright browser, builds and validates a `CapabilityRegistry` containing inherited CommonTools plus active PlatformTools, loads the case and optional lifecycle hooks through `fsq`, reads optional config lifecycle hooks from `settings.case_lifecycle`, validates configured runtime-secret text references by environment variable name without resolving values into persisted data, and executes strict lifecycle phases in order: config `onCaseStart` hooks, case `onCaseStart` hooks, main case commands when before hooks pass, case `onCaseComplete` hooks, and config `onCaseComplete` hooks. Hook `runCase` entries run another `.fsq.yaml` case with the same active registry and harness binding, using the same relative path policy as strict case inputs; hook `runShell` entries execute the operator-authored local shell command string without platform-specific command validation. Within a combined hook entry, `runCase` and `runShell` execute in the YAML order configured by the operator. Case and config `onCaseComplete` hooks run after before-hook failure or main command failure according to the lifecycle order. Any config hook, case hook, main command, complete hook, or shell hook failure makes the overall strict case fail; config before-hook failure skips case before hooks and the main command body, while case before-hook failure skips the main command body. Canonical FSQ command steps execute through `StepSequenceRunner` with `StepRunner`, the configured active harness/platform provider, the runtime secret store, and `settings.execution.post_action_delay_seconds`. Post-action delay is applied by `StepRunner` after invoke and before finalize evidence; it must not inject `waitMs` commands or synthetic evidence steps. Step-kind evidence policy is also applied by `StepRunner`, so strict replay receives the same automatic `screenshot` plus normalized `ui_snapshot` artifacts as dynamic execution without CLI-specific policy mapping. The run writes `evidence-manifest.json`, generates `core-report.md/json`, and prints generated paths. Locator fallback, action repair, recovery, testcase mutation, and strict-mode recording are not allowed. If the parsed case or any config/case hook case contains an explicitly authored `assertWithAI`/`assert_with_ai` capability, CLI applies any tracing override before provider validation, validates provider readiness, builds a provider-backed AI assertion evaluator through `providers`, and injects it into the active harness/backend support before execution.
- `fsq-agent run --platform android|web|windows|macos --strict --case-dir PATH --tracing/--no-tracing`: Run discovered `*.fsq.yaml` files serially through the same deterministic strict-core lifecycle path. Config-level hooks apply to every top-level case. Hook `runCase` targets from config-level or case-level hooks are dependencies of the owning top-level case and are not added as separate top-level directory results. Execution continues after failed top-level cases, writes a directory-run summary, and exits nonzero when any top-level case fails because of config hooks, case hooks, main commands, or nested hook dependencies.
- `fsq-agent report --platform android|web|windows|macos --run-id ID --format markdown|json`: Print a stored report from the selected platform preset's configured runs directory under the current directory `.fsq-agent-workspace`. The command resolves either `report.md/json` for LLM runs or `core-report.md/json` for strict-core runs and fails when no matching report exists or when the run id is ambiguous.
- `fsq-agent playground --platform android|web|windows|macos --host HOST --port PORT --open-browser/--no-open-browser`: Load the same platform-preset runtime settings used by other CLI commands and start the local single-user playground HTTP server using the current directory `.fsq-agent-workspace`. The command blocks until the server exits, serves the Vite-generated static browser UI included in the Python package, optionally opens the browser, and delegates runtime behavior to the `playground` module. Startup failures from configuration, missing generated frontend assets, or server binding errors must render concise CLI errors and exit nonzero. A missing frontend build identifies `npm ci` and `npm run build` as the required source-checkout preparation; installed wheels require no Node.js runtime.
- `fsq-agent control-plane --host HOST --port PORT --open-browser/--no-open-browser`: Start the local single-user Control Plane using the current directory `.fsq-agent-workspace` without selecting one platform at CLI startup. The command passes workspace and server options to `control_plane`, blocks until exit, serves the Vite-generated Control Plane entry, and optionally opens the browser. The Control Plane module loads committed platform presets for browser-selected requests and runs. CLI does not own Control Plane discovery, readiness, API routing, execution, or state. The default host is `127.0.0.1` and the default port is `8879`; the command has no `--platform`, `--config`, or `--workspace` option. Missing generated assets or server binding failures render concise errors and exit nonzero; installed wheels require no Node.js runtime.

Public CLI commands do not expose `--config` or `--workspace`; Init does not expose `--provider`, force, repair, migration, template, or custom-path options. Invocations using those options fail before filesystem or Provider side effects.

Init supports global `--output human|json|jsonl`. JSON and JSONL each emit exactly one terminal `fsq.machine/v1` Result or Error with operation `workspace.init`; machine stdout contains protocol records only. Stable successful statuses are `initialized`, `already_initialized`, and `platforms_added`. Human output reports Project ID, requested/added platforms, created paths, and recommends adding `.fsq-workspace/` to `.gitignore` without modifying that file. Workspace usage errors exit 2, configuration/layout errors exit 3, internal or rollback failures exit 5, and interruption exits 130.

`--goal`, `--case-yaml`, and `--case-dir` are mutually exclusive. `--strict --goal` is invalid because strict-core execution requires authored YAML steps. `--strict --record` and `--strict --record-on-failure` are invalid because recording is a dynamic-run post-processing workflow. `--record-on-failure` without `--record` is invalid. Every case-oriented `--case-yaml` input requires the exact lowercase `.fsq.yaml` suffix. Relative case paths resolve against `cases.dir` first, then the current working directory. Strict hook `runCase` paths from either config-level or case-level hooks use the same suffix and relative path policy.

Dynamic recording writes the following run-local files when attempted:

```text
<runs_dir>/<run-id>/
    recorded.fsq.yaml
    recording.json
```

`recorded.fsq.yaml` contains two YAML documents: generated FSQ metadata followed by recorded commands. Generated metadata must include `tags` identifying the case as recorded and `properties.recording` with source run id, source task id, source status, `draft`, required runtime secret names, and warnings. `recording.json` contains recording status, command count, recorded case path when present, required runtime secret names, warnings, skipped tool calls, validation status, and errors when recording fails. Neither file may contain secret values.

The recording helper reconstructs logical replay entries from the dynamic run's `events.jsonl` by consuming structured capability metadata emitted by `StepRunner` for CommonTool and PlatformTool calls. A completed event with `replay.kind == "fsq_command"` and `step_kind != "observation"` appends `{replay.alias: safe_replay_params}` to generated strict YAML when the status indicates success and params validate, falling back to the started event's JSON arguments only when no safe replay params are present. Observation PlatformTools such as `uiTree`, `uiSnapshot`, and `takeScreenshot` are diagnostic/current-state observations and are skipped by dynamic recording even when they remain valid authored strict YAML commands. Capabilities with no `fsq_command` replay policy are diagnostics and are not replayed. The recorder requires replay metadata rather than using `fsq_action_name` or tool names as replayability fallback. AgentTool events are dynamic-only diagnostics and are ignored by recording. Runtime-secret text inputs record as text-entry commands with `text: ENV_NAME` and `textType: runtimeSecret`; `get_runtime_secret` is not a replay dependency. `wait_ms` records as `waitMs` through its replay alias. The recorder must not decide replay behavior by checking tool names, `fsq_action_name`, or schema strictness metadata.

Strict runtime-secret text validation is an entry/core responsibility. Before passing steps to `StepSequenceRunner`, CLI may preflight every referenced runtime secret name against `runtime_secrets.allowed_env_names` and verify the corresponding environment value exists, but secret values are resolved only in memory by the shared runtime secret resolver before driver invocation and must be redacted from persisted events, manifests, reports, and logging.

Internal deterministic-core composition helper:

```python
bundle = run_fsq_core_case(
    case_path=Path("case.fsq.yaml"),
    registry=registry,
    harness=harness,
    output_dir=Path("runs/run-1"),
    run_id="run-1",
    post_action_delay_seconds=settings.execution.post_action_delay_seconds,
)
```

This helper is not a public CLI command. It exists to give `run --strict` and tests a single entry-layer path for running one FSQ case through the deterministic core. It should receive or build a lightweight active-platform capability registry, load the FSQ case, convert commands to canonical `ExecutableStep` records with a registry snapshot, resolve strict replay refs in memory, run them through `StepSequenceRunner` and `StepRunner` with caller-supplied harness/backend bindings and post-action delay settings, rely on `StepRunner` for centralized driver step-kind evidence and delay policy, write `evidence-manifest.json`, and return an `EvidenceBundle` whose `manifest_path` points to the written manifest.

Lifecycle orchestration wraps this deterministic command execution path for strict public runs. The lifecycle layer loads config-level and case-level hook metadata, resolves hook `runCase` paths, detects recursive hook chains across both hook origins, executes `runShell` commands, annotates hook case steps with lifecycle phase and hook origin metadata, and then delegates canonical FSQ command steps to the same `StepRunner`/`StepSequenceRunner` path. On Windows, `runShell` executes through non-interactive Windows PowerShell; on other platforms it uses the local system shell. The deterministic command execution helper must not parse or execute lifecycle hooks by itself.

The helper must not construct real platform drivers, choose backend settings, or add retry/report policy.

Internal strict deterministic-core entry:

```python
artifact = run_strict_fsq_core_case(
    case_path=Path("case.fsq.yaml"),
    registry=registry,
    harness=harness,
    output_dir=Path("runs/run-1"),
    run_id="run-1",
    post_action_delay_seconds=settings.execution.post_action_delay_seconds,
)
```

This strict entry executes the case lifecycle exactly as authored with the supplied registry and harness/backend bindings, writes `evidence-manifest.json`, generates `core-report.md` and `core-report.json`, and returns the generated Markdown `ReportArtifact`. It must not enable locator fallback, AI recovery, testcase mutation, platform-driver construction, OpenAI provider validation, or AI assertion evaluator construction. If AI assertion is needed, the caller must provide a harness/backend binding that already has an evaluator injected. Strict results remain auditable because recovery execution is not part of this entry.

Strict replay post-action stabilization is owned by `StepRunner` and configured through `execution.post_action_delay_seconds` plus capability metadata overrides. CLI strict execution passes those settings into the deterministic-core helper, which passes them to `StepRunner`. The delay is execution timing only; it should not modify parsed FSQ commands, add `waitMs` records to reports, or create synthetic evidence steps. Strict replay evidence capture is also owned by `StepRunner`: `action` steps capture before and after, `assertion` steps capture before only, `setup` steps capture after only, and `teardown` steps capture before only, always writing `screenshot` plus normalized `ui_snapshot` artifacts. CommonTool actions such as `wait_ms` receive automatic evidence capture; observation/diagnostic steps do not.

## Platform CLI Blocks

Shared CLI rules:

- `run`, `init`, and playground startup use `settings.harness.platform` to select readiness validation, registry bootstrap, strict harness/platform tool construction, and platform-specific error messages.
- Public CLI commands use current-directory `fsq.yaml` and do not expose project-root selection. Provider configuration and interactive authentication belong to Control Plane Config.
- Strict replay parses cases and hook case dependencies against the active platform registry snapshot containing inherited CommonTools plus active PlatformTools.
- Dynamic recording remains capability metadata-driven and must not infer platform semantics or replayability from command names, `fsq_action_name`, or schema strictness metadata.
- Dynamic recording records by-design observation skips such as Android `uiTree`/`ui_snapshot`, Web/desktop `uiSnapshot`/`ui_snapshot`, and screenshot/snapshot tools in `recording.json` audit data without adding generated case warnings.

Android CLI behavior:

- Android strict runs require Android app id from environment or case metadata according to strict validation rules.
- Android strict runs build the active harness through `HarnessFactory`; `DriverFactory` selects the configured Android backend driver, and strict execution captures automatic `screenshot`/`ui_snapshot` evidence through the centralized driver step-kind policy. Explicit `uiTree` commands remain valid authored observations.

Web CLI behavior:

- Web strict runs do not require Android app id or serial.
- Web strict runs build the active harness through `HarnessFactory` and the config-selected Web backend driver without launching a browser. Authored `startBrowser` starts or reuses the browser/page; authored `closeBrowser` closes it. CLI must not inject either command, and `navigateTo` must not be treated as startup.
- Web strict runs capture automatic `screenshot`/`ui_snapshot` evidence through the centralized driver step-kind policy when the active Web driver has a started page. Explicit `uiSnapshot` commands remain valid authored observations.
- Web strict navigation must use fully qualified URLs or the configured Web base URL policy.

Windows CLI behavior:

- Windows strict runs do not require Android app id or serial, Web browser executable settings, or macOS Appium settings.
- Windows strict runs validate `harness.windows.backend == "pywinauto"` plus environment-backed Windows app path and pywinauto adapter settings before external UI actions begin.
- Windows strict runs build the active harness through `HarnessFactory` and the config-selected Windows backend driver without launching the app during registry bootstrap or YAML parsing. Authored `launchApp` starts the configured application and authored `killApp` terminates it; CLI must not inject either command.
- Windows strict runs pass normalized settings values for app path, pywinauto backend kind, optional window title regex, and configured launch arguments into the config-selected Windows backend driver.
- Windows strict runs capture automatic `screenshot`/`ui_snapshot` evidence through the centralized driver step-kind policy and use `uiSnapshot` for explicit observation commands.

macOS CLI behavior:

- macOS strict runs do not require Android app id or serial and do not require Web browser executable settings.
- macOS strict runs validate `harness.macos.backend == "appium_mac2"` plus environment-backed Appium server and target app settings before external UI actions begin.
- macOS strict runs build the active harness through `HarnessFactory` and the config-selected macOS backend driver without connecting to Appium or launching the app during registry bootstrap or YAML parsing. Authored `launchApp` creates or reuses the Mac2 session and target app, and authored `killApp` terminates or closes it according to the driver contract. CLI must not inject either command.
- macOS strict runs capture automatic `screenshot`/`ui_snapshot` evidence through the centralized driver step-kind policy and use `uiSnapshot` for explicit observation commands.
- macOS strict runs support deterministic desktop assertions including `assertVisible` and `assertElementsOrder`; wrong element order is an assertion failure, while missing required elements are target-resolution failures.

Internal dynamic recording helper:

```python
recording = record_dynamic_run_as_strict_case(
    run_dir=Path("runs/run-1"),
    task=task,
    result=result,
    settings=settings,
    allow_failure=False,
)
```

This helper is not a public CLI command. It reads a completed dynamic run directory, writes `recorded.fsq.yaml` and `recording.json` when eligible and replayable, validates generated YAML through `fsq`, and returns an internal recording summary used for CLI output and directory-run summaries. It must not call provider APIs, execute platform actions, mutate source case files, or reveal secret values.

## Internal Structure

- `__init__.py`: Public exports only.
- `__main__.py`: Package entry point for `python -m fsq_agent.cli` and VS Code launch configurations.
- `_main.py`: Click command group and command handlers.
- `_task_loader.py`: Raw goal-source loading for LLM runs and path discovery/resolution for both run modes.
- `_capability_bootstrap.py`: Internal CLI wrapper around the package-private capability bootstrap helper used to construct lightweight platform capability definitions, build the capability registry, and identify provider-required capabilities and executable steps from registry metadata for dynamic and strict entry paths.
- `_core_execution.py`: Internal composition helper for deterministic FSQ case execution through `core` with a caller-supplied registry and harness/backend binding.
- `_case_lifecycle.py`: Internal strict lifecycle orchestration for config-level and case-level `onCaseStart`/`onCaseComplete`, hook `runCase` path resolution, recursion detection, shell hook execution, hook phase/origin metadata annotation, and aggregation of lifecycle status before report generation.
- Package-private `fsq_agent._strict_case_recording`: Shared post-run recorder used by CLI, Playground, and Control Plane to convert dynamic run capability events into run-local `recorded.fsq.yaml` and `recording.json` artifacts.
- `_strict_replay.py`: Internal strict-entry helper that preflights runtime-secret text references by environment variable name when needed before deterministic core execution. Final secret value resolution is owned by the shared execution resolver before driver invocation.
- `_formatting.py`: Logging-backed CLI rendering helpers for task results, concise phase-tagged live events, strict run summaries, and report paths. Concise live-event rendering is a human display concern only; it must not mutate `RunEvent` values or persisted run artifacts.
- `_logging.py`: CLI logging configuration.
- `playground` command handler in `_main.py`: Thin adapter that loads settings, maps host/port/browser flags into `PlaygroundServerOptions`, and calls `run_playground` without reimplementing playground routing or execution.
- `control-plane` command handler in `_main.py`: Thin adapter that validates the current-directory FSQ project, maps host/port/browser flags into `ControlPlaneServerOptions`, and calls `run_control_plane` without loading a fixed platform or reimplementing Control Plane behavior.
- `SPEC.md`: Module design.

## Python Architecture

- Architecture level: 3 Layered Application.
- Public API: `main` exported from `__init__.py`.
- Internal modules: all CLI `_*.py` files are private command/helper implementation modules. Shared dynamic-run recording composition lives in package-private `fsq_agent._strict_case_recording` and is consumed only by CLI, Playground, and Control Plane entry layers.
- Domain boundaries: CLI owns argument validation, settings loading, current-directory workspace selection for public commands, entry-mode orchestration, registry bootstrap, strict config/case lifecycle hook orchestration, strict replay secret resolution, dynamic recording, output rendering, and exit behavior. It does not own Provider configuration/authentication, capability implementation, StepRunner internals, FSQ parsing rules, provider runtime behavior, config parsing, or report rendering.
- Boundary models: tasks, results, settings, registry snapshots, executable steps, FSQ lifecycle hooks, replay refs, evidence bundles, and report artifacts come from `models`.
- Dependency direction: CLI may depend on entry/runtime modules (`config`, `providers`, `core`, `fsq`, `agent`, `playground`, `control_plane`, `report`, `tools`) but those modules must not import CLI. CLI imports only the public Control Plane API; Control Plane must not import CLI or Playground.
- Rationale: CLI coordinates multiple workflows and side-effect boundaries, so Level 3 is appropriate without adding repository or service-layer ceremony beyond focused helpers.

## Error Handling

CLI commands catch `FsqAgentError` subclasses from `models`, render concise user-facing messages, and exit nonzero. Unexpected exceptions are logged with trace details and summarized in the console.

Input validation failures, including unsupported `setup` commands, unsupported public `--workspace` options, unsupported Control Plane `--platform` options, invalid Control Plane ports, missing input source, multiple input sources, `--strict --goal`, invalid record flag combinations, unreadable dynamic case files, invalid strict YAML, malformed config-level or case-level lifecycle hook metadata, empty case directories, missing hook `runCase` files, recursive hook chains, missing strict Android app id from `FSQ_ANDROID_APP_ID` or FSQ case metadata when the active platform is Android, invalid Web navigation/base URL policy when the active platform is Web, invalid or missing Windows app path/pywinauto adapter settings when the active platform is Windows, invalid or missing macOS Appium/target settings when the active platform is macOS, missing strict replay secret allowlist/presence, missing provider readiness for authored strict `assertWithAI`, unresolved reports, or missing generated Playground/Control Plane frontend assets must fail before external UI actions begin for the affected command, case, hook, or server. Dynamic `--case-yaml` input must not fail merely because the file is invalid YAML, because that path does not parse YAML.

Control Plane startup converts shared `FsqAgentError` failures and OS-level startup failures into concise CLI errors and nonzero exits. CLI must not expose secret values, hidden reasoning, or raw server internals.

Strict lifecycle failures during execution, including failed start hooks, failed hook cases, nonzero shell hook exit codes, shell launch failures, failed main commands, and failed complete hooks, must be reflected in the owning strict case result and report without enabling recovery. `onCaseComplete` hooks must still run after a start hook or main command failure when they are configured.

Init does not perform Provider, Device, Driver, network, or platform readiness checks. An unsupported `--provider` option fails argument parsing before side effects.

Recording failures happen after a dynamic run and must not change that dynamic run's status. The CLI should log and summarize recording errors, including no replayable commands, runtime-secret text references with missing names, unsupported replay commands, generated YAML validation failures, and existing `recorded.fsq.yaml` conflicts. Directory runs continue after per-case recording failures.

## Verification Scope

- Verification covers repeatable Init platform parsing, Human/JSON/JSONL rendering, stable exit mappings, rejected unsupported options, Provider/Device/Driver/network-free initialization, dynamic goal/raw-case task construction, strict-case execution entry behavior, strict lifecycle ordering/failure semantics, dynamic recording handoff, report lookup, live event formatting, and thin Playground/Control Plane server startup delegation.
- Boundary verification ensures public commands use the current directory workspace, unsupported commands/options fail before side effects, secret values are never rendered, and strict/dynamic execution delegate behavior to owning modules.

## Current Invariants

- CLI commands are thin adapters over module APIs, not a second orchestration layer.
- Capability decorators are not a CLI concern. CLI entry paths consume validated `CapabilityRegistry` instances, registry snapshots, harness/backend bindings, and normalized runner/event metadata.
- Strict provider requirement detection uses the shared package-private capability bootstrap contract: provider-required canonical capability names are derived by comparing the active platform registry with and without provider-backed capabilities, and case steps are checked through registry resolution. CLI must not hard-code provider gating by authored or canonical action name.
- The public command surface is limited to `init`, `run`, `report`, `playground`, and `control-plane`. Unsupported command names are not retained as compatibility aliases.
- Init is a thin adapter over Config-owned initialization. CLI does not parse or persist Workspace YAML itself, has no Provider setup or authentication workflow, and never writes Provider-related `.env` or `~/.fsq` files. Public commands do not accept `--workspace` or `--env-file`; they use current-directory `fsq.yaml` and do not search parents.
- `run` applies `--tracing` or `--no-tracing` as a one-run override after `load_settings` returns and before LLM or provider-backed AI assertion validation. Sensitive tracing is never enabled by CLI.
- Android app id and serial are local environment-backed settings resolved by `config` from `FSQ_ANDROID_APP_ID` and `FSQ_ANDROID_SERIAL`; CLI does not expose app id or serial flags.
- Streaming CLI output logs live `RunEvent` values from the agent. Concise format is the default human-readable stream and includes `HH:MM:SS LEVEL` log prefixes so operators can distinguish informational, warning, and error events. Human-readable event rendering must derive concise display phase labels from existing event type/title data, including pre-plan, startup, execution, verification, report, and run-level fallbacks; derive tool identity from existing event tool fields, matching started events, safe payload metadata, or safe output preview metadata; derive tool outcome from existing payload status, runner-result status, safe preview status, or event type; keep arguments compact, redacted, and faithful to the event value, including explicit `null` values; preserve meaningful safe reasoning-summary messages as concise model reason summaries; suppress generic reasoning-summary notices that contain no model-readable content; summarize structured SDK agent messages; and suppress verbose `tool_output_preview` JSON unless it is short and no better summary exists. When verbose output is suppressed, the concise log should point to existing result, artifact, report, or run-output hints when available and must not invent new artifacts or files.
- Concise log cleanup is a presentation-only behavior. It must not change dynamic execution flow, `RunEvent` model fields, persisted `events.jsonl`, reports, recording manifests, generated strict YAML, tool artifacts, or intermediate run outputs. JSONL format emits one raw serialized event per log message for CI and log processors; the CLI formatter bypasses prefixes and human-readable compaction for those raw JSONL records so the stream remains machine-readable.
- Normal `run` is always dynamic LLM goal/reference execution. `--goal` supplies the user goal text. `--case-yaml` and `--case-dir` supply raw file content as reference material and must not use `FsqCaseLoader` or `FsqTaskAdapter`.
- Dynamic task construction separates planning references from final verification. `--goal` tasks use `planning_reference_kind="goal"` with the normalized goal text. Raw case tasks use `planning_reference_kind="raw_case"` with source path plus complete raw file content. The CLI does not derive final verifier requirements itself; pre-plan must summarize one `verification_goal` before external UI actions.
- Dynamic run recording is post-run evidence transformation, not task execution. It reads persisted normalized capability events after `FsqAgent.run` returns and writes only under that run directory.
- Recorded cases reflect actual successfully completed non-observation capabilities with `ReplayPolicy(kind="fsq_command")`. Runtime-secret inputs are recorded as text-entry command parameters, not as dependency replay capabilities. The recorder must skip observation step kinds, and must not invent setup, teardown, Web `startBrowser`/`closeBrowser`, assertions, locator fallback, recovery actions, or source YAML mutations. Missing assertions, observations, or lifecycle actions produce warnings when relevant.
- Runtime secrets in recorded cases are represented by environment variable names on text-entry commands using `textType: runtimeSecret`. Missing `textType` remains literal for YAML compatibility. Secret values are resolved only in memory during execution and are never written to generated YAML, event previews, manifests, reports, recording manifests, or logs.
- `run --strict` is strict-core execution. It parses FSQ YAML including lifecycle hook metadata, uses config-owned active platform settings including optional `caseLifecycle` hooks, and does not construct or invoke LLM components for planning, recovery, locator fallback, action repair, or final verification. Strict runs resolve platform aliases through the active registry and build the active harness through `HarnessFactory`, with `CommonPlatformTools` inherited by every platform and the concrete backend driver selected by config. CLI owns lifecycle hook orchestration around canonical command execution: config `onCaseStart`, case `onCaseStart`, main commands when before hooks pass, case `onCaseComplete`, and config `onCaseComplete` after before hooks have been attempted. Strict lifecycle execution should emit concise INFO logs for phase start and per-step/per-hook action completion, including phase (`before case`, `main case`, or `after case`), action label, status, and failure message when present. No extra CLI flag is required for these strict progress logs; existing logging configuration controls whether INFO logs are displayed. The sole provider-backed exception is an explicitly authored `assertWithAI` step, for which CLI may build and inject an AI assertion evaluator into the active harness/backend support before execution.
- Directory execution is intentionally serial because UI automation cases share external device and application state. Each case still creates independent run state so SDK sessions, harness context, AgentTool state, and platform CommonTool state do not leak across cases.
- `report` is a lookup/print command only; report generation happens during execution. It resolves either LLM reports or strict-core reports without exposing separate report commands.
- `playground` is a local developer convenience entry point. CLI owns only argument parsing, settings loading, and server startup; the `playground` module owns HTTP routes, production serving of generated browser assets, session state, execution adapters, screenshot preview, replay video handling, and report lookup. Frontend dependency resolution and compilation belong to the repository root npm/Vite project.
- `control-plane` is a platform-selectable local product entry point. CLI owns only argument parsing, current-workspace selection, and server startup; the `control_plane` module owns platform preset loading per request/run, discovery, readiness, HTTP/SSE routes, task state, evidence projection, and generated Control Plane asset serving. The command intentionally has no `--platform` option.
- CLI logging never emits API key values; it may log the configured API key environment variable name and whether it is present.
