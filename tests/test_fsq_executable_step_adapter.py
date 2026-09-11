# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from pathlib import Path

import pytest

from fsq_agent._capability_bootstrap import build_capability_registry
from fsq_agent.case_dsl import FsqCaseLoader, FsqExecutableStepAdapter
from fsq_agent.models import ConfigurationError

ROOT = Path(__file__).resolve().parents[1]

FSQ_CASE = """
schemaVersion: fsq.ai-test/v1
name: Fundamental Test bing.com website
description: Converted from Edge Android Behave BDD scenario.
platform: android
appId: com.microsoft.emmx
tags:
  - p0
  - fsq-converted
---
- launchApp
- assertVisible:
    target: New Tab Page account menu
    locator:
      accessibilityId: Account menu
    optional: false
- tapOn:
    target: Search box in NTP page
- inputText:
    text: bing.com
    target: Search box
    locator:
      resourceId: com.microsoft.emmx:id/url_bar
    timeout: 10000
- pressKey:
    key: Enter
- assert:
    element:
      resourceId: com.microsoft.emmx:id/url_bar
    text:
      contains: bing.com
    optional: false
- assertWithAI:
    prompt: Verify Bing page is visible.
    optional: false
- killApp
"""


def _load_case(tmp_path: Path):
    case_path = tmp_path / "fundamental_test_bing_com_website.fsq.yaml"
    case_path.write_text(FSQ_CASE, encoding="utf-8")
    return FsqCaseLoader().load_case(case_path)


def _adapter() -> FsqExecutableStepAdapter:
    return FsqExecutableStepAdapter(registry_snapshot=build_capability_registry().snapshot())


def _web_adapter() -> FsqExecutableStepAdapter:
    return FsqExecutableStepAdapter(registry_snapshot=build_capability_registry(platform="web").snapshot())


def _macos_adapter() -> FsqExecutableStepAdapter:
    return FsqExecutableStepAdapter(registry_snapshot=build_capability_registry(platform="macos").snapshot())


def _windows_adapter() -> FsqExecutableStepAdapter:
    return FsqExecutableStepAdapter(registry_snapshot=build_capability_registry(platform="windows").snapshot())


def test_public_web_example_matches_current_executable_contract() -> None:
    case = FsqCaseLoader().load_case(ROOT / "examples" / "web" / "example-domain.fsq.yaml")

    steps = _web_adapter().to_executable_steps(case)

    assert [step.action_name for step in steps] == [
        "start_browser",
        "navigate_to",
        "fill_text",
        "press_key",
        "fill_text",
        "press_key",
        "click_on",
        "click_on",
        "assert_visible",
        "assert_not_visible",
        "close_browser",
    ]
    assert steps[2].params["text"] == "Review FSQ evidence"
    assert steps[2].params["textType"] == "literal"
    assert "clear" not in steps[2].params
    assert steps[4].params["text"] == "Publish v0.1.0"
    assert steps[4].params["textType"] == "literal"
    assert "clear" not in steps[4].params
    assert steps[3].params["scope"]["target"] == steps[2].params["target"]
    assert steps[5].params["scope"]["target"] == steps[4].params["target"]
    assert [item["kind"] for item in steps[6].params["target"]["steps"]] == ["css", "role", "first", "css"]
    assert steps[6].params["target"]["steps"][-1]["selector"] == "input.toggle"
    assert steps[7].params["target"]["steps"][0]["name"] == "Active"
    assert steps[8].params["target"]["steps"] == [{"kind": "text", "text": "Publish v0.1.0", "exact": True}]
    assert steps[9].params["target"]["steps"] == [{"kind": "text", "text": "Review FSQ evidence", "exact": True}]


def test_windows_strict_replay_allows_null_targets(tmp_path: Path) -> None:
    case_path = tmp_path / "null-targets.fsq.yaml"
    case_path.write_text(
        "schemaVersion: fsq.ai-test/v1\n"
        "name: Null targets\n"
        "platform: windows\n"
        "---\n"
        "- typeText:\n"
        "    target:\n"
        "    locator:\n"
        "      title: ''\n"
        "      control_type: Edit\n"
        "      automation_id: view_1021\n"
        "    text: https://www.apple.com\n"
        "- clickOn:\n"
        "    target:\n"
        "    locator:\n"
        "      title: Done\n",
        encoding="utf-8",
    )

    steps = _windows_adapter().to_executable_steps(FsqCaseLoader().load_case(case_path))

    assert "target" not in steps[0].params
    assert "target" not in steps[1].params


