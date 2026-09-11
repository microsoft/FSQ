# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Execute one Case and always attempt evidence exports without losing its exit code."""

import argparse
import json
import subprocess
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("case")
    parser.add_argument("--platform", required=True, choices=("web", "android", "windows", "macos"))
    parser.add_argument("--artifacts", type=Path, default=Path("ci-evidence"))
    args = parser.parse_args()
    args.artifacts.mkdir(parents=True, exist_ok=False)
    command = [sys.executable, "-c", "from fsq_agent.adapters.cli import main; main()", "--output", "json", "--non-interactive"]
    execution = subprocess.run([*command, "case", "test", "--platform", args.platform, args.case], capture_output=True, text=True, check=False)  # noqa: S603 - explicit executable and arguments, no shell.
    (args.artifacts / "cli-result.json").write_text(execution.stdout, encoding="utf-8")
    print(execution.stdout, end="")
    try:
        record = json.loads(execution.stdout)
        run_id = record.get("result", {}).get("run_id") or record.get("error", {}).get("details", {}).get("run_id")
    except ValueError:
        run_id = None
    export_errors = []
    if run_id:
        for report_format, name in (("json", "report.json"), ("junit", "junit.xml"), ("html", "report.html"), ("bundle", "evidence-bundle.zip")):
            exported = subprocess.run(  # noqa: S603 - explicit executable and arguments, no shell.
                [*command, "runs", "export", run_id, "--platform", args.platform, "--format", report_format, "--output", str((args.artifacts / name).resolve())],
                capture_output=True,
                text=True,
                check=False,
            )
            if exported.returncode:
                export_errors.append({"format": report_format, "exit_code": exported.returncode, "output": exported.stdout})
    (args.artifacts / "export-status.json").write_text(json.dumps({"run_id": run_id, "test_exit_code": execution.returncode, "export_errors": export_errors}, indent=2), encoding="utf-8")
    return execution.returncode or (5 if export_errors else 0)


if __name__ == "__main__":
    raise SystemExit(main())
