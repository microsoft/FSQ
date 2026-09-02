# FSQ Governance

FSQ uses a maintainer-led, open-contribution governance model. Anyone may propose, discuss, review, document, test, or implement improvements. Maintainers are accountable for project direction, repository health, releases, security coordination, and the consistency of public contracts.

This document governs project collaboration. Microsoft organization policies, the [Code of Conduct](CODE_OF_CONDUCT.md), and the [Security Policy](SECURITY.md) take precedence where applicable.

## Principles

- Decisions should be transparent and supported by technical evidence.
- Community participation should be accessible without weakening quality or security.
- Public behavior and architecture should remain aligned with confirmed specifications.
- Maintainer authority comes with service responsibilities, not only merge access.

## Roles

### Contributor

Anyone who participates through Issues, discussions, documentation, tests, code, examples, reviews, or community support is a contributor. Contributors do not need repository permissions.

### Regular Contributor

A regular contributor demonstrates sustained, constructive participation and familiarity with the project workflow. This is a recognition role and does not automatically grant repository permissions.

### Area Reviewer or Triager

Area reviewers and triagers have demonstrated judgment in a project area such as harnesses, the DSL, agent runtime, Playground, documentation, or a supported platform. They may help classify Issues, review changes, and recommend decisions. Maintainers remain responsible for merges and releases unless explicit repository permissions are granted.

### Harness Author

A harness author owns or substantially maintains a platform or backend contribution. This is a specialization path rather than a required permission tier. Harness authors are expected to maintain compatibility, tests, evidence behavior, and user documentation for their area.

### Maintainer

Maintainers steward the project as a whole. Their responsibilities include:

- Triaging Issues and reviewing pull requests.
- Protecting public contracts, architecture boundaries, and specification accuracy.
- Maintaining CI, releases, dependencies, security response, and repository policy.
- Keeping contributor documentation current.
- Developing other contributors and sharing project knowledge.

The current maintainers are the recognized owners listed in [.github/CODEOWNERS](.github/CODEOWNERS). Repository permissions determine whether GitHub recognizes an account as a code owner.

## Decision Process

### Routine Changes

Small fixes, tests, documentation, examples, and repository metadata are decided through normal pull request review. The active repository ruleset and CODEOWNERS configuration define the enforced approval requirements.

### Behavior and Architecture Changes

Changes to supported behavior, public interfaces, project requirements, or architecture follow the spec-driven development contract in [SPEC.md](SPEC.md):

1. Discuss the need through an Issue when practical.
2. Produce and confirm a design document for non-trivial work.
3. Update and confirm the relevant SPEC files.
4. Implement and verify the confirmed current-fact specification.
5. Submit the change through a pull request with design, SPEC, and verification evidence.

### Project-Level Decisions

Project priorities, governance changes, release policy, and major cross-module decisions should be discussed publicly through an Issue, design document, or pull request whenever security or privacy does not require private handling.

Maintainers seek consensus. If material disagreement remains after reasonable discussion, eligible active maintainers decide by simple majority. If no proposal receives a majority, the current policy or behavior remains in place. The outcome and rationale should be recorded in the relevant public thread.

Administrators may use repository-rule bypasses only through a pull request when the configured ruleset permits it. A pull request and auditable rationale remain required.

## Project Direction and Releases

GitHub Issues and milestones communicate committed or scheduled work; placement alone does not promise delivery.

Maintainers review project priorities at least monthly and after material strategy changes. Releases are approved and published by maintainers after required validation succeeds. Release frequency depends on readiness rather than a fixed calendar.

## Role Progression

Roles are based on demonstrated stewardship rather than a fixed pull request count. Relevant evidence includes:

- Consistently correct, focused contributions with appropriate tests or documentation.
- Respect for the Code of Conduct, security rules, and spec-driven workflow.
- Constructive review, triage, support, or ownership beyond an individual's own changes.
- Sustained reliability in an area and willingness to maintain contributed work.

An existing maintainer may nominate a contributor for reviewer, triager, or maintainer responsibilities. Active maintainers evaluate the nomination using the normal project-level decision process, and the candidate may accept or decline. Permission changes are applied by a repository administrator only after the governance decision is recorded.

No contributor is required to pursue additional permissions. Detailed participation paths are documented in [CONTRIBUTING.md](CONTRIBUTING.md#contributor-growth-path).

## Stepping Down and Removal

A reviewer, triager, or maintainer may step down at any time. Maintainers may also move an inactive member to emeritus status after attempting contact; returning contributors may be nominated again without prejudice.

Repository access may be suspended immediately when required to protect users, credentials, releases, or repository integrity. Permanent involuntary removal requires a majority decision by the other eligible active maintainers, subject to Microsoft organization and Code of Conduct processes.

## Conduct and Security

All project participation follows the [Code of Conduct](CODE_OF_CONDUCT.md). Security vulnerabilities must be reported privately according to [SECURITY.md](SECURITY.md), not through public Issues or discussions.

## Governance Changes

Changes to this document require a pull request and use the project-level decision process. Governance should remain lightweight enough for the current community while making authority, responsibility, and contributor progression explicit.