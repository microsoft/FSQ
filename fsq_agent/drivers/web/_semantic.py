# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Native semantic structure plus allowlisted DOM facts, before compaction."""

import json
import re
from typing import Any

import yaml

_DESCRIPTOR = re.compile(r'^(?P<role>[\w-]+)(?:\s+(?P<name>"(?:\\.|[^"\\])*"))?(?P<tail>.*)$')
_STATE = re.compile(r"\[([\w-]+)(?:=([^\]]+))?\]")
REGIONS = frozenset({"main", "navigation", "banner", "complementary", "contentinfo", "region", "form", "dialog", "alertdialog", "search"})
CONTROLS = frozenset(
    {
        "button",
        "link",
        "textbox",
        "searchbox",
        "combobox",
        "listbox",
        "checkbox",
        "radio",
        "switch",
        "slider",
        "spinbutton",
        "tab",
        "menuitem",
        "menuitemcheckbox",
        "menuitemradio",
        "treeitem",
        "option",
        "heading",
        "list",
        "table",
        "grid",
        "iframe",
    }
)

DOM_FACTS = """node => {
  const attributes = {};
  for (const key of ['id','name','type','title','placeholder','aria-label','aria-description',
                    'role','href','data-testid','aria-setsize','aria-posinset','aria-rowcount',
                    'aria-modal','aria-expanded','aria-checked','aria-selected']) {
    if (node.hasAttribute(key)) attributes[key] = node.getAttribute(key);
  }
  const state = {visible: !!(node.getClientRects().length &&
      getComputedStyle(node).visibility !== 'hidden'),
    enabled: !node.matches(':disabled,[aria-disabled="true"]'),
    focused: node.ownerDocument.activeElement === node};
  if ('checked' in node) state.checked = node.checked;
  if ('selected' in node) state.selected = node.selected;
  if ('required' in node) state.required = node.required;
  if ('readOnly' in node) state.readonly = node.readOnly;
  if ('multiple' in node) state.multiple = node.multiple;
  state.editable = !node.disabled && !node.readOnly && (node.isContentEditable ||
    node.matches('textarea,input:not([type="button"]):not([type="submit"]):not([type="reset"]):not([type="checkbox"]):not([type="radio"]):not([type="file"]):not([type="hidden"])'));
  const options = node.tagName === 'SELECT' ? Array.from(node.options).map((o,index)=>({
    label: o.label, value:o.value, index, selected:o.selected, disabled:o.disabled ||
      (o.parentElement.tagName === 'OPTGROUP' && o.parentElement.disabled)
  })) : [];
  const items = node.matches('ul,ol,[role="list"],[role="listbox"],table,[role="grid"]') ?
    Array.from(node.children).map((n,index)=>({index,
      name:(n.innerText || n.textContent || '').trim()})) : [];
  return {tag:node.tagName.toLowerCase(),attributes,state,options,items,
    modal:node.matches('dialog[open],[aria-modal="true"]'),
    input_value_omitted:node.matches('input,textarea,[contenteditable="true"]')};
}"""


def parse_semantics(snapshot: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    candidates: list[dict[str, Any]] = []

    def descriptor(value: str, parent: dict[str, str] | None, depth: int) -> dict[str, Any]:
        match = _DESCRIPTOR.match(value)
        if match is None:
            return {"role": "text", "name": value, "state": {}, "children": []}
        role = match["role"]
        name = json.loads(match["name"]) if match["name"] else ""
        state: dict[str, Any] = {}
        native_ref = None
        for key, raw in _STATE.findall(match["tail"]):
            if key == "ref":
                native_ref = raw
            else:
                state[key] = True if not raw or raw == "true" else False if raw == "false" else raw
        node: dict[str, Any] = {"role": role, "name": name, "state": state, "children": []}
        if native_ref:
            candidates.append({**node, "native_ref": native_ref, "parent": parent, "depth": depth})
        return node

    def walk(values: Any, parent: dict[str, str] | None = None, depth: int = 0) -> list[dict[str, Any]]:
        result = []
        for entry in values if isinstance(values, list) else [values]:
            if isinstance(entry, str):
                result.append(descriptor(entry, parent, depth))
            elif isinstance(entry, dict):
                for key, children in entry.items():
                    if str(key).startswith("/"):
                        result.append({"role": "property", "name": str(key), "value": children, "state": {}, "children": []})
                        continue
                    node = descriptor(str(key), parent, depth)
                    if isinstance(children, (list, dict)):
                        node["children"] = walk(children, {"role": node["role"], "name": node["name"]}, depth + 1)
                    elif children is not None:
                        node["children"] = [{"role": "text", "name": str(children), "state": {}, "children": []}]
                    if node["role"] in {"textbox", "searchbox"}:
                        node["children"] = []
                        node["input_value_omitted"] = True
                    result.append(node)
        return result

    return walk(yaml.safe_load(snapshot) or []), candidates
