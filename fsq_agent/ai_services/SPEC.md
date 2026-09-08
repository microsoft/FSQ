# Module: ai_services

## Purpose

Own FSQ visual assertion and completed-Case suggestion business services over neutral model calls. The services prepare business inputs, interpret model text, and return FSQ results. They do not own supplier authentication, SDK objects, platform actions, Case execution, candidate publication, or reports.

## Dependencies

- `models`: `AIAssertionRequest`, `AIAssertionResult`, and `ConfigurationError`.
- `config`: resolved `Settings` for service construction.
- `providers`: public configured-session factory and supplier metadata/lifecycle access.
- `agent_engine`: neutral text/image requests, results, and safe model errors.
- Standard library file reading, MIME detection, JSON, timing, and immutable records.
- No dependencies on Core, Agent, adapters, execution, Application, or SDK packages. Core accepts an injected evaluator structurally and does not construct these services.

## Public Interface

- `AIAssertionEvaluator`: synchronous `evaluate(request: AIAssertionRequest) -> AIAssertionResult` and `close()` operations.
- `CaseSuggestionAnalyzer`: synchronous `analyze(*, parsed_case, execution_report) -> CaseSuggestionAnalysis` over completed execution facts.
- `CaseSuggestionAnalysis`: immutable summary, suggestion entries, and optional candidate Case YAML.
- `build_ai_assertion_evaluator(settings)` and `build_case_suggestion_analyzer(settings)`: create services from the selected supplier without inference.
- `check_case_suggestion_readiness(settings)`: safely constructs and closes a configured analyzer without inference or UI actions.

The stable services, factories/readiness helper, and immutable service result are exported from `__init__.py`. Their callers are Application and entry/runtime composition; they are not supplier infrastructure exports. Service-specific output facts are local records; shared assertion request/result contracts remain in `models`.

## Internal Structure

- `__init__.py`: public service, factory, result, and readiness exports.
- `_factory.py`: configured construction and safe suggestion readiness.
- `_ai_assertion.py`: screenshot/text input preparation, assertion prompting/parsing, timing, and result construction.
- `_case_suggestion.py`: read-only completed-Case prompting, suggestion validation, and service result.

## Python Architecture

- Architecture level: Level 2 Simple Package.
- Public API: stable services, their factories/readiness, and service-specific result.
- Internal modules: `_*.py` files are private.
- Domain boundaries: FSQ assertion and suggestion policy only; model invocation delegates downward.
- Boundary models: shared FSQ assertion models, immutable suggestion results, and neutral engine request/results.
- Dependency direction: business callers consume services; services consume public providers/engine APIs; neither providers nor engine depends back on services.
- Rationale: two focused inference services share configured model access without another service registry or persistence layer.

## Error Handling

Missing provider configuration remains a `ConfigurationError`. Assertion inference/parse failures preserve existing error/failed verdict behavior rather than passing the assertion. Suggestion invalid JSON, shape, blank fields, or blank non-null candidate YAML remain configuration/analysis failures. Cancellation propagates and cleanup does not overwrite primary failures. Diagnostics never expose credentials or raw SDK objects.

## Current Invariants

- Each evaluation/analysis makes one tool-free `Model.complete` request through the configured provider session, with no semantic retry, Runner, action tool, handoff, or new constrained-generation setting.
- Assertions use the selected provider/model without a separate override, prepare the existing prompt/context, read the supplied screenshot when present, and pass image bytes/MIME through neutral content. Missing screenshots retain text-only behavior.
- Assertions preserve JSON extraction, passed/failed/error semantics, confidence handling, latency, artifact references, and optional available usage. Assertion evidence is not a locator recovery mechanism or permission to mutate a Case.
- Suggestion analysis consumes parsed source Case data and bounded completed execution facts, validates existing JSON fields, and cannot alter the completed result or invoke UI actions. Application owns candidate persistence/publication rules.
- Public evaluation and analysis remain synchronous. Provider adaptation bridges asynchronous invocation with fresh loop-scoped resources and closes on every path. Service reuse does not reuse a closed async client.
- Readiness sends no inference, constructs no harness, and exposes safe verdict/message/action only.

## Verification Scope

Verify existing assertion and suggestion behavior with neutral model-session doubles, including text/image preparation, no extra inference or tools, invalid outputs, safe errors, available usage, and guaranteed cleanup. Verify factories/readiness and caller imports use this public package without adding Core or provider dependency cycles.