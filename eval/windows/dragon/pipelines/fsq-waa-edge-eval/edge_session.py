# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.
# Owns private Edge processes and process-scoped UIA targeting for local WAA evaluations.
import ctypes
import json
import subprocess
import sys
import time
from pathlib import Path

FLAGS = [
    "--no-first-run",
    "--no-default-browser-check",
    "--disable-background-mode",
    "--disable-extensions",
    "--force-renderer-accessibility",
    "--disable-features=msEdgeSidebarV2,msEdgeSidebar,msEdgeFirstRunExperience,msImplicitSignin",
    "--window-size=1280,900",
    "--window-position=0,0",
    "--lang=en-US",
]


def bounded_root(path):
    root = Path(path).resolve()
    if len(str(root)) > 110 or any(c in str(root) for c in '"\r\n]'):
        raise ValueError("Choose an evaluation root of at most 110 characters without output-marker characters.")
    return root


def require_desktop():
    if sys.platform != "win32":
        raise RuntimeError("Run this Pipeline on an unlocked Windows Machine.")
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.OpenInputDesktop.argtypes = [ctypes.c_ulong, ctypes.c_bool, ctypes.c_ulong]
    user32.OpenInputDesktop.restype = ctypes.c_void_p
    desktop = user32.OpenInputDesktop(0, False, 0x0100)
    if not desktop:
        raise RuntimeError("The Windows desktop is locked or unavailable. Unlock the executing Machine.")
    try:
        if not user32.SwitchDesktop(ctypes.c_void_p(desktop)):
            raise RuntimeError("The executing Windows desktop is not available for UI automation.")
    finally:
        user32.CloseDesktop(ctypes.c_void_p(desktop))


def require_screen_capture():
    from PIL import ImageGrab

    try:
        image = ImageGrab.grab(bbox=(0, 0, 1, 1))
        image.close()
    except OSError as error:
        raise RuntimeError(
            "The remote desktop cannot capture screen pixels. Connect to the executing Dev Box with Remote Desktop, keep it unlocked and do not minimize the connection, then start a fresh Task."
        ) from error


def desktop_lock(parent):
    from locks import file_lock

    return file_lock(parent / "desktop.lock", "Another WAA evaluation owns this account's desktop. Wait for it to finish.")


def edge_arguments(profile):
    return [f"--user-data-dir={profile}", "--profile-directory=Default", *FLAGS, "about:blank"]


def visible_owned_handle(pid):
    import win32gui
    import win32process

    handles = []

    def collect(handle, _context):
        if win32gui.IsWindowVisible(handle) and win32process.GetWindowThreadProcessId(handle)[1] == pid and win32gui.GetClassName(handle) in {"Chrome_WidgetWin_1", "#32770"}:
            handles.append(handle)

    win32gui.EnumWindows(collect, None)
    if not handles:
        raise RuntimeError("The owned Edge process has no visible application window yet.")
    foreground = win32gui.GetForegroundWindow()
    return foreground if foreground in handles else handles[0]


