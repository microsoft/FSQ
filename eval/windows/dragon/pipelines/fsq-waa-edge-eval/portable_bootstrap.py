# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.
# Prepares an isolated source-derived FSQ runtime and runs the packaged evaluator on the selected remote Machine.
import getpass
import hashlib
import json
import os
import shutil
import socket
import subprocess
import sys
import traceback
import zipfile
from datetime import UTC, datetime
from pathlib import Path

from edge_session import bounded_root, require_desktop
from git_source import GitCommands, source_inputs, sync_checkout
from locks import file_lock
from reporting import save_json, write_report
from runtime_contract import SOURCE_DRIVER_SHA256, driver_digest
from windows_job import run_owned


def owned_command(arguments, cwd, evidence, label, timeout=600):
    wrapper = [sys.executable, "-u", str(Path(__file__).resolve()), "invoke", *arguments]
    save_json(evidence / f"{label}.command.json", {"arguments": arguments, "cwd": str(cwd)})
    result = run_owned(wrapper, cwd, evidence / f"{label}.stdout.log", evidence / f"{label}.stderr.log", timeout)
    save_json(evidence / f"{label}.process.json", result)
    if result["timed_out"] or result["exit_code"] != 0:
        raise RuntimeError(f"Deployment command {label} failed or timed out. Inspect its saved stdout/stderr; existing installations were not changed.")


def extract_snapshot(archive_path, destination):
    destination.mkdir()
    with zipfile.ZipFile(archive_path) as archive:
        for item in archive.infolist():
            path = Path(item.filename)
            if path.is_absolute() or ".." in path.parts or ":" in item.orig_filename or "\\" in item.orig_filename:
                raise ValueError("The source archive contains an unsafe path.")
            if (item.external_attr >> 16) & 0o170000 == 0o120000:
                raise ValueError("Source archive links are not admitted.")
            if len(str(destination / path)) > 240:
                raise ValueError("The source snapshot exceeds the admitted Windows path budget.")
        archive.extractall(destination)


