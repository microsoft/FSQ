# FSQ YAML Code View Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace the raw FSQ YAML `<pre>` block with a readable, read-only source viewer.

**Architecture:** Keep the prototype self-contained in
`docs/ux/fsq-control-plane-product-ux.html`. Add dedicated code-view styles,
render the YAML as semantic line rows with escaped syntax spans, and attach
small local interactions for wrapping and copying without introducing editing
or persistence.

**Tech Stack:** HTML, CSS, vanilla JavaScript, Clawpilot theme variables

---

### Task 1: Add the read-only source viewer presentation

**Files:**
- Modify: `docs/ux/fsq-control-plane-product-ux.html:1037-1048`
- Modify: `docs/ux/fsq-control-plane-product-ux.html:3513-3533`

**Step 1: Capture the current failing visual condition**

Open:

```text
http://127.0.0.1:4178/fsq-control-plane-product-ux.html?view=workspace&file=fsq
```

Select `Code`.

Expected current condition: the YAML is one unstyled `<pre>` block without
line numbers, syntax hierarchy, indentation guides, or command grouping.

**Step 2: Add source-viewer CSS**

Add styles for:

```css
.source-viewer
.source-toolbar
.source-language
.source-actions
.source-scroll
.source-lines
.source-line
.source-line-number
.source-line-code
.source-indent-guide
.yaml-key
.yaml-value
.yaml-string
.yaml-list-marker
.source-command-start
.source-command-body
.source-viewer.wrap-lines
```

Use only existing `var(--cp-*)` color tokens. Keep the line-number gutter
sticky, use the required monospace stack, support horizontal scrolling by
default, and use a subtle row hover state.

**Step 3: Add a safe line-rendering helper**

In the existing script, add helpers that:

1. Escape `&`, `<`, `>`, `"`, and `'`.
2. Split the authored YAML into lines.
3. Mark keys, scalar values, URLs, and list markers with syntax classes.
4. Emit one `.source-line` per authored line with a line number.
5. Mark metadata and command line ranges without changing source order.

Keep the original YAML in one JavaScript string so rendering and copying use
the same source.

**Step 4: Replace the FSQ Code branch**

Change the `type === "fsq" && tab === "Code"` branch to render:

```html
<section class="source-viewer" id="fsqSourceViewer">
  <div class="source-toolbar">
    <span class="source-language">YAML</span>
    <span class="source-line-count">18 lines</span>
    <div class="source-actions">
      <button data-code-action="wrap">Wrap lines</button>
      <button data-code-action="copy">Copy</button>
    </div>
  </div>
  <div class="source-scroll">
    <div class="source-lines">...</div>
  </div>
</section>
```

Expected: the complete YAML remains visible and copyable, while line numbers,
syntax colors, indentation guides, and command blocks improve scanning.

**Step 5: Run a syntax check**

Run:

```bash
node -e "const fs=require('fs'),vm=require('vm');const h=fs.readFileSync('docs/ux/fsq-control-plane-product-ux.html','utf8');const s=[...h.matchAll(/<script(?:\\s[^>]*)?>([\\s\\S]*?)<\\/script>/g)].map(m=>m[1]);s.forEach((x,i)=>new vm.Script(x,{filename:'inline-'+i+'.js'}));console.log('inline scripts parse')"
```

Expected: `inline scripts parse`

**Step 6: Commit**

```bash
git add docs/ux/fsq-control-plane-product-ux.html
git commit -m "Improve FSQ YAML code readability"
```

### Task 2: Add wrap and copy interactions

**Files:**
- Modify: `docs/ux/fsq-control-plane-product-ux.html:3513-3560`

**Step 1: Verify the controls are initially inert**

Open the FSQ `Code` tab and select `Wrap lines` and `Copy`.

Expected before implementation: neither control changes the viewer.

**Step 2: Add delegated Code toolbar behavior**

Attach one click handler to the file preview container:

- `Wrap lines` toggles `.wrap-lines` on `#fsqSourceViewer` and updates
  `aria-pressed`.
- `Copy` calls `navigator.clipboard.writeText()` with the complete authored
  YAML.
- On copy success, change the button label to `Copied` briefly, then restore
  `Copy`.
- On copy failure, show `Copy failed`; do not silently report success.

Use event delegation because the Code view is rendered dynamically.

**Step 3: Preserve behavior across tab switches**

Switch `Structured → Code → Runs → Code`.

Expected: each Code render has working controls, no duplicate listeners, and
the authored YAML remains unchanged.

**Step 4: Run the syntax check**

Run the same Node command from Task 1.

Expected: `inline scripts parse`

**Step 5: Commit**

```bash
git add docs/ux/fsq-control-plane-product-ux.html
git commit -m "Add FSQ code viewer controls"
```

### Task 3: Verify the Workspace experience

**Files:**
- Verify: `docs/ux/fsq-control-plane-product-ux.html`

**Step 1: Start the existing static preview**

Run:

```bash
python3 -m http.server 4178 --directory docs/ux
```

Expected: the server listens on port `4178`.

**Step 2: Verify desktop behavior**

Open:

```text
http://127.0.0.1:4178/fsq-control-plane-product-ux.html?view=workspace&file=fsq
```

Verify:

- `Structured`, `Code`, and `Runs` remain available.
- Code has a compact toolbar and readable line structure.
- Metadata and all six commands remain in authored order.
- Hover highlighting spans line number and code.
- `Wrap lines` preserves line-number alignment.
- `Copy` reports success only after the Clipboard API resolves.

**Step 3: Verify narrow behavior**

At a narrow viewport, verify the source area scrolls horizontally with wrapping
off and remains readable with wrapping on. The sticky line-number gutter must
not overlap source text.

**Step 4: Verify theme behavior**

Open once with `?scoutTheme=light` and once with `?scoutTheme=dark`.

Expected: both themes use only Clawpilot variables, retain readable contrast,
and avoid hardcoded component colors.

**Step 5: Review the final diff**

Run:

```bash
git --no-pager diff HEAD~2 -- docs/ux/fsq-control-plane-product-ux.html
```

Expected: changes are limited to the FSQ YAML source viewer and its local
interactions.
