# Module: execution

## Purpose

Coordinate complete dynamic and deterministic Case execution independently of CLI or HTTP transports. The module owns operation-level ordering, Workspace-wide Run identity allocation, Run lifecycle and ownership, frozen execution conclusions, safe source provenance, Case lifecycle semantics, and conversion of normalized execution facts into Run-local candidate Case recordings. It does not own shared model definitions, transport state, Case parsing, capability execution, platform automation, provider construction, evidence storage formats, report formats, or historical Run queries.

## Dependencies

- `agent`: Runs SDK-neutral dynamic Goal/reference tasks and returns normalized task results and events.
- `case_dsl`: Loads, validates, normalizes, serializes, and adapts Cases through public Case DSL contracts.
- `core`: Executes canonical steps, records evidence, resolves runtime secrets, and supplies harness interfaces.
- `models`: Owns shared task, Case, Run metadata, lifecycle, provenance, runner, evidence, event, and result contracts.
- `config`: Supplies validated lifecycle settings and contained Workspace Case paths.
- `report`: Generates reports from normalized execution facts.

Execution may receive provider-backed evaluators, registries, harnesses, cancellation callbacks, event sinks, and factories through explicit inputs. It must not import `adapters`, `application`, CLI/HTTP frameworks, concrete private drivers, or adapter-private modules.

## Public Interface

The package exports its supported services and result contracts through `execution.__init__`:

- `RunLifecycleService`: Shared owner heartbeat and read-only liveness inspection, source snapshots, immutable execution-result freeze, processing finalization, and lineage append. Its `read_evidence` facade delegates journal/checkpoint recovery to public `EvidenceRecorder.recover_bundle`; Execution does not duplicate Core Evidence recovery.
- `DynamicExecutionService`: Allocates one Run, coordinates a dynamic Goal/reference task through `run_in_context(task, context, event_sink=None, *, evidence_sink, cancellation_check=None) -> DynamicAgentOutcome`, freezes outcomes, generates reports, performs optional recording, and finalizes metadata. The context and outcome are Models-owned; the injected Agent never owns lifecycle persistence.
- `DeterministicExecutionService`: Coordinates one parsed deterministic Case through the supplied registry, runtime-secret store, harness, Core runners, evidence recorder, cancellation boundary, and report generator.
- `LifecycleExecutionService`: Collects contained nested Cases and executes configuration-level and Case-level start/complete hooks plus the main Case with deterministic ordering and recursion protection.
- `RecordingService`: Converts normalized replayable capability results and safe events into a Run-local candidate through the shared Case DSL validator and serializer, and optionally publishes it to a supplied contained destination. It accepts an optional explicit Case name. `RecordingResult` exposes candidate path, stable Case name, publication outcome, draft state, and safe diagnostics. Mutable recorder state remains private.
- `publish_recorded_case`: Public contained publication boundary used by recording and Application save orchestration. It validates and serializes the candidate with the selected stable name, preserves the source, reuses identical destination bytes, and reports a conflict for differing existing bytes. It never silently replaces a different Case.
- `DynamicExecutionRequest`, `DynamicExecutionResult`, `DeterministicExecutionRequest`, `DeterministicExecutionResult`, `LifecycleExecutionRequest`, `LifecycleExecutionResult`, and `RecordingResult`: immutable execution-boundary contracts with no transport types.
- Run lifecycle operations allocate a collision-resistant Workspace-wide ID, atomically create its direct platform directory, write `fsq.run/v2` metadata before actions, advance monotonic active states, freeze execution facts, and atomically finalize one immutable terminal state. Allocation checks every configured platform and retries a collision at most five times. Existing allocation/load/transition/write exports retain the same lifecycle authority.
- `RunArtifactIndex`, `RunMetadata`, `RunResultSummary`, `RunRuntime`, `RunSource`, and `RunStepCounts` remain compatibility exports of the canonical `models` objects rather than a second model hierarchy.

Public services accept already resolved Workspace/platform settings and explicit collaborators. They return normalized results and safe artifact references; adapters alone map them to CLI output or HTTP/SSE state.

Dynamic execution checks cancellation before allocation, persists required safe source snapshots, advances metadata to `running`, and passes the allocated `RunExecutionContext`, evidence sink, and cancellation boundary to Agent. Agent returns distinct runtime and verification outcomes without allocating a Run, generating reports, or changing metadata. Execution freezes those conclusions before derived processing and finalizes the produced artifact index and processing outcomes. An Agent collaborator without `run_in_context` fails before actions rather than starting a second managed Run.

Cancellation from the asynchronous task or the explicit cancellation callback is recorded as `cancelled` when a Run exists; other execution failures are recorded as `error`. Failure-finalization errors never replace the original exception. Cancellation detected before allocation produces no Run. Metadata allocation/writes fail before invoking the Agent when initial persistence is unavailable; a finalization-only failure remains an infrastructure error with produced evidence preserved.

