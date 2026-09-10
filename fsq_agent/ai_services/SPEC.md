# Module: ai_services

## Purpose

Own FSQ visual assertion and completed-Case suggestion business services over neutral model calls. The services prepare business inputs, supply private model-facing schemas and parsers, and convert validated parsed objects into FSQ results. They do not own supplier authentication, SDK objects, platform actions, Case execution, candidate publication, or reports.

## Dependencies

- `models`: `AIAssertionRequest`, `AIAssertionResult`, and `ConfigurationError`.
- `config`: resolved `Settings` for service construction.
- `providers`: public configured-session factory and supplier metadata/lifecycle access.
- `agent_engine`: neutral text/image requests, generic output contracts/results, and safe model errors.
- Pydantic for private model-facing output schemas and strict business validation.
- Standard library file reading, MIME detection, JSON, timing, and immutable records.
- No dependencies on Core, Agent, adapters, execution, Application, or SDK packages. Core accepts an injected evaluator structurally and does not construct these services.

## Public Interface

- `AIAssertionEvaluator`: synchronous `evaluate(request: AIAssertionRequest) -> AIAssertionResult` and `close()` operations.
- `CaseSuggestionAnalyzer`: synchronous `analyze(*, parsed_case, execution_report) -> CaseSuggestionAnalysis` over completed execution facts.
- `CaseSuggestionAnalysis`: immutable summary, suggestion entries, and optional candidate Case YAML.
- `build_ai_assertion_evaluator(settings)` and `build_case_suggestion_analyzer(settings)`: create services from the selected supplier without inference.
- `check_case_suggestion_readiness(settings)`: safely constructs and closes a configured analyzer without inference or UI actions.

The stable services, factories/readiness helper, and immutable service result are exported from `__init__.py`. Their callers are Application and entry/runtime composition; they are not supplier infrastructure exports. Service-specific output facts are local records; shared assertion request/result contracts remain in `models`.

## Model-Facing Output Contracts

Each service supplies an `OutputContract` built from its private Pydantic output model's JSON Schema and parser. Both root schemas are objects, every declared field is required, and nullable fields are present with `null` when absent. Root objects and suggestion entries forbid additional properties. Field validation is strict: booleans and text are not obtained through coercion. The parser validates the whole JSON document, allowing ordinary JSON whitespace but not Markdown fences, surrounding prose, extracted substrings, non-finite numeric extensions, or non-object roots. Services consume only the expected typed `parsed_output`; a missing or wrong parsed object is `invalid_output`, not permission to parse result text again.

- Visual assertion output contains only `passed`, `explanation`, and `confidence`. `passed` is a strict boolean. `explanation` is a nonblank string normalized by stripping surrounding whitespace. `confidence` is null or a finite JSON number from 0 through 1 inclusive; integer endpoints are valid, while booleans and numeric strings are invalid. Execution-owned status, provider identity, timing, usage, and artifact metadata do not belong in the model-facing schema. A summary alias, invented explanation, or non-object fallback cannot supply a verdict.
- Case suggestion output contains only `summary`, `suggestions`, and `candidate_case_yaml`. `summary` is a nonblank string normalized by stripping surrounding whitespace. `suggestions` is a possibly empty array of objects containing only required nonblank string `kind` and `message` fields, also stripped of surrounding whitespace; kinds are not a closed enum. `candidate_case_yaml` is null or a nonblank string normalized by stripping surrounding whitespace, without another YAML formatter. Conversion returns the frozen `CaseSuggestionAnalysis` and immutable suggestion entries. A candidate string is not proof of valid FSQ YAML or successful execution; Application and Case DSL own validation and publication.

## Internal Structure

- `__init__.py`: public service, factory, result, and readiness exports.
- `_factory.py`: configured construction and safe suggestion readiness.
- `_ai_assertion.py`: private assertion output model/schema/parser, screenshot/text input preparation, prompting, timing, and parsed-verdict result construction.
- `_case_suggestion.py`: private suggestion output models/schema/parser, read-only completed-Case prompting, and immutable service result conversion.

## Python Architecture

- Architecture level: Level 2 Simple Package.
- Public API: stable services, their factories/readiness, and service-specific result.
- Internal modules: `_*.py` files are private.
- Domain boundaries: FSQ assertion and suggestion policy only; model invocation delegates downward.
- Boundary models: private Pydantic model-facing assertion/suggestion outputs, shared FSQ assertion models, immutable suggestion results, and neutral engine request/results. Private output models remain in their owning service modules and are not exported or placed in a shared business-schema catalog.
- Dependency direction: business callers consume services; services consume public providers/engine APIs; neither providers nor engine depends back on services.
- Rationale: two focused inference services share configured model access without another service registry or persistence layer.

## Error Handling

Missing provider configuration raises `ConfigurationError`. A valid assertion maps `passed=true` to `status="passed"` and `passed=false` to `status="failed"`. Generation or output-validation failures return `AIAssertionResult(status="error", passed=False, ...)`; that boolean does not represent a valid negative evaluation. Error text is generic and safe. Neutral engine failures additionally retain category and an available safe reason in `metadata["engine_error"]`, alongside existing provider metadata. Provider refusal, incompleteness, configuration, authentication, rate-limit, and timeout categories are not relabeled as invalid JSON. Diagnostics expose no raw model content, parser/validator dumps, SDK objects, or credentials.

Case suggestions propagate neutral generation/validation failures instead of converting malformed model output into a configuration error or a successful empty analysis. Application retains `case.suggestion_failed`, completed Run/report truth, and candidate publication authority without expanded HTTP/CLI result fields. The neutral cause remains in the service/error chain.

Scoped resources close on every path. Cleanup does not mask a primary generation/validation failure, including an assertion failure already converted into an error result. Cleanup-only failures remain errors. Cancellation and interruption propagate rather than becoming business verdicts; there is no semantic retry or repair inference.

## Current Invariants

- Each evaluation/analysis makes one tool-free `Model.complete` request with a caller-owned output contract through the configured provider session, with no semantic retry, JSON repair, free-text fallback, Runner, action tool, handoff, or Provider configuration setting.
- Assertions use the selected provider/model without a separate override, prepare the existing prompt/context, read the supplied screenshot when present, and pass image bytes/MIME through neutral content. Missing screenshots retain text-only behavior.
- Assertions preserve public result fields, latency, artifact references, provider/model metadata, and available usage on valid results while distinguishing passed, failed, and error status. Assertion evidence is not a locator recovery mechanism or permission to mutate a Case.
- Suggestion analysis consumes parsed source Case data and bounded completed execution facts. Its prompt prohibits changing completed status/facts or initiating another run; the service cannot invoke UI actions. A missing nullable field is invalid, not an implicit absent candidate. Application owns candidate validation, source preservation, persistence, and publication rules.
- Public evaluation and analysis remain synchronous. Provider adaptation bridges asynchronous invocation with fresh loop-scoped resources and closes on every path. Service reuse does not reuse a closed async client.
- Readiness sends no inference, constructs no harness, and exposes safe verdict/message/action only.

## Verification Scope

Verify strict whole-document validation and required nullable fields through the engine conversion/parser boundary, three-way assertion outcomes with safe category/reason metadata, evidence/timing/usage preservation, suggestion immutability and candidate normalization, and scoped cleanup with primary-error/cancellation precedence. Neutral model-session doubles honor output contracts and return parsed values; they do not depend on production text fallbacks. Actual private service schemas participate in both pinned-client controlled-transport checks. Application integration verifies no candidate publication after invalid analysis and preservation of completed Run/report facts. Factories/readiness and caller imports use this public package without inference, extra actions, or Core/provider dependency cycles.