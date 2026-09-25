from __future__ import annotations

import ctypes
import os
import sys
from ctypes import wintypes
from pathlib import Path

from cuanta.domain.shells import Shell, shell_from_name

SNAPSHOT_PROCESS = 0x00000002
QUERY_LIMITED_INFORMATION = 0x1000
MAX_DEPTH = 12
PASS_THROUGH = frozenset(
    {"python.exe", "pythonw.exe", "py.exe", "uv.exe", "uvx.exe", "cuanta.exe", "conhost.exe"}
)


class _ProcessEntry(ctypes.Structure):
    _fields_ = (
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.c_size_t),
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", ctypes.c_long),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", ctypes.c_wchar * 260),
    )


if sys.platform == "win32":

    def _native_table() -> dict[int, tuple[int, str]]:
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
        kernel.CreateToolhelp32Snapshot.argtypes = (wintypes.DWORD, wintypes.DWORD)
        kernel.Process32FirstW.argtypes = (wintypes.HANDLE, ctypes.POINTER(_ProcessEntry))
        kernel.Process32NextW.argtypes = (wintypes.HANDLE, ctypes.POINTER(_ProcessEntry))
        kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
        snapshot = kernel.CreateToolhelp32Snapshot(SNAPSHOT_PROCESS, 0)
        if not snapshot or snapshot == wintypes.HANDLE(-1).value:
            return {}
        table: dict[int, tuple[int, str]] = {}
        entry = _ProcessEntry()
        entry.dwSize = ctypes.sizeof(_ProcessEntry)
        try:
            more = kernel.Process32FirstW(snapshot, ctypes.byref(entry))
            while more:
                table[int(entry.th32ProcessID)] = (
                    int(entry.th32ParentProcessID),
                    entry.szExeFile,
                )
                more = kernel.Process32NextW(snapshot, ctypes.byref(entry))
        finally:
            kernel.CloseHandle(snapshot)
        return table

    def _creation_time(pid: int) -> int | None:
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        kernel.GetProcessTimes.argtypes = (
            wintypes.HANDLE,
            ctypes.POINTER(wintypes.FILETIME),
            ctypes.POINTER(wintypes.FILETIME),
            ctypes.POINTER(wintypes.FILETIME),
            ctypes.POINTER(wintypes.FILETIME),
        )
        kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
        handle = kernel.OpenProcess(QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return None
        created, exited, kernel_time, user_time = (wintypes.FILETIME() for _ in range(4))
        try:
            ok = kernel.GetProcessTimes(
                handle,
                ctypes.byref(created),
                ctypes.byref(exited),
                ctypes.byref(kernel_time),
                ctypes.byref(user_time),
            )
        finally:
            kernel.CloseHandle(handle)
        if not ok:
            return None
        return (int(created.dwHighDateTime) << 32) | int(created.dwLowDateTime)

else:

    def _native_table() -> dict[int, tuple[int, str]]:
        return _posix_table()

    def _creation_time(pid: int) -> int | None:
        raise OSError(f"creation time of process {pid} is only probed on Windows")


def process_table() -> dict[int, tuple[int, str]]:
    return _native_table()


def _posix_table() -> dict[int, tuple[int, str]]:
    import subprocess

    try:
        completed = subprocess.run(
            ["ps", "-A", "-o", "pid=,ppid=,comm="],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return {}
    table: dict[int, tuple[int, str]] = {}
    for line in completed.stdout.splitlines():
        parts = line.split(None, 2)
        if len(parts) == 3 and parts[0].isdigit() and parts[1].isdigit():
            table[int(parts[0])] = (int(parts[1]), Path(parts[2]).name)
    return table


def descendants(pid: int, table: dict[int, tuple[int, str]] | None = None) -> set[int]:
    processes = process_table() if table is None else table
    children: dict[int, list[int]] = {}
    for child, (parent, _) in processes.items():
        children.setdefault(parent, []).append(child)
    verify = table is None and sys.platform == "win32"
    born: dict[int, int | None] = {}

    def started(candidate: int) -> int | None:
        if candidate not in born:
            born[candidate] = _creation_time(candidate)
        return born[candidate]

    found: set[int] = set()
    frontier = [pid]
    while frontier:
        current = frontier.pop()
        for child in children.get(current, []):
            if child in found or child == pid:
                continue
            if verify and not _born_after(started(child), started(current)):
                continue
            found.add(child)
            frontier.append(child)
    return found


def _born_after(child: int | None, parent: int | None) -> bool:
    return child is not None and parent is not None and child >= parent


def ancestors(pid: int, table: dict[int, tuple[int, str]]) -> list[str]:
    names: list[str] = []
    current = pid
    for _ in range(MAX_DEPTH):
        record = table.get(current)
        if record is None:
            break
        parent, _name = record
        parent_record = table.get(parent)
        if parent_record is None or parent == current:
            break
        names.append(parent_record[1])
        current = parent
    return names


def detect_shell() -> Shell:
    override = os.environ.get("CUANTA_SHELL", "").strip().lower()
    if override:
        return shell_from_name(override)
    if sys.platform == "win32":
        detected = _windows_shell()
    else:
        shell = os.environ.get("SHELL", "")
        detected = shell_from_name(Path(shell).name) if shell else Shell.UNKNOWN
    return detected


def _windows_shell() -> Shell:
    for name in ancestors(os.getpid(), process_table()):
        lowered = name.lower()
        if lowered in PASS_THROUGH:
            continue
        return shell_from_name(lowered)
    return Shell.UNKNOWN
