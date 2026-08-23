from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import UTC, datetime
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
