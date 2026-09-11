# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import json
from pathlib import Path

import pytest
import yaml

from fsq_agent.config._settings import Settings
from fsq_agent.execution import RecordingService
from fsq_agent.models import AndroidHarnessSettings, ConfigurationError, HarnessSettings, OutputSettings, ReportArtifact, RunEvent, Task, TaskResult, VerificationResult


def _write_event(path: Path, event: RunEvent) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(event.model_dump_json() + "\n")


def test_renamed_save_retains_both_digests_outside_candidate_yaml(tmp_path):
    import hashlib

    from fsq_agent.application import CaseSaveRequest, save_recorded_case

    run = tmp_path / "origin"
    run.mkdir()
    candidate = run / "recorded.fsq.yaml"
    candidate.write_text("schemaVersion: fsq.ai-test/v1\nname: original\nplatform: web\n---\n- waitMs:\n    duration_ms: 1\n")
    mapping = [{"command_index": 0, "source_step_id": "original-step", "step_execution_id": "executed-step", "invocation_path": ["agent", "1"]}]
    recording = run / "recording.json"
    recording.write_text(json.dumps({"source_run_id": "origin", "command_mapping": mapping, "draft": False}))
    before = candidate.read_bytes(), recording.read_bytes()
    request = CaseSaveRequest(candidate_path=candidate, destination_directory=tmp_path / "cases", platform="web", case_name="renamed")
    saved = save_recorded_case(request)
    assert saved.outcome == "created"
    relation = json.loads((run / "lineage.jsonl").read_text().splitlines()[-1])
    assert relation["candidate_digest"] == hashlib.sha256(before[0]).hexdigest()
    assert relation["case_digest"] == hashlib.sha256(saved.path.read_bytes()).hexdigest()
    assert relation["candidate_digest"] != relation["case_digest"]
    assert (run / relation["saved_snapshot_path"]).read_bytes() == saved.path.read_bytes()
    assert relation["command_mapping"] == mapping
    assert (candidate.read_bytes(), recording.read_bytes()) == before
    assert save_recorded_case(request).outcome == "unchanged"
    assert len((run / "lineage.jsonl").read_text().splitlines()) == 2


def _record_with_service(**kwargs):
    return RecordingService().record(**kwargs)


async def test_explore_renamed_save_and_real_strict_lifecycle_keep_execution_lineage(tmp_path):
    from fsq_agent._capability_bootstrap import build_capability_registry
    from fsq_agent.application import CaseSaveRequest, save_recorded_case
    from fsq_agent.case_dsl import FsqCaseLoader
    from fsq_agent.core import ArtifactStore, EvidenceRecorder, HarnessFactory, StepRunner
    from fsq_agent.execution import DynamicExecutionRequest, DynamicExecutionService
    from fsq_agent.execution.lifecycle import run_strict_lifecycle_case
    from fsq_agent.models import DynamicAgentOutcome, ExecutableStep, PostActionDelaySettings, RunReportExportOptions
    from fsq_agent.report import RunReportService

    settings = Settings(harness=HarnessSettings(platform="web"))
    settings.workspace.root_dir = tmp_path
    settings.output.runs_dir = tmp_path / ".fsq/runs/web"
    settings.cases.dir = tmp_path / "cases/web"
    registry = build_capability_registry(platform="web")

    def harness(run_dir):
        return HarnessFactory().create_harness(platform="web", harness_settings=settings.harness, artifact_store=ArtifactStore(run_dir))

    class Agent:
        async def run_in_context(self, task, context, event_sink=None, *, evidence_sink, cancellation_check=None):
            gateway = harness(context.run_dir)
            try:
                runner = StepRunner(gateway, capability_registry=registry, evidence_sink=evidence_sink, post_action_delay_seconds=PostActionDelaySettings(platform=0, common=0))
                observed = runner.run_step(context.run_id, ExecutableStep(step_id="wait", kind="action", action_name="waitMs", params={"duration_ms": 1}))
                assert observed.status == "passed"
                return DynamicAgentOutcome(task=task, steps=[], verification=VerificationResult(status="success", summary="Wait completed."))
            finally:
                gateway.close()

    explored = await DynamicExecutionService(agent=Agent()).execute(
        DynamicExecutionRequest(task=Task(description="Wait", planning_reference_kind="goal", planning_reference_text="Wait"), settings=settings, record=True)
    )
    assert explored.recording.status == "recorded"
    candidate = explored.recording.recorded_case_path
    before = candidate.read_bytes()
    saved = save_recorded_case(CaseSaveRequest(candidate_path=candidate, destination_directory=settings.cases.dir, platform="web", case_name="renamed-wait"))
    strict_dir = settings.output.runs_dir / "strict-renamed"
    gateway = harness(strict_dir)
    try:
        run_strict_lifecycle_case(
            case_path=saved.path,
            case=FsqCaseLoader().load_case(saved.path),
            settings=settings,
            harness=gateway,
            output_dir=strict_dir,
            run_id=strict_dir.name,
            registry=registry,
            registry_snapshot=registry.snapshot(),
            resolve_steps=lambda steps, _: steps,
            post_action_delay_seconds=PostActionDelaySettings(platform=0, common=0),
        )
    finally:
        gateway.close()
    service = RunReportService()
    origin = service.project(candidate.parent, normalized_evidence=EvidenceRecorder.recover_bundle(candidate.parent))
    combined = service.project(strict_dir, normalized_evidence=EvidenceRecorder.recover_bundle(strict_dir), baseline=origin, related_runs=[origin])
    assert combined.comparison["baseline_current"]["steps"][0]["status"] == "matched"
    assert combined.lineage["related_runs"][0]["run"]["run_id"] == candidate.parent.name
    assert service.export(combined, RunReportExportOptions(format="json", destination=tmp_path / "lineage.json")).path.is_file()
    assert candidate.read_bytes() == before


