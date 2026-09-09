# Module: cli compatibility entry

## Purpose

Preserve existing `fsq_agent.cli:main` as a public compatibility export for the canonical `adapters.cli` command while installed `fsq` and `fsq-agent` scripts target `fsq_agent.adapters.cli:main` directly. Canonical CLI modules parse arguments, select presentation, invoke Application operations, render results/events, and map exit codes. Old `fsq_agent.cli._*` private module paths are unsupported and absent.

## Dependencies

- `application`: The boundary used for CLI business operations and their Request, Result, Event, and Error contracts. Application Case operations delegate complete run orchestration to Execution.
- `control_plane`: Public server startup boundary for `fsq ui`; business operations behind that server still use Application.
- `agent` and `adapters.coding_agent`: Narrow composition-only dependencies used by a private CLI helper to construct the SDK-neutral Agent collaborator injected into Application Case creation.
- `models`: Shared primitive/domain values only where required by Application contracts.

CLI command handlers must not directly compose `agent`, `execution`, `fsq`, `core`, `report`, concrete drivers, or provider implementations. A private CLI composition helper may construct `FsqAgent` with the public Coding Agent runtime factory solely to satisfy Application's injected `agent_factory` boundary. It contains no request validation, execution policy, persistence, Provider selection, or result interpretation. Lower modules must not import CLI.

## Public Interface

`__init__.py` exports the canonical `adapters.cli.main` object. The primary executable remains `fsq`. The public command tree is:

The sole standard installation command is `pip install fsq-agent`. The distribution installs both `fsq` and compatibility `fsq-agent` console scripts, each targeting the same canonical `fsq_agent.adapters.cli:main` object. Platform extras are not an installation surface.

```text
fsq
├── init
├── doctor
├── case
│   ├── create
│   ├── test
│   └── format PATH
├── ui
├── providers
│   ├── configure NAME
│   └── status
├── runs
│   ├── list
│   ├── show RUN_ID
│   └── logs RUN_ID
```

Core Case forms are:

```bash
fsq case create --platform web --goal "Verify product search"
fsq case test --platform web tests/search.fsq.yaml
fsq case test --platform web tests/search.fsq.yaml --suggest
```

Workspace initialization uses the current directory as the selected directory and derives its default workspace name from that directory's final path component:

```bash
fsq init --platform web --browser-channel chrome
fsq init --platform web --browser-channel msedge-canary --browser-executable-path /path/to/edge-canary
fsq init --platform android --app-id com.example.app
fsq init --platform windows --app-path /path/to/application
fsq init --platform macos --bundle-id com.example.app
```

`init` accepts the same complete platform target shapes as Control Plane: Android requires `--app-id`; Web requires `--browser-channel` while `--browser-executable-path` is optional; Windows requires `--app-path` and accepts `--window-title-re` and `--launch-args`; macOS requires at least one of `--bundle-id` and `--app-path`. Target options for another platform are rejected. When the Web path is omitted, Application discovers an executable compatible with the exact selected channel on the current host. One unambiguous compatible candidate is selected; no candidate or multiple distinct candidates fail before workspace mutation with safe guidance to install the channel or pass an explicit path. An explicit path is normalized and validated against the channel through the same shared operation.

`init` always checks selected-platform runtime readiness before workspace mutation. Readiness checks are read-only. A missing Python platform dependency reports that the installed `fsq-agent` distribution is incomplete and must be reinstalled or repaired; a missing host service, browser/application target, device prerequisite, or other system dependency reports a safe external provisioning action. `init` does not expose `--install-driver` or any runtime-install option and never invokes Python or system package managers, installs or modifies the application under test, creates emulators/VMs, connects to a device, starts Appium, or authenticates.

