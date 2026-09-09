# Module: application

## Purpose

Provide the shared, transport-neutral Application layer used by the FSQ CLI, Control Plane, and future Coding Agent APIs. The package exposes application operations grouped by Workspace, Case, Run, Provider, and Environment and coordinates existing module authorities without reimplementing their rules.

## Dependencies

Application may consume public APIs from `models`, `config`, `providers`, `ai_services`, `agent`, `execution`, `case_dsl`, `environments`, `core`, and `report`. It must not import adapters, SDK objects, frontend code, Click, HTTP/SSE frameworks, terminal rendering, or concrete UI types. Lower-level modules must not import `application`.

## Public Interface

The package exports transport-neutral operations and their Request, Result, Event, and Error contracts through `__init__.py`. The same symbols are available from their canonical resource modules so callers may depend on the narrow boundary they use. Operations are organized by resource domain rather than exposed through one generic `execute(command)` facade:

- Workspace operations support the shared workspace precondition, platform target resolution, read-only runtime readiness coordination, and workspace initialization needed by adapters.
- Case operations support creating a Case from a Goal, testing an existing Case with optional suggestions, static formatting, and saving generated recordings.
- Run operations support exact-Workspace multi-platform listing, stable detail lookup, safe structured log retrieval, historical inference, and on-demand static HTML generation.
- Provider operations support user-level OpenAI model discovery/configuration, Azure OpenAI configuration, GitHub Copilot device authorization/model activation, and active-Provider readiness status. Supplier model discovery does not expose a Provider profile inventory.
- Environment operations support listing and diagnostics.
- Doctor supports complete read-only Workspace diagnosis and per-platform command readiness.

Requests contain application inputs, Results contain operation outcomes and safe artifact references, Events describe transport-neutral progress, and Errors contain stable codes plus safe structured details. These contracts contain no Click, HTTP, SSE, terminal, or frontend types. `application.contracts` is the canonical owner of these types and groups them by shared, Workspace, Case, Run, Provider, and Environment concerns. Resource operation modules import those canonical contract objects rather than defining transport-specific or duplicate equivalents.

Canonical resource modules are:

- `application.workspace`: Workspace operations.
- `application.cases`: Case creation, testing, formatting, and generated-recording save operations.
- `application.runs`: persisted Run query and log operations.
- `application.providers`: Provider operations.
- `application.environments`: Environment operations.
- `application.doctor`: Workspace-level diagnostic orchestration.

`application.runs` owns Workspace-scoped Run query orchestration. It validates the exact registered root through Config, resolves trustworthy configured-platform inventory, aggregates platform Run roots, detects duplicate IDs, applies filters/order/limits, parses current metadata or bounded historical facts, sanitizes logs, and coordinates Report HTML generation. It does not allocate IDs, persist lifecycle metadata, render transport output, or open a browser.

Canonical immutable Pydantic contracts under `application.contracts.runs` include list/show/log/HTML requests and results, `RunSummary`, `RunDetail`, `RunLogEvent`, normalized filters, and Execution-facing Run metadata values. List results contain Workspace identity, queried platforms, filters, matched/returned counts, truncation, entries, and warnings. Show returns safe summary and relative artifact references without report or log bodies. Logs return completely validated safe events and selection metadata. HTML generation returns Run identity, platform, relative path, and generation status.

New `run.json` uses schema `fsq.run/v1` and validates Workspace name, platform, Run ID, lifecycle status, UTC timestamps, bounded Case/Goal source, safe result/step/runtime summary, and contained relative artifact references. Historical Run directories without it are inferred read-only from supported report, fallback, evidence, and event artifacts. List isolates a damaged direct-child Run as an error entry; show permits safe partial history but rejects untrustworthy identity; logs may be read independently when Run containment and the complete log are trustworthy. Query never writes inferred metadata.

## Ownership Boundaries

Application owns cross-module orchestration, shared request validation, workspace enforcement, operation-level event production, and consistent results/errors for all adapters. It delegates authoritative behavior to existing modules:

