# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import json
import subprocess
import sys
import textwrap
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from fsq_agent.adapters.control_plane import _execution

_PROVIDER_SHUTDOWN_PROBE = textwrap.dedent("""\
    import asyncio
    import gc
    import json
    import sys
    import warnings
    from types import SimpleNamespace
    from unittest.mock import patch

    import httpx

    from fsq_agent.adapters.control_plane import _execution
    from fsq_agent.agent_engine import ModelRequest, create_google_gemini_model_provider, create_model_provider

    backend, outcome = sys.argv[1:]
    diagnostics = []
    transports = []
    facts = {"requests": 0, "terminal": None, "transports_closed": False}
    handle = _execution.ExecutionHandle(request_id="offline-lifecycle")

    async def send(client, request, **kwargs):
        transports.append(client)
        facts["requests"] += 1
        if backend == "gemini":
            payload = {
                "id": "interaction_lifecycle", "status": "completed", "model": "gemini-3.5-flash",
                "steps": [{"type": "model_output", "content": [{"type": "text", "text": "done"}]}],
            }
        else:
            payload = {
                "id": "resp_lifecycle", "created_at": 0, "object": "response",
                "model": "gpt-5", "status": "completed",
                "output": [{"id": "msg_lifecycle", "type": "message", "role": "assistant", "status": "completed",
                            "content": [{"type": "output_text", "text": "done", "annotations": []}]}],
            }
        return httpx.Response(200, request=request, json=payload)

    def finish(request_id, *, status, summary):
        assert request_id == handle.request_id
        facts["terminal"] = status

    async def explore(prepared, state):
        loop = asyncio.get_running_loop()
        loop.set_exception_handler(lambda loop, context: diagnostics.append(context["message"]))
        if backend == "gemini":
            provider = create_google_gemini_model_provider(
                base_url="https://generativelanguage.googleapis.com/v1beta/", api_key="offline-placeholder"
            )
            model_name = "gemini-3.5-flash"
        else:
            provider = create_model_provider(base_url="https://api.openai.com/v1/", api_key="offline-placeholder")
            model_name = "gpt-5"
        try:
            result = await provider.get_model(model_name).complete(ModelRequest(input="Check lifecycle"))
            assert result.text == "done"
            if outcome == "cancelled":
                loop.call_soon(handle.cancel)
                await asyncio.Future()
            if outcome == "failure":
                raise RuntimeError("offline failure")
            state.finish(prepared.request_id, status="success", summary="done")
        finally:
            await provider.aclose()
            await provider.aclose()
            facts["transports_closed"] = bool(transports) and all(client.is_closed for client in transports)
            transports.clear()
            gc.collect()

    with warnings.catch_warnings(record=True) as recorded:
        warnings.simplefilter("always", RuntimeWarning)
        with patch.object(_execution, "_run_explore", explore), patch.object(httpx.AsyncClient, "send", send):
            try:
                _execution._execution_thread(
                    SimpleNamespace(mode="explore", request_id=handle.request_id), SimpleNamespace(finish=finish), handle
                )
            except RuntimeError as error:
                assert outcome == "failure" and str(error) == "offline failure"
                facts["terminal"] = "failure"
        gc.collect()
        facts["warnings"] = [str(item.message) for item in recorded if issubclass(item.category, RuntimeWarning)]
    facts["diagnostics"] = diagnostics
    facts["loop_closed"] = handle._loop.is_closed()
    facts["pending_tasks"] = [task.get_coro().__qualname__ for task in asyncio.all_tasks(handle._loop)]
    print(json.dumps(facts))
""")


@pytest.mark.parametrize("backend", ["gemini", "openai"])
@pytest.mark.parametrize("outcome", ["success", "failure", "cancelled"])
def test_explore_thread_closes_provider_event_loop(backend: str, outcome: str) -> None:
    result = subprocess.run(  # noqa: S603 - Fixed probe and parametrized values with the current test interpreter.
        [sys.executable, "-c", _PROVIDER_SHUTDOWN_PROBE, backend, outcome],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    assert json.loads(result.stdout) == {
        "requests": 1,
        "terminal": outcome,
        "transports_closed": True,
        "warnings": [],
        "diagnostics": [],
        "loop_closed": True,
        "pending_tasks": [],
    }


@pytest.mark.parametrize("cancel", [False, True])
async def test_explore_thread_finalizes_owned_resources_only(monkeypatch: pytest.MonkeyPatch, cancel: bool) -> None:
    import asyncio

    ready = threading.Event()
    release_executor = threading.Event()
    facts = {}
    diagnostics = []
    generators = []
    tasks = []
    caller_loop = asyncio.get_running_loop()
    caller_task = asyncio.create_task(asyncio.Event().wait())

    async def explore(prepared, state):
        loop = asyncio.get_running_loop()
        loop.set_exception_handler(lambda loop, context: diagnostics.append(context["message"]))
        background_started = asyncio.Event()

        async def checkpoint():
            resumed = loop.create_future()
            loop.call_soon(resumed.set_result, None)
            await resumed

        async def background():
            try:
                background_started.set()
                await asyncio.Event().wait()
            finally:
                await checkpoint()
                facts["task_finalized"] = True

        async def resource():
            try:
                yield "resource"
            finally:
                await checkpoint()
                facts["generator_finalized"] = True
                release_executor.set()

        def executor_work():
            facts["executor_released"] = release_executor.wait(5)

        generator = resource()
        generators.append(generator)
        assert await anext(generator) == "resource"
        loop.run_in_executor(None, executor_work)
        tasks.append(loop.create_task(background()))
        await background_started.wait()
        ready.set()
        if cancel:
            await asyncio.Event().wait()
        state.finish(prepared.request_id, status="success", summary="done")

    def finish(request_id, *, status, summary):
        assert request_id == "resource-lifecycle"
        facts["terminal"] = status

    monkeypatch.setattr(_execution, "_run_explore", explore)
    handle = _execution.start_execution(SimpleNamespace(mode="explore", request_id="resource-lifecycle"), SimpleNamespace(finish=finish))
    try:
        assert await asyncio.to_thread(ready.wait, 5)
        if cancel:
            handle.cancel()
        await asyncio.to_thread(handle.thread.join, 5)
        assert not handle.thread.is_alive()
        assert handle._loop.is_closed()
        assert not asyncio.all_tasks(handle._loop)
        assert tasks
        assert all(task.cancelled() for task in tasks)
        assert generators
        assert all(generator.ag_frame is None for generator in generators)
        assert facts == {
            "terminal": "cancelled" if cancel else "success",
            "task_finalized": True,
            "generator_finalized": True,
            "executor_released": True,
        }
        assert diagnostics == []
        assert asyncio.get_running_loop() is caller_loop
        assert not caller_task.done()
    finally:
        release_executor.set()
        handle.cancel()
        await asyncio.to_thread(handle.thread.join, 5)
        caller_task.cancel()
        await asyncio.gather(caller_task, return_exceptions=True)