def test_windows_strict_replay_allows_missing_target(tmp_path: Path) -> None:
    case_path = tmp_path / "missing-target.fsq.yaml"
    case_path.write_text(
        "schemaVersion: fsq.ai-test/v1\nname: Missing target\nplatform: windows\n---\n- clickOn:\n    locator:\n      title: Done\n",
        encoding="utf-8",
    )

    steps = _windows_adapter().to_executable_steps(FsqCaseLoader().load_case(case_path))

    assert steps[0].params == {"locator": {"title": "Done"}}


def test_fsq_executable_step_adapter_preserves_order_and_canonical_action_names(tmp_path: Path) -> None:
    case = _load_case(tmp_path)

    steps = _adapter().to_executable_steps(case)

    assert [step.action_name for step in steps] == [
        "launch_app",
        "assert_visible",
        "tap_on",
        "input_text",
        "press_key",
        "assert_state",
        "assert_with_ai",
        "kill_app",
    ]
    assert [step.metadata["authored_action_name"] for step in steps] == [
        "launchApp",
        "assertVisible",
        "tapOn",
        "inputText",
        "pressKey",
        "assert",
        "assertWithAI",
        "killApp",
    ]
    assert [step.kind for step in steps] == [
        "setup",
        "assertion",
        "action",
        "action",
        "action",
        "assertion",
        "assertion",
        "teardown",
    ]
    assert steps[0].step_id == "fundamental_test_bing_com_website-step-001"
    assert steps[-1].step_id == "fundamental_test_bing_com_website-step-008"


def test_fsq_executable_step_adapter_normalizes_params_and_source_refs(tmp_path: Path) -> None:
    case = _load_case(tmp_path)

    steps = _adapter().to_executable_steps(case)

    assert steps[0].params == {}
    assert steps[2].params == {"target": "Search box in NTP page"}
    assert {key: value for key, value in steps[3].params.items() if key != "textType"} == {
        "text": "bing.com",
        "target": "Search box",
        "locator": {"resourceId": "com.microsoft.emmx:id/url_bar"},
    }
    assert steps[3].timeout_ms == 10000
    assert steps[4].params == {"key": "Enter"}

    assert steps[1].source_ref is not None
    assert steps[1].source_ref.source_type == "fsq"
    assert steps[1].source_ref.source_id == str(case.path)
    assert steps[1].source_ref.step_index == 1
    assert steps[1].source_ref.metadata == {
        "case_name": "Fundamental Test bing.com website",
        "platform": "android",
    }
    assert steps[1].metadata["case_id"] == "fundamental_test_bing_com_website"
    assert steps[1].metadata["raw_command"] == case.commands[1]


def test_fsq_executable_step_adapter_supports_android_tap_at(tmp_path: Path) -> None:
    case_path = tmp_path / "tap_at.fsq.yaml"
    case_path.write_text(
        """
schemaVersion: fsq.ai-test/v1
name: Tap At Coordinate
platform: android
---
- tapAt:
    point:
      x: 100
      y: 200
""",
        encoding="utf-8",
    )
    case = FsqCaseLoader().load_case(case_path)

    steps = _adapter().to_executable_steps(case)

    assert len(steps) == 1
    assert steps[0].action_name == "tap_at"
    assert steps[0].params == {"point": {"x": 100, "y": 200}}
    assert steps[0].metadata["authored_action_name"] == "tapAt"


def test_fsq_executable_step_adapter_preserves_text_type_runtime_secret_and_waits(tmp_path: Path) -> None:
    case_path = tmp_path / "recorded.fsq.yaml"
    case_path.write_text(
        """
schemaVersion: fsq.ai-test/v1
name: Recorded Secret Case
platform: android
---
- inputText:
    text: TEST_ACCOUNT_PASSWORD
    textType: runtimeSecret
    target: Password field
- waitMs:
    duration_ms: 1
    reason: settle
""",
        encoding="utf-8",
    )
    case = FsqCaseLoader().load_case(case_path)

    steps = _adapter().to_executable_steps(case)

    assert steps[0].params == {"text": "TEST_ACCOUNT_PASSWORD", "textType": "runtimeSecret", "target": "Password field"}
    assert steps[1].action_name == "wait_ms"
    assert steps[1].metadata["authored_action_name"] == "waitMs"
    assert steps[1].params == {"duration_ms": 1, "reason": "settle"}
    assert steps[1].kind == "action"


def test_fsq_executable_step_adapter_raises_for_malformed_command(tmp_path: Path) -> None:
    case_path = tmp_path / "bad.fsq.yaml"
    case_path.write_text(
        """
schemaVersion: fsq.ai-test/v1
name: Bad Case
platform: android
---
- tapOn: Login
  inputText: hello
""",
        encoding="utf-8",
    )
    case = FsqCaseLoader().load_case(case_path)

    with pytest.raises(ConfigurationError) as exc_info:
        _adapter().to_executable_steps(case)

    assert exc_info.value.context["path"] == str(case_path)
    assert exc_info.value.context["step_index"] == 0