- `agent` owns AI planning, model/tool orchestration, and dynamic verification.
- `ai_services` owns visual assertion and read-only Case suggestion policy, service factories, and suggestion readiness; Application composes these services without SDK/model protocol knowledge.
- `providers` owns supplier authentication/configured access and readiness; it does not own assertion or suggestion business logic.
- `execution` owns complete dynamic/deterministic run coordination, Case lifecycle semantics, cancellation/teardown ordering, and candidate Case recording.
- `case_dsl` owns Case parsing, static validation, normalization, canonical serialization, and deterministic-step adaptation.
- `environments` owns host support, read-only runtime readiness, and Web executable discovery.
- `core` owns capability execution, runtime-secret handling, evidence policy, and Harness/Driver routing.
- `report` owns transformation of persisted execution facts into reports and failure analysis.
- concrete drivers own platform automation and backend-error normalization.

Application must not copy, reinterpret, or fork those rules. This specification does not require `case create`, `case test`, and suggestion handling to be three independent internal Use Cases.

Goal-based Case creation requests Run-local recording and supplies the selected platform Case directory as the optional publication destination. An optional `case_name` selects the stable identity; otherwise Execution derives it from platform and normalized Goal. A validated successful recording is published there as `<case-name>.fsq.yaml` with conflict-safe publication. The result exposes the authoritative Run-local candidate, stable name, published path, publication outcome, and safe warnings, including publication conflicts. Recording or publication failure does not replace the completed dynamic execution result.

Case testing always performs one deterministic Execution run. When suggestion is requested, Application invokes a separate post-execution analysis through an injected read-only suggestion collaborator using the parsed source Case and bounded persisted execution facts. The collaborator receives no Harness, Driver, capability registry, or action executor, cannot rerun the Case, and cannot change the completed Run result. Application returns only Run-local suggestion and optional candidate paths produced beneath the completed Run directory; the source Case and configured Case directory remain unchanged. Suggestion-analysis failure uses stable error code `case.suggestion_failed`, preserves the completed report path in safe error details, and does not rewrite or conceal the completed deterministic execution facts.

Workspace initialization accepts a selected current directory, optional workspace name, one platform's target inputs/environment, and controlled-update intent. Application resolves the name case-insensitively through Config's registry. For an unregistered name it delegates final-root selection to Config: an empty selected directory is adopted as the root, while a non-empty selected directory receives an absent `<selected-directory>/<workspace-name>` child. For a registered name it ignores the selected directory for persistence and uses the immutable stored root; an unavailable registered name is not recreated. Before any workspace mutation Application validates the request, resolves the complete target, and asks the platform runtime service to check readiness without installing software. Web target resolution requires an explicit channel and either validates the explicit executable path or discovers exactly one compatible host executable. Application delegates filesystem validation, root selection, platform persistence, registry mutation, idempotency, revision handling, and rollback to Config only after these prerequisites succeed, then returns committed workspace name/root/platform/status plus safe readiness/discovery facts needed by CLI or Control Plane presentation. The shared workspace precondition for non-init commands resolves the exact current directory through Config registry and Workspace truth; a marker directory alone never satisfies it.

Application also exposes transport-neutral workspace create, add-platform, and update-platform operations used by Control Plane. Create accepts a selected directory, name, and complete platform inputs; add and update accept the identity and revision fields required by that mutation. Each operation resolves every target, completes readiness, and only then calls Config persistence. CLI and Control Plane do not call Config workspace mutation operations directly.

Provider operations do not accept or resolve a Workspace. They coordinate the existing public Config and Providers APIs against the user Config root also used by Control Plane and never use process environment, Workspace `.env`, or platform configuration as Provider authority. Azure configuration accepts a complete endpoint, model/deployment name, and API key candidate and delegates validation plus atomic replacement to Config. GitHub configuration exposes transport-neutral device-code request, cancellable completion, eligible-model discovery, and authorization activation operations so an adapter can present and select without receiving persistence authority. Application validates that the selected model came from that authorization's discovered eligible set before activation.

`list_openai_models(*, api_key: str)` and `configure_openai(*, model: str, api_key: str, user_config_root: str | Path | None = None)` are public Provider-resource and package exports. Listing delegates to Providers and returns its immutable safe `OpenAIModel` facts, including an empty tuple when no eligible models are visible; it never persists or sends inference. Configuration re-runs discovery using the complete candidate key and requires exact selected-id membership in that current set before calling `save_openai_provider`. An empty set or an absent id is a `model_not_offered` rejection before persistence. Successful configuration returns the existing safe `ProviderConfigurationResult`. Provider-owned discovery facts are reused rather than redefined as Application or transport-specific model classes.

