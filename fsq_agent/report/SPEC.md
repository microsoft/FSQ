# Module: report

## Purpose

Generate human-readable and machine-readable reports inside the selected workspace platform's unique direct run directory from dynamic LLM task results and strict-core evidence manifests, including the checked dynamic `verification_goal`, strict lifecycle phase summaries, structured capability provenance, AgentTool/CommonTool/PlatformTool execution metadata, replay metadata, sensitivity-safe previews, and provider-backed AI assertion verdict metadata. Provide stored report lookup and deterministic on-demand static HTML derivation from persisted Run facts.

Own the shared `fsq.report/v1` projection, deterministic comparison, and JSON/JUnit/portable-HTML/evidence-bundle exporters. Execution owns frozen conclusions and Core owns recovery; Report consumes persisted facts and normalized Models evidence, never reconstructs a live runtime or duplicates journal replay.

## Dependencies

- `models`: Uses `Task`, `AgentFinalOutput`, `ToolCallRecord`, `StepResult`, `VerificationResult`, `ReportArtifact`, `EvidenceBundle`, `AIAssertionResult`, and `ReportGenerationError`.

The report module consumes persisted event, result, and evidence data. It must not import `capabilities`, inspect decorators, or rebuild platform action catalogs. Reports may read historical events that contain compatibility labels such as `tool_origin="harness"`, but live capability executor kinds are only `common` and `driver`.

## Public Interface

Current `__init__.py` exports via `__all__`:

- `ReportGenerator`: Generates reports for completed task runs under the configured output runs directory.
- `RunReportService`: projects already resolved persisted Run inputs and recovered evidence, validates comparison/lineage, and exports through Models-owned options/results and sharing contracts. Application owns Workspace, relationship, artifact, and destination authorization.
- `EvidenceBundler`: Creates a manifest for evidence references supplied by execution steps, including paths or snapshots produced by capability execution.
- `FailureAnalyzer`: Classifies failures as success, tool usage error, semantic action unmet, execution issue, planning issue, verification issue, or a combined label when multiple rule-assisted signals are present.
- `CoreEvidenceReportGenerator`: Generates Markdown and JSON reports from one deterministic core `evidence-manifest.json` path.
- `resolve_report_path(runs_dir: Path, run_id: str, report_format: Literal["markdown", "json"] = "markdown") -> Path`: Resolves a stored LLM report (`report.md/json`) or strict-core report (`core-report.md/json`) for the requested run id. It returns exactly one matching path or raises `ReportGenerationError` when the report is missing or ambiguous.
- `generate_static_run_report(run_dir: Path, facts: Mapping[str, object]) -> Path`: Rebuilds `report.html` atomically from one already resolved direct Run directory and validated safe persisted facts. It performs no Workspace discovery, execution, inference, Provider/Driver call, live UI inspection, or browser opening.

The core evidence report API is:

```python
artifact = CoreEvidenceReportGenerator().generate_from_manifest(Path("runs/run-1/evidence-manifest.json"))
```

It writes `core-report.md` and `core-report.json` next to the manifest and returns `ReportArtifact(run_id=..., path=core-report.md, evidence_manifest_path=manifest_path)`.

For strict-core evidence generated from case lifecycle hooks, `CoreEvidenceReportGenerator` must surface persisted lifecycle metadata instead of requiring users to inspect raw manifest JSON. Markdown and JSON reports should distinguish:

- `onCaseStart`: before-case hook work, including nested `runCase` command steps triggered from start hooks and `runShell` start-hook steps.
- `case`: the root case's main command body.
- `onCaseComplete`: after-case hook work, including nested `runCase` command steps triggered from complete hooks and `runShell` complete-hook steps.

The strict-core JSON summary should include lifecycle counts by phase: total, passed, failed, and status. The Markdown summary should include the same lifecycle breakdown in a concise table. The Markdown steps table should include lifecycle phase, source case name or path, action label, step id, status, failure category, and error. Action labels should prefer persisted replay aliases such as `tapOn`/`launchApp` when available, fall back to capability names, and show hook actions such as `runCase` or `runShell` with safe target/command context. Nested hook case steps should be labeled under the hook phase that triggered them, not only under their child case body phase.

## Public Report And Export Invariants

Canonical frozen results, execution evidence, progress logs, and supported v1/legacy reports remain distinct sources with explicit provenance, conflicts, and availability. Transport success is not action success. Dynamic runtime summaries and pre-plans do not become real capability counts; helper calls remain distinct. Metrics retain measured unit/scope and null-with-reason semantics, do not count attempts/containers/capture time twice, and never estimate per-step tokens or whole-Run usage from main-only usage.

