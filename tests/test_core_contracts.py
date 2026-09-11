# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import ast
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from fsq_agent.models import (
    ANDROID_ACTION_DEFINITIONS,
    ANDROID_ACTION_DEFINITIONS_BY_NAME,
    WEB_ACTION_DEFINITIONS,
    WEB_ACTION_DEFINITIONS_BY_NAME,
    AndroidPressKeyParams,
    AndroidSwipeParams,
    AndroidTapAtParams,
    AndroidTapOnParams,
    AndroidUiTreeParams,
    EvidenceArtifactRef,
    EvidenceBundle,
    EvidencePolicy,
    ExecutableStep,
    HarnessActionResult,
    HarnessArtifactRef,
    HarnessContext,
    HarnessFunctionSchema,
    RetryPolicy,
    RunnerEvent,
    RunnerStepResult,
    SourceRef,
    StepPhaseReport,
    WebClickOnParams,
    WebCloseBrowserParams,
    WebStartBrowserParams,
    WebTypeTextParams,
    WebUiSnapshotParams,
    WebWaitForParams,
)


def test_core_exports_harness_interface() -> None:
    from fsq_agent.core import HarnessInterface

    assert HarnessInterface.__name__ == "HarnessInterface"


def test_core_public_exports_follow_strict_boundary() -> None:
    from fsq_agent import core
    from fsq_agent.core import harness

    expected_core_public_names = {
        "AndroidDeviceDiscovery",
        "CapabilityDefinitionFactory",
        "CommonPlatformTools",
        "DriverObservationInterface",
        "DriverFactory",
        "HarnessFactory",
        "AndroidDriverInterface",
        "WebDriverInterface",
        "WindowsDriverInterface",
        "MacOSDriverInterface",
    }
    expected_harness_public_names = expected_core_public_names - {"CapabilityDefinitionFactory", "CommonPlatformTools"}
    for name in expected_core_public_names:
        assert hasattr(core, name)
    for name in expected_harness_public_names:
        assert hasattr(harness, name)

    private_implementation_names = {
        "AndroidHarness",
        "WebHarness",
        "WindowsHarness",
        "MacOSHarness",
        "UiAutomator2AndroidDriver",
        "PlaywrightWebDriver",
        "PywinautoWindowsDriver",
        "AppiumMac2Driver",
        "DefaultCapabilityDefinitionFactory",
        "DefaultDriverFactory",
        "DefaultHarnessFactory",
        "DriverFactoryInterface",
        "HarnessFactoryInterface",
    }
    for name in private_implementation_names:
        assert not hasattr(core, name)
        assert not hasattr(harness, name)

    assert not hasattr(core, "driver_tool")
    assert not hasattr(core, "android_capability_definitions")
    assert not hasattr(core, "web_capability_definitions")
    assert not hasattr(core, "windows_capability_definitions")
    assert not hasattr(core, "macos_capability_definitions")
    assert not hasattr(harness, "driver_tool")


def test_android_discovery_core_export_is_canonical():
    from fsq_agent.core import AndroidDeviceDiscovery
    from fsq_agent.environments import AndroidDeviceDiscovery as CanonicalDiscovery

    assert AndroidDeviceDiscovery is CanonicalDiscovery


def test_capability_definition_factory_selects_platform_and_filters_ai_assertion() -> None:
    from fsq_agent.core import CapabilityDefinitionFactory, CommonPlatformTools

    factory = CapabilityDefinitionFactory()

    web_definitions = factory.platform_definitions(platform="web", backend="playwright")
    web_without_ai = factory.platform_definitions(platform="web", backend="playwright", include_ai_assertion=False)

    assert {definition.name for definition in CommonPlatformTools.capability_definitions()} == {"wait_ms"}
    assert {definition.name for definition in web_definitions} >= {"start_browser", "close_browser", "assert_with_ai"}
    assert "assert_with_ai" not in {definition.name for definition in web_without_ai}
    assert all(definition.platform == "web" for definition in web_definitions)


