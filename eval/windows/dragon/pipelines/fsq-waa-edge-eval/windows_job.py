# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.
# Bounds all CLI descendants for this Windows-only evaluator, including after a timeout.
import ctypes
import subprocess
import time
from ctypes import wintypes


class BasicLimits(ctypes.Structure):
    _fields_ = [
        ("process_time", ctypes.c_longlong),
        ("job_time", ctypes.c_longlong),
        ("flags", wintypes.DWORD),
        ("minimum_ws", ctypes.c_size_t),
        ("maximum_ws", ctypes.c_size_t),
        ("active_limit", wintypes.DWORD),
        ("affinity", ctypes.c_size_t),
        ("priority", wintypes.DWORD),
        ("scheduling", wintypes.DWORD),
    ]


class IoCounters(ctypes.Structure):
    _fields_ = [(name, ctypes.c_ulonglong) for name in ("read_ops", "write_ops", "other_ops", "read_bytes", "write_bytes", "other_bytes")]


class ExtendedLimits(ctypes.Structure):
    _fields_ = [
        ("basic", BasicLimits),
        ("io", IoCounters),
        ("process_memory", ctypes.c_size_t),
        ("job_memory", ctypes.c_size_t),
        ("peak_process_memory", ctypes.c_size_t),
        ("peak_job_memory", ctypes.c_size_t),
    ]


class Accounting(ctypes.Structure):
    _fields_ = [
        ("user_time", ctypes.c_longlong),
        ("kernel_time", ctypes.c_longlong),
        ("period_user", ctypes.c_longlong),
        ("period_kernel", ctypes.c_longlong),
        ("page_faults", wintypes.DWORD),
        ("total", wintypes.DWORD),
        ("active", wintypes.DWORD),
        ("terminated", wintypes.DWORD),
    ]


class ProcessTree:
    def __init__(self):
        self.api = ctypes.WinDLL("kernel32", use_last_error=True)
        signatures = {
            "CreateJobObjectW": ([ctypes.c_void_p, wintypes.LPCWSTR], wintypes.HANDLE),
            "SetInformationJobObject": ([wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD], wintypes.BOOL),
            "AssignProcessToJobObject": ([wintypes.HANDLE, wintypes.HANDLE], wintypes.BOOL),
            "TerminateJobObject": ([wintypes.HANDLE, wintypes.UINT], wintypes.BOOL),
            "QueryInformationJobObject": ([wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p], wintypes.BOOL),
            "CloseHandle": ([wintypes.HANDLE], wintypes.BOOL),
        }
        for name, (arguments, result) in signatures.items():
            method = getattr(self.api, name)
            method.argtypes, method.restype = arguments, result
        self.handle = self.api.CreateJobObjectW(None, None)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        limits = ExtendedLimits()
        limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not self.api.SetInformationJobObject(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            error = ctypes.WinError(ctypes.get_last_error())
            self.api.CloseHandle(self.handle)
            raise error

    def assign(self, process):
        if not self.api.AssignProcessToJobObject(self.handle, int(process._handle)):
            raise ctypes.WinError(ctypes.get_last_error())

    def active(self):
        info = Accounting()
        if not self.api.QueryInformationJobObject(self.handle, 1, ctypes.byref(info), ctypes.sizeof(info), None):
            raise ctypes.WinError(ctypes.get_last_error())
        return info.active

    def close(self):
        try:
            active = self.active()
            if active and not self.api.TerminateJobObject(self.handle, 1):
                raise ctypes.WinError(ctypes.get_last_error())
            deadline = time.monotonic() + 10
            while self.active():
                if time.monotonic() >= deadline:
                    raise RuntimeError("The FSQ process tree did not settle. Do not retry this Task.")
                time.sleep(0.02)
            return active
        finally:
            if not self.api.CloseHandle(self.handle):
                raise ctypes.WinError(ctypes.get_last_error())


def run_owned(command, cwd, stdout_path, stderr_path, timeout, environment=None):
    tree = ProcessTree()
    process = None
    timed_out = False
    try:
        with stdout_path.open("wb") as out, stderr_path.open("wb") as err:
            process = subprocess.Popen(  # noqa: S603 - Structured caller argv; process admission stays owned by the Windows Job.
                command,
                cwd=cwd,
                stdin=subprocess.PIPE,
                stdout=out,
                stderr=err,
                creationflags=subprocess.CREATE_NO_WINDOW,
                env=environment,
            )
            # The wrapper cannot import FSQ or spawn descendants until admission.
            tree.assign(process)
            process.stdin.write(b"1")
            process.stdin.close()
            try:
                process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                timed_out = True
    finally:
        if process is not None and process.stdin and not process.stdin.closed:
            process.stdin.close()
        try:
            terminated = tree.close()
        finally:
            if process is not None:
                process.wait(timeout=5)
    return {
        "exit_code": process.returncode,
        "timed_out": timed_out,
        "terminated_processes": terminated,
    }
