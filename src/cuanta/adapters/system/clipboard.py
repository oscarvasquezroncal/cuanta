from __future__ import annotations

import ctypes
import sys

CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002


if sys.platform == "win32":
    from ctypes import wintypes

    def _libraries() -> tuple[ctypes.CDLL, ctypes.CDLL]:
        loader = vars(ctypes)["WinDLL"]
        user = loader("user32", use_last_error=True)
        kernel = loader("kernel32", use_last_error=True)
        user.OpenClipboard.argtypes = (wintypes.HWND,)
        user.OpenClipboard.restype = wintypes.BOOL
        user.EmptyClipboard.restype = wintypes.BOOL
        user.SetClipboardData.argtypes = (wintypes.UINT, wintypes.HANDLE)
        user.SetClipboardData.restype = wintypes.HANDLE
        user.CloseClipboard.restype = wintypes.BOOL
        kernel.GlobalAlloc.argtypes = (wintypes.UINT, ctypes.c_size_t)
        kernel.GlobalAlloc.restype = wintypes.HGLOBAL
        kernel.GlobalLock.argtypes = (wintypes.HGLOBAL,)
        kernel.GlobalLock.restype = wintypes.LPVOID
        kernel.GlobalUnlock.argtypes = (wintypes.HGLOBAL,)
        kernel.GlobalFree.argtypes = (wintypes.HGLOBAL,)
        return user, kernel

    def copy_native(text: str) -> bool:
        user, kernel = _libraries()
        data = text.encode("utf-16-le") + b"\x00\x00"
        if not user.OpenClipboard(None):
            return False
        try:
            user.EmptyClipboard()
            handle = kernel.GlobalAlloc(GMEM_MOVEABLE, len(data))
            if not handle:
                return False
            pointer = kernel.GlobalLock(handle)
            if not pointer:
                kernel.GlobalFree(handle)
                return False
            ctypes.memmove(pointer, data, len(data))
            kernel.GlobalUnlock(handle)
            if not user.SetClipboardData(CF_UNICODETEXT, handle):
                kernel.GlobalFree(handle)
                return False
            return True
        finally:
            user.CloseClipboard()

else:

    def copy_native(_text: str) -> bool:
        return False
