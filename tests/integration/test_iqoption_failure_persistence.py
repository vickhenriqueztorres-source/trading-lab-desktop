"""Real SQLite/UOW and fake broker: ambiguity preserves exposure across restart."""

import sqlite3
from dataclasses import asdict, replace
from datetime import UTC, datetime, timedelta

import pytest

from apps.core.coordinator import OrderCoordinator
from apps.core.health import HealthGate
from apps.core.iqoption_failures import IQFailurePolicy
from apps.iqoption_worker.order_session import IQOptionOrderSession
from packages.domain.models import Broker
from packages.persistence.database import open_writer_connection
from packages.persistence.migrations import MIGRATIONS, apply_migrations
from packages.persistence.reader import StateReader
from packages.persistence.writer import SingleDatabaseWriter
from tests.unit.test_iqoption_failure_recovery import NOW, ReplyTransport, setup_trader


@pytest.mark.parametrize("response", [{}, {"status": True}, {"status": False, "id": 123}])
def test_ambiguous_reply_preserves_one_intent_outbox_and_reservation(
    tmp_path, order_request, response
):
    path = tmp_path / "state.db"
    writer = SingleDatabaseWriter(path)
    reader = StateReader(path)
    transport = ReplyTransport(response)
    gate = HealthGate()
    coordinator = OrderCoordinator(writer, IQOptionOrderSession(transport), gate)
    request = replace(
        order_request,
        broker=Broker.IQ_OPTION,
        account_id="IQOPTION_PRACTICE",
        symbol="EURUSD-OTC",
        product="BINARY_OPTION",
        duration=1,
        duration_unit="m",
        deadline_at=datetime.now(UTC) + timedelta(seconds=30),
    )
    try:
        result = coordinator.submit(request)
        assert reader.one("orders", "order_id", result.order_id)["state"] == "UNKNOWN"
        assert reader.outbox_for_intent(result.intent_id)["state"] == "AMBIGUOUS"
        assert reader.reservation_for_intent(result.intent_id)["state"] == "ACTIVE"
        assert reader.count("trade_intents") == reader.count("orders") == 1
        assert not gate.can_enter_order(Broker.IQ_OPTION.value, request.account_id)[0]
        assert gate.can_enter_order(Broker.DERIV.value, "DERIV_DEMO")[0]
        assert (
            coordinator.dispatch_pending(broker=Broker.DERIV.value, account_id="DERIV_DEMO") is None
        )
        assert transport.calls == ["buy"]
    finally:
        writer.close()
    restarted = SingleDatabaseWriter(path)
    try:
        assert reader.one("orders", "order_id", result.order_id)["state"] == "UNKNOWN"
        assert reader.reservation_for_intent(result.intent_id)["state"] == "ACTIVE"
        assert transport.calls == ["buy"]
    finally:
        restarted.close()


def test_crash_after_rejection_restores_failure_from_correlated_evidence(tmp_path, order_request):
    trader, runtime, _, clock, config, _, build = setup_trader(tmp_path)
    transport = ReplyTransport({"status": False, "reason": "IQOPTION_ACTIVE_SUSPENDED"})
    writer = runtime.writer
    coordinator = OrderCoordinator(writer, IQOptionOrderSession(transport), HealthGate())
    request = replace(
        order_request,
        broker=Broker.IQ_OPTION,
        account_id="IQOPTION_PRACTICE",
        product="BINARY_OPTION",
        symbol="EURUSD-OTC",
        duration=1,
        duration_unit="m",
        deadline_at=datetime.now(UTC) + timedelta(seconds=30),
    )
    writer.save_iqoption_execution_state(
        {
            "version": 1,
            "signals": {"EURUSD-OTC": int(NOW.timestamp())},
            "policy": IQFailurePolicy().dump(0, NOW),
            "pending": {
                "correlation_id": request.correlation_id,
                "symbol": request.symbol,
                "config": asdict(config[0]),
            },
        }
    )
    coordinator.submit(request)  # crash before result projection was saved
    clock[0] = 10
    try:
        restarted = build()
        restarted._evaluate_cycle()
        assert restarted._failures.failures["EURUSD-OTC"].reason == "IQOPTION_ACTIVE_SUSPENDED"
        assert not runtime.requests
        assert writer.load_iqoption_execution_state()["pending"] is None
    finally:
        writer.close()


def test_write_failure_prevents_buy_and_does_not_drop_pending_signal(tmp_path):
    trader, runtime, _, _, _, _, _ = setup_trader(tmp_path)
    writer = runtime.writer
    trader._restore_execution_state(runtime)

    def fail(_):
        raise OSError("simulated disk full")

    writer.save_iqoption_execution_state = fail
    try:
        with pytest.raises(OSError):
            trader._evaluate_cycle()
        assert runtime.requests == []
        assert trader._pending_dispatch is not None
    finally:
        writer.close()


def test_migration_8_upgrade_preserves_published_checksums(tmp_path):
    path = tmp_path / "state.db"
    connection = open_writer_connection(path)
    apply_migrations(connection, MIGRATIONS[:-1])
    before = connection.execute("SELECT version, checksum FROM schema_migrations").fetchall()
    connection.close()
    writer = SingleDatabaseWriter(path)
    writer.close()
    with sqlite3.connect(path) as connection:
        assert connection.execute(
            "SELECT version, checksum FROM schema_migrations WHERE version<9"
        ).fetchall() == [tuple(row) for row in before]
        assert connection.execute("SELECT max(version) FROM schema_migrations").fetchone()[0] == 9
