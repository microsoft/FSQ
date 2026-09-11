# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import pytest

from fsq_agent import models
from fsq_agent.models import (
    AgentFinalOutput,
    AgentRuntimeSettings,
    AgentTaskInput,
    AndroidInputTextParams,
    AndroidSwipeParams,
    ExecutionStep,
    GoalPrePlan,
    HarnessSettings,
    LocalToolOutputSettings,
    MacOSClickOnParams,
    MacOSKillAppParams,
    MacOSLaunchAppParams,
    MacOSPressKeyParams,
    PageKnowledgeIndex,
    PageKnowledgePage,
    SkillConfig,
    Task,
    WaitMsParams,
    WebWaitForParams,
    WindowsClickOnParams,
)


def test_task_defaults() -> None:
    task = Task(description="Do a thing")

    assert task.id == "task"
    assert task.name == "Task"
    assert task.acceptance_criteria == []
    assert task.planning_reference_kind is None
    assert task.planning_reference_text is None
    assert task.key_actions == []
    assert task.verification_goal is None
    assert task.timeout_seconds == 300
    assert task.max_retries == 3
    assert task.knowledge_refs == []


def test_execution_step_requires_positive_id() -> None:
    step = ExecutionStep(
        step_id=1,
        action="write",
        tool="file.write",
        tool_input={"path": "out.txt", "content": "ok"},
        expected_outcome="file written",
    )

    assert step.step_id == 1


def test_agent_final_output_defaults_schema_version() -> None:
    output = AgentFinalOutput(status="success", summary="Done")

    assert output.schema_version == "task_run_v1"
    assert output.pre_plan == []


def test_agent_task_input_wraps_task_contract() -> None:
    task = Task(
        id="task-1",
        description="Do a thing",
        key_actions=["Key action 1: tap button"],
        verification_goal="Verify that doing the thing is complete.",
    )
    task_input = AgentTaskInput(
        task=task,
        acceptance_criteria=task.acceptance_criteria,
        key_actions=task.key_actions,
        verification_goal=task.verification_goal,
        acceptance_policy="Use provided criteria.",
    )

    assert task_input.schema_version == "task_input_v1"
    assert task_input.output_contract == "task_run_v1"
    assert task_input.task.id == "task-1"
    assert task_input.key_actions == ["Key action 1: tap button"]
    assert task_input.verification_goal == "Verify that doing the thing is complete."


def test_agent_runtime_settings_defaults_to_safe_offline_mode() -> None:
    settings = AgentRuntimeSettings()

    assert settings.provider is None
    assert settings.model == ""
    assert settings.base_url == ""
    assert settings.api_key == ""
    assert settings.tracing_enabled is True
    assert not hasattr(settings.prompt, "custom_instructions")
    assert not hasattr(settings.prompt, "custom_instructions_path")
    assert settings.prompt.agent_template_path is None
    assert settings.prompt.task_template_path is None
    assert settings.prompt.variables == {}
    assert settings.local_tool_output.always_write_artifact is True
    assert settings.local_tool_output.recent_inline_output_count == 3
    assert settings.local_tool_output.full_output_max_chars == 30000
    assert settings.local_tool_output.total_inline_output_max_chars == 60000


def test_harness_settings_default_to_android_uiautomator2() -> None:
    settings = HarnessSettings()

    assert settings.platform == "android"
    assert settings.android.backend == "uiautomator2"
    assert settings.android.app_id is None
    assert settings.android.serial is None


def test_skill_config_defaults_to_markdown() -> None:
    skill = SkillConfig(name="browser-testing", path="browser-testing.md")

    assert skill.kind == "markdown"
    assert skill.required is False


def test_models_public_surface_does_not_export_removed_tool_execution_settings() -> None:
    assert "ShellSettings" not in models.__all__
    assert "CLIToolConfig" not in models.__all__
    assert "DeprecatedToolSettings" not in models.__all__
    assert "VerificationCriterion" not in models.__all__
    assert "VerificationMode" not in models.__all__
    assert "VerificationSettings" not in models.__all__
    assert not hasattr(models, "ShellSettings")
    assert not hasattr(models, "CLIToolConfig")
    assert not hasattr(models, "DeprecatedToolSettings")
    assert not hasattr(models, "VerificationCriterion")
    assert not hasattr(models, "VerificationMode")
    assert not hasattr(models, "VerificationSettings")


