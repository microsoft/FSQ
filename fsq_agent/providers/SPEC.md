# Module: providers

## Purpose

Own shared model provider construction, local provider setup/auth readiness, bounded provider diagnosis, and provider-backed model call access for fsq-agent. The providers module builds Azure OpenAI and GitHub Copilot OpenAI-compatible clients from validated, resolved settings, owns provider authentication and cache interpretation, exposes diagnosis without model inference, and exposes direct Responses-style model access for provider-backed AI assertion evaluators.

The module centralizes provider behavior so the main agent loop, internal pre-planner, evidence-based verifier, and platform AI assertion evaluators reuse the same provider configuration, token cache behavior, model selection, and redaction policy.

## Dependencies

- `models`: Uses `OpenAIAgentsSettings`, `WorkspaceSettings`, `AIAssertionRequest`, `AIAssertionResult`, and `ConfigurationError`.
- `config`: Uses the resolved `Settings` aggregate as provider factory input.

The providers module must not depend on `agent`, `tools`, `core`, `cli`, `report`, `knowledge`, `skills`, or `fsq`.

## Public Interface

Current `__init__.py` exports via `__all__`:

- `ModelProviderFactory`: Builds provider sessions from resolved `Settings` for OpenAI Agents SDK runs and direct evaluator calls.
- `ModelProviderSession`: Owns the lifecycle of one configured provider client/session and exposes provider metadata, model name, an Agents SDK provider object factory, and direct Responses-style model invocation for evaluator-style calls.
- `AIAssertionEvaluator`: Provider-backed evaluator that satisfies `core`'s synchronous evaluator protocol: it accepts an `AIAssertionRequest`, calls the configured model through a `ModelProviderSession`, and returns an `AIAssertionResult`.
- `prepare_model_provider_session(settings: Settings, *, interactive_auth: bool = True) -> ModelProviderSession`: Builds a configured provider session for setup/readiness use without sending a live model request. For GitHub Copilot, `interactive_auth=True` may run device-code authentication with explicit OAuth scopes when no valid cached GitHub OAuth token exists, then exchange and cache a short-lived Copilot provider token for runtime use; `interactive_auth=False` must not start device-code authentication, but may use a valid cached GitHub OAuth token to refresh a missing or expired provider token. For Azure OpenAI, it validates and constructs local client configuration from fixed environment values.
- `refresh_model_provider_session(settings: Settings) -> ModelProviderSession`: Refreshes provider-local runtime credentials at the beginning of a dynamic task without sending a live model request. For GitHub Copilot, it must use only a valid cached GitHub OAuth token to exchange and cache a fresh short-lived Copilot provider token, regardless of whether the previous provider token is still valid, and it must not start device-code authentication. For Azure OpenAI, it validates and constructs local client configuration from fixed environment values.
- `build_model_provider_session(settings: Settings) -> ModelProviderSession`: Convenience factory for runtime construction. For GitHub Copilot, runtime construction must first read the cached Copilot provider token produced by `prepare_model_provider_session(..., interactive_auth=True)`; when the provider token is missing or expired, it may use a valid cached GitHub OAuth token to silently exchange and cache a fresh provider token, but it must not start device-code authentication.
- `build_ai_assertion_evaluator(settings: Settings) -> AIAssertionEvaluator`: Convenience factory used by entry-layer code when a platform harness needs provider-backed AI assertion. For GitHub Copilot, it follows the same non-interactive provider-token read/refresh rule as `build_model_provider_session`.
- `ProviderDiagnosticService`: Stable provider-neutral diagnostic boundary. `probe(settings, timeout_seconds=5.0, progress_sink=None)` emits immediate check progress and returns ordered sanitized `DiagnosticProbeResult` values for local settings, cache shape/expiry, and bounded endpoint reachability without device-code authentication, token refresh, or model inference. `refresh_cached_copilot_provider_token(settings, progress_sink=None)` emits repair progress and is an explicit safe-repair operation that succeeds only from a valid cached GitHub OAuth token in an existing workspace and never starts interactive authentication.

Current usage shape:

```python
session = build_model_provider_session(settings)
setup_session = prepare_model_provider_session(settings, interactive_auth=False)
refreshed_session = refresh_model_provider_session(settings)
provider = session.create_agents_provider(openai_provider_type=OpenAIProvider, async_openai_type=AsyncOpenAI)
result = await session.invoke_responses(messages=[...], response_format=...)
evaluator = build_ai_assertion_evaluator(settings)
assertion = evaluator.evaluate(request)
await session.close()
```

Concrete type annotations may use `Any` for OpenAI Agents SDK classes at the boundary so importing this module does not require the SDK unless a provider session is constructed for runtime use.

## Internal Structure

- `__init__.py`: Public exports only.
- `_factory.py`: Settings-based factory functions and `ModelProviderFactory` implementation.
- `_session.py`: `ModelProviderSession` lifecycle wrapper, provider metadata, Agents SDK provider construction, direct Responses-style invocation, and cleanup.
- `_azure_openai.py`: Azure OpenAI client construction from fixed environment-backed endpoint/model/API-key values, endpoint normalization assumptions, and provider metadata.
- `_github_copilot.py`: GitHub device-code auth with explicit OAuth scopes, non-interactive cached provider-token inspection, GitHub OAuth token cache loading/saving, Copilot provider-token cache loading/saving, non-interactive provider-token refresh from cached GitHub OAuth tokens, Copilot token exchange, plan detection, endpoint selection, request/header/timeout compatibility, and provider metadata.
- `_ai_assertion.py`: `AIAssertionEvaluator` implementation and model-response parsing into `AIAssertionResult`.
- `_diagnostics.py`: ProviderDiagnosticService, non-mutating cache inspection, bounded endpoint reachability, sanitization, and explicit Copilot cache-refresh repair.
- `SPEC.md`: Module design.

