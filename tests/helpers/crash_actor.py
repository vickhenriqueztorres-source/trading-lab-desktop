from __future__ import annotations

import json
import sys
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

from apps.core.instance import CoreInstanceGuard
from packages.domain.models import (
    Broker,
    BrokerOrderEvent,
    Direction,
    ExternalOrderStatus,
    Money,
    OrderCommand,
    OrderRequest,
    ReconciliationEvidence,
    ReconciliationSource,
    WorkerOutcome,
    utc_now,
)
from packages.persistence.database import open_writer_connection
from packages.persistence.migrations import apply_migrations
from packages.persistence.writer import SingleDatabaseWriter

INTENT_ID = "crash-intent-001"
RESERVATION_ID = "crash-reservation-001"
MESSAGE_ID = "crash-message-001"
ORDER_ID = "crash-order-001"
RECONCILIATION_ATTEMPT_ID = "crash-reconciliation-attempt-001"
EVIDENCE_ID = "crash-reconciliation-evidence-001"


def request() -> OrderRequest:
    return OrderRequest(
        correlation_id="crash-correlation-001",
        broker=Broker.DERIV,
        account_id="crash-demo-account",
        product="DIGITAL_OPTION",
        symbol="EURUSD",
        direction=Direction.CALL,
        amount=Money(1_000, "USD"),
        strategy_id="crash-test-strategy",
        strategy_version="1.0.0",
        deadline_at=datetime.now(UTC) + timedelta(hours=1),
    )


def command(order_request: OrderRequest) -> OrderCommand:
    return OrderCommand(
        message_id=MESSAGE_ID,
        correlation_id=order_request.correlation_id,
        intent_id=INTENT_ID,
        order_id=ORDER_ID,
        broker=order_request.broker,
        account_id=order_request.account_id,
        product=order_request.product,
        symbol=order_request.symbol,
        direction=order_request.direction,
        amount=order_request.amount,
        deadline_at=order_request.deadline_at,
    )


def persist_bundle(writer: SingleDatabaseWriter) -> OrderCommand:
    order_request = request()
    order_command = command(order_request)
    writer.persist_intent_reservation_outbox(
        request=order_request,
        command=order_command,
        intent_id=INTENT_ID,
        reservation_id=RESERVATION_ID,
        order_id=ORDER_ID,
        created_at=utc_now(),
    )
    return order_command


def signal_ready(ready_path: Path, action: str) -> None:
    ready_path.write_text(
        json.dumps(
            {
                "action": action,
                "intent_id": INTENT_ID,
                "reservation_id": RESERVATION_ID,
                "message_id": MESSAGE_ID,
                "order_id": ORDER_ID,
            }
        ),
        encoding="utf-8",
    )


def hold_forever() -> None:
    threading.Event().wait()


def reconciliation_evidence() -> ReconciliationEvidence:
    order_request = request()
    return ReconciliationEvidence(
        evidence_id=EVIDENCE_ID,
        source=ReconciliationSource.STATUS_QUERY,
        observed_at=utc_now(),
        client_order_ref=ORDER_ID,
        broker_order_id="SIM-CRASH-RECONCILED",
        external_status=ExternalOrderStatus.ACCEPTED,
        broker=order_request.broker,
        account_id=order_request.account_id,
        product=order_request.product,
        symbol=order_request.symbol,
        direction=order_request.direction,
        amount=order_request.amount,
        evidence_version=1,
    )


def prepare_unknown(writer: SingleDatabaseWriter) -> None:
    order_command = persist_bundle(writer)
    if writer.claim_next_message() is None:
        raise RuntimeError("test actor could not claim its outbox message")
    writer.record_dispatch_result(
        order_command,
        WorkerOutcome.TIMEOUT_AFTER_POSSIBLE_SEND.value,
    )
    writer.begin_reconciliation_attempt(
        RECONCILIATION_ATTEMPT_ID,
        ORDER_ID,
        order_command.correlation_id,
    )


def settlement_event() -> BrokerOrderEvent:
    order_request = request()
    now = utc_now()
    canonical: dict[str, object] = {
        "event_id": "crash-settlement-event-001",
        "event_version": 1,
        "broker": order_request.broker.value,
        "account_id": order_request.account_id,
        "client_order_ref": ORDER_ID,
        "broker_order_id": "SIM-CRASH-ACCEPTED",
        "correlation_id": order_request.correlation_id,
        "external_sequence": 3,
        "external_status": ExternalOrderStatus.SETTLED.value,
        "occurred_at": now.isoformat(),
        "observed_at": now.isoformat(),
        "product": order_request.product,
        "symbol": order_request.symbol,
        "direction": order_request.direction.value,
        "amount_minor": order_request.amount.minor_units,
        "currency": order_request.amount.currency,
        "result_minor": 250,
        "result_currency": order_request.amount.currency,
    }
    return BrokerOrderEvent.from_payload(
        {
            **canonical,
            "evidence_hash": BrokerOrderEvent.evidence_hash_for_payload(canonical),
        }
    )