def _recordable_web_run(
    tmp_path: Path,
    *,
    planning_reference_kind: str = "goal",
    status: str = "success",
    replay_alias: str = "clickOn",
) -> tuple[Path, Task, TaskResult, Settings]:
    run_id = "goal-recording-run"
    run_dir = tmp_path / "runs" / run_id
    run_dir.mkdir(parents=True)
    task = Task(
        id="task-1",
        name="Search",
        description="Search the web",
        planning_reference_kind=planning_reference_kind,
        planning_reference_text="Search the web",
    )
    result = TaskResult(
        task_id=task.id,
        status=status,
        steps=[],
        verification=VerificationResult(status=status, summary=status),
        report=ReportArtifact(run_id=run_id, path=run_dir / "report.md"),
    )
    output_settings = OutputSettings()
    output_settings.runs_dir = tmp_path / "runs"
    settings = Settings(output=output_settings, harness=HarnessSettings(platform="web"))
    settings.cases.dir = tmp_path / "workspace" / "cases" / "web"
    events_path = run_dir / "events.jsonl"
    _write_event(
        events_path,
        RunEvent(
            run_id=run_id,
            task_id=task.id,
            type="tool_call_started",
            title="Tool call started",
            tool_name="click_on",
            tool_call_id="call-1",
            tool_arguments={"target": {"page": "main", "steps": [{"kind": "role", "role": "button", "name": "Search"}]}},
            payload={"tool_origin": "platform", "capability_name": "click_on", "replay": {"kind": "fsq_command", "alias": replay_alias}},
        ),
    )
    _write_event(
        events_path,
        RunEvent(
            run_id=run_id,
            task_id=task.id,
            type="tool_call_completed",
            title="Tool call completed",
            tool_name="click_on",
            tool_call_id="call-1",
            payload={
                "tool_origin": "platform",
                "capability_name": "click_on",
                "replay": {"kind": "fsq_command", "alias": replay_alias},
                "status": "passed",
            },
        ),
    )
    return run_dir, task, result, settings


