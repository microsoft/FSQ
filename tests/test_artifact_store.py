# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import json
from pathlib import Path

from fsq_agent.core import ArtifactStore


def test_artifact_store_writes_json_artifact_with_relative_ref(tmp_path: Path) -> None:
    store = ArtifactStore(run_dir=tmp_path)

    ref = store.write_json(
        kind="ui_tree",
        step_id="step-1",
        phase="finalize",
        name="UI Tree",
        payload={"nodes": [{"text": "Login"}]},
    )

    expected_path = tmp_path / ref.path
    assert expected_path.exists()
    assert json.loads(expected_path.read_text(encoding="utf-8")) == {"nodes": [{"text": "Login"}]}
    assert "step-1-finalize-ui-tree" in ref.artifact_id
    assert ref.kind == "ui_tree"
    assert ref.path.parent == Path("artifacts/ui-trees")
    assert ref.mime_type == "application/json"
    assert ref.step_id == "step-1"
    assert ref.phase == "finalize"


def test_ui_snapshot_body_is_not_duplicated_in_artifact_metadata(tmp_path: Path) -> None:
    store = ArtifactStore(run_dir=tmp_path)
    payload = {
        "snapshot": '- document\n  - button "Add to Cart"',
        "coverage": {"status": "complete"},
        "truncated": False,
        "compaction": {"version": "v1"},
        "clipped": False,
    }

    ref = store.write_json(
        kind="ui_snapshot",
        step_id="step-1",
        phase="prepare",
        name="UI Snapshot",
        payload=payload,
    )

    assert json.loads((tmp_path / ref.path).read_text(encoding="utf-8")) == payload
    assert "snapshot" not in ref.metadata
    assert ref.metadata["coverage"] == payload["coverage"]
    assert ref.metadata["truncated"] is False
    assert ref.metadata["compaction"] == payload["compaction"]
    assert ref.metadata["clipped"] is False


def test_artifact_store_writes_text_log_artifact(tmp_path: Path) -> None:
    store = ArtifactStore(run_dir=tmp_path)

    ref = store.write_text(
        kind="log",
        step_id="step-1",
        phase="invoke",
        name="Driver Log",
        text="tap started\ntap finished\n",
    )

    expected_path = tmp_path / ref.path
    assert expected_path.read_text(encoding="utf-8") == "tap started\ntap finished\n"
    assert "step-1-invoke-driver-log" in ref.artifact_id
    assert ref.kind == "log"
    assert ref.path.parent == Path("artifacts/logs")
    assert ref.mime_type == "text/plain"


def test_artifact_store_writes_screenshot_bytes_artifact(tmp_path: Path) -> None:
    store = ArtifactStore(run_dir=tmp_path)

    ref = store.write_bytes(
        kind="screenshot",
        step_id="step-1",
        phase="finalize",
        name="Screen",
        data=b"fake-png",
    )

    expected_path = tmp_path / ref.path
    assert expected_path.read_bytes() == b"fake-png"
    assert "step-1-finalize-screen" in ref.artifact_id
    assert ref.kind == "screenshot"
    assert ref.path.parent == Path("artifacts/screenshots")
    assert ref.mime_type == "image/png"
