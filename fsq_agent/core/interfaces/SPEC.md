# Module: core.interfaces

## Purpose

Own public platform-neutral protocols and stable construction boundaries used by Execution, Runner, Agent, harnesses, and drivers. Interfaces invert platform and external-system dependencies without owning concrete automation behavior.

## Dependencies

- `models`: canonical invocation, result, observation, configuration, capability, and artifact boundary models.

Protocol-definition modules depend on Models only and must not import adapters, Application, SDK types, concrete harnesses/drivers, backend libraries, or sibling Core implementations.

The named composition exception is `_factories.py`: the stable `DriverFactory` and `HarnessFactory` wrappers may lazily import `drivers._factory._DriverFactoryImplementation` and `harnesses._factory._HarnessFactoryImplementation`, with public `core.evidence.ArtifactStore` used for annotation. Concrete implementations depend on protocol modules and never this wrapper. The exception preserves zero-argument construction and export identity without connecting a backend; new selectors or ownership changes require a separate boundary decision.

## Public Interface

The package exports the approved protocols and factories used across module boundaries:

- `HarnessInterface`, `DriverObservationInterface`, and `AIAssertionEvaluatorProtocol`.
- `HarnessInterface.close() -> None`, `DriverObservationInterface.close() -> None`, and `AIAssertionEvaluatorProtocol.close() -> None` release resources owned by the instance without executing a recordable capability. Closing is synchronous, idempotent after successful disposal, and does not initialize resources that have never been opened. Concrete platform protocols inherit the Driver disposal contract; borrowed-resource wrappers retain ownership of the resources they borrow.
- `AndroidDriverInterface`, `WebDriverInterface`, `WindowsDriverInterface`, and `MacOSDriverInterface`.
- `CapabilityRegistryInterface`, `RuntimeSecretResolver`, `CancellationCheck`, and `EvidenceSink`; existing event/result/bundle methods remain supported.
- `EvidenceJournalSink`: synchronous durable acknowledgement of incremental step, phase, action-result, artifact-outcome, and completion facts using Models values. Failure cannot be represented as successful acknowledgement; Core Evidence owns persistence and recovery.
- `DriverFactory` and `HarnessFactory` as stable composition boundaries with private selection implementations.

`core` and the existing `core.harness` compatibility surface re-export these same objects. Concrete harness and backend driver classes are not public.

## Internal Structure

- `__init__.py`: public protocol and factory exports.
- Private protocol modules group execution, observation, driver, harness, secret, cancellation, and evidence boundaries without platform implementation code.

## Python Architecture

- Architecture level: Level 2 Simple Package.
- Public API: named protocols and approved factories exported through `__init__.py`.
- Internal modules: private protocol definitions and factory implementation forwarding.
- Domain boundaries: interface definitions and stable construction contracts only.
- Boundary models: shared values come from `models`; no duplicate DTO hierarchy is introduced.
- Dependency direction: Runner, Evidence, Execution, Agent, Harnesses, and Drivers consume the Models-only protocols; the separate factory wrapper uses only its named selectors and annotation exception. Concrete implementations never depend on the wrapper.
- Rationale: focused protocols provide dependency inversion; Clean Architecture or a DI container would add no value.

## Error Handling

Protocols preserve normalized safe failure and cancellation contracts. Optional backend absence must remain a runtime unsupported/unavailable outcome rather than an import-time failure.

Disposal failure is explicit. Callers attempt all owned cleanup, preserve any primary execution failure or cancellation, and report cleanup-only failure instead of claiming complete disposal. Cleanup is not a UI action, does not produce replay commands, and cannot change persisted execution evidence or undo performed actions.

## Current Invariants

- Public interfaces expose no concrete platform/backend types.
- Factory selection remains lazy for optional backend dependencies.
- Re-exports preserve exact class/protocol identity and do not duplicate mutable state.
- No service locator or dependency-injection container exists.
- Journal contracts carry identities, timing, safe outcomes, and artifact availability without SDK events, persistence implementation, or report structures. Serialized `RunExecutionContext` contains allocated safe values; sinks, callbacks, identity allocators, and owner operations remain explicit live collaborators. Presentation cannot manufacture durable acknowledgements or replace action outcomes.
