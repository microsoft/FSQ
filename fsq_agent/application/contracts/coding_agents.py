# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict


class CodingAgentInstallRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    project_directory: Path
    agent: Literal["codex"]
    workspace_root: Path
    no_overwrite: bool = False


class CodingAgentInstallFileResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    path: Path
    status: Literal["created", "updated", "unchanged", "skipped"]
    backup_path: Path | None = None


class CodingAgentInstallResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    agent: Literal["codex"]
    project_directory: Path
    workspace_root: Path
    files: tuple[CodingAgentInstallFileResult, ...]


__all__ = ["CodingAgentInstallFileResult", "CodingAgentInstallRequest", "CodingAgentInstallResult"]
