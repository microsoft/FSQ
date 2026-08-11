# FSQ Doctor Design

## Goal

Provide `fsq-agent doctor` as one complete local diagnosis with one overall result, immediate safe repair and verification, and clearly separated per-check text output.

This document is the consolidated Doctor design. It supersedes the earlier mode-specific, target-readiness, post-diagnosis repair, and phase-grouped text designs.

## Scope

- Diagnose the complete environment, workspace, configuration, platform, provider, and runtime-secret surface on every valid invocation.
- Report one overall result from final check states.
- Handle eligible repairs immediately when a problem is detected.
- Verify every applied repair before treating the check as passed.
- Render each concrete check in one bracketed text section derived from its stable check id.
- Keep JSON machine-oriented and free of presentation-only state.
- Preserve bounded probes, redaction, cleanup, deterministic non-interactive behavior, and a closed repair allowlist.

## Public Command

```text
fsq-agent doctor [--platform android|web|windows|macos] [--format text|json] [--color auto|always|never] [--non-interactive] [--repair]
```

The command does not expose `--mode`. Passing it is an unknown-option usage error.

Interactive behavior requires both stdin and stdout to be TTYs and no `--non-interactive` flag. JSON is non-interactive, diagnosis-only, and incompatible with `--repair`.

The command uses the current working directory and its `.fsq-agent-workspace`; it exposes no config or workspace override.

## Diagnosis

A valid run performs, in order:

1. Platform selection.
2. Python version, core dependency, and install-provenance checks.
3. Workspace checks.
4. Platform preset, `.env`, effective-environment, and settings inspection.
5. Selected-platform probes.
6. Provider credential/configuration and bounded endpoint probes.
7. Runtime-secret presence checks.
8. Final summary from verified check states.

Checks use only `pass`, `warn`, `fail`, and `skip`. They do not carry execution-target classifications.

The overall result is:

- no final failed check: `ready`, displayed `PASS`, exit `0`;
- any final failed check: `blocked`, displayed `FAIL`, exit `1`;
- invalid/unresolved usage: `usage_error`, displayed `ERROR`, exit `2`;
- interruption: `cancelled`, displayed `CANCELLED`, exit `130`.

Warnings and skips alone do not block the diagnosis.

## Platform Selection

An explicit platform wins. Without one, Doctor inspects effective non-secret platform environment-variable presence. Exactly one candidate is selected automatically. Interactive runs ask the user when selection is unresolved; non-interactive unresolved selection returns a typed usage-error report with exit `2`.

Effective environment uses current-directory `.env` values with non-empty process environment values taking precedence.

## Boundary Models

Doctor retains request, fix, check, repair, report, and progress-event models.

It does not define or serialize:

- `DoctorMode`;
- requested mode;
- `DoctorTarget`;
- `DoctorReadiness` or per-target readiness items;
- `affected_targets`;
- report-level readiness data.

Doctor JSON remains schema version `1` and contains platform/source, overall status and exit code, final checks, repairs, and summary counts.

Progress event types are:

- `phase_started`
- `check_started`
- `action_required`
- `check_completed`
- `repair_started`
- `repair_completed`
- `summary_ready`

`action_required` is presentation-only and never becomes a check status or persisted report state.

## Check Families

### Environment

Doctor checks:

- Python `>=3.11`;
- core imports such as Pydantic, Click, YAML, and Jinja;
- source-checkout metadata;
- imported package origin;
- active interpreter coherence with the checkout `.venv`.

It reports bounded provenance categories rather than complete Python search paths or environment dumps.

### Workspace

Doctor checks the current-directory `.fsq-agent-workspace` and marker contract. It may safely create a missing workspace or mark an empty workspace, but refuses files, invalid markers, and non-empty unmarked directories.

### Configuration

Doctor uses a side-effect-free inspection path that:

