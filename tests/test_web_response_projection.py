# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import json
from copy import deepcopy

import pytest
from pydantic import BaseModel

from fsq_agent.adapters.coding_agent._harness_tools import HarnessToolAdapter
from fsq_agent.agent_engine import ToolCall, ToolInputFailure
from fsq_agent.models import EvidenceArtifactRef, HarnessFunctionSchema, LocalToolOutputSettings, RunnerStepResult, StepPhaseReport, WebObservation
from fsq_agent.tools import ToolArtifactStore


def _locator(selector="button.save"):
    return {
        "page": "main",
        "steps": [
            {"kind": "css", "selector": selector},
            {"kind": "filter", "visible": True},
            {"kind": "first"},
        ],
    }


def _observation(count=2, selector="button.save"):
    return {
        "page": "main",
        "url": "https://example.test/editor",
        "title": "Document editor",
        "coverage": {"semantic": "complete", "locators": "partial"},
        "regions": [
            {
                "role": "main",
                "name": "Editor",
                "locator": _locator("main"),
                "elements": [{"role": "button", "name": f"Save {index}", "enabled": True, "locator": _locator(selector if index == 0 else f"button.item-{index}")} for index in range(count)],
            },
        ],
    }


def _result(output, params=None, *, effect="completed", error=None):
    return RunnerStepResult(
        step_id="execution-1",
        source_step_id="source-1",
        step_execution_id="execution-1",
        action_name="click_on",
        status="failed" if error else "passed",
        action_status="passed",
        failure_category="artifact_error" if error else None,
        error_message=error,
        duration_ms=17,
        metadata={"action_effect": effect, "replay_unavailable_reason": "Replay preparation unavailable." if effect == "indeterminate" else None},
        phase_reports=[
            StepPhaseReport(
                step_id="execution-1",
                phase="invoke",
                status="passed",
                duration_ms=11,
                metadata={"safe_replay_params": params or {"target": _locator()}, "harness_output": output, "harness_metadata": {"action_effect": effect}},
                artifact_refs=[EvidenceArtifactRef(artifact_id="full-observation", kind="ui_snapshot", path="artifacts/observations/full.json")],
            ),
        ],
    )


class _Harness:
    def action_space(self):
        return [
            HarnessFunctionSchema(
                name="ui_snapshot",
                description="Observe the current page.",
                params_json_schema={"type": "object", "properties": {}, "additionalProperties": False},
                platform="web",
                driver_method="ui_snapshot",
                fsq_action_name="uiSnapshot",
                metadata={"step_kind": "observation", "executor_kind": "driver"},
            ),
        ]


def _adapter(monkeypatch, result, **kwargs):
    adapter = HarnessToolAdapter(_Harness(), run_id="projection-run", platform="web", **kwargs)
    monkeypatch.setattr(adapter.runner, "run_step", lambda **_: result)
    return adapter, adapter.build_tools()[0]


def _locators(value):
    if isinstance(value, dict):
        for key, item in value.items():
            if key == "locator" and isinstance(item, dict):
                yield item
            else:
                yield from _locators(item)
    elif isinstance(value, list):
        for item in value:
            yield from _locators(item)


@pytest.mark.parametrize("view", ["compact", "full"])
async def test_web_projection_bounds_complete_json_and_preserves_full_facts(monkeypatch, tmp_path, view):
    observation = _observation(500)
    output = {"observation": observation, "source": {"aria": "FULL SEMANTIC SOURCE\n" * 20000}}
    params = {"target": _locator(), "text": "AUTHORED INPUT" * 20000}
    result = _result(output, params)
    original = deepcopy(result.model_dump(mode="json"))
    full_results = []
    store = ToolArtifactStore(tmp_path, "projection-run", LocalToolOutputSettings())
    _, tool = _adapter(monkeypatch, result, artifact_store=store, on_full_result=lambda call_id, payload: full_results.append((call_id, payload)))

    text = await tool.invoke(ToolCall(name=tool.name, call_id="call-1", arguments={"view": view}))
    payload = json.loads(text)
    body = payload["result"]["output"]["observation"]

    assert len(text) <= 16000
    assert len(json.dumps(body, ensure_ascii=False)) <= 12000
    assert body["title"] == "Document editor"
    assert body["regions"][0]["name"] == "Editor"
    assert body["regions"][0]["elements"]
    assert body["coverage"] == observation["coverage"]
    assert payload["projection"]["omitted_items"] > 0
    assert "runner_result" not in payload
    assert "safe_replay_params" not in payload
    assert text.count('"observation":') == 1
    assert "FULL SEMANTIC SOURCE" not in text
    assert all(locator in list(_locators(observation)) for locator in _locators(body))
    assert full_results[0][0] == "call-1"
    assert full_results[0][1]["runner_result"] == original
    assert full_results[0][1]["safe_replay_params"] == params
    artifact = store.read_text(payload["artifact"]["path"])
    stored = json.loads(json.loads(artifact)["content"])
    assert stored["runner_result"] == original
    assert stored["result"]["output"] == output
    assert result.model_dump(mode="json") == original


