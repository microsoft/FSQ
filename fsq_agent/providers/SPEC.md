# Module: providers

## Purpose

Own FSQ supplier adaptation, observable GitHub Copilot authentication and authenticated model discovery, non-interactive runtime token refresh, real connection testing, and configured neutral model access. The module resolves Azure OpenAI and GitHub Copilot connection parameters from validated user-provider snapshots, owns Copilot protocol compatibility, endpoint selection, model-list parsing/filtering, and selected-model activation, and delegates inference-client construction to `agent_engine`.

The module centralizes provider behavior so the main agent loop, internal pre-planner, evidence-based verifier, and platform AI assertion evaluators reuse the same provider configuration, token cache behavior, model selection, and redaction policy.

## Dependencies

- `models`: Uses provider settings and `ConfigurationError`.
- `config`: Uses resolved `Settings`, loads the latest saved user provider for connection tests, and activates complete GitHub authentication results through public config operations.
- `agent_engine`: Uses public model/provider protocols, factories, neutral requests/results, and safe engine errors.
- HTTPX remains the supplier-authentication and model-discovery transport, not an inference SDK boundary.

The providers module must not depend on `ai_services`, `agent`, `tools`, `core`, `cli`, `report`, `knowledge`, `skills`, or `fsq`, and must not construct or consume OpenAI/Agents SDK objects.

## Public Interface

Current `__init__.py` exports via `__all__`:

- `ModelProviderFactory`: Builds supplier-configured sessions from resolved `Settings` for neutral model access.
- `ModelProviderSession`: Holds the configured supplier/model identity and safe metadata. `get_model()` returns the selected neutral `Model`; `complete(request)` and `complete_sync(request)` accept `ModelRequest` and return `ModelResult`; `close()` and `close_sync()` release owned neutral providers. Its optional test factory supplies the neutral provider protocol, never an SDK class.
- `prepare_model_provider_session(settings: Settings) -> ModelProviderSession`: Builds a configured session for readiness without sending a model request. For GitHub Copilot it may silently exchange a valid cached GitHub OAuth token when the provider token is absent or expired, but never starts device flow. For Azure it validates and constructs client configuration from the resolved user snapshot.
- `refresh_model_provider_session(settings: Settings) -> ModelProviderSession`: Refreshes provider-local runtime credentials at the beginning of a dynamic task without sending a live model request. For GitHub Copilot, it uses only a valid cached GitHub OAuth token to exchange and cache a fresh short-lived Copilot provider token and never starts device authentication. For Azure OpenAI, it validates and constructs client configuration from the resolved user snapshot.
- `build_model_provider_session(settings: Settings) -> ModelProviderSession`: Convenience factory for runtime construction. For GitHub Copilot it reads the user-level cached provider token and may silently refresh it from a valid cached OAuth token, but never starts device flow.
- `check_provider_readiness(settings)`: Validates non-interactive configured session preparation, returns safe normalized readiness facts, and closes any constructed session without inference.
- `request_github_copilot_device_code() -> GitHubDeviceCode`: Requests one GitHub device code with the existing explicit Copilot scopes and returns verification URI, user code, polling interval, and expiration without printing to a terminal or starting polling.
- `GitHubCopilotAuthorization`: Immutable provider-boundary value containing one completed GitHub OAuth/Copilot token exchange for short-lived in-memory use. Credential fields are excluded from representations and are never presentation models.
- `GitHubCopilotModel`: Immutable safe model-list value containing the exact model id and display name.
- `complete_github_copilot_device_flow(device_code: GitHubDeviceCode, *, cancel_requested: Callable[[], bool]) -> GitHubCopilotAuthorization`: Polls with GitHub's interval/slow-down semantics, checks cancellation, exchanges the OAuth token for Copilot plan/provider-token data, and returns an in-memory authorization without activating or writing Provider configuration.
- `list_github_copilot_models(authorization: GitHubCopilotAuthorization) -> tuple[GitHubCopilotModel, ...]`: Requests the authenticated plan endpoint's `/models` collection with bounded timeout and Copilot headers, preserves service order, removes duplicate ids, and returns only picker-enabled chat models whose ids are GPT major version 5 or later and whose id/name do not identify mini, nano, Codex, embedding, audio, realtime, image, search, transcription, or TTS specializations.
- `activate_github_copilot_authorization(authorization: GitHubCopilotAuthorization, *, model: str, user_config_root: str | Path | None = None) -> UserProviderConfig`: Activates the complete pending credentials with one non-empty selected model through `config`'s atomic GitHub replacement operation.
- `test_model_provider_connection(user_config_root: str | Path | None = None) -> ProviderConnectionTestResult`: Loads the latest saved provider, creates a fresh session, sends one fixed minimal prompt requesting a short deterministic acknowledgement, returns provider/model/elapsed duration after a valid response, and always closes the session.
- `ProviderConnectionTestResult`: Safe supplier/model identity and elapsed duration returned by the explicit connection test.

Public inference APIs do not accept SDK factories, arbitrary Responses kwargs, or raw vendor message dictionaries. Visual assertion and Case suggestion services and their factories/readiness are owned by `ai_services`, not re-exported here.

## Internal Structure

