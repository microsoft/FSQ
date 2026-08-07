# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import json
import re
import shutil
import subprocess
from html.parser import HTMLParser
from pathlib import Path

import pytest


def _read_fsq_control_plane_product_ux_html() -> str:
    html_path = Path(__file__).parents[1] / "docs" / "ux" / "fsq-control-plane-product-ux.html"
    return html_path.read_text(encoding="utf-8")


class _NavigationContractParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._stack: list[tuple[str, dict[str, str]]] = []
        self._primary_nav_depth: int | None = None
        self._footer_depth: int | None = None
        self.primary_nav_found = False
        self.footer_found = False
        self.primary_items: list[tuple[str, str]] = []
        self.primary_children: list[tuple[str, str]] = []
        self.footer_views: list[str] = []
        self.errors: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = {name: value or "" for name, value in attrs}
        in_primary_nav = self._primary_nav_depth is not None and len(self._stack) >= self._primary_nav_depth
        in_footer = self._footer_depth is not None and len(self._stack) >= self._footer_depth
        parent_tag, parent_attributes = self._stack[-1] if self._stack else ("", {})

        if tag == "button" and any(ancestor_tag == "button" for ancestor_tag, _ in self._stack) and (in_primary_nav or in_footer):
            self.errors.append("nested button detected in navigation contract")

        view = attributes.get("data-view")
        if view:
            if in_primary_nav:
                if parent_tag == "nav":
                    self.primary_items.append((view, attributes.get("aria-label", "")))
                    self.primary_children.append(("button", view))
                else:
                    self.errors.append(f'primary navigation button "{view}" must be a direct nav child')
            elif in_footer:
                if parent_tag == "div" and "rail-footer" in parent_attributes.get("class", "").split():
                    self.footer_views.append(view)
                else:
                    self.errors.append(f'footer navigation button "{view}" must be a direct footer child')

        if (
            tag == "div"
            and in_primary_nav
            and parent_tag == "nav"
            and attributes.get("id") == "workspaceRecents"
        ):
            self.primary_children.append(("div", "workspaceRecents"))

        self._stack.append((tag, attributes))
        if tag == "nav" and attributes.get("aria-label") == "Primary navigation":
            self.primary_nav_found = True
            self._primary_nav_depth = len(self._stack)
        elif tag == "div" and "rail-footer" in attributes.get("class", "").split():
            self.footer_found = True
            self._footer_depth = len(self._stack)

    def handle_endtag(self, tag: str) -> None:
        for index in range(len(self._stack) - 1, -1, -1):
            if self._stack[index][0] == tag:
                del self._stack[index:]
                break
        if self._primary_nav_depth is not None and len(self._stack) < self._primary_nav_depth:
            self._primary_nav_depth = None
        if self._footer_depth is not None and len(self._stack) < self._footer_depth:
            self._footer_depth = None


def _parse_navigation_contract(html: str) -> dict[str, list[tuple[str, str]] | list[str]]:
    parser = _NavigationContractParser()
    parser.feed(html)

    assert parser.primary_nav_found, "missing primary navigation"
    assert parser.footer_found, "missing rail footer"
    assert not parser.errors, "; ".join(parser.errors)
    return {
        "primary_items": parser.primary_items,
        "primary_children": parser.primary_children,
        "footer_views": parser.footer_views,
    }


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


def _run_node_json_script(script: str, *, skip_reason: str) -> dict[str, object]:
    node = shutil.which("node")
    if node is None:
        pytest.skip(skip_reason)
    result = subprocess.run([node, "--input-type=module", "-e", script], check=True, capture_output=True, text=True)  # noqa: S603
    return json.loads(result.stdout)