`--name` may override the derived name. An unregistered name uses Config's selected-directory rule: an empty current directory becomes the root, while a non-empty directory receives a new `<current-directory>/<name>` child. A registered name is resolved case-insensitively and always uses its stored root independently of the current directory; an unavailable registered Workspace is not recreated. `--env NAME=VALUE` may be repeated and supplies the complete private environment mapping. `--update-existing` permits replacement of a differing existing platform target/environment; without it, equal configuration is unchanged and differing configuration is a conflict. `--provider` is not part of workspace initialization; Provider configuration remains under `providers configure` or Control Plane Config. Public option spelling uses hyphens; underscore aliases are not accepted.

- `case create` accepts a natural-language Goal, performs AI-participating testing, and may produce a Run-local candidate `*.fsq.yaml` Case. Optional `--name` selects the stable Case name; otherwise Execution derives it from platform and normalized Goal. A validated successful recording is published as `<case-name>.fsq.yaml` without changing the Run-local candidate. Output includes publication outcome, stable name, published path, and safe warnings. Conflicting existing contents are preserved and the candidate remains available. Publication conflict exits `1` while machine results retain the actual execution status.
- `case test` executes an existing FSQ Case as authored. The source Case is immutable.
- `case test --suggest` executes the authored Case exactly once, then permits read-only AI analysis of the parsed Case and bounded persisted execution facts. It may return Run-local suggestions or a candidate Case while preserving the completed execution result and never overwriting the source Case or configured Case directory.
- When suggestion analysis produces an artifact, Human output displays each Run-local suggestion or candidate Case path and states that the source Case was not modified. JSON and JSONL terminal results expose the same paths through `suggestion_path` and `candidate_case_path`; absent artifacts remain `null`.
- `runs` is the exact-current-Workspace read-only history surface. It aggregates configured platforms by default, supports an optional configured platform, and never deletes, mutates, retries, replays, cancels, or follows a Run.
- `providers` exposes only user-level active-Provider configuration and readiness. Provider inventory is not a public CLI capability in the first release.
- `doctor` performs read-only Workspace diagnostics across every identifiable configured platform and reports fixed component checks plus readiness for `case test`, `case test --suggest`, and `case create`; `ui` starts the Control Plane adapter.

The CLI does not expose Environment inventory, Environment diagnostics, extension management, public capability/action discovery, Environment mutation, Run mutation, a daemon, async queues, sharding, or workers. Environment readiness remains an internal/public Python module capability consumed by Workspace initialization and `fsq doctor`. `environments`, `test`, `replay`, and `run` are not public top-level commands and are not retained as silent aliases.

## Workspace Rule

For Workspace-scoped commands except creation of an unregistered Workspace, the exact current directory is the CLI workspace root. User-level Provider commands and static `case format` do not require a Workspace. A valid CLI workspace is registered in the user workspace registry at that exact normalized root and contains at least one valid `.fsq/config/config.<platform>.yaml`; `.fsq-agent-workspace` markers are neither created nor accepted. Commands do not search parent directories, auto-initialize, or accept an alternate workspace flag. Commands requiring only workspace context validate the exact registered root; platform-specific commands additionally require the selected platform config. Failure uses Application error code `workspace.not_initialized` and tells a human to run `fsq init` from the intended selected directory or change to an initialized Workspace. Control Plane continues to list all registered workspaces and does not derive selection or execution paths from the CLI process startup directory.

`fsq init` is the only CLI command that may establish this precondition. It validates all input, resolves the complete target, and completes Driver readiness before workspace mutation; it then creates an unregistered Workspace from the current selected directory or initializes and updates exactly one platform at an existing registered root through the shared Application and Config operations. An empty selected directory is adopted as the new root; a non-empty selected directory is preserved and receives an absent `<current-directory>/<name>` child. Success is reported only after workspace files and registry truth are committed. It creates `.fsq/config/config.<platform>.yaml`, `.fsq/runs/<platform>/`, `cases/<platform>/`, `knowledge/<platform>/project.md`, and the user registry entry inside the selected final root. Repetition for an existing registered name is idempotent and uses its stored root independently of the current directory. A partially initialized, unavailable, mismatched, invalid, or Driver-unready Workspace fails with safe repair guidance rather than being treated as ready.

