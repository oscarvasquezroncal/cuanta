from __future__ import annotations

import contextlib
import ctypes
import os
import signal
import subprocess
import sys
from ctypes import wintypes
from typing import Any

KILL_ON_JOB_CLOSE = 0x2000
EXTENDED_LIMIT_INFORMATION = 9
PROCESS_SET_QUOTA = 0x0100
PROCESS_TERMINATE = 0x0001
PROCESS_SUSPEND_RESUME = 0x0800
CREATE_SUSPENDED = 0x00000004


class _BasicLimits(ctypes.Structure):
    _fields_ = (
        ("PerProcessUserTimeLimit", ctypes.c_int64),
        ("PerJobUserTimeLimit", ctypes.c_int64),
        ("LimitFlags", wintypes.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", wintypes.DWORD),
        ("SchedulingClass", wintypes.DWORD),
    )


class _IoCounters(ctypes.Structure):
    _fields_ = tuple((name, ctypes.c_uint64) for name in ("a", "b", "c", "d", "e", "f"))


class _ExtendedLimits(ctypes.Structure):
    _fields_ = (
        ("BasicLimitInformation", _BasicLimits),
        ("IoInfo", _IoCounters),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    )


class ProcessResumeError(OSError):
    pass


def creation_flags() -> int:
    return CREATE_SUSPENDED if sys.platform == "win32" else 0


class ProcessTree:
    def __init__(self, process: subprocess.Popen[str], suspended: bool = False) -> None:
        self._process = process
        self._job: int | None = None
        if sys.platform == "win32":
            try:
                self._job = _attach_job(process.pid)
            finally:
                if suspended:
                    _resume(process)

    def close(self) -> None:
        if sys.platform == "win32":
            self._close_job()
        else:
            with contextlib.suppress(OSError):
                os.killpg(self._process.pid, signal.SIGTERM)

    def _close_job(self) -> None:
        if self._job is None:
            if self._process.poll() is None:
                self._process.kill()
            return
        kernel = _kernel()
        kernel.TerminateJobObject(self._job, 1)
        kernel.CloseHandle(self._job)
        self._job = None


def _kernel() -> Any:
    if sys.platform == "win32":
        return _load_kernel()
    raise OSError("kernel32 is only available on Windows")


def _load_kernel() -> Any:
    loader: Any = vars(ctypes)["WinDLL"]
    kernel = loader("kernel32", use_last_error=True)
    kernel.CreateJobObjectW.restype = wintypes.HANDLE
    kernel.CreateJobObjectW.argtypes = (ctypes.c_void_p, wintypes.LPCWSTR)
    kernel.SetInformationJobObject.argtypes = (
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
    )
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
    kernel.TerminateJobObject.argtypes = (wintypes.HANDLE, wintypes.UINT)
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    return kernel


def _ntdll() -> Any:
    if sys.platform == "win32":
        loader: Any = vars(ctypes)["WinDLL"]
        ntdll = loader("ntdll")
        ntdll.NtResumeProcess.restype = ctypes.c_long
        ntdll.NtResumeProcess.argtypes = (wintypes.HANDLE,)
        return ntdll
    raise OSError("ntdll is only available on Windows")


def _resume(process: subprocess.Popen[str]) -> None:
    kernel = _kernel()
    handle = kernel.OpenProcess(PROCESS_SUSPEND_RESUME, False, process.pid)
    if not handle:
        code = int(vars(ctypes)["get_last_error"]())
        process.kill()
        raise ProcessResumeError(f"cannot open process {process.pid} to resume it: winerror {code}")
    try:
        status = int(_ntdll().NtResumeProcess(handle))
    finally:
        kernel.CloseHandle(handle)
    if status < 0:
        process.kill()
        raise ProcessResumeError(
            f"NtResumeProcess failed for process {process.pid}: status {status & 0xFFFFFFFF:#010x}"
        )


def _attach_job(pid: int) -> int | None:
    kernel = _kernel()
    job = kernel.CreateJobObjectW(None, None)
    if not job:
        return None
    limits = _ExtendedLimits()
    limits.BasicLimitInformation.LimitFlags = KILL_ON_JOB_CLOSE
    configured = kernel.SetInformationJobObject(
        job, EXTENDED_LIMIT_INFORMATION, ctypes.byref(limits), ctypes.sizeof(limits)
    )
    handle = kernel.OpenProcess(PROCESS_SET_QUOTA | PROCESS_TERMINATE, False, pid)
    assigned = bool(handle) and configured and kernel.AssignProcessToJobObject(job, handle)
    if handle:
        kernel.CloseHandle(handle)
    if not assigned:
        kernel.CloseHandle(job)
        return None
    return int(job)