- reads the selected committed platform preset;
- reads `.env` and applies process-over-file precedence;
- rejects obsolete settings;
- validates and normalizes `Settings`;
- resolves expected paths without creating output/workspace directories;
- returns sanitized failures instead of collapsing independent diagnosis.

If normalized settings are unavailable, independent platform checks continue where safe and true dependents become `skip`.

### Android

Android probes cover:

- ADB installation and execution;
- bounded `adb devices`, with one retry for daemon startup;
- configured or unique online device selection;
- uiautomator2 package and basic communication;
- app-id presence and target package installation.

An empty successful device list is `warn`; offline, unauthorized, ambiguous, or mismatched targets are `fail`.

### Web

Web probes cover:

- Playwright Python dependency;
- configured Chrome executable validity;
- isolated headless Chrome startup with a temporary profile.

The temporary process/profile is cleaned up best-effort. Doctor does not navigate, use a user profile, or create an FSQ browser session.

### Windows

Windows probes cover:

- Windows host;
- pywinauto and Pillow dependencies;
- application path;
- pywinauto backend kind;
- window-title regex;
- launch arguments;
- a warning that real accessibility automation is not exercised.

### macOS

macOS probes cover:

- Darwin host;
- Appium Python Client;
- bounded Appium `/status` readiness;
- Mac2 driver availability;
- bundle id or application path;
- a warning that real Appium session/accessibility automation is not exercised.

Doctor never creates an Appium session.

### Provider

GitHub Copilot diagnosis checks cached GitHub OAuth and short-lived Copilot provider-token state, plan-specific endpoint selection, and bounded endpoint reachability. It never starts device-code login. If an existing valid OAuth token can refresh an expired/missing provider token, Doctor offers the explicit cached-token refresh repair.

Azure OpenAI diagnosis checks the fixed endpoint/model/API-key environment shape and bounded endpoint reachability. It does not send a model inference request or claim deployment authorization, quota, or inference success.

### Runtime Secrets

Doctor reports allowlisted runtime-secret names that currently have no value without reading or rendering the secret values. Without a concrete case, missing values are warnings rather than automatic blockers.

## Immediate Repair

Safe repairs are a closed allowlist:

- initialize a missing workspace or mark an empty workspace;
- update an allowlisted non-secret platform environment value through validated atomic `.env` mutation;
- refresh a cached Copilot provider token from an existing valid GitHub OAuth token.

Interactive repair flow:

1. Detect an actionable warning/failure.
2. Emit transient `ACTION REQUIRED` without first publishing a final failure.
3. Show the concrete `DoctorFix.description` action.
4. Prompt `Apply repair: <action>. [Y/n]`.
5. Apply or decline immediately.
6. Verify an applied repair immediately.
7. Publish only the final verified result before dependent diagnosis continues.

Declined, skipped, failed, omitted, exceptional, or interrupted verification preserves the original or verified severity. A check becomes `PASS` only after an explicit successful verification result.

`--repair` immediately applies no-input repairs without confirmation. Input-required environment repairs are skipped in non-interactive mode; Doctor never invents values.

Duplicate prompts are suppressed by repair action, check id, and environment-variable identity. When a probe family is retried, already settled checks are retained without duplicate output. `rerun_check_ids` contains only checks actually observed during immediate verification.

When environment repair changes configuration, Doctor reloads effective environment and normalized settings and reconstructs affected platform probes. Provider refresh is followed by an immediate provider reprobe.

JSON performs no repair and contains no transient action-required data.

## `.env` Mutation

Allowlisted non-secret values are validated with runtime-equivalent path, URL, backend, regex, app-id, and launch-argument rules before any write.

Atomic updates:

- refuse malformed existing `.env` content;
- preserve unrelated assignments/comments/order and line endings where practical;
- create a collision-safe sibling backup for an existing file;
- write through a temporary file and atomic replacement;
- preserve access controls where supported and use owner-only backup permissions best-effort;
- return only key names and paths, never entered values.