@pytest.mark.parametrize("selector_size", [7000, 25000])
async def test_web_projection_emits_whole_locators_or_explicitly_omits_them(monkeypatch, selector_size):
    selector = '[data-precise="' + "x" * selector_size + '\\""]'
    observation = _observation(3, selector)
    _, tool = _adapter(monkeypatch, _result({"observation": observation}))

    text = await tool.invoke(ToolCall(name=tool.name, call_id="locator", arguments={}))
    payload = json.loads(text)
    emitted = list(_locators(payload))

    assert len(text) <= 16000
    assert all(locator in list(_locators(observation)) for locator in emitted)
    if selector_size == 7000:
        assert _locator(selector) in emitted
    else:
        assert _locator(selector) not in emitted
        assert payload["projection"]["omitted_locators"] > 0
        assert payload["artifact_refs"][0]["path"] == "artifacts/observations/full.json"
        assert "scope" in payload["projection"]["guidance"].lower()
    assert _locator("button.item-1") in emitted


async def test_web_projection_keeps_small_ordinary_output_useful_without_artifact_store(monkeypatch):
    observation = _observation()
    _, tool = _adapter(monkeypatch, _result({"observation": observation, "matched": 2}))
    text = await tool.invoke(ToolCall(name=tool.name, call_id="ordinary", arguments={}))
    payload = json.loads(text)

    assert payload["result"]["output"] == {"observation": observation, "matched": 2}
    assert payload["action_effect"] == "completed"
    assert payload["projection"]["omitted_items"] == 0
    assert payload["artifact"]["path"] is None
    assert payload["artifact"]["availability"] == "unavailable"
    assert len(text) < 16000


@pytest.mark.parametrize("effect", ["not_started", "completed", "indeterminate"])
async def test_web_projection_preserves_effects_despite_oversized_error(monkeypatch, effect):
    error = 'Observation failed: "quoted" error\n' * 20000
    full_results = []
    _, tool = _adapter(monkeypatch, _result({"observation": _observation(50)}, error=error, effect=effect), on_full_result=lambda _, payload: full_results.append(payload))
    text = await tool.invoke(ToolCall(name=tool.name, call_id="failure", arguments={}))
    payload = json.loads(text)

    assert len(text) <= 16000
    assert payload["status"] == "failed"
    assert payload["action_status"] == "passed"
    assert payload["action_effect"] == effect
    assert payload["failure_category"] == "artifact_error"
    assert payload["error_message"].startswith("Observation failed:")
    assert payload["projection"]["error_message_truncated"] is True
    assert full_results[0]["error_message"] == error
    assert full_results[0]["runner_result"]["error_message"] == error
    if effect == "indeterminate":
        assert payload["replay_unavailable_reason"] == "Replay preparation unavailable."


async def test_web_invalid_input_stays_bounded_and_continuable_without_invocation(monkeypatch):
    full_results = []
    adapter, tool = _adapter(monkeypatch, _result({}), on_full_result=lambda call_id, payload: full_results.append((call_id, payload)))

    def forbidden(**_):
        raise AssertionError("Invalid input must not execute a step.")

    monkeypatch.setattr(adapter.runner, "run_step", forbidden)
    error = "Expected a replayable locator, not a snapshot ref. " * 20000
    text = await tool.on_invalid_input(ToolInputFailure(name=tool.name, call_id="bad-input", message=error))
    payload = json.loads(text)

    assert tool.strict is True
    assert len(text) <= 16000
    assert payload["status"] == "failed"
    assert payload["action_effect"] == "not_started"
    assert payload["capability_name"] == "ui_snapshot"
    assert full_results[0][0] == "bad-input"
    assert full_results[0][1]["error_message"] == error
    assert "safe_replay_params" not in full_results[0][1]
    assert "replay_unavailable_reason" not in full_results[0][1]
    assert "runner_result" not in full_results[0][1]


