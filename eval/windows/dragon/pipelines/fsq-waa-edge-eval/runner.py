# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.
# Runs original WAA Edge goals through FSQ Windows CLI and preserves independent scoring evidence.
import hashlib
import importlib.metadata
import json
import os
import shutil
import sys
import time
import traceback
import uuid
from pathlib import Path

from dataset import DEFAULT_IDS, load_dataset, score_preferences, seed_profile
from edge_session import EdgeSession, bounded_root, desktop_lock, edge_arguments, install_process_scope, require_desktop, require_screen_capture
from reporting import save_json, write_report


class ExecutionBlocked(RuntimeError):  # noqa: N818 - Retain the imported evaluator exception identity.
    pass


def classify_evaluation(response, observation):
    evaluation = dict(observation)
    record = response.get("record", {})
    if response["protocol_status"] == "unknown":
        status = "unknown"
    elif response["protocol_status"] != "valid":
        status = "error"
    elif record.get("type") == "error":
        status = (
            "blocked"
            if record["error"].get("category")
            in {
                "configuration",
                "workspace_configuration",
                "unavailable",
            }
            else "error"
        )
    else:
        return evaluation
    evaluation["observed_state_score"] = evaluation.pop("benchmark_score")
    evaluation["benchmark_score"] = None
    evaluation["evaluation_status"] = status
    return evaluation


def fsq_call(root, workspace, label, arguments, operation, scope=None, timeout=60, case_id=None):
    from invocation import begin_invocation, finish_invocation
    from windows_job import run_owned

    stdout, stderr = root / f"{label}.stdout.json", root / f"{label}.stderr.log"
    command = [sys.executable, "-u", str(Path(__file__).resolve()), "invoke", json.dumps(scope), *arguments]
    command_record = begin_invocation(root, label, workspace, arguments, operation, command, scope, timeout, case_id)
    started = time.monotonic()
    try:
        process = run_owned(command, workspace, stdout, stderr, timeout)
    except BaseException as error:
        try:
            finish_invocation(command_record, "interrupted" if isinstance(error, (KeyboardInterrupt, SystemExit)) else "execution_error", error=error)
        except (OSError, ValueError) as recording_error:
            error.add_note(f"The invocation outcome could not be saved: {recording_error}")
        raise
    process["seconds"] = round(time.monotonic() - started, 3)
    process["command_record"] = command_record.name
    save_json(root / f"{label}.process.json", process)
    if process["timed_out"]:
        finish_invocation(command_record, "timed_out", process=process)
        return {**process, "protocol_status": "unknown", "error": "CLI timeout; owned descendants settled; UI outcome requires evidence."}
    try:
        record = json.loads(stdout.read_text(encoding="utf-8-sig"))
        if not isinstance(record, dict) or record.get("schema_version") != "fsq.machine/v1":
            raise ValueError("Unsupported machine schema")  # noqa: TRY301 - Normalize malformed CLI responses at this boundary.
        if record.get("operation") != operation or record.get("type") not in {"result", "error"}:
            raise ValueError("Wrong operation or nonterminal record")  # noqa: TRY301 - Normalize malformed CLI responses at this boundary.
        if record["type"] == "result" and not isinstance(record.get("result"), dict):
            raise ValueError("Missing result")  # noqa: TRY301 - Normalize malformed CLI responses at this boundary.
        if record["type"] == "error" and not isinstance(record.get("error"), dict):
            raise ValueError("Missing error")  # noqa: TRY301 - Normalize malformed CLI responses at this boundary.
        if operation == "case.create" and record["type"] == "result":
            value = record["result"]
            if value.get("status") not in {"success", "failed", "cancelled", "inconclusive"}:
                raise ValueError("Unknown FSQ execution status")  # noqa: TRY301 - Normalize malformed CLI responses at this boundary.
            if not all(isinstance(value.get(key), str) and value[key] for key in ("run_id", "report_path")):
                raise ValueError("Missing FSQ Run identity or report")  # noqa: TRY301 - Normalize malformed CLI responses at this boundary.
    except (OSError, ValueError, UnicodeDecodeError) as error:
        finish_invocation(command_record, "protocol_error", process=process, error=error)
        return {**process, "protocol_status": "error", "error": str(error)}
    finish_invocation(
        command_record,
        "completed" if process["exit_code"] == 0 and record["type"] == "result" else "failed",
        process=process,
        fsq_record=record,
    )
    return {**process, "protocol_status": "valid", "record": record}


