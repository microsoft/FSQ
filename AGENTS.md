# Agent Instructions

This repository uses Spec-Driven Development for project development.

Root `SPEC.md` and relevant module `SPEC.md` files are the grounding truth for project code and project logic. They do not define the SDD workflow.

This file and `.github/skills/` control agent write authorization and SDD phase transitions. Project `SPEC.md` files must not duplicate or override those workflow rules.

Ordinary discussion, explanation, review, and planning are read-only. Do not automatically load or invoke repository workflow skills.

Independent Windows evaluation maintenance under `eval/windows/**` is outside the FSQ project SDD workflow. A clear ordinary user request authorizes changes there, including tracked files, without `/spec-driven`, mandatory SPEC edits/confirmation, team orchestration, or `spec-implementation-audit`. Keep the evaluation toolkit outside FSQ production imports, automatic test discovery, runtime configuration, and distribution builds. Run focused evaluation tests, lint, and packaging checks as appropriate; preserve credentials, runtime copies, and user changes.

This exception does not authorize changes to FSQ production behavior, public interfaces, dependencies, or shared runtime/build/test configuration. Those changes still require the normal FSQ workflow even when requested alongside evaluation work; audit only the applicable FSQ scope, not the independent evaluation tree by default.

Except for that independent evaluation maintenance, before creating, modifying, renaming, or deleting project development files, require the user to explicitly invoke `/spec-driven <confirmed-design-document-path | direct-project-change-request>`. Project development includes project or module `SPEC.md` files, source, behavior-defining tests, runtime/build configuration, public interfaces, module ownership, dependency direction, and documentation of supported project behavior. A natural-language project edit request outside that explicit skill invocation is not authorization to write; stop and direct the user to `/spec-driven`.

`/requirements-to-design <request>` is an optional design aid that the user may explicitly invoke for any requested change, including workflow-control maintenance and local-only ignored files that do not require SDD. It produces a confirmed design document; for project development that requires SDD, that document is higher-quality `/spec-driven` input. It is never a prerequisite for modification.

A clear ordinary user request to create, modify, rename, or delete workflow-control files directly authorizes that maintenance without SDD. Workflow-control files are `AGENTS.md`, `CLAUDE.md`, `.github/copilot-instructions.md`, `.github/agents/**`, `.github/prompts/**`, and `.github/skills/**`. Ambiguous approval, discussion, or a skill name mentioned in prose is not authorization. If a workflow-control edit also changes project behavior, use project SDD.

A clear ordinary user request also authorizes local-only writes without SDD when every non-workflow path affected by the request is both untracked and ignored by Git. Verify both conditions with Git before writing; do not infer them from the path, user intent, or an ignore pattern alone. A tracked file does not qualify even if it matches an ignore rule. Changes to `.gitignore`, repository-local exclude rules, or any tracked project file follow the normal authorization rules. If any affected project-development path does not qualify, require `/spec-driven` for that project change.

During `/spec-driven`, determine whether the requested project change requires a SPEC delta. If it does, update relevant `SPEC.md` files and receive user confirmation before non-SPEC project files change. If the implementation only restores behavior already grounded in current SPEC, record the no-SPEC-delta evidence and proceed without an artificial SPEC edit.

Run repository workflow skills only through an explicit user-invoked slash command. `/requirements-to-design` is available for any requested change; `/spec-driven` remains project-development-only. This file is an agent workflow entry point, not a project specification; keep project and module requirements in `SPEC.md` files and detailed workflow procedures in `.github/skills/`.

## Shared Agent Team Protocol

- **Entry and authorization.** Tech Lead is the normal user-facing coordinator for the FSQ development team. PM, Designer, Developer, and Reviewer work only from bounded task packages supplied by Tech Lead. The team does not change the write gates above: an ordinary FSQ project change request must stop before project writes and direct the user to `/spec-driven <confirmed-design-document-path | direct-project-change-request>`. Workflow-control-only and independent `eval/windows/**` maintenance may proceed under the direct authorization described above; the mandatory team and SDD delivery gates do not apply to independent evaluation work.
- **Role boundaries.** PM owns requirement clarification, scope, and acceptance criteria. Designer owns user flows and interaction states when the change has a user-facing surface. Developer owns bounded implementation, related tests, and directly related documentation. Reviewer independently checks the authoritative SPEC, actual diff, regressions, and verification gaps. Tech Lead owns architecture, dependencies, orchestration, integration verification, user decisions, and final delivery.
- **One task package.** Every handoff states a TaskID; authorization state; branch, HEAD, and worktree status; fixed requirement/design/SPEC references; goal and non-goals; allowed files and forbidden boundaries; dependencies and entry conditions; acceptance criteria; required validation; stop conditions; and the decision owner. Do not assume shared chat context or omit unresolved fields.
- **Ready and Done.** PM may recommend Ready, but Tech Lead decides Ready only after scope, acceptance criteria, dependencies, file ownership, and required authorization are settled. Developer returns pending-review work and never self-declares Done. Reviewer reads the original authority inputs and actual diff rather than relying on summaries. Tech Lead decides Done only after required verification and independent review; tests passing alone do not establish product acceptance.
- **Scheduling and rework.** Each Developer handles one implementation task at a time. Assign exclusive file ownership, never run same-file implementation in parallel, and start dependent work only after prerequisites are complete. Reviewer findings return to the original Developer. Stop non-progressing repair loops and report the blocker, owner, next step, resume condition, and required decision.
- **Durable truth.** Root and module `SPEC.md` files remain the project truth. GitHub Issues and pull requests may carry execution status and review evidence only when their creation or use is authorized. Chat, todos, and ad hoc Markdown are not a shadow requirements system. Read-only roles return structured results for Tech Lead to persist in authorized locations.
- **Delivery package.** Every role returns the TaskID, completed work, actual scope, delivery version or inspectable diff, each acceptance criterion as pass/fail/unverified/blocked with evidence, commands and results, unverified paths, limits, blockers, and decision owners. Tech Lead preserves failures and distinguishes implementation, review, verification, and user-visible completion.
- **Safety.** Tools grant capability, not authorization. Waiting for clarification, credentials, user interaction, external-system permission, destructive-operation approval, or SPEC confirmation is a blocker rather than permission to guess or bypass. Preserve unrelated user changes and never broaden scope silently.

New Python source files must start with:

```python
# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.
```