async def test_web_optional_artifact_write_failure_does_not_reclassify_completed_effect(monkeypatch):
    class UnavailableStore:
        def write(self, *_args):
            raise OSError("Disk unavailable")

    full_results = []
    _, tool = _adapter(monkeypatch, _result({"observation": _observation()}), artifact_store=UnavailableStore(), on_full_result=lambda _, payload: full_results.append(payload))
    payload = json.loads(await tool.invoke(ToolCall(name=tool.name, call_id="unavailable", arguments={})))

    assert payload["status"] == "passed"
    assert payload["action_effect"] == "completed"
    assert payload["artifact"]["availability"] == "unavailable"
    assert full_results[0]["runner_result"]["action_status"] == "passed"


async def test_web_smaller_requested_observation_budget_is_respected(monkeypatch):
    _, tool = _adapter(monkeypatch, _result({"observation": _observation(200)}))
    payload = json.loads(await tool.invoke(ToolCall(name=tool.name, call_id="smaller", arguments={"max_chars": 3000})))
    assert len(json.dumps(payload["result"]["output"]["observation"], ensure_ascii=False)) <= 3000
    assert payload["result"]["output"]["observation"]["regions"]


@pytest.mark.parametrize("metadata_key", ["snapshot", "observation"])
async def test_web_action_reuses_complete_finalize_observation_without_recapture(monkeypatch, metadata_key):
    result = _result({"navigated_to": "https://example.test/editor"})
    before = _observation(1)
    before["title"] = "Old page"
    after = _observation(500)
    result.phase_reports.insert(
        0,
        StepPhaseReport(
            step_id=result.step_id,
            phase="prepare",
            status="passed",
            artifact_refs=[EvidenceArtifactRef(artifact_id="before", kind="ui_snapshot", path="artifacts/before.json", metadata={metadata_key: before})],
        ),
    )
    result.phase_reports.append(
        StepPhaseReport(
            step_id=result.step_id,
            phase="finalize",
            status="passed",
            artifact_refs=[EvidenceArtifactRef(artifact_id="after", kind="ui_snapshot", path="artifacts/after.json", metadata={metadata_key: after})],
        ),
    )
    _, tool = _adapter(monkeypatch, result)
    payload = json.loads(await tool.invoke(ToolCall(name=tool.name, call_id="reuse", arguments={})))

    assert payload["result"]["output"]["observation"]["title"] == "Document editor"
    assert payload["result"]["output"]["navigated_to"] == "https://example.test/editor"
    assert payload["result"]["output"]["observation"]["regions"][0]["elements"]
    assert any(ref["path"] == "artifacts/after.json" for ref in payload["artifact_refs"])
    assert all("metadata" not in ref for ref in payload["artifact_refs"])
    assert result.phase_reports[-1].artifact_refs[0].metadata[metadata_key] == after
    assert len(json.dumps(payload, ensure_ascii=False)) <= 16000


async def test_web_action_prefers_snapshot_and_retains_separate_artifact_coverage(monkeypatch):
    result = _result({"changed": True})
    snapshot = _observation()
    coverage = snapshot.pop("coverage")
    legacy = _observation()
    legacy["title"] = "Older observation"
    result.phase_reports.append(
        StepPhaseReport(
            step_id=result.step_id,
            phase="finalize",
            status="passed",
            artifact_refs=[
                EvidenceArtifactRef(
                    artifact_id="after",
                    kind="ui_snapshot",
                    path="artifacts/after.json",
                    metadata={"snapshot": snapshot, "coverage": coverage, "observation": legacy},
                ),
            ],
        ),
    )
    _, tool = _adapter(monkeypatch, result)
    payload = json.loads(await tool.invoke(ToolCall(name=tool.name, call_id="snapshot-coverage", arguments={})))

    assert payload["result"]["output"]["observation"]["title"] == "Document editor"
    assert payload["result"]["output"]["observation"]["coverage"] == coverage
    assert "coverage" not in result.phase_reports[-1].artifact_refs[0].metadata["snapshot"]