def deployment_runtime(source, base, evidence, git):
    import tomllib

    checkout = Path(source["checkout_directory"])
    snapshot = base / "source"
    if not snapshot.exists():
        archive = base / "source.zip"
        git(["archive", "--format=zip", f"--output={archive}", source["commit"]], checkout)
        extract_snapshot(archive, snapshot)
    driver = snapshot / "fsq_agent" / "drivers" / "windows" / "_pywinauto.py"
    if driver_digest(driver) != SOURCE_DRIVER_SHA256:
        raise RuntimeError("The requested source changed its Windows driver. Adapter compatibility must be reviewed before execution.")
    metadata = tomllib.loads((snapshot / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    if metadata["version"] != "0.1.4":
        raise RuntimeError("The requested source version is outside the inspected remote runtime contract.")
    python = base / "venv" / "Scripts" / "python.exe"
    marker = base / "runtime.json"
    if not marker.exists():
        if not python.exists():
            owned_command([sys.executable, "-m", "venv", str(base / "venv")], base, evidence, "create-venv")
        # A new dedicated venv lacks FSQ; install only into this commit-qualified environment.
        owned_command([str(python), "-m", "pip", "install", "--disable-pip-version-check", str(snapshot)], base, evidence, "install-source", 1200)
        owned_command([str(python), "-m", "pip", "check"], base, evidence, "check-dependencies")
    probe = (
        "import json,sys,hashlib,importlib.metadata,fsq_agent;"
        "from pathlib import Path;"
        "p=Path(fsq_agent.__file__);"
        "print(json.dumps({'python':sys.executable,'version':importlib.metadata.version('fsq-agent'),"
        "'module':str(p),'driver_sha256':hashlib.sha256((p.parent/'drivers'/'windows'/'_pywinauto.py').read_bytes().replace(bytes([13,10]),bytes([10]))).hexdigest()}))"
    )
    owned_command([str(python), "-c", probe], base, evidence, "inspect-runtime")
    actual = json.loads((evidence / "inspect-runtime.stdout.log").read_text(encoding="utf-8-sig"))
    if actual["version"] != metadata["version"] or actual["driver_sha256"] != SOURCE_DRIVER_SHA256:
        raise RuntimeError("The deployed interpreter does not match the selected source's inspected driver.")
    if not Path(actual["module"]).resolve().is_relative_to((base / "venv").resolve()):
        raise RuntimeError("The deployed FSQ module resolved outside its dedicated venv.")
    runtime = {**actual, "source": source, "snapshot_directory": str(snapshot), "prepared_at": datetime.now(UTC).isoformat()}
    save_json(marker, runtime)
    return python, runtime


def execute_stage(python, operation, root, env):
    arguments = [str(python), "-u", str(Path(__file__).with_name("runner.py")), operation]
    wrapper = [sys.executable, "-u", str(Path(__file__).resolve()), "invoke", *arguments]
    result = run_owned(wrapper, Path(__file__).parent, root / f"{operation}-stage.stdout.log", root / f"{operation}-stage.stderr.log", 2100 if operation == "run" else 180, environment=env)
    save_json(root / f"{operation}-stage.process.json", result)
    if result["timed_out"]:
        raise RuntimeError(f"The remote {operation} Stage timed out; its process tree was stopped. Inspect the retained evidence.")
    return result["exit_code"]


def run_setup_smoke(python, work, root, environment, source, report):
    from dataset import DEFAULT_IDS

    smoke_work = work / "smoke"
    smoke_root = smoke_work / "waa-eval"
    smoke_root.mkdir(parents=True)
    save_json(smoke_root / "git-source.json", source)
    smoke_env = {**environment, "DRAGON_TASK_WORK_DIR": str(smoke_work), "DRAGON_VAR_CASE_IDS": DEFAULT_IDS[0]}
    status = execute_stage(python, "prepare", smoke_root, smoke_env)
    if status == 0:
        status = execute_stage(python, "run", smoke_root, smoke_env)
    smoke_result = json.loads((smoke_root / "results.json").read_text(encoding="utf-8"))
    report["smoke"] = {"status": smoke_result["status"], "case_id": DEFAULT_IDS[0], "summary": smoke_result["summary"], "evidence_directory": "smoke-evidence"}
    shutil.copytree(smoke_root, root / "smoke-evidence", dirs_exist_ok=True)
    write_report(root, report)
    return status


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "invoke":
        if sys.stdin.buffer.read(1) != b"1":
            raise RuntimeError("Deployment process admission was not granted.")
        return subprocess.call(sys.argv[2:])  # noqa: S603 - Internal native-tool wrapper passes structured argv without a shell.
    work = bounded_root(os.environ["DRAGON_TASK_WORK_DIR"])
    root = work / "waa-eval"
    root.mkdir(exist_ok=True)
    mode = os.environ.get("DRAGON_VAR_OPERATION_MODE", "evaluate")
    report = {
        "suite": "waa-remote-deployment/v1",
        "status": "preparing",
        "tasks": [],
        "errors": [],
        "started_at": datetime.now(UTC).isoformat(),
        "execution_machine": socket.gethostname(),
        "bootstrap_python": sys.executable,
    }
    setup_lock = None
    active_check = "machine"
    if mode == "setup":
        from setup_report import new_checks

        report.update(
            {
                "suite": "fsq-devbox-setup/v1",
                "setup_checks": new_checks(),
                "execution_account": getpass.getuser(),
                "smoke": {"status": "pending" if os.environ.get("DRAGON_VAR_SETUP_SMOKE") == "true" else "not_requested"},
            }
        )

    def checked(key, detail):
        if mode == "setup":
            from setup_report import set_check

            set_check(report, key, "passed", detail)
            write_report(root, report)

    try:
        if mode not in {"evaluate", "setup"}:
            raise ValueError("The transported operation mode is invalid.")  # noqa: TRY301 - Deployment boundary persists blocked evidence.
        if os.environ.get("DRAGON_VAR_EXPECTED_HOST", "").casefold() not in {"", socket.gethostname().casefold()}:
            raise RuntimeError("This Task was dispatched to an unexpected host.")  # noqa: TRY301 - Deployment boundary persists blocked evidence.
        if mode == "evaluate":
            require_desktop()
        if os.environ.get("DRAGON_EXECUTION_MACHINE", "").casefold() == "local":
            raise RuntimeError("The portable deployment is intended for a remote Machine, not local UI execution.")  # noqa: TRY301 - Record routing failure as blocked.
        url, branch = source_inputs(os.environ["DRAGON_VAR_GIT_URL"], os.environ["DRAGON_VAR_GIT_BRANCH"])
        package = Path(__file__).parent
        if not shutil.which("git"):
            raise RuntimeError("Git is missing on the selected Machine.")  # noqa: TRY301 - Record missing prerequisite as blocked.
        if shutil.disk_usage(work).free < 2 * 1024**3:
            raise RuntimeError("At least 2 GiB free space is required for environment preparation.")  # noqa: TRY301 - Record failed prerequisite as blocked.
        parent = bounded_root(Path.home() / ".fsq-waa")
        if mode == "setup":
            guard = file_lock(parent / "setup.lock", "Another Setup Task is preparing this account. Wait for it to finish.")
            guard.__enter__()
            setup_lock = guard
            from runner import find_edge

            report["edge_executable"] = str(find_edge())
        checked("machine", f"{socket.gethostname()} / {getpass.getuser()}\uff1bWindows、Git 与磁盘前提已确认。")
        active_check = "package"
        manifest = json.loads((package / "package-manifest.json").read_text(encoding="utf-8"))
        for name, digest in manifest["files"].items():
            path = package / name
            if not path.resolve().is_relative_to(package.resolve()) or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                raise ValueError("A transported package file failed checksum validation.")  # noqa: TRY301 - Preserve checksum failure evidence.
        from dataset import DEFAULT_IDS, load_dataset

        inventory, _selected = load_dataset(package / "dataset", DEFAULT_IDS)
        checked("package", f"完整运行文件校验通过\uff1b固定测试集 {len(inventory)} 条 JSON 已到达。")
        evidence = root / "deployment"
        evidence.mkdir(exist_ok=True)
        active_check = "source"
        cache = parent / "git"
        git = GitCommands(evidence / "git", cache / "disabled-hooks")
        source = sync_checkout(url, branch, cache, git)
        source["role"] = "installed_runtime_source"
        source["runtime_note"] = "FSQ is installed from this exact commit's archive into a dedicated venv."
        save_json(root / "git-source.json", source)
        report["git_source"] = source
        checked("source", f"{branch} / {source['commit']}\uff1b{source['action']}。")
        report["status"] = "preparing"
        write_report(root, report)
        runtime_root = bounded_root(parent / "runtimes" / source["commit"][:12])
        runtime_root.mkdir(parents=True, exist_ok=True)
        active_check = "runtime"
        with file_lock(runtime_root / "prepare.lock", "Another deployment is preparing this source runtime."):
            python, runtime = deployment_runtime(source, runtime_root, evidence, git)
        save_json(root / "runtime.json", runtime)
        report["deployment"] = runtime
        if mode == "setup":
            owned_command([str(python), "-m", "pip", "check"], runtime_root, evidence, "setup-dependency-check")
        checked("runtime", f"FSQ {runtime['version']} 模块与源码驱动指纹一致\uff1b解释器 {python}。")
        environment = os.environ.copy()
        environment.update(
            {
                "PYTHONIOENCODING": "utf-8",
                "PYTHONUTF8": "1",
                "DRAGON_VAR_FSQ_PYTHON": str(python),
                "DRAGON_VAR_DATASET_DIR": str(package / "dataset"),
            }
        )
        environment.pop("PYTHONPATH", None)
        environment.pop("PYTHONHOME", None)
        print(f"Remote runtime ready: {socket.gethostname()}, source {source['commit']}, interpreter {python}", flush=True)
        if mode == "setup":
            from setup_acceptance import run_live

            write_report(root, report)
            result, _stdout = run_live([str(python), "-u", str(package / "setup_acceptance.py")], root, "setup-acceptance", 1560, environment=environment)
            if result["timed_out"]:
                raise RuntimeError("Setup acceptance or authorization exceeded its bounded deadline. Inspect the retained logs.")  # noqa: TRY301 - Persist timeout evidence.
            report = json.loads((root / "results.json").read_text(encoding="utf-8"))
            if result["exit_code"] != 0:
                return result["exit_code"]
            if report.get("status") != "setup_ready":
                raise RuntimeError("The Setup child exited without positive readiness evidence.")  # noqa: TRY301 - Fail closed and persist missing readiness.
            smoke = os.environ.get("DRAGON_VAR_SETUP_SMOKE", "false")
            if smoke not in {"true", "false"}:
                raise ValueError("setup-smoke must be true or false.")  # noqa: TRY301 - Invalid operator input produces blocked evidence.
            if smoke == "true":
                return run_setup_smoke(python, work, root, environment, source, report)
            write_report(root, report)
            return 0
        status = execute_stage(python, "prepare", root, environment)
        report = json.loads((root / "results.json").read_text(encoding="utf-8"))
        report.update({"git_source": source, "deployment": runtime, "execution_machine": socket.gethostname()})
        write_report(root, report)
        if status:
            return status
        status = execute_stage(python, "run", root, environment)
        report = json.loads((root / "results.json").read_text(encoding="utf-8"))
        report.update({"git_source": source, "deployment": runtime, "execution_machine": socket.gethostname()})
        write_report(root, report)
    except Exception as error:  # noqa: BLE001 - Top-level deployment boundary persists evidence and returns nonzero.
        if (root / "results.json").exists():
            report = json.loads((root / "results.json").read_text(encoding="utf-8"))
        report["status"] = "blocked"
        if mode == "setup":
            from setup_report import set_check

            active_check = next((item["id"] for item in report["setup_checks"] if item["status"] == "running"), active_check)
            set_check(report, active_check, "blocked", str(error))
        report.setdefault("errors", []).append({"type": type(error).__name__, "message": str(error)})
        (root / "deployment-error.log").write_text(traceback.format_exc(), encoding="utf-8")
        write_report(root, report)
        print(f"Remote deployment blocked: {error}", file=sys.stderr, flush=True)
        return 1
    else:
        return status
    finally:
        if setup_lock is not None:
            setup_lock.__exit__(None, None, None)


if __name__ == "__main__":
    raise SystemExit(main())
