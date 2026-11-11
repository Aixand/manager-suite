"""Windows file-sharing guards retained by inherited child handles."""
from contextlib import contextmanager
import ctypes as c
from ctypes import wintypes as w
import subprocess
import os

kernel = c.WinDLL('kernel32', use_last_error=True)
kernel.CreateFileW.argtypes = [w.LPCWSTR, w.DWORD, w.DWORD, c.c_void_p, w.DWORD, w.DWORD, w.HANDLE]
kernel.CreateFileW.restype = w.HANDLE
kernel.CloseHandle.argtypes = [w.HANDLE]
kernel.GetCurrentProcess.restype = w.HANDLE
kernel.DuplicateHandle.argtypes = [w.HANDLE, w.HANDLE, w.HANDLE, c.POINTER(w.HANDLE), w.DWORD, w.BOOL, w.DWORD]


@contextmanager
def guard(path, *, shared):
    # Readers deny write access. Existing administrative locks open for writing.
    path = os.path.abspath(path)
    if not path.startswith('\\\\?\\'):
        path = '\\\\?\\UNC\\' + path[2:] if path.startswith('\\\\') else '\\\\?\\' + path
    handle = kernel.CreateFileW(path, 0x80000000, 1 if shared else 0,
                               None, 4, 0x80, None)
    if handle == c.c_void_p(-1).value:
        error = c.get_last_error()
        if error in (32, 33):
            raise BlockingIOError('The environment is in use')
        raise c.WinError(error)
    try:
        yield handle
    finally:
        kernel.CloseHandle(handle)


@contextmanager
def child_options(handles):
    duplicates = []
    try:
        current = kernel.GetCurrentProcess()
        for handle in handles:
            duplicate = w.HANDLE()
            if not kernel.DuplicateHandle(current, handle, current, c.byref(duplicate), 0, True, 2):
                raise c.WinError(c.get_last_error())
            duplicates.append(duplicate.value)
        yield {'startupinfo': subprocess.STARTUPINFO(lpAttributeList={'handle_list': duplicates}),
               'close_fds': True}
    finally:
        for handle in duplicates:
            kernel.CloseHandle(handle)
