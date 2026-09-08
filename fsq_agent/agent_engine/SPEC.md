# Module: agent_engine

## Purpose

Provide SDK-neutral model access and agent execution through independently reusable contracts. The private backend uses the OpenAI Python Responses client for tool-free requests and a local function-tool continuation loop for agent execution. The module owns inference protocol conversion, strict schemas, context processing, safe tracing export, and resource cleanup, not supplier authentication, credential refresh, model discovery, filesystem policy, or FSQ business judgments.

## Dependencies

- Python standard library for protocols, dataclasses, asynchronous lifecycle, and JSON values.
- OpenAI Python for Responses requests and HTTP/SSE handling, and HTTPX for private tracing export; backend dependencies are loaded lazily.
- Caller-owned output parsers supply business validation; parser failures are normalized without coupling to FSQ models.
- No `openai-agents` distribution, `agents` imports, optional SDK backend, or runtime dependency on a reference source checkout. Selectively reused upstream code is private and carries source provenance and applicable license notices in source, wheel, and sdist.
- No dependencies on other FSQ packages, including type-only imports, settings, tasks, business models, providers, tools, Core, or adapters.

## Public Interface

Public exports from `__init__.py`:

- `Model`: `async complete(request: ModelRequest) -> ModelResult` performs one logical tool-free model request.
- `ModelProvider`: `get_model(model_name: str) -> Model` supplies an explicitly named model; `async aclose() -> None` closes owned model/client resources.
- `AgentEngine`: `async run(model: Model, request: AgentRequest, *, on_event: AgentEventSink | None = None) -> AgentResult` executes one agent task, including model/tool continuation.
- `create_model_provider`: creates a provider from explicit endpoint, credential, and optional request headers without inference or authentication operations.
- `create_agent_engine`: creates the private local engine through the public protocol.
- `JsonValue`, `TextContent`, `ImageContent`, `Message`, `ModelRequest`, `ModelResult`, and `TokenUsage`: neutral input/output values. Images contain bytes and MIME type; the engine does not read image files.
- `ToolCall`, `ToolInputFailure`, `ToolBinding`, and `OutputContract`: neutral tool identity/JSON-object arguments, safe invalid-input facts, tool schema/callback binding, and named output schema with validation/parser callback.
- `AgentRequest`, `AgentResult`, `AgentEvent`, and `AgentEventSink`: execution input, validated output, semantic events, and asynchronous event delivery.
- `ToolOutputEntry`, `ToolOutputFilter`, and `ToolOutputTrimSettings`: ordered tool-output views, restricted replacements, and user-turn trimming configuration.
- `EngineError`: safe category, message, and optional HTTP status or supported failure detail; no raw third-party exception contract.

Provider factories are the public construction boundary; concrete backend classes are not exported. Public imports do not initialize or import backend implementations and do not require credentials. Public values do not contain client/backend objects, arbitrary vendor-request kwargs, or raw response/transcript dictionaries. Factories and boundary records are intentional public APIs required by callers to construct requests and supply resources; no registry or dependency-injection container is used.

`ModelRequest` supplies optional instructions and text or ordered neutral messages. `Model.complete` validates the provider response before returning a `ModelResult` with extracted text, optional token usage, and best-available completion information. Known incomplete, failed, or refused responses raise `EngineError` and do not return partial text as a usable result. Unknown finish information on an otherwise valid response and unavailable usage remain unknown or absent, not a fabricated successful finish or precise zero measurement.

`AgentRequest` supplies name, instructions, input, tool bindings, an optional output contract, a turn limit, streaming choice, tracing choice, and optional trimming/filter configuration. `AgentResult` contains final output validated by the supplied contract and available usage. Neither result declares a business test passed.

Tool callbacks receive `ToolCall` with call ID, name, and JSON-object arguments and asynchronously return model-facing text, including caller-formatted JSON. A tool's schema constraint is separate from local business validation. Output contracts carry a caller-owned schema and parser; required constrained output is not silently downgraded to best-effort text parsing.

