from __future__ import annotations

import json
import os
import threading
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
    def emit(
        self,
        event_name: str,
        *,
        reason_code: str | None = None,
        **fields: EventValue,
    ) -> None:
        return None


class InMemoryEventSink:
    """Thread-safe structured event collector used by local diagnostics and tests."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._events: list[OperationalEvent] = []

    @property
    def events(self) -> tuple[OperationalEvent, ...]:
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

    def __init__(self, path: Path, *, max_bytes: int = 5 * 1024 * 1024) -> None:
        if max_bytes <= 0:
            raise ValueError("operational journal capacity must be positive")
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._max_bytes = max_bytes
        self._lock = threading.Lock()

    def emit(
        self,
        event_name: str,
        *,
        reason_code: str | None = None,
        **fields: EventValue,
    ) -> None:
        record = {
            "event": event_name,
            "fields": dict(sorted(fields.items())),
            "occurred_at": datetime.now(UTC).isoformat(),
            "reason_code": reason_code,
        }
        encoded = json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n"
        with self._lock:
            exceeds_capacity = (
                self._path.exists()
                and self._path.stat().st_size + len(encoded.encode()) > self._max_bytes
            )
            if exceeds_capacity:
                archive = self._path.with_suffix(self._path.suffix + ".1")
                self._path.replace(archive)
            with self._path.open("a", encoding="utf-8", newline="\n") as stream:
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
