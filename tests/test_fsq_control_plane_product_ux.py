# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest


def _read_fsq_control_plane_product_ux_html() -> str:
    html_path = Path(__file__).parents[1] / "docs" / "ux" / "fsq-control-plane-product-ux.html"
    return html_path.read_text(encoding="utf-8")


def _extract_primary_nav_contract(html: str) -> tuple[list[tuple[str, str]], str]:
    nav_match = re.search(
        r'<nav class="rail-nav" aria-label="Primary navigation">\s*(?P<nav>[\s\S]*?)\s*</nav>',
        html,
    )
    assert nav_match is not None
    nav_html = nav_match.group("nav")
    nav_items = re.findall(
        r'<button class="nav-button(?: active)?"[^>]*data-view="(?P<view>[^"]+)"[^>]*>[\s\S]*?<span>(?P<label>[^<]+)</span>',
        nav_html,
    )
    return nav_items, nav_html


def _extract_footer_nav_views(html: str) -> list[str]:
    footer_match = re.search(r'<div class="rail-footer">\s*(?P<footer>[\s\S]*?)\s*</div>', html)
    assert footer_match is not None
    return re.findall(r'data-view="([^"]+)"', footer_match.group("footer"))


def _extract_runs_section(html: str) -> str:
    runs_match = re.search(r'<section class="page" id="runs">\s*(?P<section>[\s\S]*?)\s*</section>', html)
    assert runs_match is not None
    return runs_match.group("section")


def _extract_select_option_groups(section_html: str) -> list[list[str]]:
    return [
        re.findall(r"<option>([^<]+)</option>", select_html)
        for select_html in re.findall(r'<select class="select">([\s\S]*?)</select>', section_html)
    ]


def _extract_source_viewer_css_rules(html: str) -> dict[str, list[str]]:
    style_match = re.search(r"<style>\s*(?P<css>[\s\S]*?)</style>", html)
    assert style_match is not None
    rules: dict[str, list[str]] = {}
    for rule_match in re.finditer(r"(?P<selectors>[^{}]+)\{(?P<body>[^{}]*)\}", style_match.group("css")):
        body = rule_match.group("body").strip()
        for selector in rule_match.group("selectors").split(","):
            rules.setdefault(selector.strip(), []).append(body)
    return rules


def _extract_css_declarations(rule_body: str) -> dict[str, str]:
    declarations: dict[str, str] = {}
    for declaration in rule_body.split(";"):
        if ":" not in declaration:
            continue
        name, value = declaration.split(":", 1)
        declarations[name.strip()] = value.strip()
    return declarations


def _assert_source_gutter_rules_are_opaque(rules: dict[str, list[str]]) -> None:
    shared_gutter_background_rule = _extract_css_declarations(rules[".source-line-number"][0])
    gutter_rule = _extract_css_declarations(rules[".source-line-number"][-1])

    assert rules[".source-line-code"][0] == rules[".source-line-number"][0]
    assert shared_gutter_background_rule["background"] == "var(--source-line-bg)"
    assert "opacity" not in shared_gutter_background_rule, "shared source gutter background must stay opaque"
    assert gutter_rule["border-right"] == "1px solid var(--cp-surface-soft)"
    assert "opacity" not in gutter_rule, "gutter-specific source gutter rule must stay opaque"


def _assert_source_command_ranges_stay_neutral(rules: dict[str, list[str]]) -> None:
    offending_rules: dict[str, dict[str, str]] = {}
    for selector, rule_bodies in rules.items():
        if 'data-source-range="commands"' not in selector:
            continue
        for rule_body in rule_bodies:
            declarations = _extract_css_declarations(rule_body)
            background_declarations = {
                name: value
                for name, value in declarations.items()
                if name.startswith("background")
                or (name.startswith("--") and ("background" in name or name.endswith("-bg")))
            }
            if background_declarations:
                offending_rules[selector] = background_declarations
    assert not offending_rules, f"command-range CSS must not set persistent backgrounds: {offending_rules}"


def test_extract_source_viewer_css_rules_preserves_all_rule_bodies_per_selector() -> None:
    rules = _extract_source_viewer_css_rules(
        """
        <style>
          .source-line-number,
          .source-line-code {
            background: var(--source-line-bg);
          }

          .source-line-number {
            border-right: 1px solid var(--cp-surface-soft);
          }
        </style>
        """
    )

    assert rules[".source-line-number"] == [
        "background: var(--source-line-bg);",
        "border-right: 1px solid var(--cp-surface-soft);",
    ]
    assert rules[".source-line-code"] == ["background: var(--source-line-bg);"]


