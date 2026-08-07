# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from __future__ import annotations

from datetime import datetime, timezone
import os
from pathlib import Path
import shutil
import tempfile
from typing import Mapping

from fsq_agent.models import ConfigurationError, EnvironmentFileUpdate


def read_env_values(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    text = _read_text(path)
    values: dict[str, str] = {}
    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        key, value = _split_assignment(raw_line, path=path, line_number=line_number)
        if key is not None:
            values[key] = _strip_env_value(value)
    return values


def upsert_env_values_atomic(
    path: Path,
    values: Mapping[str, str],
    *,
    backup: bool = True,
) -> EnvironmentFileUpdate:
    _validate_updates(values)
    existing = _read_text(path) if path.exists() else ""
    # Validate before creating backups or temporary files.
    for line_number, raw_line in enumerate(existing.splitlines(), start=1):
        _split_assignment(raw_line, path=path, line_number=line_number)

    line_ending = "\r\n" if "\r\n" in existing else "\n"
    lines = existing.splitlines()
    output: list[str] = []
    written: set[str] = set()
    for raw_line in lines:
        key, _ = _split_assignment(raw_line, path=path, line_number=0)
        if key in values:
            if key not in written:
                output.append(f"{key}={values[key]}")
                written.add(key)
            continue
        output.append(raw_line)
    for key, value in values.items():
        if key not in written:
            output.append(f"{key}={value}")
            written.add(key)
    rendered = line_ending.join(output) + (line_ending if output else "")

    backup_path: Path | None = None
    if backup and path.exists():
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        for suffix in range(1000):
            discriminator = "" if suffix == 0 else f".{suffix}"
            candidate = path.with_name(f"{path.name}.{stamp}{discriminator}.bak")
            try:
                with candidate.open("xb") as destination, path.open("rb") as source:
                    shutil.copyfileobj(source, destination)
                shutil.copystat(path, candidate)
            except FileExistsError:
                continue
            backup_path = candidate
            break
        if backup_path is None:
            raise ConfigurationError("Unable to allocate a unique environment backup path.", context={"path": str(path)})
        _owner_only_best_effort(backup_path)

    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent, text=True)
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as stream:
            stream.write(rendered)
            stream.flush()
            os.fsync(stream.fileno())
        if path.exists():
            shutil.copymode(path, temp_path)
        os.replace(temp_path, path)
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise
    return EnvironmentFileUpdate(
        path=path,
        keys=tuple(values),
        backup_path=backup_path,
        line_ending=line_ending,
    )


def _read_text(path: Path) -> str:
    try:
        with path.open("r", encoding="utf-8", newline="") as stream:
            return stream.read()
    except (OSError, UnicodeError) as exc:
        raise ConfigurationError("Unable to read .env file.", context={"path": str(path)}) from exc


def _split_assignment(raw_line: str, *, path: Path, line_number: int) -> tuple[str | None, str]:
    stripped = raw_line.strip()
    if not stripped or stripped.startswith("#"):
        return None, ""
    if stripped.startswith("export "):
        stripped = stripped[len("export ") :].strip()
    if "=" not in stripped:
        raise ConfigurationError(
            "Invalid .env line; expected KEY=VALUE.",
            context={"path": str(path), "line": line_number},
        )
    key, value = stripped.split("=", 1)
    key = key.strip()
    if not key:
        raise ConfigurationError(
            "Invalid .env line; key cannot be empty.",
            context={"path": str(path), "line": line_number},
        )
    return key, value.strip()


def _strip_env_value(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    return value


def _validate_updates(values: Mapping[str, str]) -> None:
    for key, value in values.items():
        if not key or any(char.isspace() for char in key) or "=" in key:
            raise ConfigurationError("Invalid environment variable name.", context={"key": key})
        if "\n" in value or "\r" in value:
            raise ConfigurationError("Environment variable value must be one line.", context={"key": key})


def _owner_only_best_effort(path: Path) -> None:
    if os.name == "nt":
        return
    try:
        path.chmod(0o600)
    except OSError:
        pass