def require_result(response, operation):
    record = response.get("record", {})
    if response["exit_code"] != 0 or response["protocol_status"] != "valid" or record.get("type") != "result":
        code = record.get("error", {}).get("code", response.get("error", "nonzero_exit"))
        raise RuntimeError(f"FSQ {operation} did not complete: {code}. Inspect its saved machine record and stderr.")
    return record["result"]


def find_edge():
    explicit = os.environ.get("DRAGON_VAR_EDGE_EXE", "").strip()
    candidates = (
        [Path(explicit)]
        if explicit
        else [
            Path(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)")) / "Microsoft" / "Edge" / "Application" / "msedge.exe",
            Path(os.environ.get("PROGRAMFILES", r"C:\Program Files")) / "Microsoft" / "Edge" / "Application" / "msedge.exe",
        ]
    )
    for path in candidates:
        if path.is_file() and path.name.casefold() == "msedge.exe":
            return path.resolve()
    raise ValueError("Set edge-exe to the installed msedge.exe on the executing Machine.")


def prepare(root, report):
    require_desktop()
    require_screen_capture()
    from runtime_contract import verify_runtime

    version = verify_runtime()
    import pywinauto

    report["fsq_version"] = version
    report["pywinauto_version"] = pywinauto.__version__
    report["edge_executable"] = str(find_edge())
    import win32api

    version_info = win32api.GetFileVersionInfo(report["edge_executable"], "\\")
    report["edge_version"] = ".".join(
        str(value)
        for value in (
            version_info["FileVersionMS"] >> 16,
            version_info["FileVersionMS"] & 0xFFFF,
            version_info["FileVersionLS"] >> 16,
            version_info["FileVersionLS"] & 0xFFFF,
        )
    )
    directory = os.environ.get("DRAGON_VAR_DATASET_DIR", "").strip()
    if not directory:
        raise ValueError("Set dataset-dir to the original WAA msedge JSON directory on this Machine.")
    selected_ids = [value.strip() for value in os.environ.get("DRAGON_VAR_CASE_IDS", "").split(",") if value.strip()] or DEFAULT_IDS
    records, selected = load_dataset(directory, selected_ids)
    report["dataset_dir"] = str(Path(directory).resolve())
    report["inventory"] = [{key: value for key, value in item.items() if key != "definition"} for item in records]
    report["tasks"] = [{"id": item["id"], "instruction": item["definition"]["instruction"], "dataset_sha256": item["sha256"], "evaluation_status": "not_run"} for item in selected]
    original = root / "dataset"
    original.mkdir(exist_ok=True)
    for item in records:
        shutil.copyfile(item["file"], original / (item["id"] + ".json"))
    provider = require_result(fsq_call(root, root, "provider", ["providers", "status"], "providers.status"), "providers.status")
    if provider.get("status") != "ready" or provider.get("authenticated") is not True:
        raise RuntimeError("The selected FSQ Provider is not ready. Configure FSQ for this account before running again.")
    report["provider"] = {key: provider.get(key) for key in ("provider", "model", "status")}
    report["target_adapter"] = f"pid-scoped-pywinauto/{version}-v1"
    report["environment_mode"] = "fresh-private-profile; shared interactive desktop; not stock WAA VM"
    report["status"] = "ready"


def copy_runs(workspace, destination):
    source = workspace / ".fsq" / "runs"
    if source.exists():
        for path in source.rglob("*"):
            if path.is_symlink():
                raise ValueError("FSQ Run evidence unexpectedly contains a symbolic link.")
        shutil.copytree(source, destination, dirs_exist_ok=True)


