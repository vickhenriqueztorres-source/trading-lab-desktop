from __future__ import annotations

import hashlib
import sqlite3
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from packages.domain.canonical import canonical_bytes

STRATEGY_DATA_DB_NAME = "strategy_data.db"
STRATEGY_BUSY_TIMEOUT_MS = 5_000


class StrategyDataError(RuntimeError):
    reason_code = "STRATEGY_DATA_ERROR"


class StrategyDataIntegrityError(StrategyDataError):
    reason_code = "STRATEGY_DATA_INTEGRITY_FAILED"


class StrategyDataMigrationError(StrategyDataError):
    reason_code = "STRATEGY_DATA_MIGRATION_FAILED"


@dataclass(frozen=True, slots=True)
class StrategyDataMigration:
    version: int
    name: str
    statements: tuple[str, ...]

    @property
    def checksum(self) -> str:
        return hashlib.sha256(canonical_bytes(list(self.statements))).hexdigest()


STRATEGY_DATA_V1 = StrategyDataMigration(
    version=1,
    name="0001_strategy_evidence",
    statements=(
        """
        CREATE TABLE candles (
            candle_id TEXT PRIMARY KEY,
            broker TEXT NOT NULL,
            symbol TEXT NOT NULL,
            timeframe_seconds INTEGER NOT NULL CHECK (timeframe_seconds > 0),
            open_time_ms INTEGER NOT NULL CHECK (open_time_ms >= 0),
            close_time_ms INTEGER NOT NULL CHECK (close_time_ms > open_time_ms),
            open_units INTEGER NOT NULL CHECK (open_units > 0),
            high_units INTEGER NOT NULL CHECK (high_units > 0),
            low_units INTEGER NOT NULL CHECK (low_units > 0),
            close_units INTEGER NOT NULL CHECK (close_units > 0),
            price_scale INTEGER NOT NULL CHECK (price_scale > 0),
            source TEXT NOT NULL,
            source_event_id TEXT NOT NULL,
            source_timestamp_ms INTEGER NOT NULL CHECK (source_timestamp_ms >= 0),
            received_timestamp_ms INTEGER NOT NULL CHECK (received_timestamp_ms >= 0),
            inserted_at_ms INTEGER NOT NULL CHECK (inserted_at_ms >= 0)
        )
        """,
        """
        CREATE UNIQUE INDEX uq_candle_stream_close
        ON candles(broker, symbol, timeframe_seconds, close_time_ms)
        """,
        """
        CREATE INDEX ix_candle_stream_range
        ON candles(broker, symbol, timeframe_seconds, close_time_ms, candle_id)
        """,
        """
        CREATE TABLE decision_events (
            event_id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL,
            sequence INTEGER NOT NULL CHECK (sequence > 0),
            event_type TEXT NOT NULL,
            occurred_at TEXT NOT NULL,
            logical_time_ms INTEGER NOT NULL CHECK (logical_time_ms >= 0),
            correlation_id TEXT NOT NULL,
            causation_id TEXT,
            strategy_id TEXT NOT NULL,
            strategy_version TEXT NOT NULL,
            manifest_sha256 TEXT NOT NULL,
            config_sha256 TEXT NOT NULL,
            candle_id TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            payload_sha256 TEXT NOT NULL,
            previous_event_sha256 TEXT NOT NULL,
            event_sha256 TEXT NOT NULL,
            UNIQUE(run_id, sequence)
        )
        """,
        """
        CREATE INDEX ix_decision_events_run
        ON decision_events(run_id, sequence)
        """,
        """
        CREATE TABLE replay_runs (
            run_id TEXT PRIMARY KEY,
            strategy_id TEXT NOT NULL,
            strategy_version TEXT NOT NULL,
            manifest_sha256 TEXT NOT NULL,
            config_sha256 TEXT NOT NULL,
            first_candle_id TEXT NOT NULL,
            last_candle_id TEXT NOT NULL,
            candle_count INTEGER NOT NULL CHECK (candle_count > 0),
            final_journal_sha256 TEXT NOT NULL,
            result_sha256 TEXT NOT NULL,
            status TEXT NOT NULL,
            completed_at_ms INTEGER NOT NULL CHECK (completed_at_ms >= 0)
        )
        """,
        """
        CREATE TABLE warmup_checkpoints (
            checkpoint_sha256 TEXT PRIMARY KEY,
            strategy_id TEXT NOT NULL,
            strategy_version TEXT NOT NULL,
            broker TEXT NOT NULL,
            account_id TEXT NOT NULL,
            product TEXT NOT NULL,
            symbol TEXT NOT NULL,
            timeframe_seconds INTEGER NOT NULL CHECK (timeframe_seconds > 0),
            configuration_version TEXT NOT NULL,
            manifest_sha256 TEXT NOT NULL,
            config_sha256 TEXT NOT NULL,
            runtime_phase TEXT NOT NULL,
            strategy_state_version INTEGER NOT NULL CHECK (strategy_state_version > 0),
            state_json TEXT NOT NULL,
            state_sha256 TEXT NOT NULL,
            last_candle_id TEXT NOT NULL,
            last_close_time_ms INTEGER NOT NULL CHECK (last_close_time_ms >= 0),
            candles_seen INTEGER NOT NULL CHECK (candles_seen > 0),
            created_at_ms INTEGER NOT NULL CHECK (created_at_ms >= 0),
            UNIQUE(
                strategy_id, strategy_version, broker, account_id, product, symbol,
                timeframe_seconds, configuration_version, last_close_time_ms
            )
        )
        """,
        """
        CREATE INDEX ix_warmup_checkpoint_context
        ON warmup_checkpoints(
            strategy_id, strategy_version, broker, account_id, product, symbol,
            timeframe_seconds, configuration_version, last_close_time_ms DESC
        )
        """,
    ),
)

