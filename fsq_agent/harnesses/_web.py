# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import json
from contextlib import nullcontext
from typing import ClassVar

from pydantic import BaseModel, ValidationError

from fsq_agent.core.evidence import ArtifactStore
from fsq_agent.core.interfaces import AIAssertionEvaluatorProtocol, WebDriverInterface
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
    WebFindElementsParams,
    WebInspectElementParams,
    WebTakeScreenshotParams,
    WebUiSnapshotParams,
)


class _WebObservationError(RuntimeError):
    pass


class WebHarness:
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
        driver: WebDriverInterface,
        artifact_store: ArtifactStore | None = None,
        ai_assertion_evaluator: AIAssertionEvaluatorProtocol | None = None,
        runtime_secret_settings: RuntimeSecretSettings | None = None,
    ) -> None:
        self.driver = driver
        self.artifact_store = artifact_store
        self.ai_assertion_evaluator = ai_assertion_evaluator
        self._owned_resources = OwnedResources(driver, ai_assertion_evaluator)
        self.common_tools = CommonPlatformTools(
            platform="web",
        )
        self._configure_driver_ai_assertion_tool()

    def capture_scope(self, callback):
        return self.artifact_store.capture_scope(callback) if self.artifact_store is not None else nullcontext()

    def close(self) -> None:
        self._owned_resources.close()

    def get_context(self) -> HarnessContext:
        context = self.driver.context()
        return HarnessContext(
            platform="web",
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
                error_message=f"Unsupported Web action: {step.action_name}",
            )
        if capability.executor_kind == "driver":
            params = self._validate_params(step, capability.params_model)
            if isinstance(params, HarnessActionResult):
                return params
            driver_method_name = str(capability.metadata.get("driver_method") or capability.name)
            if self.artifact_store is None and (isinstance(params, WebTakeScreenshotParams) or (isinstance(params, WebUiSnapshotParams) and params.view == "full")):
                return HarnessActionResult(
                    status="failed",
                    action_name=step.action_name,
                    failure_category="configuration_error",
                    error_message="Screenshots and full Web observations require an ArtifactStore.",
                    metadata={"action_effect": "not_started", "error_code": "artifact_store_unavailable"},
                )
            driver_method = getattr(self.driver, driver_method_name)
            self._prepare_driver_ai_assertion_tool_invocation(step, context)
            try:
                output = driver_method(params)
            finally:
                self._clear_driver_ai_assertion_tool_invocation()
            result = self._result_from_driver_output(step.action_name, output)
            if result.status == "passed" and isinstance(params, WebTakeScreenshotParams):
                return self._screenshot_result(step, result)
            if result.status == "passed" and isinstance(params, WebUiSnapshotParams | WebFindElementsParams | WebInspectElementParams):
                return self._observation_result(step, result, max_chars=params.max_chars)
            return result
        return HarnessActionResult(
            status="failed",
            action_name=step.action_name,
            failure_category="configuration_error",
            error_message=f"Unsupported Web capability executor: {capability.executor_kind}",
        )

    def after_action(
        self,
        step: ExecutableStep,
        context: HarnessContext,
        action_result: HarnessActionResult | None,
    ) -> None:
        return None

    def screenshot(self, params: WebTakeScreenshotParams | None = None) -> bytes:
        return self.driver.screenshot(params or WebTakeScreenshotParams(page=self._capture_page(self.get_context())))

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
        if self._browser_not_started(context):
            return self._to_harness_artifact_ref(
                self.artifact_store.write_json(
                    kind="json",
                    step_id=step_id,
                    phase=phase,
                    name=f"{reason}-{kind}-unavailable",
                    payload={
                        "status": "unavailable",
                        "reason": "browser_not_started",
                        "message": "Browser is not started. Call startBrowser before Web page actions.",
                        "requested_artifact_kind": kind,
                    },
                )
            )
        if kind == "screenshot":
            return self._to_harness_artifact_ref(
                self.artifact_store.write_bytes(
                    kind="screenshot",
                    step_id=step_id,
                    phase=phase,
                    name=reason,
                    data=self.screenshot(WebTakeScreenshotParams(page=self._capture_page(context))),
                )
            )
        if kind == "ui_snapshot":
            snapshot = self.driver.ui_snapshot(WebUiSnapshotParams(scope={"kind": "page", "page": self._capture_page(context)}))
            if snapshot.get("status") == "failed":
                raise _WebObservationError(str(snapshot.get("error_message") or "Web semantic observation failed."))
            observation = self._observation_value(snapshot.get("output", snapshot))
            _compact, ref = self._persist_observation(observation, step_id=step_id, phase=phase, name=reason)
            if ref is None:
                raise RuntimeError("Artifact capture requires an ArtifactStore.")
            return ref
        raise RuntimeError(f"Unsupported Web artifact kind: {kind}")

    def _capture_page(self, context: HarnessContext) -> str:
        page = context.metadata.get("active_page")
        return page if isinstance(page, str) and page else "main"

    def _observation_value(self, output: object) -> dict[str, object]:
        if isinstance(output, dict):
            observation = output.get("observation", output)
            if isinstance(observation, dict) and observation.get("schema_version") == "fsq.web-observation/v1":
                return observation
        raise _WebObservationError("The Web driver did not return a structured observation.")

    def _screenshot_result(self, step: ExecutableStep, result: HarnessActionResult) -> HarnessActionResult:
        output = result.output
        png = output.get("png") if isinstance(output, dict) else None
        if not isinstance(png, bytes) or not png:
            raise _WebObservationError("The Web driver did not return screenshot bytes.")
        if self.artifact_store is None:
            raise RuntimeError("Screenshot persistence requires an ArtifactStore.")
        ref = self._to_harness_artifact_ref(self.artifact_store.write_bytes(kind="screenshot", step_id=step.step_id, phase="invoke", name=step.action_name, data=png))
        return result.model_copy(update={"output": {key: value for key, value in output.items() if key != "png"}, "artifact_refs": [*result.artifact_refs, ref]})

    def _observation_result(self, step: ExecutableStep, result: HarnessActionResult, *, max_chars: int) -> HarnessActionResult:
        observation = self._observation_value(result.output)
        compact, ref = self._persist_observation(observation, step_id=step.step_id, phase="invoke", name=step.action_name, max_chars=max_chars)
        if isinstance(result.output, dict) and "observation" in result.output:
            output = {**result.output, "observation": compact}
        else:
            output = compact
        return result.model_copy(
            update={
                "output": output,
                "artifact_refs": [*result.artifact_refs, *([ref] if ref is not None else [])],
                "metadata": {**result.metadata, "full_evidence_available": ref is not None},
            }
        )

    def _persist_observation(
        self,
        observation: dict[str, object],
        *,
        step_id: str,
        phase: StepPhase,
        name: str,
        max_chars: int = 12000,
    ) -> tuple[dict[str, object], HarnessArtifactRef | None]:
        compact = {key: value for key, value in observation.items() if key not in {"full_source", "full_artifact_ref"}}
        if self.artifact_store is None:
            return compact, None
        ref = self._to_harness_artifact_ref(
            self.artifact_store.write_json(
                kind="ui_snapshot",
                step_id=step_id,
                phase=phase,
                name=name,
                payload={"snapshot": compact, "coverage": observation["coverage"], "full_source": observation.get("full_source")},
            )
        )
        # Artifact metadata is already credential-sanitized by the storage boundary.
        compact = dict(ref.metadata["snapshot"])
        referenced = {**compact, "full_artifact_ref": str(ref.path)}
        if len(json.dumps(referenced, ensure_ascii=False)) <= max_chars:
            compact = referenced
        return compact, ref

    def classify_error(self, error: BaseException, phase: StepPhase, step: ExecutableStep) -> FailureCategory:
        if isinstance(error, _WebObservationError):
            return "observation_error"
        if isinstance(error, OSError):
            return "artifact_error"
        return "harness_error"

    def _to_harness_artifact_ref(self, ref: object) -> HarnessArtifactRef:
        if isinstance(ref, HarnessArtifactRef):
            return ref
        data = ref if isinstance(ref, dict) else ref.model_dump()  # type: ignore[attr-defined]
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
            platform="web",
            metadata=updates,
        )
        return [_with_driver_metadata(definition, updates) for definition in definitions]

    def _configure_driver_ai_assertion_tool(self) -> None:
        configure = getattr(self.driver, "configure_ai_assertion_tool", None)
        if callable(configure):
            configure(
                platform="web",
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
        return _schema_from_capability_definition(definition, platform="web")

    def _validate_params(self, step: ExecutableStep, params_model: type[BaseModel]) -> BaseModel | HarnessActionResult:
        try:
            return params_model.model_validate(step.params)
        except ValidationError as exc:
            return HarnessActionResult(
                status="failed",
                action_name=step.action_name,
                failure_category="configuration_error",
                error_message=f"Invalid Web parameters for {step.action_name}.",
                metadata={"validation_errors": self._validation_errors(exc), "action_effect": "not_started"},
            )

    def _validation_errors(self, error: ValidationError) -> list[dict[str, object]]:
        return error.errors(include_url=False, include_context=False, include_input=False)

    def _result_from_driver_output(self, action_name: str, output: object) -> HarnessActionResult:
        if not isinstance(output, dict) or "status" not in output:
            return HarnessActionResult(status="passed", action_name=action_name, output=output)
        status_value = output.get("status")
        if not isinstance(status_value, str) or status_value not in self._RUNNER_STATUSES:
            return HarnessActionResult(
                status="failed",
                action_name=action_name,
                failure_category="harness_error",
                error_message="The Web driver returned an invalid execution status.",
                metadata={"error_code": "invalid_driver_result", "action_effect": "indeterminate"},
            )
        status = status_value
        failure_category_value = output.get("failure_category")
        failure_category = failure_category_value if isinstance(failure_category_value, str) and failure_category_value in self._FAILURE_CATEGORIES else None
        error_message_value = output.get("error_message")
        metadata_value = output.get("metadata")
        metadata = dict(metadata_value) if isinstance(metadata_value, dict) else {}
        if isinstance(failure_category_value, str) and failure_category_value and failure_category is None:
            metadata.setdefault("error_code", failure_category_value)
            failure_category = "target_resolution_error" if failure_category_value.startswith("target_") else "harness_error"
        artifact_refs_value = output.get("artifact_refs")
        artifact_refs = [self._to_harness_artifact_ref(ref) for ref in artifact_refs_value] if isinstance(artifact_refs_value, list) else []
        return HarnessActionResult(
            status=status,
            action_name=action_name,
            output=output.get("output"),
            artifact_refs=artifact_refs,
            error_message=error_message_value if isinstance(error_message_value, str) else None,
            failure_category=failure_category,
            metadata=metadata,
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

    def _browser_not_started(self, context: HarnessContext) -> bool:
        return context.metadata.get("browser_started") is False

    def _metadata_str(self, metadata: dict[str, object], key: str) -> str | None:
        value = metadata.get(key)
        return value if isinstance(value, str) else None