def test_source_gutter_opacity_check_rejects_shared_background_regression() -> None:
    rules = _extract_source_viewer_css_rules(
        """
        <style>
          .source-line-number,
          .source-line-code {
            background: var(--source-line-bg);
            opacity: 0.55;
          }

          .source-line-number {
            border-right: 1px solid var(--cp-surface-soft);
          }
        </style>
        """
    )

    with pytest.raises(AssertionError, match="shared source gutter background must stay opaque"):
        _assert_source_gutter_rules_are_opaque(rules)


def test_source_command_range_background_check_rejects_persistent_fill_regression() -> None:
    rules = _extract_source_viewer_css_rules(
        """
        <style>
          .source-line[data-source-range="commands"] {
            --source-line-bg: var(--cp-surface-soft);
          }
        </style>
        """
    )

    with pytest.raises(AssertionError, match="command-range CSS must not set persistent backgrounds"):
        _assert_source_command_ranges_stay_neutral(rules)


def test_fsq_code_source_viewer_uses_neutral_visual_contract() -> None:
    html = _read_fsq_control_plane_product_ux_html()
    rules = _extract_source_viewer_css_rules(html)

    assert '.source-line[data-source-range="metadata"]' not in rules
    assert ".source-command-start" not in rules
    assert ".source-command-body" not in rules

    source_line_rule = _extract_css_declarations(rules[".source-line"][-1])
    hover_rule = _extract_css_declarations(rules[".source-line:hover"][-1])
    indent_rule = _extract_css_declarations(rules[".source-indent"][-1])
    yaml_key_rule = _extract_css_declarations(rules[".yaml-key"][-1])
    yaml_value_rule = _extract_css_declarations(rules[".yaml-value"][-1])
    yaml_string_rule = _extract_css_declarations(rules[".yaml-string"][-1])
    yaml_list_marker_rule = _extract_css_declarations(rules[".yaml-list-marker"][-1])

    assert source_line_rule["--source-line-bg"] == "var(--cp-surface)"
    assert hover_rule["--source-line-bg"] == "var(--cp-surface-soft)"
    _assert_source_gutter_rules_are_opaque(rules)
    _assert_source_command_ranges_stay_neutral(rules)
    assert yaml_key_rule["color"] == "var(--cp-text)"
    assert yaml_key_rule["font-weight"] in {"500", "600"}
    assert yaml_value_rule["color"] == "var(--cp-text-muted)"
    assert yaml_string_rule["color"] == "var(--cp-link)"
    assert yaml_list_marker_rule["color"] == "var(--cp-text-soft)"
    assert indent_rule["background-image"] == "linear-gradient(90deg, var(--cp-border) 0 1px, transparent 1px)"
    assert float(indent_rule["opacity"]) < 0.6


