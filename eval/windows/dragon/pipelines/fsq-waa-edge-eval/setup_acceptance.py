# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.
# Verifies Provider and native desktop readiness without executing scoring Cases by default.
import getpass
import json
import os
import sys
import time
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from dataset import seed_profile
from edge_session import EdgeSession, bounded_root, desktop_lock, install_process_scope, require_desktop, require_screen_capture
from reporting import save_json, write_report
from runner import find_edge, fsq_call
from setup_report import set_check, setup_ready
from windows_job import run_owned


def run_live(arguments, root, label, timeout, environment=None):
    wrapper = [sys.executable, "-u", str(Path(__file__).resolve()), "invoke", *arguments]
    stdout, stderr = root / f"{label}.stdout.log", root / f"{label}.stderr.log"
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(run_owned, wrapper, Path(__file__).parent, stdout, stderr, timeout, environment=environment)
        position = 0
        while True:
            if stdout.exists():
                with stdout.open("r", encoding="utf-8", errors="replace") as stream:
                    stream.seek(position)
                    chunk = stream.read()
                    position = stream.tell()
                if chunk:
                    print(chunk, end="", flush=True)
            if future.done():
                result = future.result()
                break
            time.sleep(0.2)
    save_json(root / f"{label}.process.json", result)
    return result, stdout


def read_provider(root):
    response = fsq_call(root, root, "setup-provider", ["providers", "status"], "providers.status")
    if response["protocol_status"] != "valid" or response.get("record", {}).get("type") != "result":
        raise RuntimeError("The remote FSQ Provider status could not be read. Inspect setup-provider evidence.")
    return response["record"]["result"]


def needs_authorization(provider, connection):
    if not provider.get("configured"):
        return True
    return provider.get("provider") == "github_copilot" and connection.get("status_code") in {401, 403}


def test_connection(root, label):
    result, stdout = run_live([sys.executable, "-u", str(Path(__file__).resolve()), "provider-test"], root, label, 120)
    if result["timed_out"]:
        raise RuntimeError("The actual Provider request timed out. No model or credential fallback was attempted.")
    if result["exit_code"] != 0:
        raise RuntimeError(f"The Provider connection helper failed. Inspect {label}.stderr.log.")
    record = json.loads(stdout.read_text(encoding="utf-8"))
    if not isinstance(record, dict) or record.get("status") not in {"ready", "error"}:
        raise ValueError("The Provider connection test returned an invalid result.")
    return record


def provider_ready(root, authorize, model):
    provider = read_provider(root)
    if provider.get("configured"):
        connection = test_connection(root, "provider-connection")
        if connection["status"] == "ready":
            return {**provider, "connection": connection, "authorization_performed": False}
    else:
        connection = {"status": "error", "message": "No Provider is configured."}
    if not needs_authorization(provider, connection) or not authorize:
        raise RuntimeError(
            connection.get("message", "The saved Provider is not usable.")
            + " Start Setup with authorize-provider enabled only for missing/GitHub authorization; another configured Provider will not be replaced."
        )
    print("Remote FSQ authorization is required. Complete the official device login shown below.", flush=True)
    authorization_model = provider.get("model") or model
    result, _stdout = run_live([sys.executable, "-u", str(Path(__file__).resolve()), "authorize", authorization_model], root, "provider-authorization", 960)
    if result["timed_out"] or result["exit_code"] != 0:
        raise RuntimeError("Remote authorization did not complete in time or was denied. Credentials were not copied; start a fresh Setup Task.")
    provider = read_provider_after_auth(root)
    connection = test_connection(root, "provider-connection-after-auth")
    if connection["status"] != "ready":
        raise RuntimeError(connection.get("message", "Authorization returned but the actual Provider request still failed."))
    return {**provider, "connection": connection, "authorization_performed": True}


def read_provider_after_auth(root):
    response = fsq_call(root, root, "setup-provider-after-auth", ["providers", "status"], "providers.status")
    record = response.get("record", {})
    if response["protocol_status"] != "valid" or record.get("type") != "result":
        raise RuntimeError("Authorization completed but Provider status could not be confirmed.")
    return record["result"]


