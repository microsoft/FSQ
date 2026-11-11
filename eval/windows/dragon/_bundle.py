# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.
"""Shared offline bundle schema and filesystem guards for the script entries."""

import hashlib
import json
import re
import stat
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REPOSITORY = ROOT.parents[2]
LOCAL = "fsq-waa-edge-eval"
SOURCE_FILES = {
    LOCAL: (
        "build_remote.py",
        "dataset.py",
        "edge_session.py",
        "git_source.py",
        "invocation.py",
        "locks.py",
        "pipeline.json",
        "portable_bootstrap.py",
        "prepare.ps1",
        "publish.ps1",
        "README.md",
        "remote-stage.template.ps1",
        "report.css",
        "reporting.py",
        "run.ps1",
        "runner.py",
        "runtime_contract.py",
        "setup_acceptance.py",
        "setup_report.py",
        "sync-source.ps1",
        "test_adapter.py",
        "test_git_source.py",
        "test_portable.py",
        "test_setup.py",
        "windows_job.py",
    ),
    "fsq-dev-box-setup": ("pipeline.json", "README.md"),
    "fsq-waa-remote-eval": ("pipeline.json", "README.md"),
    "fsq-remote-readiness": ("pipeline.json", "inspect.ps1"),
    "fsq-remote-authorization": ("pipeline.json", "authorize.ps1"),
}
PIPELINES = tuple(SOURCE_FILES)
REMOTE = {"fsq-dev-box-setup": "setup", "fsq-waa-remote-eval": "evaluate"}
METADATA = ".dragon-runtime.json"


def digest(data):
    return hashlib.sha256(data).hexdigest()


def json_bytes(value):
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")


def relative_name(name):
    """Portable ordinary paths only, including Windows device/ADS restrictions."""
    if not isinstance(name, str) or not name:
        raise ValueError(f"Invalid relative path: {name!r}")
    for part in name.split("/"):
        stem = part.split(".", 1)[0].upper()
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", part) or part.endswith(".") or stem in {"CON", "PRN", "AUX", "NUL"} or re.fullmatch(r"(COM|LPT)[0-9]", stem):
            raise ValueError(f"Invalid relative path: {name!r}")
    return name


def ordinary(path):
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
        raise ValueError(f"Links/reparse points are forbidden: {path}")
    if stat.S_ISREG(info.st_mode) and info.st_nlink != 1:
        raise ValueError(f"Hard links are forbidden: {path}")
    if not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)):
        raise ValueError(f"Not an ordinary file/directory: {path}")
    return info


def checked_path(path):
    """Check all lexical ancestors before resolving, including dangling links."""
    if ".." in Path(path).parts:
        raise ValueError(f"Traversal is forbidden: {path}")
    path = Path(path).absolute()
    for parent in (*reversed(path.parents), path):
        if parent.exists() or parent.is_symlink():
            ordinary(parent)
    return path.resolve()


def overlaps(first, second):
    return first == second or first.is_relative_to(second) or second.is_relative_to(first)


def inventory(directory, *, runtime_metadata=False):
    """Read ordinary files, rejecting even unknown empty directories at equality time."""
    ordinary(directory)
    files = {}
    seen = set()

    def visit(parent):
        for path in sorted(parent.iterdir()):
            # Do not stat, open, recurse into, or otherwise interpret this runtime entry.
            if runtime_metadata and path.parent == directory and path.name == METADATA:
                continue
            name = path.relative_to(directory).as_posix()
            relative_name(name)
            folded = name.casefold()
            if folded in seen:
                raise ValueError(f"Ambiguous case-insensitive path: {path}")
            seen.add(folded)
            info = ordinary(path)
            if stat.S_ISDIR(info.st_mode):
                files[name + "/"] = None
                visit(path)
            else:
                files[name] = path.read_bytes()

    visit(directory)
    return files


def file_inventory(files):
    result = {}
    for name, data in files.items():
        relative_name(name)
        result[name] = data
        parts = name.split("/")
        for index in range(1, len(parts)):
            result["/".join(parts[:index]) + "/"] = None
    return result


