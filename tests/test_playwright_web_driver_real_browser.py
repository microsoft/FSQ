# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import json
import os
import shutil
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
import yaml

from fsq_agent._capability_bootstrap import build_capability_registry
from fsq_agent.case_dsl import FsqCaseLoader, FsqExecutableStepAdapter
from fsq_agent.config._settings import Settings
from fsq_agent.drivers.web._playwright import PlaywrightWebDriver
from fsq_agent.execution import RecordingService
from fsq_agent.harnesses._web import WebHarness
from fsq_agent.models import ExecutableStep, HarnessSettings, OutputSettings, ReportArtifact, RunEvent, Task, TaskResult, VerificationResult

_LONG_NAME = "Semantic replay control " + "雪" * 110
_FIXTURE_HTML = f"""<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><title>FSQ semantic replay</title></head>
<body>
  <main aria-label="Replay fixture">
    <h1>Semantic replay</h1>
    <div role="row" aria-label="Alice"><button onclick="recordEvent('Alice edited')">Edit</button></div>
    <div role="row" aria-label="Bob"><button onclick="recordEvent('Bob edited')">Edit</button></div>
    <button onclick="recordEvent('First chosen')">Choose</button>
    <button onclick="recordEvent('Second chosen')">Choose</button>
    <label>Search <input aria-label="Search" /></label>
    <label>Sort order <select aria-label="Sort order"><option>Newest</option><option>Oldest</option></select></label>
    <label>Tags <select aria-label="Tags" multiple><option>One</option><option>Two</option></select></label>
    <button aria-label="{_LONG_NAME}">Long semantic value</button>
    <div role="status" aria-label="Activity" id="activity">Ready</div>
  </main>
  <script>
    function recordEvent(message) {{
      document.getElementById('activity').textContent += ' | ' + message;
    }}
  </script>
</body>
</html>"""