async def test_web_projection_handles_escaped_unicode_and_large_envelope(monkeypatch, tmp_path):
    precise = _locator('button[data-name="精确\\selector"]')
    observation = _observation(200)
    observation["regions"][0]["elements"][0]["locator"] = precise
    result = _result({"observation": observation}, {"target": precise, "text": "x" * 500000}, error='\x00\n\\"错误' * 100000)
    result.metadata["replay_unavailable_reason"] = "diagnostic\n" * 20000
    result.phase_reports[0].artifact_refs.extend(
        EvidenceArtifactRef(artifact_id=f"extra-{index}", kind="ui_snapshot", path=f"artifacts/extra-{index}.json", metadata={"verbose": "x" * 1000}) for index in range(100)
    )
    store = ToolArtifactStore(tmp_path, "projection-run", LocalToolOutputSettings())
    _, tool = _adapter(monkeypatch, result, artifact_store=store)
    text = await tool.invoke(ToolCall(name=tool.name, call_id="escaped", arguments={"view": "full"}))
    payload = json.loads(text)

    assert len(text) <= 16000
    assert len(json.dumps(payload["result"]["output"], ensure_ascii=False)) <= 12000
    assert payload["action_effect"] == "completed"
    assert precise in list(_locators(payload))
    assert payload["artifact"]["availability"] == "available"
    assert payload["projection"]["omitted_items"] > 0
    assert payload["projection"]["error_message_truncated"] is True


async def test_web_projection_preserves_relative_query_and_authored_ordering(monkeypatch):
    rule = {
        "page": "main",
        "steps": [
            {"kind": "role", "role": "listitem"},
            {"kind": "filter", "has": {"steps": [{"kind": "role", "role": "button", "name": "Add to cart", "disabled": False, "exact": True}]}},
            {"kind": "first"},
            {"kind": "role", "role": "button", "name": "Add to cart", "exact": True},
        ],
    }
    observation = _observation(200)
    observation["regions"][0]["elements"][0]["locator"] = rule
    _, tool = _adapter(monkeypatch, _result({"observation": observation}))
    payload = json.loads(await tool.invoke(ToolCall(name=tool.name, call_id="ordered", arguments={})))

    assert payload["result"]["output"]["observation"]["regions"][0]["elements"][0]["locator"] == rule


async def test_web_projection_keeps_actionable_validation_diagnostics(monkeypatch):
    result = _result(None, error="Invalid Web parameters.")
    result.phase_reports[0].metadata["harness_metadata"] = {
        "error_code": "invalid_locator",
        "parameter_path": "target.steps",
        "validation_errors": [{"loc": ["target", "steps"], "type": "too_short", "msg": "Supply at least one locator step."}],
        "unrelated_large_metadata": "unrelated" * 100000,
    }
    _, tool = _adapter(monkeypatch, result)
    text = await tool.invoke(ToolCall(name=tool.name, call_id="validation", arguments={}))
    payload = json.loads(text)

    assert payload["diagnostics"]["error_code"] == "invalid_locator"
    assert payload["diagnostics"]["parameter_path"] == "target.steps"
    assert payload["diagnostics"]["validation_errors"][0]["loc"] == ["target", "steps"]
    assert payload["diagnostics"]["validation_errors"][0]["msg"] == "Supply at least one locator step."
    assert len(text) <= 16000


async def test_web_projection_supports_typed_observation_without_stringifying_it(monkeypatch):
    class ObservationOutput(BaseModel):
        observation: dict
        source: dict

    output = ObservationOutput(observation=_observation(400), source={"aria": "Full source" * 10000})
    _, tool = _adapter(monkeypatch, _result(output))
    payload = json.loads(await tool.invoke(ToolCall(name=tool.name, call_id="typed", arguments={})))

    assert payload["result"]["output"]["observation"]["title"] == "Document editor"
    assert payload["result"]["output"]["observation"]["regions"][0]["elements"]
    assert len(json.dumps(payload, ensure_ascii=False)) <= 16000


@pytest.mark.parametrize("duration", [0, None])
async def test_web_projection_does_not_replace_measured_zero_or_unknown_duration(monkeypatch, duration):
    result = _result({"observation": _observation()})
    result.duration_ms = duration
    full_results = []
    _, tool = _adapter(monkeypatch, result, on_full_result=lambda _, payload: full_results.append(payload))
    payload = json.loads(await tool.invoke(ToolCall(name=tool.name, call_id="timing", arguments={})))

    assert payload["duration_ms"] == duration
    assert full_results[0]["duration_ms"] == duration


