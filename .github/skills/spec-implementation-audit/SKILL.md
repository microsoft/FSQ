---
name: spec-implementation-audit
description: "Internal diff-based project SPEC audit rules loaded only when an explicitly invoked repository prompt directs the agent to this file."
user-invocable: false
disable-model-invocation: true
---

# SPEC Implementation Audit

Determine whether project-code or project-logic implementation from an explicitly invoked SDD workflow satisfies confirmed project specifications. This is a SPEC-centered, diff-based audit. It is not a general test pass check or a restatement of the implementer's summary.

## Invocation Gate

Load this skill only when an explicitly invoked repository prompt directs the agent to this file. Ordinary discussion, explanation, review, planning, natural-language edit requests, skill-name mentions, and prose approvals must not trigger it.

## Core Rule

This skill audits only project-code or project-logic changes. Agent write authorization, SDD phase transitions, prompt routing, and workflow skills are not project SPEC items.

The audit covers both project paths: implementation after a confirmed SPEC update and implementation under a recorded no-SPEC-delta decision. In the latter path, the reviewer independently verifies that current SPEC remains the complete grounding truth for the changed behavior.

Completion cannot be claimed until implementation is audited against:

```text
root SPEC.md + relevant module SPEC.md files + actual diff
```

Tests, lint, keyword scans, and implementation summaries are auxiliary evidence only. They do not replace diff-based SPEC audit.

## Audit Modes

- Round 1 uses `audit_mode=full`: run the complete scope and procedure below with all existing checks.
- Round 2 and later use `audit_mode=repair-only`: audit only the repairs for the previous round's findings. Carry every unresolved earlier finding forward in that previous-round report so none is dropped.

A repair-only pass reads the previous independent report, its audited snapshot, the complete repair delta, and the SPEC clauses and implementation paths needed to judge those repairs. It does not rebuild the full applicable-item inventory, re-read unchanged parts of the original diff, or search for unrelated new issues. Retain unaffected passing verdicts only when their evidence remains unchanged.

Do not automatically restart a full audit. Changes outside the previous findings' repair scope require an explicit scope decision before they can be treated as covered.

## Independence Requirement

Use a fresh reviewer or independent context whenever the platform supports it. The reviewer must not inherit the implementation agent's conversation history or rely on its self-report.

Reviewer input is limited to:

- Root `SPEC.md`.
- Relevant module `SPEC.md` files.
- Complete diff artifacts for the declared audit mode plus their identity manifest, or an exact commit range.
- For repair-only passes, the previous independent report, its finding IDs and audited snapshot, and the complete delta from that snapshot to the repaired snapshot.
- SPEC delta mode: `confirmed-update` or `no-delta`.
- Minimal navigation instructions required to locate modules and public APIs.
- Optional verification command outputs as auxiliary evidence.
- For no-SPEC-delta work, the recorded SPEC references and neutral defect reproduction evidence as claims to verify, not accepted proof.

Do not provide persuasive summaries such as "this is complete" or "tests pass, so it should be fine".

## Audit Input Integrity

For a worktree audit, validate the supplied artifact path, SHA-256, byte size, `diff --git` entry count, changed-path inventory, and any separate untracked-file artifacts for the declared audit mode. Read the complete in-scope artifacts, not terminal output or overflow wrappers. A repair-only manifest links the previous snapshot and report to the complete repair delta and verifies that code and SPEC evidence outside that scope is unchanged; identity verification does not require re-auditing unchanged content. If required evidence is missing, unreadable, truncated, wrapped, or inconsistent with its identity manifest, return `audit-blocked` without substituting a live diff or implementation summary.

Record the validated artifact identities in the audit result. The calling `spec-driven` workflow owns the post-audit comparison between these identities and a freshly regenerated worktree snapshot; the reviewer must not claim that later worktree changes are covered. For a commit-range audit, validate and report the exact immutable base and head object ids.

## First-Round Complete Scope

Before judging implementation in round 1, establish the complete scope:

- the root and relevant module SPEC inputs;
- the complete current worktree diff or commit range;
- the SPEC delta mode;
- the complete set of applicable SPEC items;
- the file, symbol, interface, dependency, configuration, or behavior boundaries relevant to each item;

Determine this initial scope independently. Later repair-only passes take their scope from the previous findings and repair delta rather than repeating this inventory.

## First-Round Complete Procedure

1. Establish the complete applicable SPEC item inventory before assigning final verdicts, including independent validation of a no-SPEC-delta decision when applicable.
2. Read the diff and relevant implementation path for every applicable item.
3. Apply the Python and frontend domain checks when those areas are in scope.
4. Record a verdict and concrete diff evidence for every item.
5. Consolidate duplicate observations of each blocking root cause and reference every affected SPEC item precisely.
6. Record repair ownership as implementation, SPEC/human decision, or verification environment.
7. Record non-blocking quality feedback separately from SPEC verdicts.
8. Return one complete coverage table and the full finding set, with blocking gaps before quality/style feedback.

