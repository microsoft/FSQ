# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.
# Preserves exact FSQ invocation data before execution and updates its explicit terminal outcome.
import hashlib
import importlib.metadata
import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

from reporting import save_json

GLOBAL_ARGUMENTS = ["--output", "json", "--non-interactive"]
SCHEMA_VERSION = "waa.fsq-invocation/v1"


def timestamp():
    return datetime.now(UTC).isoformat()


def begin_invocation(root, label, workspace, arguments, operation, command, scope, timeout, case_id):
    if not label or not all(char.isascii() and (char.isalnum() or char in "-_") for char in label):
        raise ValueError("The invocation label must be a single safe filename component.")
    if operation not in {"providers.status", "init", "doctor", "case.create"}:
        raise ValueError("Only evaluation operations may be recorded; authentication commands are excluded.")
    for argument in arguments:
        if argument in {"--api-key", "--token", "--access-token"} or argument.startswith(("--api-key=", "--token=", "--access-token=")):
            raise ValueError("Credential arguments cannot be written to invocation evidence.")
    path = root / f"{label}.command.json"
    if path.exists():
        raise FileExistsError("An invocation record already exists. Start a fresh attempt instead of overwriting it.")
    package = Path(command[2]).parent
    hashes = {
        name: hashlib.sha256((package / name).read_bytes()).hexdigest() for name in ("runner.py", "edge_session.py", "windows_job.py", "dataset.py", "invocation.py", "git_source.py", "locks.py")
    }
    source_path = root / "git-source.json"
    if not source_path.exists():
        source_path = root.parent / "git-source.json"
    source = json.loads(source_path.read_text(encoding="utf-8")) if source_path.exists() else None
    cli_arguments = [*GLOBAL_ARGUMENTS, *arguments]
    record = {
        "schema_version": SCHEMA_VERSION,
        "recorded_at": timestamp(),
        "state": "prepared",
        "case_id": case_id,
        "task_id": os.environ.get("DRAGON_TASK_ID"),
        "operation": operation,
        "working_directory": str(workspace.resolve()),
        "python_executable": sys.executable,
        "fsq_version": importlib.metadata.version("fsq-agent"),
        "cli_entry_point": "fsq_agent.adapters.cli:main",
        "cli_arguments": cli_arguments,
        "cli_display_windows": subprocess.list2cmdline(["fsq", *cli_arguments]),
        "wrapper_arguments": command,
        "timeout_seconds": timeout,
        "process_scope": scope,
        "adapter": f"pid-scoped-pywinauto/{importlib.metadata.version('fsq-agent')}-v1" if scope else None,
        "package_sha256": hashes,
        "git_source": source,
        "stdout_file": f"{label}.stdout.json",
        "stderr_file": f"{label}.stderr.log",
        "process_file": f"{label}.process.json",
        "reproduction": {
            "mode": "fresh_pipeline_task",
            "pipeline_id": "fsq-waa-edge-eval",
            "variables": {
                "fsq-python": sys.executable,
                "dataset-dir": os.environ.get("DRAGON_VAR_DATASET_DIR", ""),
                "edge-exe": (scope or {}).get("executable", os.environ.get("DRAGON_VAR_EDGE_EXE", "")),
                "case-ids": case_id or "",
                "git-url": (source or {}).get("url", os.environ.get("DRAGON_VAR_GIT_URL", "")),
                "git-branch": (source or {}).get("branch", os.environ.get("DRAGON_VAR_GIT_BRANCH", "")),
            },
            "limitations": [
                "The display command is CLI-equivalent, not a faithful replay of the process-scoped wrapper.",
                "Recorded PIDs, windows, and Profiles describe the original attempt; do not reuse an old PID.",
                "Start a fresh Pipeline Task selecting this Case to recreate the isolation and target adapter.",
                "The wrapper waits for an owning Windows Job's stdin admission byte; it is not a standalone copy/paste command.",
                "Model responses and live UI execution may differ even with the same arguments.",
                "Branch synchronization may resolve a newer source commit; compare the recorded git_source.commit.",
            ],
        },
    }
    save_json(path, record)
    return path


def finish_invocation(path, outcome, process=None, fsq_record=None, error=None):
    record = json.loads(path.read_text(encoding="utf-8"))
    record["state"] = outcome
    record["finished_at"] = timestamp()
    if process is not None:
        record["process"] = process
    if fsq_record is not None:
        record["fsq_record_type"] = fsq_record.get("type")
        record["fsq_status"] = fsq_record.get("result", {}).get("status")
        record["run_id"] = fsq_record.get("result", {}).get("run_id")
        record["fsq_error_code"] = fsq_record.get("error", {}).get("code")
    if error is not None:
        record["error"] = {"type": type(error).__name__, "message": str(error)}
    save_json(path, record)
