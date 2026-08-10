# FSQ Settings and Runs Workbench Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Convert Settings and Runs into the same 16px edge-to-edge workbench shell as Overview and Config.

**Architecture:** Move each page's standalone title into its primary panel. Keep all existing settings, run data, and interactions, changing only page-scoped HTML/CSS structure.

**Tech Stack:** HTML, CSS, vanilla JavaScript, pytest

---

### Task 1: Convert Settings

**Files:**
- Modify: `docs/ux/fsq-control-plane-product-ux.html`
- Modify: `tests/test_fsq_control_plane_product_ux.py`

**Steps:**

1. Add a failing structure/CSS test.
2. Give `#settings` 16px desktop padding and mobile bottom safe padding.
3. Remove its direct `.page-head`.
4. Move `Application settings` and description into the General panel header
   as a compact semantic `h1`.
5. Make the panel fill the remaining viewport.
6. Keep all settings rows unchanged.
7. Run focused checks and commit as `Make Settings an edge-to-edge workbench`.

### Task 2: Convert Runs

**Files:**
- Modify: `docs/ux/fsq-control-plane-product-ux.html`
- Modify: `tests/test_fsq_control_plane_product_ux.py`

**Steps:**

1. Add a failing structure/CSS test.
2. Give `#runs` 16px desktop padding and mobile bottom safe padding.
3. Remove its direct `.page-head`.
4. Create one viewport-filling `Run history` panel.
5. Put title, description, and `New run` in the panel header.
6. Attach search and filters as an internal toolbar.
7. Put the existing run table directly below without a second card boundary.
8. Preserve run rows and report-opening interactions.
9. Run focused checks, preview Settings/Runs, and commit as
   `Make Runs an edge-to-edge workbench`.