Execution services are imported from `fsq_agent.execution`. The existing `run_fsq_core_case`, `run_strict_fsq_core_case`, `collect_strict_lifecycle_cases`, and `run_strict_lifecycle_case` compatibility operations share the same execution and evidence authorities. Package-root `_strict_lifecycle` and `_strict_case_recording` compatibility modules are absent.

## Internal Structure

- `__init__.py`: Public execution exports.
- `dynamic.py`: Dynamic Goal/reference coordination and normalized result assembly.
- `deterministic.py`: Strict Case preparation and ordered Core execution coordination.
- `lifecycle.py`: Lifecycle Case collection, contained nested execution, hook ordering, teardown, recursion, shell-hook, and cancellation semantics.
- `recording.py`: Replay-policy-driven candidate Case construction, private mutable recording state, validation, atomic Run-local persistence, and optional contained publication.
- `runs.py`: Public Run allocation, owner/liveness inspection, frozen-result persistence, source provenance, lineage append, and metadata lifecycle boundary.
- Private `_*.py` files may hold shared implementation details and are not imported across package boundaries.

## Python Architecture

- Architecture level: Level 3 Layered Application.
- Public API: execution services, `RunLifecycleService`, compatible Run lifecycle operations, immutable Request/Result contracts, and shared model re-exports through `execution.__init__`.
- Internal modules: private helpers are confined to this package; the four named service modules are public resource boundaries.
- Domain boundaries: Execution owns operation-level orchestration, Run lifecycle, frozen conclusions, owner/liveness facts, and Case lifecycle/recording/provenance policy. Agent owns dynamic planning and verification; Case DSL owns Case syntax; Core owns individual capability execution and evidence mechanics; Application owns historical query; Report owns report rendering; adapters own presentation and task-state transport.
- Boundary models: execution Request/Result records wrap public shared models and safe path/artifact references without Click, HTTP, SSE, or frontend values.
- Dependency direction: adapters and Application may depend on Execution; Execution depends only on inward public APIs and injected collaborators; inward modules never import Execution unless the root architecture diagram explicitly permits it.
- Rationale: complete runs coordinate multiple side-effecting authorities, cancellation, lifecycle phases, evidence, reports, and recording, so Level 3 is warranted without Repository, Unit of Work, Clean Architecture, or DDD layers.

## Error Handling

Validation, path containment, registry resolution, and runtime-secret preflight failures occur before external Case actions. Lifecycle start failures skip remaining start/main work according to lifecycle policy while completion hooks and teardown still run. Cancellation is checked at operation and nested-Case boundaries and is propagated without being converted to success. Recording failures never change the completed dynamic execution status and expose only bounded, secret-safe warnings. Shell output, backend output, runtime-secret values, tracebacks, and hidden model reasoning are not included in public results.

Initial metadata, required source snapshot, or durable step-start failure prevents the dependent external actions and removes only an empty request-created directory. Result-freeze or final metadata failure preserves produced evidence and remains an infrastructure failure. Active status transitions are `preparing` to `running` to `finalizing`; terminal states remain `success`, `failed`, `inconclusive`, `cancelled`, and `error` and cannot be rewritten. User cancellation is preserved across CLI, Control Plane, dynamic, and strict paths, including Windows execution. Required evidence failure remains blocking; report, recording, publication, suggestion, and export failures cannot change a frozen execution verdict. Atomic metadata/result/snapshot writes use same-directory temporary files, flush, `fsync`, and replacement. Historical `fsq.run/v1` remains readable without migration or fabricated v2 facts.

## Run Facts And Finalization

Dynamic and deterministic services share one owner, cancellation, freeze, and finalization boundary. Strict resolves root/nested Cases, hooks, and allowed settings before constructing its inventory. Stable `source_step_id` and occurrence-aware invocation paths identify logical leaves; every actual attempt receives a Run-unique `step_execution_id`, also exposed as compatibility `step_id`. Unattempted leaves have no invented execution IDs. Logical leaf counts distinguish executed, skipped, and unresolved outcomes; attempt counts are separate, and hook containers, dynamic plans, and runtime summaries do not inflate action counts. Unknown counts and durations are null with a reason.

After execution and required verification reach an outcome, Execution atomically writes `fsq.execution-result/v1` in `execution-result.json`, containing separate execution, verification, evidence disposition, normalized counts, and safe failures. Strict verdicts use blocking leaf outcomes and required evidence; Explore retains goal-verification policy and may succeed after recoverable failed attempts while preserving their facts. Fatal execution failure and cancellation retain precedence. Unrequested verification is explicit; zero actions without valid verification is inconclusive, not a fabricated pass.

Initial recording and reports finish, fail, or become explicitly unavailable before terminal `run.json`. `fsq.run/v2` retains identity/source/result/runtime/artifacts and adds the frozen-result reference, evidence, processing, provenance, and lineage summaries. Compatibility `TaskResult.report` remains required and may reference the frozen minimal result when rich reporting fails. Later exports, analyses, or publications are separate artifacts or lineage entries and never rewrite terminal truth.

