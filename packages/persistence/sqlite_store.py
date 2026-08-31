"""Small, transactional SQLite state store for the enterprise order foundation."""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from packages.domain.events import OrderEvent
from packages.domain.orders import Order, OrderState

SCHEMA_VERSION = 1


class SQLiteStateStore:
    """Owns the foundation tables; callers receive immutable domain objects."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._connection = sqlite3.connect(self.path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA foreign_keys = ON")
        self._connection.execute("PRAGMA journal_mode = WAL")
        self._create_schema()

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def _create_schema(self) -> None:
        with self._lock, self._connection:
            self._connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS schema_versions (
                    name TEXT PRIMARY KEY,
                    version INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS accounts (
                    account_id TEXT PRIMARY KEY,
                    broker TEXT NOT NULL,
                    environment TEXT NOT NULL,
                    currency TEXT,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS orders (
                    internal_order_id TEXT PRIMARY KEY,
                    dedupe_key TEXT NOT NULL UNIQUE,
                    account_id TEXT NOT NULL,
                    strategy_id TEXT NOT NULL,
                    asset TEXT NOT NULL,
                    direction TEXT NOT NULL,
                    amount TEXT NOT NULL,
                    duration INTEGER NOT NULL,
                    state TEXT NOT NULL,
                    timestamps_json TEXT NOT NULL,
                    fencing_token TEXT NOT NULL,
                    reconciliation_id TEXT
                );
                CREATE INDEX IF NOT EXISTS ix_orders_account_state
                    ON orders(account_id, state);
                CREATE TABLE IF NOT EXISTS order_events (
                    event_id TEXT PRIMARY KEY,
                    order_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    correlation_id TEXT NOT NULL,
                    FOREIGN KEY(order_id) REFERENCES orders(internal_order_id)
                );
                CREATE INDEX IF NOT EXISTS ix_order_events_order
                    ON order_events(order_id, timestamp);
                CREATE TABLE IF NOT EXISTS order_reservations (
                    reservation_id TEXT PRIMARY KEY,
                    order_id TEXT NOT NULL,
                    amount TEXT NOT NULL,
                    currency TEXT NOT NULL,
                    state TEXT NOT NULL DEFAULT 'ACTIVE',
                    created_at TEXT NOT NULL,
                    released_at TEXT,
                    FOREIGN KEY(order_id) REFERENCES orders(internal_order_id)
                );
                CREATE INDEX IF NOT EXISTS ix_reservations_order_state
                    ON order_reservations(order_id, state);
                CREATE TABLE IF NOT EXISTS idempotency_keys (
                    dedupe_key TEXT PRIMARY KEY,
                    intent_id TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                INSERT INTO schema_versions(name, version) VALUES ('enterprise_foundation', 1)
                    ON CONFLICT(name) DO NOTHING;
                """
            )

    def save_order(self, order: Order) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                """INSERT INTO orders(
                    internal_order_id, dedupe_key, account_id, strategy_id, asset, direction,
                    amount, duration, state, timestamps_json, fencing_token, reconciliation_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(internal_order_id) DO UPDATE SET
                    state=excluded.state, timestamps_json=excluded.timestamps_json,
                    fencing_token=excluded.fencing_token,
                    reconciliation_id=excluded.reconciliation_id""",
                (
                    order.internal_order_id,
                    order.dedupe_key,
                    order.account_id,
                    order.strategy_id,
                    order.asset,
                    order.direction,
                    str(order.amount),
                    order.duration,
                    order.state.value,
                    json.dumps({key: value.isoformat() for key, value in order.timestamps.items()}),
                    order.fencing_token,
                    order.reconciliation_id,
                ),
            )

    def get_order(self, internal_order_id: str) -> Order | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM orders WHERE internal_order_id = ?", (internal_order_id,)
            ).fetchone()
        if row is None:
            return None
        timestamps = {
            key: datetime.fromisoformat(value).astimezone(UTC)
            for key, value in json.loads(row["timestamps_json"]).items()
        }
        return Order(
            internal_order_id=row["internal_order_id"],
            dedupe_key=row["dedupe_key"],
            account_id=row["account_id"],
            strategy_id=row["strategy_id"],
            asset=row["asset"],
            direction=row["direction"],
            amount=Decimal(row["amount"]),
            duration=row["duration"],
            state=OrderState(row["state"]),
            timestamps=timestamps,
            fencing_token=row["fencing_token"],
            reconciliation_id=row["reconciliation_id"],
        )

    def list_orders(self, account_id: str | None = None) -> list[Order]:
        with self._lock:
            if account_id is None:
                rows = self._connection.execute(
                    "SELECT internal_order_id FROM orders ORDER BY internal_order_id"
                ).fetchall()
            else:
                rows = self._connection.execute(
                    "SELECT internal_order_id FROM orders "
                    "WHERE account_id=? ORDER BY internal_order_id",
                    (account_id,),
                ).fetchall()
        return [order for row in rows if (order := self.get_order(str(row[0]))) is not None]

    def save_event(self, event: OrderEvent) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                """INSERT OR IGNORE INTO order_events(
                    event_id, order_id, event_type, timestamp, payload_json, correlation_id
                ) VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    event.event_id,
                    event.order_id,
                    event.event_type,
                    event.timestamp.isoformat(),
                    json.dumps(dict(event.payload), default=str),
                    event.correlation_id,
                ),
            )

    def get_events(self, order_id: str) -> list[OrderEvent]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT * FROM order_events WHERE order_id = ? ORDER BY timestamp, event_id",
                (order_id,),
            ).fetchall()
        return [
            OrderEvent(
                event_id=row["event_id"],
                order_id=row["order_id"],
                event_type=row["event_type"],
                timestamp=datetime.fromisoformat(row["timestamp"]).astimezone(UTC),
                payload=json.loads(row["payload_json"]),
                correlation_id=row["correlation_id"],
            )
            for row in rows
        ]

    def save_reservation(
        self,
        reservation_id: str,
        order_id: str,
        amount: Decimal,
        currency: str,
    ) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                """INSERT INTO order_reservations(
                    reservation_id, order_id, amount, currency, state, created_at
                ) VALUES (?, ?, ?, ?, 'ACTIVE', ?)""",
                (reservation_id, order_id, str(amount), currency, datetime.now(UTC).isoformat()),
            )

    def release_reservation(self, reservation_id: str) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                """UPDATE order_reservations SET state='RELEASED', released_at=?
                WHERE reservation_id=? AND state='ACTIVE'""",
                (datetime.now(UTC).isoformat(), reservation_id),
            )

    def save_idempotency_key(self, dedupe_key: str, intent_id: str) -> bool:
        with self._lock, self._connection:
            cursor = self._connection.execute(
                "INSERT OR IGNORE INTO idempotency_keys"
                "(dedupe_key, intent_id, created_at) VALUES (?, ?, ?)",
                (dedupe_key, intent_id, datetime.now(UTC).isoformat()),
            )
        return cursor.rowcount == 1

    def idempotency_key_exists(self, dedupe_key: str) -> bool:
        with self._lock:
            row = self._connection.execute(
                "SELECT 1 FROM idempotency_keys WHERE dedupe_key=?", (dedupe_key,)
            ).fetchone()
        return row is not None

    def get_schema_version(self, name: str = "enterprise_foundation") -> int | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT version FROM schema_versions WHERE name=?", (name,)
            ).fetchone()
        return None if row is None else int(row[0])

    def set_schema_version(self, version: int, name: str = "enterprise_foundation") -> None:
        with self._lock, self._connection:
            self._connection.execute(
                "INSERT INTO schema_versions(name, version) VALUES (?, ?)"
                " ON CONFLICT(name) DO UPDATE SET version=excluded.version",
                (name, version),
            )


SQLiteStore = SQLiteStateStore

__all__ = ["SCHEMA_VERSION", "SQLiteStateStore", "SQLiteStore"]