def equal_directory(directory, files, *, runtime_metadata=False):
    try:
        actual = inventory(directory, runtime_metadata=runtime_metadata)
    except ValueError as error:
        raise ValueError(f"Contents conflict: {directory}: {error}") from error
    if actual != file_inventory(files):
        raise ValueError(f"Contents conflict: {directory}")


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def parse_json(data):
    return json.loads(data, object_pairs_hook=unique_object)


def hash_mapping(value):
    if not isinstance(value, dict) or not value:
        raise ValueError("Expected a nonempty file hash mapping")
    seen = set()
    for name, expected in value.items():
        relative_name(name)
        if name.casefold() in seen:
            raise ValueError(f"Ambiguous file identity: {name}")
        seen.add(name.casefold())
        if not isinstance(expected, str) or not re.fullmatch("[0-9a-f]{64}", expected):
            raise ValueError(f"Invalid SHA-256: {name}")
    return value


def dataset_pin():
    pin = parse_json((ROOT / "dataset-manifest.json").read_bytes())
    if not isinstance(pin, dict) or set(pin) != {"format", "files"} or pin["format"] != "fsq-dragon-dataset/v1":
        raise ValueError("Invalid Dataset manifest")
    files = hash_mapping(pin["files"])
    if len(files) != 13 or any("/" in name or not name.endswith(".json") for name in files):
        raise ValueError("Invalid fixed Dataset identity set")
    return files


def bundle_names():
    names = {f"pipelines/{pipeline}/{name}" for pipeline, members in SOURCE_FILES.items() for name in members}
    names.update(f"pipelines/{pipeline}/{name}" for pipeline in REMOTE for name in ("deploy.ps1", "payload-manifest.json"))
    names.update(f"dataset/msedge/{name}" for name in dataset_pin())
    return names | {"README.md", "dataset-manifest.json"}


@dataclass
class Receipt:
    """Only entries created by this invocation may be rolled back."""

    files: list = field(default_factory=list)
    directories: list = field(default_factory=list)

    def rollback(self):
        failures = []
        for path, identity in reversed(self.files):
            try:
                info = ordinary(path)
                if (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns) != identity:
                    raise ValueError(f"New file changed concurrently; preserved: {path}")  # noqa: TRY301 - Collect rollback failures without deleting changed entries.
                path.unlink()
            except (OSError, ValueError) as error:
                failures.append(str(error))
        for path, identity in reversed(self.directories):
            try:
                info = ordinary(path)
                if (info.st_dev, info.st_ino) != identity:
                    raise ValueError(f"New directory replaced concurrently; preserved: {path}")  # noqa: TRY301 - Collect rollback failures without removing replacements.
                path.rmdir()  # Never recursively delete unknown or concurrently added content.
            except (OSError, ValueError) as error:
                failures.append(str(error))
        if failures:
            raise OSError("Rollback incomplete: " + "; ".join(failures))


def make_parents(path, receipt):
    missing = []
    current = path
    while not current.exists():
        missing.append(current)
        current = current.parent
    checked_path(current)
    for directory in reversed(missing):
        directory.mkdir()
        info = directory.lstat()
        receipt.directories.append((directory, (info.st_dev, info.st_ino)))


def publish_new(destination, files):
    """Stage fully, reserve a new directory exclusively, then publish only owned files."""
    receipt = Receipt()
    with tempfile.TemporaryDirectory(prefix=".fsq-stage-", dir=destination.parent) as temporary:
        stage = Path(temporary)
        for name, data in sorted(files.items()):
            relative_name(name)
            path = stage / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        equal_directory(stage, files)
        try:
            destination.mkdir()  # No exist_ok: never adopt a directory created concurrently.
            info = destination.lstat()
            receipt.directories.append((destination, (info.st_dev, info.st_ino)))
            for name in sorted(files):
                path = destination / name
                make_parents(path.parent, receipt)
                checked_path(path.parent)
                stream = path.open("xb")
                created = path.lstat()
                try:
                    with stream:
                        stream.write((stage / name).read_bytes())
                finally:
                    # Include partially written files, but never adopt a substituted inode.
                    info = path.lstat()
                    receipt.files.append((path, (created.st_dev, created.st_ino, info.st_size, info.st_mtime_ns)))
        except (OSError, ValueError) as failure:
            try:
                receipt.rollback()
            except OSError as error:
                raise OSError(f"{failure}; {error}") from failure
            raise
    return receipt
