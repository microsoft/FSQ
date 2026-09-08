# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import itertools
import json
import time
from typing import Any

from fsq_agent._capability_bootstrap import build_capability_registry
from fsq_agent.agent_engine import ToolBinding, ToolCall, ToolInputFailure
from fsq_agent.core import HarnessInterface, RuntimeSecretStore, StepRunner
from fsq_agent.models import CapabilityDefinition, ConfigurationError, ExecutableStep, HarnessFunctionSchema, HarnessPlatform, PostActionDelaySettings, RunnerStepResult


class HarnessToolAdapter:
    def __init__(
        self,
        harness: HarnessInterface,
        *,
        run_id: str,
        reserved_tool_names: set[str] | None = None,
        post_action_delay_seconds: PostActionDelaySettings | None = None,
        runtime_secret_store: RuntimeSecretStore | None = None,
        platform: HarnessPlatform = "android",
    ) -> None:
        self.harness = harness
        self.run_id = run_id
        self._capability_registry = build_capability_registry(platform=platform)
        self.runner = StepRunner(
            harness=harness,
            capability_registry=self._capability_registry,
            post_action_delay_seconds=post_action_delay_seconds,
            runtime_secret_store=runtime_secret_store,
        )
        self.reserved_tool_names = reserved_tool_names or set()
        self._counter = itertools.count(1)
        self._capability_snapshot = self._capability_registry.snapshot()
        self.schemas = self._discover_schemas()
        self.schemas_by_name = {schema.name: schema for schema in self.schemas}

    @property
    def tool_names(self) -> set[str]:
        return set(self.schemas_by_name)

    def build_tools(self) -> list[ToolBinding]:
        return [
            ToolBinding(
                name=schema.name,
                description=schema.description or f"Run platform action {schema.name} through the active harness.",
                parameters_schema=schema.params_json_schema,
                strict=True,
                invoke=self._handler_for(schema),
                on_invalid_input=self._invalid_input_handler_for(schema),
            )
            for schema in self.schemas
        ]

    def run_step_with_capability_result(self, run_id: str, step: ExecutableStep):
        result = self.runner.run_step(run_id=run_id, step=step)
        return result, self.runner.last_capability_execution_result

    def _discover_schemas(self) -> list[HarnessFunctionSchema]:
        try:
            schemas = self.harness.action_space()
        # Harness plugins may raise backend-specific exceptions that must become configuration errors.
        except Exception as exc:
            raise ConfigurationError("Harness action-space discovery failed.", context={"error": str(exc)}) from exc
        names: set[str] = set()
        for schema in schemas:
            if schema.name in names:
                raise ConfigurationError("Harness action-space contains duplicate tool names.", context={"tool_name": schema.name})
            if schema.name in self.reserved_tool_names:
                raise ConfigurationError("Harness action-space conflicts with a local tool name.", context={"tool_name": schema.name})
            names.add(schema.name)
        return schemas

    def _invalid_input_handler_for(self, schema: HarnessFunctionSchema):
        async def reject(failure: ToolInputFailure) -> str:
            return self._format_failure(schema, ValueError(failure.message), 0)

        return reject

    def _handler_for(self, schema: HarnessFunctionSchema):
        async def invoke(call: ToolCall) -> str:
            started = time.perf_counter()
            try:
                params = call.arguments
                action_name = self._capability_name(schema)
                step = ExecutableStep(
                    step_id=f"agent-{schema.name}-{next(self._counter)}",
                    kind=self._step_kind(schema),
                    action_name=action_name,
                    params=params,
                    metadata={
                        "run_id": self.run_id,
                        "tool_origin": self._tool_origin(schema),
                        "tool_name": schema.name,
                        "capability_name": action_name,
                        "executor_kind": schema.metadata.get("executor_kind"),
                        "replay": schema.metadata.get("replay"),
                        "platform": schema.platform,
                        "driver_method": schema.driver_method,
                        "fsq_action_name": schema.fsq_action_name,
                        "authored_action_name": schema.fsq_action_name,
                        "schema_metadata": schema.metadata,
                    },
                )
                result = self.runner.run_step(run_id=self.run_id, step=step)
                return self._format_runner_result(schema, step, result, int((time.perf_counter() - started) * 1000))
            # SDK tool transport must convert arbitrary capability failures into structured results.
            except Exception as exc:  # noqa: BLE001
                return self._format_failure(schema, exc, int((time.perf_counter() - started) * 1000))

        return invoke

    def _capability_name(self, schema: HarnessFunctionSchema) -> str:
        value = schema.metadata.get("capability_name")
        if isinstance(value, str) and value:
            return value
        capability = self._capability_for_schema(schema)
        return capability.name if capability is not None else schema.driver_method

    def _step_kind(self, schema: HarnessFunctionSchema):
        value = schema.metadata.get("step_kind")
        if value in {"action", "assertion", "observation", "diagnostic", "setup", "teardown"}:
            return value
        capability = self._capability_for_schema(schema)
        if capability is not None:
            return capability.step_kind
        return "action"

    def _capability_for_schema(self, schema: HarnessFunctionSchema) -> CapabilityDefinition | None:
        return self._capability_snapshot.resolve(schema.fsq_action_name or schema.driver_method)

    def _format_runner_result(
        self,
        schema: HarnessFunctionSchema,
        step: ExecutableStep,
        runner_result: RunnerStepResult,
        duration_ms: int,
    ) -> str:
        step_kind = self._step_kind(schema)
        artifact_refs = self._artifact_refs(runner_result)
        result_summary = self._result_summary(schema, step, runner_result, artifact_refs)
        invoke_metadata = self._invoke_metadata(runner_result)
        payload = {
            "tool_name": schema.name,
            "tool_origin": self._tool_origin(schema),
            "capability_name": self._capability_name(schema),
            "executor_kind": schema.metadata.get("executor_kind"),
            "step_kind": step_kind,
            "replay": schema.metadata.get("replay"),
            "safe_replay_params": invoke_metadata.get("safe_replay_params"),
            "platform": schema.platform,
            "driver_method": schema.driver_method,
            "fsq_action_name": schema.fsq_action_name,
            "status": runner_result.status,
            "failure_category": runner_result.failure_category,
            "error_message": runner_result.error_message,
            "duration_ms": runner_result.duration_ms or duration_ms,
            "result": result_summary,
            "metadata": schema.metadata,
            "runner_step_id": runner_result.step_id,
            "runner_result": runner_result.model_dump(mode="json"),
            "artifact_refs": artifact_refs,
        }
        if payload["safe_replay_params"] is None:
            del payload["safe_replay_params"]
        return json.dumps(payload, ensure_ascii=False, default=str)

    def _result_summary(
        self,
        schema: HarnessFunctionSchema,
        step: ExecutableStep,
        runner_result: RunnerStepResult,
        artifact_refs: list[dict[str, Any]],
    ) -> dict[str, Any]:
        step_kind = self._step_kind(schema)
        invoke_metadata = self._invoke_metadata(runner_result)
        metadata: dict[str, Any] = {
            "tool_origin": self._tool_origin(schema),
            "tool_name": schema.name,
            "capability_name": self._capability_name(schema),
            "executor_kind": schema.metadata.get("executor_kind"),
            "step_kind": step_kind,
            "replay": schema.metadata.get("replay"),
            "platform": schema.platform,
            "driver_method": schema.driver_method,
            "fsq_action_name": schema.fsq_action_name,
            "schema_metadata": schema.metadata,
        }
        harness_metadata = invoke_metadata.get("harness_metadata")
        if isinstance(harness_metadata, dict):
            metadata["harness_metadata"] = harness_metadata
        return {
            "status": runner_result.status,
            "action_name": step.action_name,
            "duration_ms": runner_result.duration_ms,
            "output": invoke_metadata.get("harness_output"),
            "artifact_refs": artifact_refs,
            "error_message": runner_result.error_message,
            "failure_category": runner_result.failure_category,
            "metadata": metadata,
        }

    def _invoke_metadata(self, runner_result: RunnerStepResult) -> dict[str, Any]:
        for phase_report in runner_result.phase_reports:
            if phase_report.phase == "invoke":
                return dict(phase_report.metadata)
        return {}

    def _artifact_refs(self, runner_result: RunnerStepResult) -> list[dict[str, Any]]:
        refs: list[dict[str, Any]] = []
        for phase_report in runner_result.phase_reports:
            refs.extend(ref.model_dump(mode="json") for ref in phase_report.artifact_refs)
        return refs

    def _format_failure(self, schema: HarnessFunctionSchema, error: Exception, duration_ms: int) -> str:
        error_message = str(error) or error.__class__.__name__
        action_name = self._capability_name(schema)
        step_kind = self._step_kind(schema)
        return json.dumps(
            {
                "tool_name": schema.name,
                "tool_origin": self._tool_origin(schema),
                "capability_name": action_name,
                "executor_kind": schema.metadata.get("executor_kind"),
                "step_kind": step_kind,
                "replay": schema.metadata.get("replay"),
                "platform": schema.platform,
                "driver_method": schema.driver_method,
                "fsq_action_name": schema.fsq_action_name,
                "status": "failed",
                "failure_category": "harness_error",
                "error_message": error_message,
                "duration_ms": duration_ms,
                "result": {
                    "status": "failed",
                    "action_name": action_name,
                    "artifact_refs": [],
                    "error_message": error_message,
                    "failure_category": "harness_error",
                    "metadata": {
                        "tool_origin": self._tool_origin(schema),
                        "tool_name": schema.name,
                        "capability_name": action_name,
                        "executor_kind": schema.metadata.get("executor_kind"),
                        "step_kind": step_kind,
                        "replay": schema.metadata.get("replay"),
                        "platform": schema.platform,
                        "driver_method": schema.driver_method,
                        "fsq_action_name": schema.fsq_action_name,
                        "schema_metadata": schema.metadata,
                    },
                },
                "metadata": schema.metadata,
                "artifact_refs": [],
            },
            ensure_ascii=False,
            default=str,
        )

    def _tool_origin(self, schema: HarnessFunctionSchema) -> str:
        return "common" if schema.metadata.get("executor_kind") == "common" else "platform"
