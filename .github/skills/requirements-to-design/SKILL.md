---
name: requirements-to-design
description: "Optional design workflow. Use when the user explicitly invokes /requirements-to-design to produce a confirmed design document without changing SPEC or implementation files."
argument-hint: "Describe the requested change"
user-invocable: true
disable-model-invocation: true
---

# Requirements To Design

Turn an explicitly supplied requested change into a reviewed design document. This optional user-invocable skill implements the `/requirements-to-design` entry point and is available whenever the user voluntarily invokes it, including for workflow-control maintenance and local-only ignored files that do not require SDD. It does not update `SPEC.md` files, implement changes, or become a prerequisite for modification. For project development that requires SDD, the confirmed design is higher-quality `/spec-driven` input.

## Invocation Gate

Run this skill only when the user explicitly invokes `/requirements-to-design` with a requested change. Ordinary discussion, explanation, review, planning, natural-language edit requests, skill-name mentions, and prose approvals must not trigger this skill. If the explicit invocation or its request is absent, stop without writing. An explicit invocation is valid for any requested change; do not reject it because downstream implementation is exempt from SDD.

## Hard Gate

Do not write implementation code. Do not update root or module `SPEC.md` files. The user-confirmed design document is the only permitted repository write. The terminal state is that confirmed document and the implementation authorization appropriate to the requested change: explicit `/spec-driven` invocation for project development that requires SDD, or a clear ordinary implementation request for an exempt change.

## Process

### 1. Explore Context

Read enough relevant local context before detailed questions:

- Root `SPEC.md` and relevant module `SPEC.md` files for project development when useful.
- `AGENTS.md`, `CLAUDE.md`, workflow-control files, project metadata, package layout, tests, target local files, and recent docs when useful.
- For frontend work, the parent frontend SPEC, affected child application SPEC, root npm/Vite metadata, and backend specs that own consumed transport contracts when useful.

If no root `SPEC.md` exists and downstream project implementation requires SDD, note that the target repository must create one through the later `/spec-driven` project SPEC phase before implementation begins.

### 2. Check Scope

If the request spans multiple independent change areas, stop and propose decomposition. Each independent area should get its own design document and appropriate implementation authorization.

### 3. Ask Clarifying Questions

Ask one question at a time. Prefer multiple choice when it helps. Focus on:

- Goal and success criteria.
- User-visible behavior.
- Constraints and non-goals.
- Affected modules and ownership boundaries.
- Risks, edge cases, rollout, and compatibility.
- For Python work: project type, package boundary, public API shape, persistence/framework coupling, and expected verification commands.
- For frontend work: audience and primary task, existing visual language, interaction states, responsive and accessibility behavior, state ownership, backend data contracts, browser support, and expected build/browser evidence.

For frontend requests, ask visual-direction questions only when the change adds or reshapes visible UI. Build, dependency, documentation, and internal refactoring requests do not need an invented aesthetic direction.

### 4. Propose Approaches

Present 2-3 approaches with trade-offs and a recommendation. For Python architecture work, include the simplest viable architecture level and why a higher level is or is not justified.

For frontend architecture work, include the simplest viable frontend level and justify any router, global state, server-state library, design system, or additional layer. Preserve a module's confirmed framework and language unless the request explicitly designs a migration.

### 5. Present Design Sections

Present reviewable sections scaled to complexity:

- Purpose and scope.
- Architecture and module ownership.
- Python package/module boundaries.
- Frontend workspace/application boundaries when applicable.
- Public behavior and interfaces.
- Data/control flow.
- Frontend state categories and interaction states when applicable.
- Visual direction, responsive behavior, and accessibility when visible UI changes.
- Error handling and edge cases.
- Verification and audit expectations.

Ask for confirmation after meaningful sections and revise until approved.

### 6. Write the Design Document

Save the confirmed design to:

```text
docs/superpowers/specs/YYYY-MM-DD-<topic>-design.md
```

Include:

- Goal.
- Scope and non-goals.
- Proposed design.
- Python architecture level and rationale when the project is Python.
- Frontend architecture level and rationale when the work affects a frontend application.
- SDD applicability and affected root/module specs, or why downstream implementation is exempt.
- Open questions resolved during discussion.
- Verification expectations.

### 7. Self-Review

Before handoff, fix:

- Placeholder text such as `TBD` or `TODO`.
- Internal contradictions.
- Scope too broad for one design and implementation cycle.
- Ambiguous requirements that could be implemented two ways.
- Hidden implementation assumptions that should be explicit.

### 8. User Review Gate

Ask the user to review the written design document. Do not proceed until the user confirms it.

### 9. Handoff

After confirmation, use the applicable handoff.

For project development that requires SDD:

```text
Design document: docs/superpowers/specs/YYYY-MM-DD-<topic>-design.md
Next step: invoke /spec-driven with this design document path.
```

For workflow-control maintenance or verified local-only writes that are exempt from SDD:

```text
Design document: docs/superpowers/specs/YYYY-MM-DD-<topic>-design.md
Next step: submit a clear ordinary implementation request; /spec-driven is not required for this exempt change.
```

## Boundaries

- The design document records the confirmed requested change but does not itself authorize implementation.
- For project development that requires SDD, implementation must follow current confirmed specs after required `SPEC.md` updates are confirmed or `/spec-driven` independently validates that no delta is needed.
- For an exempt change, a clear ordinary implementation request is sufficient and implementation follows the confirmed design plus current workflow or local constraints.
- Do not invoke an implementation plan as the next step.
- Do not start implementation directly from the design document without the applicable authorization: explicit `/spec-driven <confirmed-design-document-path>` for project development that requires SDD, or a clear ordinary implementation request for an exempt change.

## Frontend Architecture Integration

For frontend work, use the internal sibling rules at:

```text
.github/skills/frontend-architecture/SKILL.md
.github/skills/frontend-architecture/references/architecture-levels.md
.github/skills/frontend-architecture/references/design-rules.md
```

Load `architecture-levels.md` for frontend ownership and architecture choices. Load `design-rules.md` only when the request adds or reshapes user-visible UI. Do not fetch upstream frontend skills or guidelines at runtime; the repository-local rules are the deterministic source for this workflow.
