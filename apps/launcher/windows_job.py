from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes
from typing import Protocol


class ProcessContainment(Protocol):
    def assign(self, pid: int) -> None: ...

    def terminate_tree(self, exit_code: int = 1) -> None: ...

    def close(self) -> None: ...


class NoopProcessContainment:
    def assign(self, pid: int) -> None:
        if pid <= 0:
            raise ValueError("process pid must be positive")

    def terminate_tree(self, exit_code: int = 1) -> None:
        if exit_code < 0:
            raise ValueError("exit code cannot be negative")

    def close(self) -> None:
        return


if sys.platform == "win32":
    _PROCESS_TERMINATE = 0x0001
    _PROCESS_SET_QUOTA = 0x0100
    _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
    _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9

    class _IoCounters(ctypes.Structure):
        _fields_ = [
            ("ReadOperationCount", ctypes.c_ulonglong),
            ("WriteOperationCount", ctypes.c_ulonglong),
            ("OtherOperationCount", ctypes.c_ulonglong),
            ("ReadTransferCount", ctypes.c_ulonglong),
            ("WriteTransferCount", ctypes.c_ulonglong),
            ("OtherTransferCount", ctypes.c_ulonglong),
        ]

    class _BasicLimitInformation(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_longlong),
            ("PerJobUserTimeLimit", ctypes.c_longlong),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class _ExtendedLimitInformation(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", _BasicLimitInformation),
            ("IoInfo", _IoCounters),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]


class WindowsJobObject:
    """Windows Job Object whose close deterministically kills every descendant."""

    def __init__(self) -> None:
        if sys.platform != "win32":
            raise OSError("Windows Job Objects are unavailable")
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        handle = kernel32.CreateJobObjectW(None, None)
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        self._kernel32 = kernel32
        self._handle = int(handle)
        information = _ExtendedLimitInformation()
        information.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        ok = kernel32.SetInformationJobObject(
            wintypes.HANDLE(self._handle),
            _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
            ctypes.byref(information),
            ctypes.sizeof(information),
        )
        if not ok:
            kernel32.CloseHandle(wintypes.HANDLE(self._handle))
            self._handle = 0
            raise ctypes.WinError(ctypes.get_last_error())

    def assign(self, pid: int) -> None:
        if pid <= 0 or self._handle == 0:
            raise ValueError("job assignment target is invalid")
        process = self._kernel32.OpenProcess(_PROCESS_TERMINATE | _PROCESS_SET_QUOTA, False, pid)
        if not process:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            if not self._kernel32.AssignProcessToJobObject(
                wintypes.HANDLE(self._handle), wintypes.HANDLE(process)
            ):
                raise ctypes.WinError(ctypes.get_last_error())
        finally:
            self._kernel32.CloseHandle(wintypes.HANDLE(process))

    def terminate_tree(self, exit_code: int = 1) -> None:
        if exit_code < 0:
            raise ValueError("exit code cannot be negative")
        if self._handle and not self._kernel32.TerminateJobObject(
            wintypes.HANDLE(self._handle), exit_code
        ):
            raise ctypes.WinError(ctypes.get_last_error())

    def close(self) -> None:
        if self._handle:
            self._kernel32.CloseHandle(wintypes.HANDLE(self._handle))
            self._handle = 0


def create_process_containment() -> ProcessContainment:
    return WindowsJobObject() if sys.platform == "win32" else NoopProcessContainment()
