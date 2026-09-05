"""Actual Core admission and durable SPRT; all broker transports are local fakes."""

from __future__ import annotations

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest

from apps.core.families import EvalResult
from apps.core.iqoption_risk_config import IqOptionRiskConfig
from apps.core.live_monitor import LiveMonitor
from apps.core.payout_gate import PayoutGate
from apps.core.worker_client import SocketWorkerClient
from apps.iqoption_connection_worker.server import IQOptionReadOnlyWorkerServer
from packages.brokers.iqoption.community_read_only import (
    IQOptionAccountMode,
    IQOptionCommunityReadOnlySession,
    IQOptionExternalError,
)
from packages.domain.models import (
    Broker,
    BrokerEvent,
    Direction,
    ExternalOrderStatus,
    OrderState,
    ReconciliationEvidence,
    ReconciliationSource,
    WorkerOutcome,
)
from packages.persistence.writer import PersistenceError, SingleDatabaseWriter
from packages.protocol import EndpointRole, Envelope
from packages.security import SecretValue
from tests.integration.test_persistence_and_dispatch import build_coordinator
from tests.unit.test_iqoption_candidates import NOW, catalog, entry, trader_for


def execution_setup():
    cat = catalog(entry())
    cat.active_strategies["f5:a"].instance.evaluate_detailed = lambda candles, ctx: EvalResult(
        Direction.CALL, "OK", len(candles), 15, None, None, None
    )
    trader, client, runtime, clock = trader_for(
        cat, IqOptionRiskConfig(strategy_id="f5:a"), armed=True
    )
    return cat, trader, client, runtime, clock


@pytest.mark.parametrize(
    "payout",
    [Decimal("0.40"), Decimal("NaN"), Decimal("Infinity"), Decimal("-1"), Decimal("1.01"), None],
)
def test_invalid_or_low_payout_blocks_without_burning_signal(payout):
    _, trader, client, runtime, _ = execution_setup()
    client.iqoption_binary_payout = lambda symbol: payout
    trader._evaluate_cycle()
    assert not runtime.requests
    assert not trader._last_evaluated_epochs
    client.iqoption_binary_payout = lambda symbol: Decimal("0.85")
    trader._evaluate_cycle()
    assert len(runtime.requests) == 1
    assert json.loads(runtime.requests[0].manifest_context)["strategy_key"] == "f5:a"


def test_monitor_absence_blocks_even_valid_signal():
    _, trader, _, runtime, _ = execution_setup()
    trader._monitor_provider = lambda: None
    trader._evaluate_cycle()
    assert not runtime.requests
    assert trader.status_reason == "MANIFEST_MONITOR_UNAVAILABLE"


def test_budget_exhaustion_does_not_send_quote():
    from apps.core.iqoption_connection_safety import IQOptionMessageBudget

    _, trader, client, runtime, _ = execution_setup()
    trader._message_budget = IQOptionMessageBudget(limit=1)
    calls = []
    client.iqoption_binary_payout = lambda symbol: calls.append(symbol) or Decimal("0.85")
    trader._evaluate_cycle()  # Candle request consumes the single budget slot.
    assert calls == [] and runtime.requests == []
    assert trader.status_reason == "IQOPTION_MESSAGE_BUDGET_EXHAUSTED"


def test_lifecycle_rejects_unsigned_cache(tmp_path, monkeypatch):
    from apps.core.lifecycle_service import CoreLifecycleService

    monkeypatch.chdir(tmp_path)
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / "manifest.json").write_text(json.dumps({"strategies": [entry()]}), encoding="utf-8")
    service = CoreLifecycleService.__new__(CoreLifecycleService)
    service._profile_dir = tmp_path
    service._runtime = None
    service._manifest_catalog = catalog()
    service._load_local_manifest_catalog()
    assert not service._manifest_catalog.active_strategies
    assert service._manifest_load_reason != "MANIFEST_ACCEPTED"


