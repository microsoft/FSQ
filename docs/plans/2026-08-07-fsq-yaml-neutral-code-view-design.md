# FSQ YAML Neutral Code View Design

## Goal

Remove the diff-like appearance from the FSQ YAML Code tab while preserving a
faithful, readable source view.

## Selected approach

Use a clean, neutral, read-only source viewer. Every source line shares the
same base surface. Command ranges and metadata no longer receive persistent
row backgrounds.

## Visual treatment

- Keep line numbers, but reduce the contrast of the gutter and its divider.
- Keep indentation guides at low contrast for hierarchy tracking.
- Render YAML keys in the primary text color with modest font weight.
- Render ordinary scalar values in muted text.
- Render URLs and quoted strings with the link color.
- Render list markers and punctuation in soft neutral text.
- Use a very subtle neutral hover state for the current line only.
- Do not use the accent color to represent YAML structure.

## Preserved behavior

- Keep the complete authored YAML and its line order.
- Keep `Wrap lines` and `Copy` behavior unchanged.
- Keep source selection faithful and line numbers non-selectable.
- Keep light and dark theme support.

## Out of scope

- Editing or saving YAML
- Command cards or section headings inside Code
- Persistent command grouping, row fills, or left-side command markers
- Changes to Structured or Runs
