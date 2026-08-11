# Module: doctor

## Purpose

Provide fsq-agent's diagnosis application service. The module coordinates preflight/install, configuration/workspace, provider, selected-platform connectivity, safe repair, rerun, overall result calculation, and text/JSON rendering for the public `doctor` command.

The module does not parse Click arguments, implement provider authentication, own platform backend mechanics, modify repository presets, install dependencies, invoke model inference, execute FSQ cases, launch target applications, create Appium sessions, or run arbitrary remediation commands.

## Dependencies

- Internal project dependencies: `models`, `config`, `providers`, and `core` public APIs.
- External dependencies: standard library terminal, path, JSON, platform, and import metadata utilities.
- Forbidden dependencies: `agent`, `capabilities`, `tools`, `fsq`, `report`, `playground`, concrete platform drivers, provider internal modules, and CLI internal modules.

## Public Interface

Current `__init__.py` exports via `__all__`:

- `DoctorService`: Stable application service that accepts a `DoctorRequest`, runs independent checks in stable order, prompts only when the request allows interaction, applies only typed eligible repairs at immediate checkpoints, verifies applied repairs before dependent work continues, computes one final result, and returns `DoctorReport` containing only final check states.
- `render_doctor_text(report: DoctorReport) -> str`: Renders deterministic grouped text for non-interactive results and the final state of interactive runs without relying on color for meaning.
- `render_doctor_json(report: DoctorReport) -> str`: Renders one schema-versioned JSON document with stable check ids and no routine log output.
- `DoctorProgressTextRenderer`: Stateful progress-event sink for text mode. TTY mode renders one replaceable `RUNNING` line and final highlighted status; plain mode appends one line per start/completion event without terminal control sequences.

`DoctorService` accepts an optional `DoctorProgressSink` plus injected configuration inspection, provider, platform-probe, prompt, effective-environment, environment-check, workspace-initialization, and atomic environment-update boundaries for tests. Provider and platform services receive the same sink and separately inject their clock, HTTP, import, command, process, cleanup, and temporary-resource boundaries. Default construction uses public module APIs and the current process environment.

## Behavior

Doctor resolves the selected platform from an explicit request or effective non-secret platform environment-variable presence. Exactly one inferred candidate is selected automatically. Interactive runs ask the user when there are zero or multiple candidates; non-interactive unresolved selection returns exit code `2` with actionable fixes and no secret values.

Doctor always runs base, selected-platform, and provider diagnostics as one complete diagnosis. Checks do not classify failures by execution target. The final valid diagnosis is `ready` with exit code `0` when no final check failed and `blocked` with exit code `1` when any final check failed. Warnings and skips alone do not block the diagnosis.

Android Doctor readiness is tooling-oriented when no target is connected: a successful empty `adb devices` list is a warning, not a blocker, and device-dependent checks are skipped. A transient first device-discovery timeout/nonzero result receives one bounded retry so normal daemon startup does not create a false failure. ADB execution/server failure after retry remains blocking. A configured or visible but unusable target (`offline`, `unauthorized`, serial mismatch), or ambiguous multiple online targets, remains blocking.

Checks have stable ids and statuses `pass`, `warn`, `fail`, or `skip`. They contain no affected-target classification. A failed prerequisite skips only true dependents; independent checks continue. Every warning and failure has at least one concrete `DoctorFix`, including a verification command when safe. Doctor evaluates repair eligibility when a check becomes available. In interactive text runs, an actionable issue is withheld as a final check result, emitted transiently as `action_required`, and decided through a confirmation prompt that names the concrete `DoctorFix.description` action rather than merely repeating the detected problem. It is published through `check_completed` only after decline/skip/failure or immediate verification. A successful verification may replace the stale issue with `pass`; declined, skipped, failed, and unresolved repairs preserve the original or verified `warn`/`fail` severity. Overall status and exit code use final check states: `0` means no check failed, `1` means at least one check failed, `2` means command usage/platform selection is unresolved, and user interruption maps to `130` at the CLI boundary. Doctor verification commands use `fsq-agent doctor --platform <platform> --non-interactive` without a diagnostic selector.

Online probes are enabled by default and bounded, with five seconds as the normal per-probe timeout. Doctor never sends a model inference request. It closes provider/HTTP clients, bounds subprocess output, terminates temporary browser processes, removes temporary browser profiles best-effort, and converts ordinary probe exceptions to sanitized checks. It does not convert `KeyboardInterrupt` or process-termination exceptions.