`providers configure` and `providers status` are user-level commands and are exempt from the Workspace precondition. They operate on the same active Provider under `~/.fsq` as Control Plane Config and never read or write a Workspace `.env` or platform configuration.

## Case Format Command

`fsq case format PATH [--check | --diff | --write] [--json]` accepts one explicit canonical or supported legacy Case filename. Relative paths resolve against the current directory, not a configured Case directory; directory recursion and batch migration are not implicit. Mode flags are mutually exclusive; no mode means `--check`. Platform is inferred from Case metadata. This command is exempt from the Workspace precondition and is always non-interactive.

- `--check` validates and compares original bytes with canonical output without writing.
- `--diff` performs the same checks and returns a unified formatting diff without writing.
- `--write` validates first, atomically writes canonical bytes when needed, and leaves identical files untouched. Invalid input is never rewritten.
- Exit `0` means canonical input or successful write; `1` means valid but noncanonical input in check/diff mode; `2` means invalid Case, unsupported platform, missing input, or usage error; `5` means file I/O, concurrent-edit conflict, or internal failure; `130` means interruption.
- `--json` selects the existing JSON terminal envelope with the structured Application result and overrides inherited presentation for this command. Diff text is a JSON string field, never mixed with raw stdout text. Existing global JSON/JSONL modes remain supported. Syntax and validation failures are machine-readable when JSON was requested. Result fields and safe diagnostic structure follow Application SPEC.
- Human output distinguishes invalid Case, valid but noncanonical Case, already canonical Case, and successful formatting. Validation is document-local and static, not proof of execution success.
- CLI invokes Application formatting without constructing a runtime, requiring Workspace configuration, authenticating, or inspecting referenced Case/script files.

Verification covers mode exclusivity, structured diagnostics and exit codes, no-Workspace invocation, no runtime side effects, invalid-file preservation, idempotent writes, generation/CLI byte equivalence, stable create names, and publication conflicts.

`docs/case-format.md` documents the canonical format and the Coding Agent check/fix/recheck interface, including machine fields, exit codes, diff behavior, comment normalization, and the distinction between static validity and successful execution. Agent write authorization and workflow instructions remain owned by repository workflow-control files, not project specifications.

## Provider Commands

The supported configuration forms are:

```bash
fsq providers configure github_copilot [--model MODEL]
fsq providers configure azure_openai [--base-url URL] [--model MODEL] [--api-key KEY]
fsq providers status
```

In Human interactive mode, GitHub Copilot configuration requests a device code, displays only its verification URL and user code, waits with the Provider-owned polling policy, discovers eligible models, and asks the user to select one when `--model` did not select an offered model. It atomically activates the completed authorization and selected model only after all steps succeed. Because the device flow requires an observable user action before a terminal result exists, it is rejected under `--non-interactive`, JSON, and JSONL in the first release with guidance to use Human interactive mode. An explicitly requested model must be present in the discovered eligible model set.

Azure OpenAI configuration requires base URL, model/deployment name, and API key. Human interactive mode securely prompts for omitted values and hides the API key. JSON, JSONL, and `--non-interactive` never prompt and require all three options. Configuration validates and atomically saves the complete candidate through the shared user Config API. No output or error includes the API key. A failed GitHub or Azure replacement leaves the previously active Provider and its credentials usable.

`providers status` loads the real active user Provider and model, checks local authentication and non-interactive readiness through Application, and never starts device login or sends model inference. It returns one result with `status` (`ready` or `unavailable`), `configured`, `provider`, `model`, `authenticated`, a safe `message`, and a safe repair `action` when needed. Human, JSON, and JSONL expose equivalent facts; JSONL emits one terminal record and no synthetic event. Ready exits `0`; a completed unconfigured, unauthenticated, or otherwise unavailable result exits `4`. Invalid user configuration is a configuration error, and unrecoverable orchestration failure is an internal error. Output never includes tokens, API keys, authorization data, raw backend responses, exception messages, or tracebacks.

