from __future__ import annotations

import errno
import os
import sys
from pathlib import Path
from types import TracebackType
from typing import BinaryIO

from packages.observability.events import EventSink, NullEventSink

if sys.platform == "win32":
    import msvcrt

    def _lock_file(handle: BinaryIO) -> None:
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)

    def _unlock_file(handle: BinaryIO) -> None:
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)

else:
    import fcntl

    def _lock_file(handle: BinaryIO) -> None:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _unlock_file(handle: BinaryIO) -> None:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


class CoreInstanceGuardError(RuntimeError):
    reason_code = "DB_LOCK_FAILED"


class CoreInstanceAlreadyRunning(CoreInstanceGuardError):
    reason_code = "CORE_INSTANCE_ALREADY_RUNNING"


class CoreInstanceGuard:
    """OS-backed profile lock; the OS releases ownership when a process dies."""

    def __init__(
        self,
        profile_directory: Path,
        event_sink: EventSink | None = None,
    ) -> None:
        self.lock_path = profile_directory / ".core.instance.lock"
        self._event_sink = event_sink or NullEventSink()
        self._handle: BinaryIO | None = None

    @property
    def is_acquired(self) -> bool:
        return self._handle is not None

    def acquire(self) -> None:
        if self._handle is not None:
            return
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.lock_path.open("a+b")
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            self._lock(handle)
        except OSError as exc:
            handle.close()
            if exc.errno in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
                self._event_sink.emit(
                    "core_instance_lock_rejected",
                    reason_code=CoreInstanceAlreadyRunning.reason_code,
                )
                raise CoreInstanceAlreadyRunning("another Trading Core owns this profile") from exc
            self._event_sink.emit(
                "core_instance_lock_rejected",
                reason_code=CoreInstanceGuardError.reason_code,
            )
            raise CoreInstanceGuardError("failed to acquire Core profile lock") from exc
        self._handle = handle
        self._event_sink.emit("core_instance_lock_acquired")

    def release(self) -> None:
        handle = self._handle
        if handle is None:
            return
        try:
            handle.seek(0)
            _unlock_file(handle)
        finally:
            handle.close()
            self._handle = None

    @staticmethod
    def _lock(handle: BinaryIO) -> None:
        _lock_file(handle)

    def __enter__(self) -> CoreInstanceGuard:
        self.acquire()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.release()
