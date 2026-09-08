# Module: agent_engine

## Purpose

Provide SDK-neutral model access and agent execution through independently reusable contracts. The private backend uses OpenAI Agents SDK for both tool-free model requests and agent tool loops. The module owns inference protocol conversion and resource cleanup, not supplier authentication, credential refresh, model discovery, filesystem policy, or FSQ business judgments.

## Dependencies

- Python standard library for protocols, dataclasses, asynchronous lifecycle, and JSON values.
- OpenAI Agents SDK and OpenAI Python are private backend dependencies loaded lazily.
- Existing general-purpose Pydantic validation exceptions may be normalized by the backend; caller-owned output parsers supply business validation.
- No dependencies on other FSQ packages, including type-only imports, settings, tasks, business models, providers, tools, Core, or adapters.

## Public Interface

Public exports from `__init__.py`:

- `Model`: `async complete(request: ModelRequest) -> ModelResult` performs one logical tool-free model request.
- `ModelProvider`: `get_model(model_name: str) -> Model` supplies an explicitly named model; `async aclose() -> None` closes owned model/client resources.
- `AgentEngine`: `async run(model: Model, request: AgentRequest, *, on_event: AgentEventSink | None = None) -> AgentResult` executes one agent task, including model/tool continuation.
- `create_model_provider`: creates a provider from explicit endpoint, credential, and optional request headers without inference or authentication operations.
- `create_agent_engine`: creates the private SDK engine through the public protocol.
- `JsonValue`, `TextContent`, `ImageContent`, `Message`, `ModelRequest`, `ModelResult`, and `TokenUsage`: neutral input/output values. Images contain bytes and MIME type; the engine does not read image files.
- `ToolCall`, `ToolInputFailure`, `ToolBinding`, and `OutputContract`: neutral tool identity/JSON-object arguments, safe invalid-input facts, tool schema/callback binding, and named output schema with validation/parser callback.
- `AgentRequest`, `AgentResult`, `AgentEvent`, and `AgentEventSink`: execution input, validated output, semantic events, and asynchronous event delivery.
- `ToolOutputEntry`, `ToolOutputFilter`, and `ToolOutputTrimSettings`: ordered tool-output views, restricted replacements, and user-turn trimming configuration.
- `EngineError`: safe category, message, and optional HTTP status or supported failure detail; no raw SDK exception contract.

Provider factories are the public construction boundary; concrete SDK classes are not exported. Public imports do not initialize or import SDK implementations and do not require credentials. Public values do not contain SDK objects, arbitrary vendor-request kwargs, or raw response/transcript dictionaries. Factories and boundary records are intentional public APIs required by callers to construct requests and supply resources; no registry or dependency-injection container is used.

`ModelRequest` supplies optional instructions and text or ordered neutral messages. `ModelResult` contains extracted text, optional token usage, and best-available completion information. Unknown finish information and unavailable usage remain unknown or absent, not a fabricated successful finish or precise zero measurement.

`AgentRequest` supplies name, instructions, input, tool bindings, an optional output contract, a turn limit, streaming choice, tracing choice, and optional trimming/filter configuration. `AgentResult` contains final output validated by the supplied contract and available usage. Neither result declares a business test passed.

Tool callbacks receive `ToolCall` with call ID, name, and JSON-object arguments and asynchronously return model-facing text, including caller-formatted JSON. A tool's schema constraint is separate from local business validation. Output contracts carry a caller-owned schema and parser; required constrained output is not silently downgraded to best-effort text parsing.

`ToolBinding.on_invalid_input` optionally receives `ToolInputFailure` with tool name, call ID, and a safe validation message, and returns model-facing failure text. Malformed JSON or a non-object argument never invokes the normal execution callback. Without a supplied error formatter the engine returns a generic JSON error result and allows SDK continuation; it does not invent FSQ result metadata or retry the tool. Invalid raw input and SDK context are not passed to the failure formatter.

## Internal Structure

