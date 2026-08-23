from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

from packages.domain.models import (
    Broker,
    BrokerOrderEvent,
    Direction,
    ExternalOrderStatus,
    Money,
    OrderCommand,
    OrderStatusQuery,
    ReconciliationEvidence,
    ReconciliationSource,
)


class SimulatedBrokerConflict(RuntimeError):
    pass


class SimulatedBrokerStore:
    """Durable synthetic external authority; never imported by the Trading Core."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._lock = threading.RLock()
        self._connection = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute("PRAGMA synchronous=FULL")
        self._connection.execute("PRAGMA foreign_keys=ON")
        self._connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS external_orders (
                client_order_ref TEXT PRIMARY KEY,
                broker_order_id TEXT NOT NULL UNIQUE,
                broker TEXT NOT NULL,
                account_id TEXT NOT NULL,
                product TEXT NOT NULL,
                symbol TEXT NOT NULL,
                direction TEXT NOT NULL,
                amount_minor INTEGER NOT NULL CHECK (amount_minor > 0),
                currency TEXT NOT NULL CHECK (length(currency) = 3),
                correlation_id TEXT NOT NULL,
                status TEXT NOT NULL,
                submitted_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                settled_at TEXT,
                realized_pnl_minor INTEGER,
                evidence_version INTEGER NOT NULL CHECK (evidence_version > 0)
            );
            CREATE TABLE IF NOT EXISTS broker_metrics (
                metric_name TEXT PRIMARY KEY,
                metric_value INTEGER NOT NULL CHECK (metric_value >= 0)
            );
            CREATE TABLE IF NOT EXISTS simulated_broker_events (
                event_id TEXT PRIMARY KEY,
                client_order_ref TEXT NOT NULL,
                external_sequence INTEGER,
                external_status TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                delivery_count INTEGER NOT NULL DEFAULT 0 CHECK (delivery_count >= 0),
                created_at TEXT NOT NULL,
                FOREIGN KEY(client_order_ref) REFERENCES external_orders(client_order_ref)
            );
            INSERT OR IGNORE INTO broker_metrics(metric_name, metric_value)
            VALUES ('submit_count', 0), ('status_query_count', 0),
                   ('event_delivery_count', 0);
            """
        )

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def _increment(self, connection: sqlite3.Connection, metric: str) -> None:
        connection.execute(
            "UPDATE broker_metrics SET metric_value = metric_value + 1 WHERE metric_name = ?",
            (metric,),
        )

    def record_submission(
        self,
        command: OrderCommand,
        status: ExternalOrderStatus,
        *,
        realized_pnl_minor: int | None = None,
    ) -> ReconciliationEvidence:
        if status is ExternalOrderStatus.SETTLED and realized_pnl_minor is None:
            raise ValueError("SETTLED external order requires realized P&L")
        if status is not ExternalOrderStatus.SETTLED and realized_pnl_minor is not None:
            raise ValueError("realized P&L is only valid for SETTLED external orders")
        now = datetime.now(UTC)
        broker_order_id = f"SIM-{command.message_id}"
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                self._increment(self._connection, "submit_count")
                existing = self._connection.execute(
                    "SELECT * FROM external_orders WHERE client_order_ref = ?",
                    (command.order_id,),
                ).fetchone()
                if existing is None:
                    self._connection.execute(
                        """
                        INSERT INTO external_orders(
                            client_order_ref, broker_order_id, broker, account_id, product,
                            symbol, direction, amount_minor, currency, correlation_id,
                            status, submitted_at,
                            updated_at, settled_at, realized_pnl_minor, evidence_version
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
                        """,
                        (
                            command.order_id,
                            broker_order_id,
                            command.broker.value,
                            command.account_id,
                            command.product,
                            command.symbol,
                            command.direction.value,
                            command.amount.minor_units,
                            command.amount.currency,
                            command.correlation_id,
                            status.value,
                            now.isoformat(),
                            now.isoformat(),
                            now.isoformat() if status is ExternalOrderStatus.SETTLED else None,
                            realized_pnl_minor,
                        ),
                    )
                else:
                    expected = (
                        broker_order_id,
                        command.broker.value,
                        command.account_id,
                        command.product,
                        command.symbol,
                        command.direction.value,
                        command.amount.minor_units,
                        command.amount.currency,
                    )
                    actual = tuple(
                        existing[name]
                        for name in (
                            "broker_order_id",
                            "broker",
                            "account_id",
                            "product",
                            "symbol",
                            "direction",
                            "amount_minor",
                            "currency",
                        )
                    )
                    if actual != expected:
                        raise SimulatedBrokerConflict(
                            "client_order_ref already exists with conflicting external fields"
                        )
                row = self._connection.execute(
                    "SELECT * FROM external_orders WHERE client_order_ref = ?",
                    (command.order_id,),
                ).fetchone()
                self._connection.execute("COMMIT")
            except Exception:
                self._connection.execute("ROLLBACK")
                raise
        if row is None:
            raise RuntimeError("external order was not persisted")
        return self._to_evidence(row)

    def record_status_query(self) -> None:
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                self._increment(self._connection, "status_query_count")
                self._connection.execute("COMMIT")
            except Exception:
                self._connection.execute("ROLLBACK")
                raise

    def query_order(self, query: OrderStatusQuery) -> ReconciliationEvidence | None:
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                self._increment(self._connection, "status_query_count")
                row = self._connection.execute(
                    """
                    SELECT * FROM external_orders
                    WHERE client_order_ref = ? AND broker = ? AND account_id = ?
                    """,
                    (query.client_order_ref, query.broker.value, query.account_id),
                ).fetchone()
                self._connection.execute("COMMIT")
            except Exception:
                self._connection.execute("ROLLBACK")
                raise
        return self._to_evidence(row) if row is not None else None

    def update_status(
        self,
        client_order_ref: str,
        status: ExternalOrderStatus,
        *,
        realized_pnl_minor: int | None = None,
    ) -> None:
        if status is ExternalOrderStatus.SETTLED and realized_pnl_minor is None:
            raise ValueError("SETTLED external order requires realized P&L")
        if status is not ExternalOrderStatus.SETTLED and realized_pnl_minor is not None:
            raise ValueError("realized P&L is only valid for SETTLED external orders")
        now = datetime.now(UTC)
        with self._lock:
            changed = self._connection.execute(
                """
                UPDATE external_orders
                SET status = ?, updated_at = ?, settled_at = ?, realized_pnl_minor = ?,
                    evidence_version = evidence_version + 1
                WHERE client_order_ref = ?
                """,
                (
                    status.value,
                    now.isoformat(),
                    now.isoformat() if status is ExternalOrderStatus.SETTLED else None,
                    realized_pnl_minor,
                    client_order_ref,
                ),
            ).rowcount
        if changed != 1:
            raise KeyError("external order not found")

    def record_lifecycle_event(
        self,
        client_order_ref: str,
        status: ExternalOrderStatus,
        sequence: int | None,
        *,
        realized_pnl_minor: int | None = None,
    ) -> BrokerOrderEvent:
        """Persist external lifecycle truth separately from its IPC delivery state."""
        if status is ExternalOrderStatus.SETTLED and realized_pnl_minor is None:
            raise ValueError("SETTLED lifecycle event requires realized P&L")
        if status is not ExternalOrderStatus.SETTLED and realized_pnl_minor is not None:
            raise ValueError("result is only valid for SETTLED lifecycle event")
        event_key = f"{client_order_ref}:{sequence}:{status.value}"
        event_id = hashlib.sha256(event_key.encode()).hexdigest()
        with self._lock:
            existing = self._connection.execute(
                "SELECT payload_json FROM simulated_broker_events WHERE event_id = ?",
                (event_id,),
            ).fetchone()
            if existing is not None:
                return BrokerOrderEvent.from_payload(json.loads(str(existing["payload_json"])))
            now = datetime.now(UTC)
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                row = self._connection.execute(
                    "SELECT * FROM external_orders WHERE client_order_ref = ?",
                    (client_order_ref,),
                ).fetchone()
                if row is None:
                    raise KeyError("external order not found")
                self._connection.execute(
                    """
                    UPDATE external_orders
                    SET status = ?, updated_at = ?, settled_at = ?, realized_pnl_minor = ?,
                        evidence_version = evidence_version + 1
                    WHERE client_order_ref = ?
                    """,
                    (
                        status.value,
                        now.isoformat(),
                        now.isoformat() if status is ExternalOrderStatus.SETTLED else None,
                        realized_pnl_minor,
                        client_order_ref,
                    ),
                )
                canonical: dict[str, object] = {
                    "event_id": event_id,
                    "event_version": 1,
                    "broker": row["broker"],
                    "account_id": row["account_id"],
                    "client_order_ref": row["client_order_ref"],
                    "broker_order_id": row["broker_order_id"],
                    "correlation_id": row["correlation_id"],
                    "external_sequence": sequence,
                    "external_status": status.value,
                    "occurred_at": now.isoformat(),
                    "observed_at": now.isoformat(),
                    "product": row["product"],
                    "symbol": row["symbol"],
                    "direction": row["direction"],
                    "amount_minor": row["amount_minor"],
                    "currency": row["currency"],
                    "result_minor": realized_pnl_minor,
                    "result_currency": row["currency"] if realized_pnl_minor is not None else None,
                }
                payload = {
                    **canonical,
                    "evidence_hash": BrokerOrderEvent.evidence_hash_for_payload(canonical),
                }
                event = BrokerOrderEvent.from_payload(payload)
                self._connection.execute(
                    """
                    INSERT INTO simulated_broker_events(
                        event_id, client_order_ref, external_sequence, external_status,
                        payload_json, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        event.event_id,
                        client_order_ref,
                        sequence,
                        status.value,
                        json.dumps(event.to_payload(), sort_keys=True, separators=(",", ":")),
                        now.isoformat(),
                    ),
                )
                self._connection.execute("COMMIT")
            except Exception:
                self._connection.execute("ROLLBACK")
                raise
        return event

    def mark_event_delivered(self, event_id: str) -> None:
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                changed = self._connection.execute(
                    """
                    UPDATE simulated_broker_events
                    SET delivery_count = delivery_count + 1
                    WHERE event_id = ?
                    """,
                    (event_id,),
                ).rowcount
                if changed != 1:
                    raise KeyError("external lifecycle event not found")
                self._increment(self._connection, "event_delivery_count")
                self._connection.execute("COMMIT")
            except Exception:
                self._connection.execute("ROLLBACK")
                raise

    def metrics(self) -> dict[str, int]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT metric_name, metric_value FROM broker_metrics"
            ).fetchall()
        return {str(row["metric_name"]): int(row["metric_value"]) for row in rows}

    @classmethod
    def read_metrics(cls, path: Path) -> dict[str, int]:
        with closing(sqlite3.connect(path)) as connection:
            rows = connection.execute(
                "SELECT metric_name, metric_value FROM broker_metrics"
            ).fetchall()
        return {str(name): int(value) for name, value in rows}

    @staticmethod
    def _to_evidence(row: sqlite3.Row) -> ReconciliationEvidence:
        canonical = {
            "client_order_ref": row["client_order_ref"],
            "broker_order_id": row["broker_order_id"],
            "broker": row["broker"],
            "account_id": row["account_id"],
            "product": row["product"],
            "symbol": row["symbol"],
            "direction": row["direction"],
            "amount_minor": row["amount_minor"],
            "currency": row["currency"],
            "status": row["status"],
            "updated_at": row["updated_at"],
            "realized_pnl_minor": row["realized_pnl_minor"],
            "evidence_version": row["evidence_version"],
        }
        encoded = json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()
        evidence_id = hashlib.sha256(encoded).hexdigest()
        raw_reference_hash = hashlib.sha256(str(row["broker_order_id"]).encode()).hexdigest()
        return ReconciliationEvidence(
            evidence_id=evidence_id,
            source=ReconciliationSource.STATUS_QUERY,
            observed_at=datetime.fromisoformat(str(row["updated_at"])),
            client_order_ref=str(row["client_order_ref"]),
            broker_order_id=str(row["broker_order_id"]),
            external_status=ExternalOrderStatus(str(row["status"])),
            broker=Broker(str(row["broker"])),
            account_id=str(row["account_id"]),
            product=str(row["product"]),
            symbol=str(row["symbol"]),
            direction=Direction(str(row["direction"])),
            amount=Money(int(row["amount_minor"]), str(row["currency"])),
            evidence_version=int(row["evidence_version"]),
            realized_pnl_minor=(
                int(row["realized_pnl_minor"]) if row["realized_pnl_minor"] is not None else None
            ),
            raw_reference_hash=raw_reference_hash,
        )