@pytest.mark.parametrize("value", ["NaN", "sNaN", "Infinity", "-Infinity", "garbage", "101"])
def test_hostile_payout_is_fail_closed(value):
    assert not PayoutGate.check_payout(value, "0.60", "0.80").allowed


@pytest.mark.parametrize(
    "change,reason",
    [
        ("session", "IQOPTION_PAYOUT_STALE"),
        ("time", "IQOPTION_PAYOUT_STALE"),
        ("removed", "STRATEGY_NOT_FOUND"),
        ("real", "IQOPTION_REAL_ACCOUNT_FORBIDDEN"),
        ("expiry", "MANIFEST_EXPIRED"),
    ],
)
def test_rechecks_at_core_boundary(change, reason):
    cat, trader, client, _, clock = execution_setup()
    context = trader._prepare_execution("EURUSD-OTC", "f5:a", client)
    if change == "session":
        trader._supervisor_provider = lambda: SimpleNamespace(client=object())
    elif change == "time":
        from datetime import timedelta

        clock[0] += timedelta(seconds=2)
    elif change == "removed":
        cat.apply_manifest({"strategies": []})
    elif change == "real":
        trader._account_type_provider = lambda: "REAL"
    else:
        cat.apply_manifest({"strategies": [entry()], "expires_at": int(NOW.timestamp())})
    with pytest.raises(RuntimeError, match=reason):
        trader._validate_execution_ticket("EURUSD-OTC", "f5:a", context)


def test_runtime_ticket_is_single_use(order_request):
    _, trader, client, _, _ = execution_setup()
    context = trader._prepare_execution("EURUSD-OTC", "f5:a", client)
    request = replace(
        order_request,
        broker=Broker.IQ_OPTION,
        account_id="IQOPTION_PRACTICE",
        product="BINARY_OPTION",
        symbol="EURUSD-OTC",
        strategy_id="f5:a",
        manifest_context=context,
    )
    trader.validate_runtime_entry(request)
    with pytest.raises(RuntimeError, match="IQOPTION_PAYOUT_STALE"):
        trader.validate_runtime_entry(request)


def bind_order(coordinator, request, cat):
    info = cat.get_strategy("f5:a")
    bound = replace(
        request,
        broker=Broker.IQ_OPTION,
        account_id="IQOPTION_PRACTICE",
        product="BINARY_OPTION",
        symbol=info.entry.asset,
        strategy_id="f5:a",
        correlation_id=str(uuid4()),
        manifest_context=json.dumps(LiveMonitor.binding(info.entry)),
    )
    return coordinator.submit(bound)


def settle(writer, order, pnl=-100):
    event = BrokerEvent(
        str(uuid4()), order.intent_id, OrderState.SETTLED, datetime.now(UTC), realized_pnl_minor=pnl
    )
    assert writer.apply_broker_event(event)
    assert not writer.apply_broker_event(event)


def test_binding_committed_before_broker_and_sprt_recovers_crash_gap(tmp_path, order_request):
    path = tmp_path / "state.db"

    def on_receive(command):
        with sqlite3.connect(path) as conn:
            assert (
                conn.execute(
                    "SELECT count(*) FROM manifest_order_bindings WHERE order_id=?",
                    (command.order_id,),
                ).fetchone()[0]
                == 1
            )
        assert "manifest_context" not in command.to_payload()

    writer, reader, worker, _, coordinator = build_coordinator(
        path, WorkerOutcome.ACCEPTED, on_receive=on_receive
    )
    cat = catalog(entry())
    order = bind_order(coordinator, order_request, cat)
    settle(writer, order)
    writer.close()  # Crash between financial commit and monitor consumption.
    writer = SingleDatabaseWriter(path)
    try:
        monitor = LiveMonitor(cat, writer=writer)
        monitor.poll_persisted()
        monitor.poll_persisted()
        restarted = LiveMonitor(cat, writer=writer)
        restarted.poll_persisted()
        restarted.on_settlement("f5:a", won=True, ts=0, payout_pct="0.99", order_id=order.order_id)
        assert restarted.monitors["f5:a"].n == 1
        assert restarted.monitors["f5:a"].wins == 0  # Callback cannot override evidence.
        assert reader.one("orders", "order_id", order.order_id)["pnl_application_count"] == 1
        assert len(worker.received) == 1
        assert reader.list_by_state("risk_reservations", "ACTIVE") == []
    finally:
        writer.close()


