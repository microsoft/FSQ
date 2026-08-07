# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from __future__ import annotations

from pathlib import Path

from fsq_agent.config import read_env_values, upsert_env_values_atomic
from fsq_agent.models import EnvironmentFileUpdate


EnvFileUpdate = EnvironmentFileUpdate


def upsert_env_values(path: Path, values: dict[str, str]) -> EnvironmentFileUpdate:
    # Provider setup keeps its historical no-backup behavior; doctor repairs request backups.
    return upsert_env_values_atomic(path, values, backup=False)


__all__ = ["EnvFileUpdate", "read_env_values", "upsert_env_values"]