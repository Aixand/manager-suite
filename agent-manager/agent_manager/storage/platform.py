"""Small OS adapters, also importable by the standalone Carrier hook."""
import errno
import os

if os.name == 'nt':
    import ctypes
    from ctypes import wintypes

    _kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    _kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    _kernel32.OpenProcess.restype = wintypes.HANDLE
    _kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    _kernel32.WaitForSingleObject.restype = wintypes.DWORD
    _kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    _kernel32.CloseHandle.restype = wintypes.BOOL


def secure_file(stream):
    """Protect the created object by handle, even if a parent path is renamed."""
    if os.name == 'nt':
        from aixand_filesystem import secure_file as protect
        protect(stream)


def lock_file(stream, *, wait=True, shared=False):
    """Acquire a lock, released when the caller closes the file."""
    from aixand_filesystem import lock_file as acquire
    acquire(stream, blocking=wait, shared=shared)


def check_process(pid):
    """Raise ProcessLookupError for an exited PID without sending a signal."""
    if os.name != 'nt':
        os.kill(pid, 0)
        return
    if not isinstance(pid, int) or not 0 < pid <= 0xffffffff:
        raise ProcessLookupError(errno.ESRCH, 'Invalid process ID')
    # os.kill(pid, 0) terminates processes on Windows. A zero-timeout wait on a
    # SYNCHRONIZE handle instead distinguishes running and exited processes.
    handle = _kernel32.OpenProcess(0x00100000, False, pid)
    if not handle:
        error = ctypes.get_last_error()
        if error == 87:  # ERROR_INVALID_PARAMETER: no such PID
            raise ProcessLookupError(errno.ESRCH, 'Process does not exist')
        raise ctypes.WinError(error)
    try:
        status = _kernel32.WaitForSingleObject(handle, 0)
        if status == 0:  # WAIT_OBJECT_0: process has exited
            raise ProcessLookupError(errno.ESRCH, 'Process has exited')
        if status != 258:  # WAIT_TIMEOUT: process is still running
            raise ctypes.WinError(ctypes.get_last_error())
    finally:
        _kernel32.CloseHandle(handle)