def test_monitor_transaction_rolls_back_receipt_on_failure(tmp_path, order_request):
    writer, _, _, _, coordinator = build_coordinator(tmp_path / "state.db", WorkerOutcome.ACCEPTED)
    cat = catalog(entry())
    try:
        settle(writer, bind_order(coordinator, order_request, cat))

        def fail(*args):
            raise RuntimeError("injected")

        with pytest.raises(PersistenceError):
            writer.consume_manifest_orders(fail)
        with sqlite3.connect(writer.path) as conn:
            assert conn.execute("SELECT consumed FROM manifest_order_bindings").fetchone()[0] == 0
            assert conn.execute("SELECT count(*) FROM manifest_monitor_states").fetchone()[0] == 0
        monitor = LiveMonitor(cat, writer=writer)
        monitor.poll_persisted()
        assert monitor.monitors["f5:a"].n == 1
    finally:
        writer.close()


def test_reconciliation_only_settlement_and_late_evidence_are_once(tmp_path, order_request):
    writer, reader, worker, _, coordinator = build_coordinator(
        tmp_path / "state.db", WorkerOutcome.ACCEPTED
    )
    cat = catalog(entry())
    try:
        order = bind_order(coordinator, order_request, cat)
        command = worker.received[0]
        evidence = ReconciliationEvidence(
            evidence_id=str(uuid4()),
            source=ReconciliationSource.STATUS_QUERY,
            observed_at=datetime.now(UTC),
            client_order_ref=order.order_id,
            broker_order_id=reader.one("orders", "order_id", order.order_id)["broker_order_id"],
            external_status=ExternalOrderStatus.SETTLED,
            broker=Broker.IQ_OPTION,
            account_id=command.account_id,
            product=command.product,
            symbol=command.symbol,
            direction=command.direction,
            amount=command.amount,
            evidence_version=1,
            realized_pnl_minor=85,
        )
        for _ in range(2):
            attempt = str(uuid4())
            writer.begin_reconciliation_attempt(attempt, order.order_id, command.correlation_id)
            result = writer.apply_reconciliation_evidence(attempt, evidence)
            assert result.status.value in {"RESOLVED", "IDEMPOTENT"}
            monitor = LiveMonitor(cat, writer=writer)
            monitor.poll_persisted()
        assert monitor.monitors["f5:a"].n == monitor.monitors["f5:a"].wins == 1
        assert reader.one("orders", "order_id", order.order_id)["pnl_application_count"] == 1
        assert reader.list_by_state("risk_reservations", "ACTIVE") == []
    finally:
        writer.close()


def test_unconsumed_settlement_blocks_next_admission_not_database_health(tmp_path, order_request):
    writer, reader, worker, gate, coordinator = build_coordinator(
        tmp_path / "state.db", WorkerOutcome.ACCEPTED
    )
    cat = catalog(entry())
    try:
        settle(writer, bind_order(coordinator, order_request, cat))
        with pytest.raises(PersistenceError, match="MANIFEST_MONITOR_PENDING"):
            bind_order(coordinator, order_request, cat)
        assert reader.count("orders") == 1
        assert len(worker.received) == 1
        assert gate.state.is_open
        LiveMonitor(cat, writer=writer).poll_persisted()
        bind_order(coordinator, order_request, cat)
        assert reader.count("orders") == 2
    finally:
        writer.close()


