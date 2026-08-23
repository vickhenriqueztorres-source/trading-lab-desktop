from __future__ import annotations

import threading
from dataclasses import dataclass
from enum import StrEnum


class DatabaseHealthStatus(StrEnum):
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    FAILED = "FAILED"


class DatabaseFailureReason(StrEnum):
    DB_NOT_CHECKED = "DB_NOT_CHECKED"
    DB_OPEN_FAILED = "DB_OPEN_FAILED"
    DB_INTEGRITY_FAILED = "DB_INTEGRITY_FAILED"
    DB_WRITE_FAILED = "DB_WRITE_FAILED"
    DB_MIGRATION_FAILED = "DB_MIGRATION_FAILED"
    DB_LOCK_FAILED = "DB_LOCK_FAILED"
    DB_MISSING_UNEXPECTED = "DB_MISSING_UNEXPECTED"


@dataclass(frozen=True, slots=True)
class DatabaseHealthState:
    status: DatabaseHealthStatus
    reason: DatabaseFailureReason | None


class DatabaseHealth:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._state = DatabaseHealthState(
            status=DatabaseHealthStatus.DEGRADED,
            reason=DatabaseFailureReason.DB_NOT_CHECKED,
        )

    @property
    def state(self) -> DatabaseHealthState:
        with self._lock:
            return self._state

    def mark_healthy(self) -> None:
        with self._lock:
            self._state = DatabaseHealthState(DatabaseHealthStatus.HEALTHY, None)

    def mark_failed(self, reason: DatabaseFailureReason) -> None:
        with self._lock:
            self._state = DatabaseHealthState(DatabaseHealthStatus.FAILED, reason)