Evidence health follows recorded capture requirements without retroactively requiring optional historical evidence. Public gate and its error/failed/incomplete precedence follow Models while retaining the original primary action failure and frozen verification conclusion. Post-freeze processing failures alone do not change a trustworthy execution gate.

Deterministic snapshot comparisons identify before/after or explicit baseline/current, source artifact hashes, normalization algorithm, and completeness. Cross-Run comparisons require same-platform validated source occurrence or recording-command mapping; order/name/prose similarity is not correspondence. Missing, transformed, clipped, or non-comparable evidence is not unchanged. Qualified Run/artifact identities and namespaced bundle paths prevent collisions; independent Run outcomes remain visible.

JSON exports the complete public contract. JUnit emits one testcase per primary root Case/Goal invocation; steps/attempts/related Runs are details, not testcase counts. Passed/failed/error/incomplete gates map to pass/failure/error/error while retaining native statuses as properties. Portable HTML embeds escaped text and PNG evidence with inline CSS and fixed hash-CSP-protected controls, no network requests, eval, event-handler attributes, or active stored HTML/SVG/JavaScript. ZIP bundles contain the HTML, JSON, JUnit, selected sanitized inputs/evidence, inventory, and checksums; integrity does not prove authorship or anonymity.

Sharing transforms export copies only using selected qualified artifact IDs, literal text replacements, allowed optional-field removals, and validated raster masks. Immutable identity/outcome/gate/count/reference/digest/lineage facts cannot be changed; original and derived digests remain distinct. Profile-sensitive values are not reproduced in transformation logs. Optional digest-bound Case review declarations are explicitly attributed export-time user declarations, not independent approval or Run mutations.

One versioned resource policy applies to projection/export: at most eight related Runs plus one baseline, 256 KiB/256-rule share profiles, 16 MiB encoded/40 million pixel rasters, 512 KiB displayed snapshot text per artifact, 8 MiB inline text, 64 MiB raster content per report, and 512 MiB uncompressed bundles. Optional display omission/truncation is explicit and does not change source evidence or gate. Invalid identity, over-limit relationships/profiles, or bundle overflow fail rather than silently dropping selected files. Export verifies source snapshot/hash, containment, file constraints, and absent destination at write time; only explicit compatibility `report.html` rebuild may replace an existing derived file.

## Internal Structure

- `__init__.py`: Public exports only.
- `_generator.py`: Markdown and JSON report generation with minimal JSON fallback, typed agent output rendering, execution/verification report shaping, and `ToolCallRecord` reconstruction from structured capability events in `events.jsonl`.
- `_evidence.py`: Evidence manifest and bundle creation.
- `_core_evidence_report.py`: Markdown and JSON report generation from `EvidenceBundle` or a core `evidence-manifest.json` path, including strict lifecycle phase summarization when lifecycle metadata is present.
- `_resolver.py`: Stored report lookup for LLM `report.*` and strict-core `core-report.*` files.
- `_static_html.py`: Offline escaped HTML rendering, contained artifact projection, and atomic `report.html` persistence.
- `_run_report.py`: public projection and legacy input adaptation.
- `_comparison.py`: stable identity/lineage validation and normalized deterministic comparisons.
- `_export.py`: destination checks, sharing transformations, format export, bundles, and inventories.
- `_failure_analysis.py`: Failure classification helpers.
- `templates/`: Optional report templates.
- `SPEC.md`: Module design.

## Python Architecture

- Architecture level: 2 Simple Package.
- Public API: `RunReportService`, `ReportGenerator`, `EvidenceBundler`, `FailureAnalyzer`, `CoreEvidenceReportGenerator`, `resolve_report_path`, and `generate_static_run_report` exported from `__init__.py`.
- Internal modules: all `_*.py` files are private report implementation modules.
- Domain boundaries: report owns rendering, stored report lookup, evidence manifest report generation, and failure classification from persisted facts. It does not execute capabilities, read live device state, call providers, parse FSQ YAML for execution, or decide recording eligibility.
- Boundary models: task/result/final-output/evidence/report models and normalized tool call records come from `models`.
- Dependency direction: imports `models` only and must not import `application` or transport adapters. It consumes persisted JSON/JSONL files and paths supplied by Application or other authorized callers.
- Rationale: report generation is focused transformation from persisted data into Markdown, JSON, or static HTML output, so Level 2 is sufficient.

## Error Handling

If rich Markdown/JSON report generation fails after a task run, `ReportGenerator` attempts to write `report-fallback.json` with `run_id`, `task_id`, `status`, `summary`, and the rich report error. `ReportGenerationError` is raised only when both rich report generation and minimal fallback generation fail.