@pytest.mark.parametrize("source_size", [1, 10000])
async def test_final_web_observation_shape_keeps_full_source_out_of_inline_response(monkeypatch, source_size):
    view = {"elements": [{"role": "button", "name": "Save", "locator": _locator()}]}
    coverage = {"semantic": "complete", "locators": "partial"}
    observation = WebObservation(
        observation_id="typed-observation",
        page={"page": "main", "url": "https://example.test/editor", "title": "Editor"},
        view=view,
        coverage=coverage,
        full_artifact_ref="artifacts/full-observation.json",
        full_source={"semantic_text": "FULL SOURCE EVIDENCE " * source_size, "view": view, "coverage": coverage},
    )
    full_results = []
    _, tool = _adapter(monkeypatch, _result(observation), on_full_result=lambda _, payload: full_results.append(payload))
    text = await tool.invoke(ToolCall(name=tool.name, call_id="typed-full-source", arguments={"view": "full"}))
    payload = json.loads(text)

    assert payload["result"]["output"]["page"]["title"] == "Editor"
    assert payload["result"]["output"]["view"]["elements"][0]["locator"] == observation.view.elements[0].locator.model_dump(mode="json")
    assert payload["result"]["output"]["full_artifact_ref"] == "artifacts/full-observation.json"
    assert "full_source" not in payload["result"]["output"]
    assert "FULL SOURCE EVIDENCE" not in text
    assert full_results[0]["runner_result"]["phase_reports"][0]["metadata"]["harness_output"]["full_source"] == observation.full_source.model_dump(mode="json")
    assert len(text) <= 16000


async def test_web_full_facts_preserve_effect_diagnostics_without_new_replay_sentinel(monkeypatch):
    result = _result({"observation": _observation()})
    result.phase_reports[0].metadata.update(
        safe_replay_params=None,
        action_effect="indeterminate",
        replay_unavailable_reason="Canonical replay parameters could not be prepared.",
    )
    full_results = []
    _, tool = _adapter(monkeypatch, result, on_full_result=lambda _, payload: full_results.append(payload))
    payload = json.loads(await tool.invoke(ToolCall(name=tool.name, call_id="replay-unavailable", arguments={"target": _locator()})))

    assert "safe_replay_params" not in payload
    assert "safe_replay_params" not in full_results[0]
    assert full_results[0]["action_effect"] == "indeterminate"
    assert payload["action_effect"] == "indeterminate"
    assert payload["replay_unavailable_reason"] == "Canonical replay parameters could not be prepared."


@pytest.mark.parametrize("reason_size", [1, 1000])
@pytest.mark.parametrize("include_null_locator", [False, True])
async def test_web_projection_keeps_locator_unavailability_complete_or_omits_the_element(monkeypatch, reason_size, include_null_locator):
    observation = _observation()
    reason = "No reliable locator is available. " * reason_size
    description = {"role": "text", "name": "Information", "locator_unavailable_reason": reason}
    if include_null_locator:
        description["locator"] = None
    observation["regions"][0]["elements"].insert(0, description)
    _, tool = _adapter(monkeypatch, _result({"observation": observation}))
    payload = json.loads(await tool.invoke(ToolCall(name=tool.name, call_id="unavailable-element", arguments={})))
    elements = payload["result"]["output"]["observation"]["regions"][0]["elements"]
    descriptions = [element for element in elements if element.get("name") == "Information"]

    if reason_size == 1:
        assert descriptions[0].get("locator") is None
        assert descriptions[0]["locator_unavailable_reason"] == reason
    else:
        assert descriptions == []
        assert payload["projection"]["omitted_items"] > 0
    assert any(element.get("locator") == _locator() for element in elements)


async def test_web_projection_never_emits_a_partial_observation_continuation(monkeypatch):
    continuation = {"observation_id": "source-observation", "cursor": "opaque-token" * 5000}
    output = {"observation": _observation(), "continuation": continuation}
    full_results = []
    _, tool = _adapter(monkeypatch, _result(output), on_full_result=lambda _, payload: full_results.append(payload))
    payload = json.loads(await tool.invoke(ToolCall(name=tool.name, call_id="continuation", arguments={})))

    assert "continuation" not in payload["result"]["output"]
    assert payload["result"]["output"]["observation"]["regions"]
    assert full_results[0]["result"]["output"]["continuation"] == continuation
