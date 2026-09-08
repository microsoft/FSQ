# Module: adapters

## Purpose

Own external protocol adaptation for the public CLI, local Control Plane, and coding-agent runtimes. Adapters decode external inputs, invoke inward public contracts, and project safe output. They do not own Application validation, execution semantics, persistence formats, capability policy, or platform automation.

## Dependencies

Adapters may import public APIs from `application`, `execution`, `models`, `config`, `providers`, `ai_services`, `agent`, `agent_engine`, `tools`, `case_dsl`, `core`, and `report`. Inward modules must not import `adapters`. Adapter subpackages must not form cycles. SDK inference objects remain private to `agent_engine` rather than adapter APIs.

## Public Interface

- `adapters.cli` owns the `fsq` Click command tree and exports `main`.
- `adapters.control_plane` owns `ControlPlaneServer`, `ControlPlaneServerOptions`, `run_control_plane`, and the documented HTTP/SSE/static API.
- `adapters.coding_agent` implements public Agent runtime protocols by assembling FSQ requests/tool bindings and consuming neutral `agent_engine` results/events. Composition roots consume its public runtime factory and the documented runtime compatibility export.

The installed scripts target canonical `fsq_agent.adapters.cli:main`. Existing `fsq_agent.cli` and `fsq_agent.control_plane` packages remain compatibility entries for documented public symbols only, and each compatibility symbol references the canonical adapter object. Old private transport submodule imports are unsupported and absent.

## Internal Structure

- `cli/`: CLI parsing, presentation, output modes, and exit mapping.
- `control_plane/`: Control Plane HTTP/SSE/static transport, projections, and state.
- `control_plane/static/`: Generated Control Plane package data.
- `coding_agent/`: FSQ-to-agent_engine request/tool/output assembly, context policy, business event/result adaptation, and public runtime factory.
- Adapter-private `_*.py` files are not imported by inward modules.

## Python Architecture

- Architecture level: Level 3 Layered Application.
- Public API: transport entry points plus the Coding Agent runtime factory consumed by composition roots.
- Domain boundaries: external protocol decoding and presentation only.
- Boundary models: Click values and HTTP/JSON/SSE dictionaries remain at adapter boundaries.
- Dependency direction: adapters depend inward; Application and lower modules never import adapters.
- Rationale: the entry surfaces coordinate frameworks, state, static delivery, and inward services without requiring another domain layer.

## Error Handling

Adapters map stable inward failures into documented exit codes or safe HTTP/SSE responses. Compatibility paths preserve exception identity, state identity, redaction, and output behavior.

## Current Invariants

- Public compatibility packages do not duplicate transport implementations, package data, or mutable state.
- CLI and Control Plane business operations continue through Application.
- Control Plane HTML, JavaScript, CSS, `entry-assets.json`, and all referenced generated assets are runtime package data included in both wheel and source distribution. Installed `fsq ui` resolves these resources from the installed package without a frontend source tree, Node.js, or runtime asset download.
- CLI and Control Plane use the same public Execution services; adapter-private modules do not implement lifecycle or recording policy.
- CLI and Control Plane composition roots inject the same Coding Agent runtime implementation into SDK-neutral Agent orchestration. Application and inward packages never import `adapters.coding_agent`.
- Assertion evaluator construction consumes `ai_services`; configured supplier access consumes `providers`. Neither transport nor runtime adapters invoke OpenAI/Agents SDK inference directly.
- CLI keeps this composition in a private helper rather than in command handlers. The helper may construct `FsqAgent` with the public Coding Agent runtime factory and return the SDK-neutral collaborator expected by Application, but it does not validate requests or own execution policy.
- Module relocation does not change CLI, HTTP, SSE, frontend, workspace, Case, evidence, report, Provider, or Runs contracts.
