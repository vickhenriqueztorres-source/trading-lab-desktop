from __future__ import annotations

from enum import StrEnum

from packages.audit import DecisionRecord
from packages.persistence.journal_repository import (
    JournalAppendResult,
    JournalConflictError,
    SqliteDecisionJournalRepository,
)
from packages.persistence.strategy_data import StrategyDataDatabase, StrategyDataError
from packages.persistence.warmup_repository import (
    SqliteWarmupRepository,
    WarmupAppendResult,
)
from packages.strategies import WarmupCheckpoint


class CandleDecisionCommitResult(StrEnum):
    STORED = "STORED"
    ALREADY_EXISTS = "ALREADY_EXISTS"


class CandleDecisionCommitConflict(StrategyDataError):
    reason_code = "CANDLE_DECISION_COMMIT_CONFLICT"


class SqliteCandleDecisionCommitRepository:
    """Atomically commits one candle's decision batch and matching strategy checkpoint."""

    def __init__(self, database: StrategyDataDatabase) -> None:
        self._database = database
        self._journal = SqliteDecisionJournalRepository(database)
        self._warmup = SqliteWarmupRepository(database)

    def commit(
        self,
        records: tuple[DecisionRecord, ...],
        checkpoint: WarmupCheckpoint,
    ) -> CandleDecisionCommitResult:
        if not records:
            raise ValueError("candle decision commit requires at least one event")
        if any(record.event.candle_id != checkpoint.last_candle_id for record in records):
            raise CandleDecisionCommitConflict("decision batch does not match checkpoint candle")
        if len({record.event.run_id for record in records}) != 1:
            raise CandleDecisionCommitConflict("decision batch mixes replay runs")
        with self._database.transaction("strategy_candle") as connection:
            journal_result = self._journal.append_batch_in_transaction(connection, records)
            checkpoint_result = self._warmup.append_in_transaction(connection, checkpoint)
            if journal_result is JournalAppendResult.STORED and checkpoint_result in {
                WarmupAppendResult.STORED,
                WarmupAppendResult.ALREADY_EXISTS,
            }:
                return CandleDecisionCommitResult.STORED
            if (
                journal_result is JournalAppendResult.ALREADY_EXISTS
                and checkpoint_result is WarmupAppendResult.ALREADY_EXISTS
            ):
                return CandleDecisionCommitResult.ALREADY_EXISTS
            raise JournalConflictError("journal and checkpoint atomic commit states diverged")
