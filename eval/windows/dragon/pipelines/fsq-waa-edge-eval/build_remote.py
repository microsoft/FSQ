# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.
# Builds a single transported Stage containing all evaluator assets and byte-identical Dataset inputs.
import argparse
import base64
import hashlib
import io
import json
import sys
import zipfile
from pathlib import Path

FILES = (
    "portable_bootstrap.py",
    "runner.py",
    "dataset.py",
    "edge_session.py",
    "windows_job.py",
    "invocation.py",
    "locks.py",
    "git_source.py",
    "runtime_contract.py",
    "reporting.py",
    "report.css",
    "setup_acceptance.py",
    "setup_report.py",
)


def build(package, dataset, output, mode="evaluate"):
    # This retained helper is a source-checkout maintenance entry, not a remote runtime dependency.
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from _bundle import REPOSITORY, Receipt, checked_path, equal_directory, make_parents, overlaps, publish_new

    output = checked_path(output)
    if overlaps(output, REPOSITORY) and not output.is_relative_to(REPOSITORY / "dist"):
        raise ValueError(f"Output must be external or under ignored repository dist/: {output}")
    if overlaps(output, checked_path(package)) or overlaps(output, checked_path(dataset)):
        raise ValueError(f"Output overlaps payload inputs: {output}")
    if mode not in {"evaluate", "setup"}:
        raise ValueError("The transported operation mode must be evaluate or setup.")
    files = {name: (package / name).read_bytes() for name in FILES}
    definitions = sorted(dataset.glob("*.json"))
    if not definitions:
        raise ValueError("The fixed Dataset directory contains no JSON definitions.")
    for path in definitions:
        if path.is_symlink() or path.stat().st_size > 1_000_000:
            raise ValueError("Dataset inputs must be ordinary bounded JSON files.")
        files["dataset/" + path.name] = path.read_bytes()
    provenance = {
        "format": "waa-portable-package/v1",
        "files": {name: hashlib.sha256(data).hexdigest() for name, data in files.items()},
    }
    files["package-manifest.json"] = json.dumps(provenance, indent=2).encode()
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
        for name, data in sorted(files.items()):
            info = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            bundle.writestr(info, data)
    payload = archive.getvalue()
    script = (package / "remote-stage.template.ps1").read_text(encoding="utf-8")
    script = script.replace("__PAYLOAD_BASE64__", base64.b64encode(payload).decode("ascii"))
    script = script.replace("__PAYLOAD_SHA256__", hashlib.sha256(payload).hexdigest())
    script = script.replace("__OPERATION_MODE__", mode)
    rendered = {"deploy.ps1": script.encode("utf-8"), "payload-manifest.json": json.dumps(provenance, indent=2).encode("utf-8")}
    if output.exists():
        equal_directory(output, rendered)
    else:
        parents = Receipt()
        try:
            make_parents(output.parent, parents)
            publish_new(output, rendered)
        except (OSError, ValueError):
            parents.rollback()
            raise
    return provenance


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--mode", choices=("evaluate", "setup"), default="evaluate")
    args = parser.parse_args()
    build(Path(__file__).parent, args.dataset, args.output, args.mode)
