# Module: core.runner

## Purpose

Own platform-neutral capability execution for one canonical step and ordered sequences. Runner applies capability metadata, validation, runtime secrets, evidence policy, measured timing, sensitivity, result normalization, and teardown ordering. It emits durable facts through public sinks without owning filesystem formats, Run lifecycle, transports, or concrete platforms.

## Dependencies

- `models`: canonical steps, capability definitions, events, results, evidence references, and execution settings.
- `capabilities`: resolved neutral declaration metadata only where registry construction requires it.
- `core.interfaces`: harness, capability-executor, observation, runtime-secret, evidence-recorder, and cancellation boundaries.

Runner must not import adapters, Application, Agent SDK types, concrete harnesses, concrete drivers, or report renderers.

## Public Interface

- `StepRunner`: executes one capability invocation and publishes step start, phase boundaries, action result, artifact outcomes, and final result to the supplied evidence sink as they occur. `run_step`, `events`, and `last_capability_execution_result` remain supported; buffered events are a compatibility projection, not durable authority.
- `StepSequenceRunner`: executes ordered normal steps, stops on blocking failure, records explicit unexecuted-leaf outcomes, and preserves supplied teardown eligibility. `run_steps` remains supported.

Both symbols are exported from `core.runner` and re-exported from `core` with identical object identity.

## Internal Structure

- `__init__.py`: public exports.
- `_runner.py`: single-step metadata-driven execution.
- `_sequence.py`: ordered sequence and teardown coordination.

## Python Architecture

- Architecture level: Level 3 Layered Application.
- Public API: `StepRunner` and `StepSequenceRunner`.
- Internal modules: `_runner.py` and `_sequence.py`.
- Domain boundaries: capability execution policy and ordering only.
- Boundary models: public execution models come from `models`; collaborator protocols come from `core.interfaces`.
- Dependency direction: Execution depends on Runner; Runner depends on Interfaces; concrete implementations point inward to Interfaces.
- Rationale: execution coordinates validation, secrets, timing, evidence, and side effects, requiring Level 3 without a domain framework.

## Error Handling

Runner normalizes prepare/invoke/settle/finalize/capture failures into safe facts, preserves cancellation, and never exposes secrets. Registry, parameter, unresolved-secret, and durable step-start failures occur before the protected external invocation. Required evidence persistence failures block dependent work while retaining acknowledged facts.

Action failure remains the primary failure. Capture/persistence errors are separate evidence errors and cannot overwrite action category, message, or assertion verdict. Required missing evidence remains fail-closed; evidence-only failure uses compatibility `artifact_error`. Screenshot and UI snapshot outcomes remain independently visible. Teardown eligibility survives earlier failure or cancellation according to execution policy; interrupted unacknowledged results remain unknown rather than fabricated outcomes.

## Current Invariants

- Capability metadata, not action-name branches, controls routing, replay metadata, timing, sensitivity, and evidence policy.
- Automatic evidence depends on step kind and normalized observation interfaces.
- Every attempt carries stable `source_step_id`, occurrence-aware invocation path, and Run-unique `step_execution_id`; compatibility result `step_id` aliases execution identity. Planned logical leaves retain executed/skipped/unresolved outcomes and reasons, including known blockers. Attempts are counted separately from logical leaves; hooks, plans, and runtime summaries do not inflate capability counts.
- Step/phase boundaries use measured UTC timestamps and monotonic durations. Exclusive prepare/invoke/settle/finalize times do not overlap; nested capture times are not counted twice. Unknown measurements are null with a reason, not zero.
- Positive post-action delay is the measured settle phase after invocation and before final evidence, preserving configured-delay metadata without synthetic wait commands or replay results. Runner adds no automatic retries; externally requested attempts retain separate identities and outcomes.
- Teardown steps remain eligible after normal-step failure.