def _run_navigation_state_contract(html: str) -> dict[str, object]:
    start = html.index('const pages = [...document.querySelectorAll(".page")];')
    end = html.index('const workspaceNav = document.getElementById("workspaceNav");')
    snippet = html[start:end]
    script = f"""
class FakeClassList {{
  constructor(names = []) {{
    this.values = new Set(names);
  }}
  toggle(name, force) {{
    if (force === undefined) {{
      if (this.values.has(name)) {{
        this.values.delete(name);
        return false;
      }}
      this.values.add(name);
      return true;
    }}
    if (force) {{
      this.values.add(name);
      return true;
    }}
    this.values.delete(name);
    return false;
  }}
  contains(name) {{
    return this.values.has(name);
  }}
  add(name) {{
    this.values.add(name);
  }}
  remove(name) {{
    this.values.delete(name);
  }}
}}

class FakeElement {{
  constructor({{ id = "", dataset = {{}}, classNames = [], attributes = [] }} = {{}}) {{
    this.id = id;
    this.dataset = {{ ...dataset }};
    this.classList = new FakeClassList(classNames);
    this.attributeNames = new Set(attributes);
    this.listeners = new Map();
    this.hidden = false;
    this.textContent = "";
    this.className = classNames.join(" ");
  }}
  addEventListener(type, listener) {{
    if (!this.listeners.has(type)) {{
      this.listeners.set(type, []);
    }}
    this.listeners.get(type).push(listener);
  }}
  click() {{
    for (const listener of this.listeners.get("click") || []) {{
      listener();
    }}
  }}
  hasAttribute(name) {{
    return this.attributeNames.has(name);
  }}
}}

class FakeDocument {{
  constructor() {{
    this.pages = [
      new FakeElement({{ id: "home", classNames: ["page", "active"] }}),
      new FakeElement({{ id: "workspace", classNames: ["page"] }}),
      new FakeElement({{ id: "device", classNames: ["page"] }}),
      new FakeElement({{ id: "runs", classNames: ["page"] }}),
      new FakeElement({{ id: "workbench", classNames: ["page"] }}),
    ];
    this.navButtons = [
      new FakeElement({{ dataset: {{ view: "home" }}, classNames: ["nav-button", "active"] }}),
      new FakeElement({{ dataset: {{ view: "workspace" }}, classNames: ["nav-button"] }}),
      new FakeElement({{ dataset: {{ view: "device" }}, classNames: ["nav-button"] }}),
      new FakeElement({{ dataset: {{ view: "runs" }}, classNames: ["nav-button"] }}),
      new FakeElement({{ dataset: {{ view: "config" }}, classNames: ["nav-button"] }}),
      new FakeElement({{ dataset: {{ view: "settings" }}, classNames: ["nav-button"] }}),
    ];
    this.openWorkbenchTargets = [new FakeElement({{ attributes: ["data-open-workbench"] }})];
    this.elements = {{
      deviceTopbarContext: new FakeElement({{ id: "deviceTopbarContext" }}),
      deviceTopbarControls: new FakeElement({{ id: "deviceTopbarControls" }}),
      runTitle: new FakeElement({{ id: "runTitle" }}),
      runStatus: new FakeElement({{ id: "runStatus" }}),
    }};
  }}
  querySelectorAll(selector) {{
    if (selector === ".page") {{
      return this.pages;
    }}
    if (selector === ".nav-button[data-view]") {{
      return this.navButtons;
    }}
    if (selector === ".device-entry") {{
      return [];
    }}
    if (selector === "[data-view-jump]" || selector === "[data-open-workspace]") {{
      return [];
    }}
    if (selector === "[data-open-workbench]") {{
      return this.openWorkbenchTargets;
    }}
    throw new Error(`Unsupported selector: ${{selector}}`);
  }}
  getElementById(id) {{
    return this.elements[id] || null;
  }}
}}

const document = new FakeDocument();
const window = {{ scrollTo() {{}} }};

{snippet}

const runsPage = document.pages.find((page) => page.id === "runs");
const workbenchPage = document.pages.find((page) => page.id === "workbench");
const runsNav = document.navButtons.find((button) => button.dataset.view === "runs");
const openWorkbench = document.openWorkbenchTargets[0];

runsNav.click();
const runsPageActiveAfterRunsClick = runsPage.classList.contains("active");
const runsNavActiveAfterRunsClick = runsNav.classList.contains("active");
const activeNavViewsAfterRunsClick = document.navButtons
  .filter((button) => button.classList.contains("active"))
  .map((button) => button.dataset.view);

openWorkbench.click();
const activeNavViewsAfterWorkbench = document.navButtons
  .filter((button) => button.classList.contains("active"))
  .map((button) => button.dataset.view);

console.log(JSON.stringify({{
  runsPageActiveAfterRunsClick,
  runsNavActiveAfterRunsClick,
  activeNavViewsAfterRunsClick,
  workbenchPageActive: workbenchPage.classList.contains("active"),
  runsNavActiveAfterWorkbench: runsNav.classList.contains("active"),
  activeNavViewsAfterWorkbench,
}}));
"""
    return _run_node_json_script(script, skip_reason="Node.js is required for FSQ product UX script verification.")


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
    nav_contract = _parse_navigation_contract(html)

    assert nav_contract["primary_items"] == [
        ("home", "Overview"),
        ("workspace", "Workspace"),
        ("device", "Devices"),
        ("runs", "Runs"),
    ]
    assert nav_contract["primary_children"] == [
        ("button", "home"),
        ("button", "workspace"),
        ("div", "workspaceRecents"),
        ("button", "device"),
        ("button", "runs"),
    ]
    assert nav_contract["footer_views"] == ["config", "settings"]


def test_parse_navigation_contract_rejects_nested_primary_nav_buttons() -> None:
    html = """
    <nav class="rail-nav" aria-label="Primary navigation">
      <button class="nav-button" data-view="home" aria-label="Overview">
        <span>Overview</span>
        <button class="nav-button" data-view="workspace" aria-label="Workspace">
          <span>Workspace</span>
        </button>
      </button>
    </nav>
    <div class="rail-footer">
      <button class="nav-button" data-view="config" aria-label="Config"><span>Config</span></button>
      <button class="nav-button" data-view="settings" aria-label="Settings"><span>Settings</span></button>
    </div>
    """

    with pytest.raises(AssertionError, match="nested button"):
        _parse_navigation_contract(html)


def test_fsq_runs_navigation_state_contract_survives_workbench_navigation() -> None:
    payload = _run_navigation_state_contract(_read_fsq_control_plane_product_ux_html())

    assert payload == {
        "runsPageActiveAfterRunsClick": True,
        "runsNavActiveAfterRunsClick": True,
        "activeNavViewsAfterRunsClick": ["runs"],
        "workbenchPageActive": True,
        "runsNavActiveAfterWorkbench": True,
        "activeNavViewsAfterWorkbench": ["runs"],
    }


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