## Text Presentation

Every concrete check owns one section:

```text
[Appium status]
  Check: Appium /status is reachable and ready.
  Result: PASS
```

For a declined repair:

```text
[MacOS target]
  Action: Correct FSQ_MACOS_APP_PATH.
  Input: n
  Repair: DECLINED
  Check: The configured macOS application path does not exist.
  Result: FAIL
  Fix: Correct FSQ_MACOS_APP_PATH.
  Verify: fsq-agent doctor --platform macos --non-interactive
```

For an accepted repair attempt, `Input: y` is shown. Automated `--repair` has no user input and therefore no `Input` line.

Section fields are used as applicable:

- `Check`
- `Action`
- `Input`
- `Repair`
- `Verify`
- `Result`
- `Fix`
- `Run`
- `Backup`

Titles are derived from stable check ids with presentation-owned acronym/product casing, for example:

- `environment.python_version` → `Python version`
- `workspace.initialized` → `Workspace initialized`
- `android.adb.devices` → `ADB devices`
- `macos.appium.status` → `Appium status`
- `provider.github_copilot.credentials` → `GitHub Copilot credentials`

Unknown ids fall back to a readable full-id title. Phase events do not render duplicate visible phase headings.

TTY output replaces transient running/action/prompt rows in place while preserving the bracketed title. After the decision, it redraws the concrete Action, compact `Input: y|n`, Repair, verification, and final state without a blank gap. Plain output is append-only and uses the same section hierarchy without cursor-control sequences.

Completed-report text renders final check sections in report order and associates repair records by target. Unmatched repair records remain visible in `[Repair: <target>]` sections. JSON contains no display titles.

The final summary remains:

```text
Summary: PASS|FAIL|ERROR|CANCELLED
Checks: N passed, N warnings, N failed, N skipped
```

## Safety and Redaction

Doctor never:

- requests secrets;
- starts provider device-code login;
- installs dependencies;
- controls ADB/Appium services;
- launches the target workflow/application;
- creates an Appium session;
- executes arbitrary repair commands.

Entered non-secret values are validated before writes and are not persisted in reports/events. Secret-bearing summaries, metadata, commands, and URLs are sanitized before final reporting. URLs retain only safe scheme/host/path data.

Presentation sink failures do not alter diagnostic state. Ordinary probe, repair, and verification failures become typed report data where appropriate. Keyboard interruption returns a cancellation report retaining completed repairs and final known check states.

## Ownership and Architecture

- `doctor` remains Python Architecture Level 3 and owns orchestration, repair policy, verification scheduling, final check replacement, progress ordering, and text presentation.
- `models` remains Level 2 and owns serializable boundary contracts only.
- `config` owns side-effect-free inspection, environment-value validation, atomic `.env` updates, and safe workspace initialization.
- `providers` owns provider diagnosis and explicit cached-token refresh mechanics.
- `core` owns platform diagnostic probes.
- `cli` owns option parsing, TTY detection, renderer selection, and exit propagation.

No dependency direction changes or new public APIs are introduced. Presentation helpers remain private to Doctor.

## Verification

Verification covers:

- mode and target-readiness removal;
- complete diagnosis and overall status mapping;
- platform selection and unresolved usage;
- environment/workspace/config/platform/provider/runtime-secret checks;
- immediate accepted/declined/skipped/failed repair ordering;
- workspace/config/platform/provider verification and settings refresh;
- omitted, exceptional, and interrupted verification;
- settled-check suppression and accurate rerun ids;
- bracketed titles and fallback title derivation;
- target-associated repair rendering and unmatched repair fallback;
- transient TTY clearing without deleting titles or leaving blank gaps;
- Action and Input preservation after prompt clearing;
- append-only plain output;
- JSON isolation and redaction;
- summary and exit codes;
- focused and full test suites;
- SPEC/code synchronization and independent implementation audit.
