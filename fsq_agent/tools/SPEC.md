# Module: tools

## Purpose

Expose dynamic-only AgentTools through neutral agent-engine tool bindings. AgentTools help the agent inspect local context and recover large historical outputs during dynamic execution, but they are not recordable FSQ capabilities and are never included in strict replay registries.

The tools module owns scoped file read/write behavior, bounded run-artifact search and slice reads, AgentTool result normalization, model-facing output artifacting for large helper outputs, and safe AgentTool run-event metadata.

The tools module does not own CommonTools, PlatformTools, platform actions, AI assertions, runtime progress events, local CLI execution, shell execution, or runtime-secret resolution. Recordable CommonTool capabilities such as `wait_ms` are inherited by platform tool providers owned by `core`; runtime-secret text input is resolved by `core`, not by an AgentTool or secret-fetch helper. Platform actions and `assertWithAI` are PlatformTools owned by `core`, with backend-specific tool bodies on concrete drivers. Progress events are runtime-internal events emitted by `agent`; provider-backed AI evaluation is owned by `ai_services` and injected into platform runtimes by entry-layer code.

## Dependencies

- Internal project dependencies: `models` and public `agent_engine` tool contracts.
- External dependencies: standard library path, JSON, async, and typing helpers plus Pydantic for private AgentTool argument models.
- Forbidden dependencies: `agent`, `providers`, `ai_services`, `core`, `cli`, `config`, `knowledge`, `skills`, `report`, `capabilities`, and SDK classes or constructor/callback shapes, including injected SDK types.

## Public Interface

Current `__init__.py` exports via `__all__`:

- `AgentToolProvider`: Protocol for a provider of SDK-neutral dynamic helper tools. It exposes serializable AgentTool definitions and invokes one helper by canonical name with JSON-like arguments.
- `AgentToolRegistry`: Maintains the active AgentTool helper set for dynamic runtime exposure and rejects duplicate canonical names. It is not the global capability registry and must not contain recordable CommonTools or PlatformTools.
- `AgentToolExecutor`: Routes AgentTool calls to registered providers and returns normalized `AgentToolResult` values.
- `FileOps`: Performs scoped file reads and writes. Read roots include the selected workspace platform's resolved cases/knowledge roots, resolved preset skill resources, and the current run where needed; writes are restricted to the current run artifact boundary.
- `ToolArtifactStore`: Persists complete AgentTool outputs under the current run directory and provides bounded artifact search and slice reads.
- `DefaultAgentToolProvider`: Built-in provider for `read_file`, `write_file`, `search_artifact`, and `read_artifact_slice`.
- `AgentToolAdapter`: Builds neutral `ToolBinding` values from AgentTool definitions, receives `ToolCall` JSON-object arguments, and preserves existing helper execution and model-facing JSON. `build_tools` does not accept an SDK class.
- Tool bindings supply a safe invalid-input formatter so malformed JSON and non-object arguments return the existing failed AgentTool JSON without invoking a helper or losing the agent continuation path.
- Backward-compatible `CommonTool*` import aliases are private compatibility shims only. New code and SPEC text use AgentTool names for this module.

The exposed AgentTool names are:

| Tool name | Purpose |
|---|---|
| `read_file` | Read UTF-8 text from an allowed input/output path with bounded response size. |
| `write_file` | Write UTF-8 text below the configured output root or current run directory. |
| `search_artifact` | Search text artifacts below the current run directory and return bounded matches. |
| `read_artifact_slice` | Read a bounded character slice from one current-run artifact path. |

AgentTools have no `ReplayPolicy`, do not produce `CapabilityDefinition` records, and must not be converted into generated strict replay commands.

This module does not expose `wait_ms`, `get_runtime_secret`, runtime-secret resolution, `run_cli_tool`, SDK `ShellTool` construction/execution, public/common `submit_visual_assertion`, or public/common `publish_progress`.

## Internal Structure

