from __future__ import annotations

import errno
import os
import sys
from pathlib import Path
from typing import BinaryIO

if sys.platform == "win32":
    import msvcrt

    def _lock(handle: BinaryIO) -> None:
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)

    def _unlock(handle: BinaryIO) -> None:
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)

else:
    import fcntl

    def _lock(handle: BinaryIO) -> None:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _unlock(handle: BinaryIO) -> None:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


class LauncherInstanceError(RuntimeError):
    reason_code = "LAUNCHER_INSTANCE_LOCK_FAILED"


class LauncherAlreadyRunning(LauncherInstanceError):
    reason_code = "LAUNCHER_INSTANCE_ALREADY_RUNNING"


class LauncherInstanceGuard:
    """OS-backed per-profile lock; stale files are harmless after owner death."""

    def __init__(self, profile_dir: Path) -> None:
        self.lock_path = Path(profile_dir) / "profile.lock"
        self._handle: BinaryIO | None = None

    @property
    def is_acquired(self) -> bool:
        return self._handle is not None

    def acquire(self) -> None:
        if self._handle is not None:
            return
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.lock_path.open("a+b")
        if handle.seek(0, os.SEEK_END) == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            _lock(handle)
        except OSError as exc:
            handle.close()
            if exc.errno in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
                raise LauncherAlreadyRunning(LauncherAlreadyRunning.reason_code) from exc
            raise LauncherInstanceError(LauncherInstanceError.reason_code) from exc
        self._handle = handle

    def release(self) -> None:
        handle = self._handle
        if handle is None:
            return
        try:
            handle.seek(0)
            _unlock(handle)
        finally:
            handle.close()
            self._handle = None