Provider replacement commits a complete candidate before obsolete credentials are removed. Validation, authentication, discovery, selection, or cancellation before activation and confirmed persistence rollback preserve the previous active Provider and credentials. Lost responses or unexpected post-commit failures do not imply cancellation or rollback. Reading configuration establishes current persisted truth, not the terminal outcome of a disconnected request. Results contain only safe Provider type, model, configuration/readiness state, offered model facts, device verification facts where required before activation, and stable safe errors; credentials and raw backend values never enter Application result/error contracts.

The Provider status operation loads the latest user-level Provider snapshot, reports the explicit unconfigured state without manufacturing a default, and delegates non-interactive session readiness to Providers. It may use the documented cached GitHub token refresh but never starts device flow, prompts, sends model inference, or requires a Workspace. Its immutable result contains `status` (`ready` or `unavailable`), `configured`, optional `provider` and `model`, `authenticated`, a safe message, and an optional safe repair action. Expected unavailable states are results rather than exceptions; malformed persisted configuration and unrecoverable orchestration failures remain stable safe Application Errors. Exception messages, tracebacks, API keys, tokens, authorization objects, and raw provider responses are never returned.

Target resolution finishes before readiness check or Config mutation. It rejects cross-platform fields and missing required values; normalizes and validates explicit local paths for existence, regular-file shape, executable eligibility where applicable, and exact Web channel compatibility; and resolves an omitted Web executable only when discovery returns exactly one normalized candidate. Zero or ambiguous candidates are configuration errors and cause no runtime or persistence side effects.
For an explicit Web executable, exact compatibility uses Core's component-aware path identity contract rather than discovery membership or generic substring matching. A non-standard installation root is accepted when the normalized basename and directory or application-bundle components prove the selected product and channel. An ambiguous shared basename without the required channel identity is rejected with safe guidance to omit the path for discovery or provide a channel-identified path.

Application owns the transport-neutral workspace initialization, platform readiness check, and Web executable discovery use cases shared by CLI and Control Plane. It does not implement or invoke package-manager commands, filesystem registry formats, browser path tables, ADB/Appium/backend protocols, or transport wording. Platform runtime services own read-only platform-specific detection; Config owns target validation and persistence. CLI and Control Plane decode inputs and project Application results/errors without reproducing this orchestration.

Application's Doctor operation accepts the exact current directory and returns immutable `DoctorResult`, `DoctorWorkspaceSummary`, `DoctorPlatformResult`, fixed `DoctorChecks`, fixed `DoctorCommands`, `DoctorPrerequisite`, and `DoctorStatusDetail` contracts. Detail and prerequisite status is `ready`, `unavailable`, `error`, or `not_applicable`; platform and overall status is `ready`, `partial`, or `unavailable`. Each platform result contains an ordered prerequisite tuple, empty when the platform exposes no individual prerequisite details. Platforms are diagnosed in Android, Web, Windows, macOS order and only identifiable configured platforms are returned. An identifiable damaged platform produces a configuration error detail without aborting other platforms; an untrustworthy registry, root mapping, or platform inventory raises a Workspace/configuration Application Error.

Doctor delegates component facts through public Config, Environments, Providers, AI Services, Agent, and Core boundaries, isolates unexpected component exceptions into safe error details, derives command verdicts from a fixed dependency matrix, and returns ordered exact-deduplicated actions. Ordinary `case test` requires configuration, Runtime, Target configuration/availability, and Strict Core readiness. `case test --suggest` additionally requires Provider and AI Services suggestion-analyzer readiness. `case create` additionally requires Provider and dynamic-Agent readiness. Doctor does not inspect a particular Case and therefore does not promise readiness for Case-specific syntax, runtime-secret, nested-Case, or `assertWithAI` requirements.

For Android and macOS, Doctor projects Environments-owned prerequisite facts without re-running host commands or interpreting backend output. The existing `target_configuration` and `target_availability` details summarize prerequisite readiness for command dependency evaluation, while the prerequisite tuple explains each independent or blocked host requirement. Actions from prerequisite details participate in the existing ordered exact-deduplicated action list. Stable prerequisite codes are preserved across Human and machine projections.

Doctor requests Config inspection with target-path validation deferred to Environments. A missing or unusable application does not prevent independent host prerequisites from being reported; malformed or identity-mismatched platform documents remain configuration errors and cannot authorize target inspection.

### Registered-platform diagnosis

