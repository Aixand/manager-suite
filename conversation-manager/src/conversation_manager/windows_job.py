"""Stop worker-owned native children when the worker exits."""
import ctypes as c
from ctypes import wintypes as w
import time


class BasicLimits(c.Structure):
    _fields_ = [('process_time', c.c_int64), ('job_time', c.c_int64),
                ('flags', w.DWORD), ('minimum', c.c_size_t), ('maximum', c.c_size_t),
                ('active', w.DWORD), ('affinity', c.c_size_t),
                ('priority', w.DWORD), ('scheduling', w.DWORD)]


class Limits(c.Structure):
    _fields_ = [('basic', BasicLimits), ('io', c.c_uint64 * 6),
                ('process_memory', c.c_size_t), ('job_memory', c.c_size_t),
                ('peak_process', c.c_size_t), ('peak_job', c.c_size_t)]


class Accounting(c.Structure):
    _fields_ = [('times', c.c_int64 * 4), ('faults', w.DWORD),
                ('total', w.DWORD), ('active', w.DWORD), ('terminated', w.DWORD)]


class ThreadEntry(c.Structure):
    _fields_ = [('size', w.DWORD), ('usage', w.DWORD), ('thread', w.DWORD),
                ('owner', w.DWORD), ('priority', w.LONG), ('delta', w.LONG), ('flags', w.DWORD)]


def resume_process(pid):
    """Resume the initial thread after assigning the suspended child to its job."""
    k = c.WinDLL('kernel32', use_last_error=True)
    k.CreateToolhelp32Snapshot.argtypes = [w.DWORD, w.DWORD]
    k.CreateToolhelp32Snapshot.restype = w.HANDLE
    k.Thread32First.argtypes = k.Thread32Next.argtypes = [w.HANDLE, c.POINTER(ThreadEntry)]
    k.OpenThread.argtypes = [w.DWORD, w.BOOL, w.DWORD]
    k.OpenThread.restype = w.HANDLE
    k.ResumeThread.argtypes = [w.HANDLE]
    k.ResumeThread.restype = w.DWORD
    k.CloseHandle.argtypes = [w.HANDLE]
    snapshot = k.CreateToolhelp32Snapshot(4, 0)
    if snapshot == c.c_void_p(-1).value:
        raise c.WinError(c.get_last_error())
    try:
        entry = ThreadEntry()
        entry.size = c.sizeof(entry)
        found = k.Thread32First(snapshot, c.byref(entry))
        while found:
            if entry.owner == pid:
                thread = k.OpenThread(2, False, entry.thread)
                if not thread:
                    raise c.WinError(c.get_last_error())
                try:
                    if k.ResumeThread(thread) == 0xffffffff:
                        raise c.WinError(c.get_last_error())
                finally:
                    k.CloseHandle(thread)
                return
            found = k.Thread32Next(snapshot, c.byref(entry))
        raise ProcessLookupError('The suspended native thread is unavailable')
    finally:
        k.CloseHandle(snapshot)


class ProcessJob:
    def __init__(self, pid):
        self.kernel = k = c.WinDLL('kernel32', use_last_error=True)
        k.CreateJobObjectW.argtypes = [c.c_void_p, w.LPCWSTR]
        k.CreateJobObjectW.restype = w.HANDLE
        k.SetInformationJobObject.argtypes = [w.HANDLE, c.c_int, c.c_void_p, w.DWORD]
        k.AssignProcessToJobObject.argtypes = [w.HANDLE, w.HANDLE]
        k.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
        k.OpenProcess.restype = w.HANDLE
        k.CloseHandle.argtypes = [w.HANDLE]
        k.TerminateJobObject.argtypes = [w.HANDLE, w.UINT]
        k.QueryInformationJobObject.argtypes = [w.HANDLE, c.c_int, c.c_void_p, w.DWORD, c.c_void_p]
        self.handle = k.CreateJobObjectW(None, None)
        if not self.handle:
            raise c.WinError(c.get_last_error())
        process = None
        try:
            limits = Limits()
            limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            if not k.SetInformationJobObject(self.handle, 9, c.byref(limits), c.sizeof(limits)):
                raise c.WinError(c.get_last_error())
            process = k.OpenProcess(0x0101, False, pid)
            if not process or not k.AssignProcessToJobObject(self.handle, process):
                raise c.WinError(c.get_last_error())
        except BaseException:
            self.close()
            raise
        finally:
            if process:
                k.CloseHandle(process)

    def close(self):
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None

    def terminate(self):
        if not self.kernel.TerminateJobObject(self.handle, 1):
            raise c.WinError(c.get_last_error())
        deadline = time.monotonic() + 8
        while True:
            accounting = Accounting()
            if not self.kernel.QueryInformationJobObject(self.handle, 1, c.byref(accounting), c.sizeof(accounting), None):
                raise c.WinError(c.get_last_error())
            if accounting.active == 0:
                return
            if time.monotonic() >= deadline:
                raise TimeoutError('Native process cleanup is unconfirmed')
            time.sleep(.02)