STRATEGY_DATA_V2 = StrategyDataMigration(
    version=2,
    name="0002_strategy_validation_reports",
    statements=(
        """
        CREATE TABLE strategy_validation_reports (
            report_id TEXT PRIMARY KEY,
            strategy_id TEXT NOT NULL,
            strategy_version TEXT NOT NULL,
            code_hash TEXT NOT NULL,
            stage TEXT NOT NULL,
            is_approved INTEGER NOT NULL CHECK (is_approved IN (0, 1)),
            metrics_json TEXT NOT NULL,
            dataset_hash TEXT NOT NULL,
            created_at_utc TEXT NOT NULL
        )
        """,
        """
        CREATE INDEX ix_strategy_validation_reports_strategy
        ON strategy_validation_reports(strategy_id, strategy_version, stage, is_approved)
        """,
    ),
)

STRATEGY_DATA_MIGRATIONS = (STRATEGY_DATA_V1, STRATEGY_DATA_V2)


class StrategyDataDatabase:
    """Single local writer boundary for non-financial strategy evidence only."""

    def __init__(
        self,
        path: Path,
        *,
        fault_injector: Callable[[str], None] | None = None,
    ) -> None:
        if path.name.casefold() == "state.db":
            raise ValueError("strategy evidence must never use state.db")
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._lock = threading.RLock()
        self._fault_injector = fault_injector
        try:
            self._connection = sqlite3.connect(
                path,
                timeout=STRATEGY_BUSY_TIMEOUT_MS / 1_000,
                isolation_level=None,
                check_same_thread=False,
            )
            self._connection.row_factory = sqlite3.Row
            self._connection.execute(f"PRAGMA busy_timeout = {STRATEGY_BUSY_TIMEOUT_MS}")
            self._connection.execute("PRAGMA foreign_keys = ON")
            self._verify_integrity()
            self._connection.execute("PRAGMA journal_mode = WAL")
            self._connection.execute("PRAGMA synchronous = FULL")
            self._apply_migrations()
        except Exception:
            if hasattr(self, "_connection"):
                self._connection.close()
            raise

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    @contextmanager
    def transaction(self, label: str | None = None) -> Iterator[sqlite3.Connection]:
        with self._lock:
            try:
                self._connection.execute("BEGIN IMMEDIATE")
                yield self._connection
                if label is not None and self._fault_injector is not None:
                    self._fault_injector(f"before_{label}_commit")
                self._connection.execute("COMMIT")
                if label is not None and self._fault_injector is not None:
                    self._fault_injector(f"after_{label}_commit")
            except sqlite3.Error as exc:
                if self._connection.in_transaction:
                    self._connection.execute("ROLLBACK")
                raise StrategyDataError("strategy data transaction failed") from exc
            except Exception:
                if self._connection.in_transaction:
                    self._connection.execute("ROLLBACK")
                raise

    def query(self, sql: str, parameters: tuple[object, ...] = ()) -> tuple[sqlite3.Row, ...]:
        with self._lock:
            try:
                return tuple(self._connection.execute(sql, parameters).fetchall())
            except sqlite3.Error as exc:
                raise StrategyDataError("strategy data query failed") from exc

    def _verify_integrity(self) -> None:
        try:
            rows = self._connection.execute("PRAGMA quick_check").fetchall()
        except sqlite3.Error as exc:
            raise StrategyDataIntegrityError("strategy data integrity check failed") from exc
        if tuple(str(row[0]) for row in rows) != ("ok",):
            raise StrategyDataIntegrityError("strategy data is corrupt")

    def _apply_migrations(self) -> None:
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS strategy_schema_migrations (
                    version INTEGER PRIMARY KEY,
                    name TEXT NOT NULL UNIQUE,
                    checksum TEXT NOT NULL,
                    applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            applied = {
                int(row["version"]): (str(row["name"]), str(row["checksum"]))
                for row in self._connection.execute(
                    "SELECT version, name, checksum FROM strategy_schema_migrations"
                )
            }
            known_versions = {migration.version for migration in STRATEGY_DATA_MIGRATIONS}
            if any(version not in known_versions for version in applied):
                raise StrategyDataMigrationError("unsupported strategy data migration")
            for migration in STRATEGY_DATA_MIGRATIONS:
                existing = applied.get(migration.version)
                if existing is not None:
                    if existing != (migration.name, migration.checksum):
                        raise StrategyDataMigrationError(
                            "strategy data migration checksum mismatch"
                        )
                    continue
                for statement in migration.statements:
                    self._connection.execute(statement)
                self._connection.execute(
                    """
                    INSERT INTO strategy_schema_migrations(version, name, checksum)
                    VALUES (?, ?, ?)
                    """,
                    (migration.version, migration.name, migration.checksum),
                )
            self._connection.execute("COMMIT")
        except Exception as exc:
            if self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            if isinstance(exc, StrategyDataMigrationError):
                raise
            raise StrategyDataMigrationError("strategy data migration failed") from exc