## Provenance And Ownership

Required root/nested source snapshots and allowlisted effective non-secret settings are captured before actions with producer/schema versions and digests of safe persisted representations. Secret values and their digests are excluded; safe secret names/presence and explicit omission records do not claim byte-identical original content. Effective runtime policy uses `agent_runtime`, including resolved effort, without reintroducing SDK-prefixed settings or refreshing active task policy.

Recording links Run, candidate content, saved/published content, and Strict source through execution identities and content digests. New Case YAML contains no Run provenance; `recording.json` and append-only lineage carry those facts. Existing user `properties.recording` data may be preserved as legacy input. Source filename similarity is never a replay link, and recording validation is not proof of replay success.

A separate owner record contains process identity, process-start identity, and heartbeat. Read-only liveness distinguishes verified alive, confirmed dead, and unknown; stale, absent, inaccessible, or unsupported checks alone do not prove death. Inspection does not modify metadata, resume actions, or finalize Runs.

## Verification Scope

- Dynamic, deterministic, lifecycle, and recording behavior is identical across CLI and Control Plane for equivalent inputs and collaborators.
- Lifecycle verification covers configuration and Case hook ordering, repeated actions, nested Cases, recursion, containment, start failure, completion hooks, teardown, cancellation, and platform shell selection.
- Recording verification covers replay-policy filtering, authored aliases, normalized safe params, browser lifecycle facts, runtime-secret exclusion, validation, atomic Run-local writes, optional publication, and failure isolation.
- Compatibility verification proves canonical and package-root lifecycle symbols share identity and no adapter contains an independent lifecycle engine or recorder. Recording verification exercises the public `RecordingService` boundary rather than importing its private recorder implementation.

## Current Invariants

- Dynamic and deterministic execution semantics are transport-neutral and have one canonical implementation.
- Run query, aggregation, filtering, historical inference, and HTML generation remain outside Execution. All execution entry points use the same Execution-owned Run allocation and metadata lifecycle rather than constructing IDs in adapters.
- Execution is the single writer of authoritative Run lifecycle and frozen conclusions. Agent receives its Run context, retains business progress and verification policy, and never imports Execution or Report. Evidence journaling/checkpoints belong to Core Evidence; reports and transport task state are projections, not lifecycle authorities.
- Lifecycle hooks are metadata around a Case, not synthetic Case commands. Authored order is preserved, nested `runCase` paths remain contained below the selected platform Case root, and recursive chains fail before infinite execution.
- Trailing teardown steps and completion hooks remain eligible after an earlier blocking normal-step failure.
- Recording consumes final normalized capability results rather than low-level progress events as execution truth. It records only replayable non-observation facts allowed by capability metadata and never invents setup, cleanup, or browser lifecycle commands.
- Runtime-secret values, sensitive raw arguments, subprocess output, and hidden reasoning are never persisted into generated Cases or returned in safe execution summaries.
- Adapters depend on `RecordingService` and `RecordingResult`; they do not import, inject, or expose the private mutable recording state or function-style recorder implementation.
- Adapters may supply transport-specific event sinks, cancellation callbacks, and progress recorders, but they do not determine lifecycle order, replayability, evidence policy, or recording content.

## Stable Case Identity And Publication

- Explicit names are safe suffix-free basenames using the same policy for CLI creation and Control Plane save. Without a name, a Goal recording uses `case-` plus the full lowercase SHA-256 of compact UTF-8 JSON `[platform, normalized_goal]`, with Unicode preserved. Goal normalization collapses whitespace as in Application Case creation; identity contains no Run ID, timestamp, random value, or host path.
- Goal descriptions use normalized Goal text with Task name as the blank-reference fallback. Non-Goal recording without an explicit name uses `case-` plus the full lowercase SHA-256 of compact UTF-8 JSON `[platform, task.name, task.description]`, with Unicode preserved, and uses the stable Task description without embedding a Run ID. Existing-source suggestion candidates preserve the source Case identity.
- Both successful and draft recordings use the same Case content rules. `recording.json` retains source Run/Task identity, status, draft state, required secret names, warnings, skipped calls, validation result, stable Case name, and publication outcome; these recording facts are not injected into newly generated YAML.
- Publication outcomes distinguish not requested, created, unchanged, conflict, and failed. Existing identical bytes are a successful no-op. Different contents or a competing different publication leave the destination intact, preserve the candidate, and return `case.publication_conflict`. Conflict does not change the completed execution result. Creation must be atomic and must not clobber a destination created concurrently.
- Candidate validation precedes atomic Run-local persistence. Invalid candidate data is reported in recording diagnostics without publishing invalid YAML. Recording uses safe replay parameters, retains actual action order, and never reconstructs parameters from secret-resolved values.
- Stable names and conflicts apply across CLI publication and explicit Control Plane saves. Explicit save changes only the destination Case name before shared serialization and leaves Run-local candidate bytes unchanged.
