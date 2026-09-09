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
- Standard-library runtime, JSON, timing, and filesystem helpers; no SDK/OpenAI imports or injected SDK constructor types.

The adapter must not be imported by Application, Agent, Execution, Core, Case DSL, Drivers, Harnesses, Environments, Config, Models, or other inward packages.

## Public Interface

`create_coding_agent_runtime(settings, *, harness_factory=None)` is the stable composition factory and returns `CodingAgentRuntime`. `DefaultCodingAgentRuntime` is the exported concrete FSQ runtime implementation, not an SDK object. These are the only package exports; concrete tool adapters are private. Runtime tests may inject neutral engine/provider collaborators without exposing SDK types.

The runtime implements required public `run_task`, `run_pre_plan`, and `run_verification` operations. It receives or constructs `CodingAgentPolicy` through the public Agent API and does not import Agent-private modules.

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

## Current Invariants

- Main execution, pre-plan, and verification use the same neutral engine contracts for model settings, tracing, tool-output filtering, event metadata, and structured output. The adapter does not depend on OpenAI Agents SDK or implement a tool-continuation loop.
- Runtime settings are consumed through `settings.agent_runtime`. FSQ-owned implementation names, progress text, and result summaries describe the neutral agent runtime rather than SDK objects.
- Main and verification provenance records use `agent_runtime.runner` and `agent_runtime.verifier`. They are runtime summaries, not capability invocations or recordable Case commands.
- Startup readiness uses the `Agent runtime ready` title, and runtime failures use `Agent run failed`; event types and payload field shapes are stable.
- All three operations call `AgentEngine.run` with the selected neutral model. Main execution requests streaming; output contracts are derived from the authoritative FSQ Pydantic models and their parsers, not duplicated schemas or SDK classes.
- Tracing readiness is computed from the configured switch and OpenAI export-key presence, then passed as an effective neutral choice. The private backend supplies fixed GPT agent parameters.
- Context policy receives ordered raw tool-output entries and returns output replacements only; vendor transcripts and private reasoning state are not exposed. It applies the same recency and size rules to all tool-output text and does not interpret tool sensitivity markers. Runtime-secret redaction remains owned by the existing upstream execution boundaries and occurs before context processing. No user-turn trimming policy is configured.
- The most recent three tool outputs remain inline only when each output is within the 30,000-character per-output limit and the retained outputs together fit the 60,000-character cumulative inline budget. The policy selects inline outputs newest-first. Every other historical output is represented by a bounded artifact reference, regardless of its individual size, so model-facing tool history remains bounded as tool calls accumulate.
- Historical replacements contain concise artifact references without copying output previews into every later model request. Unnamed historical tool-output entries use `runtime_tool` when a new artifact must be written. Existing artifact references retain their saved paths and are never renamed, nested inside replacement artifacts, or reconstructed from current tool labels. Repeated filtering reuses the artifact associated with the original call ID.
- Neutral tool events are the single source of model-call events. FSQ adds run/task identity, redaction, capability metadata, display text, and existing persisted event types without duplicate calls/events.
- After streamed main execution returns an `AgentResult`, the adapter emits exactly one `dynamic_agent_token_usage` event when neutral usage measurements are available. Its safe payload contains the configured provider and model plus available request, input, output, total, cached-input, and reasoning token counts. It does not estimate missing usage, inspect prompts or responses, include pre-plan or verification usage, write Run files directly, or mutate `run.json`.
- Capability calls continue through Core `StepRunner`; AgentTool calls continue through Tools-owned behavior.
- Capability tool bindings format neutral invalid-input failures through their existing failure-result shape, including capability provenance, without executing StepRunner or a platform action. The engine retains call IDs and returns the failure to the model for continuation.
- Harness construction remains lazy and browser/application lifecycle remains explicit capability behavior.
- CLI and Control Plane inject the same runtime factory at composition boundaries.
- The adapter does not migrate user configuration or historical Run files. CLI and HTTP/SSE field shapes, report schemas, evidence paths, and business verdict semantics remain independent of the concrete Python runtime name.
