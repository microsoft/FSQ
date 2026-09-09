# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import json

import httpx
from google import genai

from fsq_agent.models import AgentFinalOutput, MacOSUiSnapshotParams


async def test_google_client_transmits_native_fsq_contract_without_storage() -> None:
    requests: list[httpx.Request] = []
    output = AgentFinalOutput(status="success", summary="Done.")

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "id": "interaction_test",
                "model": "gemini-3.8-flash",
                "status": "completed",
                "steps": [{"type": "model_output", "content": [{"type": "text", "text": output.model_dump_json()}]}],
            },
        )

    transport = httpx.AsyncClient(transport=httpx.MockTransport(respond), follow_redirects=False)
    client = genai.Client(
        api_key="synthetic-google-key",
        enterprise=False,
        vertexai=False,
        http_options={
            "base_url": "https://generativelanguage.googleapis.com",
            "api_version": "v1beta",
            "httpx_async_client": transport,
            "retry_options": {"attempts": 1},
        },
    )
    try:
        response = await client.aio.interactions.create(
            model="gemini-3.8-flash",
            input=[{"type": "text", "text": "Inspect."}, {"type": "image", "data": "aW1hZ2U=", "mime_type": "image/png"}],
            store=False,
            tools=[{"type": "function", "name": "ui_snapshot", "parameters": MacOSUiSnapshotParams.model_json_schema()}],
            response_format={"type": "text", "mime_type": "application/json", "schema": AgentFinalOutput.model_json_schema()},
        )
        assert response.status == "completed"
        assert len(requests) == 1
        request = requests[0]
        assert str(request.url) == "https://generativelanguage.googleapis.com/v1beta/interactions"
        assert request.headers["x-goog-api-key"] == "synthetic-google-key"
        payload = json.loads(request.content)
        assert payload["store"] is False
        assert "previous_interaction_id" not in payload
        assert "background" not in payload
        assert payload["tools"][0]["parameters"] == MacOSUiSnapshotParams.model_json_schema()
        assert payload["response_format"]["schema"] == AgentFinalOutput.model_json_schema()
    finally:
        await client.aio.aclose()
        client.close()
        await transport.aclose()
    assert transport.is_closed
