# FSQ Control Plane UX Prototype

## Purpose

FSQ Control Plane is a local Web UI concept for making FSQ understandable and
usable without requiring users to learn its command-line workflow or manually
assemble LLM configuration.

This prototype focuses on product structure, interaction hierarchy, and visual
language. It does not define production frontend architecture or backend APIs.

## Product model

FSQ has two primary testing loops:

- **Explore with AI**: a user describes a visible goal; FSQ plans actions,
  operates the target, captures evidence, verifies the outcome, and can produce
  a replayable case.
- **Strict Replay**: FSQ executes an authored YAML case deterministically and
  captures fresh evidence for regression testing.

The Control Plane presents these loops as understandable product workflows
rather than command-line concepts.

## Navigation

The primary navigation is:

1. **Overview** — learn the FSQ loop and start a run.
2. **Workspace** — open a local project and inspect its files and FSQ cases.
3. **Devices** — select a platform and target, then explore or replay.
4. **Runs** — browse execution history and open evidence reports.

Workspace-scoped **Config** and global **Settings** remain at the bottom of the
navigation.

## Global shell

- A persistent left navigation rail establishes product context.
- Every page retains the same fixed title-bar height.
- The Device page uses the title bar for Platform and Device controls.
- Other pages keep the title bar visually empty.
- Primary workbench pages use the full available shell width.
- Overview, Runs, Config, and Settings use a 16px outer workbench margin and
  one dominant full-width panel.
- The visual language uses warm off-white grid-paper backgrounds, clean
  surfaces, subtle borders, and deep rose as the single brand accent.

## Page designs

### Overview

Overview starts directly with a full-width **Start a run** panel. It contains
the two FSQ entry points:

- Explore with AI
- Replay a Case

Below it, the page explains the Explore → Capture → Verify → Save Case → Replay
loop and shows recent activity plus environment readiness.

### Workspace

Workspace follows a GitHub-style file browser:

- repository tree and branch controls on the left;
- breadcrumb, file context, and preview on the right;
- file-specific Preview and Code views.

FSQ YAML files provide three views:

- **Structured** — case metadata, lifecycle hooks, commands, and replay action;
- **Code** — a clean, read-only YAML viewer with line numbers, restrained
  syntax highlighting, wrapping, and copying;
- **Runs** — historical executions associated with the case.

The Code view intentionally avoids persistent row coloring so it does not look
like a source diff.

### Devices

The Device workbench keeps target selection in the title bar:

- Platform
- Device
- connection status

The left side is the FSQ operation area:

- Explore accepts a natural-language goal.
- Strict Replay selects a reviewed case.
- Starting a run replaces the composer with a live execution timeline.

The right side focuses on the Live Screen, UI Tree, and Logs.

### Runs and Run report

Runs uses one continuous workbench panel containing:

- title and New run action;
- search and mode/status/platform filters;
- run history table.

Selecting a run opens an evidence-oriented report with:

- Planning, Execution, and Verification timeline;
- selected-step evidence;
- Screen, UI Tree, and Logs;
- Before and After comparison;
- verification goal, key actions, and verifier conclusion.

### LLM Configuration

Config intentionally avoids provider-specific concepts. It contains only:

- `base_url`
- `api_key`
- `model_name`

The values are presented as an engineering-style key-value form. The API key
is masked, and the prototype demonstrates local validation and connection
feedback without making a network request.

### Settings

Settings uses one full-height panel for global Control Plane preferences:

- theme;
- external editor;
- diagnostic logging;
- update checks.

## Interaction principles

- Evidence and state changes should remain visible and inspectable.
- Failed or missing evidence must be explicit rather than represented by an
  empty success-shaped panel.
- Source assets and execution results remain distinct: Workspace owns files
  and cases; Runs owns execution history and reports.
- Prototype actions provide local visual feedback only.

## Prototype

Open:

```text
docs/ux/fsq-control-plane-product-ux.html
```

Useful preview routes include:

```text
?view=home
?view=workspace&file=fsq
?view=device
?view=device&state=running
?view=runs
?view=workbench
?view=config
?view=settings
```

## Scope

This deliverable is a UX prototype. It does not include:

- production frontend implementation;
- backend services or persistence;
- real device discovery;
- real LLM connection testing;
- report export;
- finalized API contracts.
