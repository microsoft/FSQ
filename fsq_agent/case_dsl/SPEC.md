# Module: case_dsl

## Purpose

Canonically load, validate, normalize, and serialize FSQ AI Test DSL Cases, lifecycle metadata, goal-only Cases, replay input, and deterministic commands, then convert commands into executable steps using a supplied capability registry snapshot. It does not execute Cases or hooks, allocate Case identities, or publish files.

## Dependencies

- `models`: canonical Case, lifecycle, capability snapshot, executable-step, parameter, and error contracts.

The package must not import Core, Capabilities, Execution, Application, adapters, drivers, harnesses, providers, or tools.

## Public Interface

`__init__.py` exports `FSQ_CASE_SUFFIX`, `FsqCaseLoader`, `FsqCaseValidator`, `FsqCaseSerializer`, `FsqExecutableStepAdapter`, and `is_fsq_case_file`. The legacy `fsq` package forwards these exact objects and preserves existing private module identity where repository callers observe it.

`FsqCaseLoader` accepts file paths and in-memory YAML with a diagnostic source path through `load_case` and `load_text`. `FsqCaseValidator.validate(case)` validates a parsed Case against its supplied registry snapshot without persistence or execution. `FsqCaseSerializer.normalize(case)` returns a canonical Case without mutating the input; `serialize(case)` validates and returns canonical UTF-8 bytes. Serializer and step adapter share command validation and parameter normalization rather than maintaining separate rules.

## Internal Structure

- `_loader.py`: YAML parsing, Case shape and lifecycle validation, goal-only normalization, and discovery.
- `_step_adapter.py`: registry-backed command resolution, parameter validation, and executable-step conversion.
- `_validation.py`: shared static Case and command validation and model-backed parameter normalization.
- `_serializer.py`: deterministic Case document construction and YAML byte serialization.

## Python Architecture

- Architecture level: Level 2 Simple Package.
- Public API: loader, validator, serializer, step adapter, suffix, and filename predicate.
- Internal modules: `_loader.py`, `_validation.py`, `_serializer.py`, and `_step_adapter.py`.
- Domain boundaries: deterministic Case syntax, validation, and normalization only.
- Boundary models: shared values come from `models`.
- Dependency direction: depends only on public Models contracts.
- Rationale: parsing and normalization are focused stateless behavior.

## Error Handling

Invalid YAML, schema versions, lifecycle metadata, command shapes, aliases, replay support, and parameter payloads raise safe `ConfigurationError` values before execution. Goal-only Cases remain valid.

Diagnostics identify source path, zero-based command index or metadata field path, and stable error code without echoing input values or raw exception bodies. Duplicate YAML keys, recursive aliases, and values without a deterministic supported representation are rejected instead of silently losing data.

## Canonical Case Output

- Output consists of a metadata mapping followed by the ordered command-list document, including `[]` for goal-only Cases. Each command uses its registry replay alias and an object payload, including `{}` for parameterless commands. Unsupported commands fail validation.
- Metadata uses public aliases in this order: `schemaVersion`, `name`, `description`, `platform`, `appId`, `url`, `tags`, `env`, `properties`, `onCaseStart`, `onCaseComplete`; supported extra metadata follows in lexical key order. Known model fields, including nested parameter fields, use model declaration order. Model field ordering is an output contract.
- Model validation resolves defaults before serialization. Non-null effective defaults are emitted, including false, zero, empty strings, lists, and mappings. A null model field is omitted only when absence has the same meaning; required nullable values and nulls inside free data are preserved. Input field presence does not change canonical bytes for equivalent values.
- Free mappings use lexical string-key order recursively; unsupported non-string keys fail explicitly. Lists retain order and duplicates. Lifecycle hook fields serialize as ordered lists of public hook mappings, preserving action order inside each mapping; hook action mappings are not sorted because their order is executable behavior.
- Legacy runtime-secret text references normalize to `text` plus `textType: runtimeSecret`; implicit literal text normalizes to explicit `textType: literal`. Secret names remain references and are never resolved. Valid runner-owned `timeout` is retained after capability parameter fields and remains separate from driver parameters; an explicit invalid timeout is a validation error.
- Canonicalization preserves Case identity, description, authored user data, runtime-secret references, lifecycle behavior, and effective execution parameters. It never adds or removes actions, weakens assertions, trims action text, or rounds numeric values to conceal a difference. Existing recording metadata is preserved as user data by formatting; Run-metadata separation is a recording policy, not a formatter migration.
- YAML uses UTF-8 without a BOM, LF line endings, two-space indentation with indented block sequences, Unicode text, deterministic scalar quoting, no anchors or aliases, one `---` between documents, no final document-end marker, and exactly one final newline. Comments and authored presentation are not retained by canonical serialization.
- Serialization is idempotent after reloading its output. Equivalent Cases under the same schema and capability contracts produce identical bytes independent of input mapping order and equivalent default/null spellings.

## Static Validation Boundary

Validation covers the selected document's schema, metadata, hook syntax, active platform command/replay support, parameter models, and timeout fields. It does not inspect live UI, load Workspace configuration, check credentials, resolve `runCase` files, inspect `runShell` scripts, or determine runtime readiness. Referenced-file existence, recursion, containment, and config-level hooks remain Execution preflight responsibilities.

Web Cases use the same Models-owned locator/action contract as dynamic invocation. Ref-bearing targets, snapshot-description strings, incompatible locator bags, and session-local selector engines are rejected before execution without stripping fields, guessing a locator, or enabling a legacy mode. Ordinary application text/URLs containing the word `ref` are not themselves locator refs.

Canonicalization preserves page aliases, frame/container paths, ordered filters and explicit first/nth steps, and event expectations through the existing model-backed serializer. It never replaces a collection rule with an observed item identity. Live selector uniqueness remains a Driver check; static validation performs neither browser access nor locator normalization.

## Verification Scope

Verification proves deterministic bytes, reload/serialize idempotence, model-aware null/default behavior, preservation of ordered hooks and steps, timeout and runtime-secret semantics, shared generation/formatting behavior, safe validation failures, and no execution or input mutation during static operations.

## Current Invariants

- `*.fsq.yaml` remains canonical and `*.codex.yaml` retains its current warning compatibility.
- Registry snapshots determine active commands and parameter validation.
- Lifecycle hooks remain metadata; path resolution, recursion, shell execution, cancellation, and failure policy belong to Execution.
- Runtime-secret references remain unresolved safe names until Core execution.
- Relocation does not alter models, executable steps, warnings, exceptions, or Case behavior.
