# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.
"""Validate and install new Pipeline directories only; identical directories skip."""

import argparse
import sys
from pathlib import Path

from _bundle import (
    PIPELINES,
    REPOSITORY,
    Receipt,
    bundle_names,
    checked_path,
    dataset_pin,
    digest,
    equal_directory,
    hash_mapping,
    inventory,
    make_parents,
    overlaps,
    parse_json,
    publish_new,
)


def validated_package(package):
    contents = inventory(package)
    raw = contents.pop("bundle-manifest.json", None)
    if raw is None:
        raise ValueError(f"Missing bundle inventory: {package}")
    manifest = parse_json(raw)
    if not isinstance(manifest, dict) or set(manifest) != {"format", "files"} or manifest["format"] != "fsq-dragon-bundle/v1":
        raise ValueError("Invalid bundle inventory schema")
    hashes = hash_mapping(manifest["files"])
    if set(hashes) != bundle_names():
        raise ValueError("Incomplete or unknown bundle inventory entries")
    ordinary_files = {name: data for name, data in contents.items() if data is not None}
    if set(ordinary_files) != set(hashes):
        raise ValueError("Bundle file inventory conflict")
    # Also reject unlisted empty directories.
    equal_directory(package, {**ordinary_files, "bundle-manifest.json": raw})
    for name, expected in hashes.items():
        if digest(ordinary_files[name]) != expected:
            raise ValueError(f"Bundle hash conflict: {package / name}")
    for name, expected in dataset_pin().items():
        if digest(ordinary_files[f"dataset/msedge/{name}"]) != expected:
            raise ValueError(f"Fixed Dataset hash conflict: {name}")
    if parse_json(ordinary_files["dataset-manifest.json"]) != parse_json((Path(__file__).parent / "dataset-manifest.json").read_bytes()):
        raise ValueError("Packaged Dataset manifest conflict")
    return ordinary_files


def install(package, dragon_data_dir):
    package = checked_path(package)
    target = checked_path(dragon_data_dir)
    if overlaps(target, package) or overlaps(target, REPOSITORY):
        raise ValueError(f"Target overlaps maintained source or package: {target}")
    if overlaps(package, REPOSITORY) and not package.is_relative_to(REPOSITORY / "dist"):
        raise ValueError(f"Package overlaps maintained source: {package}")
    files = validated_package(package)  # Snapshot everything before target writes.
    pipelines = checked_path(target / "pipelines")
    pending = []
    for pipeline in PIPELINES:
        prefix = f"pipelines/{pipeline}/"
        members = {name.removeprefix(prefix): data for name, data in files.items() if name.startswith(prefix)}
        destination = checked_path(pipelines / pipeline)
        if destination.exists():
            equal_directory(destination, members, runtime_metadata=True)
        else:
            pending.append((destination, members))
    if not pending:
        return
    parents = Receipt()
    installed = []
    try:
        make_parents(pipelines, parents)
        for destination, members in pending:
            installed.append(publish_new(destination, members))
    except (OSError, ValueError) as failure:
        errors = []
        for receipt in reversed([parents, *installed]):
            try:
                receipt.rollback()
            except OSError as error:
                errors.append(str(error))
        if errors:
            raise OSError(f"{failure}; " + "; ".join(errors)) from failure
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", type=Path, required=True)
    parser.add_argument("--dragon-data-dir", type=Path, required=True)
    args = parser.parse_args()
    try:
        install(args.package, args.dragon_data_dir)
    except (OSError, ValueError) as error:
        print(f"Install failed: {error}", file=sys.stderr)
        return 1
    print(f"Pipelines verified (new installed, identical skipped): {args.dragon_data_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
