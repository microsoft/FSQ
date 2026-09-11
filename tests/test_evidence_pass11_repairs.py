# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import hashlib
import json
from pathlib import Path

import pytest

from fsq_agent.adapters.control_plane._evidence import read_replay_frames
from fsq_agent.core import ArtifactStore, EvidenceRecorder
from fsq_agent.execution import RunLifecycleService
from fsq_agent.models import ReportGenerationError, RunnerEvent
from fsq_agent.report._run_report import sanitize_content


def test_comment_credentials_redacted_before_snapshot_and_export():
    text = "name: safe\n# Authorization: Bearer COMMENT_CANARY\n---\n- closeBrowser: {}\n"
    assert "COMMENT_CANARY" not in RunLifecycleService.safe_source_text(text)
    assert "COMMENT_CANARY" not in sanitize_content(text, format_kind="yaml")


def test_basic_auth_removed_from_core_artifacts_and_journal(tmp_path):
    ref = ArtifactStore(tmp_path).write_json(kind="ui_snapshot", step_id="s", phase="prepare", name="snapshot", payload={"snapshot": "Basic PRIVATE_CANARY"})
    assert "PRIVATE_CANARY" not in (tmp_path / ref.path).read_text()
    recorder = EvidenceRecorder(run_id="run", output_dir=tmp_path)
    recorder.record_event(RunnerEvent(run_id="run", event_type="session_start", payload={"message": "Basic PRIVATE_CANARY"}))
    assert "PRIVATE_CANARY" not in (tmp_path / "evidence-events.jsonl").read_text()


@pytest.mark.parametrize("key", ["config", "configuration"])
def test_structured_export_omits_private_config_maps(key):
    text = json.dumps({key: {"CUSTOM": "PRIVATE_CANARY"}})
    assert "PRIVATE_CANARY" not in sanitize_content(text, format_kind="json")


@pytest.mark.parametrize("availability", ["available", "failed"])
def test_replay_checks_recorded_availability_and_hash(tmp_path, availability):
    (tmp_path / "screen.png").write_bytes(b"changed")
    (tmp_path / "evidence-manifest.json").write_text(
        json.dumps({"artifacts": [{"kind": "screenshot", "path": "screen.png", "availability": availability, "sha256": hashlib.sha256(b"original").hexdigest(), "size_bytes": 8}]})
    )
    replay = read_replay_frames(tmp_path)
    assert replay["available"] is False
    assert replay["frames"][0]["error"]
    assert "contentBase64" not in replay["frames"][0]


def test_history_recovery_checks_size_before_read(tmp_path, monkeypatch):
    path = tmp_path / "evidence-events.jsonl"
    with path.open("wb") as stream:
        stream.truncate(33 * 1024 * 1024)
    reads = []
    monkeypatch.setattr(Path, "read_bytes", lambda *args: reads.append(True) or b"")
    with pytest.raises(ReportGenerationError, match="limit"):
        RunLifecycleService.read_evidence(tmp_path)
    assert not reads
