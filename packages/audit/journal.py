from __future__ import annotations

import hashlib
from datetime import UTC, datetime

from packages.audit.models import DecisionEvent, DecisionEventType, DecisionRecord
from packages.domain.canonical import canonical_bytes

GENESIS_HASH = "0" * 64
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def _logical_time_ms(value: datetime) -> int:
    delta = value - _EPOCH
    return delta.days * 86_400_000 + delta.seconds * 1_000 + delta.microseconds // 1_000


def decision_chain_hash(previous_hash: str, event: DecisionEvent) -> str:
    return hashlib.sha256(previous_hash.encode("ascii") + event.canonical_bytes()).hexdigest()


def verify_decision_chain(records: tuple[DecisionRecord, ...]) -> bool:
    previous = GENESIS_HASH
    for expected_sequence, record in enumerate(records, start=1):
        if record.event.sequence != expected_sequence or record.previous_hash != previous:
            return False
        if record.event_hash != decision_chain_hash(previous, record.event):
            return False
        previous = record.event_hash
    return True


class DecisionJournal:
    def __init__(
        self,
        run_id: str,
        *,
        max_events: int,
        initial_records: tuple[DecisionRecord, ...] = (),
    ) -> None:
        if not run_id.strip() or max_events <= 0:
            raise ValueError("journal identity and capacity are required")
        if len(initial_records) > max_events:
            raise ValueError("initial journal exceeds capacity")
        if any(record.event.run_id != run_id for record in initial_records):
            raise ValueError("initial journal belongs to another run")
        if not verify_decision_chain(initial_records):
            raise ValueError("initial journal hash chain is invalid")
        self._run_id = run_id
        self._max_events = max_events
        self._records = list(initial_records)

    @property
    def records(self) -> tuple[DecisionRecord, ...]:
        return tuple(self._records)

    @property
    def final_hash(self) -> str:
        return self._records[-1].event_hash if self._records else GENESIS_HASH

    def append(
        self,
        event_type: DecisionEventType,
        *,
        occurred_at: datetime,
        correlation_id: str,
        causation_id: str | None,
        strategy_id: str,
        strategy_version: str,
        manifest_hash: str,
        configuration_hash: str,
        candle_id: str,
        payload: tuple[tuple[str, str], ...],
    ) -> DecisionRecord:
        if len(self._records) >= self._max_events:
            raise RuntimeError("decision journal capacity reached")
        sequence = len(self._records) + 1
        ordered_payload = tuple(sorted(payload))
        payload_hash = hashlib.sha256(canonical_bytes(list(ordered_payload))).hexdigest()
        event_identity = "|".join(
            (
                self._run_id,
                str(sequence),
                event_type.value,
                correlation_id,
                candle_id,
            )
        )
        event = DecisionEvent(
            event_id=hashlib.sha256(event_identity.encode("utf-8")).hexdigest(),
            run_id=self._run_id,
            sequence=sequence,
            event_type=event_type,
            occurred_at=occurred_at,
            logical_time_ms=_logical_time_ms(occurred_at),
            correlation_id=correlation_id,
            causation_id=causation_id,
            strategy_id=strategy_id,
            strategy_version=strategy_version,
            manifest_hash=manifest_hash,
            configuration_hash=configuration_hash,
            candle_id=candle_id,
            payload=ordered_payload,
            payload_sha256=payload_hash,
        )
        previous = self.final_hash
        record = DecisionRecord(event, previous, decision_chain_hash(previous, event))
        self._records.append(record)
        return record
