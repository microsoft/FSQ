# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import asyncio
import json

import httpx
import pytest

from fsq_agent.agent_engine import _tracing


@pytest.mark.parametrize("statuses", [(200,), (400,), (500, 200), (500, 500, 500)])
async def test_trace_export_wire_and_nonfatal_failures(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, statuses: tuple[int, ...]) -> None:
    requests = []
    clients = []
    original_client = httpx.AsyncClient

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(statuses[len(requests) - 1], text="private exporter response")

    def make_client(**kwargs):
        client = original_client(transport=httpx.MockTransport(respond), **kwargs)
        clients.append(client)
        return client

    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-trace-key")
    monkeypatch.setenv("OPENAI_ORG_ID", "synthetic-org")
    monkeypatch.setenv("OPENAI_PROJECT_ID", "synthetic-project")
    monkeypatch.setattr(_tracing.httpx, "AsyncClient", make_client)
    monkeypatch.setattr(_tracing, "_BASE_DELAY", 0)
    async with _tracing.TraceSession("workflow", enabled=True):
        with _tracing.span("agent", name="agent", handoffs=[], tools=["echo"], output_type="str"):
            with _tracing.span("response", response_id=None, usage={"input_tokens": 1, "output_tokens": 2}):
                pass
            with _tracing.span("function", name="echo", input=None, output=None, mcp_data=None):
                pass
    assert len(requests) == len(statuses)
    assert all(client.is_closed for client in clients)
    assert all(str(request.url) == "https://api.openai.com/v1/traces/ingest" for request in requests)
    assert requests[0].headers["Authorization"] == "Bearer synthetic-trace-key"
    assert requests[0].headers["OpenAI-Beta"] == "traces=v1"
    assert requests[0].headers["OpenAI-Organization"] == "synthetic-org"
    assert requests[0].headers["OpenAI-Project"] == "synthetic-project"
    payload = json.loads(requests[0].content)["data"]
    trace = next(item for item in payload if item["object"] == "trace")
    spans = [item for item in payload if item["object"] == "trace.span"]
    assert trace["workflow_name"] == "workflow"
    assert trace["id"].startswith("trace_")
    assert len(spans) == 3
    assert all(item["trace_id"] == trace["id"] and item["ended_at"] for item in spans)
    agent_span = next(item for item in spans if item["span_data"]["type"] == "agent")
    assert all(item["parent_id"] == agent_span["id"] for item in spans if item is not agent_span)
    assert "usage" not in next(item["span_data"] for item in spans if item["span_data"]["type"] == "response")
    assert "private exporter response" not in caplog.text
    assert "synthetic-trace-key" not in caplog.text


async def test_trace_scope_isolation_and_owned_error_data(monkeypatch: pytest.MonkeyPatch) -> None:
    items = []

    async def capture(batch):
        items.extend(batch)

    monkeypatch.setattr(_tracing, "_export", capture)
    async with _tracing.TraceSession("caller", enabled=True):
        with _tracing.span("custom", name="caller") as caller:
            caller["error"] = {"message": "caller-owned error", "data": {"category": "caller"}}
            async with _tracing.TraceSession("silent", enabled=False):
                with _tracing.span("agent", name="silent"):
                    pass
            async with _tracing.TraceSession("nested", enabled=True):
                with _tracing.span("agent", name="nested"):
                    pass
            with _tracing.span("function", name="after"):
                pass
    assert [item["workflow_name"] for item in items if item["object"] == "trace"] == ["nested", "caller"]
    caller_span = next(item for item in items if item.get("span_data", {}).get("name") == "caller")
    after_span = next(item for item in items if item.get("span_data", {}).get("name") == "after")
    assert caller_span["error"]["message"] == "caller-owned error"
    assert after_span["parent_id"] == caller_span["id"]
    assert all(item.get("span_data", {}).get("name") != "silent" for item in items)


async def test_trace_flush_timeout_closes_transport(monkeypatch: pytest.MonkeyPatch) -> None:
    entered = asyncio.Event()
    cancelled = asyncio.Event()
    blocked = asyncio.Event()
    clients = []
    original_client = httpx.AsyncClient

    async def respond(request: httpx.Request) -> httpx.Response:
        entered.set()
        try:
            await blocked.wait()
        finally:
            cancelled.set()
        return httpx.Response(200)

    def make_client(**kwargs):
        client = original_client(transport=httpx.MockTransport(respond), **kwargs)
        clients.append(client)
        return client

    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-trace-key")
    monkeypatch.setattr(_tracing.httpx, "AsyncClient", make_client)
    monkeypatch.setattr(_tracing, "_FLUSH_TIMEOUT", 0.05)
    async with _tracing.TraceSession("bounded", enabled=True):
        with _tracing.span("agent", name="bounded"):
            pass
    assert entered.is_set()
    assert cancelled.is_set()
    assert clients
    assert all(client.is_closed for client in clients)


async def test_no_export_client_without_key_or_enabled_trace(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    def forbidden_client(**kwargs):
        raise AssertionError("Disabled or unconfigured trace must not create a client")

    monkeypatch.setattr(_tracing.httpx, "AsyncClient", forbidden_client)
    for enabled in (False, True):
        async with _tracing.TraceSession("disabled", enabled=enabled):
            with _tracing.span("agent", name="disabled"):
                pass
