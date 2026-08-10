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

        if tag == "div" and in_primary_nav and parent_tag == "nav" and attributes.get("id") == "workspaceRecents":
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


def _extract_home_section(html: str) -> str:
    home_match = re.search(r'<section class="page active" id="home">\s*(?P<section>[\s\S]*?)\s*</section>', html)
    assert home_match is not None
    return home_match.group("section")


def _extract_workbench_section(html: str) -> str:
    workbench_match = re.search(
        r'<section class="page workbench-page" id="workbench">\s*(?P<section>[\s\S]*?)\s*</section>',
        html,
    )
    assert workbench_match is not None
    return workbench_match.group("section")


def _extract_select_option_groups(section_html: str) -> list[list[str]]:
    return [re.findall(r"<option>([^<]+)</option>", select_html) for select_html in re.findall(r'<select class="select">([\s\S]*?)</select>', section_html)]


def _extract_source_viewer_css_rules(html: str) -> dict[str, list[str]]:
    style_match = re.search(r"<style>\s*(?P<css>[\s\S]*?)</style>", html)
    assert style_match is not None
    rules: dict[str, list[str]] = {}
    for rule_match in re.finditer(r"(?P<selectors>[^{}]+)\{(?P<body>[^{}]*)\}", style_match.group("css")):
        body = rule_match.group("body").strip()
        for selector in rule_match.group("selectors").split(","):
            rules.setdefault(selector.strip(), []).append(body)
    return rules


def _extract_style_block(html: str) -> str:
    style_match = re.search(r"<style>\s*(?P<css>[\s\S]*?)</style>", html)
    assert style_match is not None
    return style_match.group("css")


def _extract_css_declarations(rule_body: str) -> dict[str, str]:
    declarations: dict[str, str] = {}
    for declaration in rule_body.split(";"):
        if ":" not in declaration:
            continue
        name, value = declaration.split(":", 1)
        declarations[name.strip()] = value.strip()
    return declarations


def _extract_css_blocks(css: str) -> list[tuple[str, str]]:
    blocks: list[tuple[str, str]] = []
    index = 0
    while index < len(css):
        while index < len(css) and css[index].isspace():
            index += 1
        if index >= len(css):
            break
        body_start = css.find("{", index)
        assert body_start != -1, f"unterminated CSS block starting at: {css[index : index + 40]!r}"
        header = css[index:body_start].strip()
        depth = 1
        body_end = body_start + 1
        while body_end < len(css) and depth > 0:
            if css[body_end] == "{":
                depth += 1
            elif css[body_end] == "}":
                depth -= 1
            body_end += 1
        assert depth == 0, f"unterminated CSS block for: {header!r}"
        blocks.append((header, css[body_start + 1 : body_end - 1].strip()))
        index = body_end
    return blocks


def _extract_media_rule_with_selector(css: str, *, media_condition: str, selector: str) -> tuple[list[str], dict[str, str]]:
    media_header = f"@media ({media_condition})"
    for header, body in _extract_css_blocks(css):
        if header != media_header:
            continue
        for selector_group, rule_body in _extract_css_blocks(body):
            selectors = [candidate.strip() for candidate in selector_group.split(",")]
            if selector in selectors:
                return selectors, _extract_css_declarations(rule_body)
    raise AssertionError(f'missing selector "{selector}" inside {media_header}')


def _assert_media_rule_declaration(
    css: str,
    *,
    media_condition: str,
    selector: str,
    property_name: str,
    expected_value: str,
) -> None:
    selectors, declarations = _extract_media_rule_with_selector(
        css,
        media_condition=media_condition,
        selector=selector,
    )
    actual_value = declarations.get(property_name)
    assert actual_value == expected_value, f"missing declaration {property_name}: {expected_value} for selector group {selectors} inside @media ({media_condition}); found {declarations}"


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
                name: value for name, value in declarations.items() if name.startswith("background") or (name.startswith("--") and ("background" in name or name.endswith("-bg")))
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
  constructor({{ id = "", dataset = {{}}, classNames = [], attributes = [], queryResults = {{}}, text = "" }} = {{}}) {{
    this.id = id;
    this.dataset = {{ ...dataset }};
    this.classList = new FakeClassList(classNames);
    this.attributeNames = new Set(attributes);
    this.queryResults = new Map(Object.entries(queryResults).map(([selector, results]) => [selector, Array.isArray(results) ? results : [results]]));
    this.listeners = new Map();
    this.hidden = false;
    this.textContent = text;
    this.className = classNames.join(" ");
  }}
  set className(value) {{
    this._className = value;
    this.classList = new FakeClassList(String(value).split(/\\s+/).filter(Boolean));
  }}
  get className() {{
    return this._className;
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
  querySelector(selector) {{
    return (this.queryResults.get(selector) || [])[0] || null;
  }}
  querySelectorAll(selector) {{
    return this.queryResults.get(selector) || [];
  }}
}}

