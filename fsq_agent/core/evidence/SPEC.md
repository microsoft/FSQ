# Module: core.evidence

## Purpose

Own run-contained artifact storage, durable runner evidence journals, atomic evidence checkpoints, and read-only reconstruction. Evidence persists safe facts supplied by Runner and harness observation boundaries; it does not decide execution, Run lifecycle/liveness, transport projection, Case recording, or report presentation.

## Dependencies

- `models`: evidence bundles, runner events/results, artifact references, and safe metadata.
- `core.interfaces`: artifact/evidence sink boundaries where required by callers.

Evidence must not import adapters, Application, Agent, Case DSL, concrete harnesses, concrete drivers, or report renderers.

## Public Interface

- `ArtifactStore`: owns contained unique artifact allocation, atomic writes, and explicit capture availability records.
- `EvidenceRecorder`: implements public evidence sinks, durably appends execution facts, builds bundles, and atomically checkpoints manifests. Existing `record_event`, `record_step_result`, `build_bundle`, and `write_manifest` remain supported. Public `recover_bundle(run_dir: Path) -> EvidenceBundle` reconstructs validated checkpoint/journal facts without execution or source mutation.

Both symbols are exported from `core.evidence` and re-exported from `core` with identical object identity.

## Internal Structure

- `__init__.py`: public exports.
- `_artifact_store.py`: contained unique artifact paths, availability records, and atomic writes.
- `_recorder.py`: journal append, evidence reconstruction, bundle accumulation, and atomic checkpoints.

## Python Architecture

- Architecture level: Level 2 Simple Package.
- Public API: `ArtifactStore` and `EvidenceRecorder`.
- Internal modules: `_artifact_store.py` and `_recorder.py`.
- Domain boundaries: safe artifact/evidence persistence only.
- Boundary models: evidence and artifact records come from `models`.
- Dependency direction: Runner and Execution consume Evidence; Evidence depends only on shared models and public interfaces.
- Rationale: persistence is focused and run-local, so no repository or Unit of Work is warranted.

## Error Handling

All paths remain contained under the explicit Run directory, including resolved symlinks. IO/serialization failures do not leak secrets, replace a valid checkpoint, or acknowledge unpersisted facts. Recovery preserves validated records before an incomplete trailing write and reports truncation. Malformed complete records, conflicting sequences, and inconsistent identities are integrity errors, not silently discarded facts. Missing, unreadable, truncated, omitted, not-applicable, and captured artifacts remain distinct; unknown values are null with reasons.

## Current Invariants

- Artifact paths are Run-relative in persisted contracts.
- Structured Web locators are atomic executable data: credential-shaped words alone do not rewrite their strings. A locator containing a configured private value is omitted rather than rewritten, with an explicit unavailable reason on observation elements. Other evidence sanitization is unchanged.
- Callers do not manually construct artifact storage paths.
- Evidence facts remain distinct from transport progress projection and generated Case recording.
- `evidence-events.jsonl` stores `fsq.evidence-event/v1` records with strictly increasing Run-local sequence and stable event identity, separately from Agent progress. Step starts, phases, action results, artifact outcomes, and completion are durably acknowledged as they occur rather than buffered until the Run completes.
- Atomic `evidence-manifest.json` uses `fsq.evidence/v2` with checkpoint sequence/completeness; recovery exposes partial Runs. Core evidence `1.0` and the supported unversioned dynamic shape remain readable without migration or invented facts.
- Artifact identity combines kind, execution identity, phase, and capture occurrence; repeated invocations/attempts never overwrite evidence. Integrity metadata describes safe persisted bytes, never private-value digests.
- Completeness follows required capture outcomes and acknowledged execution facts, independently of action/verification verdicts. Execution exposes recovery through its read-only boundary; Report receives a normalized Models bundle without importing Core or replaying the journal independently.
