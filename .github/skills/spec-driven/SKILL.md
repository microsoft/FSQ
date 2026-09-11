---
name: spec-driven
description: "Required project modification workflow. Use when the user explicitly invokes /spec-driven with a confirmed design document path or direct project change request."
argument-hint: "Confirmed design path or direct project change request"
user-invocable: true
disable-model-invocation: true
---

# Spec-Driven Development

Turn an explicitly supplied confirmed project design document or direct project change request into SPEC-grounded implementation, verification, and independent audit. This user-invocable skill is the required `/spec-driven` project write entry point; `/requirements-to-design` is optional upstream input refinement.

## Invocation Gate

Run this skill only when the user explicitly invokes `/spec-driven` with either a confirmed project design document path or a direct project change request. Do not infer invocation or skill input from ordinary discussion, editor state, a natural-language project edit request outside the skill invocation, a skill-name mention, or prose approval. If the explicit invocation or a non-empty input is absent, stop without writing and ask the user to invoke `/spec-driven <confirmed-design-document-path | direct-project-change-request>`.

This skill does not handle workflow-control-only maintenance. If the supplied input changes only `AGENTS.md`, `CLAUDE.md`, `.github/copilot-instructions.md`, `.github/prompts/**`, or `.github/skills/**`, stop and explain that a clear ordinary edit request is sufficient.

## Core Rule

`SPEC.md` files are the source of truth for project implementation and must describe current project or module facts.

- Root `SPEC.md` owns current project-wide architecture, module navigation, dependency diagrams, project development constraints, and project implementation invariants.
- Module `SPEC.md` files own current module contracts, public interfaces, internal structure, dependencies, error handling, architecture level, and current invariants.
- Design documents, implementation notes, migration history, removed behavior, future roadmap, and detailed test matrices are not SPEC content unless they describe a currently supported compatibility behavior or a current verification obligation.

Project SPEC files must not define, duplicate, or override the SDD workflow. Agent workflow files may consume project SPEC as audit input, but they must not make SPEC the authority for how SDD is invoked or executed.

Project implementation may start only after required `SPEC.md` changes are reviewed and confirmed, or after a no-SPEC-delta decision is recorded with concrete existing-SPEC and defect evidence. If current SPEC does not ground the intended supported behavior, a SPEC delta is required.

## SPEC Hygiene Rule

Before writing or updating any `SPEC.md`, apply this filter:

- Keep: current behavior, current public API, current module ownership, current dependency direction, current configuration surface, current error semantics, current architecture level, and current invariants that constrain implementation.
- Keep as compatibility facts only when true in code: legacy input shapes, compatibility aliases, obsolete keys that are actively rejected, and supported migration shims. Write them as present-tense facts, not as history.
- Move out of SPEC: why a decision was made, discarded alternatives, design-process narrative, implementation plan, future platform ideas, planned signatures, target-state wording, removed behavior that code no longer accepts, and exhaustive test case lists.
- Avoid process markers such as `target`, `planned`, `future`, `after this change`, `this SPEC cycle`, `first batch`, `transitional`, `removed`, `previously`, and `during migration` unless the sentence is documenting a current compatibility fact or current rejection behavior.
- If a module SPEC starts to become a reference manual, keep only ownership and invariants in SPEC and move tables, examples, endpoint catalogs, or command catalogs to a reference document.

## Required Flow

```text
explicit /spec-driven <confirmed-design-document-path | direct-project-change-request>
  -> resolve and clarify the input
  -> read current SPEC and implementation evidence
  -> determine whether a SPEC delta is required
  -> if required: update root/module SPEC.md and get user confirmation
  -> if none: record existing-SPEC and defect evidence
  -> implement against confirmed SPEC.md
  -> run verification
  -> run the first complete spec-implementation-audit
  -> batch-fix complete finding set
  -> from round 2, audit only the previous round's finding repairs
  -> final report
```

Do not redirect a direct project change request to `/requirements-to-design`; that design phase is optional. Clarify ambiguous direct input within `/spec-driven` before deciding the SPEC delta or writing project files.

## Input Resolution

If the input resolves to a design document path, require the document to exist and be user-confirmed, then read it before deciding the SPEC delta. Design documents normally use:

```text
docs/superpowers/specs/YYYY-MM-DD-<topic>-design.md
```

If the input is not a design document path, treat the complete prompt argument as the direct project change request. Read enough current implementation, tests, and local documentation to remove material ambiguity. Ask focused questions when two materially different implementations remain possible, but do not require a separate design document.

A design document or direct request is input to the SPEC-delta decision, not final project implementation authority. Project code must be implemented against current confirmed `SPEC.md` files.