def test_pre_persist_recheck_rejects_without_records(tmp_path, order_request):
    writer, reader, worker, _, coordinator = build_coordinator(
        tmp_path / "state.db", WorkerOutcome.ACCEPTED
    )

    def reject(request):
        raise RuntimeError("MANIFEST_EXPIRED")

    try:
        with pytest.raises(RuntimeError, match="MANIFEST_EXPIRED"):
            coordinator.submit(order_request, pre_persist=reject)
        assert reader.count("orders") == reader.count("trade_intents") == 0
        assert worker.received == []
    finally:
        writer.close()


def test_new_revision_does_not_consume_old_order_into_new_statistics(tmp_path, order_request):
    writer, _, _, _, coordinator = build_coordinator(tmp_path / "state.db", WorkerOutcome.ACCEPTED)
    cat = catalog(entry())
    try:
        order = bind_order(coordinator, order_request, cat)
        monitor = LiveMonitor(cat, writer=writer)
        monitor.poll_persisted()
        cat.apply_manifest(
            {
                "strategies": [
                    entry(validated={"wilson_lower": "0.70", "p_min_at_validation": "0.55"})
                ]
            }
        )
        settle(writer, order)
        monitor.poll_persisted()
        assert monitor.monitors["f5:a"].n == 0
        persisted = json.loads(writer.manifest_monitor_states()[0]["state_json"])
        assert persisted["n"] == 1 and persisted["p0"] == "0.60"
    finally:
        writer.close()


def test_monitor_thread_stops_and_database_error_blocks(tmp_path, monkeypatch):
    writer = SingleDatabaseWriter(tmp_path / "state.db")
    monitor = LiveMonitor(catalog(entry()), writer=writer)
    try:
        monitor.start()
        assert monitor.ready
        monitor.stop()
        assert not monitor.ready and not monitor._thread.is_alive()

        def broken():
            raise OSError("fixture failure")

        monkeypatch.setattr(writer, "manifest_monitor_states", broken)
        with pytest.raises(OSError):
            monitor.poll_persisted()
        assert not monitor.ready
    finally:
        monitor.stop()
        writer.close()


def test_quote_ipc_routes_read_only_to_worker(monkeypatch):
    calls = []
    server = IQOptionReadOnlyWorkerServer.__new__(IQOptionReadOnlyWorkerServer)
    server._order_session = object()
    server._session = SimpleNamespace(
        is_connected=True, get_binary_payout=lambda symbol: calls.append(symbol) or Decimal("0.85")
    )
    client = SocketWorkerClient.__new__(SocketWorkerClient)
    client._worker_role = EndpointRole.IQOPTION_WORKER

    def request(kind, payload):
        envelope = Envelope(
            1,
            "test-quote",
            "test-correlation",
            None,
            EndpointRole.CORE,
            EndpointRole.IQOPTION_WORKER,
            kind,
            NOW,
            None,
            payload,
        )
        response_kind, response_payload = server._dispatch(envelope)
        return replace(envelope, message_type=response_kind, payload=response_payload)

    monkeypatch.setattr(client, "_read_only_request", request)
    assert client.iqoption_binary_payout("EURUSD-OTC") == Decimal("0.85")
    assert calls == ["EURUSD-OTC"]


def test_decimal_json_decoding_preserves_commission_digits(monkeypatch):
    session = IQOptionCommunityReadOnlySession(
        "test@example.invalid", SecretValue("fixture-only"), IQOptionAccountMode.PRACTICE
    )
    received = []
    monkeypatch.setattr(session, "_route_pending", received.append)
    session._handle_message(
        '{"name":"initialization-data","msg":{"commission":15.123456789123456789}}'
    )
    assert received[0]["msg"]["commission"] == Decimal("15.123456789123456789")


