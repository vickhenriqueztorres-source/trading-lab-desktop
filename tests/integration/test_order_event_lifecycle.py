from __future__ import annotations

import socket
import time
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest

from apps.core.coordinator import OrderCoordinator
from apps.core.health import HealthGate
from apps.core.runtime import CoreRuntime
from apps.core.worker_client import SocketWorkerClient
from apps.simulated_worker.broker_store import SimulatedBrokerStore
from apps.simulated_worker.scenarios import WorkerScenario
from apps.simulated_worker.worker import SimulatedWorker
from packages.domain.models import (
    Broker,
    BrokerOrderEvent,
    ExternalOrderStatus,
    OrderRequest,
    OrderState,
    WorkerOutcome,
)
from packages.persistence.reader import StateReader
from packages.persistence.writer import BrokerEventApplyStatus, SingleDatabaseWriter
from packages.protocol.envelope import EndpointRole, Envelope, MessageType
from packages.protocol.errors import ProtocolError, ProtocolErrorCode
from packages.protocol.messages import WorkerCapabilities
from packages.protocol.transport import FramedSocket


def wait_until(predicate, timeout: float = 3.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("condition was not reached before timeout")


def prepare_order(path: Path, request: OrderRequest):
    writer = SingleDatabaseWriter(path)
    reader = StateReader(path)
    coordinator = OrderCoordinator(
        writer,
        SimulatedWorker([WorkerOutcome.ACCEPTED]),
        HealthGate(),
    )
    persisted = coordinator.submit(request)
    order = reader.one("orders", "order_id", persisted.order_id)
    assert order is not None
    return writer, reader, persisted, str(order["broker_order_id"])


def event_for(
    request: OrderRequest,
    order_id: str,
    broker_order_id: str,
    status: ExternalOrderStatus,
    sequence: int,
    *,
    event_id: str | None = None,
    result_minor: int | None = None,
) -> BrokerOrderEvent:
    now = datetime.now(UTC)
    canonical: dict[str, object] = {
        "event_id": event_id or str(uuid4()),
        "event_version": 1,
        "broker": request.broker.value,
        "account_id": request.account_id,
        "client_order_ref": order_id,
        "broker_order_id": broker_order_id,
        "correlation_id": request.correlation_id,
        "external_sequence": sequence,
        "external_status": status.value,
        "occurred_at": now.isoformat(),
        "observed_at": now.isoformat(),
        "product": request.product,
        "symbol": request.symbol,
        "direction": request.direction.value,
        "amount_minor": request.amount.minor_units,
        "currency": request.amount.currency,
        "result_minor": result_minor,
        "result_currency": request.amount.currency if result_minor is not None else None,
    }
    return BrokerOrderEvent.from_payload(
        {
            **canonical,
            "evidence_hash": BrokerOrderEvent.evidence_hash_for_payload(canonical),
        }
    )


def mutated(event: BrokerOrderEvent, **changes: object) -> BrokerOrderEvent:
    canonical = event.canonical_payload()
    canonical.update(changes)
    return BrokerOrderEvent.from_payload(
        {
            **canonical,
            "evidence_hash": BrokerOrderEvent.evidence_hash_for_payload(canonical),
        }
    )


def runtime_request(request: OrderRequest, suffix: str) -> OrderRequest:
    return replace(
        request,
        correlation_id=f"{request.correlation_id}-{suffix}-{uuid4()}",
    )


def test_evt_01_02_03_accepted_open_settled_are_atomic(
    tmp_path: Path, order_request: OrderRequest
) -> None:
    writer, reader, persisted, broker_order_id = prepare_order(tmp_path / "state.db", order_request)
    try:
        accepted = event_for(
            order_request,
            persisted.order_id,
            broker_order_id,
            ExternalOrderStatus.ACCEPTED,
            1,
        )
        opened = event_for(
            order_request,
            persisted.order_id,
            broker_order_id,
            ExternalOrderStatus.OPEN,
            2,
        )
        settled = event_for(
            order_request,
            persisted.order_id,
            broker_order_id,
            ExternalOrderStatus.SETTLED,
            3,
            result_minor=250,
        )
        assert writer.apply_normalized_broker_event(accepted).order_state is OrderState.ACCEPTED
        assert writer.apply_normalized_broker_event(opened).order_state is OrderState.OPEN
        assert writer.apply_normalized_broker_event(settled).order_state is OrderState.SETTLED
        order = reader.one("orders", "order_id", persisted.order_id)
        reservation = reader.reservation_for_intent(persisted.intent_id)
        assert order is not None and order["realized_pnl_minor"] == 250
        assert reservation is not None and reservation["state"] == "RELEASED"
        assert reader.financial_effect_counts(persisted.order_id) == {
            "pnl_application_count": 1,
            "reservation_release_count": 1,
        }
    finally:
        writer.close()


def test_evt_04_05_duplicates_do_not_repeat_effects(
    tmp_path: Path, order_request: OrderRequest
) -> None:
    writer, reader, persisted, broker_order_id = prepare_order(tmp_path / "state.db", order_request)
    try:
        accepted = event_for(
            order_request,
            persisted.order_id,
            broker_order_id,
            ExternalOrderStatus.ACCEPTED,
            1,
        )
        settled = event_for(
            order_request,
            persisted.order_id,
            broker_order_id,
            ExternalOrderStatus.SETTLED,
            2,
            result_minor=250,
        )
        writer.apply_normalized_broker_event(accepted)
        assert (
            writer.apply_normalized_broker_event(accepted).status
            is BrokerEventApplyStatus.DUPLICATE
        )
        writer.apply_normalized_broker_event(settled)
        for _ in range(100):
            assert (
                writer.apply_normalized_broker_event(settled).status
                is BrokerEventApplyStatus.DUPLICATE
            )
        assert reader.financial_effect_counts(persisted.order_id) == {
            "pnl_application_count": 1,
            "reservation_release_count": 1,
        }
    finally:
        writer.close()


def test_evt_06_07_08_replay_late_and_impossible_terminal_events(
    tmp_path: Path, order_request: OrderRequest
) -> None:
    writer, reader, persisted, broker_order_id = prepare_order(tmp_path / "state.db", order_request)
    try:
        settled = event_for(
            order_request,
            persisted.order_id,
            broker_order_id,
            ExternalOrderStatus.SETTLED,
            1,
            result_minor=250,
        )
        writer.apply_normalized_broker_event(settled)
        conflict = mutated(settled, result_minor=-1000)
        assert (
            writer.apply_normalized_broker_event(conflict).status is BrokerEventApplyStatus.CONFLICT
        )
        late = event_for(
            order_request,
            persisted.order_id,
            broker_order_id,
            ExternalOrderStatus.ACCEPTED,
            2,
        )
        impossible = event_for(
            order_request,
            persisted.order_id,
            broker_order_id,
            ExternalOrderStatus.REJECTED,
            3,
        )
        assert (
            writer.apply_normalized_broker_event(late).status is BrokerEventApplyStatus.LATE_IGNORED
        )
        assert (
            writer.apply_normalized_broker_event(impossible).status
            is BrokerEventApplyStatus.CONFLICT
        )
        order = reader.one("orders", "order_id", persisted.order_id)
        assert order is not None and order["state"] == "SETTLED"
        assert reader.financial_effect_counts(persisted.order_id)["pnl_application_count"] == 1
    finally:
        writer.close()


@pytest.mark.parametrize(
    ("mutation", "reason"),
    [
        ({"broker": Broker.IQ_OPTION.value}, "BROKER_EVENT_SCOPE_MISMATCH"),
        ({"account_id": "other-account"}, "BROKER_EVENT_ACCOUNT_MISMATCH"),
        ({"broker_order_id": "SIM-OTHER"}, "BROKER_ORDER_ID_CONFLICT"),
    ],
)
def test_evt_09_10_11_scope_matching_is_strict(
    tmp_path: Path,
    order_request: OrderRequest,
    mutation: dict[str, object],
    reason: str,
) -> None:
    writer, reader, persisted, broker_order_id = prepare_order(tmp_path / "state.db", order_request)
    try:
        source = event_for(
            order_request,
            persisted.order_id,
            broker_order_id,
            ExternalOrderStatus.OPEN,
            1,
        )
        result = writer.apply_normalized_broker_event(mutated(source, **mutation))
        assert result.status is BrokerEventApplyStatus.CONFLICT
        assert result.reason_code == reason
        order = reader.one("orders", "order_id", persisted.order_id)
        assert order is not None and order["state"] == "ACCEPTED"
    finally:
        writer.close()


def test_evt_12_truncated_event_frame_has_no_financial_effect(
    tmp_path: Path, order_request: OrderRequest
) -> None:
    writer, reader, persisted, _ = prepare_order(tmp_path / "state.db", order_request)
    left, right = socket.socketpair()
    try:
        right.sendall((200).to_bytes(4, "big") + b'{"partial":')
        right.close()
        with pytest.raises(ProtocolError):
            FramedSocket(left).receive()
        order = reader.one("orders", "order_id", persisted.order_id)
        assert order is not None and order["state"] == "ACCEPTED"
        assert reader.count("broker_order_events") == 0
    finally:
        left.close()
        writer.close()


def test_evt_13_bounded_event_queue_saturation_fails_closed(
    order_request: OrderRequest,
) -> None:
    left, right = socket.socketpair()
    gate = HealthGate()
    failures: list[ProtocolErrorCode] = []

    def disconnected(code: ProtocolErrorCode) -> None:
        failures.append(code)
        if code is ProtocolErrorCode.IPC_BACKPRESSURE:
            gate.block("HG_BROKER_EVENT_BACKPRESSURE")

    client = SocketWorkerClient(
        FramedSocket(left),
        WorkerCapabilities(
            broker="simulated",
            account_modes=("practice",),
            products=("DIGITAL_OPTION",),
            supports_reconciliation=True,
            supports_quotes=False,
            supports_order_status_query=True,
            worker_version="test",
            supports_order_events=True,
        ),
        event_queue_size=1,
        on_disconnect=disconnected,
    )
    transport = FramedSocket(right)
    event = event_for(
        order_request,
        "order-queue",
        "SIM-QUEUE",
        ExternalOrderStatus.ACCEPTED,
        1,
    )
    try:
        for _ in range(2):
            transport.send(
                Envelope(
                    protocol_version=1,
                    message_id=str(uuid4()),
                    correlation_id=event.correlation_id,
                    causation_id=None,
                    source=EndpointRole.SIMULATED_WORKER,
                    target=EndpointRole.CORE,
                    message_type=MessageType.ORDER_EVENT,
                    created_at_utc=datetime.now(UTC),
                    deadline_at=None,
                    payload=event.to_payload(),
                )
            )
        wait_until(lambda: bool(failures))
        assert failures == [ProtocolErrorCode.IPC_BACKPRESSURE]
        assert gate.state.reason_code == "HG_BROKER_EVENT_BACKPRESSURE"
        assert client.is_ready is False
    finally:
        client.close()
        transport.close()


@pytest.mark.parametrize("repeat", range(3))
def test_evt_01_03_04_normal_lifecycle_central_proof_repeated(
    tmp_path: Path, order_request: OrderRequest, repeat: int
) -> None:
    profile = tmp_path / f"normal-{repeat}"
    runtime = CoreRuntime(profile, worker_scenario=WorkerScenario.NORMAL_LIFECYCLE)
    runtime.start()
    persisted = runtime.submit(runtime_request(order_request, f"normal-{repeat}"))
    try:
        wait_until(
            lambda: (
                runtime.reader.one("orders", "order_id", persisted.order_id)["state"] == "SETTLED"
            )
        )
        outbox = runtime.reader.one("outbox_messages", "message_id", persisted.message_id)
        assert outbox is not None and outbox["attempt_count"] == 1
        assert runtime.reader.financial_effect_counts(persisted.order_id) == {
            "pnl_application_count": 1,
            "reservation_release_count": 1,
        }
        metrics = SimulatedBrokerStore.read_metrics(runtime.simulated_broker_store_path)
        assert metrics["submit_count"] == 1
        assert metrics["event_delivery_count"] == 3
        assert runtime.reader.count("broker_order_events") == 3
    finally:
        runtime.shutdown()


@pytest.mark.parametrize("repeat", range(3))
def test_evt_14_15_gap_uses_status_fallback_without_resubmit_repeated(
    tmp_path: Path, order_request: OrderRequest, repeat: int
) -> None:
    profile = tmp_path / f"fallback-{repeat}"
    runtime = CoreRuntime(profile, worker_scenario=WorkerScenario.DROP_OPEN_EVENT)
    runtime.start()
    persisted = runtime.submit(runtime_request(order_request, f"fallback-{repeat}"))
    try:
        wait_until(
            lambda: (
                runtime.reader.one("orders", "order_id", persisted.order_id)["state"] == "SETTLED"
                and SimulatedBrokerStore.read_metrics(runtime.simulated_broker_store_path)[
                    "status_query_count"
                ]
                > 0
            )
        )
        metrics = SimulatedBrokerStore.read_metrics(runtime.simulated_broker_store_path)
        outbox = runtime.reader.one("outbox_messages", "message_id", persisted.message_id)
        assert metrics["submit_count"] == 1
        assert metrics["status_query_count"] > 0
        assert outbox is not None and outbox["attempt_count"] == 1
        assert runtime.reader.financial_effect_counts(persisted.order_id) == {
            "pnl_application_count": 1,
            "reservation_release_count": 1,
        }
    finally:
        runtime.shutdown()


def test_evt_16_18_20_lost_settlement_survives_core_and_worker_restart(
    tmp_path: Path, order_request: OrderRequest
) -> None:
    profile = tmp_path / "restart-profile"
    store_path = profile / "broker.db"
    first = CoreRuntime(
        profile,
        worker_scenario=WorkerScenario.DROP_SETTLED_EVENT,
        simulated_broker_store_path=store_path,
    )
    first.start()
    persisted = first.submit(runtime_request(order_request, "restart"))
    wait_until(
        lambda: first.reader.one("orders", "order_id", persisted.order_id)["state"] == "OPEN"
    )
    assert first.reader.reservation_for_intent(persisted.intent_id)["state"] == "ACTIVE"
    first.shutdown()

    restarted = CoreRuntime(profile, simulated_broker_store_path=store_path)
    restarted.start()
    try:
        order = restarted.reader.one("orders", "order_id", persisted.order_id)
        assert order is not None and order["state"] == "SETTLED"
        assert restarted.reader.financial_effect_counts(persisted.order_id) == {
            "pnl_application_count": 1,
            "reservation_release_count": 1,
        }
        metrics = SimulatedBrokerStore.read_metrics(store_path)
        assert metrics["submit_count"] == 1
        assert metrics["status_query_count"] > 0
    finally:
        restarted.shutdown()


def test_evt_17_19_worker_crash_reconnects_and_reconciles_without_rearming(
    tmp_path: Path, order_request: OrderRequest
) -> None:
    profile = tmp_path / "worker-crash"
    runtime = CoreRuntime(
        profile,
        worker_scenario=WorkerScenario.CRASH_BEFORE_SETTLED_EVENT,
    )
    runtime.start()
    persisted = runtime.submit(runtime_request(order_request, "worker-crash"))
    try:
        deadline = time.monotonic() + 6.0
        order = runtime.reader.one("orders", "order_id", persisted.order_id)
        while order is not None and order["state"] != "SETTLED" and time.monotonic() < deadline:
            time.sleep(0.01)
            order = runtime.reader.one("orders", "order_id", persisted.order_id)
        assert order is not None and order["state"] == "SETTLED", order
        wait_until(lambda: runtime.worker_supervisor.health_state.value == "READY")
        reservation = runtime.reader.reservation_for_intent(persisted.intent_id)
        assert reservation is not None and reservation["state"] == "RELEASED"
        assert runtime.reader.financial_effect_counts(persisted.order_id) == {
            "pnl_application_count": 1,
            "reservation_release_count": 1,
        }
        assert runtime.safe_stop_active is True
    finally:
        runtime.shutdown()


def test_worker_crash_mid_settlement_frame_never_applies_partial_event(
    tmp_path: Path, order_request: OrderRequest
) -> None:
    runtime = CoreRuntime(
        tmp_path / "partial-event-crash",
        worker_scenario=WorkerScenario.CRASH_DURING_EVENT_WRITE,
    )
    runtime.start()
    persisted = runtime.submit(runtime_request(order_request, "partial-event-crash"))
    try:
        wait_until(
            lambda: (
                runtime.reader.one("orders", "order_id", persisted.order_id)["state"] == "SETTLED"
            ),
            timeout=6.0,
        )
        wait_until(lambda: runtime.worker_supervisor.health_state.value == "READY")
        order = runtime.reader.one("orders", "order_id", persisted.order_id)
        assert order is not None and order["realized_pnl_minor"] is not None
        # The truncated settlement frame was never admitted as an event. The
        # authoritative status query after worker recovery settles it exactly once.
        pre_crash_events = runtime.reader.broker_events_for_order(persisted.order_id)
        assert 1 <= len(pre_crash_events) <= 2
        assert all(event["external_status"] != "SETTLED" for event in pre_crash_events)
        assert runtime.reader.financial_effect_counts(persisted.order_id) == {
            "pnl_application_count": 1,
            "reservation_release_count": 1,
        }
        assert runtime.safe_stop_active is True
    finally:
        runtime.shutdown()


def test_evt_21_event_and_reconciliation_paths_have_same_financial_result(
    tmp_path: Path, order_request: OrderRequest
) -> None:
    results: list[dict[str, int]] = []
    for scenario in (WorkerScenario.NORMAL_LIFECYCLE, WorkerScenario.DROP_OPEN_EVENT):
        runtime = CoreRuntime(tmp_path / scenario.value, worker_scenario=scenario)
        runtime.start()
        persisted = runtime.submit(runtime_request(order_request, scenario.value))
        try:
            wait_until(
                lambda runtime=runtime, persisted=persisted: (
                    runtime.reader.one("orders", "order_id", persisted.order_id)["state"]
                    == "SETTLED"
                )
            )
            results.append(runtime.reader.financial_effect_counts(persisted.order_id))
        finally:
            runtime.shutdown()
    assert results == [
        {"pnl_application_count": 1, "reservation_release_count": 1},
        {"pnl_application_count": 1, "reservation_release_count": 1},
    ]


def test_evt_22_23_settlement_unknown_or_silence_never_invents_result(
    tmp_path: Path, order_request: OrderRequest
) -> None:
    for scenario, expected in (
        (WorkerScenario.SETTLEMENT_UNKNOWN_EVENT, "SETTLEMENT_UNKNOWN"),
        (WorkerScenario.DROP_SETTLED_EVENT, "OPEN"),
    ):
        runtime = CoreRuntime(tmp_path / scenario.value, worker_scenario=scenario)
        runtime.start()
        persisted = runtime.submit(runtime_request(order_request, scenario.value))
        try:
            wait_until(
                lambda runtime=runtime, persisted=persisted, expected=expected: (
                    runtime.reader.one("orders", "order_id", persisted.order_id)["state"]
                    == expected
                )
            )
            time.sleep(0.1)
            order = runtime.reader.one("orders", "order_id", persisted.order_id)
            reservation = runtime.reader.reservation_for_intent(persisted.intent_id)
            assert order is not None and order["realized_pnl_minor"] is None
            assert reservation is not None and reservation["state"] == "ACTIVE"
        finally:
            runtime.shutdown()


def test_duplicate_settlement_storm_and_conflicting_settlement_fail_safely(
    tmp_path: Path, order_request: OrderRequest
) -> None:
    duplicate = CoreRuntime(
        tmp_path / "duplicate",
        worker_scenario=WorkerScenario.DUPLICATE_SETTLED_EVENT,
    )
    duplicate.start()
    persisted = duplicate.submit(runtime_request(order_request, "duplicate"))
    try:
        wait_until(
            lambda: (
                SimulatedBrokerStore.read_metrics(duplicate.simulated_broker_store_path)[
                    "event_delivery_count"
                ]
                == 102
            )
        )
        assert duplicate.reader.financial_effect_counts(persisted.order_id) == {
            "pnl_application_count": 1,
            "reservation_release_count": 1,
        }
    finally:
        duplicate.shutdown()

    conflict = CoreRuntime(
        tmp_path / "conflict",
        worker_scenario=WorkerScenario.CONFLICTING_SETTLEMENT_EVENT,
    )
    conflict.start()
    conflicted = conflict.submit(runtime_request(order_request, "conflict"))
    try:
        wait_until(lambda: conflict.health_gate.state.reason_code == "HG_ORDER_EVENT_CONFLICT")
        assert conflict.reader.financial_effect_counts(conflicted.order_id) == {
            "pnl_application_count": 1,
            "reservation_release_count": 1,
        }
    finally:
        conflict.shutdown()


def test_account_scoped_event_failure_does_not_block_an_unrelated_account(
    tmp_path: Path, order_request: OrderRequest
) -> None:
    writer = SingleDatabaseWriter(tmp_path / "state.db")
    gate = HealthGate()
    worker = SimulatedWorker([WorkerOutcome.ACCEPTED])
    coordinator = OrderCoordinator(writer, worker, gate)
    gate.block_scope(
        order_request.broker.value,
        order_request.account_id,
        "HG_ORDER_EVENT_CONFLICT",
    )
    other = replace(
        order_request,
        correlation_id="corr-unrelated-account",
        account_id="demo-account-2",
    )
    try:
        persisted = coordinator.submit(other)
        order = StateReader(writer.path).one("orders", "order_id", persisted.order_id)
        assert order is not None and order["state"] == "ACCEPTED"
        assert len(worker.received) == 1
    finally:
        writer.close()
