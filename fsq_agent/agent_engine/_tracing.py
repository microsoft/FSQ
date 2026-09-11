# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from __future__ import annotations

import asyncio
import logging
import os
import random
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import uuid4

import httpx

if TYPE_CHECKING:
    from collections.abc import Iterator

_ENDPOINT = "https://api.openai.com/v1/traces/ingest"
_MAX_QUEUE_SIZE = 8192
_MAX_BATCH_SIZE = 128
_MAX_RETRIES = 3
_BASE_DELAY = 1.0
_MAX_DELAY = 30.0
_FLUSH_TIMEOUT = 5.0
_logger = logging.getLogger(__name__)
_current_trace: ContextVar[TraceSession | None] = ContextVar("agent_engine_trace", default=None)
_current_span: ContextVar[str | None] = ContextVar("agent_engine_span", default=None)


class TraceSession:
    def __init__(self, name: str, *, enabled: bool) -> None:
        self.name = name
        self.enabled = enabled
        self.trace_id = f"trace_{uuid4().hex}" if enabled else ""
        self._items: list[dict] = []
        self._dropped = False

    async def __aenter__(self) -> TraceSession:
        self._trace_token = _current_trace.set(self if self.enabled else None)
        self._span_token = _current_span.set(None)
        if self.enabled:
            self.add({"object": "trace", "id": self.trace_id, "workflow_name": self.name, "group_id": None, "metadata": None})
        return self

    async def __aexit__(self, exc_type, exc_value, traceback) -> None:
        _current_span.reset(self._span_token)
        _current_trace.reset(self._trace_token)
        items, self._items = self._items, []
        if not items:
            return
        try:
            await asyncio.wait_for(_export(items), timeout=_FLUSH_TIMEOUT)
        except BaseException as error:
            if isinstance(error, asyncio.CancelledError | KeyboardInterrupt | SystemExit) and exc_type is None:
                raise
            _logger.warning("Trace export did not complete.")

    def add(self, item: dict) -> None:
        if len(self._items) < _MAX_QUEUE_SIZE:
            self._items.append(item)
        elif not self._dropped:
            self._dropped = True
            _logger.warning("Trace capacity reached; additional spans are omitted.")


@contextmanager
def span(kind: str, **data) -> Iterator[dict | None]:
    trace = _current_trace.get()
    if trace is None:
        yield None
        return
    item = {
        "object": "trace.span",
        "id": f"span_{uuid4().hex[:24]}",
        "trace_id": trace.trace_id,
        "parent_id": _current_span.get(),
        "started_at": datetime.now(UTC).isoformat(),
        "ended_at": None,
        "span_data": {"type": kind, **data},
        "error": None,
    }
    trace.add(item)
    token = _current_span.set(item["id"])
    try:
        yield item
    except BaseException:
        item["error"] = {"message": "Agent operation failed.", "data": None}
        raise
    finally:
        item["ended_at"] = datetime.now(UTC).isoformat()
        _current_span.reset(token)


async def _export(items: list[dict]) -> None:
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        return
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json", "OpenAI-Beta": "traces=v1"}
    for environment, header in (("OPENAI_ORG_ID", "OpenAI-Organization"), ("OPENAI_PROJECT_ID", "OpenAI-Project")):
        value = os.getenv(environment)
        if value:
            headers[header] = value
    sanitized = []
    for item in items:
        data = item.get("span_data")
        payload_item = item
        if isinstance(data, dict) and data.get("type") != "generation" and "usage" in data:
            payload_item = {**item, "span_data": {key: value for key, value in data.items() if key != "usage"}}
        sanitized.append(payload_item)
    async with httpx.AsyncClient(timeout=httpx.Timeout(60, connect=5)) as client:
        for offset in range(0, len(sanitized), _MAX_BATCH_SIZE):
            payload = {"data": sanitized[offset : offset + _MAX_BATCH_SIZE]}
            delay = _BASE_DELAY
            for attempt in range(_MAX_RETRIES):
                try:
                    response = await client.post(_ENDPOINT, headers=headers, json=payload)
                    if response.status_code < 300:
                        break
                    if 400 <= response.status_code < 500:
                        _logger.warning("Trace export was rejected.")
                        break
                except httpx.RequestError:
                    _logger.warning("Trace export request failed.")
                if attempt + 1 == _MAX_RETRIES:
                    _logger.warning("Trace export retry limit reached.")
                    break
                await asyncio.sleep(delay + random.SystemRandom().uniform(0, 0.1 * delay))
                delay = min(delay * 2, _MAX_DELAY)