def test_concurrent_monitor_consumers_do_not_duplicate(tmp_path, order_request):
    writer, _, _, _, coordinator = build_coordinator(tmp_path / "state.db", WorkerOutcome.ACCEPTED)
    cat = catalog(entry())
    try:
        settle(writer, bind_order(coordinator, order_request, cat))
        a, b = LiveMonitor(cat, writer=writer), LiveMonitor(cat, writer=writer)
        with ThreadPoolExecutor(2) as pool:
            list(pool.map(lambda monitor: monitor.poll_persisted(), [a, b]))
        assert json.loads(writer.manifest_monitor_states()[0]["state_json"])["n"] == 1
    finally:
        writer.close()


def test_retiring_kept_until_durable_settlement(tmp_path, order_request):
    writer, _, _, _, coordinator = build_coordinator(tmp_path / "state.db", WorkerOutcome.ACCEPTED)
    cat = catalog(entry())
    try:
        order = bind_order(coordinator, order_request, cat)
        monitor = LiveMonitor(cat, writer=writer)
        monitor.poll_persisted()
        cat.apply_manifest({"strategies": []})
        assert "f5:a" in cat.retiring_strategies
        assert (
            cat.is_eligible("f5:a", account_type="DEMO", current_payout="0.85")[1]
            == "STRATEGY_RETIRING"
        )
        settle(writer, order)
        monitor.poll_persisted()
        assert not cat.retiring_strategies
        assert json.loads(writer.manifest_monitor_states()[0]["state_json"])["n"] == 1
    finally:
        writer.close()


def test_demotion_survives_restart_and_identical_republish(tmp_path, order_request):
    writer, _, _, _, coordinator = build_coordinator(tmp_path / "state.db", WorkerOutcome.ACCEPTED)
    recipe = entry(validated={"wilson_lower": "0.90", "p_min_at_validation": "0.10"})
    cat = catalog(recipe)
    try:
        monitor = LiveMonitor(cat, writer=writer)
        for _ in range(2):
            settle(writer, bind_order(coordinator, order_request, cat))
            monitor.poll_persisted()
        monitor.poll_persisted()
        assert cat.get_strategy("f5:a").status == "observation"
        cat.apply_manifest({"manifest_version": 2, "strategies": [recipe]})
        assert cat.get_strategy("f5:a").status == "observation"
        fresh_catalog = catalog(recipe)
        LiveMonitor(fresh_catalog, writer=writer).poll_persisted()
        assert fresh_catalog.get_strategy("f5:a").status == "observation"
        assert (
            fresh_catalog.is_eligible("f5:a", account_type="REAL", current_payout="0.85")[1]
            == "OBSERVATION_ONLY_DEMO"
        )
    finally:
        writer.close()


@pytest.mark.parametrize("mode", [IQOptionAccountMode.PRACTICE, IQOptionAccountMode.REAL])
def test_payout_adapter_only_reads_exact_turbo_asset(mode, monkeypatch):
    session = IQOptionCommunityReadOnlySession(
        "test@example.invalid", SecretValue("fixture-only"), mode
    )
    calls = []

    def request(payload, **kwargs):
        calls.append(payload)
        return {
            "name": "initialization-data",
            "msg": {
                "turbo": {
                    "actives": {
                        str(session._active_id("EURUSD-OTC")): {
                            "name": "front.EURUSD-OTC",
                            "enabled": True,
                            "is_suspended": False,
                            "option": {"profit": {"commission": "15"}},
                        }
                    }
                }
            },
        }

    monkeypatch.setattr(session, "_request_message", request)
    if mode is IQOptionAccountMode.REAL:
        with pytest.raises(IQOptionExternalError, match="REAL_ACCOUNT_FORBIDDEN"):
            session.get_binary_payout("EURUSD-OTC")
        assert calls == []
    else:
        assert session.get_binary_payout("EURUSD-OTC") == Decimal("0.85")
        assert len(calls) == 1
        assert calls[0]["msg"]["name"] == "get-initialization-data"
