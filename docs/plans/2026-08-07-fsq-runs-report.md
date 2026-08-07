# FSQ Runs and Run Report Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a first-class Runs entry and turn the existing workbench into a coherent single-run evidence report.

**Architecture:** Reuse the existing hidden `runs` and `workbench` pages in the self-contained UX prototype. Add Runs to primary navigation, restyle the report as a two-column timeline/evidence layout, and route all existing run entry points through the same report state without adding backend behavior.

**Tech Stack:** HTML, CSS, vanilla JavaScript, pytest

---

### Task 1: Expose Runs history in primary navigation

**Files:**
- Modify: `docs/ux/fsq-control-plane-product-ux.html:2764-2806`
- Modify: `docs/ux/fsq-control-plane-product-ux.html:3035-3066`
- Modify: `tests/test_fsq_control_plane_product_ux.py`

**Step 1: Add failing navigation contract tests**

Add focused tests that parse the prototype and assert:

```python
primary_views == ["home", "workspace", "device", "runs"]
footer_views == ["config", "settings"]
```

Also assert that the Runs page retains search plus mode, status, and platform
filters.

**Step 2: Run the focused test**

Run:

```bash
uv run --frozen --extra dev pytest tests/test_fsq_control_plane_product_ux.py -q
```

Expected: fail because `runs` is not in the primary rail.

**Step 3: Add the Runs navigation item**

Insert a `Runs` button directly below `Devices`:

```html
<button class="nav-button" data-view="runs" aria-label="Runs">
  <!-- inline history/report icon -->
  <span>Runs</span>
</button>
```

Use the existing rail icon treatment. Keep Config and Settings in the footer.

**Step 4: Refine the Runs history copy**

Keep the existing table and filters, but align naming with the approved model:

- eyebrow: `WORKSPACE / RUNS`
- title: `Run history`
- description: auditable execution and evidence records
- row selection opens the report

Do not add analytics or trend cards.

**Step 5: Run checks**

Run focused pytest, ruff, inline script parse, and `git diff --check`.

**Step 6: Commit**

```bash
git add docs/ux/fsq-control-plane-product-ux.html tests/test_fsq_control_plane_product_ux.py
git commit -m "Expose Runs history in navigation"
```

### Task 2: Reshape Workbench into Run report

**Files:**
- Modify: `docs/ux/fsq-control-plane-product-ux.html:2001-2180`
- Modify: `docs/ux/fsq-control-plane-product-ux.html:3168-3229`
- Modify: `tests/test_fsq_control_plane_product_ux.py`

**Step 1: Add failing report-structure tests**

Assert the report contains:

- `Back to Runs`
- run name, run ID, mode, platform, duration, and final status
- Retry, Open evidence folder, and Export report
- Planning, Execution, and Verification phases
- Screen, UI Tree, and Logs tabs
- Before and After evidence
- verification goal, key actions, and verifier conclusion

Assert that the report does not retain the old third-column `Inspector`.

**Step 2: Run the focused test**

Expected: fail because the current workbench has three columns, a separate
Inspector, old tab names, and incomplete report actions.

**Step 3: Implement the two-column report shell**

Update the existing `workbench` page rather than adding a duplicate page:

```text
Run report header
├── left: timeline
└── right: selected-step evidence
    ├── title / description / status
    ├── Screen / UI Tree / Logs
    ├── primary captured evidence
    ├── Before / After
    └── verification summary
```

Keep the warm grid-paper visual language and use only `var(--cp-*)` colors.
Keep the left timeline fixed-width and the evidence area fluid.

**Step 4: Consolidate report content**

Move useful Inspector facts into the evidence panel:

- selected action/capability details near the selected-step header;
- artifact count and filenames near evidence tabs;
- final verifier conclusion in the bottom verification summary.

Remove the old third Inspector column.

**Step 5: Add report actions**

Use prototype-only buttons:

- Retry
- Open evidence folder
- Export report

Buttons may show toasts; do not implement filesystem export.

**Step 6: Preserve report states**

Use the existing successful and failed rows to drive:

- report title;
- final status;
- selected failed step when appropriate;
- explicit missing/failed evidence message rather than an empty success panel.

**Step 7: Run checks**

Run focused pytest, ruff, inline script parse, and `git diff --check`.

**Step 8: Commit**

```bash
git add docs/ux/fsq-control-plane-product-ux.html tests/test_fsq_control_plane_product_ux.py
git commit -m "Design the FSQ Run report"
```

### Task 3: Connect report entry points

**Files:**
- Modify: `docs/ux/fsq-control-plane-product-ux.html:2862-2881`
- Modify: `docs/ux/fsq-control-plane-product-ux.html:2982-2993`
- Modify: `docs/ux/fsq-control-plane-product-ux.html:3372-3895`
- Modify: `tests/test_fsq_control_plane_product_ux.py`

**Step 1: Add failing interaction contract tests**

Assert:

- Runs rows use the shared report-opening handler.
- Overview Recent activity opens the same report view.
- Workspace FSQ `Runs` entries can open the same report view.
- completed Device state exposes `View report`.
- `showView("workbench")` keeps Runs active in the rail.
- `?view=workbench` remains directly previewable.

**Step 2: Run the focused test**

Expected: fail because Device completion and Workspace run entries are not
connected to the report.

**Step 3: Centralize report opening**

Add one helper:

```javascript
function openRunReport(runState) {
  applyRunReportState(runState);
  showView("workbench");
}
```

Use it for Runs rows, Overview Recent activity, Workspace FSQ Runs entries, and
Device completion. Do not duplicate per-entry report setup.

**Step 4: Add Device completion prototype state**

After the final Device timeline state, replace the cancel control with:

```html
<button class="btn primary" data-open-report>View report</button>
```

Keep running state unchanged.

**Step 5: Add prototype feedback**

Wire Open evidence folder and Export report to explicit prototype toasts.
Retry should reopen Device with the prior mode/source represented.

**Step 6: Run all focused checks**

Run:

```bash
uv run --frozen --extra dev pytest tests/test_fsq_control_plane_product_ux.py -q
uv run --frozen --extra dev ruff check tests/test_fsq_control_plane_product_ux.py
node -e "const fs=require('fs'),vm=require('vm');const h=fs.readFileSync('docs/ux/fsq-control-plane-product-ux.html','utf8');const s=[...h.matchAll(/<script(?:\\s[^>]*)?>([\\s\\S]*?)<\\/script>/g)].map(m=>m[1]);s.forEach((x,i)=>new vm.Script(x,{filename:'inline-'+i+'.js'}));console.log('inline scripts parse')"
git diff --check
```

Expected: all checks pass.

**Step 7: Verify in browser**

Verify:

```text
?view=runs
?view=workbench
?view=workbench&run=failed
?view=device&state=complete
```

Check desktop, narrow viewport, light theme, and dark theme. Confirm Device is
still the only page with title-bar controls.

**Step 8: Commit**

```bash
git add docs/ux/fsq-control-plane-product-ux.html tests/test_fsq_control_plane_product_ux.py
git commit -m "Connect Run report entry points"
```
