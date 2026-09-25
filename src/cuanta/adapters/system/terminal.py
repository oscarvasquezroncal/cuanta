from __future__ import annotations

import ctypes
import os
import sys

from cuanta.adapters.system.shell import ancestors, process_table
from cuanta.domain.terminal import TerminalProbe

ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004
STD_OUTPUT_HANDLE = -11
CLASS_BUFFER = 256


if sys.platform == "win32":
    from ctypes import wintypes

    def _libraries() -> tuple[ctypes.CDLL, ctypes.CDLL]:
        loader = vars(ctypes)["WinDLL"]
        kernel = loader("kernel32", use_last_error=True)
        user = loader("user32", use_last_error=True)
        kernel.GetConsoleWindow.restype = wintypes.HWND
        kernel.GetStdHandle.restype = wintypes.HANDLE
        kernel.GetConsoleMode.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
        kernel.SetConsoleMode.argtypes = (wintypes.HANDLE, wintypes.DWORD)
        user.GetClassNameW.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
        return kernel, user

    def window_class() -> str:
        kernel, user = _libraries()
        handle = kernel.GetConsoleWindow()
        if not handle:
            return ""
        buffer = ctypes.create_unicode_buffer(CLASS_BUFFER)
        length = user.GetClassNameW(handle, buffer, CLASS_BUFFER)
        return buffer.value if length else ""

    def vt_enabled() -> bool | None:
        kernel, _ = _libraries()
        output = kernel.GetStdHandle(STD_OUTPUT_HANDLE)
        mode = wintypes.DWORD()
        if not kernel.GetConsoleMode(output, ctypes.byref(mode)):
            return None
        if mode.value & ENABLE_VIRTUAL_TERMINAL_PROCESSING:
            return True
        enabled = bool(
            kernel.SetConsoleMode(output, mode.value | ENABLE_VIRTUAL_TERMINAL_PROCESSING)
        )
        if enabled:
            kernel.SetConsoleMode(output, mode.value)
        return enabled

    def probe_terminal() -> TerminalProbe:
        try:
            names = tuple(ancestors(os.getpid(), process_table()))
        except OSError:
            names = ()
        return TerminalProbe(sys.platform, window_class(), vt_enabled(), names)

else:

    def window_class() -> str:
        return ""

    def vt_enabled() -> bool | None:
        return None

    def probe_terminal() -> TerminalProbe:
        return TerminalProbe(sys.platform)