When translating a design document into SPEC updates, copy only the resulting current contract. Do not copy the design process, rejected options, implementation sequence, temporary target state, or historical narrative into SPEC.

## SPEC Delta Decision

Before modifying non-SPEC project files, read the root and relevant module specs plus enough implementation and verification evidence to classify the request.

A no-SPEC-delta path is allowed only when all of these are true:

- Current SPEC already grounds the intended supported behavior and remains accurate after the repair.
- The request restores implementation conformance rather than adding or changing a supported contract.
- Public interfaces, configuration semantics, module ownership, dependency direction, architecture level, and the set of supported behaviors remain unchanged.
- The defect is demonstrated by concrete implementation evidence, a failing focused test, or a reproducible behavior mismatch.

Record the no-SPEC-delta decision before implementation with precise SPEC references, defect evidence, and the boundaries expected to remain unchanged. A user label such as `bugfix` or `no-spec-delta` is not proof and does not control this decision.

If any condition is unproven, current SPEC is missing or wrong, or the requested behavior conflicts with SPEC, a SPEC delta is required. Update the relevant current-fact specs and get user confirmation before modifying non-SPEC project files. Never add artificial SPEC text solely to manufacture a delta.

## Python Architecture Integration

For Python projects or Python modules, apply the sibling `python-architecture` rules layer before:

- Choosing module ownership.
- Writing or updating module `SPEC.md` files.
- Implementing code.
- Running the consolidated project implementation audit.

When this bundle is installed under `.github/skills/`, the references are expected at:

```text
.github/skills/python-architecture/SKILL.md
.github/skills/python-architecture/references/architecture-levels.md
.github/skills/python-architecture/references/module-spec-template.md
.github/skills/python-architecture/references/implementation-rules.md
.github/skills/python-architecture/references/audit-checklist.md
```

Load only the reference needed for the current phase:

- SPEC design or module ownership: `architecture-levels.md` and `module-spec-template.md`.
- Implementation: `implementation-rules.md`.
- Consolidated project audit: `audit-checklist.md`.

If the sibling files are unavailable, apply the local Python rules below and continue.

### Python Rules Summary

- Default to the simplest architecture level that satisfies the SPEC.
- Do not introduce Repository, Unit of Work, Service Layer, Clean Architecture, or DDD patterns without a SPEC-recorded reason.
- Public APIs are exported through module entry points such as `__init__.py`.
- Internal modules use the project convention, usually `_name.py`, and must not be imported across module boundaries.
- Domain logic must not depend on FastAPI, Django, Flask, SQLAlchemy sessions, HTTP request objects, or CLI argument parsers unless the module SPEC explicitly permits that coupling.
- Pydantic schemas, serializers, ORM models, and DTOs are boundary models unless the SPEC explicitly chooses a simpler combined model.

## Frontend Architecture Integration

For frontend projects or modules, apply the sibling `frontend-architecture` rules layer before:

- Choosing frontend module ownership.
- Writing or updating frontend `SPEC.md` files.
- Implementing frontend source, build, or dependency changes.
- Running frontend verification and the consolidated project implementation audit.

The references are expected at:

```text
.github/skills/frontend-architecture/SKILL.md
.github/skills/frontend-architecture/references/architecture-levels.md
.github/skills/frontend-architecture/references/module-spec-template.md
.github/skills/frontend-architecture/references/design-rules.md
.github/skills/frontend-architecture/references/implementation-rules.md
.github/skills/frontend-architecture/references/verification-checklist.md
.github/skills/frontend-architecture/references/audit-checklist.md
```

Load only the reference needed for the current phase:

- Requirements or visible design: `architecture-levels.md` and, when applicable, `design-rules.md`.
- SPEC ownership and authoring: `architecture-levels.md` and `module-spec-template.md`.
- Implementation: `implementation-rules.md`.
- Verification: `verification-checklist.md`.
- Consolidated project audit: `audit-checklist.md`.

Frontend rules do not create a new user entry point and must not be fetched from remote sources at runtime. New frontend application modules default to the root Vite workspace with React and TypeScript/TSX. Existing modules keep the framework and language in their confirmed module SPEC until a separate SPEC update authorizes a migration.

## SPEC Update Procedure

### New module or feature

1. Read root `SPEC.md` and relevant module `SPEC.md` files.
2. Read the resolved design document or direct project change request.
3. Decide which module owns the feature, or whether a new module is needed.
4. For Python work, choose the Python architecture level and write the rationale into SPEC.
5. For frontend work, choose the frontend architecture level or accurately record a current legacy exception and write the ownership boundaries into SPEC.
6. Write or update relevant module `SPEC.md` files.
7. If adding a module or changing module relationships, update root `SPEC.md` module table and architecture diagram.
8. Ask the user to confirm SPEC changes before implementation.
9. Implement only after confirmation.
10. Run verification and the consolidated project implementation audit.