## Python Architecture

- Architecture level: 2 Simple Package.
- Public API: provider/session factories, evaluator, and `ProviderDiagnosticService` exported from `__init__.py`.
- Internal modules: provider adapters, cache/auth mechanics, diagnostics, and evaluator implementation remain private `_*.py` modules.
- Domain boundaries: providers owns credentials, endpoint/client behavior, bounded provider probes, and explicit cache refresh; callers own diagnostic aggregation, prompts, and platform behavior.
- Boundary models: settings come from config; shared diagnostic and AI assertion records come from models.
- Dependency direction: providers imports config and models only among project modules and must not import doctor, CLI, agent, core, or other entry modules.
- Rationale: provider selection and diagnostics are focused factory/service behavior; no richer domain or persistence abstraction is justified.

## Error Handling

Provider setup failures raise `ConfigurationError` from `models` with non-secret context such as provider name, missing environment variable name, endpoint shape, token-cache path, HTTP status code, or Copilot plan value. Provider errors must never include API keys, OAuth tokens, Copilot API tokens, authorization headers, cookies, or model prompt content containing runtime secrets.

GitHub Copilot device-code authorization failures should distinguish request failure, polling failure, expired device code, authorization denial, token exchange failure, and unknown plan. Azure OpenAI validation failures should distinguish missing fixed environment variables, invalid base URL shape, and client construction failure.

Non-interactive Copilot readiness checks and runtime construction must not start device-code polling. They may call the Copilot token exchange endpoint only when a valid cached GitHub OAuth token is available and the short-lived provider token is missing or expired. They must fail clearly when neither a valid cached Copilot provider token nor a valid cached GitHub OAuth token exists. Provider setup/readiness helpers must not send Responses API model requests.

Doctor diagnosis is stricter about side effects than runtime construction: `ProviderDiagnosticService.probe` never refreshes caches. It reports an eligible refresh repair when a valid cached GitHub OAuth token can repair a missing/expired provider token. Azure reachability proves URL/DNS/TLS/HTTP accessibility only and does not claim deployment existence, authorization, quota, or inference success.

Provider diagnostics emit start/completion events around cache inspection and endpoint reachability. They must emit `check_started` before network access so text users receive feedback during bounded waits.

Direct evaluator invocation failures should return or raise structured diagnostics that entry-layer code can convert into failed `HarnessActionResult` values. Missing provider credentials for an explicitly authored `assertWithAI` step should produce a configuration failure, not a silent assertion pass or fallback path.

## Current Invariants

- Provider construction belongs in `providers`, not `agent`, because the main runner, pre-planner, verifier, and platform AI assertion evaluator need the same Azure/Copilot behavior.
- `providers` may depend on `config` because it consumes resolved `Settings`, but `config` must not depend on `providers`.
- The resolved `openai_agents.provider` and provider model are the provider/model source for AI assertions. There is no separate AI assertion model override.
- All configured providers use the Responses API. GitHub Copilot mode is the default provider path, uses Copilot model `gpt-5.5`, and must keep device-code OAuth, explicit OAuth scopes for Copilot token exchange, token cache under the fsq-agent workspace, short-lived Copilot provider-token caching, dynamic task startup provider-token refresh from cached GitHub OAuth tokens, plan-specific endpoint selection, and Copilot headers. Dynamic task startup refreshes the provider token once from the cached GitHub OAuth token before pre-plan begins. Runtime surfaces including pre-plan, dynamic execution, verification, and provider-backed AI assertion never start device-code authentication; after startup refresh they use the cached provider token.
- Azure OpenAI remains available when config resolves `azure_openai`, but endpoint, model/deployment name, and API key are supplied by fixed environment variables resolved by `config`: `AZURE_OPENAI_BASE_URL`, `AZURE_OPENAI_MODEL`, and `AZURE_OPENAI_API_KEY`. Provider diagnostics may name those variables when missing but must never include values.
- Provider setup readiness is local readiness only. It may perform GitHub/Copilot authentication and token exchange, but it must not call model inference endpoints or prove deployment authorization through a live request.
- The providers module owns provider client lifecycle so callers do not leave `AsyncOpenAI` clients open.
- `AIAssertionEvaluator.evaluate` is synchronous to satisfy the current `core` harness protocol. It may internally bridge to asynchronous provider calls, but that detail must not leak into `core`.
- OpenAI Agents SDK runtime objects are not shared models. Provider sessions may construct SDK objects, while `models` stores only serializable settings, requests, results, and metadata.
- `core` must not import `providers`. Platform harnesses receive an evaluator object structurally and call it through an evaluator protocol owned by `core` or supplied by entry-layer code.
- AI assertion evaluator output is evidence, not a recovery mechanism. It must not perform locator fallback, mutate testcases, or convert unrelated strict-core failures into passes.
- Provider diagnostics in events and reports should include provider name, model name, endpoint family, and safe status details, but never secret values.
- Provider diagnostic metadata may include cache path, expiry state, endpoint family, sanitized URL origin, HTTP status class, and Copilot plan, but never tokens, keys, headers, cookies, credential-bearing URL components, secret lengths, fingerprints, or response bodies.