`diagnose_registered_platform` and its immutable `RegisteredPlatformDoctorRequest` are public Application exports for explicitly selected registered Workspace diagnosis. The request supplies a Workspace name and platform; an optional user-config root is a trusted composition input and is not accepted from browser requests. Application resolves the registered root through Config and returns a `DoctorResult` containing only the requested platform. It shares component checks, ordered prerequisite facts, command dependency rules, and safe errors with CLI Doctor. CLI Doctor retains its exact-current-root, all-configured-platform behavior.

The public `diagnose_platform_settings` operation diagnoses already resolved settings and returns a `DoctorPlatformResult`; registered-platform diagnosis and CLI Doctor use this same implementation after establishing trustworthy configuration. Control Plane macOS run preparation uses it on the settings frozen for that execution attempt. Explore requires the `case_create` verdict; Strict requires `case_test` plus Provider readiness only when the parsed Case requires AI assertions. A browser's earlier ready response is not reusable start authority. Failure prevents Run allocation, model execution, Driver construction, and UI actions.

`DoctorPrerequisite` projects the explicit, default-empty `commands` tuple from Environments facts. Application does not extract commands from explanatory prose or execute remediation. Workspace diagnosis preserves independent check results and safe repair eligibility without returning private configuration values.

### Android selected-device diagnosis

`RegisteredPlatformDoctorRequest` accepts an optional Android-only `target_id` as a transient exact serial. It rejects that field for other platforms, never writes it to Workspace/configuration, and applies it only to a private resolved settings copy. CLI Doctor keeps its all-platform, no-device-selection contract: one online authorized device is unambiguous; multiple online devices produce actionable selection-required diagnosis without inventing a persisted serial setting.

Android platform results expose the effective selected `target_id` (or null) solely to bind device-specific diagnosis to selection. Supplied-but-missing devices do not resolve to another device. The shared readiness dependency matrix is unchanged: Explore requires Provider/dynamic-agent readiness, provider-free Strict does not, and parsed Strict AI assertions additionally require Provider readiness.

Control Plane startup diagnoses the exact Android settings copy after applying the requested device and resolving the effective application identity using the same precedence as execution (Workspace app ID, then supported Case metadata fallback). Existing Strict nested/lifecycle semantics remain unchanged. Installed-app checks use that effective run app rather than a different inferred target. Startup failure occurs before Run allocation, model execution, Driver construction or UI actions. Discovery does not imply app availability. The same diagnosis operation used by CLI provides these prerequisite facts; Application adds no ADB protocol implementation.

## Python Architecture

- Architecture level: Level 3 Layered Application.
- Public API: resource-grouped operations and transport-neutral Request, Result, Event, and Error contracts exported from `__init__.py`.
- Dependency direction: CLI and Control Plane adapters depend on Application; Application depends on owning module public APIs; owning modules do not depend on Application.
- Rationale: the package coordinates several existing authorities and presents one consistent application boundary to multiple transports without introducing repositories, a database, a daemon, a queue, or Clean Architecture ceremony.

## Internal Structure

- `__init__.py`: Complete convenience exports for the public Application API.
- `contracts/`: Canonical transport-neutral Request, Result, Event, Error, summary, and machine-record contracts grouped by resource concern.
- `workspace.py`: Public Workspace operation boundary and private Workspace orchestration helpers.
- `cases.py`: Public Case creation, testing, formatting, and generated-recording save boundary.
- `_case_format.py`: Static Case file orchestration and atomic conditional formatting writes.
- `runs.py`: Public persisted Run query/log boundary.
- `providers.py`: Public Provider operation boundary.
- `environments.py`: Public Environment operation boundary.
- `doctor.py`: Public Workspace Doctor operation and aggregation boundary.
- Private `_*.py` files may support these public modules but are not imported across package boundaries.

## Error Handling

Application normalizes expected operation failures into stable application Errors and preserves safe structured details. It never exposes secrets, hidden model reasoning, backend objects, transport status codes, or tracebacks. Adapters map Application Errors to exit codes, HTTP statuses, and presentation text.

OpenAI operations preserve `details.provider="openai"` and a validated `details.reason` through the public `ApplicationError` boundary. Invalid candidates and `model_not_offered` use configuration errors; authentication, access denial, rate limiting, timeout, network failure, and malformed supplier responses use Provider-unavailable errors; confirmed no-activation/rollback storage failures use configuration errors. Their reason values are `invalid_candidate`, `model_not_offered`, `authentication`, `access_denied`, `rate_limited`, `timeout`, `network`, `malformed_response`, and `storage`. Unexpected or unconfirmed persistence outcomes use internal errors, with safe reason `internal`. Classification uses owned typed/status facts rather than exception-message parsing. Error details are allowlisted, not copied from backend context, and contain no credentials, upstream bodies, HTTP status, or exception causes. Control Plane owns the corresponding HTTP/code mapping; CLI owns the documented exit mapping. Existing Azure and GitHub classifications retain their behavior.

