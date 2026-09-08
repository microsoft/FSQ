# Changelog

All notable changes are documented here. FSQ follows Semantic Versioning after publication; during the 0.x alpha period, minor releases may change public behavior with release-note guidance.

## [Unreleased]

### Added

- Initial open-source release preparation.

### Changed

- FSQ business runtime and settings use SDK-neutral names: `DefaultCodingAgentRuntime`, `AgentRuntimeSettings`, `AgentPromptConfig`, and `agent_runtime`. Custom Python integrations and low-level runtime YAML must use the canonical names; the old SDK-prefixed exports and configuration key are not aliases.
- New dynamic Run provenance uses `agent_runtime.runner` and `agent_runtime.verifier`, with `agent_runtime_error` for generic runtime failures. External scripts matching old output strings need to adopt the new values. The actual OpenAI SDK backend and supplier identifiers are unchanged.
- Existing local data from v0.1.0 and later retains its original formats, labels, contents, and artifact paths. This naming update requires no Workspace reinitialization, configuration edits, or reauthentication; historical queries do not rewrite authoritative data.

### Removed

- Removed the legacy Playground browser application, standalone HTTP server, Python APIs, and packaged frontend assets. Use `fsq ui` for the supported Control Plane browser workflows; raw YAML dynamic execution, lifecycle-hook editing, completed-Run loading, and automatic Goal Case publication are not carried forward.

## [0.1.0] - Unreleased

### Added

- Evidence-first AI exploration and deterministic Case replay across Web, Android, Windows, and macOS.
- Local Workspace configuration, readiness diagnostics, user-level Provider configuration, Run queries, redacted logs, and offline HTML reports.
- Local Control Plane for Workspace, Provider, Case, Run, and evidence workflows.
- GitHub Copilot and Azure OpenAI Provider configuration.
- Wheel and source distributions containing all supported Python platform dependencies and compiled frontend assets.

### Stability

- This is an alpha release. Public APIs and Case authoring details may evolve before 1.0.

[Unreleased]: https://github.com/microsoft/FSQ/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/microsoft/FSQ/releases/tag/v0.1.0