A blocking issue must not cause an early return. Complete the current pass before implementation repair or a human decision begins. The only early termination is `audit-blocked`, used when required audit input, a required tool, or a required artifact is unavailable. `audit-blocked` is not an implementation verdict, does not consume a repair attempt, and cannot satisfy the completion gate.

If blocking gaps exist, return the complete result to `spec-driven`. The implementation agent may repair findings but may not mark them closed.

## No-SPEC-Delta Audit

When the implementation used the no-SPEC-delta path, independently verify all of the following from current SPEC and the actual diff:

- Current SPEC already grounds the intended supported behavior repaired by the change.
- The diff restores conformance and does not add or change a supported contract.
- Public interfaces, configuration semantics, module ownership, dependency direction, architecture level, and supported-behavior scope remain accurately described.
- Concrete defect evidence demonstrates an implementation mismatch rather than an undocumented requirement.

The implementation agent's classification, a `bugfix` label, and passing tests are not sufficient proof. If any condition is unproven, return a blocking `spec-delta-required` finding owned by SPEC/human decision. Project implementation pauses until the relevant SPEC is updated and confirmed; after reconciliation and verification, re-audit that finding in repair-only mode. A decision that extends beyond the finding's repair scope requires explicit scope confirmation, not an automatic full re-audit.

## Python Architecture Audit

For Python modules, also verify the following within the declared audit mode's scope:

- Public Interface in SPEC matches `__init__.py` exports, endpoints, commands, events, or documented public symbols.
- Dependencies in SPEC match actual project imports.
- Internal Structure in SPEC matches actual files.
- No cross-module imports from `_private` implementation files.
- Dependency direction follows root `SPEC.md`.
- Domain/application/infrastructure/framework boundaries match SPEC.
- Boundary model choices match SPEC: Pydantic schemas, ORM models, DTOs, serializers, and domain objects do not silently swap roles.
- Tests cover public behavior and invariants promised by SPEC.

## Frontend Architecture Audit

For frontend-owned files, also verify the following within the declared audit mode's scope:

- Root module navigation and parent/child frontend SPEC links match actual ownership boundaries.
- Parent specs own workspace/build policy while child specs own application behavior, state flow, source structure, and browser integration.
- The implemented framework and source language match the module SPEC, including named legacy exceptions.
- npm manifest, lock file, Vite configuration, imports, and generated-output policy agree.
- Frontend code consumes documented backend transport contracts without importing backend implementation or duplicating backend security/filesystem policy.
- Server data, durable browser preferences, transient interaction state, derived values, streams, timers, and cleanup follow the documented owners.
- Loading, empty, error, disabled, cancellation, conflict, completion, responsive, keyboard, focus, and reduced-motion behavior required by SPEC is implemented.
- Available build, type, lint, unit/component, integration, package, browser, accessibility, and visual checks required by SPEC were run.

Use `.github/skills/frontend-architecture/references/audit-checklist.md` for the full checklist. Missing browser or screenshot evidence is blocking when the confirmed SPEC requires it.

## Structured Result

Each applicable item records:

- SPEC source and requirement text or a precise requirement reference;
- represented implementation boundaries;
- concrete diff evidence;
- audit verdict;
- notes needed to reproduce or repair a gap.

Each finding records:

- a stable finding ID retained across repair-only passes;
- affected SPEC items or precise requirement references;
- the distinct root cause;
- blocking verdict and concrete evidence;
- repair owner: implementation, SPEC/human decision, or verification environment;

Duplicate observations of the same root cause are consolidated into one finding that may reference multiple SPEC items. Distinct root causes under one SPEC item remain distinct findings.

## Repair-Only Follow-Up

An audit pass must finish and return its coverage table and full finding set for the declared scope before any implementation repair begins. Do not interleave audit and repair.

After an audit result:

1. Resolve any SPEC/human-decision or verification-environment blocker that can change or prevent implementation repair.
2. Repair every implementation-fixable blocking finding from that result in one batch.
3. Finish all verification affected by the complete repair batch.
4. Start an independent repair-only audit with the previous report, its snapshot, the complete repair delta, relevant SPEC clauses, and neutral verification evidence.
5. Judge every previous-round finding against the actual repair and its controlling path. Keep an unfixed or partially fixed finding open. A defect in the repair that prevents the same requirement from being met also keeps that finding open.
6. Return a verdict for every previous-round finding and carry all remaining blockers forward. Repeat repair-only passes until none remain, subject to the repair limit below.