def test_capability_parameter_schemas_include_llm_facing_guidance() -> None:
    wait_schema = WaitMsParams.model_json_schema()
    android_text_schema = AndroidInputTextParams.model_json_schema()
    android_swipe_schema = AndroidSwipeParams.model_json_schema()
    web_wait_schema = WebWaitForParams.model_json_schema()
    windows_click_schema = WindowsClickOnParams.model_json_schema()
    macos_click_schema = MacOSClickOnParams.model_json_schema()
    macos_launch_schema = MacOSLaunchAppParams.model_json_schema()
    macos_kill_schema = MacOSKillAppParams.model_json_schema()
    macos_press_key_schema = MacOSPressKeyParams.model_json_schema()

    assert "Wait without touching platform state" in wait_schema["description"]
    assert "milliseconds" in wait_schema["properties"]["duration_ms"]["description"]

    assert "target or non-empty locator" in android_text_schema["description"]
    assert "runtimeSecret" in android_text_schema["properties"]["textType"]["description"]
    assert "text to enter" in android_text_schema["properties"]["text"]["description"]
    assert "semantic target" in android_text_schema["properties"]["target"]["description"]
    assert "structured Android locator" in android_text_schema["properties"]["locator"]["description"]

    assert "direction or both start and end" in android_swipe_schema["description"]
    assert "screen size" in android_swipe_schema["properties"]["reference_screen_size"]["description"]

    assert "condition" in web_wait_schema["description"]
    assert "not a sleep" in web_wait_schema["description"]
    assert "milliseconds" in web_wait_schema["properties"]["timeout_ms"]["description"]

    assert "non-empty locator" in windows_click_schema["description"]
    assert "descriptive" in windows_click_schema["properties"]["target"]["description"]
    assert "Windows control locator" in windows_click_schema["properties"]["locator"]["description"]

    assert "target, non-empty locator, or point" in macos_click_schema["description"]
    assert "macOS screen point" in macos_click_schema["properties"]["point"]["description"]

    assert MacOSLaunchAppParams().new_session is False
    assert macos_launch_schema["properties"]["new_session"]["default"] is False
    assert "existing Mac2 session" in macos_launch_schema["properties"]["new_session"]["description"]
    assert "session creation" in macos_launch_schema["properties"]["arguments"]["description"]
    assert "configured bundle id" in macos_launch_schema["properties"]["bundle_id"]["description"]
    assert "environment" not in macos_launch_schema["properties"]
    assert "retain" in macos_kill_schema["properties"]["close_session"]["description"]
    assert "Enter" in macos_press_key_schema["properties"]["key"]["description"]
    assert "COMMAND" in macos_press_key_schema["properties"]["modifiers"]["description"]

    with pytest.raises(ValueError, match="Extra inputs are not permitted"):
        MacOSLaunchAppParams(environment={"APP_MODE": "test"})


def test_local_tool_output_rejects_artifact_subdir_escape() -> None:
    with pytest.raises(ValueError, match="artifact_subdir"):
        LocalToolOutputSettings(artifact_subdir="../outside")


@pytest.mark.parametrize("field", ["full_output_max_chars", "total_inline_output_max_chars"])
def test_local_tool_output_rejects_nonpositive_character_budgets(field: str) -> None:
    with pytest.raises(ValueError, match=field):
        LocalToolOutputSettings(**{field: 0})


def test_local_tool_output_allows_no_recent_inline_outputs() -> None:
    settings = LocalToolOutputSettings(recent_inline_output_count=0)

    assert settings.recent_inline_output_count == 0


def test_page_knowledge_page_uses_semantic_identifiers_and_reference_locators() -> None:
    page = PageKnowledgePage.model_validate(
        {
            "page_id": "edge_android_new_tab_page",
            "name": "New Tab Page",
            "identifiers": [{"name": "Account menu visible", "description": "Account entry is visible."}],
            "images": [{"path": "../assets/pages/ntp.png", "description": "Typical NTP."}],
            "elements": [
                {
                    "name": "Browser menu",
                    "role": "button",
                    "reference_locators": [
                        {
                            "strategy": "id",
                            "selector": "com.microsoft.emmx:id/overflow_button_bottom",
                            "confidence": "high",
                            "notes": "Observed in bottom toolbar mode.",
                        }
                    ],
                    "operations": [
                        {
                            "operation": "tap",
                            "result": {
                                "type": "navigate",
                                "to_page_id": "edge_android_overflow_menu",
                                "description": "Opens the overflow menu.",
                            },
                        }
                    ],
                }
            ],
        }
    )

    assert page.schema_version == "page_knowledge_page_v1"
    assert page.identifiers[0].model_dump() == {"name": "Account menu visible", "description": "Account entry is visible."}
    assert page.elements[0].reference_locators[0].confidence == "high"
    assert page.elements[0].operations[0].result.to_page_id == "edge_android_overflow_menu"


def test_page_knowledge_index_and_goal_pre_plan_defaults() -> None:
    index = PageKnowledgeIndex(
        product="Microsoft Edge",
        platform="Android",
        pages=[
            {
                "page_id": "edge_android_new_tab_page",
                "file": "pages/edge_android_new_tab_page.md",
                "name": "New Tab Page",
                "intents": ["new tab", "search"],
            }
        ],
    )
    plan = GoalPrePlan(
        goal="Open downloads",
        key_actions=[{"step_id": 1, "action": "Open browser menu", "source_page_ids": ["edge_android_new_tab_page"]}],
        verification_goal="Verify that Downloads can be opened from the browser menu.",
    )

    assert index.schema_version == "page_knowledge_index_v1"
    assert index.pages[0].page_id == "edge_android_new_tab_page"
    assert plan.schema_version == "goal_pre_plan_v1"
    assert plan.key_actions[0].step_id == 1
    assert plan.verification_goal == "Verify that Downloads can be opened from the browser menu."
