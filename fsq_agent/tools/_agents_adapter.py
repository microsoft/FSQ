# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import asyncio
import inspect
import itertools
import json
import time
from collections.abc import Callable
from typing import Any

from fsq_agent.agent_engine import ToolBinding, ToolCall, ToolInputFailure
from fsq_agent.models import (
    AgentToolCall,
    AgentToolResult,
    CapabilityExecutionResult,
    ExecutableStep,
    LocalToolOutputSettings,
    RunEvent,
    RunEventSink,
    RunnerStepResult,
)
from fsq_agent.tools._agent_tools import AgentToolExecutor, AgentToolRegistry, DefaultAgentToolProvider

RunnerInvoker = Callable[
    [str, ExecutableStep],
    RunnerStepResult | tuple[RunnerStepResult, CapabilityExecutionResult | None],
]


class AgentToolAdapter:
    def __init__(
        self,
        registry: AgentToolRegistry,
        *,
        local_tool_output_settings: LocalToolOutputSettings | None = None,
    ) -> None:
        self.registry = registry
        self.executor = AgentToolExecutor(registry)
        self.local_tool_output_settings = local_tool_output_settings or LocalToolOutputSettings()
        self.run_id = ""
        self.task_id = ""
        self.event_sink: RunEventSink | None = None
        self.runner_invoker: RunnerInvoker | None = None
        self._counter = itertools.count(1)

    def build_tools(
        self,
        *,
        run_id: str = "",
        task_id: str = "",
        event_sink: RunEventSink | None = None,
        runner_invoker: RunnerInvoker | None = None,
    ) -> list[ToolBinding]:
        self.run_id = run_id
        self.task_id = task_id
        self.event_sink = event_sink
        self.runner_invoker = runner_invoker
        self._configure_provider_runs(run_id)
        return [
            ToolBinding(
                name=definition.name,
                description=definition.description,
                parameters_schema=definition.params_json_schema,
                strict=definition.strict,
                invoke=self._handler_for(definition.name),
                on_invalid_input=self._invalid_input_handler_for(definition.name),
            )
            for definition in self.registry.list_tools()
        ]

    def _invalid_input_handler_for(self, tool_name: str):
        async def reject(failure: ToolInputFailure) -> str:
            result = AgentToolResult(tool_name=tool_name, status="failed", error=failure.message, duration_ms=0)
            await self._emit_tool_failed(tool_name, failure.message, time.perf_counter(), {})
            return self._format_tool_response(result)

        return reject

    def _handler_for(self, tool_name: str):
        async def invoke(call: ToolCall) -> str:
            started = time.perf_counter()
            arguments: dict[str, Any] = {}
            try:
                arguments = call.arguments
                await self._emit_tool_started(tool_name, arguments)
                if self.runner_invoker is not None and self._recordable_capability(tool_name) is not None:
                    result = await self._execute_through_runner(tool_name, arguments)
                else:
                    result = await self.executor.execute(AgentToolCall(tool_name=tool_name, arguments=arguments))
            # The tool callback boundary normalizes all tool failures into structured results.
            except Exception as exc:  # noqa: BLE001
                result = AgentToolResult(
                    tool_name=tool_name,
                    status="failed",
                    error=str(exc) or exc.__class__.__name__,
                    duration_ms=int((time.perf_counter() - started) * 1000),
                )
                output = self._format_tool_response(result)
                await self._emit_tool_failed(tool_name, result.error or "AgentTool failed.", started, arguments)
                return output
            output = self._format_tool_response(result)
            if result.status == "failed":
                await self._emit_tool_failed(tool_name, result.error or "AgentTool failed.", started, arguments)
            else:
                await self._emit_tool_completed(tool_name, arguments, output, started, result)
            return output

        return invoke

    async def _execute_through_runner(self, tool_name: str, arguments: dict[str, Any]) -> AgentToolResult:
        if self.runner_invoker is None:
            raise RuntimeError("No StepRunner invoker is configured for AgentTool execution.")
        capability = self._recordable_capability(tool_name)
        step = ExecutableStep(
            step_id=f"agent-{tool_name}-{next(self._counter)}",
            kind=capability.step_kind if capability is not None else "action",
            action_name=capability.name if capability is not None else tool_name,
            params=arguments,
            metadata=self._step_metadata(tool_name),
        )
        runner_response = await asyncio.to_thread(self.runner_invoker, self.run_id, step)
        return self._common_result_from_runner(tool_name, runner_response)

    def _step_metadata(self, tool_name: str) -> dict[str, Any]:
        capability = self._recordable_capability(tool_name)
        payload: dict[str, Any] = {
            "run_id": self.run_id,
            "tool_origin": "common" if capability is not None and capability.executor_kind == "common" else "agent_tool",
            "tool_name": tool_name,
            "capability_name": capability.name if capability is not None else tool_name,
            "executor_kind": capability.executor_kind if capability is not None else "common",
        }
        if capability is not None:
            payload.update(
                {
                    "replay": capability.replay.model_dump(mode="json") if capability.replay else None,
                    "sensitivity": capability.sensitivity,
                    "schema_metadata": capability.safe_metadata(),
                }
            )
        return payload

    def _common_result_from_runner(
        self,
        tool_name: str,
        runner_response: RunnerStepResult | tuple[RunnerStepResult, CapabilityExecutionResult | None],
    ) -> AgentToolResult:
        runner_result, capability_result = self._unpack_runner_response(runner_response)
        invoke_metadata = self._invoke_metadata(runner_result)
        output = capability_result.output if capability_result is not None else invoke_metadata.get("common_output")
        metadata = self._safe_runner_metadata(invoke_metadata, runner_result)
        if capability_result is not None:
            metadata.update(capability_result.metadata)
            if capability_result.replay is not None:
                metadata["replay"] = capability_result.replay.model_dump(mode="json")
            if capability_result.safe_replay_params:
                metadata["safe_replay_params"] = dict(capability_result.safe_replay_params)
        sensitive = bool((capability_result.sensitivity if capability_result is not None else False) or invoke_metadata.get("sensitivity") or invoke_metadata.get("sensitive"))
        metadata["runner_step_id"] = runner_result.step_id
        metadata["runner_result"] = runner_result.model_dump(mode="json")
        return AgentToolResult(
            tool_name=tool_name,
            status=self._common_status(runner_result),
            output=output,
            artifact_path=self._metadata_str(metadata, "artifact_path"),
            artifact_content_chars=self._metadata_int(metadata, "artifact_content_chars"),
            model_output=self._metadata_model_output(metadata),
            sensitive=sensitive,
            error=runner_result.error_message,
            duration_ms=(capability_result.duration_ms if capability_result is not None else 0) or runner_result.duration_ms,
            metadata=metadata,
        )

    def _unpack_runner_response(
        self,
        runner_response: RunnerStepResult | tuple[RunnerStepResult, CapabilityExecutionResult | None],
    ) -> tuple[RunnerStepResult, CapabilityExecutionResult | None]:
        if isinstance(runner_response, tuple):
            runner_result, capability_result = runner_response
            return runner_result, capability_result
        return runner_response, None

    def _invoke_metadata(self, runner_result: RunnerStepResult) -> dict[str, Any]:
        for phase_report in runner_result.phase_reports:
            if phase_report.phase == "invoke":
                return dict(phase_report.metadata)
        return {}

    def _safe_runner_metadata(self, metadata: dict[str, Any], runner_result: RunnerStepResult) -> dict[str, Any]:
        safe = {key: value for key, value in metadata.items() if key != "common_output"}
        runner_dump = runner_result.model_dump(mode="json")
        for phase_report in runner_dump.get("phase_reports", []):
            if isinstance(phase_report, dict):
                phase_metadata = phase_report.get("metadata")
                if isinstance(phase_metadata, dict):
                    phase_report["metadata"] = {key: value for key, value in phase_metadata.items() if key != "common_output"}
        safe["runner_result"] = runner_dump
        return safe

    def _common_status(self, runner_result: RunnerStepResult):
        if runner_result.status == "passed":
            return "success"
        if runner_result.status == "skipped":
            return "skipped"
        return "failed"

    def _metadata_str(self, metadata: dict[str, Any], key: str) -> str | None:
        value = metadata.get(key)
        return str(value) if value is not None else None

    def _metadata_int(self, metadata: dict[str, Any], key: str) -> int | None:
        value = metadata.get(key)
        return value if isinstance(value, int) else None

    def _metadata_model_output(self, metadata: dict[str, Any]):
        value = metadata.get("model_output")
        return value if value in {"full", "artifact_reference"} else "full"

    def _parse_args(self, args: str) -> dict[str, Any]:
        if not args:
            return {}
        payload = json.loads(args)
        if not isinstance(payload, dict):
            raise TypeError("AgentTool arguments must be a JSON object.")
        return payload

    def _format_tool_response(self, result: AgentToolResult) -> str:
        payload = result.model_dump(mode="json")
        metadata = self._response_metadata(result)
        if result.sensitive:
            return json.dumps(
                {
                    "tool_name": result.tool_name,
                    "model_output": "full",
                    "sensitive": True,
                    "artifact": self._artifact_payload(result),
                    "result": payload,
                    **metadata,
                },
                ensure_ascii=False,
                default=str,
            )
        full_output = json.dumps(payload, ensure_ascii=False, default=str)
        settings = self.local_tool_output_settings
        if len(full_output) <= settings.full_output_max_chars:
            return json.dumps(
                {
                    "tool_name": result.tool_name,
                    "model_output": "full",
                    "artifact": self._artifact_payload(result),
                    "result": payload,
                    **metadata,
                },
                ensure_ascii=False,
                default=str,
            )

        preview = full_output[: settings.historical_preview_chars]
        response = {
            "tool_name": result.tool_name,
            "model_output": settings.historical_output_mode,
            "artifact": self._artifact_payload(result),
            "preview": preview,
            "instructions": "Use search_artifact or read_artifact_slice with artifact.path when details beyond the preview are needed.",
            **metadata,
        }
        encoded = json.dumps(response, ensure_ascii=False, default=str)
        if len(encoded) <= settings.model_response_max_chars:
            return encoded
        preview_limit = len(preview)
        while preview_limit > 0:
            response["preview"] = preview[:preview_limit]
            encoded = json.dumps(response, ensure_ascii=False, default=str)
            if len(encoded) <= settings.model_response_max_chars:
                return encoded
            preview_limit //= 2
        response["preview"] = ""
        return json.dumps(response, ensure_ascii=False, default=str)

    def _artifact_payload(self, result: AgentToolResult) -> dict[str, Any]:
        return {
            "path": str(result.artifact_path) if result.artifact_path else None,
            "content_chars": result.artifact_content_chars,
        }

    def _response_metadata(self, result: AgentToolResult) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        for key in (
            "capability_name",
            "executor_kind",
            "replay",
            "sensitivity",
            "safe_replay_params",
            "runner_step_id",
            "runner_result",
            "duration_ms",
        ):
            if key in result.metadata:
                payload[key] = result.metadata[key]
        payload.setdefault("status", "passed" if result.status == "success" else result.status)
        if result.error:
            payload["error_message"] = result.error
        return payload

    def _redact_sensitive_result(self, payload: dict[str, Any]) -> dict[str, Any]:
        output = payload.get("output")
        if isinstance(output, dict) and "value" in output:
            redacted_output = dict(output)
            redacted_output["value"] = "***"
            payload = dict(payload)
            payload["output"] = redacted_output
        return payload

    def _redact_sensitive_response(self, output: str) -> str:
        try:
            payload = json.loads(output)
        except json.JSONDecodeError:
            return output
        if not isinstance(payload, dict):
            return output
        result = payload.get("result")
        if isinstance(result, dict):
            payload = dict(payload)
            payload["result"] = self._redact_sensitive_result(dict(result))
            return json.dumps(payload, ensure_ascii=False, default=str)
        return json.dumps(self._redact_sensitive_result(payload), ensure_ascii=False, default=str)

    async def _emit_tool_started(self, tool_name: str, arguments: dict[str, Any] | str) -> None:
        await self._emit(
            RunEvent(
                run_id=self.run_id,
                task_id=self.task_id,
                type="tool_call_started",
                title="Tool call started",
                message=f"Calling {tool_name}.",
                tool_name=tool_name,
                tool_arguments=self._redact(arguments),
                payload=self._event_payload(tool_name, arguments),
            )
        )

    async def _emit_tool_completed(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        output: str,
        started: float,
        result: AgentToolResult,
    ) -> None:
        preview_output = self._redact_sensitive_response(output) if result.sensitive else output
        payload = self._event_payload(tool_name, arguments, result=result)
        payload["artifact_path"] = str(result.artifact_path) if result.artifact_path else None
        await self._emit(
            RunEvent(
                run_id=self.run_id,
                task_id=self.task_id,
                type="tool_call_completed",
                title="Tool call completed",
                message=f"{tool_name} completed.",
                tool_name=tool_name,
                tool_output_preview=self._preview(preview_output),
                duration_ms=result.duration_ms or int((time.perf_counter() - started) * 1000),
                payload=payload,
            )
        )

    async def _emit_tool_failed(self, tool_name: str, error: str, started: float, arguments: dict[str, Any] | None = None) -> None:
        await self._emit(
            RunEvent(
                run_id=self.run_id,
                task_id=self.task_id,
                type="tool_call_failed",
                title="Tool call failed",
                message=error,
                tool_name=tool_name,
                duration_ms=int((time.perf_counter() - started) * 1000),
                payload=self._event_payload(tool_name, arguments or {}),
            )
        )

    def _event_payload(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        *,
        result: AgentToolResult | None = None,
    ) -> dict[str, Any]:
        capability = self._recordable_capability(tool_name)
        tool_origin = "common" if capability is not None and capability.executor_kind == "common" else "agent_tool"
        payload: dict[str, Any] = {
            "tool_origin": tool_origin,
            "capability_name": capability.name if capability is not None else tool_name,
            "executor_kind": capability.executor_kind if capability is not None else "agent_tool",
        }
        if capability is not None:
            payload.update(
                {
                    "sensitive": capability.sensitivity,
                    "replay": capability.replay.model_dump(mode="json") if capability.replay else None,
                    "metadata": capability.metadata,
                }
            )
        metadata = result.metadata if result is not None else {}
        if metadata:
            payload.update(metadata)
        runtime_secret_name = metadata.get("runtime_secret_name") or metadata.get("name") or arguments.get("name")
        if runtime_secret_name:
            payload["runtime_secret_name"] = str(runtime_secret_name)
        if "duration_ms" not in payload and "duration_ms" in arguments:
            payload["duration_ms"] = arguments.get("duration_ms")
        if "reason" not in payload and "reason" in arguments:
            payload["reason"] = arguments.get("reason")
        return payload

    async def _emit(self, event: RunEvent) -> None:
        if not self.event_sink or not self.run_id or not self.task_id:
            return
        result = self.event_sink(event)
        if inspect.isawaitable(result):
            await result

    def _preview(self, value: Any, limit: int = 1000) -> str:
        text = value if isinstance(value, str) else repr(value)
        text = text.replace("\r", " ").replace("\n", " ")
        return text if len(text) <= limit else f"{text[:limit]}..."

    def _redact(self, value: Any) -> Any:
        sensitive = ("token", "key", "secret", "password", "authorization", "cookie")
        if isinstance(value, dict):
            return {key: "***" if any(part in str(key).lower() for part in sensitive) else self._redact(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self._redact(item) for item in value]
        return value

    def _configure_provider_runs(self, run_id: str) -> None:
        for provider in self.registry.list_providers():
            if isinstance(provider, DefaultAgentToolProvider):
                provider.configure_run(run_id)

    def _recordable_capability(self, tool_name: str):
        capability_for = getattr(self.registry, "capability_for", None)
        if not callable(capability_for):
            return None
        return capability_for(tool_name)


AgentsCommonToolAdapter = AgentToolAdapter
