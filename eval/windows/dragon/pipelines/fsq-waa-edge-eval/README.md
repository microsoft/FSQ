# FSQ WAA Edge eval

**Scope:** This package owns the local execution procedure and interpretation for
adapting original WindowsAgentArena Edge tasks to FSQ Windows UI automation.

**Maintenance:** Update this guide when supported tasks, isolation, FSQ driver
compatibility, input parameters, or evidence and scoring behavior change.
The original Dataset remains the task-definition authority.

## Run locally

Open Dragon **Pipelines**, select **FSQ WAA Edge Eval**, and choose the local
Windows Machine. The desktop must be unlocked and interactive. Do not use the
mouse or keyboard while a task is executing because Windows input shares the
current account's desktop.

Inputs in [the manifest](pipeline.json):

| Input | Meaning |
|---|---|
| `git-url` | Remote GitHub source URL; defaults to `https://github.com/microsoft/FSQ.git`. |
| `git-branch` | Remote branch fetched before each new Task; defaults to `user/ty/win-eval-dragon`. |
| `fsq-python` | Absolute interpreter path inside the installed FSQ environment. |
| `dataset-dir` | Original WAA `examples\msedge` JSON directory on that Machine. |
| `edge-exe` | Optional Edge executable path; otherwise standard installations are discovered. |
| `case-ids` | Optional comma-separated original IDs; omitted selects the three supported tasks. |

FSQ must already have an authenticated Provider for the executing account.
The package installs nothing and does not change Provider configuration. It
records the interpreter, FSQ version, Edge version, Provider/model, raw task
bytes and hashes, and the complete Dataset inventory. A different interpreter
can select a different FSQ installation.

## Remote Git source

The Pipeline is configured by remote URL and branch, not by the operator's
existing personal checkout. After readiness, [the source Stage](sync-source.ps1)
uses [the source resolver](git_source.py) to clone once into a dedicated cache
under the executing account's `.fsq-waa\git` directory. Later Tasks reuse that
checkout, fetch the configured remote branch, and select its resolved commit
in detached-HEAD mode. It does not merge with `git pull`, update the operator's
existing FSQ checkout, switch that checkout's branch, or discard local changes.

A cache-local process lock covers synchronization. The resolver verifies its
ownership marker, origin, repository root, and clean state before updating.
Dirty or mismatched caches block the Task. Fetch failures do not silently fall
back to an older commit. Clone candidates use unique staging directories and
are never silently reused. Git may remove a failed clone itself; any surviving
partial directories are left untouched. Inspect the saved Git command logs
for failure evidence. Git hooks are disabled and long-path mode is scoped to the
owned Git commands, without changing global Git settings.

The report, `git-source.json`, Git command logs, and per-Case CLI records name
the URL, branch, resolved SHA, and dedicated checkout. **This source is code
context only:** it is not installed into the FSQ interpreter and is not
executed by the evaluation. The actual FSQ runtime remains the installation
selected by `fsq-python`, subject to the existing driver-version gate. Do not
interpret a fetched latest-source SHA as evidence that latest source was tested.
Fetching the same branch in a later Task may select a different SHA.

## Supported first local lane

[The Dataset adapter](dataset.py) supports the original profile-name, default
font-size, and download-directory tasks. It admits the corresponding original
launch and postconfiguration forms, but translates their lifecycle intent
instead of executing Dataset-provided shell commands.

All JSON tasks are inventoried. Selecting an unsupported task fails readiness;
unselected or unsupported tasks are never counted as passes. This version does
not adapt bookmarks, history, cookies, PWA installation, desktop shortcuts, or
the other Edge settings. It does not generate replacement tasks.

Each selected task starts from a newly created Profile, named `FSQ Eval`, with
default font size 16 and an attempt-private download directory. The baseline
must fail the task's metric before execution can begin. This is a declared
local compatibility lane, **not restoration of the original WAA VM snapshot**.
It preserves the original instruction and expected-rule values, not an
assertion of exact benchmark-environment equivalence.

Initialization opens an ordinary private Edge window at `about:blank`, rather
than opening a debugging endpoint or a proxy. The original source URLs are
provenance, not implicit navigation instructions.

## FSQ execution and isolation

[The runner](runner.py) invokes FSQ's installed CLI entry point in a child
process with `--output json --non-interactive` and `case create --platform
windows`. It uses a fresh registered Workspace and one attempt per task.
Generated Cases are evidence only; this lane does not replay or rewrite them.

The installed FSQ `0.1.0a3` Windows driver normally resolves a window by title
or active state. [The process-scoped adapter](edge_session.py) narrows that
resolution to the private Edge PID. It also makes `launchApp` attach to the
already owned test application instead of starting another instance, and
makes driver close/`killApp` detach rather than terminate the Runner-owned
application. The Runner alone owns actual browser termination. It
verifies executable identity and the exact private Profile command argument
before enabling the adapter, retains a native process handle, and rejects
target access after that process exits. It leaves FSQ's planning and ordinary
UI actions unchanged. The adapter is installed only in the child process;
FSQ source and installed package files are not edited.

This private-driver integration is version-gated. The installed 0.1.0a3 lane
and the source 0.1.4 lane with the inspected driver fingerprint are admitted.
Another native driver must be explicitly reviewed; it is not silently
treated as compatible.
The report names this adapter so its results cannot be mistaken for an
unmodified-driver baseline.

