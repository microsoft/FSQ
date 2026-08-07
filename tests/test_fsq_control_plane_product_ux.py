# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import json
import shutil
import subprocess
from pathlib import Path

import pytest


def test_fsq_code_copy_state_survives_code_rerenders() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is required for FSQ product UX script verification.")
    html_path = Path(__file__).parents[1] / "docs" / "ux" / "fsq-control-plane-product-ux.html"
    html = html_path.read_text(encoding="utf-8")
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