def test_fsq_code_copy_state_survives_code_rerenders() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is required for FSQ product UX script verification.")
    html = _read_fsq_control_plane_product_ux_html()
    start = html.index("const fsqYamlSource = `")
    end = html.index("function renderRepoFile(")
    snippet = html[start:end]
    script = f"""
class FakeClassList {{
  constructor() {{
    this.values = new Set();
  }}
  toggle(name) {{
    if (this.values.has(name)) {{
      this.values.delete(name);
      return false;
    }}
    this.values.add(name);
    return true;
  }}
}}

class FakeButton {{
  constructor(action, label, options = {{}}) {{
    this.dataset = {{ codeAction: action }};
    if (options.pending) {{
      this.dataset.codeActionPending = "true";
    }}
    this.disabled = Boolean(options.disabled);
    this.isConnected = true;
    this.textContent = label;
    this.attributes = new Map();
    this.codeActionResetTimer = undefined;
  }}
  setAttribute(name, value) {{
    this.attributes.set(name, String(value));
  }}
  closest(selector) {{
    return selector === "[data-code-action]" ? this : null;
  }}
}}

class FakeViewer {{
  constructor(buttons) {{
    this.buttons = buttons;
    this.classList = new FakeClassList();
    this.isConnected = true;
  }}
  querySelector(selector) {{
    if (selector === '[data-code-action="copy"]') {{
      return this.buttons.find((button) => button.dataset.codeAction === "copy") || null;
    }}
    return null;
  }}
}}

class FakeElement {{
  constructor() {{
    this.textContent = "";
  }}
}}

class FakePreview {{
  constructor(document) {{
    this.document = document;
    this.listeners = new Map();
    this.buttons = [];
    this.viewer = null;
    this.className = "";
  }}
  addEventListener(type, listener) {{
    this.listeners.set(type, listener);
  }}
  contains(node) {{
    return this.buttons.includes(node);
  }}
  set innerHTML(html) {{
    for (const button of this.buttons) {{
      button.isConnected = false;
    }}
    if (this.viewer) {{
      this.viewer.isConnected = false;
    }}
    this.buttons = [];
    this.viewer = null;
    delete this.document.elements.fsqSourceViewer;
    const wrapMatch = html.match(/<button type="button" data-code-action="wrap"([^>]*)>([^<]*)<\\/button>/);
    const copyMatch = html.match(/<button type="button" data-code-action="copy"([^>]*)>([^<]*)<\\/button>/);
    if (!wrapMatch || !copyMatch) {{
      return;
    }}
    const wrapAriaLabel = /aria-label="([^"]*)"/.exec(wrapMatch[1])?.[1] || wrapMatch[2];
    const copyAriaLabel = /aria-label="([^"]*)"/.exec(copyMatch[1])?.[1] || copyMatch[2];
    const wrapButton = new FakeButton("wrap", wrapMatch[2]);
    wrapButton.setAttribute("aria-label", wrapAriaLabel);
    wrapButton.setAttribute("aria-pressed", "false");
    const copyOptions = {{
      disabled: /\\sdisabled(?=[\\s>])/.test(copyMatch[1]),
      pending: /data-code-action-pending="true"/.test(copyMatch[1]),
    }};
    const copyButton = new FakeButton("copy", copyMatch[2], copyOptions);
    copyButton.setAttribute("aria-label", copyAriaLabel);
    this.buttons = [wrapButton, copyButton];
    this.viewer = new FakeViewer(this.buttons);
    this.document.elements.fsqSourceViewer = this.viewer;
  }}
  click(button) {{
    if (button.disabled) {{
      return;
    }}
    const listener = this.listeners.get("click");
    if (!listener) {{
      throw new Error("Missing delegated click listener.");
    }}
    listener({{ target: button }});
  }}
}}

class FakeDocument {{
  constructor() {{
    this.elements = {{}};
  }}
  getElementById(id) {{
    return this.elements[id] || null;
  }}
}}

const document = new FakeDocument();
document.elements.filePreview = new FakePreview(document);
document.elements.yamlPreview = new FakeElement();
const window = {{
  setTimeout(callback) {{
    const id = ++window.lastTimerId;
    window.timers.set(id, callback);
    return id;
  }},
  lastTimerId: 0,
  timers: new Map(),
}};
function clearTimeout(timerId) {{
  window.timers.delete(timerId);
}}
function flushTimers() {{
  const callbacks = [...window.timers.values()];
  window.timers.clear();
  for (const callback of callbacks) {{
    callback();
  }}
}}
const clipboardOperations = [];
const clipboardWrites = [];
const navigator = {{
  clipboard: {{
    writeText(text) {{
      clipboardWrites.push(text);
      return new Promise((resolve, reject) => {{
        clipboardOperations.push({{ resolve, reject }});
      }});
    }},
  }},
}};

{snippet}

function currentCopyButton() {{
  return document.getElementById("fsqSourceViewer")?.querySelector('[data-code-action="copy"]') || null;
}}
function renderCodeView() {{
  filePreview.innerHTML = renderFsqSourceViewer(fsqYamlSource);
  return currentCopyButton();
}}
async function flushMicrotasks() {{
  await Promise.resolve();
  await Promise.resolve();
}}
function buttonState(button) {{
  return {{
    disabled: button.disabled,
    pending: button.dataset.codeActionPending || "",
    label: button.textContent,
    ariaLabel: button.attributes.get("aria-label") || "",
  }};
}}
function resolveNextClipboardWrite() {{
  (clipboardOperations.shift() || {{ resolve() {{}} }}).resolve();
}}
function rejectNextClipboardWrite() {{
  (clipboardOperations.shift() || {{ reject() {{}} }}).reject(new Error("denied"));
}}

const firstButton = renderCodeView();
filePreview.click(firstButton);
const rerenderedButton = renderCodeView();
const rerenderedPending = buttonState(rerenderedButton);
filePreview.click(rerenderedButton);
const writeCountBeforeRetry = clipboardWrites.length;
resolveNextClipboardWrite();
await flushMicrotasks();
const successState = buttonState(rerenderedButton);
filePreview.click(rerenderedButton);
const pendingAfterSuccessRetry = buttonState(rerenderedButton);
flushTimers();
const pendingAfterSuccessRetryTimerFlush = buttonState(rerenderedButton);
resolveNextClipboardWrite();
await flushMicrotasks();
const retrySuccessState = buttonState(rerenderedButton);
flushTimers();
const resetAfterSuccessRetry = rerenderedButton.textContent;
filePreview.click(rerenderedButton);
rejectNextClipboardWrite();
await flushMicrotasks();
const failureState = buttonState(rerenderedButton);
filePreview.click(rerenderedButton);
const pendingAfterFailureRetry = buttonState(rerenderedButton);
flushTimers();
const pendingAfterFailureRetryTimerFlush = buttonState(rerenderedButton);
rejectNextClipboardWrite();
await flushMicrotasks();
const retryFailureState = buttonState(rerenderedButton);
flushTimers();
console.log(JSON.stringify({{
  clipboardWriteCount: clipboardWrites.length,
  writeCountBeforeRetry,
  firstWrite: clipboardWrites[0] || "",
  secondWrite: clipboardWrites[1] || "",
  thirdWrite: clipboardWrites[2] || "",
  fourthWrite: clipboardWrites[3] || "",
  rerenderedPending,
  successState,
  pendingAfterSuccessRetry,
  pendingAfterSuccessRetryTimerFlush,
  retrySuccessState,
  resetAfterSuccessRetry,
  failureState,
  pendingAfterFailureRetry,
  pendingAfterFailureRetryTimerFlush,
  retryFailureState,
  resetAfterFailureRetry: rerenderedButton.textContent,
}}));
"""
    result = subprocess.run([node, "--input-type=module", "-e", script], check=True, capture_output=True, text=True)  # noqa: S603
    payload = json.loads(result.stdout)

    assert payload["clipboardWriteCount"] == 4
    assert payload["writeCountBeforeRetry"] == 1
    assert payload["firstWrite"] == payload["secondWrite"]
    assert payload["secondWrite"] == payload["thirdWrite"] == payload["fourthWrite"]
    assert payload["firstWrite"].startswith("schemaVersion: fsq.ai-test/v1\n")
    assert payload["rerenderedPending"] == {"disabled": True, "pending": "true", "label": "Copying…", "ariaLabel": "Copying…"}
    assert payload["successState"] == {"disabled": False, "pending": "", "label": "Copied", "ariaLabel": "Copied"}
    assert payload["pendingAfterSuccessRetry"] == {"disabled": True, "pending": "true", "label": "Copying…", "ariaLabel": "Copying…"}
    assert payload["pendingAfterSuccessRetryTimerFlush"] == {"disabled": True, "pending": "true", "label": "Copying…", "ariaLabel": "Copying…"}
    assert payload["retrySuccessState"] == {"disabled": False, "pending": "", "label": "Copied", "ariaLabel": "Copied"}
    assert payload["resetAfterSuccessRetry"] == "Copy"
    assert payload["failureState"] == {"disabled": False, "pending": "", "label": "Copy failed", "ariaLabel": "Copy failed"}
    assert payload["pendingAfterFailureRetry"] == {"disabled": True, "pending": "true", "label": "Copying…", "ariaLabel": "Copying…"}
    assert payload["pendingAfterFailureRetryTimerFlush"] == {"disabled": True, "pending": "true", "label": "Copying…", "ariaLabel": "Copying…"}
    assert payload["retryFailureState"] == {"disabled": False, "pending": "", "label": "Copy failed", "ariaLabel": "Copy failed"}
    assert payload["resetAfterFailureRetry"] == "Copy"


