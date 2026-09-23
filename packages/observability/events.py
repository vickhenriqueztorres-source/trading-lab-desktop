from __future__ import annotations

import json
import os
import threading
import time
from collections import deque
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

EventValue = str | int | bool | None


@dataclass(frozen=True, slots=True)
class OperationalEvent:
    event_name: str
    occurred_at: datetime
    reason_code: str | None
    fields: tuple[tuple[str, EventValue], ...]


class EventSink(Protocol):
    def emit(
        self,
        event_name: str,
        *,
        reason_code: str | None = None,
        **fields: EventValue,
    ) -> None: ...


class NullEventSink:
    @property
    def recent_events(self) -> tuple[OperationalEvent, ...]:
        return ()

    def emit(
        self,
        event_name: str,
        *,
        reason_code: str | None = None,
        **fields: EventValue,
    ) -> None:
        pass


class InMemoryEventSink:
    """Thread-safe structured event collector used by local diagnostics and tests."""

    def __init__(self, *, max_events: int = 256) -> None:
        self._max_events = max_events
        self._events: deque[OperationalEvent] = deque(maxlen=max_events)
        self._lock = threading.Lock()

    @property
    def events(self) -> tuple[OperationalEvent, ...]:
        with self._lock:
            return tuple(self._events)

    @property
    def recent_events(self) -> tuple[OperationalEvent, ...]:
        with self._lock:
            return tuple(self._events)

    def emit(
        self,
        event_name: str,
        *,
        reason_code: str | None = None,
        **fields: EventValue,
    ) -> None:
        event = OperationalEvent(
            event_name=event_name,
            occurred_at=datetime.now(UTC),
            reason_code=reason_code,
            fields=tuple(sorted(fields.items())),
        )
        with self._lock:
            self._events.append(event)


class PersistentJsonlEventSink:
    """Small append-only operational journal retained across process restarts."""

    def __init__(
        self,
        path: Path,
        *,
        max_bytes: int = 5 * 1024 * 1024,
        fsync_interval_seconds: float = 2.0,
    ) -> None:
        if max_bytes <= 0:
            raise ValueError("operational journal capacity must be positive")
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._max_bytes = max_bytes
        self._fsync_interval = fsync_interval_seconds
        self._last_fsync_monotonic = 0.0
        self._lock = threading.Lock()
        self._recent: deque[OperationalEvent] = deque(maxlen=256)
        self._current_size = 0
        with suppress(OSError):
            if self._path.exists():
                self._current_size = self._path.stat().st_size

    @property
    def recent_events(self) -> tuple[OperationalEvent, ...]:
        with self._lock:
            return tuple(self._recent)

    def emit(
        self,
        event_name: str,
        *,
        reason_code: str | None = None,
        **fields: EventValue,
    ) -> None:
        occurred_at = datetime.now(UTC)
        event = OperationalEvent(
            event_name=event_name,
            occurred_at=occurred_at,
            reason_code=reason_code,
            fields=tuple(sorted(fields.items())),
        )
        record = {
            "event": event_name,
            "fields": dict(sorted(fields.items())),
            "occurred_at": occurred_at.isoformat(),
            "reason_code": reason_code,
        }
        encoded = json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n"
        encoded_bytes = encoded.encode("utf-8")
        encoded_len = len(encoded_bytes)
        now_mono = time.monotonic()
        with self._lock:
            exceeds_capacity = self._current_size + encoded_len > self._max_bytes
            if exceeds_capacity:
                archive = self._path.with_suffix(self._path.suffix + ".1")
                with suppress(OSError):
                    self._path.replace(archive)
                self._current_size = 0
                self._last_fsync_monotonic = 0.0

            force_fsync = (
                exceeds_capacity
                or reason_code in {"HG_SAFE_STOP", "CRASH", "FATAL"}
                or (now_mono - self._last_fsync_monotonic >= self._fsync_interval)
            )

            with self._path.open("ab") as stream:
                stream.write(encoded_bytes)
                stream.flush()
                if force_fsync:
                    os.fsync(stream.fileno())
                    self._last_fsync_monotonic = now_mono

            self._current_size += encoded_len
            self._recent.append(event)
