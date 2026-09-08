# Module: adapters.coding_agent

## Purpose

Implement public SDK-neutral Agent runtime protocols through `agent_engine`. This adapter owns FSQ request assembly, configured model access, capability/helper tool bindings, business output contracts, neutral-event-to-RunEvent conversion, and FSQ context/artifact policy. It does not construct SDK agents, tools, clients, results, or raw protocol messages. Dynamic planning and verification policy remain in Agent; execution recording and platform automation remain in their owning modules.

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

`create_coding_agent_runtime(settings, *, harness_factory=None)` is the stable composition factory and returns `CodingAgentRuntime`. `OpenAIAgentsRuntime` is a compatibility export of the canonical FSQ runtime implementation, not an SDK object. Concrete tool adapters are private. Runtime tests may inject neutral engine/provider collaborators without exposing SDK types.

The runtime implements required public `run_task`, `run_pre_plan`, and `run_verification` operations. It receives or constructs `CodingAgentPolicy` through the public Agent API and does not import Agent-private modules.

## Internal Structure

- `__init__.py`: public factory and compatibility export.
- `_openai_runtime.py`: FSQ runtime assembly, neutral provider/engine wiring, main/pre-plan/verification requests, context/artifact policy, and business event/result conversion.
- `_harness_tools.py`: capability-to-neutral ToolBinding construction and StepRunner-backed invocation.

## Python Architecture

- Architecture level: Level 3 Layered Application adapter.
- Public API: runtime factory plus the documented concrete-runtime compatibility export.
- Internal modules: all `_*.py` implementation files.
- Domain boundaries: FSQ-to-generic-agent request and result assembly; SDK mechanisms remain inside `agent_engine`.
- Boundary models: FSQ values come from `models` or public Agent protocols; generic inference values come from `agent_engine`.
- Dependency direction: composition roots depend on this adapter; inward packages never do.
- Cross-module boundary: adapter implementation imports Agent-owned behavior only from `fsq_agent.agent`; imports from `fsq_agent.agent._*` are forbidden.
- Rationale: runtime assembly coordinates supplier access, tools, streaming, structured output, and external failures without a DI container.

## Error Handling

Neutral engine dependency/configuration, model, tool conversion, streaming, content filtering, timeout, and structured-output failures map to safe FSQ results/events. The adapter branches on `EngineError` categories, not SDK error strings/types. Cancellation propagates after scoped cleanup; cleanup does not overwrite the primary failure.

## Current Invariants

- Main execution, pre-plan, and verification preserve current SDK behavior, model settings, tracing, context trimming, event metadata, and structured output contracts.
- All three operations call `AgentEngine.run` with the selected neutral model. Main execution requests streaming; output contracts are derived from the authoritative FSQ Pydantic models and their parsers, not duplicated schemas or SDK classes.
- Tracing readiness is computed from the configured switch and OpenAI export-key presence, then passed as an effective neutral choice. The private backend supplies fixed GPT agent parameters.
- Context policy receives only ordered post-turn-trimmer tool-output entries. It preserves tool-count protection, artifact contents/references, previews, and secret handling and returns output replacements only; SDK history and private reasoning state are not exposed.
- Neutral tool events are the single source of model-call events. FSQ adds run/task identity, redaction, capability metadata, display text, and existing persisted event types without duplicate calls/events.
- Capability calls continue through Core `StepRunner`; AgentTool calls continue through Tools-owned behavior.
- Capability tool bindings format neutral invalid-input failures through their existing failure-result shape, including capability provenance, without executing StepRunner or a platform action. The engine retains call IDs and returns the failure to the model for continuation.
- Harness construction remains lazy and browser/application lifecycle remains explicit capability behavior.
- CLI and Control Plane inject the same runtime factory at composition boundaries.
- Relocation does not change CLI, HTTP/SSE, reports, evidence, provider configuration, workspace behavior, or `fsq runs`.
