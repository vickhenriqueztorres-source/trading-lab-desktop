from __future__ import annotations

from contextlib import closing
from datetime import datetime
from pathlib import Path
from typing import Any

from packages.persistence.database import open_reader_connection


class StateReader:
    """Read-only projection interface; PRAGMA query_only prevents accidental writes."""

    def __init__(self, path: Path) -> None:
        self._path = path

    def count(self, table: str) -> int:
        allowed = {
            "trade_intents",
            "risk_reservations",
            "outbox_messages",
            "orders",
            "processed_order_events",
            "reconciliation_attempts",
            "reconciliation_evidence",
            "broker_order_events",
            "digit_risk_runtime",
            "schema_migrations",
        }
        if table not in allowed:
            raise ValueError("unsupported table")
        with closing(open_reader_connection(self._path)) as connection:
            row = connection.execute(f"SELECT COUNT(*) AS total FROM {table}").fetchone()
            return int(row["total"])

    def one(self, table: str, key_name: str, key_value: str) -> dict[str, Any] | None:
        keys = {
            "trade_intents": "intent_id",
            "risk_reservations": "reservation_id",
            "outbox_messages": "message_id",
            "orders": "order_id",
            "reconciliation_attempts": "attempt_id",
            "reconciliation_evidence": "evidence_id",
            "broker_order_events": "event_id",
        }
        if keys.get(table) != key_name:
            raise ValueError("unsupported lookup")
        with closing(open_reader_connection(self._path)) as connection:
            row = connection.execute(
                f"SELECT * FROM {table} WHERE {key_name} = ?", (key_value,)
            ).fetchone()
            return dict(row) if row is not None else None

    def order_for_intent(self, intent_id: str) -> dict[str, Any] | None:
        with closing(open_reader_connection(self._path)) as connection:
            row = connection.execute(
                "SELECT * FROM orders WHERE intent_id = ?", (intent_id,)
            ).fetchone()
            return dict(row) if row is not None else None

    def reservation_for_intent(self, intent_id: str) -> dict[str, Any] | None:
        with closing(open_reader_connection(self._path)) as connection:
            row = connection.execute(
                "SELECT * FROM risk_reservations WHERE intent_id = ?", (intent_id,)
            ).fetchone()
            return dict(row) if row is not None else None

    def outbox_for_intent(self, intent_id: str) -> dict[str, Any] | None:
        with closing(open_reader_connection(self._path)) as connection:
            row = connection.execute(
                "SELECT * FROM outbox_messages WHERE intent_id = ?", (intent_id,)
            ).fetchone()
            return dict(row) if row is not None else None

    def list_by_state(self, table: str, state: str) -> list[dict[str, Any]]:
        if table not in {"orders", "risk_reservations", "outbox_messages"}:
            raise ValueError("unsupported state lookup")
        with closing(open_reader_connection(self._path)) as connection:
            rows = connection.execute(
                f"SELECT * FROM {table} WHERE state = ? ORDER BY created_at", (state,)
            ).fetchall()
            return [dict(row) for row in rows]

    def list_nonterminal_orders(self) -> list[dict[str, Any]]:
        with closing(open_reader_connection(self._path)) as connection:
            rows = connection.execute(
                """
                SELECT * FROM orders
                WHERE state NOT IN ('SETTLED', 'REJECTED')
                ORDER BY created_at
                """
            ).fetchall()
            return [dict(row) for row in rows]

    def list_reconciliation_candidates(self) -> list[dict[str, Any]]:
        with closing(open_reader_connection(self._path)) as connection:
            rows = connection.execute(
                """
                SELECT o.order_id, o.intent_id, o.correlation_id, o.broker,
                       o.account_id, o.broker_order_id, o.state AS order_state,
                       o.created_at AS order_created_at,
                       ti.product, ti.symbol, ti.direction, ti.amount_minor, ti.currency,
                       ob.message_id, ob.state AS outbox_state, ob.attempt_count,
                       rr.reservation_id, rr.state AS reservation_state
                FROM orders o
                JOIN trade_intents ti ON ti.intent_id = o.intent_id
                JOIN outbox_messages ob ON ob.intent_id = o.intent_id
                JOIN risk_reservations rr ON rr.intent_id = o.intent_id
                WHERE o.state IN ('ACCEPTED', 'OPEN', 'UNKNOWN', 'SETTLEMENT_UNKNOWN')
                ORDER BY o.created_at
                """
            ).fetchall()
            return [dict(row) for row in rows]

    def reconciliation_candidate(self, order_id: str) -> dict[str, Any] | None:
        """Load one exact order projection, including terminal state for gap verification."""
        with closing(open_reader_connection(self._path)) as connection:
            row = connection.execute(
                """
                SELECT o.order_id, o.intent_id, o.correlation_id, o.broker,
                       o.account_id, o.broker_order_id, o.state AS order_state,
                       o.created_at AS order_created_at,
                       ti.product, ti.symbol, ti.direction, ti.amount_minor, ti.currency,
                       ob.message_id, ob.state AS outbox_state, ob.attempt_count,
                       rr.reservation_id, rr.state AS reservation_state
                FROM orders o
                JOIN trade_intents ti ON ti.intent_id = o.intent_id
                JOIN outbox_messages ob ON ob.intent_id = o.intent_id
                JOIN risk_reservations rr ON rr.intent_id = o.intent_id
                WHERE o.order_id = ?
                """,
                (order_id,),
            ).fetchone()
            return dict(row) if row is not None else None

    def reconciliation_attempts_for_order(self, order_id: str) -> list[dict[str, Any]]:
        with closing(open_reader_connection(self._path)) as connection:
            rows = connection.execute(
                """
                SELECT * FROM reconciliation_attempts
                WHERE order_id = ? ORDER BY started_at, attempt_id
                """,
                (order_id,),
            ).fetchall()
            return [dict(row) for row in rows]

    def reconciliation_evidence_for_order(self, order_id: str) -> list[dict[str, Any]]:
        with closing(open_reader_connection(self._path)) as connection:
            rows = connection.execute(
                """
                SELECT * FROM reconciliation_evidence
                WHERE order_id = ? ORDER BY observed_at, evidence_id
                """,
                (order_id,),
            ).fetchall()
            return [dict(row) for row in rows]

    def broker_events_for_order(self, order_id: str) -> list[dict[str, Any]]:
        with closing(open_reader_connection(self._path)) as connection:
            rows = connection.execute(
                """
                SELECT * FROM broker_order_events
                WHERE order_id = ? ORDER BY created_at, event_id
                """,
                (order_id,),
            ).fetchall()
            return [dict(row) for row in rows]

    def financial_effect_counts(self, order_id: str) -> dict[str, int]:
        with closing(open_reader_connection(self._path)) as connection:
            row = connection.execute(
                """
                SELECT o.pnl_application_count, rr.release_count
                FROM orders o
                JOIN risk_reservations rr ON rr.intent_id = o.intent_id
                WHERE o.order_id = ?
                """,
                (order_id,),
            ).fetchone()
            if row is None:
                raise ValueError("order not found")
            return {
                "pnl_application_count": int(row["pnl_application_count"]),
                "reservation_release_count": int(row["release_count"]),
            }

    def digit_risk_runtime(self) -> dict[str, Any] | None:
        """Return the singleton durable Digit Edge progression projection."""

        with closing(open_reader_connection(self._path)) as connection:
            row = connection.execute(
                "SELECT * FROM digit_risk_runtime WHERE singleton_id = 1"
            ).fetchone()
            return dict(row) if row is not None else None

    def has_nonterminal_deriv_digit_order(self) -> bool:
        with closing(open_reader_connection(self._path)) as connection:
            row = connection.execute(
                """
                SELECT 1
                FROM orders o
                JOIN trade_intents ti ON ti.intent_id = o.intent_id
                WHERE o.broker = 'DERIV'
                  AND ti.product IN (
                      'DIGITDIFF', 'DIGITOVER', 'DIGITUNDER', 'DIGITEVEN', 'DIGITODD'
                  )
                  AND o.state NOT IN ('SETTLED', 'REJECTED')
                LIMIT 1
                """
            ).fetchone()
            return row is not None

    def ui_order_summaries(self, *, limit: int = 50) -> list[dict[str, Any]]:
        """Return a bounded, read-only order projection for the UI service."""

        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("UI order projection limit is outside bounds")
        with closing(open_reader_connection(self._path)) as connection:
            rows = connection.execute(
                """
                SELECT o.order_id, o.broker, ti.symbol, ti.direction,
                       ti.amount_minor, ti.currency, o.state, o.created_at,
                       o.broker_order_id, o.realized_pnl_minor
                FROM orders o
                JOIN trade_intents ti ON ti.intent_id = o.intent_id
                ORDER BY o.created_at DESC, o.order_id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
            return [dict(row) for row in rows]

    def daily_realized_pnl_by_currency(self, *, since_utc: datetime) -> dict[str, int]:
        """Aggregate only settled P&L, preserving currency boundaries."""

        offset = since_utc.utcoffset()
        if since_utc.tzinfo is None or offset is None or offset.total_seconds() != 0:
            raise ValueError("P&L projection boundary must be timezone-aware UTC")
        with closing(open_reader_connection(self._path)) as connection:
            rows = connection.execute(
                """
                SELECT ti.currency, SUM(o.realized_pnl_minor) AS pnl_minor
                FROM orders o
                JOIN trade_intents ti ON ti.intent_id = o.intent_id
                WHERE o.state = 'SETTLED'
                  AND o.realized_pnl_minor IS NOT NULL
                  AND o.updated_at >= ?
                GROUP BY ti.currency
                ORDER BY ti.currency
                """,
                (since_utc.isoformat(),),
            ).fetchall()
            return {str(row["currency"]): int(row["pnl_minor"]) for row in rows}

    def deriv_strategy_performance(
        self,
        strategy_id: str,
        *,
        symbol: str,
        limit: int = 200,
    ) -> dict[str, int | float | str | None]:
        """Recent settled evidence for one strategy and synthetic index.

        The circuit breaker is intentionally scoped to an asset: a poor result on R_10 must
        not suppress independent, warmed signals on R_25/R_50/R_75/R_100.
        """

        if not strategy_id.strip() or len(strategy_id) > 128:
            raise ValueError("strategy performance identity is invalid")
        if not symbol.strip() or len(symbol) > 64:
            raise ValueError("strategy performance symbol is invalid")
        if type(limit) is not int or not 30 <= limit <= 1000:
            raise ValueError("strategy performance limit is outside bounds")
        with closing(open_reader_connection(self._path)) as connection:
            row = connection.execute(
                """
                SELECT COUNT(1) AS settled_count,
                       SUM(CASE WHEN realized_pnl_minor > 0 THEN 1 ELSE 0 END) AS wins,
                       SUM(CASE WHEN realized_pnl_minor < 0 THEN 1 ELSE 0 END) AS losses,
                       SUM(realized_pnl_minor) AS total_pnl_minor,
                       AVG(CASE WHEN realized_pnl_minor > 0
                                THEN realized_pnl_minor END) AS avg_win_minor,
                       AVG(CASE WHEN realized_pnl_minor < 0
                                THEN -realized_pnl_minor END) AS avg_loss_minor,
                       MAX(updated_at) AS last_settled_at
                FROM (
                    SELECT o.realized_pnl_minor, o.updated_at
                    FROM orders o
                    JOIN trade_intents ti ON ti.intent_id = o.intent_id
                    WHERE o.broker = 'DERIV'
                      AND o.state = 'SETTLED'
                      AND o.realized_pnl_minor IS NOT NULL
                      AND ti.strategy_id = ?
                      AND ti.symbol = ?
                    ORDER BY o.updated_at DESC, o.order_id DESC
                    LIMIT ?
                )
                """,
                (strategy_id, symbol, limit),
            ).fetchone()
            if row is None:
                return {
                    "settled_count": 0,
                    "wins": 0,
                    "losses": 0,
                    "total_pnl_minor": 0,
                    "avg_win_minor": None,
                    "avg_loss_minor": None,
                    "last_settled_at": None,
                }
            return dict(row)