## Global Machine Contract

- `--output human|json|jsonl` selects presentation and defaults to `human`.
- `--non-interactive` forbids prompts and implicit interactive authentication.
- Human output may be styled; JSON emits one complete operation envelope; JSONL emits one object per event followed by one terminal result or error object.
- Machine output is stdout-only. Diagnostics that cannot be represented as protocol records use stderr. Secrets and hidden reasoning are never emitted.
- Exit categories are `0` success, `1` test/case failure, `2` usage or validation error, `3` workspace/configuration error, `4` provider/environment unavailable, `5` internal/infrastructure error, and `130` interruption.

`doctor` has no platform option and checks configured platforms in Android, Web, Windows, macOS order. Its Human output shows Workspace and platform status, ordered platform prerequisite details when present, fixed checks, the three command verdicts, reasons/actions for non-ready items, and ordered deduplicated actions. macOS prerequisite labels cover full Xcode, active Xcode developer directory, Appium CLI, Appium Mac2 driver, Appium endpoint, application path, and bundle identifier. JSON and JSONL each emit exactly one terminal result record with the same prerequisite facts; JSONL emits no synthetic event. Doctor exits `0` for `ready` or `partial`, `4` for a completed `unavailable` result, `3` when Workspace registry/root/config inventory is not trustworthy, `5` for unrecoverable internal orchestration failure, and `130` for interruption.

Doctor is read-only: it does not install, mutate configuration, authenticate interactively, send model inference, launch applications/browsers, or create external sessions. Provider readiness may perform only its supported non-interactive cached-token refresh. Human color never carries unique information, and all output obeys the global secret and safe-error contract.

Android Doctor presents ordered ADB executable, uiautomator2 dependency, existing ADB server, device connection/authorization, device selection, application identity and installation checks, including stable codes and explicit copyable/manual repair guidance. It uses shared Application diagnosis and does not spawn ADB or initialize device automation. Human and JSON/JSONL distinguish missing prerequisites, timeouts and query failures; `not_applicable` explains blocked checks. No new Doctor platform or serial option is introduced. Multiple online devices direct the user to explicit Control Plane selection or an operator-controlled unambiguous connection, never to a nonexistent persisted serial setting. The default-server startup command is manual guidance, not performed by Doctor.

## Run Commands

```bash
fsq runs list [--platform PLATFORM] [--status STATUS]... [--mode strict|explore] [--since DURATION] [--case CASE_ID] [--limit NUMBER]
fsq runs show RUN_ID [--platform PLATFORM] [--open]
fsq runs logs RUN_ID [--platform PLATFORM] [--level LEVEL]... [--phase PHASE]... [--limit NUMBER]
```

All Run commands require the exact current registered Workspace root. Omitted platform means every configured platform; a supplied platform must be configured. `list` accepts repeatable status OR filters, one mode, exact normalized Case ID, a positive integer duration suffixed by `m`, `h`, or `d`, and limit `1..200` defaulting to `20`. Filtering precedes deterministic newest-first sorting and limiting. Empty results succeed. Human columns are Run ID, platform, mode, status, start, duration, and Case/Goal; machine output contains equivalent filters, counts, truncation, entries, and safe warnings.

`show` returns one bounded summary with source, result, timing, runtime metadata actually used, relative artifact paths, and warnings; it does not inline complete reports, logs, screenshots, or UI snapshots. Without platform, no match is `run.not_found` and multiple platform matches are `run.id_conflict`. Human times use the local timezone and machine times remain UTC ISO 8601.

`show --open` is Human-interactive-only. It requests Application to rebuild the contained static HTML report, then CLI opens the returned path with the system default browser and prints the normal summary plus relative HTML path. JSON, JSONL, and `--non-interactive` reject `--open` before side effects. Browser-opening failure is `run.report_open_failed` and provides only the safe Workspace-relative path for manual opening.

