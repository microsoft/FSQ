# FSQ Full-Width Layout and LLM Config Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make all Control Plane pages use the available shell width and replace Config with a three-field LLM key-value form.

**Architecture:** Remove the shared centered content constraint while preserving page-specific layouts and responsive rules. Replace the current three-card Config markup with one full-width form panel and local prototype validation/interactions.

**Tech Stack:** HTML, CSS, vanilla JavaScript, pytest

---

### Task 1: Make shared page content full width

**Files:**
- Modify: `docs/ux/fsq-control-plane-product-ux.html:518-600`
- Modify: `tests/test_fsq_control_plane_product_ux.py`

**Step 1: Add a failing layout contract test**

Assert the shared `.content-grid` rule does not set `max-width` or centered
horizontal margins. Assert `.page` retains its standard padding and the
Workspace and Device page-specific layouts still exist.

**Step 2: Run the focused test**

Run:

```bash
uv run --frozen --extra dev pytest tests/test_fsq_control_plane_product_ux.py -q
```

Expected: fail because `.content-grid` currently uses `max-width: 1480px` and
`margin: 0 auto`.

**Step 3: Remove the shared width constraint**

Update `.content-grid` to fill available width:

```css
.content-grid {
  width: 100%;
  min-width: 0;
}
```

Do not remove `.page` padding or alter the Workspace and Device column models.

**Step 4: Run checks**

Run focused pytest, ruff, inline script parse, and `git diff --check`.

**Step 5: Commit**

```bash
git add docs/ux/fsq-control-plane-product-ux.html tests/test_fsq_control-plane-product-ux.py
git commit -m "Make Control Plane pages full width"
```

### Task 2: Replace Config with the LLM key-value form

**Files:**
- Modify: `docs/ux/fsq-control-plane-product-ux.html:1195-1270`
- Modify: `docs/ux/fsq-control-plane-product-ux.html:3150-3160`
- Modify: `docs/ux/fsq-control-plane-product-ux.html:3561-4100`
- Modify: `tests/test_fsq_control_plane_product_ux.py`

**Step 1: Add failing Config contract tests**

Assert Config contains exactly these configuration keys:

```text
base_url
api_key
model_name
```

Assert it does not contain Provider, Account, Run defaults, platform defaults,
post-action delay, max turns, browser executable, `.env`, or project Base URL
configuration.

Assert the panel contains `Save changes`, `Test connection`, status feedback,
and Show/Hide behavior for the password field.

**Step 2: Run the focused test**

Expected: fail on the existing Provider, runtime, and environment cards.

**Step 3: Add Config panel styles**

Add scoped styles for:

```css
.config-panel
.config-row
.config-key
.config-control
.config-input-wrap
.config-error
.config-footer
.config-status
```

Use only `var(--cp-*)` colors. Rows use a left key/description column and a
right control column. Under the existing narrow breakpoint, stack each row.

**Step 4: Replace Config markup**

Render one full-width panel with:

- URL input for `base_url`;
- password input plus Show/Hide for `api_key`;
- text input for `model_name`;
- `Test connection` and compact status in the panel footer;
- `Save changes` in the page header.

Use explicit labels, descriptions, input types, and error containers.

**Step 5: Add prototype validation**

Add one validation helper that:

- accepts only HTTP/HTTPS URLs for `base_url`;
- requires `api_key`;
- requires `model_name`;
- writes errors to the associated row;
- sets `aria-invalid` consistently;
- never reports save/test success when validation fails.

Add delegated or direct handlers for:

- Show/Hide key;
- Save changes;
- Test connection.

Successful actions may update local status/toast only; do not persist data or
perform network calls.

**Step 6: Add behavior tests**

Use the existing script-backed test approach to verify:

- password is masked by default;
- Show/Hide toggles input type and label;
- invalid URL and empty values produce inline errors;
- Save/Test do not report success on invalid values;
- valid values produce explicit prototype success feedback.

**Step 7: Run checks**

Run:

```bash
uv run --frozen --extra dev pytest tests/test_fsq_control_plane_product_ux.py -q
uv run --frozen --extra dev ruff check tests/test_fsq_control_plane_product_ux.py
node -e "const fs=require('fs'),vm=require('vm');const h=fs.readFileSync('docs/ux/fsq-control-plane-product-ux.html','utf8');const s=[...h.matchAll(/<script(?:\\s[^>]*)?>([\\s\\S]*?)<\\/script>/g)].map(m=>m[1]);s.forEach((x,i)=>new vm.Script(x,{filename:'inline-'+i+'.js'}));console.log('inline scripts parse')"
git diff --check
```

Expected: all checks pass.

**Step 8: Verify in browser**

Open:

```text
?view=home
?view=runs
?view=config
?view=settings
```

Confirm each uses available width while keeping page padding. Verify Config in
light/dark themes and at a narrow viewport.

**Step 9: Commit**

```bash
git add docs/ux/fsq-control-plane-product-ux.html tests/test_fsq_control_plane_product_ux.py
git commit -m "Simplify Config to LLM connection values"
```
