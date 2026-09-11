# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import asyncio
import itertools
import json
import time
from collections.abc import Callable
from typing import Any

from fsq_agent._capability_bootstrap import build_capability_registry
from fsq_agent.adapters.coding_agent._web_response import project_web_response
from fsq_agent.agent_engine import ToolBinding, ToolCall, ToolInputFailure
from fsq_agent.core import HarnessInterface, RuntimeSecretStore, StepRunner
from fsq_agent.core.interfaces import EvidenceJournalSink
from fsq_agent.models import CapabilityDefinition, ConfigurationError, ExecutableStep, HarnessFunctionSchema, HarnessPlatform, PostActionDelaySettings, RunnerStepResult
from fsq_agent.tools import ToolArtifactStore


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
        evidence_sink: EvidenceJournalSink | None = None,
        cancellation_check=None,
        artifact_store: ToolArtifactStore | None = None,
        on_full_result: Callable[[str, dict[str, Any]], None] | None = None,
    ) -> None:
        self.cancellation_check = cancellation_check
        self.harness = harness
        self.run_id = run_id
        self.platform = platform
        self.artifact_store = artifact_store
        self.on_full_result = on_full_result
        self._capability_registry = build_capability_registry(platform=platform)
        self.runner = StepRunner(
            harness=harness,
            capability_registry=self._capability_registry,
            post_action_delay_seconds=post_action_delay_seconds,
            runtime_secret_store=runtime_secret_store,
            evidence_sink=evidence_sink,
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
            return self._format_failure(schema, ValueError(failure.message), 0, call_id=failure.call_id, action_effect="not_started")

        return reject

    def _handler_for(self, schema: HarnessFunctionSchema):
        async def invoke(call: ToolCall) -> str:
            started = time.perf_counter()
            runner_started = False
            try:
                if self.cancellation_check is not None:
                    self.cancellation_check()
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
                runner_started = True
                result = self.runner.run_step(run_id=self.run_id, step=step)
            # Tool transport must convert arbitrary capability failures into structured results.
            except Exception as exc:
                if getattr(exc, "fsq_evidence_fatal", False):
                    raise
                if type(exc).__name__ in {"TaskCancelledError", "ExecutionCancelled", "RunCancelled"}:
                    raise asyncio.CancelledError() from exc
                return self._format_failure(
                    schema,
                    exc,
                    int((time.perf_counter() - started) * 1000),
                    call_id=call.call_id,
                    action_effect="indeterminate" if runner_started else "not_started",
                )
            return self._format_runner_result(schema, step, result, int((time.perf_counter() - started) * 1000), call_id=call.call_id)

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
        *,
        call_id: str = "",
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
            "source_step_id": runner_result.source_step_id,
            "step_execution_id": runner_result.step_execution_id,
            "runner_result": runner_result.model_dump(mode="json"),
            "artifact_refs": artifact_refs,
        }
        if payload["safe_replay_params"] is None:
            del payload["safe_replay_params"]
        if self.platform == "web":
            payload["duration_ms"] = runner_result.duration_ms
            payload["action_status"] = runner_result.action_status
            harness_metadata = invoke_metadata.get("harness_metadata", {})
            for key in ("action_effect", "replay_unavailable_reason"):
                for metadata in (invoke_metadata, runner_result.metadata, harness_metadata):
                    if isinstance(metadata, dict) and key in metadata:
                        payload[key] = metadata[key]
                        break
        max_chars = step.params.get("max_chars")
        return self._serialize_response(schema, payload, call_id, observation_max_chars=max_chars if type(max_chars) is int else 12000)

    def _serialize_response(self, schema: HarnessFunctionSchema, payload: dict[str, Any], call_id: str, *, observation_max_chars: int = 12000) -> str:
        if self.platform != "web":
            return json.dumps(payload, ensure_ascii=False, default=str)
        # The full callback precedes all lossy presentation and optional artifact I/O.
        if self.on_full_result is not None:
            self.on_full_result(call_id, payload)
        full_text = json.dumps(payload, ensure_ascii=False, default=str)
        artifact: dict[str, Any] = {"path": None, "availability": "unavailable"}
        if self.artifact_store is not None:
            try:
                path = self.artifact_store.write(schema.name, full_text, {"source": "web_response_projection", "call_id": call_id})
            except OSError:
                path = None
                artifact["error_code"] = "artifact_write_failed"
            if path is not None:
                artifact = {"path": str(path), "availability": "available", "content_chars": len(full_text)}
        return project_web_response(payload, artifact=artifact, observation_max_chars=observation_max_chars)

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
        output = invoke_metadata.get("harness_output")
        if self.platform == "web":
            for report in reversed(runner_result.phase_reports):
                if report.phase != "finalize":
                    continue
                for ref in reversed(report.artifact_refs):
                    artifact_metadata = ref.model_dump(mode="json")["metadata"]
                    observation = artifact_metadata.get("snapshot")
                    if not isinstance(observation, dict):
                        observation = artifact_metadata.get("observation")
                    if ref.kind == "ui_snapshot" and ref.availability == "available" and isinstance(observation, dict):
                        if "coverage" not in observation and isinstance(artifact_metadata.get("coverage"), dict):
                            observation = {**observation, "coverage": artifact_metadata["coverage"]}
                        output = {**(output if isinstance(output, dict) else {"action_output": output}), "observation": observation}
                        break
                else:
                    continue
                break
        return {
            "status": runner_result.status,
            "action_name": step.action_name,
            "duration_ms": runner_result.duration_ms,
            "output": output,
            "artifact_refs": artifact_refs,
            "error_message": runner_result.error_message,
            "failure_category": runner_result.failure_category,
            "metadata": metadata,
        }

    def _invoke_metadata(self, runner_result: RunnerStepResult) -> dict[str, Any]:
        for phase_report in runner_result.phase_reports:
            if phase_report.phase == "invoke":
                if self.platform == "web":
                    return phase_report.model_dump(mode="json")["metadata"]
                return dict(phase_report.metadata)
        return {}

    def _artifact_refs(self, runner_result: RunnerStepResult) -> list[dict[str, Any]]:
        refs: list[dict[str, Any]] = []
        for phase_report in runner_result.phase_reports:
            refs.extend(ref.model_dump(mode="json") for ref in phase_report.artifact_refs)
        return refs

    def _format_failure(self, schema: HarnessFunctionSchema, error: Exception, duration_ms: int, *, call_id: str = "", action_effect: str = "not_started") -> str:
        error_message = str(error) or error.__class__.__name__
        action_name = self._capability_name(schema)
        step_kind = self._step_kind(schema)
        payload = {
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
        }
        if self.platform == "web":
            payload["action_effect"] = action_effect
        return self._serialize_response(schema, payload, call_id)

    def _tool_origin(self, schema: HarnessFunctionSchema) -> str:
        return "common" if schema.metadata.get("executor_kind") == "common" else "platform"
