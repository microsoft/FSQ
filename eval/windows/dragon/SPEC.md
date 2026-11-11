# Windows Evaluation Toolkit: eval/windows/dragon

## Purpose

Own the Git-maintained FSQ Dragon Pipeline definitions, deployment and setup acceptance code, WindowsAgentArena Edge evaluation adapter, report CSS, tests, and build/install entry points. This toolkit is outside the installed `fsq-agent` distribution. It does not own Dragon Service, Machine registration, operator credentials, or execution records.

## Dependencies

- Build and install use Python 3.11+ standard-library facilities and checked-in files only. Packaging does not fetch code, execute Pipelines, or require access to Dragon credentials.
- Windows execution uses PowerShell 7, Git, an interactive desktop, Edge, and an admitted FSQ runtime with its Windows dependencies. The remote bootstrap retains its pinned private Python/tool installation behavior inside explicitly executed Tasks.
- Runtime integrations use the selected FSQ installation's CLI, Models and Provider interfaces. The external evaluator's `edge_session.py` and `setup_acceptance.py` access the private native driver only in version/fingerprint-admitted evaluation or acceptance child processes; this is not an FSQ production API or a permission for FSQ modules to import private implementations.
- The fixed Dataset is the 13 original JSON definitions in `eval/windows/examples/msedge`. A checked-in hash manifest identifies the exact file set and SHA-256 values; build rejects missing, additional, or changed definitions.
- FSQ production modules do not import this toolkit. The toolkit does not import unrelated Dragon application internals.

## Public Interface

- `python eval/windows/dragon/build.py --output <directory>` produces a complete local Dragon install bundle containing the five Pipelines, their required scripts and guides, the fixed Dataset, generated setup/evaluation deployment payloads, and a bundle inventory with file hashes.
- `python eval/windows/dragon/install.py --package <directory> --dragon-data-dir <directory>` validates a built bundle and installs its Pipelines under the explicit target's `pipelines` directory. It does not discover host configuration or invoke Dragon APIs.
- Maintained Pipeline identities are `fsq-waa-edge-eval`, `fsq-dev-box-setup`, `fsq-waa-remote-eval`, `fsq-remote-readiness`, and `fsq-remote-authorization`.
- The local evaluation Pipeline retains explicit interpreter and Dataset inputs. Setup and remote evaluation transport the fixed Dataset and runtime assets in one self-contained deployment Stage. Readiness remains read-only; authorization uses FSQ's official authorization entry point and is not run by build or install.
- Script command failures are nonzero and identify the conflicting path or failed verification. There is no overwrite, force, replace, merge, or update operation.

## Internal Structure

- `build.py` and `install.py`: standalone bundle construction and guarded local installation entries.
- `dataset-manifest.json`: pinned identities for the canonical Edge Dataset; it contains no duplicated task definitions.
- `pipelines/fsq-waa-edge-eval/`: maintained Python deployment, setup, evaluation, source synchronization, invocation, reporting, native process/lock helpers, `report.css`, PowerShell Stage scripts, Pipeline definition, guide, and the original four test modules.
- `pipelines/fsq-dev-box-setup/` and `pipelines/fsq-waa-remote-eval/`: maintained Pipeline definitions and operator guides. Their `deploy.ps1` and `payload-manifest.json` are generated build outputs, not independently edited or checked-in source.
- `pipelines/fsq-remote-readiness/` and `pipelines/fsq-remote-authorization/`: Pipeline definitions and their maintained PowerShell scripts.
- `tests/`: packaging, Dataset pinning, input exclusion, and non-overwriting installation regression tests.
- `README.md`: maintenance, build, install, validation, and operator instructions without machine-specific selections.
- Generated bundles belong under ignored `dist/` or an explicit external output directory, never in the maintained Pipeline source tree.

## Python Architecture

- Architecture level: Level 3, script-oriented integration application. Existing responsibility-owned helper files separate source/runtime preparation, native process ownership, evaluation, reporting, and command entry points without introducing additional service or repository layers.
- Public API consists of standalone script commands and Pipeline Stage contracts, not FSQ Python package exports. Existing sibling imports remain local to the evaluator directory; FSQ production modules do not import these helpers.
- Existing runtime fingerprints, admitted versions, process identity checks, locks, timeout handling, independent scoring, evidence recording, and setup checks remain enforced.
- The private driver exception is restricted to the two documented evaluator helpers in isolated child processes. It does not modify FSQ source or installed package files.

## Frontend Architecture

- Architecture level: Level 1, existing static evidence-report exception.
- Reports are Python-rendered HTML with maintained `report.css`; no framework, JavaScript application, Vite entry, or new browser dependency is introduced.
- Report state comes from persisted setup/evaluation facts. Packaging does not generate or commit run reports.
- Existing report rendering, escaping, evidence links, and scoring distinctions are preserved.

## Error Handling

- Build validates the exact pinned Dataset and explicit source allowlist before publishing a bundle. Credential/configuration files, caches, Task data, evidence, and unknown files are not included through recursive directory copying.
- Bundle generation is deterministic for the same maintained inputs, including embedded archives and their manifests. An existing output with different contents is rejected rather than overwritten.
- Install validates bundle inventory, hashes, relative paths, and ordinary files before writing. Absolute paths, traversal, links/reparse points, unknown entries, and a target overlapping maintained source or the package are rejected.
- Every existing target Pipeline is preflighted before any installation writes. Identical managed contents are skipped; any changed, missing, or extra content is a conflict. The sole admitted runtime-only metadata file is `.dragon-runtime.json`, which is ignored during equality checks and never read, copied, removed, or overwritten.
- A conflict in any Pipeline aborts the complete install before adding other Pipelines. Failures are reported explicitly; existing directories and unrelated Dragon content remain untouched.

## Verification Scope

- Source inventory accounts for all maintained Python, PowerShell, CSS, Pipeline definitions, guides, tests, and build templates from the five identified source Pipelines.
- Tests cover Dataset hash pinning, deterministic complete packaging, payload/source equality, source/configuration exclusions, safe path handling, install idempotence, whole-install conflict preflight, and preservation of existing files and runtime metadata.
- The original adapter, Git source, portable deployment, and setup tests remain runnable against the maintained sources. Tests that inspect generated payloads build temporary bundles rather than requiring checked-in generated scripts.
- Python validation uses the repository-pinned Ruff lint/format checks and focused toolkit tests, with explicit reporting of unavailable platform or dependency requirements. FSQ's default pytest discovery remains scoped to its own tests.
- A package round-trip verifies the fixed JSON bytes and each deployed runtime asset. Mocked or packaging-only tests do not establish remote Machine readiness, successful authorization, or successful live evaluation.

## Current Invariants

- Migration copies maintained sources into FSQ; it never renames, deletes, rewrites, or installs over the existing Dragon runtime copies or the user's changes.
- Credentials, provider caches, `.dragon-runtime.json`, host/repository selections, user-specific absolute defaults, runtime environments, logs, locks, reports, and generated evidence are not Git-maintained inputs or bundled artifacts.
- Machine-specific examples in operator guides use placeholders. Operator-selected interpreter, Dataset, host, and model inputs remain runtime parameters; supported generic defaults and compatibility fingerprints remain explicit.
- New Python sources carry the repository-required Microsoft MIT copyright header. Migration may apply repository lint/format fixes without changing supported runtime or scoring behavior.
- Build and install do not authorize a Provider, deploy to a Machine, execute a Pipeline, change Dragon registration, or edit the original five Pipeline directories. Live execution requires a separate operator action.