`logs` accepts repeatable level and phase OR filters and limit `1..5000` defaulting to `200`. It filters first, selects the newest matching limit, and presents events in stable ascending sequence order. Missing historical sequence, duplicate sequence, or reverse file order produces safe warnings; invalid non-empty JSONL records invalidate the complete result. Human output is a concise safe table. JSON emits one result containing filters, counts, truncation, warnings, and events. JSONL emits one Application event envelope per event followed by one terminal result. No matches succeed; a missing or invalid event log does not masquerade as an empty log.

Run queries sanitize secrets and unsafe backend details and return no unnecessary absolute host paths. Successfully reading a failed historical Run exits `0`. `run.not_found` exits `2`; Workspace, platform inventory, ID conflict, metadata, log, or unavailable-report errors exit `3`; HTML generation/opening, ID allocation, and internal failures exit `5`; interruption exits `130`.

## Compatibility

The executable and command hierarchy change explicitly to `fsq`. Legacy `fsq-agent run`, `run --strict`, `report`, and raw dynamic `--case-yaml` forms are not silently forwarded. Compatibility messaging may identify the corresponding new command. Case discovery treats `*.fsq.yaml` as canonical and may accept `*.codex.yaml` for one deprecation cycle with a machine-visible warning. `*.intent.yaml` and `fsq.test-intent/v1` are unsupported.

## Python Architecture

- Architecture level: Level 3 transport adapter.
- Public API: `main`.
- CLI owns Click declarations, argument decoding, terminal/JSON/JSONL rendering, prompt policy, exit-code mapping, and narrow private collaborator composition for adapter-owned runtime implementations.
- Application owns shared request validation, orchestration, operation events, results, and stable errors.
- Dependency direction: CLI to Application; never Application to CLI.

## Error Handling

CLI maps stable Application Errors to the documented exit categories and presentation protocol. Malformed command syntax and unsupported legacy options fail before invoking Application. Interrupted operations exit `130`. Unexpected exceptions are normalized as internal errors with only the exception type retained as safe diagnostic detail. Human internal-error output includes that safe exception type; machine-mode errors include it in the structured error details. Neither form includes the exception message, traceback, arguments, secrets, hidden reasoning, or unrestricted backend values. Machine-mode errors remain valid JSON or JSONL.

## Verification Scope

Verification covers command/option parsing; installed `fsq` and `fsq-agent` console-script equivalence; absence of platform installation extras; absence and rejection of public `environments` and `providers list`; rejection of `--install-driver`; platform-target exclusivity; required Web channel and optional executable path; unambiguous shared Web discovery and explicit-path validation; read-only runtime readiness; incomplete-distribution and external-prerequisite guidance; no workspace mutation before readiness; current-directory name derivation/override; existing-project adoption; idempotent initialization/update/conflict behavior; registry and directory creation; workspace precondition presentation; Provider commands outside a Workspace; shared CLI/Control Plane user configuration in both directions; complete Azure interactive and non-interactive configuration; GitHub device flow, model discovery/selection, cancellation, and machine/non-interactive rejection; atomic Provider replacement failure preservation; real safe Provider status; Human/JSON/JSONL rendering and exit codes; Doctor platform ordering, command dependency/status aggregation, component error isolation, and no-side-effect boundary; Application request mapping; safe unexpected-exception diagnostics; installed-distribution `ui` startup through package-contained Control Plane static resources and the current public server options without startup-directory workspace selection; legacy rejection/migration guidance; and absence of direct domain/runtime orchestration.

## Current Invariants

- CLI is a transport adapter over Application.
- CLI runtime composition is isolated behind a private helper and is limited to constructing collaborators injected into Application; command handlers and composition helpers do not own business behavior.
- Existing Case source files are never overwritten by `case test` or `--suggest`.
- Machine output is stable, structured, and safe for Coding Agents.
- Public command names and hierarchy match this specification exactly.