Every independently observable check emits `check_started` immediately before its potentially blocking work and `check_completed` as soon as its final `pass|warn|fail|skip` result exists. An interactively actionable check emits `action_required` before the repair decision and does not first emit a final `check_completed` failure. Accepted repair ordering is `check_started`, `action_required`, `repair_started`, `repair_completed`, optional verification `check_started`, then final verified `check_completed`; declined, skipped, or failed repair emits the original final `check_completed` after `repair_completed`. Diagnostic phases emit `phase_started`. Event ordering is stable and matches final report ordering. Event emission is synchronous and failures in a presentation sink must not corrupt diagnostic state.

Text mode is interactive only when both input and output are TTYs and `--non-interactive` is absent. Interactive runs may prompt for unresolved platform selection, repair confirmation, and eligible non-secret repair inputs but never prompt for a diagnostic mode or secret. `ACTION REQUIRED` is a transient text state, not a check/report status. Non-interactive text without `--repair` never emits `ACTION REQUIRED` or prompts and publishes final check states directly. JSON implies non-interactive diagnosis, is incompatible with repair, always has an empty repairs array, contains no transient action-required data, and uses schema version `1` without diagnostic-mode, target-readiness, or affected-target fields.

Human-readable text renders each concrete check as one bracketed section such as `[Appium status]`; phase events remain presentation-neutral and do not render duplicate phase headings. Section titles are derived deterministically from stable check ids by splitting id/underscore words, retaining a meaningful suffix, applying a small presentation-owned acronym map, and falling back to the readable full id. The stable id and JSON are unchanged. Lines within a section use `Check`, `Action`, `Input`, `Repair`, `Verify`, `Result`, `Fix`, `Run`, and `Backup` labels as applicable. The streaming renderer associates repair events through their target check id, keeps progress/repair/verification/final output under the same section, displays `ACTION REQUIRED` with the action description before a repair prompt, removes that temporary prompt block after the decision, then preserves the action description and compact `Input: y|n` decision with the repair outcome. Declined repairs render `Input: n`; accepted repair attempts render `Input: y`; automated non-interactive repairs have no user-input line. The repair status communicates whether the accepted operation was applied, skipped, or failed; no separate choice line is rendered. It never opens duplicate final sections for one repaired check. Plain output is append-only, uses the same bracketed hierarchy, and emits no cursor-control sequences. Deterministic rendering consumes a completed report, renders final checks in report order without category grouping, associates repair records with their target sections, and uses a fallback `[Repair: <target>]` section for unmatched repairs. It never renders `RUNNING` or `ACTION REQUIRED`. Deterministic and streamed final text use two lines: `Summary: PASS|FAIL|ERROR|CANCELLED` and `Checks: <passed>, <warnings>, <failed>, <skipped>`. They contain no readiness section or per-check impact lines. `PASS` is green, `WARN` and `ACTION REQUIRED` use warning styling, `FAIL` is bold red, `SKIP` dim gray, and `RUNNING` cyan when color is enabled; final `ERROR` and `CANCELLED` statuses also remain visible with terminal-aware styling. Status words are always present. Color policy is `auto` for color-capable TTY output, `always` to force ANSI styling, and `never` to disable it. JSON always disables color and progress rendering.

Eligible repairs are a closed set:

- initialize a missing current-directory fsq-agent workspace;
- add the workspace marker only to an empty existing workspace;
- atomically update validated non-secret platform environment values entered interactively, with a collision-safe backup of an existing valid `.env`;
- refresh a Copilot provider token from an already valid cached GitHub OAuth token in an existing workspace.

`--repair` applies eligible no-input repairs without confirmation when their issue is encountered and immediately verifies each applied repair before continuing. Non-interactive repair skips input-required `.env` repairs rather than inventing values and publishes the original severity. Interactive repair checkpoints immediately refresh effective environment, normalized settings, and affected probe construction when a repair changes later prerequisites. Doctor suppresses duplicate prompts for the same repair action/check pair and replaces stale checks resolved by one repair. Doctor refuses malformed `.env` mutation, non-empty unmarked workspace adoption, preset/source edits, secret entry, provider login, dependency installation, administrator operations, ADB/Appium service control, and arbitrary command execution. Repair records are encounter-ordered, contain only action id, sanitized target, outcome, optional backup path, and checks actually verified immediately in `rerun_check_ids`, and never contain entered values.