Doctor component failures do not expose exception messages, arguments, tracebacks, raw subprocess/backend output, env values, or credentials and do not abort independent checks. Only an untrustworthy Workspace identity/inventory or an unrecoverable top-level orchestration failure prevents a complete result.

## Current Invariants

- CLI and Control Plane business operations pass through Application.
- CLI and Control Plane use the same Config-owned registered workspace identity and `.fsq` layout; Application contains no legacy marker-based workspace authority.
- Shared runtime readiness and Web executable discovery flow through Application; adapters and Application do not execute installers or maintain browser discovery tables.
- All CLI and Control Plane workspace mutations flow through Application; Config remains the persistence and transaction owner.
- Application is a real Python package and an architectural layer, not a documentation-only label.
- There is no generic command-string facade.
- Application contracts have one canonical definition under `application.contracts`; package-root and resource-module exports reference the same objects.
- Resource modules contain the authoritative implementation for their operation group; compatibility exports do not copy behavior or state.
- Run queries are read-only except for explicitly requested derived `report.html`; they never execute, authenticate, invoke Providers/Drivers, or rewrite authoritative metadata or results.
- Transport concerns remain in adapters.
- Domain and runtime rules remain in their owning modules.
- Case operations coordinate through public Execution services and do not import package-root or adapter-private execution helpers.
- Suggestion-enabled Case testing separates deterministic execution from read-only post-execution AI analysis; the analysis has no UI-action authority and all generated artifacts remain inside the completed Run directory.
- Doctor is a read-only Application use case; CLI presents its result but does not reproduce diagnostic or command-readiness rules.
- Provider configuration and status are user-level Application use cases shared in persistence authority with Control Plane, require no Workspace, and never recover Provider state from `.env` or process environment.
- The Provider boundary has one active Provider and no profile inventory, retained profiles, fallback chain, or transport-specific UI models. OpenAI and GitHub model discovery enumerate eligible model facts only and do not activate a Provider.

## Static Case Formatting And Generated Save

`format_case`, `CaseFormatRequest`, `CaseFormatResult`, and `CaseFormatDiagnostic` are public Application contracts exported through the Case resource and package entries. Requests identify one explicit file path, its current-directory base, and check/diff/write mode. No Workspace registration, platform configuration, Provider readiness, credential resolution, or external runtime construction is required. Platform comes from Case metadata and selects the declarative capability registry, including AI assertion schemas without constructing an evaluator.

The operation delegates content validation and canonical bytes to Case DSL. Cross-file lifecycle resolution and runtime checks are outside its scope and are identified as such in results. Format does not rename files, strip recording metadata, or migrate historical files. Invalid data yields field-addressable safe diagnostics and no write. Successful writes replace atomically only after validating all data, preserve file permission bits, avoid a write when bytes match, and fail if the source changed since it was read rather than knowingly overwriting concurrent edits.

Results include path, mode, `valid`, `formatted`, `changed`, `needs_formatting`, diagnostics, and optional unified diff. `valid` describes static validity; `formatted` describes whether the resulting on-disk file is canonical; `changed` means this invocation actually wrote different bytes; `needs_formatting` describes the original input. Read-only noncanonical input has valid=true, formatted=false, changed=false. Successful normalization writes have valid=true, formatted=true, changed=true. Invalid input has valid=false, formatted=false, changed=false. Diagnostic fields are stable code, safe message, file, optional zero-based command index, and field path; raw rejected values are excluded.

`save_recorded_case`, `CaseSaveRequest`, and `CaseSaveResult` coordinate the supplied frozen candidate/destination/platform/name through Execution's public contained publication boundary. Control Plane owns terminal-run authorization and frozen input selection; Application and Execution own saving semantics.

Suggestion candidates pass shared static validation and canonical serialization before persistence, preserve the parsed source identity and description, and never gain Run provenance in their YAML. Invalid candidates remain unavailable with safe diagnostics; the completed execution result and source bytes are preserved.