def _browser_target() -> tuple[str, Path] | None:
    candidates: list[tuple[str, Path]] = []
    for environment_name, relative_paths in (
        ("PROGRAMFILES", (("chrome", "Google/Chrome/Application/chrome.exe"), ("msedge", "Microsoft/Edge/Application/msedge.exe"))),
        ("PROGRAMFILES(X86)", (("chrome", "Google/Chrome/Application/chrome.exe"), ("msedge", "Microsoft/Edge/Application/msedge.exe"))),
        ("LOCALAPPDATA", (("chrome", "Google/Chrome/Application/chrome.exe"), ("msedge", "Microsoft/Edge/Application/msedge.exe"))),
    ):
        root = os.environ.get(environment_name)
        if root:
            candidates.extend((channel, Path(root) / relative_path) for channel, relative_path in relative_paths)
    if sys.platform == "darwin":
        candidates.append(("chrome", Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")))
    for channel, command in (("chrome", "google-chrome"), ("chrome", "google-chrome-stable"), ("msedge", "microsoft-edge")):
        executable = shutil.which(command)
        if executable:
            candidates.append((channel, Path(executable)))
    return next(((channel, path) for channel, path in candidates if path.is_file()), None)


@pytest.fixture
def semantic_web_fixture() -> str:
    body = _FIXTURE_HTML.encode("utf-8")

    class FixtureHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, _format: str, *_args: object) -> None:
            return None

    server = ThreadingHTTPServer(("127.0.0.1", 0), FixtureHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/fixture"
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def _write_event(path: Path, event: RunEvent) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(event.model_dump_json() + "\n")


def test_recorded_semantic_web_case_replays_in_fresh_browser_without_snapshot(tmp_path: Path, semantic_web_fixture: str) -> None:
    browser_target = _browser_target()
    if browser_target is None:
        pytest.skip("A supported local Chrome or Edge executable is required for real-browser verification.")
    channel, executable_path = browser_target
    run_id = "semantic-web-recording"
    run_dir = tmp_path / run_id
    run_dir.mkdir()
    events_path = run_dir / "events.jsonl"
    task = Task(id="task-1", name="Semantic replay", description="Exercise replayable semantic Web actions")
    result = TaskResult(
        task_id=task.id,
        status="success",
        steps=[],
        verification=VerificationResult(status="success", summary="Semantic actions passed."),
        report=ReportArtifact(run_id=run_id, path=run_dir / "report.md"),
    )
    output_settings = OutputSettings()
    output_settings.runs_dir = tmp_path
    settings = Settings(output=output_settings, harness=HarnessSettings(platform="web"))
    commands = [
        ("startBrowser", "start_browser", "setup", {}),
        ("navigateTo", "navigate_to", "action", {"url": semantic_web_fixture}),
        ("uiSnapshot", "ui_snapshot", "observation", {}),
        ("clickOn", "click_on", "action", {"locator": {"role": "button", "name": "Edit", "within": {"role": "row", "name": "Alice"}}}),
        ("clickOn", "click_on", "action", {"locator": {"role": "button", "name": "Choose", "index": 1}}),
        ("typeText", "type_text", "action", {"locator": {"role": "textbox", "name": "Search"}, "text": "playwright"}),
        ("selectOption", "select_option", "action", {"locator": {"role": "combobox", "name": "Sort order"}, "labels": ["Newest"]}),
        ("selectOption", "select_option", "action", {"locator": {"role": "listbox", "name": "Tags"}, "labels": ["One", "Two"]}),
        ("waitFor", "wait_for", "action", {"locator": {"role": "status", "name": "Activity"}, "state": "visible"}),
        ("assertText", "assert_text", "assertion", {"locator": {"role": "status", "name": "Activity"}, "text": {"contains": "Second chosen"}}),
        ("assertText", "assert_text", "assertion", {"text": {"contains": "Alice edited"}}),
        ("assertNotVisible", "assert_not_visible", "assertion", {"locator": {"role": "dialog"}}),
        ("closeBrowser", "close_browser", "teardown", {}),
    ]

    recording_driver = PlaywrightWebDriver(channel=channel, executable_path=executable_path, headless=True)
    recording_harness = WebHarness(recording_driver)
    try:
        for index, (alias, action_name, step_kind, params) in enumerate(commands):
            call_id = f"call-{index}"
            _write_event(
                events_path,
                RunEvent(
                    run_id=run_id,
                    task_id=task.id,
                    type="tool_call_started",
                    title="Tool call started",
                    tool_name=action_name,
                    tool_call_id=call_id,
                    tool_arguments=params,
                    payload={
                        "tool_origin": "platform",
                        "capability_name": action_name,
                        "step_kind": step_kind,
                        "replay": {"kind": "fsq_command", "alias": alias},
                    },
                ),
            )
            action_result = recording_harness.invoke_action(
                ExecutableStep(step_id=f"dynamic-{index}", kind=step_kind, action_name=action_name, params=params),
                recording_harness.get_context(),
            )
            assert action_result.status == "passed", action_result
            if action_name == "ui_snapshot":
                assert action_result.output["snapshot_type"] == "aria"
                assert action_result.output["truncated"] is True
                assert "[ref=" not in action_result.output["snapshot"]
                assert _LONG_NAME[:100] + "..." in action_result.output["snapshot"]
            _write_event(
                events_path,
                RunEvent(
                    run_id=run_id,
                    task_id=task.id,
                    type="tool_call_completed",
                    title="Tool call completed",
                    tool_name=action_name,
                    tool_call_id=call_id,
                    payload={
                        "tool_origin": "platform",
                        "capability_name": action_name,
                        "step_kind": step_kind,
                        "replay": {"kind": "fsq_command", "alias": alias},
                        "status": "passed",
                    },
                ),
            )
    finally:
        recording_harness.close()

    recording = RecordingService().record(run_dir=run_dir, task=task, result=result, settings=settings)

    assert recording.status == "recorded"
    assert recording.validation_status == "passed"
    documents = list(yaml.safe_load_all(recording.recorded_case_path.read_text(encoding="utf-8")))
    serialized_commands = json.dumps(documents[1])
    assert all(alias != "uiSnapshot" for command in documents[1] for alias in command)
    assert '"ref"' not in serialized_commands
    assert '"target"' not in serialized_commands
    assert not any(field in serialized_commands for field in ('"css"', '"xpath"', '"testId"'))

    case = FsqCaseLoader().load_case(recording.recorded_case_path)
    steps = FsqExecutableStepAdapter(build_capability_registry(platform="web").snapshot()).to_executable_steps(case)
    assert all(step.action_name != "ui_snapshot" for step in steps)
    replay_driver = PlaywrightWebDriver(channel=channel, executable_path=executable_path, headless=True)
    replay_harness = WebHarness(replay_driver)
    try:
        for step in steps:
            replay_result = replay_harness.invoke_action(step, replay_harness.get_context())
            assert replay_result.status == "passed", replay_result
    finally:
        replay_harness.close()
