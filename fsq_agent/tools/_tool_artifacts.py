# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import json
import re
from pathlib import Path
from typing import Any

from fsq_agent.models import LocalToolOutputSettings, ToolExecutionError


class ToolArtifactStore:
    def __init__(self, runs_dir: Path, run_id: str, settings: LocalToolOutputSettings) -> None:
        self.runs_dir = runs_dir
        self.run_id = run_id
        self.settings = settings
        self.call_index = 0

    @property
    def root(self) -> Path:
        return self.runs_dir / self.run_id / self.settings.artifact_subdir

    def write(self, tool_name: str, content: str, metadata: dict[str, Any] | None = None) -> Path | None:
        if not self.settings.artifact_enabled or not self.settings.always_write_artifact:
            return None
        self.root.mkdir(parents=True, exist_ok=True)
        safe_tool_name = re.sub(r"[^A-Za-z0-9_.-]+", "-", tool_name).strip("-._") or "tool"
        while True:
            self.call_index += 1
            path = self.root / f"{self.call_index:06d}-{safe_tool_name}.json"
            payload = {
                "tool_name": tool_name,
                "run_id": self.run_id,
                "call_index": self.call_index,
                "content_chars": len(content),
                "metadata": metadata or {},
                "content": content,
            }
            encoded = json.dumps(payload, ensure_ascii=False, indent=2)
            try:
                with path.open("x", encoding="utf-8") as artifact:
                    artifact.write(encoded)
            except FileExistsError:
                continue
            return path

    def read_text(self, artifact_path: str) -> str:
        path = self._resolve(artifact_path)
        return path.read_text(encoding="utf-8")

    def read_slice(self, artifact_path: str, offset: int, length: int) -> dict[str, Any]:
        text = self.read_text(artifact_path)
        safe_offset = min(max(offset, 0), len(text))
        safe_length = max(length, 1)
        end = min(safe_offset + safe_length, len(text))
        return {
            "artifact_path": str(self._resolve(artifact_path)),
            "offset": safe_offset,
            "length": end - safe_offset,
            "total_chars": len(text),
            "content": text[safe_offset:end],
            "has_more_before": safe_offset > 0,
            "has_more_after": end < len(text),
        }

    def search(self, artifact_path: str, query: str, case_sensitive: bool, max_matches: int, context_chars: int) -> dict[str, Any]:
        if not query:
            raise ToolExecutionError("Artifact search query cannot be empty.")
        text = self.read_text(artifact_path)
        haystack = text if case_sensitive else text.lower()
        needle = query if case_sensitive else query.lower()
        matches: list[dict[str, Any]] = []
        start = 0
        while len(matches) < max_matches:
            index = haystack.find(needle, start)
            if index < 0:
                break
            context_start = max(0, index - context_chars)
            context_end = min(len(text), index + len(query) + context_chars)
            matches.append(
                {
                    "offset": index,
                    "context_start": context_start,
                    "context_end": context_end,
                    "preview": text[context_start:context_end],
                }
            )
            start = index + max(len(needle), 1)
        return {
            "artifact_path": str(self._resolve(artifact_path)),
            "query": query,
            "total_chars": len(text),
            "matches": matches,
            "truncated": len(matches) >= max_matches and haystack.find(needle, start) >= 0,
        }

    def _resolve(self, artifact_path: str) -> Path:
        path = Path(artifact_path)
        if not path.is_absolute():
            path = self.runs_dir / self.run_id / path
        path = path.resolve()
        run_root = (self.runs_dir / self.run_id).resolve()
        if run_root != path and run_root not in path.parents:
            raise ToolExecutionError("Artifact path must stay inside the current run directory.")
        if not path.exists() or not path.is_file():
            raise ToolExecutionError(f"Artifact not found: {artifact_path}")
        return path