def test_driver_factory_returns_platform_driver_protocols(monkeypatch: pytest.MonkeyPatch) -> None:
    from fsq_agent.core import (
        AndroidDriverInterface,
        DriverFactory,
        MacOSDriverInterface,
        WebDriverInterface,
        WindowsDriverInterface,
    )
    from fsq_agent.core.harness._uiautomator2_driver import UiAutomator2AndroidDriver
    from fsq_agent.models import AndroidHarnessSettings, MacOSHarnessSettings, WebHarnessSettings, WindowsHarnessSettings

    monkeypatch.setattr(UiAutomator2AndroidDriver, "_connect", lambda self, serial: object())
    factory = DriverFactory()

    assert isinstance(factory.create_android_driver(AndroidHarnessSettings(), app_id="app"), AndroidDriverInterface)
    assert isinstance(factory.create_web_driver(WebHarnessSettings()), WebDriverInterface)
    assert isinstance(factory.create_windows_driver(WindowsHarnessSettings()), WindowsDriverInterface)
    assert isinstance(factory.create_macos_driver(MacOSHarnessSettings()), MacOSDriverInterface)


def test_harness_factory_returns_harness_interface(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from fsq_agent.core import ArtifactStore, HarnessFactory, HarnessInterface
    from fsq_agent.core.harness._uiautomator2_driver import UiAutomator2AndroidDriver
    from fsq_agent.models import HarnessSettings

    monkeypatch.setattr(UiAutomator2AndroidDriver, "_connect", lambda self, serial: object())
    factory = HarnessFactory()

    harness = factory.create_harness(
        platform="android",
        harness_settings=HarnessSettings(platform="android"),
        artifact_store=ArtifactStore(run_dir=tmp_path),
        app_id="app",
    )

    assert isinstance(harness, HarnessInterface)


def test_non_core_package_code_does_not_import_core_private_modules() -> None:
    package_root = Path("fsq_agent")
    violations: list[str] = []

    for path in package_root.rglob("*.py"):
        if path.parts[:2] == ("fsq_agent", "core"):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                module_name = node.module or ""
                if module_name.startswith(("fsq_agent.core._", "fsq_agent.core.harness._")):
                    violations.append(f"{path}:{node.lineno}: from {module_name} import ...")
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    module_name = alias.name
                    if module_name.startswith(("fsq_agent.core._", "fsq_agent.core.harness._")):
                        violations.append(f"{path}:{node.lineno}: import {module_name}")

    assert violations == []


def test_runner_submodule_imports_only_public_core_interfaces():
    violations = []
    for path in Path("fsq_agent/core/runner").glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            imports = [node.module or ""] if isinstance(node, ast.ImportFrom) else [alias.name for alias in node.names] if isinstance(node, ast.Import) else []
            violations.extend(
                f"{path}:{node.lineno}:{module}" for module in imports if module.startswith("fsq_agent.core") and not module.startswith(("fsq_agent.core.interfaces", "fsq_agent.core.runner"))
            )
    assert violations == []


def test_fake_harness_satisfies_runtime_protocol() -> None:
    from contextlib import nullcontext

    from fsq_agent.core.harness import HarnessInterface

    class FakeHarness:
        def close(self) -> None:
            return None

        def capture_scope(self, callback):
            return nullcontext()

        def get_context(self) -> HarnessContext:
            return HarnessContext(platform="android", session_id="session-1")

        def action_space(self) -> dict[str, object]:
            return {"tap": {"description": "Tap an element"}}

        def before_action(self, step: ExecutableStep, context: HarnessContext) -> None:
            return None

        def invoke_action(self, step: ExecutableStep, context: HarnessContext) -> HarnessActionResult:
            return HarnessActionResult(status="passed", action_name=step.action_name)

        def after_action(
            self,
            step: ExecutableStep,
            context: HarnessContext,
            action_result: HarnessActionResult,
        ) -> None:
            return None

        def capture_artifact(
            self,
            kind: str,
            reason: str,
            context: HarnessContext,
            step_id: str,
            phase: str,
        ) -> HarnessArtifactRef:
            return HarnessArtifactRef(artifact_id=f"{kind}-1", kind="log", path=Path(f"runs/run-1/{step_id}-{phase}-{reason}.log"))

        def classify_error(self, error: BaseException, phase: str, step: ExecutableStep) -> str:
            return "unknown"

    assert isinstance(FakeHarness(), HarnessInterface)


def test_fake_driver_satisfies_observation_protocol() -> None:
    from fsq_agent.core.harness import DriverObservationInterface

    class FakeDriver:
        def close(self) -> None:
            return None

        def screenshot(self, params: object | None = None) -> bytes:
            return b"png"

        def ui_snapshot(self, params: object | None = None) -> dict[str, object]:
            return {"nodes": []}

    assert isinstance(FakeDriver(), DriverObservationInterface)


def test_platform_driver_protocols_extend_observation_contract() -> None:
    from fsq_agent.core.harness import (
        AndroidDriverInterface,
        DriverObservationInterface,
        MacOSDriverInterface,
        WebDriverInterface,
        WindowsDriverInterface,
    )

    assert issubclass(AndroidDriverInterface, DriverObservationInterface)
    assert issubclass(WebDriverInterface, DriverObservationInterface)
    assert issubclass(WindowsDriverInterface, DriverObservationInterface)
    assert issubclass(MacOSDriverInterface, DriverObservationInterface)


def test_harness_function_schema_is_serializable_contract() -> None:
    schema = HarnessFunctionSchema(
        name="tap_on",
        description="Tap a target.",
        params_json_schema=AndroidTapOnParams.model_json_schema(),
        platform="android",
        driver_method="tap_on",
        fsq_action_name="tapOn",
        metadata={"backend": "uiautomator2"},
    )

    dumped = schema.model_dump(mode="json")

    assert dumped["name"] == "tap_on"
    assert "strict" not in dumped
    assert dumped["params_json_schema"]["type"] == "object"
    assert dumped["metadata"] == {"backend": "uiautomator2"}


def test_android_parameter_models_produce_canonical_dumps_and_reject_extra_fields() -> None:
    tap = AndroidTapOnParams.model_validate({"target": "Login"})
    tap_at = AndroidTapAtParams.model_validate({"point": {"x": 100, "y": 200}, "reference_screen_size": {"width": 1080, "height": 2400}})
    swipe = AndroidSwipeParams.model_validate(
        {
            "start": {"x": 800, "y": 1900},
            "end": {"x": 200, "y": 1900},
            "reference_screen_size": {"width": 1080, "height": 2400},
            "duration": 1000,
        }
    )

    assert tap.model_dump(mode="json", exclude_none=True) == {"target": "Login"}
    assert tap_at.model_dump(mode="json", exclude_none=True) == {
        "point": {"x": 100, "y": 200},
        "reference_screen_size": {"width": 1080, "height": 2400},
    }
    assert swipe.model_dump(mode="json", exclude_none=True) == {
        "start": {"x": 800, "y": 1900},
        "end": {"x": 200, "y": 1900},
        "reference_screen_size": {"width": 1080, "height": 2400},
        "duration": 1000,
    }
    with pytest.raises(ValidationError):
        AndroidTapOnParams.model_validate({"locator": {"unknown": "Login"}})
    with pytest.raises(ValidationError):
        AndroidTapOnParams.model_validate({"value": "Login"})


def test_android_action_definitions_are_single_source_for_android_contract() -> None:
    action_names = [definition.fsq_action_name for definition in ANDROID_ACTION_DEFINITIONS]

    assert len(action_names) == len(set(action_names))
    assert set(ANDROID_ACTION_DEFINITIONS_BY_NAME) == set(action_names)
    assert ANDROID_ACTION_DEFINITIONS_BY_NAME["tapOn"].driver_method == "tap_on"
    assert ANDROID_ACTION_DEFINITIONS_BY_NAME["tapOn"].params_model is AndroidTapOnParams
    assert ANDROID_ACTION_DEFINITIONS_BY_NAME["tapOn"].step_kind == "action"
    assert ANDROID_ACTION_DEFINITIONS_BY_NAME["tapAt"].driver_method == "tap_at"
    assert ANDROID_ACTION_DEFINITIONS_BY_NAME["tapAt"].params_model is AndroidTapAtParams
    assert ANDROID_ACTION_DEFINITIONS_BY_NAME["tapAt"].step_kind == "action"
    assert ANDROID_ACTION_DEFINITIONS_BY_NAME["pressKey"].driver_method == "press_key"
    assert ANDROID_ACTION_DEFINITIONS_BY_NAME["pressKey"].params_model is AndroidPressKeyParams
    assert ANDROID_ACTION_DEFINITIONS_BY_NAME["uiTree"].driver_method == "ui_snapshot"
    assert ANDROID_ACTION_DEFINITIONS_BY_NAME["uiTree"].params_model is AndroidUiTreeParams
    assert ANDROID_ACTION_DEFINITIONS_BY_NAME["uiTree"].step_kind == "observation"
    assert ANDROID_ACTION_DEFINITIONS_BY_NAME["assertWithAI"].driver_method == "assert_with_ai"
    assert ANDROID_ACTION_DEFINITIONS_BY_NAME["assertWithAI"].step_kind == "assertion"


def test_web_action_definitions_are_single_source_for_web_contract() -> None:
    action_names = [definition.fsq_action_name for definition in WEB_ACTION_DEFINITIONS]

    assert len(action_names) == len(set(action_names))
    assert set(WEB_ACTION_DEFINITIONS_BY_NAME) == set(action_names)
    assert WEB_ACTION_DEFINITIONS_BY_NAME["startBrowser"].driver_method == "start_browser"
    assert WEB_ACTION_DEFINITIONS_BY_NAME["startBrowser"].params_model is WebStartBrowserParams
    assert WEB_ACTION_DEFINITIONS_BY_NAME["startBrowser"].step_kind == "setup"
    assert WEB_ACTION_DEFINITIONS_BY_NAME["closeBrowser"].driver_method == "close_browser"
    assert WEB_ACTION_DEFINITIONS_BY_NAME["closeBrowser"].params_model is WebCloseBrowserParams
    assert WEB_ACTION_DEFINITIONS_BY_NAME["closeBrowser"].step_kind == "teardown"
    assert WEB_ACTION_DEFINITIONS_BY_NAME["clickOn"].driver_method == "click_on"
    assert WEB_ACTION_DEFINITIONS_BY_NAME["clickOn"].params_model is WebClickOnParams
    assert WEB_ACTION_DEFINITIONS_BY_NAME["typeText"].driver_method == "type_text"
    assert WEB_ACTION_DEFINITIONS_BY_NAME["typeText"].params_model is WebTypeTextParams
    assert WEB_ACTION_DEFINITIONS_BY_NAME["waitFor"].driver_method == "wait_for"
    assert WEB_ACTION_DEFINITIONS_BY_NAME["waitFor"].params_model is WebWaitForParams
    assert WEB_ACTION_DEFINITIONS_BY_NAME["uiSnapshot"].driver_method == "ui_snapshot"
    assert WEB_ACTION_DEFINITIONS_BY_NAME["uiSnapshot"].params_model is WebUiSnapshotParams
    assert WEB_ACTION_DEFINITIONS_BY_NAME["uiSnapshot"].step_kind == "observation"
    assert WEB_ACTION_DEFINITIONS_BY_NAME["assertWithAI"].driver_method == "assert_with_ai"
    assert WEB_ACTION_DEFINITIONS_BY_NAME["assertWithAI"].owner == "driver"


def test_web_parameter_models_produce_canonical_dumps_and_reject_extra_fields() -> None:
    start = WebStartBrowserParams.model_validate({})
    close = WebCloseBrowserParams.model_validate({})
    target = {"page": "main", "steps": [{"kind": "css", "selector": "#control"}]}
    condition = {"kind": "text", "scope": {"kind": "page", "page": "main"}, "text": {"kind": "contains", "value": "Results"}, "present": True}
    click = WebClickOnParams.model_validate({"target": target})
    typed = WebTypeTextParams.model_validate({"target": target, "text": "bing.com"})
    wait = WebWaitForParams.model_validate({"condition": condition, "timeout_ms": 5000})

    assert start.model_dump(mode="json", exclude_none=True) == {}
    assert close.model_dump(mode="json", exclude_none=True) == {}
    assert click.model_dump(mode="json", exclude_none=True) == {"target": target, "button": "left", "click_count": 1, "modifiers": [], "timeout_ms": 10000}
    assert typed.model_dump(mode="json", exclude_none=True) == {
        "target": target,
        "text": "bing.com",
        "textType": "literal",
        "delay_ms": 0,
        "timeout_ms": 10000,
    }
    assert wait.model_dump(mode="json", exclude_none=True) == {"condition": condition, "timeout_ms": 5000}
    with pytest.raises(ValidationError):
        WebStartBrowserParams.model_validate({"url": "https://example.com"})
    with pytest.raises(ValidationError):
        WebCloseBrowserParams.model_validate({"force": True})
    with pytest.raises(ValidationError):
        WebClickOnParams.model_validate({"locator": {"ref": "e83"}})
    with pytest.raises(ValidationError):
        WebClickOnParams.model_validate({"locator": {"unknown": "Login"}})
    with pytest.raises(ValidationError):
        WebClickOnParams.model_validate({"value": "Login"})


def test_executable_step_accepts_contract_fields() -> None:
    step = ExecutableStep(
        step_id="step-1",
        source_ref=SourceRef(source_type="fsq", source_id="case.yaml", step_index=1),
        kind="action",
        action_name="tap",
        params={"text": "Login"},
        target_ref="button:login",
        retry_policy=RetryPolicy(max_attempts=2),
        evidence_policy=EvidencePolicy(capture_before=True, capture_after=True),
        timeout_ms=5000,
        metadata={"owner": "test"},
    )

    assert step.step_id == "step-1"
    assert step.kind == "action"
    assert step.retry_policy.max_attempts == 2
    assert step.evidence_policy.capture_after is True


def test_executable_step_rejects_unknown_kind() -> None:
    with pytest.raises(ValidationError):
        ExecutableStep(step_id="step-1", kind="unknown", action_name="tap")


def test_phase_report_preserves_phase_failure_boundary() -> None:
    report = StepPhaseReport(
        step_id="step-1",
        phase="prepare",
        status="failed",
        duration_ms=12,
        failure_category="context_error",
        error_message="context unavailable",
    )

    assert report.phase == "prepare"
    assert report.failure_category == "context_error"


def test_runner_event_requires_known_event_type() -> None:
    with pytest.raises(ValidationError):
        RunnerEvent(run_id="run-1", event_type="unknown", payload={})


def test_evidence_bundle_serializes_artifact_refs_without_binary_payloads() -> None:
    created_at = datetime.now(UTC)
    artifact = EvidenceArtifactRef(
        artifact_id="artifact-1",
        kind="screenshot",
        path=Path("runs/run-1/screenshot.png"),
        mime_type="image/png",
        created_at=created_at,
        step_id="step-1",
        phase="finalize",
    )
    bundle = EvidenceBundle(
        bundle_id="bundle-1",
        run_id="run-1",
        created_at=created_at,
        manifest_path=Path("runs/run-1/evidence.json"),
        artifacts=[artifact],
    )

    payload = bundle.model_dump(mode="json")

    assert payload["artifacts"][0]["path"] == "runs/run-1/screenshot.png"
    assert "bytes" not in payload["artifacts"][0]


def test_runner_step_result_uses_distinct_name_from_legacy_step_result() -> None:
    result = RunnerStepResult(
        step_id="step-1",
        status="passed",
        phase_reports=[StepPhaseReport(step_id="step-1", phase="invoke", status="passed")],
    )

    assert result.status == "passed"
    assert result.phase_reports[0].phase == "invoke"


def test_harness_models_capture_context_action_and_artifacts() -> None:
    artifact = HarnessArtifactRef(artifact_id="artifact-1", kind="log", path=Path("runs/run-1/action.log"))
    context = HarnessContext(platform="android", session_id="session-1", current_activity="MainActivity")
    result = HarnessActionResult(status="passed", action_name="tap", artifact_refs=[artifact])

    assert context.platform == "android"
    assert result.artifact_refs[0].kind == "log"
