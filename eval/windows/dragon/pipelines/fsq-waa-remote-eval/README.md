# FSQ WAA remote eval

**Scope:** This package owns deployment and execution of the packaged FSQ/WAA
evaluation on a selected remote Windows Machine.

**Maintenance:** Update this guide when transport, runtime preparation,
authorization, Machine routing, evidence, or supported task scope changes.

## Run on a Dev Box

For first-time preparation without scoring Cases, use
[FSQ Dev Box Setup](../fsq-dev-box-setup/README.md).
This evaluation Pipeline remains the separate path for actual task scoring.

Select **FSQ WAA Remote Eval** in Dragon and choose the remote Machine.
Choose the registered FSQ scheduling checkout on `<target-windows-machine>`.
No script pulls, resets, installs into, or edits that existing checkout.
Another Machine needs its own registered scheduling checkout selection.

[The manifest](pipeline.json) defaults to remote
`https://github.com/microsoft/FSQ.git`, branch `user/ty/win-eval-dragon`.
Optional `bootstrap-python` names a Python 3.11+ interpreter on that Machine.
If omitted, the Stage prepares private Python 3.12.12 using pinned official
uv 0.9.9, verifies the uv release asset's SHA-256, and changes no global PATH.
Git, Edge, internet access, and an unlocked interactive Windows desktop remain
host prerequisites. Readiness also probes screen capture. If Remote Desktop
is disconnected or minimized, Windows can reject capture even when an input
desktop exists; connect to the target, keep it unlocked and the connection
visible, then start a fresh Task. No desktop takeover or session-switch
workaround is performed automatically.

Unlike the local prototype, this Pipeline does not require the operator to
provide a local `fsq-python` or `dataset-dir`. Its generated Stage embeds the
complete Python/CSS evaluator and all 13 original JSON definitions. It checks
the transported payload and file hashes before use. Current evaluation scope
remains the three profile-name, font-size, and download-directory tasks.
An optional comma-separated `case-ids` selects among those tasks.

## Runtime and source identity

The Stage fetches the configured branch into a dedicated account-local cache,
records the resolved commit, and archives that exact commit into a private
snapshot. It installs the snapshot into a commit-qualified virtual environment,
checks dependencies, and verifies the installed FSQ module path, version, and
inspected Windows-driver fingerprint. It does not claim that fetching source
alone constitutes source-version evaluation.

Current source compatibility is FSQ 0.1.4 with the reviewed native-driver
fingerprint. Changed driver implementations block execution until their
adapter compatibility is reviewed. The Runner-owned Edge PID adapter remains
explicitly recorded; this is not an unmodified-driver or stock WAA VM result.

Provider authorization belongs to the executing account on the remote
Machine. No credentials, personal Profiles, or Hub authentication caches are
transported. A missing or expired Provider blocks the evaluation and is
reported separately from task capability. It requires the remote account's
normal FSQ authorization before a fresh evaluation Task.

## Evidence and retained state

Each Task owns a short directory under the executing account's
`.fsq-waa\jobs\<Task ID>`. It contains the verified package, deployment logs,
resolved Git identity, runtime identity, and per-Case evidence. Source caches
and virtual environments are retained for reuse; old PIDs are never reused.
Artifacts are published through normal Stage output markers, including on
failure, so Dragon can retain the HTML report, JSON, and ZIP on the Hub.

The report names the actual remote hostname. Scores, command records,
screenshots, and errors describe that Machine, not the Hub's previous local
evaluation. A blocked or incomplete result must not be presented as a
remote pass. Desktop input is shared with the logged-in user; do not interact
with it while a Case is executing.

## Rebuilding the transported package

The maintained implementation is in the adjacent
[local evaluator package](../fsq-waa-edge-eval/README.md).
The [maintenance build entry](../../README.md) creates this package's
`deploy.ps1` and `payload-manifest.json` from an explicit runtime-file allowlist
and the pinned canonical Dataset directory. These files exist only in the output
bundle, not the maintained source. Regenerate after changing any
runtime file or reviewed Dataset pin. The output is a self-contained Stage; remote
execution never assumes the Hub's absolute package directory exists.