Windows Jobs own the Edge launcher and descendants and each FSQ invocation.
An account-local byte-range lock rejects overlapping invocations of this
package. The original global Edge termination commands are never executed.
Only the owned test browser is closed, with bounded process settlement.
A cleanup requiring forced termination makes evaluation incomplete instead of
assuming Preferences were flushed.

Private Profiles and PID-scoped UIA targeting do not provide an OS sandbox.
The FSQ process has the executing account's permissions, and restrictions in
its goal do not constitute security enforcement. Do not supply personal test
data. Use a dedicated test account or VM before evaluating tasks requiring
stronger desktop isolation or system-wide changes.

## Independent scoring and evidence

After FSQ returns, the runner captures the owned window, gracefully closes it,
and reads only that attempt's private Preferences file. There is no read or
write of the account's normal Edge Profile.

The custom evaluator implements the selected original metric semantics:
profile name and download path use exact equality; font size uses
`min <= default_font_size < max`. It does not import the whole WAA evaluator
dependency tree. Missing or malformed state is an evaluation error, not a
valid zero or a passing fallback.

An FSQ configuration or Provider error, including an actual request returning
401 after cached status said ready, is a blocked execution. Any observed
unchanged-state score is diagnostic only and excluded from benchmark scoring.
Later tasks are not started until the blocker is corrected in a fresh Task.

The original result score is independent of FSQ's self-reported status.
Both are retained. The HTML report puts the round's local success rate and
its explicit valid-evaluation denominator first, followed by a readable
per-Case table with independent verdicts, actual and expected values, FSQ
status, and CLI duration. Selected, executed, validly evaluated, passing,
nonpassing, and unscored counts remain distinct. The complete Dataset size
and unexecuted tasks are visible; the round's rate is not a full-Dataset
success claim. The result donut shows the selected tasks' verdict counts;
the separate coverage donut shows FSQ invocations over the complete Dataset.
Per-Case bars compare recorded CLI durations within this round, not against
an invented historical baseline or timeout budget. Total round duration uses
the recorded start/end timestamps and includes setup and scoring; missing
timestamps remain unknown rather than being replaced with a CLI-time sum.
Expand each Case for its original instruction and embedded
before/after screenshots. The ZIP
includes original Dataset JSON, machine records, process diagnostics, selected
observations, and available FSQ Runs, including their screenshots and Cases.
Private browser files and workspaces are retained under the account's
`.fsq-waa` directory, named in results, for investigation. Full browser
Profiles are not included in the exported evidence.

Every FSQ CLI invocation now writes a per-call `*.command.json` before the child
process starts. It preserves the exact argument array, full goal when present,
working directory, interpreter and FSQ version, timeout, actual wrapper
arguments, target-adapter scope, and package code hashes. Completion updates
the same record with timestamps, exit status, timeout or protocol failure,
Run ID, and stdout/stderr filenames. `prepared` means the parameters were
saved but no terminal outcome was confirmed; it does not prove execution
never started.

Expand **FSQ CLI 调用记录** inside a Case to inspect its initialization,
doctor, and creation calls. The ZIP carries the original record files.
Authentication commands, credential caches, and the complete environment are
not collected. Goals are recorded in full, so use only authorized test data
and never put credentials in the Dataset instruction.

The Windows command-line display is CLI-equivalent, not a faithful replay of
the PID-scoped wrapper. Old PIDs and window handles must not be reused, and
the actual wrapper requires its owning Job's stdin admission. To reproduce
the isolated execution, start a fresh Pipeline Task using the record's
`reproduction.variables`, selecting the one original Case with `case-ids`.
Paths and Provider setup must exist on that Machine; matching arguments do
not guarantee identical model responses. Historical Tasks without these
records are labelled as missing rather than having commands reconstructed
and presented as observed.

The Pipeline publishes HTML, JSON, and ZIP outputs through normal Stage output
handling before Dragon removes Task scratch storage. Failed preparation or
execution also publishes available reports without changing the failure to
success. Results use `blocked` for readiness failures, `incomplete` for
unestablished execution/evaluation, and `failed` for valid nonpassing task
scores. No automatic retries occur. A single attempt is not stability evidence.
Unsupported tasks and unknown denominators remain explicit in the report.

## Other Dev Boxes

Use [FSQ Dev Box Setup](../fsq-dev-box-setup/README.md) to prepare and accept an
already-connected remote Machine without scoring Cases by default.

Use the separately packaged
[remote Pipeline](../fsq-waa-remote-eval/README.md) for deployment to another
Windows Machine. It transports the complete evaluator and Dataset, prepares a
private Python/runtime from the exact selected source commit, and records the
actual execution host. This local Pipeline retains its explicit interpreter
and Dataset inputs; it does not silently become the remote deployment path.
Remote success requires actual Task evidence, not the local baseline or
successful package generation.

## Verification

For source maintenance, from this directory in the FSQ checkout (not an installed
Dragon data directory), using an interpreter with the repository test dependencies:

```powershell
& '<fsq-python>' -W error::ResourceWarning -m pytest -v test_setup.py test_portable.py test_git_source.py test_adapter.py
```

[The tests](test_adapter.py) cover Dataset admission, metric boundary values,
missing evidence, immutable inputs, malicious command rejection, process
identity rejection, overlap, long-root rejection, and report bundling.

Each attempt has a unique registered Workspace name; different Tasks never
update an earlier Workspace's target configuration.

The package is Windows-only. Logical evaluation roots above 110 characters or
containing output-marker control characters are deliberately rejected before
materialization. This Git-maintained toolkit participates in root pytest
discovery and changes no Dragon browser UI, FSQ source, or WAA source.