function createWorkbenchTarget({{ title, summary = "", badge = "", runId = "", cells = [], attributes = [] }}) {{
  const queryResults = {{
    strong: new FakeElement({{ text: title }}),
  }};
  if (summary) {{
    queryResults.small = new FakeElement({{ text: summary }});
  }} else if (runId) {{
    queryResults.small = new FakeElement({{ text: runId }});
  }}
  if (badge) {{
    queryResults[".badge"] = new FakeElement({{ text: badge }});
  }}
  if (cells.length) {{
    queryResults.td = cells.map((text) => new FakeElement({{ text }}));
  }}
  return new FakeElement({{ attributes, queryResults }});
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
    this.openWorkbenchTargets = [
      createWorkbenchTarget({{
        title: "Create project flow",
        summary: "AI explore · Web · 4m ago",
        badge: "success",
        attributes: ["data-open-workbench"],
      }}),
      createWorkbenchTarget({{
        title: "Checkout smoke",
        runId: "run_20260807_131914",
        badge: "failed",
        cells: ["Checkout smoke run_20260807_131914", "↻ Strict replay", "Web", "failed", "34s"],
        attributes: ["data-open-workbench", "data-failed-run"],
      }}),
      createWorkbenchTarget({{
        title: "Settings profile",
        summary: "AI explore · macOS · yesterday",
        badge: "inconclusive",
        attributes: ["data-open-workbench"],
      }}),
      createWorkbenchTarget({{
        title: "Onboarding navigation",
        runId: "run_20260806_145522",
        badge: "success",
        cells: ["Onboarding navigation run_20260806_145522", "↻ Strict replay", "Android", "success", "58s"],
        attributes: ["data-open-workbench"],
      }}),
      createWorkbenchTarget({{
        title: "Desktop launch",
        runId: "run_20260805_112011",
        badge: "success",
        cells: ["Desktop launch run_20260805_112011", "↻ Strict replay", "Windows", "success", "27s"],
        attributes: ["data-open-workbench"],
      }}),
      createWorkbenchTarget({{
        title: "Unexpected smoke",
        runId: "run_20260801_101010",
        badge: "success",
        cells: ["Unexpected smoke run_20260801_101010", "✦ AI explore", "Web", "success", "45s"],
        attributes: ["data-open-workbench"],
      }}),
    ];
    this.steps = Array.from({{ length: 7 }}, (_, index) => new FakeElement({{
      id: `reportStep${{index}}`,
      dataset: {{ stepSlot: String(index) }},
      classNames: ["step"],
    }}));
    this.tabs = ["Screen", "UI Tree", "Logs"].map((label, index) => {{
      const tab = new FakeElement({{ classNames: ["tab", ...(index === 0 ? ["active"] : [])] }});
      tab.textContent = label;
      return tab;
    }});
    this.elements = {{
      deviceTopbarContext: new FakeElement({{ id: "deviceTopbarContext" }}),
      deviceTopbarControls: new FakeElement({{ id: "deviceTopbarControls" }}),
      reportContext: new FakeElement({{ id: "reportContext" }}),
      runTitle: new FakeElement({{ id: "runTitle" }}),
      runId: new FakeElement({{ id: "runId" }}),
      runMode: new FakeElement({{ id: "runMode" }}),
      runPlatform: new FakeElement({{ id: "runPlatform" }}),
      runDuration: new FakeElement({{ id: "runDuration" }}),
      runStatus: new FakeElement({{ id: "runStatus" }}),
      evidenceTitle: new FakeElement({{ id: "evidenceTitle" }}),
      evidenceDetail: new FakeElement({{ id: "evidenceDetail" }}),
      evidenceStepStatus: new FakeElement({{ id: "evidenceStepStatus" }}),
      evidenceSurfaceTitle: new FakeElement({{ id: "evidenceSurfaceTitle" }}),
      evidenceSurfaceNote: new FakeElement({{ id: "evidenceSurfaceNote" }}),
      evidenceSurfaceStatus: new FakeElement({{ id: "evidenceSurfaceStatus" }}),
      evidenceOutcome: new FakeElement({{ id: "evidenceOutcome" }}),
      evidenceMissingReason: new FakeElement({{ id: "evidenceMissingReason" }}),
      evidenceBeforeLabel: new FakeElement({{ id: "evidenceBeforeLabel" }}),
      evidenceBeforeText: new FakeElement({{ id: "evidenceBeforeText" }}),
      evidenceAfterLabel: new FakeElement({{ id: "evidenceAfterLabel" }}),
      evidenceAfterText: new FakeElement({{ id: "evidenceAfterText" }}),
      evidenceCapability: new FakeElement({{ id: "evidenceCapability" }}),
      evidenceTarget: new FakeElement({{ id: "evidenceTarget" }}),
      evidenceKind: new FakeElement({{ id: "evidenceKind" }}),
      evidenceStepDuration: new FakeElement({{ id: "evidenceStepDuration" }}),
      evidenceArtifactCount: new FakeElement({{ id: "evidenceArtifactCount" }}),
      evidenceBeforeArtifact: new FakeElement({{ id: "evidenceBeforeArtifact" }}),
      evidenceAfterArtifact: new FakeElement({{ id: "evidenceAfterArtifact" }}),
      evidenceUiTreeArtifact: new FakeElement({{ id: "evidenceUiTreeArtifact" }}),
      evidenceLogArtifact: new FakeElement({{ id: "evidenceLogArtifact" }}),
      verificationGoal: new FakeElement({{ id: "verificationGoal" }}),
      verificationKeyActions: new FakeElement({{ id: "verificationKeyActions" }}),
      verifierConclusion: new FakeElement({{ id: "verifierConclusion" }}),
      reportRetry: new FakeElement({{ id: "reportRetry" }}),
      reportOpenEvidence: new FakeElement({{ id: "reportOpenEvidence" }}),
      reportExport: new FakeElement({{ id: "reportExport" }}),
    }};
    for (let index = 0; index < 7; index += 1) {{
      this.elements[`reportStep${{index}}`] = this.steps[index];
      this.elements[`reportStepName${{index}}`] = new FakeElement({{ id: `reportStepName${{index}}` }});
      this.elements[`reportStepStatus${{index}}`] = new FakeElement({{ id: `reportStepStatus${{index}}` }});
      this.elements[`reportStepMeta${{index}}`] = new FakeElement({{ id: `reportStepMeta${{index}}` }});
    }}
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
    if (selector === ".step") {{
      return this.steps;
    }}
    if (selector === ".evidence-stage .tab") {{
      return this.tabs;
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


class _WorkbenchContractParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._stack: list[tuple[str, dict[str, str]]] = []
        self._workbench_depth: int | None = None
        self._text_targets: list[tuple[str, list[str]]] = []
        self.panel_count = 0
        self.header_buttons: list[str] = []
        self.header_action_buttons: list[str] = []
        self.meta_terms: list[str] = []
        self.phase_labels: list[str] = []
        self.tab_labels: list[str] = []
        self.section_headings: list[str] = []
        self.compare_labels: list[str] = []
        self.timeline_step_count = 0
        self.inspector_mentions: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = {name: value or "" for name, value in attrs}
        self._stack.append((tag, attributes))
        if tag == "section" and attributes.get("id") == "workbench":
            self._workbench_depth = len(self._stack)
            return
        if self._workbench_depth is None or len(self._stack) < self._workbench_depth:
            return

        classes = set(attributes.get("class", "").split())
        if "workbench-panel" in classes:
            self.panel_count += 1
        if tag == "button" and "step" in classes:
            self.timeline_step_count += 1
        if tag == "button" and "back-link" in classes:
            self._text_targets.append(("header_buttons", []))
        elif tag == "button" and "tab" in classes:
            self._text_targets.append(("tab_labels", []))
        elif tag == "button" and "btn" in classes and any("workbench-header-actions" in ancestor_attrs.get("class", "").split() for _, ancestor_attrs in self._stack[:-1]):
            self._text_targets.append(("header_action_buttons", []))
        elif tag == "dt" and any("report-meta-list" in ancestor_attrs.get("class", "").split() for _, ancestor_attrs in self._stack[:-1]):
            self._text_targets.append(("meta_terms", []))
        elif tag == "div" and "phase" in classes:
            self._text_targets.append(("phase_labels", []))
        elif tag in {"h2", "h3"}:
            self._text_targets.append(("section_headings", []))
        elif tag == "strong" and "compare-label" in classes:
            self._text_targets.append(("compare_labels", []))

    def handle_data(self, data: str) -> None:
        if not self._text_targets:
            return
        text = data.strip()
        if text:
            self._text_targets[-1][1].append(text)

    def handle_endtag(self, tag: str) -> None:
        if self._text_targets and self._stack and self._stack[-1][0] == tag:
            target_name, chunks = self._text_targets.pop()
            text = " ".join(chunks).strip()
            if text:
                getattr(self, target_name).append(text)
                if text == "Inspector":
                    self.inspector_mentions.append(text)

        for index in range(len(self._stack) - 1, -1, -1):
            if self._stack[index][0] == tag:
                del self._stack[index:]
                break
        if self._workbench_depth is not None and len(self._stack) < self._workbench_depth:
            self._workbench_depth = None


def _parse_workbench_contract(html: str) -> dict[str, object]:
    parser = _WorkbenchContractParser()
    parser.feed(html)
    return {
        "panel_count": parser.panel_count,
        "header_buttons": parser.header_buttons,
        "header_action_buttons": parser.header_action_buttons,
        "meta_terms": parser.meta_terms,
        "phase_labels": parser.phase_labels,
        "tab_labels": parser.tab_labels,
        "section_headings": parser.section_headings,
        "compare_labels": parser.compare_labels,
        "timeline_step_count": parser.timeline_step_count,
        "inspector_mentions": parser.inspector_mentions,
    }


class _PageLayoutContractParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._stack: list[tuple[str, dict[str, str]]] = []
        self._page_depth: int | None = None
        self._page_id: str | None = None
        self.page_children: dict[str, list[dict[str, str]]] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = {name: value or "" for name, value in attrs}
        self._stack.append((tag, attributes))

        classes = set(attributes.get("class", "").split())
        if tag == "section" and "page" in classes:
            self._page_depth = len(self._stack)
            self._page_id = attributes.get("id")
            if self._page_id:
                self.page_children.setdefault(self._page_id, [])
            return

        if self._page_id is None or self._page_depth is None:
            return

        if len(self._stack) == self._page_depth + 1 and tag == "div":
            self.page_children[self._page_id].append(attributes)

    def handle_endtag(self, tag: str) -> None:
        for index in range(len(self._stack) - 1, -1, -1):
            if self._stack[index][0] == tag:
                del self._stack[index:]
                break
        if self._page_depth is not None and len(self._stack) < self._page_depth:
            self._page_depth = None
            self._page_id = None


def _parse_page_layout_contract(html: str) -> dict[str, list[dict[str, str]]]:
    parser = _PageLayoutContractParser()
    parser.feed(html)
    return parser.page_children


def _find_page_child_with_class(page_children: dict[str, list[dict[str, str]]], page_id: str, class_name: str) -> dict[str, str]:
    for child in page_children.get(page_id, []):
        if child.get("class") == class_name:
            return child
    pytest.fail(f'missing "{class_name}" direct child on page "{page_id}"')


def _extract_config_section(html: str) -> str:
    config_match = re.search(r'<section class="page" id="config">\s*(?P<section>[\s\S]*?)\s*</section>', html)
    assert config_match is not None
    return config_match.group("section")


class _PageSectionMarkupParser(HTMLParser):
    def __init__(self, page_id: str) -> None:
        super().__init__(convert_charrefs=False)
        self._page_id = page_id
        self._stack: list[tuple[str, dict[str, str]]] = []
        self._page_depth: int | None = None
        self._markup_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = {name: value or "" for name, value in attrs}
        self._stack.append((tag, attributes))

        if self._page_depth is not None:
            self._markup_parts.append(self.get_starttag_text())
            return

        if tag == "section" and attributes.get("id") == self._page_id and "page" in attributes.get("class", "").split():
            self._page_depth = len(self._stack)
            self._markup_parts.append(self.get_starttag_text())

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self._page_depth is not None:
            self._markup_parts.append(self.get_starttag_text())

    def handle_endtag(self, tag: str) -> None:
        if self._page_depth is not None:
            self._markup_parts.append(f"</{tag}>")

        for index in range(len(self._stack) - 1, -1, -1):
            if self._stack[index][0] == tag:
                del self._stack[index:]
                break

        if self._page_depth is not None and len(self._stack) < self._page_depth:
            self._page_depth = None

    def handle_data(self, data: str) -> None:
        if self._page_depth is not None:
            self._markup_parts.append(data)

    def handle_entityref(self, name: str) -> None:
        if self._page_depth is not None:
            self._markup_parts.append(f"&{name};")

    def handle_charref(self, name: str) -> None:
        if self._page_depth is not None:
            self._markup_parts.append(f"&#{name};")

    def handle_comment(self, data: str) -> None:
        if self._page_depth is not None:
            self._markup_parts.append(f"<!--{data}-->")


def _extract_page_markup(html: str, page_id: str) -> str:
    parser = _PageSectionMarkupParser(page_id)
    parser.feed(html)
    page_markup = "".join(parser._markup_parts).strip()
    assert page_markup, f'missing page markup for "{page_id}"'
    return page_markup


def _extract_settings_section(html: str) -> str:
    return _extract_page_markup(html, "settings")


class _ConfigContractParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._stack: list[tuple[str, dict[str, str]]] = []
        self._config_depth: int | None = None
        self._text_targets: list[tuple[str, list[str]]] = []
        self._current_row: dict[str, object] | None = None
        self.context = ""
        self.title = ""
        self.description = ""
        self.header_buttons: list[str] = []
        self.row_keys: list[str] = []
        self.row_descriptions: list[str] = []
        self.row_input_types: list[str] = []
        self.row_input_ids: list[str] = []
        self.row_input_names: list[str] = []
        self.row_input_values: list[str] = []
        self.row_input_placeholders: list[str] = []
        self.row_input_data_has_saved_key: list[str] = []
        self.row_label_fors: list[str] = []
        self.row_label_texts: list[str] = []
        self.row_error_ids: list[str] = []
        self.toggle_buttons: list[dict[str, str]] = []
        self.footer_buttons: list[str] = []
        self.footer_button_types: list[str] = []
        self.status_ids: list[str] = []
        self.status_texts: list[str] = []
        self.section_text: list[str] = []
        self.panel_count = 0
        self.header_button_types: list[str] = []
        self.header_button_form_ids: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = {name: value or "" for name, value in attrs}
        self._stack.append((tag, attributes))

        classes = set(attributes.get("class", "").split())
        if tag == "section" and attributes.get("id") == "config":
            self._config_depth = len(self._stack)
            return
        if self._config_depth is None or len(self._stack) < self._config_depth:
            return

        if "config-panel" in classes:
            self.panel_count += 1
        if tag == "p" and "eyebrow" in classes:
            self._text_targets.append(("context", []))
        elif tag == "h1":
            self._text_targets.append(("title", []))
        elif tag == "p" and "muted" in classes and any(
            ("page-head" in ancestor_attrs.get("class", "").split()) or ("card-head" in ancestor_attrs.get("class", "").split())
            for _, ancestor_attrs in self._stack[:-1]
        ):
            self._text_targets.append(("description", []))

        if "config-row" in classes:
            self._current_row = {"toggle_button": None}

        if self._current_row is not None:
            if tag == "strong" and any("config-key" in ancestor_attrs.get("class", "").split() for _, ancestor_attrs in self._stack[:-1]):
                self._text_targets.append(("row_key", []))
            elif tag == "label" and any("config-key" in ancestor_attrs.get("class", "").split() for _, ancestor_attrs in self._stack[:-1]):
                self._current_row["label_for"] = attributes.get("for", "")
                self._text_targets.append(("row_label", []))
            elif tag == "p" and any("config-key" in ancestor_attrs.get("class", "").split() for _, ancestor_attrs in self._stack[:-1]):
                self._text_targets.append(("row_description", []))
            elif tag == "input":
                self._current_row["input_type"] = attributes.get("type", "text")
                self._current_row["input_id"] = attributes.get("id", "")
                self._current_row["input_name"] = attributes.get("name", "")
                self._current_row["input_value"] = attributes.get("value", "")
                self._current_row["input_placeholder"] = attributes.get("placeholder", "")
                self._current_row["input_data_has_saved_key"] = attributes.get("data-has-saved-key", "")
            elif "config-error" in classes:
                self._current_row["error_id"] = attributes.get("id", "")
            elif tag == "button" and any("config-input-wrap" in ancestor_attrs.get("class", "").split() for _, ancestor_attrs in self._stack[:-1]):
                self._current_row["toggle_button"] = {
                    "aria_label": attributes.get("aria-label", ""),
                }
                self._text_targets.append(("toggle_button", []))

        if tag == "button" and any(
            ("page-head" in ancestor_attrs.get("class", "").split()) or ("card-head" in ancestor_attrs.get("class", "").split())
            for _, ancestor_attrs in self._stack[:-1]
        ):
            self.header_button_types.append(attributes.get("type", ""))
            self.header_button_form_ids.append(attributes.get("form", ""))
            self._text_targets.append(("header_buttons", []))
        elif tag == "button" and any("config-footer" in ancestor_attrs.get("class", "").split() for _, ancestor_attrs in self._stack[:-1]):
            self.footer_button_types.append(attributes.get("type", ""))
            self._text_targets.append(("footer_buttons", []))

        if "config-status" in classes:
            self.status_ids.append(attributes.get("id", ""))
            self._text_targets.append(("status_text", []))

    def handle_data(self, data: str) -> None:
        if self._config_depth is None:
            return
        text = data.strip()
        if text:
            self.section_text.append(text)
        if not self._text_targets or not text:
            return
        for _, chunks in self._text_targets:
            chunks.append(text)

    def handle_endtag(self, tag: str) -> None:
        if self._text_targets and self._stack and self._stack[-1][0] == tag:
            target_name, chunks = self._text_targets.pop()
            text = " ".join(chunks).strip()
            if text:
                if target_name == "context":
                    self.context = text
                elif target_name == "title":
                    self.title = text
                elif target_name == "description":
                    self.description = text
                elif target_name == "header_buttons":
                    self.header_buttons.append(text)
                elif target_name == "footer_buttons":
                    self.footer_buttons.append(text)
                elif target_name == "row_key" and self._current_row is not None:
                    self._current_row["row_key"] = text
                elif target_name == "row_description" and self._current_row is not None:
                    self._current_row["row_description"] = text
                elif target_name == "row_label" and self._current_row is not None:
                    self._current_row["label_text"] = text
                elif target_name == "toggle_button" and self._current_row is not None:
                    toggle_button = self._current_row.get("toggle_button")
                    if isinstance(toggle_button, dict):
                        toggle_button["text"] = text
                elif target_name == "status_text":
                    self.status_texts.append(text)

        if self._current_row is not None and tag == "div":
            current_tag, current_attributes = self._stack[-1]
            if current_tag == "div" and "config-row" in current_attributes.get("class", "").split():
                self.row_keys.append(str(self._current_row.get("row_key", "")))
                self.row_descriptions.append(str(self._current_row.get("row_description", "")))
                self.row_input_types.append(str(self._current_row.get("input_type", "")))
                self.row_input_ids.append(str(self._current_row.get("input_id", "")))
                self.row_input_names.append(str(self._current_row.get("input_name", "")))
                self.row_input_values.append(str(self._current_row.get("input_value", "")))
                self.row_input_placeholders.append(str(self._current_row.get("input_placeholder", "")))
                self.row_input_data_has_saved_key.append(str(self._current_row.get("input_data_has_saved_key", "")))
                self.row_label_fors.append(str(self._current_row.get("label_for", "")))
                self.row_label_texts.append(str(self._current_row.get("label_text", "")))
                self.row_error_ids.append(str(self._current_row.get("error_id", "")))
                toggle_button = self._current_row.get("toggle_button")
                if isinstance(toggle_button, dict):
                    self.toggle_buttons.append(toggle_button)
                self._current_row = None

        for index in range(len(self._stack) - 1, -1, -1):
            if self._stack[index][0] == tag:
                del self._stack[index:]
                break
        if self._config_depth is not None and len(self._stack) < self._config_depth:
            self._config_depth = None
            self._current_row = None


def _parse_config_contract(html: str) -> dict[str, object]:
    parser = _ConfigContractParser()
    parser.feed(html)
    return {
        "context": parser.context,
        "title": parser.title,
        "description": parser.description,
        "header_buttons": parser.header_buttons,
        "row_keys": parser.row_keys,
        "row_descriptions": parser.row_descriptions,
        "row_input_types": parser.row_input_types,
        "row_input_ids": parser.row_input_ids,
        "row_input_names": parser.row_input_names,
        "row_input_values": parser.row_input_values,
        "row_input_placeholders": parser.row_input_placeholders,
        "row_input_data_has_saved_key": parser.row_input_data_has_saved_key,
        "row_label_fors": parser.row_label_fors,
        "row_label_texts": parser.row_label_texts,
        "row_error_ids": parser.row_error_ids,
        "toggle_buttons": parser.toggle_buttons,
        "footer_buttons": parser.footer_buttons,
        "footer_button_types": parser.footer_button_types,
        "status_ids": parser.status_ids,
        "status_texts": parser.status_texts,
        "section_text": " ".join(parser.section_text),
        "panel_count": parser.panel_count,
        "header_button_types": parser.header_button_types,
        "header_button_form_ids": parser.header_button_form_ids,
    }


def _run_config_form_contract(html: str) -> dict[str, object]:
    config_contract = _parse_config_contract(html)
    row_input_names = json.dumps(config_contract["row_input_names"])
    row_input_values = json.dumps(config_contract["row_input_values"])
    row_input_placeholders = json.dumps(config_contract["row_input_placeholders"])
    row_input_data_has_saved_key = json.dumps(config_contract["row_input_data_has_saved_key"])
    status_texts = json.dumps(config_contract["status_texts"])
    start = html.index('const configForm = document.getElementById("configForm");')
    end = html.index("const runReports = {")
    snippet = html[start:end]
    script = f"""
class FakeClassList {{
  constructor(names = []) {{
    this.values = new Set(names);
  }}
  add(...names) {{
    names.forEach((name) => this.values.add(name));
  }}
  remove(...names) {{
    names.forEach((name) => this.values.delete(name));
  }}
  contains(name) {{
    return this.values.has(name);
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
  toString() {{
    return [...this.values].join(" ");
  }}
}}

class FakeElement {{
  constructor({{
    id = "",
    classNames = [],
    text = "",
    type = "",
    name = "",
    placeholder = "",
    value = "",
    dataset = {{}},
    hidden = false,
    formOwner = null,
  }} = {{}}) {{
    this.id = id;
    this.dataset = {{ ...dataset }};
    this.classList = new FakeClassList(classNames);
    this.listeners = new Map();
    this.attributes = new Map();
    this.hidden = hidden;
    this.textContent = text;
    this.type = type;
    this.value = value;
    this.placeholder = placeholder;
    this.className = classNames.join(" ");
    this.formOwner = formOwner;
    this.name = name;
  }}
  addEventListener(type, listener) {{
    if (!this.listeners.has(type)) {{
      this.listeners.set(type, []);
    }}
    this.listeners.get(type).push(listener);
  }}
  click() {{
    const event = {{
      defaultPrevented: false,
      preventDefault() {{
        this.defaultPrevented = true;
      }},
      currentTarget: this,
      target: this,
    }};
    for (const listener of this.listeners.get("click") || []) {{
      listener(event);
    }}
    if (this.type === "submit" && this.formOwner && !event.defaultPrevented) {{
      this.formOwner.requestSubmit(this);
    }}
  }}
  setAttribute(name, value) {{
    this.attributes.set(name, String(value));
    if (name === "aria-label") {{
      this.ariaLabel = String(value);
    }}
    if (name === "aria-invalid") {{
      this.ariaInvalid = String(value);
    }}
  }}
  getAttribute(name) {{
    return this.attributes.has(name) ? this.attributes.get(name) : null;
  }}
}}

class FakeForm extends FakeElement {{
  constructor(options = {{}}) {{
    super(options);
    this.elements = [];
    this.submitCount = 0;
    this.lastSubmitterId = null;
  }}
  requestSubmit(submitter = null) {{
    const event = {{
      defaultPrevented: false,
      preventDefault() {{
        this.defaultPrevented = true;
      }},
      currentTarget: this,
      submitter,
      target: this,
    }};
    for (const listener of this.listeners.get("submit") || []) {{
      listener(event);
    }}
    this.submitCount += 1;
    this.lastSubmitterId = submitter ? submitter.id : null;
  }}
}}

class FakeFormData {{
  constructor(form) {{
    this._entries = [];
    this._values = new Map();
    for (const control of form?.elements || []) {{
      if (!control?.name || control.type === "button" || control.type === "submit") {{
        continue;
      }}
      this._entries.push([control.name, control.value]);
      this._values.set(control.name, control.value);
    }}
  }}
  entries() {{
    return this._entries[Symbol.iterator]();
  }}
  get(name) {{
    return this._values.has(name) ? this._values.get(name) : null;
  }}
}}

const FormData = FakeFormData;

const nodes = {{}};
nodes.configForm = new FakeForm({{ id: "configForm" }});
const [configBaseUrlName, configApiKeyName, configModelNameName] = {row_input_names};
const [configBaseUrlValue, configApiKeyValue, configModelNameValue] = {row_input_values};
const [configBaseUrlPlaceholder, configApiKeyPlaceholder, configModelNamePlaceholder] = {row_input_placeholders};
const [configBaseUrlSavedKey, configApiKeySavedKey, configModelNameSavedKey] = {row_input_data_has_saved_key};
const [configStatusText = ""] = {status_texts};
nodes.configBaseUrl = new FakeElement({{
  id: "configBaseUrl",
  type: "url",
  name: configBaseUrlName,
  placeholder: configBaseUrlPlaceholder,
  value: configBaseUrlValue,
  dataset: {{ hasSavedKey: configBaseUrlSavedKey }},
  formOwner: nodes.configForm,
}});
nodes.configBaseUrlError = new FakeElement({{ id: "configBaseUrlError" }});
nodes.configApiKey = new FakeElement({{
  id: "configApiKey",
  type: "password",
  name: configApiKeyName,
  placeholder: configApiKeyPlaceholder,
  value: configApiKeyValue,
  dataset: {{ hasSavedKey: configApiKeySavedKey }},
  formOwner: nodes.configForm,
}});
nodes.configApiKeyError = new FakeElement({{ id: "configApiKeyError" }});
nodes.configApiKeyToggle = new FakeElement({{
  id: "configApiKeyToggle",
  text: "Show",
  classNames: ["btn", "small"],
}});
nodes.configApiKeyToggle.setAttribute("aria-label", "Show api_key value");
nodes.configModelName = new FakeElement({{
  id: "configModelName",
  type: "text",
  name: configModelNameName,
  placeholder: configModelNamePlaceholder,
  value: configModelNameValue,
  dataset: {{ hasSavedKey: configModelNameSavedKey }},
  formOwner: nodes.configForm,
}});
nodes.configModelNameError = new FakeElement({{ id: "configModelNameError" }});
nodes.configSaveButton = new FakeElement({{ id: "configSaveButton", text: "Save changes", type: "submit", formOwner: nodes.configForm }});
nodes.configTestButton = new FakeElement({{ id: "configTestButton", text: "Test connection", type: "button" }});
nodes.configStatus = new FakeElement({{ id: "configStatus", classNames: ["config-status"], text: configStatusText }});
nodes.configForm.elements = [
  nodes.configBaseUrl,
  nodes.configApiKey,
  nodes.configModelName,
  nodes.configSaveButton,
  nodes.configTestButton,
];

const document = {{
  getElementById(id) {{
    return nodes[id] || null;
  }},
}};

const toastMessages = [];
function showToast(message) {{
  toastMessages.push(message);
}}

let now = 0;
const timers = [];
function setTimeout(callback, delay) {{
  const timer = {{
    id: timers.length + 1,
    callback,
    runAt: now + delay,
  }};
  timers.push(timer);
  return timer.id;
}}
function clearTimeout(timerId) {{
  const index = timers.findIndex((timer) => timer.id === timerId);
  if (index >= 0) {{
    timers.splice(index, 1);
  }}
}}
function flushTimers() {{
  timers.sort((a, b) => a.runAt - b.runAt);
  while (timers.length) {{
    const timer = timers.shift();
    now = timer.runAt;
    timer.callback();
  }}
}}

{snippet}

const safePayloads = {{
  untouched: typeof buildConfigPayload === "function" ? buildConfigPayload(new FormData(configForm)) : null,
}};
const initialState = {{
  inputType: configApiKey.type,
  inputValue: configApiKey.value,
  inputPlaceholder: configApiKey.placeholder,
  savedKeyMarker: configApiKey.dataset.hasSavedKey || "",
  toggleLabel: configApiKeyToggle.textContent,
  toggleAriaLabel: configApiKeyToggle.getAttribute("aria-label"),
  statusText: configStatus.textContent,
}};

configApiKeyToggle.click();
const visibleState = {{
  inputType: configApiKey.type,
  inputValue: configApiKey.value,
  inputPlaceholder: configApiKey.placeholder,
  toggleLabel: configApiKeyToggle.textContent,
  toggleAriaLabel: configApiKeyToggle.getAttribute("aria-label"),
}};
configApiKeyToggle.click();
const hiddenState = {{
  inputType: configApiKey.type,
  inputValue: configApiKey.value,
  inputPlaceholder: configApiKey.placeholder,
  toggleLabel: configApiKeyToggle.textContent,
  toggleAriaLabel: configApiKeyToggle.getAttribute("aria-label"),
}};

configApiKey.value = "";
configApiKey.dataset.hasSavedKey = "false";
configSaveButton.click();
const missingKeyState = {{
  statusText: configStatus.textContent,
  statusClassName: configStatus.className,
  apiKeyError: configApiKeyError.textContent,
  apiKeyInvalid: configApiKey.getAttribute("aria-invalid"),
  submitCount: configForm.submitCount,
  submitterId: configForm.lastSubmitterId,
  toastMessages: [...toastMessages],
}};

configApiKey.dataset.hasSavedKey = "true";
configSaveButton.click();
const untouchedSaveState = {{
  statusText: configStatus.textContent,
  statusClassName: configStatus.className,
  baseUrlError: configBaseUrlError.textContent,
  apiKeyError: configApiKeyError.textContent,
  modelNameError: configModelNameError.textContent,
  baseUrlInvalid: configBaseUrl.getAttribute("aria-invalid"),
  apiKeyInvalid: configApiKey.getAttribute("aria-invalid"),
  modelNameInvalid: configModelName.getAttribute("aria-invalid"),
  submitCount: configForm.submitCount,
  submitterId: configForm.lastSubmitterId,
  toastMessages: [...toastMessages],
}};

configTestButton.click();
const untouchedTestPending = {{
  statusText: configStatus.textContent,
  statusClassName: configStatus.className,
  baseUrlError: configBaseUrlError.textContent,
  apiKeyError: configApiKeyError.textContent,
  modelNameError: configModelNameError.textContent,
  submitCount: configForm.submitCount,
}};
configModelName.value = "";
for (const listener of configModelName.listeners.get("input") || []) {{
  listener({{ currentTarget: configModelName, target: configModelName }});
}}
const invalidAfterPendingEdit = {{
  statusText: configStatus.textContent,
  statusClassName: configStatus.className,
  modelNameError: configModelNameError.textContent,
  modelNameInvalid: configModelName.getAttribute("aria-invalid"),
}};
flushTimers();
configModelName.value = "gpt-5.6";
for (const listener of configModelName.listeners.get("input") || []) {{
  listener({{ currentTarget: configModelName, target: configModelName }});
}}
configTestButton.click();
flushTimers();
const untouchedTestConnected = {{
  statusText: configStatus.textContent,
  statusClassName: configStatus.className,
}};

configForm.requestSubmit();
const enterSaveState = {{
  statusText: configStatus.textContent,
  statusClassName: configStatus.className,
  submitCount: configForm.submitCount,
  submitterId: configForm.lastSubmitterId,
  toastMessages: [...toastMessages],
}};

configApiKey.value = "replacement-secret";
safePayloads.replacement = typeof buildConfigPayload === "function" ? buildConfigPayload(new FormData(configForm)) : null;
configApiKeyToggle.click();
const replacementVisibleState = {{
  inputType: configApiKey.type,
  inputValue: configApiKey.value,
  inputPlaceholder: configApiKey.placeholder,
  toggleLabel: configApiKeyToggle.textContent,
  toggleAriaLabel: configApiKeyToggle.getAttribute("aria-label"),
}};

console.log(JSON.stringify({{
  initialState,
  visibleState,
  hiddenState,
  missingKeyState,
  untouchedSaveState,
  untouchedTestPending,
  invalidAfterPendingEdit,
  untouchedTestConnected,
  enterSaveState,
  replacementVisibleState,
  safePayloads,
}}));
"""
    return _run_node_json_script(script, skip_reason="Node.js is required for FSQ config UX script verification.")


def _run_workbench_report_state_contract(html: str) -> dict[str, object]:
    start = html.index("const runReports = {")
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
  constructor({{ id = "", dataset = {{}}, classNames = [], attributes = [], queryResults = {{}}, text = "" }} = {{}}) {{
    this.id = id;
    this.dataset = {{ ...dataset }};
    this.classList = new FakeClassList(classNames);
    this.attributeNames = new Set(attributes);
    this.queryResults = new Map(Object.entries(queryResults).map(([selector, results]) => [selector, Array.isArray(results) ? results : [results]]));
    this.listeners = new Map();
    this.hidden = false;
    this.textContent = text;
    this.className = classNames.join(" ");
  }}
  set className(value) {{
    this._className = value;
    this.classList = new FakeClassList(String(value).split(/\\s+/).filter(Boolean));
  }}
  get className() {{
    return this._className;
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
  querySelector(selector) {{
    return (this.queryResults.get(selector) || [])[0] || null;
  }}
  querySelectorAll(selector) {{
    return this.queryResults.get(selector) || [];
  }}
}}

function createWorkbenchTarget({{ title, summary = "", badge = "", runId = "", cells = [], attributes = [] }}) {{
  const queryResults = {{
    strong: new FakeElement({{ text: title }}),
  }};
  if (summary) {{
    queryResults.small = new FakeElement({{ text: summary }});
  }} else if (runId) {{
    queryResults.small = new FakeElement({{ text: runId }});
  }}
  if (badge) {{
    queryResults[".badge"] = new FakeElement({{ text: badge }});
  }}
  if (cells.length) {{
    queryResults.td = cells.map((text) => new FakeElement({{ text }}));
  }}
  return new FakeElement({{ attributes, queryResults }});
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
    this.openWorkbenchTargets = [
      createWorkbenchTarget({{
        title: "Create project flow",
        summary: "AI explore · Web · 4m ago",
        badge: "success",
        attributes: ["data-open-workbench"],
      }}),
      createWorkbenchTarget({{
        title: "Checkout smoke",
        runId: "run_20260807_131914",
        badge: "failed",
        cells: ["Checkout smoke run_20260807_131914", "↻ Strict replay", "Web", "failed", "34s"],
        attributes: ["data-open-workbench", "data-failed-run"],
      }}),
      createWorkbenchTarget({{
        title: "Settings profile",
        summary: "AI explore · macOS · yesterday",
        badge: "inconclusive",
        attributes: ["data-open-workbench"],
      }}),
      createWorkbenchTarget({{
        title: "Onboarding navigation",
        runId: "run_20260806_145522",
        badge: "success",
        cells: ["Onboarding navigation run_20260806_145522", "↻ Strict replay", "Android", "success", "58s"],
        attributes: ["data-open-workbench"],
      }}),
      createWorkbenchTarget({{
        title: "Desktop launch",
        runId: "run_20260805_112011",
        badge: "success",
        cells: ["Desktop launch run_20260805_112011", "↻ Strict replay", "Windows", "success", "27s"],
        attributes: ["data-open-workbench"],
      }}),
      createWorkbenchTarget({{
        title: "Unexpected smoke",
        runId: "run_20260801_101010",
        badge: "success",
        cells: ["Unexpected smoke run_20260801_101010", "✦ AI explore", "Web", "success", "45s"],
        attributes: ["data-open-workbench"],
      }}),
    ];
    this.steps = Array.from({{ length: 7 }}, (_, index) => new FakeElement({{
      id: `reportStep${{index}}`,
      dataset: {{ stepSlot: String(index) }},
      classNames: ["step"],
    }}));
    this.tabs = ["Screen", "UI Tree", "Logs"].map((label, index) => {{
      const tab = new FakeElement({{ classNames: ["tab", ...(index === 0 ? ["active"] : [])] }});
      tab.textContent = label;
      return tab;
    }});
    this.elements = {{
      deviceTopbarContext: new FakeElement({{ id: "deviceTopbarContext" }}),
      deviceTopbarControls: new FakeElement({{ id: "deviceTopbarControls" }}),
      runTitle: new FakeElement({{ id: "runTitle" }}),
      reportContext: new FakeElement({{ id: "reportContext" }}),
      runId: new FakeElement({{ id: "runId" }}),
      runMode: new FakeElement({{ id: "runMode" }}),
      runPlatform: new FakeElement({{ id: "runPlatform" }}),
      runDuration: new FakeElement({{ id: "runDuration" }}),
      runStatus: new FakeElement({{ id: "runStatus" }}),
      evidenceTitle: new FakeElement({{ id: "evidenceTitle" }}),
      evidenceDetail: new FakeElement({{ id: "evidenceDetail" }}),
      evidenceStepStatus: new FakeElement({{ id: "evidenceStepStatus" }}),
      evidenceSurfaceTitle: new FakeElement({{ id: "evidenceSurfaceTitle" }}),
      evidenceSurfaceNote: new FakeElement({{ id: "evidenceSurfaceNote" }}),
      evidenceSurfaceStatus: new FakeElement({{ id: "evidenceSurfaceStatus" }}),
      evidenceOutcome: new FakeElement({{ id: "evidenceOutcome" }}),
      evidenceMissingReason: new FakeElement({{ id: "evidenceMissingReason" }}),
      evidenceBeforeLabel: new FakeElement({{ id: "evidenceBeforeLabel" }}),
      evidenceBeforeText: new FakeElement({{ id: "evidenceBeforeText" }}),
      evidenceAfterLabel: new FakeElement({{ id: "evidenceAfterLabel" }}),
      evidenceAfterText: new FakeElement({{ id: "evidenceAfterText" }}),
      evidenceCapability: new FakeElement({{ id: "evidenceCapability" }}),
      evidenceTarget: new FakeElement({{ id: "evidenceTarget" }}),
      evidenceKind: new FakeElement({{ id: "evidenceKind" }}),
      evidenceStepDuration: new FakeElement({{ id: "evidenceStepDuration" }}),
      evidenceArtifactCount: new FakeElement({{ id: "evidenceArtifactCount" }}),
      evidenceBeforeArtifact: new FakeElement({{ id: "evidenceBeforeArtifact" }}),
      evidenceAfterArtifact: new FakeElement({{ id: "evidenceAfterArtifact" }}),
      evidenceUiTreeArtifact: new FakeElement({{ id: "evidenceUiTreeArtifact" }}),
      evidenceLogArtifact: new FakeElement({{ id: "evidenceLogArtifact" }}),
      verificationGoal: new FakeElement({{ id: "verificationGoal" }}),
      verificationKeyActions: new FakeElement({{ id: "verificationKeyActions" }}),
      verifierConclusion: new FakeElement({{ id: "verifierConclusion" }}),
      reportRetry: new FakeElement({{ id: "reportRetry" }}),
      reportOpenEvidence: new FakeElement({{ id: "reportOpenEvidence" }}),
      reportExport: new FakeElement({{ id: "reportExport" }}),
    }};
    for (let index = 0; index < 7; index += 1) {{
      this.elements[`reportStep${{index}}`] = this.steps[index];
      this.elements[`reportStepName${{index}}`] = new FakeElement({{ id: `reportStepName${{index}}` }});
      this.elements[`reportStepStatus${{index}}`] = new FakeElement({{ id: `reportStepStatus${{index}}` }});
      this.elements[`reportStepMeta${{index}}`] = new FakeElement({{ id: `reportStepMeta${{index}}` }});
    }}
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
    if (selector === ".step") {{
      return this.steps;
    }}
    if (selector === ".evidence-stage .tab") {{
      return this.tabs;
    }}
    throw new Error(`Unsupported selector: ${{selector}}`);
  }}
  getElementById(id) {{
    return this.elements[id] || null;
  }}
}}

const document = new FakeDocument();
const window = {{ scrollTo() {{}}, clearTimeout() {{}}, setTimeout(callback) {{ callback(); return 1; }} }};
let toastMessages = [];
function showToast(message) {{
  toastMessages.push(message);
}}

const pages = document.querySelectorAll(".page");
const navButtons = document.querySelectorAll(".nav-button[data-view]");

function showView(view) {{
  pages.forEach((page) => page.classList.toggle("active", page.id === view));
  navButtons.forEach((button) => button.classList.toggle("active", button.dataset.view === view || (view === "workbench" && button.dataset.view === "runs")));
  document.getElementById("deviceTopbarContext").hidden = view !== "device";
  document.getElementById("deviceTopbarControls").hidden = view !== "device";
  window.scrollTo({{ top: 0, behavior: "smooth" }});
}}

{snippet}

function captureState() {{
  return {{
    reportContext: document.getElementById("reportContext").textContent,
    runTitle: document.getElementById("runTitle").textContent,
    runId: document.getElementById("runId").textContent,
    runMode: document.getElementById("runMode").textContent,
    runPlatform: document.getElementById("runPlatform").textContent,
    runDuration: document.getElementById("runDuration").textContent,
    runStatusText: document.getElementById("runStatus").textContent,
    runStatusClass: document.getElementById("runStatus").className,
    evidenceTitle: document.getElementById("evidenceTitle").textContent,
    evidenceStepStatus: document.getElementById("evidenceStepStatus").textContent,
    missingReasonHidden: document.getElementById("evidenceMissingReason").hidden,
    missingReason: document.getElementById("evidenceMissingReason").textContent,
    afterText: document.getElementById("evidenceAfterText").textContent,
    conclusion: document.getElementById("verifierConclusion").textContent,
    failedStepIndexes: document.steps
      .map((step, index) => step.classList.contains("failed") ? index : -1)
      .filter((index) => index >= 0),
    activeStepIndexes: document.steps
      .map((step, index) => step.classList.contains("active") ? index : -1)
      .filter((index) => index >= 0),
    activeTabLabels: document.tabs
      .filter((tab) => tab.classList.contains("active"))
      .map((tab) => tab.textContent),
  }};
}}

const createProjectRun = document.openWorkbenchTargets[0];
const failedRun = document.openWorkbenchTargets[1];
const settingsRun = document.openWorkbenchTargets[2];
const onboardingRun = document.openWorkbenchTargets[3];
const desktopRun = document.openWorkbenchTargets[4];
const unknownRun = document.openWorkbenchTargets[5];

createProjectRun.click();
const createProjectState = captureState();

failedRun.click();
const failedState = captureState();

settingsRun.click();
const settingsState = captureState();

onboardingRun.click();
const onboardingState = captureState();

desktopRun.click();
const desktopState = captureState();

document.tabs[2].click();
const logsSelectedState = {{
  activeTabLabels: document.tabs
    .filter((tab) => tab.classList.contains("active"))
    .map((tab) => tab.textContent),
}};

unknownRun.click();
const unknownState = captureState();

console.log(JSON.stringify({{
  createProjectState,
  failedState,
  settingsState,
  onboardingState,
  desktopState,
  logsSelectedState,
  unknownState,
  toastMessages,
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


def test_fsq_shared_content_grid_fills_shell_width_without_changing_workspace_or_device_layouts() -> None:
    html = _read_fsq_control_plane_product_ux_html()
    rules = _extract_source_viewer_css_rules(html)
    page_children = _parse_page_layout_contract(html)
    css = _extract_style_block(html)

    page_rule = _extract_css_declarations(rules[".page"][0])
    page_head_rule = _extract_css_declarations(rules[".page-head"][0])
    content_grid_rule = _extract_css_declarations(rules[".content-grid"][0])
    workspace_rule = _extract_css_declarations(rules[".github-workspace"][0])
    device_workbench_rule = _extract_css_declarations(rules[".device-workbench"][0])
    device_content_grid_rule = _extract_css_declarations(rules["#device .content-grid"][0])

    assert page_rule["padding"] == "28px"
    assert page_head_rule["display"] == "flex"
    assert page_head_rule["justify-content"] == "space-between"
    assert page_head_rule["margin"] == "2px 0 24px"
    assert "max-width" not in page_head_rule
    assert content_grid_rule["width"] == "100%"
    assert content_grid_rule["min-width"] == "0"
    assert "max-width" not in content_grid_rule
    assert "margin" not in content_grid_rule

    assert _find_page_child_with_class(page_children, "home", "content-grid")["class"] == "content-grid"
    assert _find_page_child_with_class(page_children, "runs", "content-grid")["class"] == "content-grid"
    assert _find_page_child_with_class(page_children, "config", "content-grid")["class"] == "content-grid"
    settings_content = _find_page_child_with_class(page_children, "settings", "content-grid")
    assert settings_content["class"] == "content-grid"
    assert "max-width" not in settings_content.get("style", "")

    assert _find_page_child_with_class(page_children, "workspace", "github-workspace")["class"] == "github-workspace"
    assert workspace_rule["height"] == "calc(100vh - 76px)"
    assert workspace_rule["grid-template-columns"] == "310px minmax(0, 1fr)"

    assert _find_page_child_with_class(page_children, "device", "content-grid device-workbench")["class"] == "content-grid device-workbench"
    assert device_workbench_rule["grid-template-columns"] == "430px minmax(520px, 1fr)"
    assert device_workbench_rule["min-height"] == "calc(100vh - 108px)"
    assert device_content_grid_rule["max-width"] == "none"
    responsive_device_selectors, responsive_device_rule = _extract_media_rule_with_selector(
        css,
        media_condition="max-width: 1120px",
        selector=".device-workbench",
    )
    assert responsive_device_selectors == [".device-workbench", ".structured-case"]
    assert responsive_device_rule["grid-template-columns"] == "1fr"


def test_fsq_overview_page_uses_start_run_workbench_contract() -> None:
    html = _read_fsq_control_plane_product_ux_html()
    rules = _extract_source_viewer_css_rules(html)
    css = _extract_style_block(html)
    page_children = _parse_page_layout_contract(html)
    home_section = _extract_home_section(html)

    home_rule = _extract_css_declarations(rules["#home"][0])

    assert home_rule["padding"] == "16px"
    _assert_media_rule_declaration(
        css,
        media_condition="max-width: 820px",
        selector="#home",
        property_name="padding",
        expected_value="18px 14px 88px",
    )
    assert [child.get("class") for child in page_children["home"]] == ["content-grid"]
    assert '<div class="page-head">' not in home_section
    assert '<p class="eyebrow">' not in home_section
    assert home_section.count("<h1>") == 1
    assert re.search(r'<div class="content-grid">\s*<section class="card overview-start-panel">', home_section)
    assert re.search(
        r'<section class="card overview-start-panel">[\s\S]*?<div class="card-head">[\s\S]*?<div class="overview-start-copy">[\s\S]*?<h1>Start a run</h1>\s*<p class="muted">Start with the core loop',
        home_section,
    )
    assert 'class="btn" id="learnFsq">How FSQ works</button>' in home_section
    assert re.search(r'<section class="card overview-start-panel">[\s\S]*?<div class="launch-grid">', home_section)
    assert "<h2>Explore with AI</h2>" in home_section
    assert "<h2>Replay a Case</h2>" in home_section
    assert "Core loop" not in home_section


def test_fsq_config_page_uses_three_llm_connection_keys_only() -> None:
    html = _read_fsq_control_plane_product_ux_html()
    rules = _extract_source_viewer_css_rules(html)
    css = _extract_style_block(html)
    config_contract = _parse_config_contract(html)
    config_section = _extract_config_section(html)
    page_children = _parse_page_layout_contract(html)

    config_rule = _extract_css_declarations(rules["#config"][0])
    config_panel_rule = _extract_css_declarations(rules[".config-panel"][0])
    config_form_rule = _extract_css_declarations(rules[".config-panel form"][0])
    config_row_rule = _extract_css_declarations(rules[".config-row"][0])
    config_key_rule = _extract_css_declarations(rules[".config-key"][0])
    config_control_rule = _extract_css_declarations(rules[".config-control"][0])
    config_input_wrap_rule = _extract_css_declarations(rules[".config-input-wrap"][0])
    config_error_rule = _extract_css_declarations(rules[".config-error"][0])
    config_footer_rule = _extract_css_declarations(rules[".config-footer"][0])
    config_status_rule = _extract_css_declarations(rules[".config-status"][0])

    assert config_rule["padding"] == "16px"
    _assert_media_rule_declaration(
        css,
        media_condition="max-width: 820px",
        selector="#config",
        property_name="padding",
        expected_value="18px 14px 88px",
    )
    assert [child.get("class") for child in page_children["config"]] == ["content-grid"]
    assert '<div class="page-head">' not in config_section
    assert '<p class="eyebrow">' not in config_section
    assert config_section.count("<h1>") == 1
    assert re.search(
        r'<section class="card config-panel">\s*<div class="card-head">[\s\S]*?<h1>LLM Configuration</h1>[\s\S]*?<p class="muted">Connection values for the active Workspace LLM endpoint\.</p>[\s\S]*?id="configSaveButton"[\s\S]*?</div>\s*<form id="configForm"',
        config_section,
    )

    assert config_contract["context"] == ""
    assert config_contract["title"] == "LLM Configuration"
    assert config_contract["description"] == "Connection values for the active Workspace LLM endpoint."
    assert config_contract["header_buttons"] == ["Save changes"]
    assert config_contract["panel_count"] == 1
    assert config_contract["row_keys"] == ["base_url", "api_key", "model_name"]
    assert config_contract["row_input_types"] == ["url", "password", "text"]
    assert config_contract["row_input_ids"] == ["configBaseUrl", "configApiKey", "configModelName"]
    assert config_contract["row_input_names"] == ["base_url", "api_key", "model_name"]
    assert config_contract["row_input_values"][1] == ""
    assert config_contract["row_input_placeholders"][1] == "Saved key ••••••••"
    assert config_contract["row_input_data_has_saved_key"][1] == "true"
    assert config_contract["row_label_fors"] == ["configBaseUrl", "configApiKey", "configModelName"]
    assert config_contract["row_label_texts"] == ["base_url", "api_key", "model_name"]
    assert config_contract["row_error_ids"] == [
        "configBaseUrlError",
        "configApiKeyError",
        "configModelNameError",
    ]
    assert config_contract["header_button_types"] == ["submit"]
    assert config_contract["header_button_form_ids"] == ["configForm"]
    assert config_contract["toggle_buttons"] == [
        {
            "aria_label": "Show api_key value",
            "text": "Show",
        }
    ]
    assert config_contract["footer_buttons"] == ["Test connection"]
    assert config_contract["footer_button_types"] == ["button"]
    assert config_contract["status_ids"] == ["configStatus"]
    assert "compatible model service endpoint" in str(config_contract["row_descriptions"][0]).lower()
    assert "leave blank to keep the saved key" in str(config_contract["row_descriptions"][1]).lower()
    assert "deployment name" in str(config_contract["row_descriptions"][2]).lower()

    for removed_text in [
        "provider",
        "account",
        "reconnect",
        "run defaults",
        "default platform",
        "post-action delay",
        "max agent turns",
        "browser executable",
        "project environment",
        ".env",
    ]:
        assert removed_text not in config_section.lower()

    assert config_panel_rule["width"] == "100%"
    assert config_panel_rule["display"] == "grid"
    assert config_panel_rule["min-height"] == "calc(100vh - 108px)"
    assert config_form_rule["display"] == "flex"
    assert config_row_rule["display"] == "grid"
    assert config_row_rule["grid-template-columns"] == "minmax(0, 1fr) minmax(280px, 420px)"
    assert config_key_rule["display"] == "grid"
    assert config_control_rule["display"] == "grid"
    assert config_input_wrap_rule["display"] == "grid"
    assert config_input_wrap_rule["grid-template-columns"] == "minmax(0, 1fr) auto"
    assert config_error_rule["color"] == "var(--cp-danger)"
    assert config_footer_rule["display"] == "flex"
    assert config_status_rule["border"] == "1px solid var(--cp-border)"
    _assert_media_rule_declaration(
        css,
        media_condition="max-width: 1120px",
        selector=".config-row",
        property_name="grid-template-columns",
        expected_value="1fr",
    )
    _assert_media_rule_declaration(
        css,
        media_condition="max-width: 1120px",
        selector=".config-input-wrap",
        property_name="grid-template-columns",
        expected_value="1fr",
    )


def test_fsq_config_workbench_panel_css_stacks_header_at_mobile_breakpoint() -> None:
    html = _read_fsq_control_plane_product_ux_html()
    rules = _extract_source_viewer_css_rules(html)
    css = _extract_style_block(html)

    config_header_rule = _extract_css_declarations(rules[".config-panel .card-head"][0])
    config_header_copy_rule = _extract_css_declarations(rules[".config-panel-header-copy"][0])
    config_heading_rule = _extract_css_declarations(rules[".config-panel-header-copy h1"][0])

    assert config_header_rule["align-items"] == "flex-start"
    assert config_header_copy_rule["display"] == "grid"
    assert config_header_copy_rule["gap"] == "8px"
    assert config_heading_rule["font-size"] == "18px"
    assert config_heading_rule["line-height"] == "1.2"
    _assert_media_rule_declaration(
        css,
        media_condition="max-width: 820px",
        selector=".config-panel .card-head",
        property_name="flex-direction",
        expected_value="column",
    )
    _assert_media_rule_declaration(
        css,
        media_condition="max-width: 820px",
        selector=".config-panel .card-head .btn",
        property_name="width",
        expected_value="100%",
    )


def test_fsq_overview_start_run_panel_css_preserves_card_and_launch_stack_behavior() -> None:
    html = _read_fsq_control_plane_product_ux_html()
    rules = _extract_source_viewer_css_rules(html)
    css = _extract_style_block(html)

    overview_panel_rule = _extract_css_declarations(rules[".overview-start-panel"][0])
    overview_panel_header_rule = _extract_css_declarations(rules[".overview-start-panel .card-head"][0])
    overview_panel_body_rule = _extract_css_declarations(rules[".overview-start-panel .card-body"][0])

    assert overview_panel_rule["width"] == "100%"
    assert overview_panel_rule["overflow"] == "hidden"
    assert overview_panel_header_rule["align-items"] == "flex-start"
    assert overview_panel_body_rule["display"] == "grid"
    assert overview_panel_body_rule["gap"] == "18px"
    _assert_media_rule_declaration(
        css,
        media_condition="max-width: 820px",
        selector=".launch-grid",
        property_name="grid-template-columns",
        expected_value="1fr",
    )


def test_fsq_settings_page_uses_edge_to_edge_workbench_contract() -> None:
    html = _read_fsq_control_plane_product_ux_html()
    page_children = _parse_page_layout_contract(html)
    settings_section = _extract_settings_section(html)

    assert [child.get("class") for child in page_children["settings"]] == ["content-grid"]
    assert settings_section.startswith('<section class="page" id="settings">')
    assert '<div class="page-head">' not in settings_section
    assert '<p class="eyebrow">' not in settings_section
    assert re.search(r'<div class="content-grid">\s*<section class="card settings-panel">', settings_section)
    assert re.search(
        r'<section class="card settings-panel">\s*<div class="card-head">[\s\S]*?<div class="settings-panel-header-copy">[\s\S]*?<h1>Application settings</h1>\s*<p class="muted">Global preferences for the local Control Plane\. These settings are not tied to a workspace\.</p>[\s\S]*?</div>\s*</div>',
        settings_section,
    )
    assert re.search(
        r'<div class="card-body settings-panel-body">[\s\S]*?<section class="settings-section">\s*<h2 class="settings-section-label">General</h2>[\s\S]*?<div class="settings-list">[\s\S]*?<div class="settings-row"><span><strong>Theme</strong><small>Use light, dark, or follow the operating system\.</small></span><select class="select">',
        settings_section,
    )
    assert '<p class="settings-section-label">General</p>' not in settings_section
    assert re.search(r'<section class="settings-section">\s*<h2 class="settings-section-label">General</h2>', settings_section)
    assert re.search(r'</section>\s*</div>\s*</section>\s*</div>\s*</section>$', settings_section)
    assert settings_section.count('class="settings-row"') == 4


def test_extract_settings_section_preserves_markup_after_nested_section_close() -> None:
    html = """
    <main>
      <section class="page" id="settings">
        <div class="content-grid">
          <section class="settings-section">
            <h2 class="settings-section-label">General</h2>
          </section>
          <div class="after-nested">Still included</div>
        </div>
      </section>
    </main>
    """

    settings_section = _extract_settings_section(html)

    assert settings_section.startswith('<section class="page" id="settings">')
    assert '<h2 class="settings-section-label">General</h2>' in settings_section
    assert '<div class="after-nested">Still included</div>' in settings_section
    assert re.search(r'</section>\s*<div class="after-nested">Still included</div>\s*</div>\s*</section>$', settings_section)


def test_fsq_settings_workbench_panel_css_preserves_viewport_fill_and_mobile_padding() -> None:
    html = _read_fsq_control_plane_product_ux_html()
    rules = _extract_source_viewer_css_rules(html)
    css = _extract_style_block(html)

    settings_rule = _extract_css_declarations(rules["#settings"][0])
    settings_panel_rule = _extract_css_declarations(rules[".settings-panel"][0])
    settings_header_rule = _extract_css_declarations(rules[".settings-panel .card-head"][0])
    settings_header_copy_rule = _extract_css_declarations(rules[".settings-panel-header-copy"][0])
    settings_heading_rule = _extract_css_declarations(rules[".settings-panel-header-copy h1"][0])
    settings_body_rule = _extract_css_declarations(rules[".settings-panel-body"][0])

    assert settings_rule["padding"] == "16px"
    _assert_media_rule_declaration(
        css,
        media_condition="max-width: 820px",
        selector="#settings",
        property_name="padding",
        expected_value="18px 14px calc(88px + env(safe-area-inset-bottom, 0px))",
    )
    assert settings_panel_rule["width"] == "100%"
    assert settings_panel_rule["display"] == "grid"
    assert settings_panel_rule["grid-template-rows"] == "auto 1fr"
    assert settings_panel_rule["min-height"] == "calc(100vh - 108px)"
    assert settings_panel_rule["overflow"] == "hidden"
    assert settings_header_rule["align-items"] == "flex-start"
    assert settings_header_copy_rule["display"] == "grid"
    assert settings_header_copy_rule["gap"] == "8px"
    assert settings_heading_rule["font-size"] == "18px"
    assert settings_heading_rule["line-height"] == "1.2"
    assert settings_body_rule["display"] == "grid"
    assert settings_body_rule["align-content"] == "start"
    _assert_media_rule_declaration(
        css,
        media_condition="max-width: 820px",
        selector=".settings-panel .card-head",
        property_name="flex-direction",
        expected_value="column",
    )


def test_fsq_config_form_behavior_validates_toggle_and_status_feedback() -> None:
    payload = _run_config_form_contract(_read_fsq_control_plane_product_ux_html())

    assert payload["initialState"] == {
        "inputType": "password",
        "inputValue": "",
        "inputPlaceholder": "Saved key ••••••••",
        "savedKeyMarker": "true",
        "toggleLabel": "Show",
        "toggleAriaLabel": "Show api_key value",
        "statusText": "Prototype only. Validation runs locally. Leave api_key blank to keep the saved key.",
    }
    assert payload["visibleState"] == {
        "inputType": "text",
        "inputValue": "",
        "inputPlaceholder": "Saved key ••••••••",
        "toggleLabel": "Hide",
        "toggleAriaLabel": "Hide api_key value",
    }
    assert payload["hiddenState"] == {
        "inputType": "password",
        "inputValue": "",
        "inputPlaceholder": "Saved key ••••••••",
        "toggleLabel": "Show",
        "toggleAriaLabel": "Show api_key value",
    }
    assert payload["missingKeyState"] == {
        "statusText": "Please correct the highlighted LLM configuration values before continuing.",
        "statusClassName": "config-status invalid",
        "apiKeyError": "Enter an API key.",
        "apiKeyInvalid": "true",
        "submitCount": 1,
        "submitterId": "configSaveButton",
        "toastMessages": [],
    }
    assert payload["untouchedSaveState"] == {
        "statusText": "Prototype: LLM configuration saved locally for this mockup. Existing api_key kept.",
        "statusClassName": "config-status success",
        "baseUrlError": "",
        "apiKeyError": "",
        "modelNameError": "",
        "baseUrlInvalid": "false",
        "apiKeyInvalid": "false",
        "modelNameInvalid": "false",
        "submitCount": 2,
        "submitterId": "configSaveButton",
        "toastMessages": ["Prototype: LLM configuration saved locally for this mockup. Existing api_key kept."],
    }
    assert payload["untouchedTestPending"] == {
        "statusText": "Prototype: testing connection with the saved api_key…",
        "statusClassName": "config-status pending",
        "baseUrlError": "",
        "apiKeyError": "",
        "modelNameError": "",
        "submitCount": 2,
    }
    assert payload["invalidAfterPendingEdit"] == {
        "statusText": "Please correct the highlighted LLM configuration values before continuing.",
        "statusClassName": "config-status invalid",
        "modelNameError": "Enter a model or deployment name.",
        "modelNameInvalid": "true",
    }
    assert payload["untouchedTestConnected"] == {
        "statusText": "Prototype: connected with the saved api_key. No network request was sent.",
        "statusClassName": "config-status success",
    }
    assert payload["enterSaveState"] == {
        "statusText": "Prototype: LLM configuration saved locally for this mockup. Existing api_key kept.",
        "statusClassName": "config-status success",
        "submitCount": 3,
        "submitterId": None,
        "toastMessages": [
            "Prototype: LLM configuration saved locally for this mockup. Existing api_key kept.",
            "Prototype: LLM configuration saved locally for this mockup. Existing api_key kept.",
        ],
    }
    assert payload["replacementVisibleState"] == {
        "inputType": "text",
        "inputValue": "replacement-secret",
        "inputPlaceholder": "Saved key ••••••••",
        "toggleLabel": "Hide",
        "toggleAriaLabel": "Hide api_key value",
    }
    assert payload["safePayloads"]["untouched"] == {
        "base_url": "https://models.example.test/openai/v1",
        "model_name": "gpt-5.6",
    }
    assert payload["safePayloads"]["replacement"] == {
        "base_url": "https://models.example.test/openai/v1",
        "api_key": "replacement-secret",
        "model_name": "gpt-5.6",
    }


def test_extract_media_rule_with_selector_rejects_unrelated_one_column_rules() -> None:
    css = """
    @media (max-width: 1120px) {
      .device-workbench,
      .structured-case {
        min-height: auto;
      }

      .report-meta-list {
        grid-template-columns: 1fr;
      }
    }
    """

    with pytest.raises(AssertionError, match=r"missing declaration .*grid-template-columns.*1fr"):
        _assert_media_rule_declaration(
            css,
            media_condition="max-width: 1120px",
            selector=".device-workbench",
            property_name="grid-template-columns",
            expected_value="1fr",
        )


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


def test_fsq_workbench_entries_keep_pre_task2_routing_markup() -> None:
    html = _read_fsq_control_plane_product_ux_html()
    entry_tags = re.findall(r"<(?:button|tr)[^>]*data-open-workbench[^>]*>", html)

    assert entry_tags
    assert len([tag for tag in entry_tags if "data-failed-run" in tag]) == 2
    assert all("data-run-" not in tag for tag in entry_tags)
    assert all("data-report-template" not in tag for tag in entry_tags)


def test_fsq_workbench_entries_keep_pre_task2_handler_shape() -> None:
    html = _read_fsq_control_plane_product_ux_html()

    assert "function openRunReport" not in html
    assert 'document.querySelectorAll("[data-open-workbench]").forEach((target) => {' in html
    assert "const title = visibleRunTitle(target);" in html
    assert "renderRunReport(runReportStates[title] || buildFallbackRunReportState(target, title));" in html
    assert 'showView("workbench");' in html
    assert 'const reportEvidenceTabs = [...document.querySelectorAll(".evidence-stage .tab")];' in html


def test_fsq_run_report_structure_matches_approved_contract() -> None:
    contract = _parse_workbench_contract(_read_fsq_control_plane_product_ux_html())

    assert contract["panel_count"] == 2
    assert contract["header_buttons"] == ["← Back to Runs"]
    assert contract["header_action_buttons"] == ["Retry", "Open evidence folder", "Export report"]
    assert contract["meta_terms"] == ["Run ID", "Mode", "Platform", "Duration", "Final status"]
    assert contract["phase_labels"] == ["Planning", "Execution", "Verification"]
    assert contract["tab_labels"] == ["Screen", "UI Tree", "Logs"]
    assert contract["timeline_step_count"] == 7
    assert "Inspector" not in contract["section_headings"]
    assert not contract["inspector_mentions"]
    assert "Execution timeline" in contract["section_headings"]
    assert "Action details" in contract["section_headings"]
    assert any(heading.startswith("Artifacts") for heading in contract["section_headings"])
    assert "Verification summary" in contract["section_headings"]
    assert contract["compare_labels"] == ["Before", "After"]


def test_fsq_run_report_css_uses_two_columns_and_stacks_responsively() -> None:
    css = _extract_style_block(_read_fsq_control_plane_product_ux_html())
    workbench_layout_rule = _extract_css_declarations(_extract_source_viewer_css_rules(_read_fsq_control_plane_product_ux_html())[".workbench-layout"][0])

    assert workbench_layout_rule["grid-template-columns"] == "280px minmax(0, 1fr)"
    assert re.search(
        r"@media\s*\(max-width:\s*1120px\)\s*\{[\s\S]*?\.workbench-layout\s*\{[\s\S]*?grid-template-columns:\s*1fr;",
        css,
    )
    assert re.search(
        r"@media\s*\(max-width:\s*820px\)\s*\{[\s\S]*?\.workbench-layout\s*\{[\s\S]*?display:\s*block;",
        css,
    )


def test_fsq_run_report_state_contract_keeps_run_metadata_and_tab_resets() -> None:
    payload = _run_workbench_report_state_contract(_read_fsq_control_plane_product_ux_html())

    assert payload["createProjectState"] == {
        "reportContext": "RUN REPORT",
        "runTitle": "Create project flow",
        "runId": "run_20260807_135642",
        "runMode": "AI explore",
        "runPlatform": "Web",
        "runDuration": "1m 42s",
        "runStatusText": "success",
        "runStatusClass": "tag success",
        "evidenceTitle": "assertVisible project row",
        "evidenceStepStatus": "passed",
        "missingReasonHidden": True,
        "missingReason": "",
        "afterText": "New matching row is visible and selected.",
        "conclusion": "Verification passed because the after evidence shows the new project row in the selected state.",
        "failedStepIndexes": [],
        "activeStepIndexes": [5],
        "activeTabLabels": ["Screen"],
    }
    assert payload["failedState"]["runTitle"] == "Checkout smoke"
    assert payload["failedState"]["runId"] == "run_20260807_131914"
    assert payload["failedState"]["runMode"] == "Strict replay"
    assert payload["failedState"]["runPlatform"] == "Web"
    assert payload["failedState"]["runDuration"] == "34s"
    assert payload["failedState"]["runStatusText"] == "failed"
    assert payload["failedState"]["runStatusClass"] == "tag failed"
    assert payload["failedState"]["evidenceTitle"] == "assertVisible checkout confirmation"
    assert payload["failedState"]["evidenceStepStatus"] == "failed"
    assert payload["failedState"]["missingReasonHidden"] is False
    assert "missing after evidence" in payload["failedState"]["missingReason"].lower()
    assert "timed out" in payload["failedState"]["afterText"].lower()
    assert "verification failed" in payload["failedState"]["conclusion"].lower()
    assert payload["failedState"]["failedStepIndexes"] == [5]
    assert payload["failedState"]["activeStepIndexes"] == [5]
    assert payload["settingsState"]["reportContext"] == "RUN REPORT · Inconclusive"
    assert payload["settingsState"]["runTitle"] == "Settings profile"
    assert payload["settingsState"]["runId"] == "run_20260806_172305"
    assert payload["settingsState"]["runMode"] == "AI explore"
    assert payload["settingsState"]["runPlatform"] == "macOS"
    assert payload["settingsState"]["runDuration"] == "2m 09s"
    assert payload["settingsState"]["runStatusText"] == "inconclusive"
    assert payload["settingsState"]["runStatusClass"] == "tag warning"
    assert payload["settingsState"]["evidenceTitle"] == "Evidence verifier"
    assert payload["settingsState"]["evidenceStepStatus"] == "inconclusive"
    assert payload["settingsState"]["activeStepIndexes"] == [6]
    assert "inconclusive" in payload["settingsState"]["conclusion"].lower()
    assert payload["onboardingState"]["runTitle"] == "Onboarding navigation"
    assert payload["onboardingState"]["runId"] == "run_20260806_145522"
    assert payload["onboardingState"]["runMode"] == "Strict replay"
    assert payload["onboardingState"]["runPlatform"] == "Android"
    assert payload["onboardingState"]["runDuration"] == "58s"
    assert payload["onboardingState"]["runStatusText"] == "success"
    assert payload["desktopState"]["runTitle"] == "Desktop launch"
    assert payload["desktopState"]["runId"] == "run_20260805_112011"
    assert payload["desktopState"]["runMode"] == "Strict replay"
    assert payload["desktopState"]["runPlatform"] == "Windows"
    assert payload["desktopState"]["runDuration"] == "27s"
    assert payload["desktopState"]["runStatusText"] == "success"
    assert payload["logsSelectedState"] == {"activeTabLabels": ["Logs"]}
    assert payload["unknownState"]["reportContext"] == "RUN REPORT · Success"
    assert payload["unknownState"]["runTitle"] == "Unexpected smoke"
    assert payload["unknownState"]["runId"] == "run_20260801_101010"
    assert payload["unknownState"]["runMode"] == "AI explore"
    assert payload["unknownState"]["runPlatform"] == "Web"
    assert payload["unknownState"]["runDuration"] == "45s"
    assert payload["unknownState"]["runStatusText"] == "success"
    assert payload["unknownState"]["runTitle"] != "Create project flow"
    assert payload["unknownState"]["activeTabLabels"] == ["Screen"]
    assert "fallback metadata" in payload["unknownState"]["conclusion"].lower()
