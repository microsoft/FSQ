# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import inspect
from typing import Any

from fsq_agent.models import RunEvent, RunEventSink
from fsq_agent.observation import ExecutionLogger


class RunEventEmitter:
    def __init__(self, logger: ExecutionLogger | None = None, sink: RunEventSink | None = None, *, secret_values: tuple[str, ...] = ()) -> None:
        self.logger = logger
        self.sink = sink
        self.sequence = 0
        self._secret_values = tuple(sorted({value for value in secret_values if value}, key=len, reverse=True))

    async def emit(self, event: RunEvent) -> None:
        self.sequence += 1
        safe_fields = {name: self._redact(getattr(event, name)) for name in ("task_id", "title", "message", "tool_arguments", "tool_output_preview", "payload")}
        sequenced = event.model_copy(update={**safe_fields, "sequence": self.sequence})
        if self.logger:
            self.logger.write_run_event(sequenced)
        if self.sink:
            result = self.sink(sequenced)
            if inspect.isawaitable(result):
                await result

    def _redact(self, value: Any) -> Any:
        if isinstance(value, str):
            for private_value in self._secret_values:
                value = value.replace(private_value, "***")
        elif isinstance(value, dict):
            return {name: self._redact(item) for name, item in value.items()}
        elif isinstance(value, list):
            return [self._redact(item) for item in value]
        return value