`ToolBinding.on_invalid_input` optionally receives `ToolInputFailure` with tool name, call ID, and a safe validation message, and returns model-facing failure text. Malformed JSON or a non-object argument never invokes the normal execution callback. Without a supplied error formatter the engine returns a generic JSON error result and allows agent continuation; it does not invent FSQ result metadata or retry the tool. Invalid raw input and private execution context are not passed to the failure formatter.

## Internal Structure

- `__init__.py`: public exports and lazy factories.
- `_contracts.py`: protocols, boundary values, and errors.
- `_openai_backend.py`: provider/model lifecycle, Responses client access, tool-free requests, and compatible engine construction.
- `_runner.py`: shared streaming/non-streaming agent continuation, function-tool dispatch, output resolution, and run-owned task cleanup.
- `_conversion.py`: shared private response validation plus Responses input, output, tool, usage, semantic event, and safe error conversion.
- `_context.py`: user-turn output trimming and neutral tool-output replacement filtering.
- `_schema.py`: private strict-schema normalization for tools and output contracts.
- `_tracing.py`: required trace/span collection, safe compatible export, and exporter resource lifecycle.

## Python Architecture

- Architecture level: Level 2 Simple Package.
- Public API: the protocols, factories, errors, and boundary records listed above.
- Internal modules: `_*.py` files are private to this package.
- Domain boundaries: generic inference and agent execution only; business prompts, files, authentication, supplier selection, and result interpretation are external.
- Boundary models: locally owned neutral dataclasses, JSON types, protocols, and generic validation callbacks.
- Dependency direction: other FSQ packages consume this public API; the engine does not depend on them. OpenAI and tracing transport imports stay inside private implementation modules.
- Rationale: model access and execution need separate contracts but no persistence, service/repository layering, or plugin framework.

## Error Handling

Missing backend dependencies become a neutral configuration error when backend construction/use requires them, not a failure to import public contracts. Authentication, rate limiting, availability, timeout, invalid output, supported refusal/incomplete information, incompatible backend/model pairs, turn exhaustion, and runtime/cleanup failures have safe neutral diagnostics. Business consumers do not inspect third-party exceptions or object representations.

`AgentEngine.run` and `Model.complete` use the same private response validation. Top-level or output-item incompleteness raises `EngineError(category="incomplete")`, retaining `reason="content_filter"` when available and otherwise `reason="incomplete"`. A failed or invalid terminal response raises `invalid_output`. An explicit refusal item raises `refusal` even when its explanation is empty. Validation precedes successful result conversion, semantic output delivery, and tool effects; partial response contents and raw provider error details are not exposed through exceptions. Direct requests do not invoke the Agent loop, and no additional model request is issued to repair these failures. Business consumers map the neutral errors through their existing error handling rather than implementing response-status policies independently.

Python cancellation and interruption propagate after run-local stream and tool-task cleanup, including cancellation raised inside callbacks. Cleanup failures do not replace a primary failure or cancellation. A cleanup-only failure is reported rather than silently claiming complete resource disposal. The engine never retries an entire agent run, repairs invalid business JSON with an extra model call, or promises to roll back executed tool effects. Trace-export failures are non-fatal to business results and do not trigger model or tool retries.

## Current Invariants

