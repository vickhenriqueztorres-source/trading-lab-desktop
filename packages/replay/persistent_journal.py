from __future__ import annotations

from datetime import datetime

from packages.audit import DecisionEventType, DecisionJournal, DecisionRecord
from packages.persistence.journal_repository import SqliteDecisionJournalRepository


class PersistentDecisionJournal:
    def __init__(
        self,
        run_id: str,
        repository: SqliteDecisionJournalRepository,
        *,
        max_events: int,
    ) -> None:
        self._repository = repository
        self._run_id = run_id
        self._max_events = max_events
        self._failed = False
        self._active_candle_id: str | None = None
        self._committed_count = 0
        records = repository.events_for_run(run_id)
        verification = repository.verify_chain(run_id)
        if not verification.valid:
            raise ValueError(verification.reason_code or "journal verification failed")
        self._journal = DecisionJournal(
            run_id,
            max_events=max_events,
            initial_records=records,
        )
        self._committed_count = len(records)

    @property
    def records(self) -> tuple[DecisionRecord, ...]:
        return self._journal.records

    @property
    def final_hash(self) -> str:
        return self._journal.final_hash

    @property
    def pending_records(self) -> tuple[DecisionRecord, ...]:
        if self._active_candle_id is None:
            return ()
        return self._journal.records[self._committed_count :]

    def begin_candle(self, candle_id: str) -> None:
        if self._failed:
            raise RuntimeError("persistent decision journal is failed closed")
        if self._active_candle_id is not None:
            raise RuntimeError("another candle decision batch is already active")
        if not candle_id.strip():
            raise ValueError("candle id is required")
        self._active_candle_id = candle_id

    def confirm_candle(self) -> None:
        if self._active_candle_id is None or not self.pending_records:
            raise RuntimeError("cannot confirm an empty candle decision batch")
        if any(record.event.candle_id != self._active_candle_id for record in self.pending_records):
            self.fail_candle()
            raise RuntimeError("candle decision batch contains another candle")
        self._committed_count = len(self._journal.records)
        self._active_candle_id = None

    def cancel_empty_candle(self) -> None:
        if self._active_candle_id is None:
            return
        if self.pending_records:
            self.fail_candle()
            raise RuntimeError("cannot cancel a non-empty candle decision batch")
        self._active_candle_id = None

    def fail_candle(self) -> None:
        committed = self._journal.records[: self._committed_count]
        self._failed = True
        self._active_candle_id = None
        self._journal = DecisionJournal(
            self._run_id,
            max_events=self._max_events,
            initial_records=committed,
        )

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
        if self._failed:
            raise RuntimeError("persistent decision journal is failed closed")
        if self._active_candle_id is None:
            raise RuntimeError("persistent journal append requires an active candle batch")
        if candle_id != self._active_candle_id:
            self.fail_candle()
            raise RuntimeError("journal event does not match active candle batch")
        record = self._journal.append(
            event_type,
            occurred_at=occurred_at,
            correlation_id=correlation_id,
            causation_id=causation_id,
            strategy_id=strategy_id,
            strategy_version=strategy_version,
            manifest_hash=manifest_hash,
            configuration_hash=configuration_hash,
            candle_id=candle_id,
            payload=payload,
        )
        return record