def run_one(root, attempt, row, case, executable):
    require_desktop()
    profile, workspace = attempt / "profile", workspace_path(attempt)
    workspace.mkdir()
    baseline = seed_profile(profile, attempt / "downloads")
    before_score = score_preferences(case, baseline)
    if before_score["benchmark_score"] != 0:
        raise ValueError("The fresh baseline already satisfies the goal; this attempt would not measure task completion.")
    save_json(root / "baseline-evaluation.json", before_score)
    initialized = require_result(
        fsq_call(
            root,
            workspace,
            "init",
            ["init", "--platform", "windows", "--app-path", str(executable), "--launch-args", json.dumps(edge_arguments(profile))],
            "init",
            case_id=case["id"],
        ),
        "init",
    )
    if Path(initialized["root_path"]).resolve() != workspace.resolve():
        raise ValueError("FSQ registered a different Workspace root.")
    doctor = require_result(fsq_call(root, workspace, "doctor", ["doctor"], "doctor", case_id=case["id"]), "doctor")
    windows = next((item for item in doctor["platforms"] if item["platform"] == "windows"), None)
    if windows is None or windows["commands"]["case_create"]["status"] != "ready":
        raise RuntimeError("FSQ doctor did not establish Windows creation readiness.")
    session = EdgeSession(executable, profile)
    try:
        session.start()
        row["owned_edge_pid"] = session.pid
        session.snapshot(root / "before.png")
        scope = {"pid": session.pid, "profile": str(profile), "executable": str(executable), "error_path": str(root / "target-error.json")}
        goal = (
            case["instruction"] + "\n\nExecution context: The configured Edge test application is already open "
            "with a private disposable Profile. Use Windows UI actions in that application only. "
            "Do not use shell, file editing, registry, scripts, other applications, or direct preference writes. "
            "Do not open another Profile, sign in, close the application, or change unrelated settings. "
            "The evaluator independently reads the resulting state."
        )
        response = fsq_call(
            root,
            workspace,
            "create",
            ["case", "create", "--platform", "windows", "--goal", goal],
            "case.create",
            scope=scope,
            timeout=540,
            case_id=case["id"],
        )
        row["cli"] = {key: value for key, value in response.items() if key != "record"}
        result = response.get("record", {}).get("result", {})
        row["fsq_status"] = result.get("status", "unknown" if response["protocol_status"] == "unknown" else "error")
        row["run_id"] = result.get("run_id")
        row["fsq_report_path"] = result.get("report_path")
        row["fsq_error"] = response.get("record", {}).get("error")
        row["workspace"] = str(workspace)
        try:
            session.snapshot(root / "after.png")
        except (RuntimeError, TimeoutError) as error:
            row.setdefault("evidence_errors", []).append(str(error))
    finally:
        session.stop()
        copy_runs(workspace, root / "fsq-runs")
    preference_path = profile / "Default" / "Preferences"
    if preference_path.stat().st_size > 10_000_000:
        raise ValueError("The isolated Profile Preferences exceed the evidence-size limit.")
    preferences = json.loads(preference_path.read_text(encoding="utf-8"))
    row.update(classify_evaluation(response, score_preferences(case, preferences)))
    target_error = root / "target-error.json"
    if target_error.exists():
        row["target_error"] = json.loads(target_error.read_text(encoding="utf-8"))
        row["observed_state_score"] = row["benchmark_score"]
        row["benchmark_score"] = None
        row["evaluation_status"] = "error"
    row["state_evidence"] = {
        "source": str(preference_path),
        "sha256": hashlib.sha256(preference_path.read_bytes()).hexdigest(),
        "flush": "owned Edge window closed and complete owned Job settled",
    }
    save_json(root / "observed-evaluation.json", {key: row[key] for key in ("evaluation_status", "benchmark_score", "getter", "metric", "observed", "expected", "state_evidence")})
    row["cleanup"] = "settled"
    if row["evaluation_status"] == "blocked":
        raise ExecutionBlocked("FSQ could not execute because its Provider or configuration was unavailable. Inspect create.stdout.json and reconfigure FSQ before a fresh Task.")
    if row["evaluation_status"] in {"unknown", "error"}:
        raise RuntimeError("The FSQ invocation could not be established. Its observed state is retained but excluded from benchmark scoring.")


def workspace_path(attempt):
    return attempt / f"waa-{attempt.parent.name}-{attempt.name}"


