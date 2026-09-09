# FSQ Case Format

An FSQ Case is UTF-8 YAML with the `.fsq.yaml` suffix. The current schema identifier is `fsq.ai-test/v1`. The following independent minimum example opens TodoMVC and verifies its heading:

```yaml
schemaVersion: fsq.ai-test/v1
name: TodoMVC smoke test
platform: web
---
- startBrowser
- navigateTo:
    url: https://todomvc.com/examples/react/dist/
- assertVisible:
    target: TodoMVC heading
    locator:
      role: heading
      name: todos
    optional: false
- closeBrowser
```

For a complete recorded workflow with text entry, clicks, and final-state assertions, see [`examples/web/example-domain.fsq.yaml`](../examples/web/example-domain.fsq.yaml).

The first YAML document contains metadata. The optional second document contains ordered commands. Unknown fields, unsupported schema versions, malformed commands, and platform mismatches fail validation.

Commands execute in authored order through the selected platform Harness. Evidence and the authoritative result are written to one Run directory; the source Case is not modified. With `--suggest`, FSQ still executes only once. Later AI analysis cannot operate the UI or rewrite the result.

Prefer semantic roles, accessible names, resource identifiers, and stable application-owned attributes. Keep secrets and machine-specific executable paths out of Cases. Treat generated candidate Cases as proposals requiring review.

## Canonical formatting and static checks

All generated and saved Cases use the same model-backed validator and deterministic serializer as the CLI:

```bash
fsq case format example.fsq.yaml --check --json
fsq case format example.fsq.yaml --diff --json
fsq case format example.fsq.yaml --write --json
```

Choose one mode; the default is `--check`. Paths are explicit and relative to the current directory. No Workspace, Provider login, browser, or device is required. Validation checks the document, platform command schemas, parameters, and lifecycle syntax. It does not resolve referenced Case/script files, check credentials, or prove successful execution.

`--write` validates before atomically rewriting the named file; invalid inputs remain unchanged. Already canonical files are not rewritten. Formatting replaces comments and authored layout with canonical YAML but preserves Case identity, historical metadata, ordered operations, and effective parameters. There is no implicit migration or directory traversal.

Canonical YAML has two documents, fixed metadata/model field order, sorted free mappings, and unchanged list and lifecycle-action order. Omissible null model fields are excluded; effective non-null defaults and meaningful empty/false/zero values are retained. Free-data nulls are preserved. Text uses UTF-8, LF, consistent indentation and quoting, no anchors, and one final newline.

JSON uses the standard FSQ machine envelope. Its `result` contains:

| Field | Meaning |
|---|---|
| `valid` | The input passed document-local static validation. |
| `formatted` | The resulting on-disk file is canonical. |
| `changed` | This invocation wrote different bytes. |
| `needs_formatting` | The original valid input differed from canonical bytes. |
| `diagnostics` | Stable error code, safe reason, file, optional zero-based step index, and field path. |
| `diff` | Unified diff for `--diff`; a JSON string rather than separate stdout text. |
| `validation_scope` | Explicit limits of the static check. |

Exit codes: `0` canonical or successfully written; `1` valid but formatting required in check/diff mode; `2` invalid Case, missing file, unsupported platform, or usage error; `5` I/O, concurrent-edit conflict, or internal failure; `130` interrupted. Invalid inputs have `valid=false`, `formatted=false`, and `changed=false`.

Coding agents should check after each authorized edit, fix diagnostics without changing unrelated semantics, apply formatting if needed, recheck, and review the final Git diff. Static success and execution-test success must be reported separately.

Generated Goal Cases use an explicit stable name (`fsq case create --name NAME`) or `case-` followed by a deterministic SHA-256 of the platform and normalized Goal. Run provenance and draft status are stored in `recording.json`; Control Plane displays draft status during save. Publication reuses identical bytes and returns `case.publication_conflict` for a different existing Case, preserving both the existing file and the Run-local candidate. Ordinary formatting neither renames historical Cases nor removes their recording metadata.