- `__init__.py`: public exports and lazy factories.
- `_contracts.py`: protocols, boundary values, and errors.
- `_openai_backend.py`: provider/model lifecycle, tool-free SDK model invocation, agent construction/execution, output validation, and error normalization.
- `_conversion.py`: private SDK input, output, tool, usage, and semantic event conversion.
- `_context.py`: private SDK trimming and neutral tool-output filter bridge.

## Python Architecture

- Architecture level: Level 2 Simple Package.
- Public API: the protocols, factories, errors, and boundary records listed above.
- Internal modules: `_*.py` files are private to this package.
- Domain boundaries: generic inference and agent execution only; business prompts, files, authentication, supplier selection, and result interpretation are external.
- Boundary models: locally owned neutral dataclasses, JSON types, protocols, and generic validation callbacks.
- Dependency direction: other FSQ packages consume this public API; the engine does not depend on them. SDK imports stay inside private implementation modules.
- Rationale: model access and execution need separate contracts but no persistence, service/repository layering, or plugin framework.

## Error Handling

Missing SDK dependencies become a neutral configuration error when backend construction/use requires them, not a failure to import public contracts. Authentication, rate limiting, availability, timeout, invalid output, supported refusal/incomplete information, incompatible backend/model pairs, and runtime/cleanup failures have safe neutral diagnostics. Business consumers do not inspect SDK exceptions or object representations.

Python cancellation and interruption propagate after run-local cleanup. Cleanup failures do not replace a primary failure or cancellation. A cleanup-only failure is reported rather than silently claiming complete resource disposal. The engine never retries an entire agent run, repairs business JSON with an extra model call, or promises to roll back executed tool effects.

## Current Invariants

- Single requests call SDK `Model.get_response` once logically with no tools, handoffs, Runner, server-managed continuation, or prompt references. Transport retries retain existing client behavior without an additional wrapper retry layer.
- Agent execution uses SDK Runner as the sole tool dispatcher and continuation loop. Private SDK tools and output adapters are created internally; callers never provide SDK classes.
- The private SDK agent execution uses explicit `reasoning.effort="medium"` and `verbosity="medium"`. These are backend compatibility settings, not public cross-model options, and are not applied to single requests.
- SDK model/engine implementations are paired. Incompatible model input fails before model inference or tool execution; arbitrary external Model implementations are not silently adapted to the SDK Runner.
- A provider owns its clients and models. Returned models are borrowed. `aclose` is idempotent and closed providers/models cannot silently recreate resources. The engine closes only run-local resources, not the supplied model's provider.
- Async clients stay on one event loop; an instance cannot be concurrently used for multiple runs/calls or shared across event loops. No process-global client/provider singleton is introduced.
- Construction and `get_model` do not log in, refresh credentials, discover models, choose a default model, read configuration files, or send inference. Credential-bearing values are excluded from representations and diagnostics.
- `run` preserves the requested streaming/non-streaming choice. Semantic events retain tool call/result association and order without duplicate execution. Only available public reasoning summaries are emitted, never hidden thinking or signed provider state.
- Single-request tracing is disabled. Agent tracing obeys the supplied effective tracing choice; the FSQ adapter owns its export-key readiness gate. SDK logging and diagnostics do not expose model payloads, credentials, or tool data outside the permitted tracing policy.
- Context processing first applies SDK turn-based output trimming using user-message boundaries. A caller filter then receives ordered post-trimmer `ToolOutputEntry` values, including entry reference, call ID, name, user-turn position, and output, and returns replacements for known entries only.
- The context bridge does not reorder/drop messages, change call IDs, or expose/rebuild unrelated messages or private model state. The caller owns tool-count protection, artifact storage, previews, and sensitive-output policy.
- The backend uses the pinned SDK's available result information. SDK-default usage values are not treated as reliable measurements when actual usage cannot be established.

## Verification Scope

Verify public import independence, neutral request/tool/output/event conversion with the real pinned SDK and controlled transport, no single-request tool loop or tracing, strict output handling, event association, ordered context replacements, unknown result information, safe errors, model/backend compatibility, and loop-bound resource/cancellation cleanup. Business orchestration tests use neutral protocol implementations, not SDK-shaped test doubles.