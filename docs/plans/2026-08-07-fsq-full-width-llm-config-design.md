# FSQ Full-Width Layout and LLM Config Design

## Goal

Make every Control Plane page use the available application width and reduce
Config to the three LLM connection values users actually need.

## Global page layout

- Remove the shared centered `max-width` constraint from page content.
- Keep the fixed title bar and existing page padding.
- Keep page headings and each page's current information structure.
- Preserve the Workspace file workbench and Device operation/Live Screen
  layouts.
- Let Overview, Runs, Config, Settings, and other pages expand across the
  available shell width.
- On narrow screens, retain the existing responsive stacking behavior.

The goal is consistent use of space, not identical content composition across
pages.

## Config information architecture

Rename the page context to `LLM Configuration`. Config applies to the active
Workspace and contains one full-width key-value form panel.

Do not expose or ask the user to choose an LLM provider.

## Configuration fields

### `base_url`

- URL input
- Describes the compatible model service endpoint
- Shows an inline error when the value is not a valid HTTP or HTTPS URL

### `api_key`

- Password input
- Existing values remain masked
- Provides a Show/Hide control
- Shows an inline error when empty
- Never displays a saved key as plain text by default

### `model_name`

- Text input
- Accepts a model name or deployment name
- Shows an inline error when empty

## Actions and status

- Keep `Save changes` in the page header.
- Add `Test connection` in the form panel.
- Display a compact connection status near the panel actions.
- Failed validation or connection state must be explicit and associated with
  the relevant control.
- Do not use success-shaped fallback content after an error.

## Removed Config content

- Provider selection
- Account and reconnect controls
- Run defaults
- Platform defaults
- Post-action delay
- Maximum agent turns
- Browser executable
- Project Base URL environment setting
- `.env` actions

## Visual design

- Use one full-width engineering-style panel rather than separate marketing
  cards.
- Each row places the key and description on the left and its control on the
  right.
- Use the existing warm Control Plane surfaces, subtle borders, and Clawpilot
  theme variables.
- On narrow screens, each key-value row stacks its label above its control.

## Out of scope

- Backend persistence
- Real connection testing
- Provider-specific fields
- Advanced generation parameters
- Runtime, platform, or project environment configuration