def prepare_accepted(writer: SingleDatabaseWriter) -> None:
    order_command = persist_bundle(writer)
    if writer.claim_next_message() is None:
        raise RuntimeError("test actor could not claim its outbox message")
    writer.record_dispatch_result(
        order_command,
        WorkerOutcome.ACCEPTED.value,
        broker_order_id="SIM-CRASH-ACCEPTED",
    )


def crash_before_commit(profile: Path, ready_path: Path) -> None:
    database_path = profile / "state.db"
    initializer = SingleDatabaseWriter(database_path)
    initializer.close()
    connection = open_writer_connection(database_path)
    apply_migrations(connection)
    now = utc_now().isoformat()
    connection.execute("BEGIN IMMEDIATE")
    connection.execute(
        """
        INSERT INTO trade_intents(
            intent_id, correlation_id, broker, account_id, product, symbol,
            direction, amount_minor, currency, status, created_at,
            strategy_id, strategy_version
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            INTENT_ID,
            "crash-correlation-001",
            "DERIV",
            "crash-demo-account",
            "DIGITAL_OPTION",
            "EURUSD",
            "CALL",
            1_000,
            "USD",
            "CREATED",
            now,
            "crash-test-strategy",
            "1.0.0",
        ),
    )
    connection.execute(
        """
        INSERT INTO risk_reservations(
            reservation_id, intent_id, broker, account_id, amount_minor,
            currency, state, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            RESERVATION_ID,
            INTENT_ID,
            "DERIV",
            "crash-demo-account",
            1_000,
            "USD",
            "ACTIVE",
            now,
        ),
    )
    signal_ready(ready_path, "before_commit")
    hold_forever()


def run(action: str, profile: Path, ready_path: Path) -> None:
    profile.mkdir(parents=True, exist_ok=True)
    if action == "hold_lock":
        guard = CoreInstanceGuard(profile)
        guard.acquire()
        signal_ready(ready_path, action)
        hold_forever()
        return
    if action == "before_commit":
        crash_before_commit(profile, ready_path)
        return
    if action == "reconciliation_before_commit":

        def pause_before_commit(point: str) -> None:
            if point == "before_reconciliation_commit":
                signal_ready(ready_path, action)
                hold_forever()

        writer = SingleDatabaseWriter(
            profile / "state.db",
            fault_injector=pause_before_commit,
        )
        prepare_unknown(writer)
        writer.apply_reconciliation_evidence(
            RECONCILIATION_ATTEMPT_ID,
            reconciliation_evidence(),
        )
        return
    if action == "reconciliation_after_commit":
        writer = SingleDatabaseWriter(profile / "state.db")
        prepare_unknown(writer)
        writer.apply_reconciliation_evidence(
            RECONCILIATION_ATTEMPT_ID,
            reconciliation_evidence(),
        )
        signal_ready(ready_path, action)
        hold_forever()
        return
    if action == "settlement_before_commit":

        def pause_settlement(point: str) -> None:
            if point == "before_broker_event_commit":
                signal_ready(ready_path, action)
                hold_forever()

        writer = SingleDatabaseWriter(
            profile / "state.db",
            fault_injector=pause_settlement,
        )
        prepare_accepted(writer)
        writer.apply_normalized_broker_event(settlement_event())
        return
    if action == "settlement_after_commit":
        writer = SingleDatabaseWriter(profile / "state.db")
        prepare_accepted(writer)
        writer.apply_normalized_broker_event(settlement_event())
        signal_ready(ready_path, action)
        hold_forever()
        return

    writer = SingleDatabaseWriter(profile / "state.db")
    order_command = persist_bundle(writer)
    if action == "after_commit":
        signal_ready(ready_path, action)
        hold_forever()
        return
    claimed = writer.claim_next_message()
    if claimed is None:
        raise RuntimeError("test actor could not claim its outbox message")
    if action == "during_claim":
        signal_ready(ready_path, action)
        hold_forever()
        return
    if action == "accepted":
        writer.record_dispatch_result(
            order_command,
            WorkerOutcome.ACCEPTED.value,
            broker_order_id="SIM-CRASH-ACCEPTED",
        )
        signal_ready(ready_path, action)
        hold_forever()
        return
    raise ValueError(f"unknown crash action: {action}")


if __name__ == "__main__":
    run(sys.argv[1], Path(sys.argv[2]), Path(sys.argv[3]))
