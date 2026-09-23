"""CLI tool and programmatic API for operator manual resolution of stuck orders.

Provides audited recovery operations (confirm not executed or resolve with broker evidence)
with CAS version validation, idempotency, and risk reservation release.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any
from uuid import uuid4

from packages.domain.models import OrderState
from packages.persistence.reader import StateReader
from packages.persistence.writer import PersistenceError, SingleDatabaseWriter


def default_database_path() -> Path:
    local_db = Path("state.db")
    if local_db.exists():
        return local_db
    local_appdata = os.getenv("LOCALAPPDATA")
    if local_appdata:
        p = Path(local_appdata) / "TradingLab" / "profiles" / "default" / "core" / "state.db"
        if p.exists():
            return p
    appdata = os.getenv("APPDATA")
    if appdata:
        appdata_db = Path(appdata) / "TradingLab" / "state.db"
        if appdata_db.exists():
            return appdata_db
    user_home = Path.home() / ".tradinglab" / "state.db"
    if user_home.exists():
        return user_home
    return local_db


def list_stuck_orders(reader: StateReader) -> list[dict[str, Any]]:
    manual_review = reader.list_manual_review_orders()
    nonterminal = reader.list_active_financial_exposures()
    all_orders: dict[str, dict[str, Any]] = {}
    for row in manual_review:
        all_orders[str(row["order_id"])] = row
    for row in nonterminal:
        oid = str(row["order_id"])
        if oid not in all_orders:
            all_orders[oid] = row
    return sorted(
        all_orders.values(),
        key=lambda r: str(r.get("created_at") or r.get("order_created_at") or ""),
    )


def inspect_order(reader: StateReader, order_id: str) -> dict[str, Any] | None:
    order = reader.reconciliation_candidate(order_id)
    if order is None:
        order = reader.one("orders", "order_id", order_id)
        if order is None:
            return None
    attempts = reader.reconciliation_attempts_for_order(order_id)
    evidence = reader.reconciliation_evidence_for_order(order_id)
    return {
        "order": order,
        "attempts": attempts,
        "evidence": evidence,
    }


def execute_manual_settlement(
    writer: SingleDatabaseWriter,
    reader: StateReader,
    *,
    order_id: str,
    broker_order_id: str,
    realized_pnl_minor: int,
    operator: str,
    reason: str,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    order = reader.one("orders", "order_id", order_id)
    if order is None:
        return {"success": False, "error": f"Order {order_id} not found"}

    expected_version = int(order.get("version", 1))
    current_state = str(order.get("state"))
    idemp_key = idempotency_key or f"manual-{uuid4().hex[:16]}"

    if current_state != OrderState.MANUAL_REVIEW.value:
        writer.record_reconciliation_conflict(
            order_id=order_id,
            attempt_id=None,
            reason_code="MANUAL_OPERATOR_REVIEW_REQUESTED",
            details=f"Transitioned to MANUAL_REVIEW by operator {operator}",
        )
        updated = reader.one("orders", "order_id", order_id)
        if updated is not None:
            expected_version = int(updated.get("version", expected_version + 1))

    try:
        writer.resolve_with_broker_evidence(
            order_id=order_id,
            expected_version=expected_version,
            idempotency_key=idemp_key,
            broker_order_id=broker_order_id,
            realized_pnl_minor=realized_pnl_minor,
            operator_id=operator,
            reason=reason,
        )
        return {
            "success": True,
            "order_id": order_id,
            "resolved_state": "SETTLED",
            "broker_order_id": broker_order_id,
            "realized_pnl_minor": realized_pnl_minor,
            "operator": operator,
            "idempotency_key": idemp_key,
        }
    except PersistenceError as exc:
        return {"success": False, "error": str(exc)}


def execute_confirm_not_executed(
    writer: SingleDatabaseWriter,
    reader: StateReader,
    *,
    order_id: str,
    operator: str,
    reason: str,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    order = reader.one("orders", "order_id", order_id)
    if order is None:
        return {"success": False, "error": f"Order {order_id} not found"}

    expected_version = int(order.get("version", 1))
    current_state = str(order.get("state"))
    idemp_key = idempotency_key or f"manual-{uuid4().hex[:16]}"

    if current_state != OrderState.MANUAL_REVIEW.value:
        writer.record_reconciliation_conflict(
            order_id=order_id,
            attempt_id=None,
            reason_code="MANUAL_OPERATOR_REVIEW_REQUESTED",
            details=f"Transitioned to MANUAL_REVIEW by operator {operator}",
        )
        updated = reader.one("orders", "order_id", order_id)
        if updated is not None:
            expected_version = int(updated.get("version", expected_version + 1))

    try:
        writer.confirm_not_executed(
            order_id=order_id,
            expected_version=expected_version,
            idempotency_key=idemp_key,
            operator_id=operator,
            reason=reason,
        )
        return {
            "success": True,
            "order_id": order_id,
            "resolved_state": "REJECTED",
            "operator": operator,
            "idempotency_key": idemp_key,
        }
    except PersistenceError as exc:
        return {"success": False, "error": str(exc)}


def main() -> int:
    parser = argparse.ArgumentParser(description="Trading Lab Desktop Order Recovery CLI")
    parser.add_argument("--db-path", type=Path, default=None, help="Path to state.db")
    parser.add_argument(
        "--list", action="store_true", help="List all pending / manual review orders"
    )
    parser.add_argument(
        "--inspect", type=str, metavar="ORDER_ID", help="Inspect order details and history"
    )
    parser.add_argument(
        "--resolve-settled", type=str, metavar="ORDER_ID", help="Resolve order as SETTLED"
    )
    parser.add_argument(
        "--confirm-not-executed", type=str, metavar="ORDER_ID", help="Resolve order as REJECTED"
    )
    parser.add_argument(
        "--broker-order-id", type=str, default="", help="Broker order ID for settlement"
    )
    parser.add_argument(
        "--pnl-minor", type=int, default=0, help="Realized PnL in minor units (e.g. 176 for $1.76)"
    )
    parser.add_argument("--operator", type=str, default="CLI_OPERATOR", help="Operator ID")
    parser.add_argument(
        "--reason", type=str, default="Manual resolution via CLI", help="Justification"
    )
    parser.add_argument("--idempotency-key", type=str, default=None, help="Custom idempotency key")

    args = parser.parse_args()
    db_path = args.db_path or default_database_path()

    if not db_path.exists():
        print(f"Error: database file not found at {db_path}", file=sys.stderr)
        return 1

    reader = StateReader(db_path)
    writer = SingleDatabaseWriter(db_path)

    if args.list:
        orders = list_stuck_orders(reader)
        print(json.dumps(orders, indent=2, default=str))
        return 0

    if args.inspect:
        details = inspect_order(reader, args.inspect)
        if details is None:
            print(f"Order {args.inspect} not found", file=sys.stderr)
            return 1
        print(json.dumps(details, indent=2, default=str))
        return 0

    if args.resolve_settled:
        if not args.broker_order_id:
            print("Error: --broker-order-id is required for resolving as settled", file=sys.stderr)
            return 1
        result = execute_manual_settlement(
            writer,
            reader,
            order_id=args.resolve_settled,
            broker_order_id=args.broker_order_id,
            realized_pnl_minor=args.pnl_minor,
            operator=args.operator,
            reason=args.reason,
            idempotency_key=args.idempotency_key,
        )
        print(json.dumps(result, indent=2, default=str))
        return 0 if result.get("success") else 1

    if args.confirm_not_executed:
        result = execute_confirm_not_executed(
            writer,
            reader,
            order_id=args.confirm_not_executed,
            operator=args.operator,
            reason=args.reason,
            idempotency_key=args.idempotency_key,
        )
        print(json.dumps(result, indent=2, default=str))
        return 0 if result.get("success") else 1

    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