### Existing functionality change

1. Read root `SPEC.md` and current module specs for every touched module.
2. Read the resolved design document or direct project change request.
3. Determine impact: public interface, module contract, internal-only, or cross-module dependency change.
4. Apply the SPEC delta decision rules.
5. When a delta is required, update relevant specs and ask the user to confirm them before implementation.
6. When no delta is required, record the required evidence and proceed without editing SPEC.
7. Implement against current confirmed SPEC.
8. Run verification and the consolidated project implementation audit.

## Module SPEC Structure

Every module has exactly one `SPEC.md`. Use this structure unless root `SPEC.md` defines a compatible local convention:

```text
# Module: {name}
## Purpose
## Dependencies
## Public Interface
## Internal Structure
## Python Architecture        (for Python modules)
## Data And State Flow        (for stateful frontend modules)
## Frontend Architecture      (for frontend modules)
## Error Handling             (if applicable)
## Verification Scope         (optional; current externally visible verification obligations only)
## Current Invariants         (optional; present-tense constraints that keep implementation aligned)
```

Root `SPEC.md` should contain repository-wide sections such as:

```text
# {project} Project Specification
## Project Specification Ownership
## Module Table
## Architecture Diagram
## Development Rules
## Python Architecture Rules   (for Python repositories)
```

Do not add `Testing Contract` or `Design Decisions` as default sections for new module specs. Existing sections with those names should be narrowed during touched updates: test matrices move to tests/docs, while decision rationale becomes current invariants only when it still constrains code.

## Project Implementation Rules

After required project SPEC confirmation or a recorded no-SPEC-delta decision:

1. Re-read confirmed root/module specs.
2. Implement only what the confirmed specs require.
3. If implementation reveals a missing or wrong SPEC decision, stop and update SPEC first.
4. Keep edits scoped to affected modules and tests.
5. For behavior changes, write or update tests before production code unless the user explicitly accepts a generated-code or throwaway exception.
6. For frontend work, follow the confirmed framework/language contract and the staged frontend implementation rules; do not mix a partial migration into an existing exception.
7. Run the repository's available verification commands and the applicable frontend verification checklist.

## Project Audit Lifecycle

After project implementation verification, `spec-driven` starts a complete project audit with `audit_mode=full`. This first pass independently establishes the full applicable-item inventory from the current SPEC inputs and complete current diff, then inspects every item before returning. Finding the first blocker must not end the pass, and implementation repair must not begin while the audit is still in progress.

From round 2 onward, use `audit_mode=repair-only`: audit only the repairs for the previous round's findings. Carry unresolved findings forward, retain unaffected passing verdicts with unchanged evidence, and do not rebuild the complete inventory or search for unrelated new issues. The audit skill owns the detailed scope and verdict rules.

### Audit Artifact And Identity Gate

For the first worktree audit, create the complete tracked diff as a file directly through Git, such as `git diff HEAD --no-ext-diff --binary --output=<artifact>`. For a repair-only pass, create the complete repair delta through Git between the previous audited snapshot and the current snapshot, including new or deleted repair files. Never use terminal-captured stdout or a tool's overflow wrapper as the audit artifact.

Before starting the reviewer:

1. Record the artifact's absolute path, SHA-256, byte size, and `diff --git` entry count.
2. Record Git's complete changed-path inventory for the declared audit scope and verify its count and paths agree with the artifact.
3. Verify that no in-scope untracked project file is omitted. Include each such file as a separate complete artifact with its path and SHA-256, or stop as `audit-blocked`.
4. Give the reviewer the artifact identities and require it to validate them before auditing. A missing, truncated, wrapped, mismatched, or unreadable artifact is `audit-blocked`; do not substitute a live diff or an implementation summary.
5. For repair-only passes, include the previous independent report and snapshot, map the repair delta to its finding IDs, and verify that code and SPEC evidence outside the repair scope is unchanged. Do not require the reviewer to re-audit unchanged content.

Treat the verified artifact identities as the audit snapshot. Do not intentionally edit in-scope project files while the audit is running. After a passing audit and immediately before claiming completion, regenerate the worktree artifacts with the same commands and compare their SHA-256 values and path inventory with the audited snapshot and inherited evidence. A mismatch blocks completion: re-verify and re-audit changed repairs, or ask for a scope decision if the changes are unrelated. Do not automatically restart a full audit. For an audited commit range, record the exact immutable base and head object ids instead of a worktree artifact hash.

