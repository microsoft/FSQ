# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.
"""Construct a deterministic offline bundle; never execute any Pipeline."""

import argparse
import importlib.util
import sys
import tempfile
from pathlib import Path

from _bundle import (
    LOCAL,
    REMOTE,
    REPOSITORY,
    ROOT,
    SOURCE_FILES,
    Receipt,
    bundle_names,
    checked_path,
    dataset_pin,
    digest,
    equal_directory,
    inventory,
    json_bytes,
    make_parents,
    overlaps,
    publish_new,
)

SOURCES = ROOT / "pipelines"
DATASET = ROOT.parent / "examples" / "msedge"


def runtime_builder():
    # The approved sibling script is the only code loaded; no generated script runs.
    spec = importlib.util.spec_from_file_location("fsq_dragon_build_remote", ROOT / "pipelines" / LOCAL / "build_remote.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.build


def build(output):
    output = checked_path(output)
    if overlaps(output, REPOSITORY) and not output.is_relative_to(REPOSITORY / "dist"):
        raise ValueError(f"Output must be external or under ignored repository dist/: {output}")
    if overlaps(output, checked_path(SOURCES)) or overlaps(output, checked_path(DATASET)):
        raise ValueError(f"Output overlaps maintained inputs: {output}")
    pin = dataset_pin()
    dataset = inventory(checked_path(DATASET))
    if set(dataset) != set(pin):
        raise ValueError(f"Fixed Dataset file set conflict: {DATASET}")
    for name, expected in pin.items():
        if digest(dataset[name]) != expected:
            raise ValueError(f"Fixed Dataset hash conflict: {DATASET / name}")
    files = {"README.md": (ROOT / "README.md").read_bytes(), "dataset-manifest.json": (ROOT / "dataset-manifest.json").read_bytes()}
    for pipeline, members in SOURCE_FILES.items():
        directory = checked_path(SOURCES / pipeline)
        for name in members:
            path = checked_path(directory / name)
            if not path.is_file():
                raise ValueError(f"Missing maintained source file: {path}")
            files[f"pipelines/{pipeline}/{name}"] = path.read_bytes()
    files.update({f"dataset/msedge/{name}": data for name, data in dataset.items()})
    # Use snapshots so generated payloads and top-level inventory share exactly the same bytes.
    with tempfile.TemporaryDirectory(prefix="fsq-build-") as temporary:
        stage = Path(temporary)
        local = stage / LOCAL
        local.mkdir()
        fixed = stage / "dataset"
        fixed.mkdir()
        for name in SOURCE_FILES[LOCAL]:
            (local / name).write_bytes(files[f"pipelines/{LOCAL}/{name}"])
        for name, data in dataset.items():
            (fixed / name).write_bytes(data)
        generate = runtime_builder()
        for pipeline, mode in REMOTE.items():
            destination = stage / pipeline
            generate(local, fixed, destination, mode)
            for name in ("deploy.ps1", "payload-manifest.json"):
                files[f"pipelines/{pipeline}/{name}"] = (destination / name).read_bytes()
    if set(files) != bundle_names():
        raise ValueError("Build output allowlist mismatch")
    files["bundle-manifest.json"] = json_bytes({"format": "fsq-dragon-bundle/v1", "files": {name: digest(data) for name, data in sorted(files.items())}})
    if output.exists():
        equal_directory(output, files)
        return
    parents = Receipt()
    try:
        make_parents(output.parent, parents)
        publish_new(output, files)
    except (OSError, ValueError):
        parents.rollback()
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        build(args.output)
    except (OSError, ValueError) as error:
        print(f"Build failed: {error}", file=sys.stderr)
        return 1
    print(f"Bundle verified: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