- `__init__.py`: Public exports only.
- `_factory.py`: Settings-based factory functions and `ModelProviderFactory` implementation.
- `_session.py`: Neutral provider lifecycle, selected-model access, safe supplier metadata, and synchronous/asynchronous invocation bridging.
- `_azure_openai.py`: Azure OpenAI connection configuration from resolved endpoint/model/API-key values and safe metadata.
- `_github_copilot.py`: Observable device-code request and cancellable polling, non-interactive cached token inspection/refresh under the user auth root, Copilot token exchange, plan detection, endpoint selection, authenticated model discovery/filtering, selected-model activation, and request/header/timeout compatibility.
- `_connection_test.py`: Fresh-session minimal neutral model request, nonempty response validation, elapsed-time measurement, safe result shaping, and guaranteed cleanup.
- `SPEC.md`: Module design.

## Python Architecture

- Architecture level: 2 Simple Package.
- Public API: session/factory types, non-interactive preparation/refresh/build/readiness helpers, GitHub authorization/discovery/activation operations and values, and connection testing exported from `__init__.py`.
- Internal modules: `_factory.py`, `_session.py`, `_azure_openai.py`, `_github_copilot.py`, and `_connection_test.py` are private implementation files.
- Domain boundaries: providers owns supplier authentication, connection policy, and configured model access. Config owns files and active-provider persistence; `agent_engine` owns inference protocols; `ai_services` owns assertion/suggestion business rules.
- Boundary models: settings come from public `config`/`models`, inference values from `agent_engine`, and supplier-specific protocol/result records remain immutable public facts where required.
- Dependency direction: providers may depend on public `models`, `config`, and `agent_engine`; it must not import business services, agent, entry-layer, execution, report, or frontend modules.
- Rationale: two provider integrations share a narrow session abstraction and protocol helpers, but no additional application/service layer is justified.

## Error Handling

Provider preparation, authentication, refresh, and test failures raise `ConfigurationError` from `models` with non-secret context such as provider name, endpoint family, token-cache path, safe HTTP category/status, or Copilot plan value. Errors never include API keys, OAuth tokens, Copilot API tokens, authorization headers, cookies, or user/workspace prompt content.

GitHub device authorization distinguishes request failure, polling/network failure, slow-down, expiration, denial, cancellation, token exchange failure, and unknown plan. GitHub model discovery distinguishes authorization, timeout, HTTP, malformed-envelope, and unusable-model failures without exposing raw response bodies or credentials. Azure validation distinguishes incomplete saved values, invalid base URL shape, authorization, unavailable model/deployment, rate limiting, malformed response, timeout, and client construction failure.

Non-interactive readiness and runtime construction never start device polling. They may call token exchange only when a valid cached OAuth token exists and the short-lived provider token is missing or expired. Readiness helpers do not send model requests; only the explicit connection-test operation sends a live model inference request. Authenticated model discovery requests provider metadata only.

Provider readiness is independent from Workspace platform Target/Runtime diagnosis. It reports selected Provider configuration, model, and local authentication availability without exposing saved values. Missing provider credentials produce a configuration failure, not a silent fallback. Connection errors are mapped from neutral engine categories/status information, not SDK exception classes.

## Current Invariants

- Provider construction belongs in `providers`, not `agent`, because the main runner, pre-planner, verifier, and platform AI assertion evaluator need the same Azure/Copilot behavior.
- `providers` may depend on `config` because it consumes resolved `Settings`, but `config` must not depend on `providers`.
- The resolved `openai_agents.provider` and provider model are the provider/model source for AI assertions. There is no separate AI assertion model override.
- All configured providers use neutral model access with the non-empty model stored in the user-provider record. There is no default provider or fixed GitHub model; the private engine backend owns the Responses protocol.
- GitHub keeps the existing explicit OAuth scopes, token exchange, plan detection, plan-specific endpoints, Copilot headers, and expiration behavior, but token files live under `~/.fsq/auth`. Runtime surfaces never start device authentication.
- Azure endpoint, model/deployment name, and API key come from the resolved user-provider snapshot, not fixed environment variables.
- Readiness proves local configuration/token readiness only. The explicit connection test is the sole setup surface that sends a live minimal model request.
- Device-flow operations do not print or prompt. Control Plane owns background transaction state, offered-model allowlisting, expiration, and presentation; `providers` owns protocol timing, cancellation checks, token exchange, model discovery/filtering, and selected-model activation through `config`.
- Provider sessions own the scope of their neutral model providers. The engine owns concrete clients. Async access reuses a provider only on its owning event loop; close releases it without exposing SDK objects.
- Each synchronous call creates, invokes, and closes a fresh neutral provider on the same event loop, including calls made while another loop is running. It never reuses a client from a closed or foreign loop.
- Cleanup failures do not overwrite a primary invocation failure or cancellation. No wrapper semantic retries or full-run restarts are added.
- Model requests contain neutral text/image input and return neutral text/usage/completion facts. Model inference does not trigger provider login or refresh; those operations follow the existing explicit preparation policies.
- `core` must not import `providers`. Platform harnesses receive an evaluator object structurally and call it through an evaluator protocol owned by `core` or supplied by entry-layer code.
- Provider diagnostics in events and reports should include provider name, model name, endpoint family, and safe status details, but never secret values.
