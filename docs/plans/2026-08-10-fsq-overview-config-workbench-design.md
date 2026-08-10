# FSQ Overview and Config Workbench Design

## Goal

Make Overview and LLM Configuration use the same edge-to-edge workbench shell
as the Device page.

## Shared shell

- Use 16px page padding for Overview and Config.
- Remove their standalone page-heading regions.
- Place the first content panel directly below the fixed empty title bar.
- Keep the title bar empty on both pages.
- Device remains the only page with title-bar controls.

## Overview

- Add one full-width `Start a run` panel at the top.
- Move the short Overview introduction and `How FSQ works` action into the
  panel header.
- Keep Explore and Strict Replay as the panel's two primary launch choices.
- Keep the core loop, Recent activity, and Environment content below.
- Remove the floating eyebrow and large `Learn FSQ by running it` page title.

## LLM Configuration

- Move `LLM Configuration`, its short description, and `Save changes` into the
  configuration panel header.
- Keep the `base_url`, `api_key`, and `model_name` rows directly below the
  header.
- Keep `Test connection` and connection status in the panel footer.
- Give the panel enough minimum height to fill the remaining viewport.
- Remove the standalone Config page heading.

## Responsive behavior

- Preserve the existing Overview card stacking at narrow widths.
- Stack panel header content and actions when space is constrained.
- Preserve Config key-value row stacking.

## Scope

This is a visual prototype adjustment only. It does not add persistence,
network requests, or backend behavior.