def test_fsq_executable_step_adapter_raises_for_invalid_android_payload(tmp_path: Path) -> None:
    case_path = tmp_path / "bad_payload.fsq.yaml"
    case_path.write_text(
        """
schemaVersion: fsq.ai-test/v1
name: Bad Payload
platform: android
---
- tapOn:
    locator:
      unknown: Login
""",
        encoding="utf-8",
    )
    case = FsqCaseLoader().load_case(case_path)

    with pytest.raises(ConfigurationError) as exc_info:
        _adapter().to_executable_steps(case)

    assert exc_info.value.context["path"] == str(case_path)
    assert exc_info.value.context["step_index"] == 0
    assert exc_info.value.context["action_name"] == "tapOn"
    assert exc_info.value.context["validation_errors"]


def test_fsq_executable_step_adapter_rejects_legacy_scalar_android_payload(tmp_path: Path) -> None:
    case_path = tmp_path / "legacy_scalar.fsq.yaml"
    case_path.write_text(
        """
schemaVersion: fsq.ai-test/v1
name: Legacy Scalar
platform: android
---
- pressKey: Enter
""",
        encoding="utf-8",
    )
    case = FsqCaseLoader().load_case(case_path)

    with pytest.raises(ConfigurationError) as exc_info:
        _adapter().to_executable_steps(case)

    assert exc_info.value.context["path"] == str(case_path)
    assert exc_info.value.context["step_index"] == 0
    assert exc_info.value.context["action_name"] == "pressKey"
    assert exc_info.value.context["validation_errors"]


def test_fsq_executable_step_adapter_returns_no_steps_for_goal_only_case(tmp_path: Path) -> None:
    case_path = tmp_path / "goal_only.fsq.yaml"
    case_path.write_text(
        """
schemaVersion: fsq.ai-test/v1
name: Goal Only
platform: android
""",
        encoding="utf-8",
    )
    case = FsqCaseLoader().load_case(case_path)

    assert _adapter().to_executable_steps(case) == []


def test_fsq_executable_step_adapter_ignores_lifecycle_hooks(tmp_path: Path) -> None:
    case_path = tmp_path / "hooked.fsq.yaml"
    case_path.write_text(
        """
schemaVersion: fsq.ai-test/v1
name: Hooked Case
platform: android
onCaseStart:
    runCase: hooks/login.fsq.yaml
onCaseComplete:
    runShell: ./scripts/cleanup.sh
---
- launchApp
""",
        encoding="utf-8",
    )
    case = FsqCaseLoader().load_case(case_path)

    steps = _adapter().to_executable_steps(case)

    assert [step.action_name for step in steps] == ["launch_app"]
    assert steps[0].metadata["raw_command"] == "launchApp"


def test_fsq_executable_step_adapter_resolves_web_aliases_from_web_registry(tmp_path: Path) -> None:
    case_path = tmp_path / "web_case.fsq.yaml"
    case_path.write_text(
        """
schemaVersion: fsq.ai-test/v1
name: Web Case
platform: web
---
- startBrowser
- navigateTo:
    page: main
    url: https://www.bing.com
- uiSnapshot:
    scope: {kind: page, page: main}
- clickOn:
    target: {page: main, steps: [{kind: role, role: textbox, name: Search}]}
- typeText:
    text: playwright
    target: {page: main, steps: [{kind: role, role: textbox, name: Search}]}
- pressKey:
    scope: {kind: element, target: {page: main, steps: [{kind: role, role: textbox, name: Search}]}}
    key: Enter
- waitFor:
    condition:
      kind: text
      scope: {kind: page, page: main}
      text: {kind: contains, value: playwright}
    timeout_ms: 5000
- assertText:
    target: {page: main, steps: [{kind: role, role: main, name: Results}]}
    text: {kind: contains, value: playwright}
- closeBrowser
""",
        encoding="utf-8",
    )
    case = FsqCaseLoader().load_case(case_path)

    steps = _web_adapter().to_executable_steps(case)

    assert [step.action_name for step in steps] == [
        "start_browser",
        "navigate_to",
        "ui_snapshot",
        "click_on",
        "type_text",
        "press_key",
        "wait_for",
        "assert_text",
        "close_browser",
    ]
    assert [step.kind for step in steps] == [
        "setup",
        "action",
        "observation",
        "action",
        "action",
        "action",
        "action",
        "assertion",
        "teardown",
    ]
    assert steps[0].params == {}
    assert steps[1].params == {"page": "main", "url": "https://www.bing.com", "wait_until": "load", "timeout_ms": 10000}
    assert steps[3].params["target"]["steps"][0]["name"] == "Search"
    assert steps[4].params["target"] == steps[3].params["target"]
    assert steps[4].params["text"] == "playwright"
    assert steps[4].params["textType"] == "literal"
    assert steps[4].params["delay_ms"] == 0
    assert steps[6].params["condition"]["text"] == {"kind": "contains", "value": "playwright"}
    assert steps[6].params["timeout_ms"] == 5000
    assert steps[7].params["text"] == {"kind": "contains", "value": "playwright"}
    assert steps[8].params == {}
    assert all(step.metadata["platform"] == "web" for step in steps)


