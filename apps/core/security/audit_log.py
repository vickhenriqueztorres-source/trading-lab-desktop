"""Append-only, tamper-evident audit log.

The logger accepts already-redacted event fields.  It stores only operational
metadata and never receives broker credentials or order payloads.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from typing import Any
from uuid import uuid4


@dataclass(frozen=True, slots=True)
class AuditEvent:
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))
    event_type: str = ""
    actor_id: str = "system"
    actor_type: str = "service"
    resource_type: str = ""
    resource_id: str = ""
    action: str = ""
    success: bool = True
    correlation_id: str = field(default_factory=lambda: uuid4().hex)
    previous_event_hash: str = ""
    event_hash: str = ""
    signature: str = ""


class AuditIntegrityError(RuntimeError):
    pass


class AuditLogger:
    """Hash-chain logger with optional append-only JSONL persistence."""

    def __init__(self, secret: bytes, *, path: Path | None = None) -> None:
        if not secret:
            raise ValueError("audit HMAC secret must not be empty")
        self._secret = bytes(secret)
        self._path = path
        self._events: list[AuditEvent] = []
        self._lock = Lock()

    @staticmethod
    def _canonical(event: AuditEvent) -> bytes:
        payload = {
            "timestamp": event.timestamp.astimezone(UTC).isoformat(),
            "event_type": event.event_type,
            "actor_id": event.actor_id,
            "actor_type": event.actor_type,
            "resource_type": event.resource_type,
            "resource_id": event.resource_id,
            "action": event.action,
            "success": event.success,
            "correlation_id": event.correlation_id,
            "previous_event_hash": event.previous_event_hash,
        }
        return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")

    def log(self, event: AuditEvent | Mapping[str, Any]) -> AuditEvent:
        candidate = event if isinstance(event, AuditEvent) else AuditEvent(**dict(event))
        with self._lock:
            previous = self._events[-1].event_hash if self._events else ""
            chained = replace(candidate, previous_event_hash=previous)
            event_hash = hashlib.sha256(self._canonical(chained)).hexdigest()
            signature = hmac.new(
                self._secret, event_hash.encode("ascii"), hashlib.sha256
            ).hexdigest()
            committed = replace(chained, event_hash=event_hash, signature=signature)
            self._events.append(committed)
            self._append(committed)
            return committed

    def _append(self, event: AuditEvent) -> None:
        if self._path is None:
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "timestamp": event.timestamp.astimezone(UTC).isoformat(),
            "event_type": event.event_type,
            "actor_id": event.actor_id,
            "actor_type": event.actor_type,
            "resource_type": event.resource_type,
            "resource_id": event.resource_id,
            "action": event.action,
            "success": event.success,
            "correlation_id": event.correlation_id,
            "previous_event_hash": event.previous_event_hash,
            "event_hash": event.event_hash,
            "signature": event.signature,
        }
        with self._path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, sort_keys=True) + "\n")

    def query(
        self, filters: Mapping[str, Any] | None = None, **kwargs: Any
    ) -> tuple[AuditEvent, ...]:
        criteria = {**dict(filters or {}), **kwargs}
        with self._lock:
            events = tuple(self._events)
        return tuple(
            event
            for event in events
            if all(getattr(event, key, object()) == value for key, value in criteria.items())
        )

    def verify_integrity(self) -> bool:
        with self._lock:
            previous = ""
            for event in self._events:
                if event.previous_event_hash != previous:
                    return False
                expected_hash = hashlib.sha256(self._canonical(event)).hexdigest()
                expected_signature = hmac.new(
                    self._secret, expected_hash.encode("ascii"), hashlib.sha256
                ).hexdigest()
                if not hmac.compare_digest(event.event_hash, expected_hash):
                    return False
                if not hmac.compare_digest(event.signature, expected_signature):
                    return False
                previous = event.event_hash
            return True


__all__ = ["AuditEvent", "AuditIntegrityError", "AuditLogger"]
