# FSQ Settings and Runs Workbench Design

## Goal

Apply the approved 16px edge-to-edge workbench shell to Settings and Runs.

## Settings

- Remove the standalone page heading.
- Use one viewport-filling `Application settings` panel.
- Move title and global-preferences description into the panel header.
- Keep the existing General settings rows unchanged.

## Runs

- Remove the standalone page heading.
- Use one complete `Run history` panel.
- Move title, audit description, and `New run` into the panel header.
- Place search and mode/status/platform filters in an attached toolbar.
- Place the run table directly below the toolbar in the same panel.
- Remove the visual gap and duplicate rounded card boundaries between filters
  and table.

## Shared behavior

- Use 16px desktop page padding.
- Preserve mobile bottom safe padding.
- Stack panel header content/actions on narrow screens.
- Keep Settings fields, Runs data, filters, and report-opening interactions
  unchanged.
- Keep the global title bar empty on both pages.

## Scope

Visual prototype only. No persistence, filtering logic, or backend changes.