def test_fsq_executable_step_adapter_rejects_web_locator_ref(tmp_path: Path) -> None:
    case_path = tmp_path / "web_ref_case.fsq.yaml"
    case_path.write_text(
        """
schemaVersion: fsq.ai-test/v1
name: Web Ref Case
platform: web
---
- clickOn:
    locator:
      ref: e83
""",
        encoding="utf-8",
    )
    case = FsqCaseLoader().load_case(case_path)

    with pytest.raises(ConfigurationError, match="Invalid FSQ command parameters"):
        _web_adapter().to_executable_steps(case)


def test_fsq_executable_step_adapter_preserves_web_text_type_runtime_secret(tmp_path: Path) -> None:
    case_path = tmp_path / "web_secret.fsq.yaml"
    case_path.write_text(
        """
schemaVersion: fsq.ai-test/v1
name: Web Secret Case
platform: web
---
- typeText:
    text: TEST_ACCOUNT_PASSWORD
    textType: runtimeSecret
    target: {page: main, steps: [{kind: label, text: Password}]}
""",
        encoding="utf-8",
    )
    case = FsqCaseLoader().load_case(case_path)

    steps = _web_adapter().to_executable_steps(case)

    assert steps[0].action_name == "type_text"
    assert steps[0].params == {
        "text": "TEST_ACCOUNT_PASSWORD",
        "textType": "runtimeSecret",
        "target": {"page": "main", "steps": [{"kind": "label", "text": "Password", "exact": True}]},
        "timeout_ms": 10000,
        "delay_ms": 0,
    }


def test_fsq_executable_step_adapter_resolves_macos_aliases_and_asserts_order(tmp_path: Path) -> None:
    case_path = tmp_path / "macos_case.fsq.yaml"
    case_path.write_text(
        """
schemaVersion: fsq.ai-test/v1
name: macOS Case
platform: macos
---
- launchApp
- clickOn:
    point:
      x: 120
      y: 240
- typeText:
    target: Search field
    text: TEST_ACCOUNT_PASSWORD
    textType: runtimeSecret
- assertElementsOrder:
    direction: horizontal
    elements:
      - target: File
      - locator:
          accessibilityId: Edit
    expected_order:
      - 0
      - 1
- killApp
""",
        encoding="utf-8",
    )
    case = FsqCaseLoader().load_case(case_path)

    steps = _macos_adapter().to_executable_steps(case)

    assert [step.action_name for step in steps] == [
        "launch_app",
        "click_on",
        "type_text",
        "assert_elements_order",
        "kill_app",
    ]
    assert [step.kind for step in steps] == ["setup", "action", "action", "assertion", "teardown"]
    assert steps[1].params == {"point": {"x": 120, "y": 240}}
    assert steps[2].params == {"target": "Search field", "text": "TEST_ACCOUNT_PASSWORD", "textType": "runtimeSecret"}
    assert {key: value for key, value in steps[3].params.items() if key != "textType"} == {
        "elements": [{"target": "File"}, {"locator": {"accessibilityId": "Edit"}}],
        "direction": "horizontal",
        "expected_order": [0, 1],
        "require_all": True,
    }
    assert all(step.metadata["platform"] == "macos" for step in steps)


def test_fsq_executable_step_adapter_preserves_windows_text_type_runtime_secret(tmp_path: Path) -> None:
    case_path = tmp_path / "windows_secret.fsq.yaml"
    case_path.write_text(
        """
schemaVersion: fsq.ai-test/v1
name: Windows Secret Case
platform: windows
---
- typeText:
    target: Password field
    locator:
      title: Password
    text: TEST_ACCOUNT_PASSWORD
    textType: runtimeSecret
""",
        encoding="utf-8",
    )
    case = FsqCaseLoader().load_case(case_path)

    steps = _windows_adapter().to_executable_steps(case)

    assert steps[0].action_name == "type_text"
    assert steps[0].params == {
        "target": "Password field",
        "locator": {"title": "Password"},
        "text": "TEST_ACCOUNT_PASSWORD",
        "textType": "runtimeSecret",
    }
