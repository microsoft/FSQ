# FSQ Dragon maintenance toolkit

This standalone toolkit is maintained in Git, outside the installed `fsq-agent`
distribution. It owns five Pipeline definitions and their maintained scripts.
Build and install need only Python 3.11+ and the source checkout. Neither command
runs Dragon, PowerShell stages, authorization, desktop automation, or evaluation.

## Build and install

From the FSQ repository root:

```powershell
python eval\windows\dragon\build.py --output dist\dragon-bundle
python eval\windows\dragon\install.py --package dist\dragon-bundle --dragon-data-dir '<dragon-data-directory>'
```

Use an explicit Dragon data directory, not a Machine checkout. No host configuration
is discovered. Inspect the bundle first. All five targets under `pipelines/` are
preflighted **before any writes**. New directories install; byte-identical managed
trees skip without rewriting. Changed, missing, or extra files (including unknown
empty directories) fail the entire batch. There is no overwrite/update/force mode.
Choose a new data directory or resolve a conflict outside this tool; do not delete
existing user changes to make installation pass.

The sole runtime-only exception is the top-level `.dragon-runtime.json` entry in
an existing Pipeline: it is not read, copied, removed, or modified. Other Dragon
Pipelines and unrelated data are untouched. Newly written entries are staged;
failures roll back only this invocation's new entries where safe. Concurrently
changed files are preserved and an incomplete rollback is an explicit error.
Run installation with Dragon/operator writers quiescent; this is not a
cross-process transaction or a power-loss recovery system.

Build outputs belong in ignored repository `dist/` or an external directory.
An identical existing output skips; any difference fails without overwriting.
Bundles contain a complete `bundle-manifest.json` SHA-256 inventory (the inventory
itself is not self-hashed), all maintained Pipeline assets and original tests,
the fixed Dataset, generated deployment scripts and payload manifests, and this
guide. Hashes detect corruption, **not authenticity**: use trusted source/bundles.
No links/reparse points, traversal, absolute paths, case-ambiguous entries, or
unknown package entries are accepted. Do not select overlapping source, package,
or target trees.

## Ownership and maintenance

* `pipelines/fsq-waa-edge-eval/` owns the shared evaluator, setup/acceptance,
  isolated process/lock helpers, Git synchronization, HTML rendering, CSS, local
  stages, the remote-stage template, and four original test modules.
* Setup and remote evaluation own only `pipeline.json` and their guides.
  Their `deploy.ps1` and `payload-manifest.json` are **generated outputs**, never
  checked-in inputs. `build_remote.py` supplies the deterministic ZIP prior art;
  use the top-level builder for complete pinned bundles.
* Readiness and authorization retain their explicit helper scripts. Installing
  these files does not execute them or authorize a Provider.
* `dataset-manifest.json` pins the exact 13 original JSON definitions already in
  `eval/windows/examples/msedge`. No duplicate corpus is maintained. Changed,
  missing, additional, or case-ambiguous inputs block the build.

Only explicit source allowlists are copied. `.env`, `.npmrc`, runtime metadata,
credentials/configuration, caches, virtual environments, logs, locks, reports,
Task data and evidence are never discovered or bundled. Missing maintained
inputs fail; extra source files do not become distributable implicitly.
Original Dragon copies remain read-only migration inputs, not build dependencies.

## Operator execution is separate

After an explicitly chosen installation, use Dragon's normal Machine connection
and scheduling procedures. Select the execution Machine and registered scheduling
checkout yourself; no personal Machine selection is transported. See the local,
setup and remote evaluation Pipeline guides for input defaults, inspected runtime
versions/driver fingerprints, Provider behavior, task scope and scoring limits.
The local lane requires explicit interpreter and Dataset paths; the bundle's
`dataset/msedge` directory can be selected. Keep that bundle if using this path.
Remote setup/evaluation embed the fixed Dataset and runtime assets in one stage.

Actual execution requires Windows, PowerShell 7, Git, Edge, an interactive unlocked
desktop and an admitted FSQ runtime. Remote bootstrap can download its pinned tools
and install the selected source into a private environment **only when explicitly
executed by an operator**. Setup can request official device authorization and a
minimal charged model probe; its default success means environment ready, not
scored Cases. The private-driver integration remains version/fingerprint gated.
Package tests and successful installation establish no live readiness,
authorization, remote deployment, or evaluation success.

## Offline validation

```powershell
python -m pytest eval\windows\dragon -q
python -m ruff check eval\windows\dragon
python -m ruff format --check eval\windows\dragon
```

Use the repository-pinned Ruff version from `pyproject.toml`. Root pytest discovery
does not include these independent evaluation tests; run them explicitly.
Imported payload tests build temporary complete bundles;
they do not require checked-in deployment scripts or the original Dragon data.
Native desktop/live evaluation paths need separate operator validation.