## Internal Structure

- `__init__.py`: Public exports only.
- `_service.py`: `DoctorService`, phase orchestration, dependency-aware check execution, immediate repair checkpoints, verification, final check replacement, overall status, and exit-code calculation.
- `_environment.py`: Effective environment presence and platform detection helpers; injectable environment checks supplied to `DoctorService` own Python/core dependency and install-provenance facts.
- `_checks.py`: Check construction, prerequisite helpers, sanitization, and stable fix builders.
- `_repairs.py`: Closed repair action ids, affected-check mapping, and config-owned non-secret value-validation adapter; `DoctorService` coordinates confirmation and records because it owns the use-case sequence.
- `_render.py`: Deterministic bracketed check-section text and JSON rendering.
- `_streaming.py`: TTY/plain live check-section state, bracketed titles, in-place transient replacement, repair association, status styling, color-policy resolution, and final summary rendering.
- `_presentation.py`: Private stable-check-id to human-readable title formatting shared by deterministic and streaming text renderers.
- `_prompts.py`: TTY-safe platform/non-secret value selection and repair confirmation boundary.
- `SPEC.md`: Module specification.

## Python Architecture

- Architecture level: 3 Layered Application.
- Public API: `DoctorService`, `DoctorProgressTextRenderer`, `render_doctor_text`, and `render_doctor_json` exported from `__init__.py`.
- Internal modules: all `_*.py` files are private and are not imported outside `doctor`.
- Domain boundaries: doctor owns diagnostic orchestration, safe-repair eligibility and decisions, immediate verification scheduling, final check replacement, overall result calculation, and presentation; config owns settings/env/workspace mechanics, providers owns credential/endpoint mechanics, and core owns platform probe mechanics. Diagnostic producers do not prompt or apply repairs.
- Boundary models: `DoctorRequest`, `DoctorCheckResult`, `DoctorFix`, `DoctorRepairResult`, `DoctorReport`, and generic probe results come from `models`.
- Dependency direction: doctor imports public APIs from `models`, `config`, `providers`, and `core`; those modules do not import doctor. CLI imports doctor's public API only.
- Rationale: complete diagnostic orchestration spans multiple external side-effect boundaries and interactive/automation transports, which justifies Level 3. There is no persistence or rich domain behavior requiring Clean Architecture, repositories, or DDD.

## Error Handling

Expected environment, configuration, dependency, connectivity, timeout, and repair failures become sanitized typed results and do not stop independent checks. Malformed configuration is reported precisely; checks that do not need normalized settings continue, while dependent checks are skipped. Secret values, secret lengths/fingerprints, authorization headers, cookies, credential-bearing URL components, complete PATH/Python search paths, and unbounded external output never enter reports.

Invalid option combinations and unresolved non-interactive platform selection produce a valid report with exit code `2`. JSON rendering is one clean standard-output document. Interactive cancellation during decision, value entry, repair, or verification does not apply the pending incomplete repair; already completed atomic repairs and verified results remain represented in the final report.

## Verification Scope

- Verification covers bracketed per-check titles, stable-id title derivation and fallback, one live section per check, repair/verification/final association, transient TTY replacement, append-only plain output, phase-heading suppression, unmatched-repair fallback sections, platform selection, rejection of the unsupported `--mode` option, unconditional provider diagnosis, immediate `ACTION REQUIRED` ordering, accepted/declined/skipped/failed/unresolved repairs, immediate verification and final-state replacement, refreshed settings/probes, duplicate-prompt suppression, non-interactive direct final states, `--repair` no-input behavior, JSON isolation, overall result calculation, two-line final summaries, backups and atomic updates, redaction, timeouts, cleanup, and exit codes.
- Boundary verification uses injected fake probes/commands/services and requires no live provider, device, browser, Appium server, or desktop target.

## Current Invariants

- Stable check ids and the JSON schema are public automation contracts.
- Progress event types and ordering are public text-observability contracts; JSON schema and final report semantics remain unchanged.
- Diagnosis has no persistent side effects; only typed eligible repair actions may write local state.
- Every warning/failure is actionable, and safe fixes include verification commands where possible.
- Final reports contain only final verified check states; status and summary never use stale pre-repair checks.
- Interactive prompts never request secrets and never appear unless both input and output are interactive.
- Doctor does not import implementation internals from config, providers, core, or CLI.