Each audit result must declare its mode and contain precise SPEC references, concrete diff evidence, verdicts, repair ownership, complete coverage for that mode's scope, and the full finding status for that scope. A repair-only report accounts for every previous-round finding, including unresolved findings carried from earlier rounds. The only allowed early return is `audit-blocked` when required authority inputs, tools, or artifacts are unavailable. `audit-blocked` is not a passing verdict and does not consume a repair round.

After the complete result returns:

1. Group all open findings by implementation repair, authority/human decision, or verification environment.
2. Resolve authority/human decisions and verification-environment blockers before starting an implementation repair batch when they can change or prevent that repair.
3. Repair all in-scope implementation-fixable blocking findings in one batch. Do not trigger another audit after only a partial repair.
4. Complete the affected verification for the entire repair batch.
5. Start an independent repair-only audit of the previous findings using their repair delta and relevant SPEC evidence. Do not reopen unrelated passing items or expand the pass into a new full audit.
6. Repeat repair-only audit, complete repair, and verification rounds until no unresolved blocking findings remain, subject to the repair limit below.

The implementation agent may explain its repair but may not declare audit findings resolved. Only the independent reviewer determines whether each repair satisfies the previous finding. Supply the previous independent report as the follow-up scope, not a persuasive implementation summary.

Run at most two automatic repair rounds; switching to repair-only mode does not reset this count. If the audit after the second repair round still has blocking findings, or one complete repair and re-audit round makes no substantive progress, pause automatic repair and present the complete current finding status, current evidence, attempted repair rounds, reviewer rationale, and concrete human decision options. The findings remain blocking until an allowed decision and any required project SPEC confirmation are complete.

## Consolidated Project Implementation Audit

For a project change, `spec-implementation-audit` owns the applicable-item inventory, verdict semantics, evidence, and coverage for each audit mode. SPEC/code synchronization is a category in that audit, not a separate scan. Include these checks in the full first pass; in repair-only passes apply only those needed to resolve the previous findings:

- [ ] Root `SPEC.md` module table matches actual modules.
- [ ] Root `SPEC.md` architecture diagram matches actual project dependencies.
- [ ] Module `SPEC.md` Public Interface matches exported public symbols.
- [ ] Module `SPEC.md` Dependencies match actual imports from other project modules.
- [ ] Module `SPEC.md` Internal Structure lists actual module files.
- [ ] Python Architecture section matches package layout, import direction, framework boundaries, and model boundaries.
- [ ] Parent and child frontend SPEC ownership matches the workspace and application directories without duplicated contracts.
- [ ] Frontend Public Interface, Data And State Flow, Internal Structure, and Frontend Architecture match entries, source, state ownership, transport boundaries, framework, and language.
- [ ] Frontend manifest, lock file, Vite configuration, generated-output policy, and required browser evidence match the confirmed specs.
- [ ] SPEC text contains current facts only; design process, historical narrative, target-state wording, future roadmap, and detailed test matrices are absent or moved elsewhere.

Before claiming project completion, read and run:

```text
.github/skills/spec-implementation-audit/SKILL.md
```

Audit against the current project authority inputs and actual diff:

```text
root SPEC.md + relevant module SPEC.md files + actual diff
```

Tests, lint, and summaries are supporting evidence only. They do not replace diff-based SPEC audit.

If project SPEC itself needs correction, stop project implementation, update and confirm the applicable SPEC, reconcile the implementation and verification with it, and re-audit the relevant finding in repair-only mode. If the decision introduces changes outside that finding's repair scope, obtain an explicit scope decision rather than automatically restarting a full audit.

## Completion Gate

Do not claim completion while any blocking finding remains. Completion also requires:

- a complete first audit plus complete repair-only coverage of each previous round's findings;
- a matching post-audit artifact identity check linking the current worktree or immutable commit range to that audit chain;
- the first pass's complete applicable-item inventory, with unaffected passing evidence unchanged;
- concrete diff evidence and an initial or repair verdict for every item;
- no unresolved implementation-fixable blocking finding in the carried-forward finding set;
- required verification run or unavailable verification reported as blocking;
- any accepted `needs-human-decision` recorded explicitly.

These gates do not require a new full audit after repairs. Unrelated changes cannot inherit coverage and require a scope decision.

## Required Final Report

End with:

- Input type: confirmed design document or direct project change request.
- Project specs updated and confirmed, or the no-SPEC-delta decision with precise SPEC and defect evidence.
- Files implemented.
- Verification commands run and results.
- Audited SPEC inputs, full and repair-only audit identities or exact commit ranges, post-audit identity result, and cumulative finding status. Distinguish the full first pass from later repair-only passes.
- Complete current finding status, repair rounds performed, and any accepted human decisions.
