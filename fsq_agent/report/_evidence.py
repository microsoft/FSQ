# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import json
from pathlib import Path

from fsq_agent.models import StepResult


class EvidenceBundler:
    def __init__(self, runs_dir: Path) -> None:
        self.runs_dir = runs_dir

    def create_manifest(self, run_id: str, steps: list[StepResult]) -> Path:
        path = self.runs_dir / run_id / "evidence-manifest.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        if (path.parent / "evidence-events.jsonl").is_file():
            return path
        if path.is_file():
            existing = json.loads(path.read_text(encoding="utf-8"))
            if existing.get("schema_version") == "fsq.evidence/v2":
                return path
        manifest = {
            "run_id": run_id,
            "steps": [
                {
                    "step_id": step.step_id,
                    "status": step.status,
                    "screenshot_path": str(step.screenshot_path) if step.screenshot_path else None,
                    "has_ui_tree": step.ui_tree_snapshot is not None,
                }
                for step in steps
            ],
        }
        path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
        return path