def window_acceptance(root, executable):
    from fsq_agent.drivers.windows._pywinauto import PywinautoWindowsDriver
    from fsq_agent.models import WindowsPressKeyParams, WindowsUiSnapshotParams

    parent = bounded_root(Path.home() / ".fsq-waa")
    with desktop_lock(parent):
        attempt = parent / ("setup-" + uuid.uuid4().hex[:8])
        attempt.mkdir()
        seed_profile(attempt / "profile", attempt / "downloads")
        session = EdgeSession(executable, attempt / "profile")
        try:
            session.start()
            install_process_scope(session.pid, session.profile, session.executable)
            driver = PywinautoWindowsDriver(app_path=str(session.executable))
            try:
                context = driver.context()
                for key in ("^l", "{ESC}"):
                    response = driver.press_key(WindowsPressKeyParams(key=key))
                    if response.get("status") != "passed":
                        raise RuntimeError("The owned test window did not accept the native keyboard probe.")
                save_json(root / "setup-window-snapshot.json", driver.ui_snapshot(WindowsUiSnapshotParams()))
                (root / "setup-before.png").write_bytes(driver.screenshot())
            finally:
                driver.close()
            session.snapshot(root / "setup-after-driver-close.png")
        finally:
            session.stop()
        return {"pid": session.pid, "profile": str(attempt / "profile"), "context": context, "native_keyboard_probe": "passed", "driver_detached_without_kill": True, "cleanup": "settled"}


def accept(root, report, authorize=True, model="gpt-5.6-sol-fast"):
    report["execution_account"] = getpass.getuser()
    current = "provider"
    try:
        set_check(report, current, "running", "检查保存配置\uff0c并发送一次 FSQ 官方最小模型连接请求。")
        write_report(root, report)
        report["provider"] = provider_ready(root, authorize, model)
        set_check(report, current, "passed", f"{report['provider']['provider']} / {report['provider']['model']} 实际请求已返回。")
        current = "desktop"
        set_check(report, current, "running", "检查交互桌面与实际截屏能力。")
        write_report(root, report)
        require_desktop()
        require_screen_capture()
        set_check(report, current, "passed", "交互桌面可用\uff0c截屏探测成功。")
        current = "window"
        set_check(report, current, "running", "启动独立 Edge\uff0c验证 FSQ 驱动连接、取图、断开和正常关闭。")
        write_report(root, report)
        report["window_acceptance"] = window_acceptance(root, find_edge())
        set_check(report, current, "passed", "独立窗口取图成功\uff0c驱动关闭未误杀应用\uff0cRunner 正常关闭并释放锁。")
        current = "record"
        set_check(report, current, "passed", "保存本次环境准备记录\uff1b后续评测仍会重新检查授权与桌面。")
        if not setup_ready(report):
            raise RuntimeError("A prerequisite did not pass; Setup cannot mark this Machine ready.")  # noqa: TRY301 - Persist blocked readiness without a success fallback.
        report["status"] = "setup_ready"
        report["finished_at"] = datetime.now(UTC).isoformat()
        marker = bounded_root(Path.home() / ".fsq-waa") / "setup-ready.json"
        report["readiness_record"] = str(marker)
        save_json(marker, report)
    except Exception as error:  # noqa: BLE001 - Acceptance boundary records failure and returns blocked evidence.
        set_check(report, current, "blocked", str(error))
        report["status"] = "blocked"
        report.setdefault("errors", []).append({"type": type(error).__name__, "message": str(error)})
        (root / "setup-acceptance-error.log").write_text(traceback.format_exc(), encoding="utf-8")
        return 1
    else:
        return 0
    finally:
        write_report(root, report)


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "invoke":
        if sys.stdin.buffer.read(1) != b"1":
            raise RuntimeError("Setup child process admission was not granted.")
        import subprocess

        return subprocess.call(sys.argv[2:])  # noqa: S603 - Internal native-tool wrapper passes structured argv without a shell.
    if len(sys.argv) > 1 and sys.argv[1] == "authorize":
        from fsq_agent.adapters.cli import main as fsq_main

        fsq_main(args=["providers", "configure", "github_copilot", "--model", sys.argv[2]], prog_name="fsq")
        return 0
    if len(sys.argv) > 1 and sys.argv[1] == "provider-test":
        from fsq_agent.models import ConfigurationError
        from fsq_agent.providers import test_model_provider_connection

        try:
            result = test_model_provider_connection()
            print(json.dumps({"status": "ready", **asdict(result)}), flush=True)
        except ConfigurationError as error:
            print(json.dumps({"status": "error", "message": str(error), "status_code": error.context.get("status_code")}), flush=True)
        return 0
    root = Path(os.environ["DRAGON_TASK_WORK_DIR"]) / "waa-eval"
    report = json.loads((root / "results.json").read_text(encoding="utf-8"))
    flag = os.environ.get("DRAGON_VAR_AUTHORIZE_PROVIDER", "true")
    if flag not in {"true", "false"}:
        raise ValueError("authorize-provider must be true or false.")
    model = os.environ.get("DRAGON_VAR_SETUP_MODEL", "gpt-5.6-sol-fast")
    return accept(root, report, authorize=flag == "true", model=model)


if __name__ == "__main__":
    raise SystemExit(main())
