# FSQ YAML Code View Design

## Goal

Make FSQ YAML easier to read in the Workspace without turning the Control Plane
into a source-code editor.

## Selected approach

Use an engineering-focused, read-only YAML viewer that preserves the complete
file and its authored line order.

## Layout

- Keep the existing `Structured`, `Code`, and `Runs` file tabs.
- Add a compact Code toolbar with the `YAML` language label, line count,
  `Wrap lines`, and `Copy`.
- Render source in a light code surface with a separate line-number gutter.
- Keep horizontal scrolling by default; `Wrap lines` enables readable wrapping
  without changing line-number alignment.

## Readability

- Highlight YAML keys, scalar values, URLs, and list markers with restrained
  theme colors.
- Show indentation guides so nested command parameters are easy to scan.
- Use a subtle full-width hover highlight for the active line.
- Give metadata and individual command ranges a slight background hierarchy
  while preserving one continuous source document.

## Interaction

- The view is read-only and does not expose editing or saving controls.
- `Copy` copies the complete YAML and briefly changes to `Copied`.
- `Wrap lines` toggles wrapping locally for the current view.
- Narrow layouts retain the line-number gutter and allow horizontal scrolling.

## Out of scope

- YAML editing and file persistence
- Search, minimap, diagnostics, or IDE-style command palettes
- Selection synchronization between Structured and Code tabs
- Reordering or transforming the authored YAML