class EdgeSession:
    def __init__(self, executable, profile):
        self.executable = Path(executable).resolve(strict=True)
        self.profile = bounded_root(profile)
        self.process = None
        self.job = None
        self.log = None

    def start(self):
        from pywinauto import Application
        from windows_job import ProcessTree

        if self.process is not None:
            raise RuntimeError("The owned Edge process is already running.")
        self.profile.mkdir(parents=True, exist_ok=True)
        self.log = (self.profile.parent / "edge-process.log").open("ab")
        self.job = ProcessTree()
        wrapper = [sys.executable, "-u", str(Path(__file__).resolve()), "launch", str(self.executable), *edge_arguments(self.profile)]
        self.process = subprocess.Popen(  # noqa: S603 - Fixed interpreter/wrapper argv, no shell; Job admission owns the child.
            wrapper,
            stdin=subprocess.PIPE,
            stdout=self.log,
            stderr=self.log,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        try:
            self.job.assign(self.process)
            self.process.stdin.write(b"1")
            self.process.stdin.close()
            deadline = time.monotonic() + 40
            last_error = None
            while time.monotonic() < deadline:
                if self.process.poll() is not None:
                    raise RuntimeError("The private Edge launcher exited before its UI became ready.")  # noqa: TRY301 - The enclosing boundary must clean up the owned Job.
                pid_file = self.profile.parent / "edge-pid.json"
                if pid_file.exists():
                    pid = json.loads(pid_file.read_text())["pid"]
                    try:
                        app = Application(backend="uia").connect(process=pid, timeout=1)
                        window = app.window(handle=visible_owned_handle(pid))
                        if window.is_visible() and window.is_enabled():
                            self.pid = pid
                            self.app = app
                            window.set_focus()
                            return
                    except (RuntimeError, TimeoutError) as error:
                        last_error = error
                time.sleep(0.25)
            raise TimeoutError(f"The private Edge window did not become ready: {last_error}")  # noqa: TRY301 - Preserve cleanup on readiness timeout.
        except BaseException as error:
            try:
                self.stop()
            except Exception as cleanup_error:  # noqa: BLE001 - Annotate any cleanup failure, then re-raise the original failure.
                error.add_note(f"Cleanup also failed: {cleanup_error}")
            raise

    def snapshot(self, path):
        self.window().capture_as_image().save(path)

    def window(self):
        return self.app.window(handle=visible_owned_handle(self.pid))

    def stop(self):
        failure = None
        try:
            if self.process is not None and self.process.poll() is None:
                if self.process.stdin and not self.process.stdin.closed:
                    self.process.stdin.close()
                try:
                    if hasattr(self, "app"):
                        self.window().close()
                    self.process.wait(timeout=12)
                except (RuntimeError, TimeoutError, subprocess.TimeoutExpired) as error:
                    failure = error
            if self.job is not None:
                deadline = time.monotonic() + 3
                while self.job.active() and time.monotonic() < deadline:
                    time.sleep(0.05)
                forced = self.job.close()
                self.job = None
                if forced and failure is None:
                    failure = RuntimeError(f"Forced cleanup of {forced} owned processes after the Edge launcher exited.")
            if self.process is not None:
                self.process.wait(timeout=5)
        finally:
            self.process = None
            if self.log:
                self.log.close()
                self.log = None
        if failure:
            raise RuntimeError("The test browser required forced cleanup; its persisted result is not reliable.") from failure


def install_process_scope(pid, profile, executable, error_path=None):
    import win32api
    import win32com.client
    import win32event
    import win32process

    from fsq_agent.drivers.windows._pywinauto import PywinautoWindowsDriver

    expected_profile = f"--user-data-dir={Path(profile).resolve()}"
    handle = win32api.OpenProcess(0x100000 | 0x410, False, pid)
    if Path(win32process.GetModuleFileNameEx(handle, 0)).resolve() != Path(executable).resolve():
        raise RuntimeError("The target PID does not belong to the selected Edge executable.")
    processes = list(win32com.client.GetObject("winmgmts:").ExecQuery(f"SELECT CommandLine FROM Win32_Process WHERE ProcessId = {int(pid)}"))  # noqa: S608 - WMI predicate contains only an integer-converted PID.
    if len(processes) != 1 or not processes[0].CommandLine:
        raise RuntimeError("The target Edge command line could not be established.")
    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    shell32.CommandLineToArgvW.argtypes = [ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_int)]
    shell32.CommandLineToArgvW.restype = ctypes.POINTER(ctypes.c_wchar_p)
    count = ctypes.c_int()
    arguments = shell32.CommandLineToArgvW(processes[0].CommandLine, ctypes.byref(count))
    if not arguments:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        values = [arguments[index] for index in range(count.value)]
    finally:
        ctypes.windll.kernel32.LocalFree(ctypes.cast(arguments, ctypes.c_void_p))
    if expected_profile not in values:
        raise RuntimeError("The target Edge PID does not own the private test Profile.")

    def owned_window(driver, *, wait=False, wait_for="exists visible"):
        from pywinauto import Application

        if win32event.WaitForSingleObject(handle, 0) != win32event.WAIT_TIMEOUT:
            if error_path:
                Path(error_path).write_text(json.dumps({"reason": "owned_edge_exited", "pid": pid}), encoding="utf-8")
            raise RuntimeError("The owned Edge process changed. UI execution has stopped.")
        connected = Application(backend=driver.backend_kind).connect(process=pid, timeout=5)
        window = connected.window(handle=visible_owned_handle(pid))
        window.wait(wait_for, timeout=10 if wait else 3)
        driver._app = connected
        return window

    def launch_owned(driver, params):
        if params.extra_args:
            raise RuntimeError("Extra application arguments are not allowed in this isolated evaluation.")
        window = owned_window(driver, wait=True, wait_for="exists visible enabled")
        window.set_focus()
        return driver._passed({"already_started": True, "owned_process": pid})

    PywinautoWindowsDriver._resolve_main_window = owned_window
    PywinautoWindowsDriver._launch_app = launch_owned
    PywinautoWindowsDriver._kill_app = detach_owned


def detach_owned(driver):
    driver._app = None
    return driver._passed({"detached": True, "application_owner": "evaluation-runner"})


if __name__ == "__main__":
    print("Private Edge launcher awaiting process admission.", flush=True)
    if sys.argv[1] != "launch" or sys.stdin.buffer.read(1) != b"1":
        raise SystemExit("The evaluator did not grant Edge process admission.")
    command = sys.argv[2:]
    profile_argument = next(value for value in command if value.startswith("--user-data-dir="))
    profile = Path(profile_argument.split("=", 1)[1])
    print("Starting the private Edge process.", flush=True)
    child = subprocess.Popen(command)  # noqa: S603 - Validated Edge executable and owned profile argv, never shell text.
    print(f"Private Edge process created: {child.pid}", flush=True)
    (profile.parent / "edge-pid.json").write_text(json.dumps({"pid": child.pid}), encoding="utf-8")
    raise SystemExit(child.wait())