- `__init__.py`: Public exports only.
- `_agent_tools.py`: AgentTool provider protocol, registry, executor, built-in helper wiring, and AgentTool result normalization.
- `_agents_adapter.py`: Neutral tool-binding construction, AgentTool execution callbacks, AgentTool-origin events, and existing safe model-facing results.
- `_tool_artifacts.py`: Per-run AgentTool output artifact persistence plus bounded artifact search and slice helpers.
- `_file_ops.py`: Scoped file operations.
- `_common.py`: Compatibility module for existing aliases only; new behavior belongs in AgentTool modules above.
- `SPEC.md`: Module design.

## Python Architecture

- Architecture level: 2 Simple Package.
- Public API: AgentTool provider/executor interfaces, built-in AgentTool provider, scoped file/artifact utilities, and neutral tool adapter exported from `__init__.py`.
- Internal modules: `_agent_tools.py`, `_agents_adapter.py`, `_tool_artifacts.py`, `_file_ops.py`, and `_common.py` are private implementation modules.
- Domain boundaries: dynamic helper safety, scoped file access, artifact bounds, output artifacting, and AgentTool event metadata live here. Global capability routing, evidence capture, replay decisions, CommonTool behavior, PlatformTool behavior, and result/event normalization for recordable execution live in `core` and entry/runtime layers.
- Boundary models: AgentTool definitions/calls/results come from `models` or private Pydantic helper models; model-facing bindings/callback records come from public `agent_engine`.
- Dependency direction: may import public symbols from `models` and `agent_engine`; must not import `capabilities`, `core`, `agent`, `cli`, `config`, `providers`, `knowledge`, `skills`, `report`, or SDK packages.
- Rationale: focused dynamic helper behavior and neutral tool adaptation do not require platform orchestration or another application layer.

## Error Handling

During SDK-managed runs, recoverable AgentTool failures return model-visible structured error JSON so the agent can retry or report failure. Invalid configuration, duplicate tool names, invalid tool names, malformed arguments, path traversal, attempts to read or write outside allowed roots, artifact bounds violations, and malformed outputs raise or normalize to `ToolExecutionError` from `models` depending on whether the failure happens during construction or invocation.

AgentTool outputs may be persisted to run-local tool artifacts when they exceed configured inline limits. AgentTool artifacts must stay under the current run directory and bounded read/search helpers must enforce maximum response sizes.

AgentTool events must use an AgentTool-specific origin such as `agent_tool`. Recorders must ignore AgentTool events even when they succeed.

## Verification Scope

- Verification covers AgentTool registration, scoped file access, bounded artifact lookup/slicing, large-output artifacting, structured failure results, and neutral tool binding behavior.
- Boundary verification ensures AgentTools remain dynamic-only, absent from strict capability registries, and ignored by dynamic strict-case recording.

## Current Invariants

- AgentTool is the dynamic-only helper concept in this module.
- CommonTool is reserved for platform-default recordable capabilities owned by `core` platform tool providers.
- AgentTools do not use the `capabilities` decorator layer because they are not executable FSQ capabilities and must not enter strict registries. Any decorated compatibility path must be hidden behind compatibility shims and must not be exposed as current AgentTool behavior.
- File operation tools treat the selected workspace platform's cases/knowledge and resolved preset skill resources as read-only inputs. They write generated files only under the current run's artifact directory, which is a direct-child run below the resolved platform run root.
- Artifact read tools only resolve paths inside the current run directory and enforce bounded search/slice results so artifact recovery cannot reintroduce unbounded context growth.
- Local CLI and shell execution remain out of scope. Command execution requires an explicitly scoped capability with its own SPEC update.
- `publish_progress` is not an AgentTool. Runtime progress is emitted directly by `agent` as `RunEvent` values so user-visible status does not consume a model tool call or become confused with external capabilities.
- `submit_visual_assertion` is not an AgentTool. Platform `assertWithAI` is a backend-owned PlatformTool exposed by the active platform capability surface and evaluated through an injected provider-backed evaluator.
