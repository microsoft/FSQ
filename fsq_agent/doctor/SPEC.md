# Module: doctor

## Purpose

Provide fsq-agent's readiness diagnosis application service. The module coordinates preflight/install, configuration/workspace, provider, selected-platform connectivity, safe repair, rerun, readiness aggregation, and text/JSON rendering for the public `doctor` command.

The module does not parse Click arguments, implement provider authentication, own platform backend mechanics, modify repository presets, install dependencies, invoke model inference, execute FSQ cases, launch target applications, create Appium sessions, or run arbitrary remediation commands.

## Dependencies

- Internal project dependencies: `models`, `config`, `providers`, and `core` public APIs.
- External dependencies: standard library terminal, path, JSON, platform, and import metadata utilities.
- Forbidden dependencies: `agent`, `capabilities`, `tools`, `fsq`, `report`, `playground`, concrete platform drivers, provider internal modules, and CLI internal modules.

## Public Interface

Current `__init__.py` exports via `__all__`:

- `DoctorService`: Stable application service that accepts a `DoctorRequest`, runs independent checks in stable order, prompts only when the request allows interaction, applies only typed eligible repairs, reruns affected checks, computes final readiness, and returns `DoctorReport`.
- `render_doctor_text(report: DoctorReport) -> str`: Renders deterministic grouped text for non-interactive results and the final state of interactive runs without relying on color for meaning.
- `render_doctor_json(report: DoctorReport) -> str`: Renders one schema-versioned JSON document with stable check ids and no routine log output.
- `DoctorProgressTextRenderer`: Stateful progress-event sink for text mode. TTY mode renders one replaceable `RUNNING` line and final highlighted status; plain mode appends one line per start/completion event without terminal control sequences.

`DoctorService` accepts an optional `DoctorProgressSink` plus injected configuration inspection, provider, platform-probe, prompt, effective-environment, environment-check, workspace-initialization, and atomic environment-update boundaries for tests. Provider and platform services receive the same sink and separately inject their clock, HTTP, import, command, process, cleanup, and temporary-resource boundaries. Default construction uses public module APIs and the current process environment.

## Behavior

Doctor resolves the selected platform from an explicit request or effective non-secret platform environment-variable presence. Exactly one inferred candidate is selected automatically. Interactive runs ask the user when there are zero or multiple candidates; non-interactive unresolved selection returns exit code `2` with actionable fixes and no secret values.

Mode `dynamic` requires base, selected-platform, and provider readiness. Mode `strict` requires base and selected-platform readiness without provider readiness. Mode `all` independently reports Dynamic LLM, Strict core, and AI assertion readiness; AI assertion requires platform and provider readiness. Targets outside a requested single mode are `not_checked`.

Android Doctor readiness is tooling-oriented when no target is connected: a successful empty `adb devices` list is a warning, not a blocker, and device-dependent checks are skipped. A transient first device-discovery timeout/nonzero result receives one bounded retry so normal daemon startup does not create a false failure. ADB execution/server failure after retry remains blocking. A configured or visible but unusable target (`offline`, `unauthorized`, serial mismatch), or ambiguous multiple online targets, remains blocking.

Checks have stable ids and statuses `pass`, `warn`, `fail`, or `skip`. A failed prerequisite skips only true dependents; independent checks continue. Every warning and failure has at least one concrete `DoctorFix`, including a verification command when safe. Readiness and exit code use the final post-repair check state: `0` means requested targets are ready, `1` means a requested target is blocked, `2` means command usage/platform selection is unresolved, and user interruption maps to `130` at the CLI boundary.

Online probes are enabled by default and bounded, with five seconds as the normal per-probe timeout. Doctor never sends a model inference request. It closes provider/HTTP clients, bounds subprocess output, terminates temporary browser processes, removes temporary browser profiles best-effort, and converts ordinary probe exceptions to sanitized checks. It does not convert `KeyboardInterrupt` or process-termination exceptions.

Every independently observable check emits `check_started` immediately before its potentially blocking work and `check_completed` as soon as its final `pass|warn|fail|skip` result exists. Diagnostic phases emit `phase_started`; repair and post-repair verification emit corresponding repair/check events. Event ordering is stable and matches final report ordering. Event emission is synchronous and failures in a presentation sink must not corrupt diagnostic state.

Text mode is interactive only when both input and output are TTYs and `--non-interactive` is absent. Omitted interactive mode selection prompts with `all` as the default. JSON implies non-interactive diagnosis and is incompatible with repair. JSON always has an empty repairs array.

Text progress rendering does not repeat completed check details in the final report section; after streaming, it prints readiness and aggregate counts. `PASS` is green, `WARN` yellow, `FAIL` bold red, `SKIP` dim gray, and `RUNNING` cyan when color is enabled. Status words are always present. Color policy is `auto` for color-capable TTY output, `always` to force ANSI styling, and `never` to disable it. JSON always disables color and progress rendering.

