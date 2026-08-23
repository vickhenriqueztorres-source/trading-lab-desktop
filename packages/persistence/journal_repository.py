from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol

from packages.audit import (
    GENESIS_HASH,
    DecisionEvent,
    DecisionEventType,
    DecisionRecord,
    decision_chain_hash,
    verify_decision_chain,
)
from packages.domain.canonical import canonical_bytes
from packages.persistence.strategy_data import StrategyDataDatabase, StrategyDataError


class JournalAppendResult(StrEnum):
    STORED = "STORED"
    ALREADY_EXISTS = "ALREADY_EXISTS"


class JournalConflictError(StrategyDataError):
    reason_code = "JOURNAL_APPEND_CONFLICT"


@dataclass(frozen=True, slots=True)
class JournalVerification:
    valid: bool
    event_count: int
    final_hash: str
    reason_code: str | None


class DecisionJournalRepository(Protocol):
    def append(self, record: DecisionRecord) -> JournalAppendResult: ...

    def events_for_run(self, run_id: str) -> tuple[DecisionRecord, ...]: ...

    def verify_chain(self, run_id: str) -> JournalVerification: ...


class SqliteDecisionJournalRepository:
    def __init__(self, database: StrategyDataDatabase) -> None:
        self._database = database

    def append(self, record: DecisionRecord) -> JournalAppendResult:
        with self._database.transaction() as connection:
            return self.append_batch_in_transaction(connection, (record,))

    def append_batch_in_transaction(
        self,
        connection: sqlite3.Connection,
        records: tuple[DecisionRecord, ...],
    ) -> JournalAppendResult:
        if not records:
            raise ValueError("journal batch cannot be empty")
        run_id = records[0].event.run_id
        if any(record.event.run_id != run_id for record in records):
            raise JournalConflictError("journal batch mixes replay runs")
        first_sequence = records[0].event.sequence
        last_sequence = records[-1].event.sequence
        if tuple(record.event.sequence for record in records) != tuple(
            range(first_sequence, last_sequence + 1)
        ):
            raise JournalConflictError("journal batch sequence is not contiguous")
        existing_rows = connection.execute(
            """
            SELECT * FROM decision_events
            WHERE run_id = ? AND sequence BETWEEN ? AND ? ORDER BY sequence
            """,
            (run_id, first_sequence, last_sequence),
        ).fetchall()
        if existing_rows:
            try:
                existing = tuple(self._from_row(row) for row in existing_rows)
            except (TypeError, ValueError, KeyError, json.JSONDecodeError) as exc:
                raise JournalConflictError("persisted journal event is invalid") from exc
            if existing == records:
                return JournalAppendResult.ALREADY_EXISTS
            raise JournalConflictError("journal batch overlaps incompatible persisted events")
        last = connection.execute(
            """
            SELECT sequence, event_sha256 FROM decision_events
            WHERE run_id = ? ORDER BY sequence DESC LIMIT 1
            """,
            (run_id,),
        ).fetchone()
        expected_sequence = 1 if last is None else int(last["sequence"]) + 1
        expected_previous = GENESIS_HASH if last is None else str(last["event_sha256"])
        for record in records:
            if (
                record.event.sequence != expected_sequence
                or record.previous_hash != expected_previous
            ):
                raise JournalConflictError("journal sequence or previous hash is invalid")
            if record.event_hash != decision_chain_hash(expected_previous, record.event):
                raise JournalConflictError("journal event hash is invalid")
            event_id_row = connection.execute(
                "SELECT run_id, sequence FROM decision_events WHERE event_id = ?",
                (record.event.event_id,),
            ).fetchone()
            if event_id_row is not None:
                raise JournalConflictError("event id already belongs to another journal position")
            connection.execute(
                """
                INSERT INTO decision_events(
                    event_id, run_id, sequence, event_type, occurred_at, logical_time_ms,
                    correlation_id, causation_id, strategy_id, strategy_version,
                    manifest_sha256, config_sha256, candle_id, payload_json, payload_sha256,
                    previous_event_sha256, event_sha256
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.event.event_id,
                    record.event.run_id,
                    record.event.sequence,
                    record.event.event_type.value,
                    record.event.occurred_at.isoformat(),
                    record.event.logical_time_ms,
                    record.event.correlation_id,
                    record.event.causation_id,
                    record.event.strategy_id,
                    record.event.strategy_version,
                    record.event.manifest_hash,
                    record.event.configuration_hash,
                    record.event.candle_id,
                    canonical_bytes(list(record.event.payload)).decode(),
                    record.event.payload_sha256,
                    record.previous_hash,
                    record.event_hash,
                ),
            )
            expected_sequence += 1
            expected_previous = record.event_hash
        return JournalAppendResult.STORED

    def events_for_run(self, run_id: str) -> tuple[DecisionRecord, ...]:
        rows = self._database.query(
            "SELECT * FROM decision_events WHERE run_id = ? ORDER BY sequence",
            (run_id,),
        )
        try:
            return tuple(self._from_row(row) for row in rows)
        except (TypeError, ValueError, KeyError, json.JSONDecodeError) as exc:
            raise JournalConflictError("persisted journal event is invalid") from exc

    def verify_chain(self, run_id: str) -> JournalVerification:
        try:
            records = self.events_for_run(run_id)
        except JournalConflictError:
            return JournalVerification(False, 0, GENESIS_HASH, "JOURNAL_PAYLOAD_INVALID")
        valid = verify_decision_chain(records)
        return JournalVerification(
            valid=valid,
            event_count=len(records),
            final_hash=records[-1].event_hash if records else GENESIS_HASH,
            reason_code=None if valid else "JOURNAL_HASH_CHAIN_INVALID",
        )

    @staticmethod
    def _from_row(row: sqlite3.Row) -> DecisionRecord:
        raw_payload = json.loads(str(row["payload_json"]))
        if not isinstance(raw_payload, list) or any(
            not isinstance(item, list)
            or len(item) != 2
            or not all(isinstance(value, str) for value in item)
            for item in raw_payload
        ):
            raise ValueError("journal payload schema is invalid")
        event = DecisionEvent(
            event_id=str(row["event_id"]),
            run_id=str(row["run_id"]),
            sequence=int(row["sequence"]),
            event_type=DecisionEventType(str(row["event_type"])),
            occurred_at=datetime.fromisoformat(str(row["occurred_at"])),
            logical_time_ms=int(row["logical_time_ms"]),
            correlation_id=str(row["correlation_id"]),
            causation_id=(None if row["causation_id"] is None else str(row["causation_id"])),
            strategy_id=str(row["strategy_id"]),
            strategy_version=str(row["strategy_version"]),
            manifest_hash=str(row["manifest_sha256"]),
            configuration_hash=str(row["config_sha256"]),
            candle_id=str(row["candle_id"]),
            payload=tuple((str(item[0]), str(item[1])) for item in raw_payload),
            payload_sha256=str(row["payload_sha256"]),
        )
        return DecisionRecord(
            event=event,
            previous_hash=str(row["previous_event_sha256"]),
            event_hash=str(row["event_sha256"]),
        )
