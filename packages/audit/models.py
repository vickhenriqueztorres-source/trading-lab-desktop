from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from packages.domain.canonical import canonical_bytes
from packages.domain.models import require_aware_utc


class DecisionEventType(StrEnum):
    CANDLE_ACCEPTED = "CANDLE_ACCEPTED"
    STRATEGY_EVALUATED = "STRATEGY_EVALUATED"
    STRATEGY_BLOCKED = "STRATEGY_BLOCKED"
    SIGNAL_CREATED = "SIGNAL_CREATED"
    SIGNAL_CANCELLED = "SIGNAL_CANCELLED"
    SIGNAL_CONSOLIDATED = "SIGNAL_CONSOLIDATED"
    ALLOCATION_APPROVED = "ALLOCATION_APPROVED"
    ALLOCATION_REJECTED = "ALLOCATION_REJECTED"
    RISK_ACCEPTED = "RISK_ACCEPTED"
    RISK_REJECTED = "RISK_REJECTED"
    ORDER_INTENT_CREATED = "ORDER_INTENT_CREATED"


@dataclass(frozen=True, slots=True)
class DecisionEvent:
    event_id: str
    run_id: str
    sequence: int
    event_type: DecisionEventType
    occurred_at: datetime
    logical_time_ms: int
    correlation_id: str
    causation_id: str | None
    strategy_id: str
    strategy_version: str
    manifest_hash: str
    configuration_hash: str
    candle_id: str
    payload: tuple[tuple[str, str], ...]
    payload_sha256: str

    def __post_init__(self) -> None:
        require_aware_utc(self.occurred_at, "occurred_at")
        if self.sequence <= 0:
            raise ValueError("journal sequence must be positive")
        if self.logical_time_ms < 0:
            raise ValueError("logical_time_ms cannot be negative")
        for field_name in (
            "event_id",
            "run_id",
            "correlation_id",
            "strategy_id",
            "strategy_version",
            "manifest_hash",
            "configuration_hash",
            "candle_id",
        ):
            if not getattr(self, field_name).strip():
                raise ValueError(f"{field_name} cannot be empty")
        names = tuple(name for name, _ in self.payload)
        if any(not name.strip() for name in names) or len(set(names)) != len(names):
            raise ValueError("decision payload names must be unique and non-empty")
        expected_payload_hash = hashlib.sha256(canonical_bytes(list(self.payload))).hexdigest()
        if self.payload_sha256 != expected_payload_hash:
            raise ValueError("decision payload hash mismatch")

    def canonical_bytes(self) -> bytes:
        value = {
            "candle_id": self.candle_id,
            "causation_id": self.causation_id,
            "configuration_hash": self.configuration_hash,
            "correlation_id": self.correlation_id,
            "event_id": self.event_id,
            "event_type": self.event_type.value,
            "manifest_hash": self.manifest_hash,
            "logical_time_ms": self.logical_time_ms,
            "occurred_at": self.occurred_at.isoformat(),
            "payload": list(self.payload),
            "payload_sha256": self.payload_sha256,
            "run_id": self.run_id,
            "sequence": self.sequence,
            "strategy_id": self.strategy_id,
            "strategy_version": self.strategy_version,
        }
        return canonical_bytes(value)


@dataclass(frozen=True, slots=True)
class DecisionRecord:
    event: DecisionEvent
    previous_hash: str
    event_hash: str

    @property
    def previous_event_sha256(self) -> str | None:
        return None if self.previous_hash == "0" * 64 else self.previous_hash

    @property
    def event_sha256(self) -> str:
        return self.event_hash