Stored report lookup raises `ReportGenerationError` when no report exists for the requested run id/format or when both LLM and strict-core report files exist for the same run id/format.

Static HTML generation requires enough trustworthy persisted metadata, report, evidence, or logs to avoid a misleading page. Optional missing artifacts render unavailable sections. Rendering or atomic persistence failure raises a safe report error and never changes authoritative Run metadata, result, or existing source artifacts.

## Current Invariants

- Markdown and JSON reports are part of the design because they are easy to inspect in CI and IDEs.
- JSON reports are structured by lifecycle concern: `task`, `agent_output`, `execution`, `verification`, and `failure_classification`. The `task` and `verification` sections should make the single checked dynamic `verification_goal` visible for LLM runs. The `agent_output` section contains the typed `AgentFinalOutput` when available. The `execution.tool_calls` collection contains normalized `ToolCallRecord` values for real AgentTool, CommonTool, and PlatformTool invocations reconstructed from run events. Tool origin is derived first from structured metadata (`agent_tool`, `common`, `platform`, `runtime`, capability name, platform/backend/owner, and compatibility `tool_origin` when present), not hard-coded tool-name sets. Runtime-only records such as progress events, pre-plan reconstruction, provider setup, and agent-runtime summaries are not represented as real tool calls. Step records use `source` for runtime/provenance labels rather than overloading it as a tool name. Failure classification may use both verification output and normalized real tool-call output previews so tool usage failures can be distinguished from planning failures.
- Current-run structured output is extracted from `agent_runtime.runner` steps, with verification provenance recorded as `agent_runtime.verifier`. Generic runtime failure metadata uses `agent_runtime_error`; provider-specific failure categories retain their meaning.
- Stored reports and events from supported releases remain historical facts, including their original runtime labels and generic error values. Lookup and static derivation do not normalize those stored names, modify authoritative source files, reconstruct a runtime, or change the Run verdict. Artifact references use the saved paths, independent of current runtime naming.
- Reports treat capability and AgentTool metadata as persisted execution evidence, not as live decorator state. Report generation must not depend on the module that originally declared a capability. Reports may display replay aliases from persisted `ReplayPolicy` metadata, but they must not expect `CapabilityDefinition.aliases` or per-capability schema strictness fields in persisted capability metadata. Automatic and explicit runner evidence uses normalized `ui_snapshot` artifacts across platforms, and reports render those artifacts uniformly.
- Reports must preserve AI assertion evidence emitted by backend PlatformTools. For Android/Web/Windows/macOS `assert_with_ai`/`assertWithAI`, reports should include the prompt summary, verdict status, explanation, provider/model metadata safe for display, latency/token diagnostics when safe, screenshot artifact references, and any evaluator error. Reports must not re-inspect screenshot pixels or include hidden model reasoning.
- Sensitive runtime-secret text input values must be redacted in reports. Reports may show safe metadata such as requested workspace secret name, text source type, allowlist/presence status, capability name, and replay alias, but never private values. Historical `get_runtime_secret` dependency events are not part of the target runtime-secret input path and need not be treated as active recording dependencies.
- Initial reports live under the Run; explicit exports use only an Application-authorized destination. Report does not discover Workspace roots or write arbitrary paths.
- Dynamic and strict reports keep compatible internal shapes and feed one public projection shared by CLI, Control Plane, portable HTML, and CI.
- Static HTML is a derived offline view rebuilt only on explicit request. It contains a Run overview, source/result/runtime summary, step timeline, sanitized logs, escaped UI snapshots, contained screenshot/evidence links, Run-local suggestions, candidate Case, and allowlisted artifact inventory where available. It uses inline presentation resources, no network resources or requests, a restrictive Content Security Policy, escaped persisted text, and only links that resolve inside the Run directory. Artifact HTML, SVG, or JavaScript is never embedded as active content. `report.html` is not authoritative evidence and is not written back into `run.json`.
- Failure analysis is rule-assisted. Provider-side incomplete Responses failures such as `response.incomplete` with `content_filter` must be classified as provider failures, not tool usage errors.
- Deterministic core execution reports should be generated from persisted evidence manifests rather than live runner objects. This keeps report generation replayable and allows reports to be regenerated after real-device runs.
- Regression comparison reports should be generated after execution from persisted strict and recovery manifests. This keeps self-healing auditable and prevents recovery from masking the original regression signal. AI assertion verdicts in strict evidence remain part of the strict result, while AI-assisted repair attempts belong only to separate recovery evidence.