- Single requests call the OpenAI Responses client once logically with no tools, agent loop, server-managed continuation, or prompt references. Transport retries retain client behavior without an additional wrapper retry layer.
- Response validity and provider failure classification are shared by direct and Agent requests inside this package, not repeated in supplier sessions or individual business services. Agent execution alone owns tool continuation, model-turn budgets, and caller-supplied final-output parsing.
- Agent execution has one local tool dispatcher and continuation loop. Streaming and non-streaming modes share turn resolution and tool dispatch, and use private tool-capable Responses requests rather than invoking public tool-free `Model.complete` from the loop. Supported tools are caller-bound function tools; no handoff, hosted-tool, approval/resume, session-storage, WebSocket, voice, realtime, or sandbox framework is exposed.
- Private agent execution uses explicit `reasoning.effort="medium"` and `verbosity="medium"`. These are backend compatibility settings, not public cross-model options, and are not applied to single requests.
- Backend model/engine implementations are paired. Incompatible model input fails before model inference or tool execution; arbitrary external Model implementations are not silently adapted to the local loop.
- Agent name, positive turn limit, and unique bound tool names are validated before inference or tool effects. Turn limits count model requests, not individual tool calls, and exhaustion prevents another model request.
- Same-turn function callbacks are scheduled concurrently; transcript assembly and semantic events retain call/result association and defined ordering without promising chronological ordering of concurrent effects. Unknown tools fail safely. Schema conversion copies caller schemas and does not replace callback-owned business validation.
- Responses with pending function tools continue after tool execution even when they also contain text. With no pending tools, final text comes from the last message; plain-text output may be empty. A required structured output with no final text continues within the turn budget, while invalid candidate text fails validation without a repair request. Refusal and incomplete-response information retain their safe neutral distinctions.
- A provider owns its clients and models. Returned models are borrowed. `aclose` is idempotent and closed providers/models cannot silently recreate resources. The engine closes only run-local resources, not the supplied model's provider.
- Failed provider disposal retains resources for a later close attempt without permitting new model use. Run-owned streams and tool callbacks are cancelled and joined on run failure, cancellation, or event-consumer failure before control returns; no whole-run restart or tool rollback occurs.
- Async clients stay on one event loop; an instance cannot be concurrently used for multiple runs/calls or shared across event loops. No process-global client/provider singleton is introduced.
- Construction and `get_model` do not log in, refresh credentials, discover models, choose a default model, read configuration files, or send inference. Credential-bearing values are excluded from representations and diagnostics.
- `run` preserves the requested streaming/non-streaming choice. Streaming emits neutral agent-start, tool-call, tool-output, message, and available reasoning-summary events; non-streaming does not synthesize streaming callbacks. Partial tool arguments and repeated incremental/completed item representations never cause early or duplicate tool execution. Failed or unterminated streams do not produce successful results. Only available public reasoning summaries are emitted, never hidden thinking or signed provider state.
- Single-request tracing is disabled. Agent tracing obeys the supplied effective tracing choice; the FSQ adapter owns its export-key readiness gate. Required Agent/response/function spans retain compatible metadata association and real export to the OpenAI tracing ingest endpoint, independently of model-provider authentication. Run-local tracing choices are isolated, and tracing does not rewrite unrelated caller-owned trace data.
- Trace export uses bounded retries and owned queue/client lifecycle with bounded shutdown/flush handling. Model/tool payloads, credentials, private model state, and raw exception details are excluded from traces and diagnostic logs; exporter failures remain safe and non-fatal.
- Context processing first applies turn-based string tool-output trimming using user-message boundaries. A caller filter then receives ordered post-trimmer `ToolOutputEntry` values, including entry reference, call ID, name, user-turn position, and output, and returns text replacements for known integer entry IDs only.
- The context bridge does not reorder/drop messages, change call IDs, or expose/rebuild unrelated messages or private model state. The caller owns tool-count protection, artifact storage, previews, and sensitive-output policy.
- Private transcript continuation preserves required opaque provider items without exposing vendor transcripts publicly or using server-managed conversation identifiers. Caller-owned inputs and schemas are not mutated.
- Results use available provider measurements and supported completion information. Missing usage and unknown finish information remain absent or unknown rather than fabricated zero measurements or successful finishes.

## Verification Scope

Verify public import independence and dependency direction, neutral request/tool/output/event conversion with the pinned OpenAI client and controlled HTTP/SSE transport, single-request isolation, strict output and turn resolution, concurrent tool association, context replacement integrity, unknown result information, safe errors and trace export, and loop-bound resource/cancellation cleanup. Business orchestration tests use neutral protocol implementations. Clean wheel and sdist installations exercise the six inference paths without the `openai-agents` distribution or `agents` import, and include applicable upstream source notices.