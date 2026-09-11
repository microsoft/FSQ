# Agent Instructions

This repository uses Spec-Driven Development for project development.

Root `SPEC.md` and relevant module `SPEC.md` files are the grounding truth for project code and project logic. They do not define the SDD workflow.

This file and `.github/skills/` control agent write authorization and SDD phase transitions. Project `SPEC.md` files must not duplicate or override those workflow rules.

Ordinary discussion, explanation, review, and planning are read-only. Do not automatically load or invoke repository workflow skills.

Before creating, modifying, renaming, or deleting project development files, require the user to explicitly invoke `/spec-driven <confirmed-design-document-path | direct-project-change-request>`. Project development includes project or module `SPEC.md` files, source, behavior-defining tests, runtime/build configuration, public interfaces, module ownership, dependency direction, and documentation of supported project behavior. A natural-language project edit request outside that explicit skill invocation is not authorization to write; stop and direct the user to `/spec-driven`.

`/requirements-to-design <request>` is an optional design aid that the user may explicitly invoke for any requested change, including workflow-control maintenance and local-only ignored files that do not require SDD. It produces a confirmed design document; for project development that requires SDD, that document is higher-quality `/spec-driven` input. It is never a prerequisite for modification.

A clear ordinary user request to create, modify, rename, or delete workflow-control files directly authorizes that maintenance without SDD. Workflow-control files are `AGENTS.md`, `CLAUDE.md`, `.github/copilot-instructions.md`, `.github/prompts/**`, and `.github/skills/**`. Ambiguous approval, discussion, or a skill name mentioned in prose is not authorization. If a workflow-control edit also changes project behavior, use project SDD.

A clear ordinary user request also authorizes local-only writes without SDD when every non-workflow path affected by the request is both untracked and ignored by Git. Verify both conditions with Git before writing; do not infer them from the path, user intent, or an ignore pattern alone. A tracked file does not qualify even if it matches an ignore rule. Changes to `.gitignore`, repository-local exclude rules, or any tracked project file follow the normal authorization rules. If any affected project-development path does not qualify, require `/spec-driven` for that project change.

During `/spec-driven`, determine whether the requested project change requires a SPEC delta. If it does, update relevant `SPEC.md` files and receive user confirmation before non-SPEC project files change. If the implementation only restores behavior already grounded in current SPEC, record the no-SPEC-delta evidence and proceed without an artificial SPEC edit.

Run repository workflow skills only through an explicit user-invoked slash command. `/requirements-to-design` is available for any requested change; `/spec-driven` remains project-development-only. This file is an agent workflow entry point, not a project specification; keep project and module requirements in `SPEC.md` files and detailed workflow procedures in `.github/skills/`.

New Python source files must start with:

```python
# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.
```

## FSQ Case Editing

Within already authorized Case edits, use the shared CLI as the format and validation authority:

1. Preserve Case identity and execution semantics outside the requested change; do not add Run provenance to Case YAML.
2. After creating or editing a Case, run `fsq case format <path> --check --json`.
3. Repair invalid fields using structured diagnostics. Do not delete steps, weaken assertions, or invent parameters to bypass validation.
4. For formatting-only differences, run `fsq case format <path> --write --json`, then repeat the check.
5. Review the final Git diff against the request. Report static validation separately from actual execution tests.

See [Case format reference](docs/case-format.md) for modes, exit codes, and machine fields. These instructions do not grant write authorization or change the SDD gates above.
