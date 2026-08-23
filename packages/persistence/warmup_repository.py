from __future__ import annotations

import json
import sqlite3
from enum import StrEnum
from typing import Protocol

from packages.domain.canonical import canonical_bytes
from packages.persistence.strategy_data import StrategyDataDatabase, StrategyDataError
from packages.strategies import RuntimeContext
from packages.strategies.checkpoint import RuntimePhase, StrategyStateV1, WarmupCheckpoint


class WarmupAppendResult(StrEnum):
    STORED = "STORED"
    ALREADY_EXISTS = "ALREADY_EXISTS"


class WarmupCheckpointConflict(StrategyDataError):
    reason_code = "CHECKPOINT_CONFLICT"


class WarmupRepository(Protocol):
    def append(self, checkpoint: WarmupCheckpoint) -> WarmupAppendResult: ...

    def latest(self, context: RuntimeContext) -> WarmupCheckpoint | None: ...


class SqliteWarmupRepository:
    def __init__(self, database: StrategyDataDatabase) -> None:
        self._database = database

    def append(self, checkpoint: WarmupCheckpoint) -> WarmupAppendResult:
        with self._database.transaction() as connection:
            return self.append_in_transaction(connection, checkpoint)

    def append_in_transaction(
        self,
        connection: sqlite3.Connection,
        checkpoint: WarmupCheckpoint,
    ) -> WarmupAppendResult:
        existing = connection.execute(
            "SELECT * FROM warmup_checkpoints WHERE checkpoint_sha256 = ?",
            (checkpoint.checkpoint_sha256,),
        ).fetchone()
        if existing is not None:
            if self._from_row(existing) != checkpoint:
                raise WarmupCheckpointConflict("checkpoint hash has incompatible persisted content")
            return WarmupAppendResult.ALREADY_EXISTS
        same_position = connection.execute(
            """
            SELECT checkpoint_sha256 FROM warmup_checkpoints
            WHERE strategy_id = ? AND strategy_version = ? AND broker = ?
              AND account_id = ? AND product = ? AND symbol = ?
              AND timeframe_seconds = ? AND configuration_version = ?
              AND last_close_time_ms = ?
            """,
            (
                checkpoint.strategy_id,
                checkpoint.strategy_version,
                checkpoint.broker,
                checkpoint.account_id,
                checkpoint.product,
                checkpoint.symbol,
                checkpoint.timeframe_seconds,
                checkpoint.configuration_version,
                checkpoint.last_close_time_ms,
            ),
        ).fetchone()
        if same_position is not None:
            raise WarmupCheckpointConflict("checkpoint position already has different state")
        connection.execute(
            """
                INSERT INTO warmup_checkpoints(
                    checkpoint_sha256, strategy_id, strategy_version, broker, account_id,
                    product, symbol, timeframe_seconds, configuration_version,
                    manifest_sha256, config_sha256, runtime_phase, strategy_state_version,
                    state_json, state_sha256, last_candle_id, last_close_time_ms,
                    candles_seen, created_at_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
            (
                checkpoint.checkpoint_sha256,
                checkpoint.strategy_id,
                checkpoint.strategy_version,
                checkpoint.broker,
                checkpoint.account_id,
                checkpoint.product,
                checkpoint.symbol,
                checkpoint.timeframe_seconds,
                checkpoint.configuration_version,
                checkpoint.manifest_sha256,
                checkpoint.config_sha256,
                checkpoint.runtime_phase.value,
                checkpoint.state.version,
                canonical_bytes(checkpoint.state.to_payload()).decode(),
                checkpoint.state_sha256,
                checkpoint.last_candle_id,
                checkpoint.last_close_time_ms,
                checkpoint.candles_seen,
                checkpoint.created_at_ms,
            ),
        )
        return WarmupAppendResult.STORED

    def latest(self, context: RuntimeContext) -> WarmupCheckpoint | None:
        rows = self._database.query(
            """
            SELECT * FROM warmup_checkpoints
            WHERE strategy_id = ? AND strategy_version = ? AND broker = ?
              AND account_id = ? AND product = ? AND symbol = ?
              AND timeframe_seconds = ? AND configuration_version = ?
            ORDER BY last_close_time_ms DESC LIMIT 1
            """,
            (
                context.strategy_id,
                context.strategy_version,
                context.broker.value,
                context.account_id,
                context.product,
                context.symbol,
                context.timeframe_seconds,
                context.configuration_version,
            ),
        )
        if not rows:
            return None
        try:
            return self._from_row(rows[0])
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise WarmupCheckpointConflict("persisted checkpoint is invalid") from exc

    @staticmethod
    def _from_row(row: sqlite3.Row) -> WarmupCheckpoint:
        state = StrategyStateV1.from_payload(json.loads(str(row["state_json"])))
        if state.state_sha256 != str(row["state_sha256"]):
            raise ValueError("checkpoint state hash is invalid")
        if state.version != int(row["strategy_state_version"]):
            raise ValueError("checkpoint state version mismatch")
        if state.candles_seen != int(row["candles_seen"]):
            raise ValueError("checkpoint candle count mismatch")
        return WarmupCheckpoint(
            strategy_id=str(row["strategy_id"]),
            strategy_version=str(row["strategy_version"]),
            broker=str(row["broker"]),
            account_id=str(row["account_id"]),
            product=str(row["product"]),
            symbol=str(row["symbol"]),
            timeframe_seconds=int(row["timeframe_seconds"]),
            configuration_version=str(row["configuration_version"]),
            manifest_sha256=str(row["manifest_sha256"]),
            config_sha256=str(row["config_sha256"]),
            runtime_phase=RuntimePhase(str(row["runtime_phase"])),
            state=state,
            last_candle_id=str(row["last_candle_id"]),
            last_close_time_ms=int(row["last_close_time_ms"]),
            created_at_ms=int(row["created_at_ms"]),
            checkpoint_sha256=str(row["checkpoint_sha256"]),
        )