Eligible repairs are a closed set:

- initialize a missing current-directory fsq-agent workspace;
- add the workspace marker only to an empty existing workspace;
- atomically update validated non-secret platform environment values entered interactively, with a collision-safe backup of an existing valid `.env`;
- refresh a Copilot provider token from an already valid cached GitHub OAuth token in an existing workspace.

`--repair` applies eligible no-input repairs without confirmation. Non-interactive repair skips input-required `.env` repairs rather than inventing values. Doctor refuses malformed `.env` mutation, non-empty unmarked workspace adoption, preset/source edits, secret entry, provider login, dependency installation, administrator operations, ADB/Appium service control, and arbitrary command execution. Repair records contain only action id, sanitized target, outcome, optional backup path, and rerun check ids; they never contain entered values.

## Internal Structure

- `__init__.py`: Public exports only.
- `_service.py`: `DoctorService`, phase orchestration, dependency-aware check execution, repair coordination, reruns, readiness, and exit-code calculation.
- `_environment.py`: Effective environment presence and platform detection helpers; injectable environment checks supplied to `DoctorService` own Python/core dependency and install-provenance facts.
- `_checks.py`: Check construction, prerequisite helpers, sanitization, and stable fix builders.
- `_repairs.py`: Closed repair action ids, affected-check mapping, and config-owned non-secret value-validation adapter; `DoctorService` coordinates confirmation and records because it owns the use-case sequence.
- `_render.py`: Deterministic text and JSON rendering.
- `_streaming.py`: TTY/plain progress-event rendering, in-place line replacement, status styling, color-policy resolution, and final readiness/summary rendering.
- `_prompts.py`: TTY-safe platform/mode/non-secret value selection and repair confirmation boundary.
- `SPEC.md`: Module specification.

## Python Architecture

- Architecture level: 3 Layered Application.
- Public API: `DoctorService`, `DoctorProgressTextRenderer`, `render_doctor_text`, and `render_doctor_json` exported from `__init__.py`.
- Internal modules: all `_*.py` files are private and are not imported outside `doctor`.
- Domain boundaries: doctor owns diagnostic orchestration, safe-repair policy, readiness aggregation, and presentation; config owns settings/env/workspace mechanics, providers owns credential/endpoint mechanics, and core owns platform probe mechanics.
- Boundary models: `DoctorRequest`, `DoctorCheckResult`, `DoctorFix`, `DoctorRepairResult`, `DoctorReadiness`, `DoctorReport`, and generic probe results come from `models`.
- Dependency direction: doctor imports public APIs from `models`, `config`, `providers`, and `core`; those modules do not import doctor. CLI imports doctor's public API only.
- Rationale: mode-aware orchestration spans multiple external side-effect boundaries and interactive/automation transports, which justifies Level 3. There is no persistence or rich domain behavior requiring Clean Architecture, repositories, or DDD.

## Error Handling

Expected environment, configuration, dependency, connectivity, timeout, and repair failures become sanitized typed results and do not stop independent checks. Malformed configuration is reported precisely; checks that do not need normalized settings continue, while dependent checks are skipped. Secret values, secret lengths/fingerprints, authorization headers, cookies, credential-bearing URL components, complete PATH/Python search paths, and unbounded external output never enter reports.

Invalid option combinations and unresolved non-interactive platform selection produce a valid report with exit code `2`. JSON rendering is one clean standard-output document. Interactive cancellation does not apply the pending repair; already completed atomic repairs remain represented in the final report.

## Verification Scope

- Verification covers platform/mode selection, TTY behavior, progress emitted before blocking work, stable event ordering, TTY in-place replacement, plain-stream fallback, color policies/status styles, check ordering and skips, mode-specific readiness, Android empty-device warnings versus ADB failures, text/JSON stability, safe repair progress, backups and atomic updates, reruns, redaction, timeouts, cleanup, and exit codes.
- Boundary verification uses injected fake probes/commands/services and requires no live provider, device, browser, Appium server, or desktop target.

## Current Invariants

- Stable check ids and the JSON schema are public automation contracts.
- Progress event types and ordering are public text-observability contracts; JSON schema and final report semantics remain unchanged.
- Diagnosis has no persistent side effects; only typed eligible repair actions may write local state.
- Every warning/failure is actionable, and safe fixes include verification commands where possible.
- Final readiness is computed after repair reruns, never from stale pre-repair checks.
- Interactive prompts never request secrets and never appear unless both input and output are interactive.
- Doctor does not import implementation internals from config, providers, core, or CLI.
