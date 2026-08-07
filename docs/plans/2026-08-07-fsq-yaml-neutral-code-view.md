# FSQ YAML Neutral Code View Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Remove persistent row coloring and accent-colored YAML keys so the FSQ Code tab reads like a clean source viewer rather than a diff.

**Architecture:** Keep the existing source DOM, rendering helpers, command metadata, and interactions unchanged. Restrict implementation to CSS presentation and add a focused regression test that asserts command and metadata rows no longer receive persistent visual fills.

**Tech Stack:** HTML, CSS, vanilla JavaScript, pytest

---

### Task 1: Neutralize the source viewer

**Files:**
- Modify: `docs/ux/fsq-control-plane-product-ux.html:1107-1189`
- Modify: `tests/test_fsq_control_plane_product_ux.py`

**Step 1: Add a failing visual-contract test**

Extend `tests/test_fsq_control_plane_product_ux.py` with a test that reads the
artifact CSS and asserts:

```python
assert '.source-line[data-source-range="metadata"]' not in html
assert ".source-command-start" not in css_with_persistent_background
assert ".source-command-body" not in css_with_persistent_background
assert ".yaml-key {\n        color: var(--cp-text);" in html
assert ".source-line:hover" in html
assert "--source-line-bg: var(--cp-surface-soft)" in hover_rule
```

The test must distinguish CSS selectors from the JavaScript class names that
remain for semantic metadata.

**Step 2: Run the focused test and confirm failure**

Run:

```bash
uv run --frozen --extra dev pytest tests/test_fsq_control_plane_product_ux.py -q
```

Expected: the new visual-contract test fails because metadata and command rows
still have persistent fills and YAML keys still use the accent color.

**Step 3: Apply the neutral visual treatment**

In `docs/ux/fsq-control-plane-product-ux.html`:

1. Remove the persistent metadata row background rule.
2. Remove persistent `.source-command-start` and `.source-command-body`
   background rules.
3. Keep `.source-line` on `var(--cp-surface)` or transparent over the source
   surface.
4. Change hover to a subtle neutral `var(--cp-surface-soft)` background.
5. Change `.yaml-key` to `var(--cp-text)` and add modest font weight.
6. Keep `.yaml-value` muted, `.yaml-string` linked, and list markers soft.
7. Reduce gutter divider and indentation-guide prominence using existing
   theme variables and opacity only.
8. Do not modify source rendering, Copy, Wrap, tabs, or file content.

**Step 4: Run focused checks**

Run:

```bash
uv run --frozen --extra dev pytest tests/test_fsq_control_plane_product_ux.py -q
uv run --frozen --extra dev ruff check tests/test_fsq_control_plane_product_ux.py
node -e "const fs=require('fs'),vm=require('vm');const h=fs.readFileSync('docs/ux/fsq-control-plane-product-ux.html','utf8');const s=[...h.matchAll(/<script(?:\\s[^>]*)?>([\\s\\S]*?)<\\/script>/g)].map(m=>m[1]);s.forEach((x,i)=>new vm.Script(x,{filename:'inline-'+i+'.js'}));console.log('inline scripts parse')"
git diff --check
```

Expected: all tests and checks pass.

**Step 5: Verify in the browser**

Open:

```text
http://127.0.0.1:4178/fsq-control-plane-product-ux.html?view=workspace&file=fsq&scoutTheme=light
```

Select `Code` and confirm:

- no metadata or command row has a persistent fill;
- no YAML key uses the rose accent color;
- only the hovered row gets a subtle neutral fill;
- line numbers and indentation guides remain visible but understated;
- Wrap and Copy still work.

Repeat with `scoutTheme=dark`.

**Step 6: Commit**

```bash
git add docs/ux/fsq-control-plane-product-ux.html tests/test_fsq_control_plane_product_ux.py
git commit -m "Neutralize FSQ YAML source styling"
```
