# FSQ Runs and Run Report Design

## Goal

Make completed FSQ runs easy to find, inspect, and share without mixing runtime
evidence into Workspace source files or Device execution controls.

## Information architecture

Add `Runs` as a primary navigation item directly below `Devices`.

```text
Overview
Workspace
Devices
Runs

Config
Settings
```

`Runs` is the durable history entry point. A `Run report` is the detail view
for one selected run, not a separate primary navigation item.

## Entry points

The same Run report can be opened from:

- a row in Runs history;
- `View report` after a Device run completes;
- Recent activity on Overview;
- a run entry in the Workspace FSQ file `Runs` tab.

All entry points resolve to the same report view and selected run.

## Runs history

The Runs page provides:

- search by goal, case, or run ID;
- filters for mode, status, and platform;
- run name and ID;
- mode, platform, status, duration, and start time.

Selecting a row opens that run's report.

## Run report

The report uses a two-column evidence layout.

### Header

- Back to Runs
- Run name and ID
- mode, platform, duration, and final status
- Retry
- Open evidence folder
- Export report

### Timeline

The fixed left column groups steps into:

- Planning
- Execution
- Verification

Each step shows its name, semantic tags, and status. Selecting a step updates
the evidence panel.

### Evidence panel

The right column contains:

1. selected-step title, description, and status;
2. `Screen`, `UI Tree`, and `Logs` tabs;
3. the primary captured evidence;
4. side-by-side Before and After evidence;
5. verification goal, key actions, and verifier conclusion.

The layout follows the accepted warm grid-paper Control Plane visual system and
keeps evidence as the dominant content.

## State behavior

- Passed, failed, and inconclusive reports keep the same layout.
- Failed steps identify the failure at the step and report levels.
- Missing evidence displays an explicit reason in the evidence region.
- The UI must not render an empty success-shaped panel when evidence is absent.
- Device runs replace `Cancel run` with `View report` only after completion.

## Global shell behavior

- Run history and Run report retain the fixed title bar.
- Their title bars remain empty, consistent with non-Device pages.
- Device remains the only page with Platform and Device controls in the title
  bar.

## Out of scope

- Cross-run analytics or trend dashboards
- Editing report evidence
- Remote report publishing
- Real report export implementation
- Backend run storage or APIs