def test_fsq_control_plane_promotes_runs_into_primary_navigation() -> None:
    html = _read_fsq_control_plane_product_ux_html()
    primary_items, primary_nav_html = _extract_primary_nav_contract(html)

    assert primary_items == [
        ("home", "Overview"),
        ("workspace", "Workspace"),
        ("device", "Devices"),
        ("runs", "Runs"),
    ]
    assert _extract_footer_nav_views(html) == ["config", "settings"]
    assert re.search(
        r'data-view="workspace"[\s\S]*?<div class="workspace-recents" id="workspaceRecents">[\s\S]*?data-view="device"[\s\S]*?data-view="runs"',
        primary_nav_html,
    )


def test_fsq_runs_history_page_keeps_auditable_copy_and_filters() -> None:
    runs_section = _extract_runs_section(_read_fsq_control_plane_product_ux_html())

    assert '<p class="eyebrow">WORKSPACE / RUNS</p>' in runs_section
    assert "<h1>Run history</h1>" in runs_section
    assert "auditable execution and evidence records" in runs_section
    assert 'placeholder="Search runs by goal, case, or run ID"' in runs_section
    assert _extract_select_option_groups(runs_section) == [
        ["All modes", "AI explore", "Strict replay"],
        ["All statuses", "Success", "Failed", "Inconclusive"],
        ["All platforms", "Web", "Android", "Windows", "macOS"],
    ]
    assert re.findall(r"<tr[^>]*data-open-workbench", runs_section)