def test_record_dynamic_run_writes_strict_yaml_with_runtime_secret_and_wait(tmp_path: Path) -> None:
    run_dir = tmp_path / "runs" / "recorded-run"
    run_dir.mkdir(parents=True)
    events_path = run_dir / "events.jsonl"
    task = Task(id="task-1", name="Login", description="Log in")
    result = TaskResult(
        task_id="task-1",
        status="success",
        steps=[],
        verification=VerificationResult(status="success", summary="ok"),
        report=ReportArtifact(run_id="recorded-run", path=run_dir / "report.md"),
    )
    output_settings = OutputSettings()
    output_settings.runs_dir = tmp_path / "runs"
    android_settings = AndroidHarnessSettings()
    android_settings.app_id = "com.example"
    settings = Settings(output=output_settings, harness=HarnessSettings(android=android_settings))

    _write_event(
        events_path,
        RunEvent(
            run_id="recorded-run",
            task_id="task-1",
            type="tool_call_started",
            title="Tool call started",
            tool_name="input_text",
            tool_call_id="call-1",
            tool_arguments={"text": "TEST_ACCOUNT_PASSWORD", "textType": "runtimeSecret", "target": "Password field"},
            payload={"tool_origin": "platform", "capability_name": "input_text", "replay": {"kind": "fsq_command", "alias": "inputText"}},
        ),
    )
    _write_event(
        events_path,
        RunEvent(
            run_id="recorded-run",
            task_id="task-1",
            type="tool_call_completed",
            title="Tool call completed",
            tool_name="input_text",
            tool_call_id="call-1",
            payload={"tool_origin": "platform", "capability_name": "input_text", "replay": {"kind": "fsq_command", "alias": "inputText"}, "status": "passed"},
        ),
    )
    _write_event(
        events_path,
        RunEvent(
            run_id="recorded-run",
            task_id="task-1",
            type="tool_call_completed",
            title="Tool call completed",
            tool_name="wait_ms",
            payload={"tool_origin": "common", "replay": {"kind": "fsq_command", "alias": "waitMs"}, "duration_ms": 1, "reason": "settle"},
        ),
    )

    recording = _record_with_service(run_dir=run_dir, task=task, result=result, settings=settings)

    assert recording.status == "recorded"
    assert recording.validation_status == "passed"
    docs = list(yaml.safe_load_all((run_dir / "recorded.fsq.yaml").read_text(encoding="utf-8")))
    assert json.loads((run_dir / "recording.json").read_text())["required_runtime_secret_names"] == ["TEST_ACCOUNT_PASSWORD"]
    assert docs[1] == [
        {"inputText": {"text": "TEST_ACCOUNT_PASSWORD", "textType": "runtimeSecret", "target": "Password field"}},
        {"waitMs": {"duration_ms": 1, "reason": "settle"}},
    ]
    manifest = json.loads((run_dir / "recording.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "recorded"
    assert manifest["command_count"] == 2


def test_record_dynamic_web_run_validates_against_web_registry(tmp_path: Path) -> None:
    run_dir = tmp_path / "runs" / "recorded-web-run"
    run_dir.mkdir(parents=True)
    events_path = run_dir / "events.jsonl"
    task = Task(id="task-1", name="Search", description="Search the web")
    result = TaskResult(
        task_id="task-1",
        status="success",
        steps=[],
        verification=VerificationResult(status="success", summary="ok"),
        report=ReportArtifact(run_id="recorded-web-run", path=run_dir / "report.md"),
    )
    output_settings = OutputSettings()
    output_settings.runs_dir = tmp_path / "runs"
    settings = Settings(output=output_settings, harness=HarnessSettings(platform="web"))

    _write_event(
        events_path,
        RunEvent(
            run_id="recorded-web-run",
            task_id="task-1",
            type="tool_call_started",
            title="Tool call started",
            tool_name="click_on",
            tool_call_id="call-1",
            tool_arguments={"target": {"page": "main", "steps": [{"kind": "role", "role": "button", "name": "Search"}]}},
            payload={"tool_origin": "platform", "capability_name": "click_on", "replay": {"kind": "fsq_command", "alias": "clickOn"}},
        ),
    )
    _write_event(
        events_path,
        RunEvent(
            run_id="recorded-web-run",
            task_id="task-1",
            type="tool_call_completed",
            title="Tool call completed",
            tool_name="click_on",
            tool_call_id="call-1",
            payload={"tool_origin": "platform", "capability_name": "click_on", "replay": {"kind": "fsq_command", "alias": "clickOn"}, "status": "passed"},
        ),
    )

    recording = _record_with_service(run_dir=run_dir, task=task, result=result, settings=settings)

    assert recording.status == "recorded"
    assert recording.validation_status == "passed"
    docs = list(yaml.safe_load_all((run_dir / "recorded.fsq.yaml").read_text(encoding="utf-8")))
    assert docs[0]["platform"] == "web"
    assert "appId" not in docs[0]
    from fsq_agent.models import WebClickOnParams

    expected = WebClickOnParams(target={"page": "main", "steps": [{"kind": "role", "role": "button", "name": "Search"}]}).model_dump(mode="json", exclude_none=True)
    assert docs[1] == [{"clickOn": expected}]


def test_record_dynamic_run_does_not_infer_replay_from_fsq_action_name(tmp_path: Path) -> None:
    run_dir = tmp_path / "runs" / "recorded-missing-replay-run"
    run_dir.mkdir(parents=True)
    events_path = run_dir / "events.jsonl"
    task = Task(id="task-1", name="Tap", description="Tap a target")
    result = TaskResult(
        task_id="task-1",
        status="success",
        steps=[],
        verification=VerificationResult(status="success", summary="ok"),
        report=ReportArtifact(run_id="recorded-missing-replay-run", path=run_dir / "report.md"),
    )
    output_settings = OutputSettings()
    output_settings.runs_dir = tmp_path / "runs"
    android_settings = AndroidHarnessSettings()
    android_settings.app_id = "com.example"
    settings = Settings(output=output_settings, harness=HarnessSettings(android=android_settings))

    _write_event(
        events_path,
        RunEvent(
            run_id="recorded-missing-replay-run",
            task_id="task-1",
            type="tool_call_started",
            title="Tool call started",
            tool_name="tap_on",
            tool_call_id="call-1",
            tool_arguments={"target": "Login"},
            payload={"tool_origin": "platform", "capability_name": "tap_on", "fsq_action_name": "tapOn"},
        ),
    )
    _write_event(
        events_path,
        RunEvent(
            run_id="recorded-missing-replay-run",
            task_id="task-1",
            type="tool_call_completed",
            title="Tool call completed",
            tool_name="tap_on",
            tool_call_id="call-1",
            payload={"tool_origin": "platform", "capability_name": "tap_on", "fsq_action_name": "tapOn", "status": "passed"},
        ),
    )

    recording = _record_with_service(run_dir=run_dir, task=task, result=result, settings=settings)

    assert recording.status == "failed"
    assert not (run_dir / "recorded.fsq.yaml").exists()
    assert recording.skipped_tool_calls == ({"tool_name": "tap_on", "reason": "platform tool did not include fsq_command replay metadata"},)


def test_record_dynamic_run_skips_observation_capabilities(tmp_path: Path) -> None:
    run_dir = tmp_path / "runs" / "recorded-observation-run"
    run_dir.mkdir(parents=True)
    events_path = run_dir / "events.jsonl"
    task = Task(id="task-1", name="Tap and observe", description="Tap then inspect UI tree")
    result = TaskResult(
        task_id="task-1",
        status="success",
        steps=[],
        verification=VerificationResult(status="success", summary="ok"),
        report=ReportArtifact(run_id="recorded-observation-run", path=run_dir / "report.md"),
    )
    output_settings = OutputSettings()
    output_settings.runs_dir = tmp_path / "runs"
    android_settings = AndroidHarnessSettings()
    android_settings.app_id = "com.example"
    settings = Settings(output=output_settings, harness=HarnessSettings(android=android_settings))

    _write_event(
        events_path,
        RunEvent(
            run_id="recorded-observation-run",
            task_id="task-1",
            type="tool_call_started",
            title="Tool call started",
            tool_name="tap_on",
            tool_call_id="call-1",
            tool_arguments={"target": "Login"},
            payload={
                "tool_origin": "platform",
                "capability_name": "tap_on",
                "step_kind": "action",
                "replay": {"kind": "fsq_command", "alias": "tapOn"},
            },
        ),
    )
    _write_event(
        events_path,
        RunEvent(
            run_id="recorded-observation-run",
            task_id="task-1",
            type="tool_call_completed",
            title="Tool call completed",
            tool_name="tap_on",
            tool_call_id="call-1",
            payload={
                "tool_origin": "platform",
                "capability_name": "tap_on",
                "step_kind": "action",
                "replay": {"kind": "fsq_command", "alias": "tapOn"},
                "status": "passed",
            },
        ),
    )
    _write_event(
        events_path,
        RunEvent(
            run_id="recorded-observation-run",
            task_id="task-1",
            type="tool_call_started",
            title="Tool call started",
            tool_name="ui_snapshot",
            tool_call_id="call-2",
            tool_arguments={},
            payload={
                "tool_origin": "platform",
                "capability_name": "ui_snapshot",
                "step_kind": "observation",
                "replay": {"kind": "fsq_command", "alias": "uiTree"},
            },
        ),
    )
    _write_event(
        events_path,
        RunEvent(
            run_id="recorded-observation-run",
            task_id="task-1",
            type="tool_call_completed",
            title="Tool call completed",
            tool_name="ui_snapshot",
            tool_call_id="call-2",
            payload={
                "tool_origin": "platform",
                "capability_name": "ui_snapshot",
                "step_kind": "observation",
                "replay": {"kind": "fsq_command", "alias": "uiTree"},
                "status": "passed",
            },
        ),
    )

    recording = _record_with_service(run_dir=run_dir, task=task, result=result, settings=settings)

    assert recording.status == "recorded"
    docs = list(yaml.safe_load_all((run_dir / "recorded.fsq.yaml").read_text(encoding="utf-8")))
    assert list(recording.warnings) == []
    assert docs[1] == [{"tapOn": {"target": "Login"}}]
    assert recording.skipped_tool_calls == ({"tool_name": "ui_snapshot", "reason": "observation tool is not recorded"},)
    manifest = json.loads((run_dir / "recording.json").read_text(encoding="utf-8"))
    assert manifest["warnings"] == []
    assert manifest["skipped_tool_calls"] == [{"tool_name": "ui_snapshot", "reason": "observation tool is not recorded"}]


def test_record_dynamic_android_tap_at_includes_reference_screen_size(tmp_path: Path) -> None:
    run_dir = tmp_path / "runs" / "recorded-tap-at-run"
    run_dir.mkdir(parents=True)
    events_path = run_dir / "events.jsonl"
    task = Task(id="task-1", name="Tap coordinate", description="Tap a coordinate")
    result = TaskResult(
        task_id="task-1",
        status="success",
        steps=[],
        verification=VerificationResult(status="success", summary="ok"),
        report=ReportArtifact(run_id="recorded-tap-at-run", path=run_dir / "report.md"),
    )
    output_settings = OutputSettings()
    output_settings.runs_dir = tmp_path / "runs"
    android_settings = AndroidHarnessSettings()
    android_settings.app_id = "com.example"
    settings = Settings(output=output_settings, harness=HarnessSettings(android=android_settings))

    _write_event(
        events_path,
        RunEvent(
            run_id="recorded-tap-at-run",
            task_id="task-1",
            type="tool_call_started",
            title="Tool call started",
            tool_name="tap_at",
            tool_call_id="call-1",
            tool_arguments={"point": {"x": 100, "y": 200}},
            payload={"tool_origin": "platform", "capability_name": "tap_at", "replay": {"kind": "fsq_command", "alias": "tapAt"}},
        ),
    )
    _write_event(
        events_path,
        RunEvent(
            run_id="recorded-tap-at-run",
            task_id="task-1",
            type="tool_call_completed",
            title="Tool call completed",
            tool_name="tap_at",
            tool_call_id="call-1",
            payload={
                "tool_origin": "platform",
                "capability_name": "tap_at",
                "replay": {"kind": "fsq_command", "alias": "tapAt"},
                "status": "passed",
                "safe_replay_params": {
                    "point": {"x": 100, "y": 200},
                    "reference_screen_size": {"width": 1080, "height": 2400},
                },
            },
        ),
    )

    recording = _record_with_service(run_dir=run_dir, task=task, result=result, settings=settings)

    assert recording.status == "recorded"
    assert recording.validation_status == "passed"
    docs = list(yaml.safe_load_all((run_dir / "recorded.fsq.yaml").read_text(encoding="utf-8")))
    assert docs[1] == [
        {
            "tapAt": {
                "point": {"x": 100, "y": 200},
                "reference_screen_size": {"width": 1080, "height": 2400},
            }
        }
    ]


def test_record_dynamic_android_point_swipe_includes_reference_screen_size(tmp_path: Path) -> None:
    run_dir = tmp_path / "runs" / "recorded-swipe-run"
    run_dir.mkdir(parents=True)
    events_path = run_dir / "events.jsonl"
    task = Task(id="task-1", name="Swipe coordinate", description="Swipe by coordinates")
    result = TaskResult(
        task_id="task-1",
        status="success",
        steps=[],
        verification=VerificationResult(status="success", summary="ok"),
        report=ReportArtifact(run_id="recorded-swipe-run", path=run_dir / "report.md"),
    )
    output_settings = OutputSettings()
    output_settings.runs_dir = tmp_path / "runs"
    android_settings = AndroidHarnessSettings()
    android_settings.app_id = "com.example"
    settings = Settings(output=output_settings, harness=HarnessSettings(android=android_settings))

    _write_event(
        events_path,
        RunEvent(
            run_id="recorded-swipe-run",
            task_id="task-1",
            type="tool_call_started",
            title="Tool call started",
            tool_name="swipe",
            tool_call_id="call-1",
            tool_arguments={"start": {"x": 800, "y": 1900}, "end": {"x": 200, "y": 1900}, "duration": 1000},
            payload={"tool_origin": "platform", "capability_name": "swipe", "replay": {"kind": "fsq_command", "alias": "swipe"}},
        ),
    )
    _write_event(
        events_path,
        RunEvent(
            run_id="recorded-swipe-run",
            task_id="task-1",
            type="tool_call_completed",
            title="Tool call completed",
            tool_name="swipe",
            tool_call_id="call-1",
            payload={
                "tool_origin": "platform",
                "capability_name": "swipe",
                "replay": {"kind": "fsq_command", "alias": "swipe"},
                "status": "passed",
                "safe_replay_params": {
                    "start": {"x": 800, "y": 1900},
                    "end": {"x": 200, "y": 1900},
                    "duration": 1000,
                    "reference_screen_size": {"width": 1080, "height": 2400},
                },
            },
        ),
    )

    recording = _record_with_service(run_dir=run_dir, task=task, result=result, settings=settings)

    assert recording.status == "recorded"
    assert recording.validation_status == "passed"
    docs = list(yaml.safe_load_all((run_dir / "recorded.fsq.yaml").read_text(encoding="utf-8")))
    assert docs[1] == [
        {
            "swipe": {
                "start": {"x": 800, "y": 1900},
                "end": {"x": 200, "y": 1900},
                "duration": 1000,
                "reference_screen_size": {"width": 1080, "height": 2400},
            }
        }
    ]


def test_record_dynamic_goal_publishes_validated_case_to_platform_cases_dir(tmp_path: Path) -> None:
    run_dir, task, result, settings = _recordable_web_run(tmp_path)

    recording = _record_with_service(
        run_dir=run_dir,
        task=task,
        result=result,
        settings=settings,
        publication_directory=settings.cases.dir,
    )

    expected_path = settings.cases.dir / "case-5c6a5d06b26b468e9b799c1cfb18d9a54c183fcb368408f798977784af48a8d0.fsq.yaml"
    assert recording.status == "recorded"
    assert recording.validation_status == "passed"
    assert recording.published_case_path == expected_path
    assert expected_path.read_bytes() == (run_dir / "recorded.fsq.yaml").read_bytes()
    docs = list(yaml.safe_load_all(expected_path.read_text(encoding="utf-8")))
    assert docs[0]["name"] == recording.case_name
    assert docs[0]["description"] == "Search the web"
    manifest = json.loads((run_dir / "recording.json").read_text(encoding="utf-8"))
    assert manifest["published_case_path"] == str(expected_path)
    assert manifest["warnings"] == []


def test_record_dynamic_goal_keeps_validated_case_run_local_when_publication_is_disabled(tmp_path: Path) -> None:
    run_dir, task, result, settings = _recordable_web_run(tmp_path)

    recording = _record_with_service(
        run_dir=run_dir,
        task=task,
        result=result,
        settings=settings,
        publication_directory=None,
    )

    assert recording.status == "recorded"
    assert recording.validation_status == "passed"
    assert recording.recorded_case_path == run_dir / "recorded.fsq.yaml"
    assert recording.published_case_path is None
    assert recording.recorded_case_path.is_file()
    assert not settings.cases.dir.exists()
    manifest = json.loads((run_dir / "recording.json").read_text(encoding="utf-8"))
    assert manifest["published_case_path"] is None


def test_record_dynamic_goal_preserves_conflicting_case_with_valid_draft(tmp_path: Path) -> None:
    run_dir, task, result, settings = _recordable_web_run(tmp_path, status="failed")
    settings.cases.dir.mkdir(parents=True)
    published = settings.cases.dir / "stable.fsq.yaml"
    published.write_text("existing")
    recording = _record_with_service(run_dir=run_dir, task=task, result=result, settings=settings, allow_failure=True, case_name="stable", publication_directory=settings.cases.dir)
    assert recording.status == "recorded"
    assert recording.draft is True
    assert recording.publication_outcome == "conflict"
    assert recording.published_case_path is None
    assert published.read_text() == "existing"
    assert recording.recorded_case_path.is_file()


@pytest.mark.parametrize("planning_reference_text", [None, "   "])
def test_record_dynamic_goal_falls_back_to_task_name_when_reference_is_blank(
    tmp_path: Path,
    planning_reference_text: str | None,
) -> None:
    run_dir, task, result, settings = _recordable_web_run(tmp_path)
    task = task.model_copy(update={"name": "Fallback goal", "planning_reference_text": planning_reference_text})

    recording = _record_with_service(run_dir=run_dir, task=task, result=result, settings=settings)

    assert recording.status == "recorded"
    docs = list(yaml.safe_load_all((run_dir / "recorded.fsq.yaml").read_text(encoding="utf-8")))
    assert docs[0]["name"] == recording.case_name
    assert docs[0]["description"] == "Fallback goal"


def test_record_dynamic_goal_normalizes_nonblank_reference(tmp_path: Path) -> None:
    run_dir, task, result, settings = _recordable_web_run(tmp_path)
    task = task.model_copy(update={"planning_reference_text": "  Search   the\nweb  "})

    recording = _record_with_service(run_dir=run_dir, task=task, result=result, settings=settings)

    assert recording.status == "recorded"
    docs = list(yaml.safe_load_all((run_dir / "recorded.fsq.yaml").read_text(encoding="utf-8")))
    assert docs[0]["description"] == "Search the web"


def test_record_dynamic_raw_case_does_not_publish(tmp_path: Path) -> None:
    run_dir, task, result, settings = _recordable_web_run(tmp_path, planning_reference_kind="raw_case")

    recording = _record_with_service(
        run_dir=run_dir,
        task=task,
        result=result,
        settings=settings,
        publication_directory=settings.cases.dir,
    )

    assert recording.status == "recorded"
    assert recording.published_case_path is None
    assert not settings.cases.dir.exists()
    docs = list(yaml.safe_load_all((run_dir / "recorded.fsq.yaml").read_text(encoding="utf-8")))
    assert docs[0]["name"] == recording.case_name
    assert docs[0]["description"] == task.description
    manifest = json.loads((run_dir / "recording.json").read_text(encoding="utf-8"))
    assert manifest["published_case_path"] is None


def test_record_dynamic_goal_does_not_publish_when_generated_case_validation_fails(tmp_path: Path, monkeypatch) -> None:
    run_dir, task, result, settings = _recordable_web_run(tmp_path)

    def fail_validation(*_args, **_kwargs):
        raise ConfigurationError("invalid generated case")

    monkeypatch.setattr("fsq_agent.execution.recording.FsqCaseSerializer.serialize", fail_validation)

    recording = _record_with_service(
        run_dir=run_dir,
        task=task,
        result=result,
        settings=settings,
        publication_directory=settings.cases.dir,
    )

    assert recording.status == "failed"
    assert recording.validation_status == "failed"
    assert recording.published_case_path is None
    assert not settings.cases.dir.exists()


def test_record_dynamic_goal_publication_failure_preserves_recording_and_existing_case(tmp_path: Path, monkeypatch) -> None:
    run_dir, task, result, settings = _recordable_web_run(tmp_path)
    published_path = settings.cases.dir / "case-5c6a5d06b26b468e9b799c1cfb18d9a54c183fcb368408f798977784af48a8d0.fsq.yaml"
    published_path.parent.mkdir(parents=True)
    published_path = published_path.with_name("unrelated.fsq.yaml")
    published_path.write_text("existing", encoding="utf-8")

    import os

    original_replace = os.replace

    def fail_replace(_source: Path, _destination: Path) -> None:
        if Path(_destination).parent == settings.cases.dir:
            raise OSError("replace failed")
        original_replace(_source, _destination)

    monkeypatch.setattr("fsq_agent.execution.recording.os.link", fail_replace)

    recording = _record_with_service(
        run_dir=run_dir,
        task=task,
        result=result,
        settings=settings,
        publication_directory=settings.cases.dir,
    )

    assert recording.status == "recorded"
    assert recording.validation_status == "passed"
    assert recording.published_case_path is None
    assert published_path.read_text(encoding="utf-8") == "existing"
    assert list(settings.cases.dir.iterdir()) == [published_path]
    assert any("publication" in warning.lower() for warning in recording.warnings)
    assert recording.publication_status == "failed"
    assert recording.publication_errors
    manifest = json.loads((run_dir / "recording.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "recorded"
    assert manifest["validation_status"] == "passed"
    assert manifest["published_case_path"] is None
    assert manifest["warnings"] == list(recording.warnings)


def test_same_actions_across_runs_have_identical_case_bytes(tmp_path: Path) -> None:
    first_dir, task, result, settings = _recordable_web_run(tmp_path)
    first = _record_with_service(run_dir=first_dir, task=task, result=result, settings=settings)
    second_dir = first_dir.with_name("different-run")
    second_dir.mkdir()
    (second_dir / "events.jsonl").write_bytes((first_dir / "events.jsonl").read_bytes().replace(b"goal-recording-run", b"different-run"))
    second_result = result.model_copy(update={"report": result.report.model_copy(update={"run_id": "different-run"})})
    second = _record_with_service(run_dir=second_dir, task=task.model_copy(update={"id": "other-task"}), result=second_result, settings=settings)
    assert first.recorded_case_path.read_bytes() == second.recorded_case_path.read_bytes()
    assert first.case_name == second.case_name
    assert "source_run_id" not in first.recorded_case_path.read_text()
    assert json.loads(second.recording_path.read_text())["source_run_id"] == "different-run"


def test_publication_rejection_preserves_candidate_and_metadata(tmp_path: Path) -> None:
    run_dir, task, result, settings = _recordable_web_run(tmp_path)
    settings.cases.dir.mkdir(parents=True)
    outside = tmp_path / "outside.fsq.yaml"
    outside.write_text("untouched")
    (settings.cases.dir / "stable.fsq.yaml").symlink_to(outside)
    recording = _record_with_service(run_dir=run_dir, task=task, result=result, settings=settings, case_name="stable", publication_directory=settings.cases.dir)
    assert recording.status == "recorded"
    assert recording.recorded_case_path.is_file()
    assert recording.publication_outcome == "failed"
    assert json.loads(recording.recording_path.read_text())["publication_outcome"] == "failed"
    assert outside.read_text() == "untouched"