Do not reopen unrelated passing items or expand the pass into a new full audit. Unrelated discoveries are separate feedback, not automatic additions to this repair batch. If unrelated code or SPEC changes invalidate inherited evidence, pause for a scope decision rather than claiming coverage for them.

Run at most two automatic repair rounds. Switching from full to repair-only mode does not reset that limit. An `audit-blocked` result does not consume a repair round because no complete finding set is available to repair. If blocking findings remain after the second repair round, or one round makes no substantive progress, return the complete current finding status for human decision.

The implementation agent may not declare findings resolved. Only the independent reviewer may give a repaired finding verdict `implemented`; earlier unaffected passing verdicts remain backed by the initial audit and matching evidence.

## Consolidated Synchronization Category

SPEC/code synchronization is part of the applicable-item inventory, not a standalone scan. Cover these items in the full first pass; in repair-only mode inspect only those needed to resolve previous findings:

- root module navigation and dependency direction;
- module public interfaces and exports;
- declared and actual dependencies;
- internal structure and ownership;
- Python architecture and boundary rules;
- frontend parent/child ownership, state, transport, dependency, build, and generated-output contracts;
- current-fact SPEC hygiene.

Report synchronization verdicts within the current audit mode's result, not as another audit pass.

## Verdicts

- `implemented`: Diff contains concrete implementation satisfying the SPEC item.
- `incomplete`: Diff partially implements the SPEC item but leaves required behavior uncovered.
- `missing`: No meaningful implementation evidence exists in the diff.
- `diverged`: Implementation contradicts the SPEC item.
- `documentation-only`: Diff changes docs/specs but not required implementation.
- `interface-only`: Diff exposes signatures, exports, config, or declarations without required behavior.
- `mock-or-stub`: Diff uses placeholders, hardcoded responses, fake paths, or non-production behavior in place of required implementation.
- `boundary-violation`: Python imports, exports, layering, or model boundaries violate confirmed SPEC.
- `spec-delta-required`: Current SPEC does not ground the changed behavior or would become inaccurate without an update.
- `needs-human-decision`: SPEC and implementation cannot be reconciled without a product or design decision.

Any verdict except `implemented` is blocking unless the user explicitly accepts `needs-human-decision` as out of scope for the current change.

## Required Output

For a full first pass, produce a complete item table:

```text
SPEC item | Boundaries | Diff evidence | Verdict | Notes
```

For every pass, produce the complete finding table for its scope:

```text
Finding ID | Affected SPEC items | Root cause | Evidence | Verdict | Repair owner
```

For repair-only passes, replace the full item inventory with a table covering every previous-round finding:

```text
Previous finding ID | Repair delta evidence | Verification evidence | Verdict | Remaining gap
```

State `audit_mode=full|repair-only`, `coverage_complete=true|false` for that mode's scope, `spec_delta_mode=confirmed-update|no-delta`, audited SPEC inputs, and validated diff artifact identities or exact commit range. A repair-only result also identifies the previous report and snapshot it extends. Do not describe scoped coverage as a new full audit. Each evidence entry must cite concrete files and, when possible, line numbers or changed symbols. If evidence is absent, say so directly.

## What Not To Accept

- "The tests pass" as proof that a SPEC item is implemented.
- "The implementation agent said it handled this" as proof.
- Keyword search as proof without reading the changed code path.
- A design document as final authority after `SPEC.md` exists.
- A `bugfix` or `no-spec-delta` label as proof that current SPEC needs no update.
- Agent workflow instructions presented as proof that project behavior satisfies SPEC.
- Public API declarations without backing behavior.
- Mocks, stubs, hardcoded success paths, or placeholder fallbacks as production implementation.
- Python layers or patterns that exist only as empty pass-through abstractions.

## Completion Gate

Before claiming completion, state:

- Which root/module `SPEC.md` files were covered by the full first pass and any repair-only follow-ups.
- Which validated snapshots or exact commit ranges connect the initial audit to the current implementation.
- Which SPEC delta mode was audited and, for no-delta work, whether its classification was independently validated.
- Whether the first full audit and every subsequent repair-only pass have complete coverage for their declared scopes.
- Whether every applicable SPEC item is supported by an initial passing verdict or an independent repair verdict, with unaffected evidence unchanged.
- Whether any unresolved blocking finding remains in the carried-forward finding set.
- Any remaining `needs-human-decision` items accepted by the user.

Completion can be established by the full first pass plus the repair-only audit chain; it does not require another full audit. If a pass is incomplete, the evidence chain is broken, required verification is unavailable, or blocking gaps remain, do not claim completion.
