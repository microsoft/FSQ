# FSQ Overview and Config Workbench Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Convert Overview and LLM Configuration from title-led pages into 16px edge-to-edge workbench pages.

**Architecture:** Keep the existing content and interactions, but move page-level titles/actions into the first full-width panel on each page. Add only page-scoped CSS so Workspace, Device, Runs, Settings, and report pages remain unchanged.

**Tech Stack:** HTML, CSS, vanilla JavaScript, pytest

---

### Task 1: Convert Overview to a workbench page

**Files:**
- Modify: `docs/ux/fsq-control-plane-product-ux.html`
- Modify: `tests/test_fsq_control_plane_product_ux.py`

**Steps:**

1. Add a failing structural test asserting `#home` has no direct
   `.page-head`, uses 16px page padding, and starts with a full-width
   `Start a run` panel.
2. Add scoped Overview workbench styles.
3. Move the introduction and `How FSQ works` into the new panel header.
4. Move the existing Explore/Strict launch grid into the panel body.
5. Keep core loop, Recent activity, and Environment below unchanged.
6. Verify existing narrow card stacking.
7. Run focused pytest, ruff, inline-script parse, and `git diff --check`.
8. Commit as `Make Overview an edge-to-edge workbench`.

### Task 2: Convert Config to a workbench page

**Files:**
- Modify: `docs/ux/fsq-control-plane-product-ux.html`
- Modify: `tests/test_fsq_control_plane_product_ux.py`

**Steps:**

1. Add a failing structural test asserting `#config` has no direct
   `.page-head`, uses 16px page padding, and places title, description, and
   Save inside `.config-panel`.
2. Add a panel header to Config using the existing card header language.
3. Move `LLM Configuration`, description, and Save into that header.
4. Keep the three key-value rows and footer unchanged.
5. Give `.config-panel` a viewport-filling minimum height.
6. Stack the panel header/action at the existing narrow breakpoint.
7. Run focused pytest, ruff, inline-script parse, and `git diff --check`.
8. Verify `?view=home` and `?view=config` in light and dark themes.
9. Commit as `Make LLM Config an edge-to-edge workbench`.
