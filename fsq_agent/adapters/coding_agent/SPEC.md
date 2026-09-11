# Module: adapters.coding_agent

## Purpose

Implement public SDK-neutral Agent runtime protocols through `agent_engine`. This adapter owns FSQ request assembly, configured model access, capability/helper tool bindings, business output contracts, neutral-event-to-RunEvent conversion, and FSQ context/artifact policy. It does not construct concrete backend agents, protocol tools, clients, results, or raw protocol messages. Dynamic planning and verification policy remain in Agent; execution recording and platform automation remain in their owning modules.

## Dependencies

- `agent`: public runtime protocols and the public `CodingAgentPolicy` SDK-neutral facade.
- `models`: settings and runtime boundary values.
- `providers`: supplier-configured neutral model sessions.
- `agent_engine`: public engine/model protocols, requests, tool bindings, output contracts, events, filters, and errors.
- `ai_services`: configured visual assertion evaluator construction.
- `config`: runtime settings and validation.
- `tools`: AgentTool definitions and execution adapters.
- `core`: capability registry, StepRunner, and public harness/factory contracts.
- Standard-library runtime, JSON, timing, and filesystem helpers; no OpenAI/Google SDK imports or injected SDK constructor types.

The adapter must not be imported by Application, Agent, Execution, Core, Case DSL, Drivers, Harnesses, Environments, Config, Models, or other inward packages.

## Public Interface

`create_coding_agent_runtime(settings, *, harness_factory=None)` is the stable composition factory and returns `CodingAgentRuntime`. `DefaultCodingAgentRuntime` is the exported concrete FSQ runtime implementation, not an SDK object. These are the only package exports; concrete tool adapters are private. Runtime tests may inject neutral engine/provider collaborators without exposing SDK types.

The runtime implements required public `run_task`, `run_pre_plan`, and `run_verification` operations. It receives or constructs `CodingAgentPolicy` through the public Agent API and does not import Agent-private modules.

Runtime composition binds the Models-owned Execution-allocated context, Core evidence-journal sink, execution identities, and cancellation boundary before external actions. The public factory retains settings and optional harness-factory inputs. The adapter does not import Execution, allocate Runs, update owner records, freeze results, generate final reports, or transition metadata.

The main runtime owns the Harness returned by its default or supplied `harness_factory` for that invocation and closes it through `HarnessInterface.close` on success, startup failure after construction, inference failure, or cancellation. It also closes its Provider session even if Harness disposal fails. A caller supplying a shared borrowed Harness uses a wrapper with non-owning disposal. Test collaborators implement the same close contract without external effects.

## Internal Structure

- `__init__.py`: public factory and concrete-runtime export.
- `_runtime.py`: FSQ runtime assembly, neutral provider/engine wiring, main/pre-plan/verification requests, context/artifact policy, and business event/result conversion.
- `_harness_tools.py`: capability-to-neutral ToolBinding construction and StepRunner-backed invocation.

## Python Architecture

- Architecture level: Level 3 Layered Application adapter.
- Public API: runtime factory plus the documented `DefaultCodingAgentRuntime` concrete implementation export.
- Internal modules: all `_*.py` implementation files.
- Domain boundaries: FSQ-to-generic-agent request and result assembly; inference protocol, continuation, tool dispatch, and tracing mechanisms remain inside `agent_engine`.
- Boundary models: FSQ values come from `models` or public Agent protocols; generic inference values come from `agent_engine`.
- Dependency direction: composition roots depend on this adapter; inward packages never do.
- Cross-module boundary: adapter implementation imports Agent-owned behavior only from `fsq_agent.agent`; imports from `fsq_agent.agent._*` are forbidden.
- Rationale: runtime assembly coordinates supplier access, tools, streaming, structured output, and external failures without a DI container.

## Error Handling

Neutral engine dependency/configuration, model, tool conversion, streaming, content filtering, timeout, and structured-output failures map to safe FSQ results/events. The adapter branches on `EngineError` categories, not SDK error strings/types. Generic runtime failures use `agent_runtime_error` for both failure category and reason. Provider content-filter and incomplete-response failures retain their distinct categories and reasons. Cancellation propagates after scoped cleanup; cleanup does not overwrite the primary failure.

The construction-timeout boundary disposes any late-created owned Harness rather than abandoning it. Late construction never permits tool invocation. Synchronous resource disposal does not block the event loop; cancellation does not detach cleanup while owned resources remain releasable. Primary failures, including failures already converted to StepResult, retain precedence; cleanup-only failures remain visible and do not fabricate an authored browser/application lifecycle step.

## Current Invariants

