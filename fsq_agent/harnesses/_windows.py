# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from contextlib import nullcontext
from typing import ClassVar

from pydantic import BaseModel, ValidationError

from fsq_agent.core.evidence import ArtifactStore
from fsq_agent.core.interfaces import AIAssertionEvaluatorProtocol, WindowsDriverInterface
from fsq_agent.drivers._capabilities import _capability_matches, _discover_driver_capability_definitions, _schema_from_capability_definition, _with_driver_metadata
from fsq_agent.harnesses._common_tools import CommonPlatformTools
from fsq_agent.harnesses._resources import OwnedResources
from fsq_agent.models import (
    CapabilityDefinition,
    ExecutableStep,
    FailureCategory,
    HarnessActionResult,
    HarnessArtifactRef,
    HarnessContext,
    HarnessFunctionSchema,
    RuntimeSecretSettings,
    StepPhase,
    WindowsUiSnapshotParams,
)


class WindowsHarness:
    _RUNNER_STATUSES: ClassVar[set[str]] = {"passed", "failed", "skipped", "cancelled"}
    _FAILURE_CATEGORIES: ClassVar[set[str]] = {
        "configuration_error",
        "context_error",
        "target_resolution_error",
        "action_error",
        "assertion_error",
        "timeout_error",
        "observation_error",
        "artifact_error",
        "harness_error",
        "cancelled",
        "unknown",
    }

    def __init__(
        self,
        driver: WindowsDriverInterface,
        artifact_store: ArtifactStore | None = None,
        ai_assertion_evaluator: AIAssertionEvaluatorProtocol | None = None,
        runtime_secret_settings: RuntimeSecretSettings | None = None,
    ) -> None:
        self.driver = driver
        self.artifact_store = artifact_store
        self.ai_assertion_evaluator = ai_assertion_evaluator
        self._owned_resources = OwnedResources(driver, ai_assertion_evaluator)
        self.common_tools = CommonPlatformTools(
            platform="windows",
        )
        self._configure_driver_ai_assertion_tool()

    def capture_scope(self, callback):
        return self.artifact_store.capture_scope(callback) if self.artifact_store is not None else nullcontext()

    def close(self) -> None:
        self._owned_resources.close()

    def get_context(self) -> HarnessContext:
        context = self.driver.context()
        return HarnessContext(
            platform="windows",
            session_id=self._optional_str(context.get("session_id")),
            current_url=self._optional_str(context.get("current_url")),
            screen_size=self._screen_size(context.get("screen_size")),
            capabilities=self._dict_value(context.get("capabilities")),
            metadata=self._dict_value(context.get("metadata")),
        )

    def action_space(self) -> list[HarnessFunctionSchema]:
        definitions = self._capability_definitions()
        if self.ai_assertion_evaluator is None:
            definitions = [definition for definition in definitions if definition.name != "assert_with_ai"]
        return [*self.common_tools.common_action_space(), *[self._schema_from_capability(definition) for definition in definitions]]

    def before_action(self, step: ExecutableStep, context: HarnessContext) -> None:
        return None

    def invoke_action(self, step: ExecutableStep, context: HarnessContext) -> HarnessActionResult:
        if self.common_tools.common_capability_for(step.action_name) is not None:
            return self.common_tools.invoke_common_tool(step)
        capability = self._capability_for(step.action_name)
        if capability is None:
            return HarnessActionResult(
                status="failed",
                action_name=step.action_name,
                failure_category="configuration_error",
                error_message=f"Unsupported Windows action: {step.action_name}",
            )
        if capability.executor_kind == "driver":
            params = self._validate_params(step, capability.params_model)
            if isinstance(params, HarnessActionResult):
                return params
            driver_method_name = str(capability.metadata.get("driver_method") or capability.name)
            driver_method = getattr(self.driver, driver_method_name)
            self._prepare_driver_ai_assertion_tool_invocation(step, context)
            try:
                output = driver_method(params)
            finally:
                self._clear_driver_ai_assertion_tool_invocation()
            return self._result_from_driver_output(step.action_name, output)
        return HarnessActionResult(
            status="failed",
            action_name=step.action_name,
            failure_category="configuration_error",
            error_message=f"Unsupported Windows capability executor: {capability.executor_kind}",
        )

    def after_action(
        self,
        step: ExecutableStep,
        context: HarnessContext,
        action_result: HarnessActionResult | None,
    ) -> None:
        return None

    def capture_artifact(
        self,
        kind: str,
        reason: str,
        context: HarnessContext,
        step_id: str,
        phase: StepPhase,
    ) -> HarnessArtifactRef:
        if self.artifact_store is None:
            raise RuntimeError("Artifact capture requires an ArtifactStore.")
        if kind == "screenshot":
            return self._to_harness_artifact_ref(
                self.artifact_store.write_bytes(
                    kind="screenshot",
                    step_id=step_id,
                    phase=phase,
                    name=reason,
                    data=self._capture_screenshot(phase),
                )
            )
        if kind == "ui_snapshot":
            return self._to_harness_artifact_ref(
                self.artifact_store.write_json(
                    kind="ui_snapshot",
                    step_id=step_id,
                    phase=phase,
                    name=reason,
                    payload=self._capture_ui_snapshot(phase),
                )
            )
        raise RuntimeError(f"Unsupported Windows artifact kind: {kind}")

    def _capture_screenshot(self, phase: StepPhase) -> bytes:
        return self.driver.screenshot()

    def _capture_ui_snapshot(self, phase: StepPhase) -> dict[str, object]:
        return self.driver.ui_snapshot(WindowsUiSnapshotParams())

    def classify_error(self, error: BaseException, phase: StepPhase, step: ExecutableStep) -> FailureCategory:
        if isinstance(error, TimeoutError):
            return "timeout_error"
        if isinstance(error, LookupError):
            return "target_resolution_error"
        return "harness_error"

    def _to_harness_artifact_ref(self, ref: object) -> HarnessArtifactRef:
        if isinstance(ref, HarnessArtifactRef):
            return ref
        data = ref.model_dump()  # type: ignore[attr-defined]
        return HarnessArtifactRef(
            artifact_id=data["artifact_id"],
            kind=data["kind"],
            path=data["path"],
            mime_type=data.get("mime_type"),
            size_bytes=data.get("size_bytes"),
            sha256=data.get("sha256"),
            availability=data.get("availability", "available"),
            unavailable_reason=data.get("unavailable_reason"),
            capture_occurrence=data.get("capture_occurrence"),
            created_at=data["created_at"],
            metadata=dict(data.get("metadata") or {}),
        )

    def _capability_for(self, name_or_alias: str) -> CapabilityDefinition | None:
        for capability in self._capability_definitions():
            if _capability_matches(capability, name_or_alias):
                return capability
        return None

    def _capability_definitions(self) -> list[CapabilityDefinition]:
        backend = getattr(self.driver, "backend", None)
        driver_class = type(self.driver).__name__
        updates = {"driver_class": driver_class}
        if isinstance(backend, str):
            updates["backend"] = backend
        definitions = _discover_driver_capability_definitions(
            self.driver,
            platform="windows",
            metadata=updates,
        )
        return [_with_driver_metadata(definition, updates) for definition in definitions]

    def _configure_driver_ai_assertion_tool(self) -> None:
        configure = getattr(self.driver, "configure_ai_assertion_tool", None)
        if callable(configure):
            configure(
                platform="windows",
                artifact_store=self.artifact_store,
                ai_assertion_evaluator=self.ai_assertion_evaluator,
            )

    def _prepare_driver_ai_assertion_tool_invocation(self, step: ExecutableStep, context: HarnessContext) -> None:
        prepare = getattr(self.driver, "prepare_ai_assertion_tool_invocation", None)
        if callable(prepare):
            prepare(
                context=context,
                step_id=step.step_id,
                action_name=step.action_name,
                metadata=step.metadata,
                capture_artifact=self.capture_artifact,
            )

    def _clear_driver_ai_assertion_tool_invocation(self) -> None:
        clear = getattr(self.driver, "clear_ai_assertion_tool_invocation", None)
        if callable(clear):
            clear()

    def _schema_from_capability(self, definition: CapabilityDefinition) -> HarnessFunctionSchema:
        return _schema_from_capability_definition(definition, platform="windows")

    def _validate_params(self, step: ExecutableStep, params_model: type[BaseModel]) -> BaseModel | HarnessActionResult:
        try:
            return params_model.model_validate(step.params)
        except ValidationError as exc:
            validation_errors = self._validation_errors(exc)
            return HarnessActionResult(
                status="failed",
                action_name=step.action_name,
                failure_category="configuration_error",
                error_message=self._validation_error_message(step.action_name, validation_errors),
                metadata={"validation_errors": validation_errors},
            )

    def _validation_error_message(self, action_name: str, errors: list[dict[str, object]]) -> str:
        details: list[str] = []
        for error in errors:
            location = error.get("loc")
            message = error.get("msg")
            if isinstance(location, (list, tuple)) and isinstance(message, str):
                path = ".".join(str(part) for part in location)
                details.append(f"{path}: {message}")
        suffix = f" {'; '.join(details)}" if details else ""
        return f"Invalid Windows parameters for {action_name}.{suffix}"

    def _validation_errors(self, error: ValidationError) -> list[dict[str, object]]:
        try:
            return error.errors(include_url=False, include_context=False)
        except TypeError:
            return error.errors()

    def _result_from_driver_output(self, action_name: str, output: object) -> HarnessActionResult:
        if not isinstance(output, dict) or "status" not in output:
            return HarnessActionResult(status="passed", action_name=action_name, output=output)
        status_value = output.get("status")
        status = status_value if isinstance(status_value, str) and status_value in self._RUNNER_STATUSES else "passed"
        failure_category_value = output.get("failure_category")
        failure_category = failure_category_value if isinstance(failure_category_value, str) and failure_category_value in self._FAILURE_CATEGORIES else None
        error_message_value = output.get("error_message")
        metadata_value = output.get("metadata")
        artifact_refs_value = output.get("artifact_refs")
        artifact_refs = [self._to_harness_artifact_ref(ref) for ref in artifact_refs_value] if isinstance(artifact_refs_value, list) else []
        return HarnessActionResult(
            status=status,
            action_name=action_name,
            output=output.get("output"),
            artifact_refs=artifact_refs,
            error_message=error_message_value if isinstance(error_message_value, str) else None,
            failure_category=failure_category,
            metadata=metadata_value if isinstance(metadata_value, dict) else {},
        )

    def _optional_str(self, value: object) -> str | None:
        return value if isinstance(value, str) else None

    def _screen_size(self, value: object) -> tuple[int, int] | None:
        if not isinstance(value, tuple) or len(value) != 2:
            return None
        width, height = value
        if not isinstance(width, int) or not isinstance(height, int):
            return None
        return (width, height)

    def _dict_value(self, value: object) -> dict[str, object]:
        return value if isinstance(value, dict) else {}

    def _metadata_str(self, metadata: dict[str, object], key: str) -> str | None:
        value = metadata.get(key)
        return value if isinstance(value, str) else None
