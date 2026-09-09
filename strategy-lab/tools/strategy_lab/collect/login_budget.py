"""Cross-process daily login budget for the unofficial read-only connector."""

from __future__ import annotations

import importlib
import json
import os
import sys
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import BinaryIO, Never

from strategy_lab.collect.clock import utc_now_ts

MAX_LOGIN_ATTEMPTS_PER_UTC_DAY = 2
MAX_STATE_BYTES = 512


class LoginBudgetError(RuntimeError):
    """Stable public reason only; state never contains credentials."""


def _reject_constant(value: str) -> Never:
    raise ValueError


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError
        result[key] = value
    return result


class LoginBudget:
    def __init__(self, path: Path, *, now: Callable[[], int] = utc_now_ts) -> None:
        self._path = path
        self._lock_path = path.with_suffix(path.suffix + ".lock")
        self._now = now

    def consume(self) -> None:
        """Reserve one attempt before network authentication, fail-closed."""
        now_ts = self._now()
        if type(now_ts) is not int or now_ts < 0:
            raise LoginBudgetError("IQ_LOGIN_BUDGET_CLOCK_INVALID")
        day = now_ts // 86400
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with _exclusive_lock(self._lock_path):
            stored_day, attempts = self._read()
            if stored_day > day:
                raise LoginBudgetError("IQ_LOGIN_BUDGET_STATE_INVALID")
            if stored_day < day:
                attempts = 0
            if attempts >= MAX_LOGIN_ATTEMPTS_PER_UTC_DAY:
                raise LoginBudgetError("IQ_LOGIN_DAILY_LIMIT")
            self._write(day, attempts + 1)

    def _read(self) -> tuple[int, int]:
        if not self._path.exists():
            return 0, 0
        try:
            if self._path.stat().st_size > MAX_STATE_BYTES:
                raise ValueError
            raw = json.loads(
                self._path.read_text(encoding="utf-8"),
                parse_float=lambda value: (_ for _ in ()).throw(ValueError()),
                parse_constant=_reject_constant,
                object_pairs_hook=_unique_object,
            )
            if not isinstance(raw, dict) or set(raw) != {"schema_version", "day", "attempts"}:
                raise ValueError
            if raw["schema_version"] != 1:
                raise ValueError
            day = raw["day"]
            attempts = raw["attempts"]
            if type(day) is not int or type(attempts) is not int or day < 0 or attempts < 0:
                raise ValueError
            if attempts > MAX_LOGIN_ATTEMPTS_PER_UTC_DAY:
                raise ValueError
            return day, attempts
        except (OSError, UnicodeError, ValueError, TypeError, RecursionError):
            raise LoginBudgetError("IQ_LOGIN_BUDGET_STATE_INVALID") from None

    def _write(self, day: int, attempts: int) -> None:
        payload = json.dumps(
            {"schema_version": 1, "day": day, "attempts": attempts},
            sort_keys=True,
            separators=(",", ":"),
        ).encode("ascii")
        temporary = self._path.with_name(
            f".{self._path.name}.{os.getpid()}.{threading.get_ident()}.tmp"
        )
        try:
            with temporary.open("xb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self._path)
        except OSError:
            raise LoginBudgetError("IQ_LOGIN_BUDGET_WRITE_FAILED") from None
        finally:
            temporary.unlink(missing_ok=True)


@contextmanager
def _exclusive_lock(path: Path) -> Iterator[None]:
    deadline = time.monotonic() + 5
    with path.open("a+b") as handle:
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        while True:
            try:
                _lock(handle)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise LoginBudgetError("IQ_LOGIN_BUDGET_LOCK_TIMEOUT") from None
                time.sleep(0.01)
        try:
            yield
        finally:
            _unlock(handle)


def _lock(handle: BinaryIO) -> None:
    handle.seek(0)
    if sys.platform == "win32":
        msvcrt = importlib.import_module("msvcrt")
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        return
    fcntl = importlib.import_module("fcntl")
    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock(handle: BinaryIO) -> None:
    handle.seek(0)
    if sys.platform == "win32":
        msvcrt = importlib.import_module("msvcrt")
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        return
    fcntl = importlib.import_module("fcntl")
    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