- Main execution, pre-plan, and verification use the same neutral engine contracts for model settings, tracing, tool-output filtering, event metadata, and structured output. The adapter does not depend on OpenAI Agents SDK or implement a tool-continuation loop.
- Runtime settings are consumed through `settings.agent_runtime`. FSQ-owned implementation names, progress text, and result summaries describe the neutral agent runtime rather than SDK objects.
- The request builder forwards the task's resolved `settings.agent_runtime.reasoning_effort` to every pre-plan, main-execution, and final-verification `AgentRequest`. All three phases and the injected visual assertion evaluator use the same platform settings snapshot without mid-run effort refresh or phase-specific effort tuning. The adapter neither translates native effort values nor infers them from model names; backend mapping belongs to Agent Engine.
- Main and verification provenance records use `agent_runtime.runner` and `agent_runtime.verifier`. They are runtime summaries, not capability invocations or recordable Case commands.
- Startup readiness uses the `Agent runtime ready` title, and runtime failures use `Agent run failed`; event types and payload field shapes are stable.
- All three operations call `AgentEngine.run` with the selected neutral model. Main execution requests streaming; output contracts are derived from the authoritative FSQ Pydantic models and their parsers, not duplicated schemas or SDK classes.
- Production composition obtains the engine through `create_agent_engine_for_model` using the neutral model from the frozen Provider session. It does not assume every model accepts the OpenAI engine, inspect SDK objects, or reuse an incompatible engine across sessions. Injected neutral test collaborators retain their documented composition behavior. Pre-plan, main execution, and verification remain on the prepared task's Provider snapshot; a saved Provider replacement affects only subsequently constructed tasks.
- Tracing readiness is computed from the configured switch and OpenAI export-key presence, then passed as an effective neutral choice. Private engine backends own provider-specific generation parameters; Google inference credentials do not authorize OpenAI trace export.
- Context policy receives ordered raw tool-output entries and returns output replacements only; vendor transcripts and private reasoning state are not exposed. It applies the same recency and size rules to all tool-output text and does not interpret tool sensitivity markers. Runtime-secret redaction remains owned by the existing upstream execution boundaries and occurs before context processing. No user-turn trimming policy is configured.
- The most recent three tool outputs remain inline only when each output is within the 30,000-character per-output limit and the retained outputs together fit the 60,000-character cumulative inline budget. The policy selects inline outputs newest-first. Every other historical output is represented by a bounded artifact reference, regardless of its individual size, so model-facing tool history remains bounded as tool calls accumulate.
- Historical replacements contain concise artifact references without copying output previews into every later model request. Unnamed historical tool-output entries use `runtime_tool` when a new artifact must be written. Existing artifact references retain their saved paths and are never renamed, nested inside replacement artifacts, or reconstructed from current tool labels. Repeated filtering reuses the artifact associated with the original call ID.
- Neutral tool events are the single source of model-call events. FSQ adds run/task identity, redaction, capability metadata, display text, and existing persisted event types without duplicate calls/events.
- After streamed main execution returns an `AgentResult`, the adapter emits exactly one `dynamic_agent_token_usage` event when neutral usage measurements are available. Its safe payload contains the configured provider and model plus available request, input, output, total, cached-input, and reasoning token counts. It does not estimate missing usage, inspect prompts or responses, include pre-plan or verification usage, write Run files directly, or mutate `run.json`.
- Capability calls continue through Core `StepRunner`; AgentTool calls continue through Tools-owned behavior.
- Every actual capability invocation receives stable source identity and a unique execution identity before action, including repeated calls and recovery attempts. New `runner_step_id` and result `step_id` alias `step_execution_id`. Core durable acknowledgements are independent of engine stream completion, final tool JSON, and context truncation; neutral progress events correlate identities without becoming a second execution ledger.
- Structured `runner_result` preserves measured timing, primary action failure, secondary evidence errors, artifact availability, and identities before display truncation. Unknown measurements are null with reasons. AgentTools, pre-plan entries, and runner summary records do not inflate real capability counts; measured main-only usage is not estimated per step.
- Capability tool bindings format neutral invalid-input failures through their existing failure-result shape, including capability provenance, without executing StepRunner or a platform action. The engine retains call IDs and returns the failure to the model for continuation.
- Harness construction remains lazy and browser/application lifecycle remains explicit capability behavior.
- CLI and Control Plane inject the same runtime factory at composition boundaries.
- The adapter does not migrate user configuration or historical Run files. CLI and HTTP/SSE field shapes, report schemas, evidence paths, and business verdict semantics remain independent of the concrete Python runtime name.

## Verification Scope

Model-paired engine construction, all three runtime operations, output contracts, tool events, context-budget replacements, and measured main-only usage work through both private engine backends without SDK imports in the adapter. Shared real-client transport tests exercise actual FSQ schemas and tool/result continuation; isolated runtime tests preserve neutral collaborator injection, task snapshot isolation, and existing OpenAI/Azure/Copilot behavior.

Effort verification uses configured non-default values to establish propagation through all three Agent phases and visual evaluator composition, with the same task snapshot and no extra inference. Runtime behavior follows the selected preset's value rather than fixing a mutable platform default in tests.

Context-binding verification proves that the shared Core sink receives actual capability facts once even when progress delivery fails or model-facing outputs are shortened, and that cancellation preserves persisted facts and backend resource cleanup.
