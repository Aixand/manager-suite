"""Process birth identities prevent cancellation from signaling a reused PID."""

import os
from pathlib import Path
import subprocess
import sys


def identity(pid):
    if sys.platform.startswith("linux"):
        try:
            return Path(f"/proc/{pid}/stat").read_text().rpartition(")")[2].split()[19]
        except (OSError, IndexError):
            return None
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        kernel.GetProcessTimes.argtypes = (
            wintypes.HANDLE,
            *([ctypes.POINTER(wintypes.FILETIME)] * 4),
        )
        kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            return None
        try:
            created, exited, kernel_time, user_time = (
                wintypes.FILETIME() for _ in range(4)
            )
            if kernel.GetProcessTimes(
                handle,
                ctypes.byref(created),
                ctypes.byref(exited),
                ctypes.byref(kernel_time),
                ctypes.byref(user_time),
            ):
                return str(created.dwHighDateTime << 32 | created.dwLowDateTime)
        finally:
            kernel.CloseHandle(handle)
        return None
    try:
        value = subprocess.check_output(
            ["ps", "-p", str(pid), "-o", "lstart="], text=True, timeout=2
        ).strip()
        return value or None
    except (OSError, subprocess.SubprocessError):
        return None
