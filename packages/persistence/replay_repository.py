from __future__ import annotations

import sqlite3
from enum import StrEnum
from typing import Protocol

from packages.persistence.strategy_data import StrategyDataDatabase, StrategyDataError
from packages.replay.models import ReplayRecord, ReplayStatus


class ReplayAppendResult(StrEnum):
    STORED = "STORED"
    ALREADY_EXISTS = "ALREADY_EXISTS"


class ReplayConflictError(StrategyDataError):
    reason_code = "REPLAY_RECORD_CONFLICT"


class ReplayRepository(Protocol):
    def append(self, record: ReplayRecord) -> ReplayAppendResult: ...

    def get(self, run_id: str) -> ReplayRecord | None: ...


class SqliteReplayRepository:
    def __init__(self, database: StrategyDataDatabase) -> None:
        self._database = database

    def append(self, record: ReplayRecord) -> ReplayAppendResult:
        with self._database.transaction() as connection:
            existing = connection.execute(
                "SELECT * FROM replay_runs WHERE run_id = ?", (record.run_id,)
            ).fetchone()
            if existing is not None:
                if self._from_row(existing) != record:
                    raise ReplayConflictError("run id has incompatible replay result")
                return ReplayAppendResult.ALREADY_EXISTS
            connection.execute(
                """
                INSERT INTO replay_runs(
                    run_id, strategy_id, strategy_version, manifest_sha256, config_sha256,
                    first_candle_id, last_candle_id, candle_count, final_journal_sha256,
                    result_sha256, status, completed_at_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.run_id,
                    record.strategy_id,
                    record.strategy_version,
                    record.manifest_sha256,
                    record.config_sha256,
                    record.first_candle_id,
                    record.last_candle_id,
                    record.candle_count,
                    record.final_journal_sha256,
                    record.result_sha256,
                    record.status.value,
                    record.completed_at_ms,
                ),
            )
        return ReplayAppendResult.STORED

    def get(self, run_id: str) -> ReplayRecord | None:
        rows = self._database.query("SELECT * FROM replay_runs WHERE run_id = ?", (run_id,))
        return None if not rows else self._from_row(rows[0])

    @staticmethod
    def _from_row(row: sqlite3.Row) -> ReplayRecord:
        return ReplayRecord(
            run_id=str(row["run_id"]),
            strategy_id=str(row["strategy_id"]),
            strategy_version=str(row["strategy_version"]),
            manifest_sha256=str(row["manifest_sha256"]),
            config_sha256=str(row["config_sha256"]),
            first_candle_id=str(row["first_candle_id"]),
            last_candle_id=str(row["last_candle_id"]),
            candle_count=int(row["candle_count"]),
            final_journal_sha256=str(row["final_journal_sha256"]),
            result_sha256=str(row["result_sha256"]),
            status=ReplayStatus(str(row["status"])),
            completed_at_ms=int(row["completed_at_ms"]),
        )
