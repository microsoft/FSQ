# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import json
import shutil
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from fsq_agent._capability_bootstrap import build_capability_registry
from fsq_agent.adapters.coding_agent._harness_tools import HarnessToolAdapter
from fsq_agent.agent_engine import ToolCall
from fsq_agent.case_dsl import FsqCaseLoader, FsqExecutableStepAdapter
from fsq_agent.config import Settings
from fsq_agent.core import ArtifactStore, HarnessFactory
from fsq_agent.execution import DynamicExecutionRequest, DynamicExecutionService, run_fsq_core_case
from fsq_agent.models import DynamicAgentOutcome, PostActionDelaySettings, Task, VerificationResult, WebLocator


@pytest.fixture
def local_product_page():
    selected = []
    order = ["a", "b"]

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            request = urlsplit(self.path)
            if request.path == "/selected":
                selected.append(parse_qs(request.query)["item"][0])
                body = b"ok"
            else:
                products = "<li><button disabled>Choose</button></li>"
                for item in order:
                    products += f"<li><button onclick=\"fetch('/selected?item={item}').then(() => document.getElementById('done').hidden = false)\">Choose</button></li>"
                body = (
                    "<!doctype html><title>Replay fixture</title><main><h1>Products</h1>"
                    f'<ul aria-label="Products">{products}</ul>'
                    '<div id="done" role="status" aria-label="Selection done" hidden>Selected</div>'
                    '<section aria-label="Options"><select><option value="blue">Blue label</option></select></section>'
                    "</main>"
                ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            return None

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", selected, order
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)
        assert not worker.is_alive()


def _browser_executable() -> Path:
    candidates = [
        Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
        Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
    ]
    candidates.extend(Path(found) for command in ["chromium", "chromium-browser", "google-chrome"] if (found := shutil.which(command)))
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    pytest.skip("A local Chrome/Edge/Chromium executable is required for browser recording/replay.")


@pytest.mark.asyncio
async def test_dynamic_web_recording_replays_rule_in_fresh_browser_context(tmp_path: Path, local_product_page) -> None:
    executable = _browser_executable()
    url, selected, order = local_product_page
    settings = Settings()
    settings.workspace.root_dir = tmp_path
    settings.harness.platform = "web"
    settings.harness.web.browser_executable_path = str(executable)
    settings.harness.web.channel = "msedge" if executable.name == "msedge.exe" else "chrome"
    settings.harness.web.headless = True
    settings.output.runs_dir = tmp_path / "runs"
    settings.cases.dir = tmp_path / "cases"
    settings.execution.post_action_delay_seconds = PostActionDelaySettings(platform=0, common=0)
    rule = {
        "page": "main",
        "steps": [
            {"kind": "role", "role": "list", "name": "Products"},
            {"kind": "role", "role": "listitem"},
            {
                "kind": "filter",
                "has": {"steps": [{"kind": "role", "role": "button", "name": "Choose", "disabled": False}, {"kind": "filter", "visible": True}]},
            },
            {"kind": "first"},
            {"kind": "role", "role": "button", "name": "Choose", "disabled": False},
        ],
    }
    task = Task(id="first-eligible", name="Choose the first eligible product", description="Choose the first eligible product and verify completion.", planning_reference_kind="goal")
    responses = []

    class ScriptedAgent:
        async def run_in_context(self, task, context, event_sink=None, **execution_context):
            run_dir = settings.output.runs_dir / context.run_id
            harness = HarnessFactory().create_harness(platform="web", harness_settings=settings.harness, artifact_store=ArtifactStore(run_dir))
            adapter = HarnessToolAdapter(
                harness, run_id=context.run_id, platform="web", evidence_sink=execution_context["evidence_sink"], post_action_delay_seconds=settings.execution.post_action_delay_seconds
            )
            tools = {tool.name: tool for tool in adapter.build_tools()}
            calls = [
                ("start_browser", {}, "passed"),
                ("navigate_to", {"page": "main", "url": url}, "passed"),
                ("ui_snapshot", {"scope": {"kind": "page", "page": "main"}}, "passed"),
                ("click_on", {"target": {"page": "main", "steps": [{"kind": "role", "role": "button", "name": "Choose"}]}, "timeout_ms": 1000}, "failed"),
                ("click_on", {"target": rule, "timeout_ms": 2000}, "passed"),
                ("assert_visible", {"target": {"page": "main", "steps": [{"kind": "role", "role": "status", "name": "Selection done"}]}, "timeout_ms": 2000}, "passed"),
                ("close_browser", {}, "passed"),
            ]
            try:
                for index, (name, arguments, status) in enumerate(calls):
                    response = await tools[name].invoke(ToolCall(name=name, arguments=arguments, call_id=f"call-{index}"))
                    responses.append(response)
                    assert len(response) <= 16_000
                    assert "[ref=" not in response
                    assert json.loads(response)["status"] == status, response
            finally:
                harness.close()
            return DynamicAgentOutcome(task=task, steps=[], verification=VerificationResult(status="success", summary="The selected product is visible."), duration_ms=0)

    result = await DynamicExecutionService(agent=ScriptedAgent()).execute(
        DynamicExecutionRequest(task=task, settings=settings, record=True, publication_directory=settings.cases.dir, case_name="First eligible product")
    )
    assert selected == ["a"]
    assert result.recording is not None
    assert result.recording.status == "recorded"
    assert result.recording.published_case_path is not None
    case = FsqCaseLoader().load_case(result.recording.published_case_path)
    registry = build_capability_registry(platform="web")
    steps = FsqExecutableStepAdapter(registry_snapshot=registry.snapshot()).to_executable_steps(case)
    assert [step.action_name for step in steps] == ["start_browser", "navigate_to", "click_on", "assert_visible", "close_browser"]
    assert steps[2].params["target"] == WebLocator.model_validate(rule).model_dump(mode="json", by_alias=True, exclude_none=True)
    for response in responses:
        assert "aria-ref=" not in response

    order.reverse()
    replay_dir = tmp_path / "replay"
    harness = HarnessFactory().create_harness(platform="web", harness_settings=settings.harness, artifact_store=ArtifactStore(replay_dir))
    try:
        evidence = run_fsq_core_case(
            case_path=result.recording.published_case_path,
            harness=harness,
            output_dir=replay_dir,
            run_id="fresh-replay",
            registry=registry,
            post_action_delay_seconds=settings.execution.post_action_delay_seconds,
        )
    finally:
        harness.close()
    assert selected == ["a", "b"]
    assert all(step.status == "passed" for step in evidence.steps)
