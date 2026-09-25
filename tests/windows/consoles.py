from __future__ import annotations

import ctypes
import subprocess
import sys
import time
from pathlib import Path

SW_HIDE = 0
STARTF_USESHOWWINDOW = 1
PSEUDOCONSOLE_ATTRIBUTE = 0x00020016
EXTENDED_STARTUPINFO = 0x00080000
CHILD = """
import json, os, sys
from cuanta.adapters.system.terminal import probe_terminal
from cuanta.domain.terminal import classify
report = classify(probe_terminal(), dict(os.environ))
probe = probe_terminal()
with open(sys.argv[1], "w", encoding="utf-8") as out:
    json.dump({"class": probe.window_class, "kind": report.kind.value}, out)
"""


def _wait(target: Path, seconds: float = 30.0) -> str:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if target.exists() and target.stat().st_size:
            time.sleep(0.1)
            return target.read_text(encoding="utf-8")
        time.sleep(0.1)
    return ""


if sys.platform == "win32":

    def run_in_conhost(target: Path) -> str:
        info = subprocess.STARTUPINFO()
        info.dwFlags |= STARTF_USESHOWWINDOW
        info.wShowWindow = SW_HIDE
        clean = {
            key: value
            for key, value in __import__("os").environ.items()
            if key not in {"WT_SESSION", "TERM_PROGRAM", "CUANTA_TERMINAL"}
        }
        process = subprocess.Popen(
            ["conhost.exe", sys.executable, "-c", CHILD, str(target)], startupinfo=info, env=clean
        )
        try:
            return _wait(target)
        finally:
            process.wait(timeout=30)

    def run_in_conpty(target: Path) -> str:
        from ctypes import wintypes

        class Coord(ctypes.Structure):
            _fields_ = (("X", wintypes.SHORT), ("Y", wintypes.SHORT))

        class StartupInfo(ctypes.Structure):
            _fields_ = (
                ("cb", wintypes.DWORD),
                ("lpReserved", wintypes.LPWSTR),
                ("lpDesktop", wintypes.LPWSTR),
                ("lpTitle", wintypes.LPWSTR),
                ("dwX", wintypes.DWORD),
                ("dwY", wintypes.DWORD),
                ("dwXSize", wintypes.DWORD),
                ("dwYSize", wintypes.DWORD),
                ("dwXCountChars", wintypes.DWORD),
                ("dwYCountChars", wintypes.DWORD),
                ("dwFillAttribute", wintypes.DWORD),
                ("dwFlags", wintypes.DWORD),
                ("wShowWindow", wintypes.WORD),
                ("cbReserved2", wintypes.WORD),
                ("lpReserved2", ctypes.c_void_p),
                ("hStdInput", wintypes.HANDLE),
                ("hStdOutput", wintypes.HANDLE),
                ("hStdError", wintypes.HANDLE),
            )

        class StartupInfoEx(ctypes.Structure):
            _fields_ = (("StartupInfo", StartupInfo), ("lpAttributeList", ctypes.c_void_p))

        class ProcessInformation(ctypes.Structure):
            _fields_ = (
                ("hProcess", wintypes.HANDLE),
                ("hThread", wintypes.HANDLE),
                ("dwProcessId", wintypes.DWORD),
                ("dwThreadId", wintypes.DWORD),
            )

        kernel = vars(ctypes)["WinDLL"]("kernel32", use_last_error=True)
        handle = ctypes.POINTER(wintypes.HANDLE)
        kernel.CreatePipe.argtypes = (handle, handle, ctypes.c_void_p, wintypes.DWORD)
        kernel.CreatePseudoConsole.argtypes = (
            Coord,
            wintypes.HANDLE,
            wintypes.HANDLE,
            wintypes.DWORD,
            ctypes.POINTER(ctypes.c_void_p),
        )
        kernel.CreatePseudoConsole.restype = ctypes.c_long
        kernel.InitializeProcThreadAttributeList.argtypes = (
            ctypes.c_void_p,
            wintypes.DWORD,
            wintypes.DWORD,
            ctypes.POINTER(ctypes.c_size_t),
        )
        kernel.UpdateProcThreadAttribute.argtypes = (
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.c_size_t,
            ctypes.c_void_p,
            ctypes.c_size_t,
            ctypes.c_void_p,
            ctypes.c_void_p,
        )
        kernel.CreateProcessW.argtypes = (
            wintypes.LPCWSTR,
            wintypes.LPWSTR,
            ctypes.c_void_p,
            ctypes.c_void_p,
            wintypes.BOOL,
            wintypes.DWORD,
            ctypes.c_void_p,
            wintypes.LPCWSTR,
            ctypes.c_void_p,
            ctypes.POINTER(ProcessInformation),
        )
        kernel.ClosePseudoConsole.argtypes = (ctypes.c_void_p,)
        kernel.TerminateProcess.argtypes = (wintypes.HANDLE, wintypes.UINT)
        kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
        in_read, in_write, out_read, out_write = (wintypes.HANDLE() for _ in range(4))
        kernel.CreatePipe(ctypes.byref(in_read), ctypes.byref(in_write), None, 0)
        kernel.CreatePipe(ctypes.byref(out_read), ctypes.byref(out_write), None, 0)
        console = ctypes.c_void_p()
        if kernel.CreatePseudoConsole(Coord(80, 25), in_read, out_write, 0, ctypes.byref(console)):
            return ""
        size = ctypes.c_size_t()
        kernel.InitializeProcThreadAttributeList(None, 1, 0, ctypes.byref(size))
        attributes = ctypes.create_string_buffer(size.value)
        kernel.InitializeProcThreadAttributeList(attributes, 1, 0, ctypes.byref(size))
        kernel.UpdateProcThreadAttribute(
            attributes,
            0,
            PSEUDOCONSOLE_ATTRIBUTE,
            console,
            ctypes.sizeof(ctypes.c_void_p),
            None,
            None,
        )
        startup = StartupInfoEx()
        startup.StartupInfo.cb = ctypes.sizeof(StartupInfoEx)
        startup.lpAttributeList = ctypes.cast(attributes, ctypes.c_void_p)
        info = ProcessInformation()
        script = target.with_suffix(".py")
        script.write_text(CHILD, encoding="utf-8")
        command = ctypes.create_unicode_buffer(f'"{sys.executable}" "{script}" "{target}"')
        created = kernel.CreateProcessW(
            None,
            command,
            None,
            None,
            False,
            EXTENDED_STARTUPINFO,
            None,
            None,
            ctypes.byref(startup),
            ctypes.byref(info),
        )
        if not created:
            kernel.ClosePseudoConsole(console)
            return ""
        try:
            return _wait(target)
        finally:
            kernel.TerminateProcess(info.hProcess, 0)
            kernel.ClosePseudoConsole(console)
            for item in (info.hProcess, info.hThread, in_read, in_write, out_read, out_write):
                kernel.CloseHandle(item)

else:

    def run_in_conhost(target: Path) -> str:
        return ""

    def run_in_conpty(target: Path) -> str:
        return ""