def run(root, report):
    if report["status"] != "ready":
        raise RuntimeError("Readiness did not complete. Start a fresh Task after correcting its blocker.")
    if importlib.metadata.version("fsq-agent") != report["fsq_version"]:
        raise RuntimeError("FSQ changed after readiness. Start a fresh Task.")
    source_path = root / "git-source.json"
    if not source_path.exists():
        raise RuntimeError("Git source synchronization did not complete. No UI task was started.")
    source = json.loads(source_path.read_text(encoding="utf-8"))
    from git_source import source_inputs

    requested = source_inputs(os.environ.get("DRAGON_VAR_GIT_URL", ""), os.environ.get("DRAGON_VAR_GIT_BRANCH", ""))
    if (source.get("url"), source.get("branch")) != requested:
        raise RuntimeError("Git source inputs changed after synchronization. Start a fresh Task.")
    report["git_source"] = source
    current, _ = load_dataset(report["dataset_dir"], [row["id"] for row in report["tasks"]])
    expected_hashes = {item["id"]: item["sha256"] for item in report["inventory"]}
    if {item["id"]: item["sha256"] for item in current} != expected_hashes:
        raise RuntimeError("The Dataset changed after readiness. No UI task was started.")
    report["status"] = "running"
    parent = bounded_root(Path.home() / ".fsq-waa")
    with desktop_lock(parent):
        run_root = parent / uuid.uuid4().hex[:8]
        run_root.mkdir()
        report["retained_root"] = str(run_root)
        for index, row in enumerate(report["tasks"]):
            case = json.loads((root / "dataset" / f"{row['id']}.json").read_text(encoding="utf-8"))
            evidence = root / row["id"]
            evidence.mkdir()
            attempt = run_root / str(index + 1)
            attempt.mkdir()
            print(f"Executing original WAA task {row['id']} through FSQ Windows CLI.", flush=True)
            write_report(root, report)
            try:
                run_one(evidence, attempt, row, case, report["edge_executable"])
            except Exception as error:
                if row.get("evaluation_status") not in {"blocked", "unknown"}:
                    row["evaluation_status"] = "error"
                row["benchmark_score"] = None
                row["error"] = {"type": type(error).__name__, "message": str(error)}
                (evidence / "adapter-error.log").write_text(traceback.format_exc(), encoding="utf-8")
                report["status"] = "blocked" if isinstance(error, ExecutionBlocked) else "incomplete"
                write_report(root, report)
                if isinstance(error, ExecutionBlocked):
                    raise
                raise RuntimeError(f"Task {row['id']} could not be evaluated safely. Later tasks were not started.") from error
            write_report(root, report)
    if any(row.get("cli", {}).get("protocol_status") != "valid" for row in report["tasks"]):
        report["status"] = "incomplete"
    else:
        report["status"] = "passed" if all(row.get("benchmark_score") == 1 for row in report["tasks"]) else "failed"
    report["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def main():
    os.environ["PYTHONIOENCODING"] = "utf-8"
    if sys.argv[1] == "invoke":
        from invocation import GLOBAL_ARGUMENTS

        if sys.stdin.buffer.read(1) != b"1":
            raise RuntimeError("FSQ process admission was not granted.")
        scope = json.loads(sys.argv[2])
        if scope:
            install_process_scope(**scope)
        from fsq_agent.adapters.cli import main as fsq_main

        fsq_main(args=[*GLOBAL_ARGUMENTS, *sys.argv[3:]], prog_name="fsq")
        return 0
    operation = sys.argv[1]
    work = os.environ.get("DRAGON_TASK_WORK_DIR")
    if not work:
        raise ValueError("DRAGON_TASK_WORK_DIR is missing. Run this package through Dragon.")
    root = bounded_root(Path(work) / "waa-eval")
    root.mkdir(parents=True, exist_ok=True)
    report = (
        {"suite": "waa-edge-private-profile/v1", "status": "preparing", "tasks": [], "errors": [], "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "python": sys.executable}
        if operation == "prepare"
        else json.loads((root / "results.json").read_text(encoding="utf-8"))
    )
    try:
        if operation == "prepare":
            prepare(root, report)
        elif operation == "run":
            run(root, report)
        else:
            raise ValueError("Unknown evaluator operation.")  # noqa: TRY301 - Script boundary persists failure evidence.
    except Exception as error:  # noqa: BLE001 - Script boundary persists failure evidence and returns nonzero.
        report["status"] = "blocked" if operation == "prepare" or isinstance(error, ExecutionBlocked) else "incomplete"
        report["errors"].append({"type": type(error).__name__, "message": str(error)})
        (root / f"{operation}-error.log").write_text(traceback.format_exc(), encoding="utf-8")
        write_report(root, report)
        print(f"Evaluation {report['status']}: {error}", file=sys.stderr, flush=True)
        return 1
    write_report(root, report)
    print(f"Evaluation status: {report['status']}", flush=True)
    return 0 if report["status"] in {"ready", "passed"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
