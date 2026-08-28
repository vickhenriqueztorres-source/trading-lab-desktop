from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import suppress
from datetime import UTC, datetime

import pytest

from apps.core.health import HealthGate
from apps.core.read_only_worker_supervisor import (
    ReadOnlyWorkerSpec,
    ReadOnlyWorkerSupervisor,
)
from apps.core.worker_client import DeliveryCertainty, WorkerDispatchError
from apps.core.worker_supervisor import WorkerHealthState, WorkerSupervisor
from apps.deriv_worker.fake_transport import FakeDerivScenario, FakeDerivTransport
from apps.deriv_worker.public_session import PublicDerivSession
from apps.deriv_worker.server import DerivWorkerServer
from apps.simulated_worker.scenarios import WorkerScenario
from packages.protocol.envelope import EndpointRole, Envelope, MessageType
from packages.protocol.errors import ProtocolError, ProtocolErrorCode


def _spec(*arguments: str) -> ReadOnlyWorkerSpec:
    return ReadOnlyWorkerSpec(
        module="apps.deriv_worker",
        role=EndpointRole.DERIV_WORKER,
        broker="DERIV",
        extra_arguments=arguments,
    )


@pytest.fixture
def deriv_supervisor() -> Iterator[ReadOnlyWorkerSupervisor]:
    supervisor = ReadOnlyWorkerSupervisor(HealthGate(), _spec())
    supervisor.start()
    try:
        yield supervisor
    finally:
        supervisor.shutdown()


def test_deriv_contract_01_to_03_handshake_and_public_capabilities(
    deriv_supervisor: ReadOnlyWorkerSupervisor,
) -> None:
    client = deriv_supervisor.client
    capabilities = client.broker_capabilities()

    assert client.capabilities.broker == "DERIV"
    assert client.capabilities.can_submit_orders is False
    assert client.capabilities.supports_market_data is True
    assert capabilities.authenticated is False
    assert capabilities.can_trade is False
    assert capabilities.connection_mode.value == "PUBLIC_READ_ONLY"
    health = client.request_health_snapshot()
    assert health["contract_events_overflow_total"] == 0
    assert health["reconciliation_required"] is False
    assert health["pings_sent_total"] == 0
    assert health["heartbeat_kills_total"] == 0
    assert health["last_kill_reason"] is None


def test_deriv_contract_04_core_and_worker_reject_order_submit(
    deriv_supervisor: ReadOnlyWorkerSupervisor,
) -> None:
    with pytest.raises(WorkerDispatchError) as captured:
        deriv_supervisor.client.submit_order(object())  # type: ignore[arg-type]
    assert captured.value.code is ProtocolErrorCode.WORKER_CAPABILITY_DENIED
    assert captured.value.delivery is DeliveryCertainty.NOT_SENT

    server = DerivWorkerServer(
        "127.0.0.1",
        1,
        protocol_version=1,
        session=PublicDerivSession(FakeDerivTransport()),
    )
    request = Envelope(
        protocol_version=1,
        message_id="message-order",
        correlation_id="correlation-order",
        causation_id=None,
        source=EndpointRole.CORE,
        target=EndpointRole.DERIV_WORKER,
        message_type=MessageType.ORDER_SUBMIT,
        created_at_utc=datetime.now(UTC),
        deadline_at=None,
        payload={},
    )
    response_type, payload = server._dispatch_read_only(request)
    assert response_type is MessageType.ERROR
    assert payload == {"reason_code": "WORKER_CAPABILITY_DENIED"}


def test_deriv_contract_05_public_symbol_is_normalized(
    deriv_supervisor: ReadOnlyWorkerSupervisor,
) -> None:
    symbols = deriv_supervisor.client.market_symbols()
    assert symbols[0].broker_symbol == "frxEURUSD"
    assert str(symbols[0].pip_size) == "0.0001"


def test_deriv_contract_06_tick_and_event_are_normalized(
    deriv_supervisor: ReadOnlyWorkerSupervisor,
) -> None:
    subscribed = deriv_supervisor.client.subscribe_market_ticks("frxEURUSD")
    event = deriv_supervisor.client.receive_market_tick(1.0)
    assert subscribed.broker_symbol == "frxEURUSD"
    assert event == subscribed


def test_deriv_contract_06b_continuous_stream_event_is_forwarded_over_ipc() -> None:
    supervisor = ReadOnlyWorkerSupervisor(
        HealthGate(),
        _spec("--scenario", FakeDerivScenario.STREAMING_TICKS.value),
    )
    supervisor.start()
    try:
        subscribed = supervisor.client.subscribe_market_ticks("frxEURUSD")
        first_event = supervisor.client.receive_market_tick(1.0)
        second_event = supervisor.client.receive_market_tick(1.0)
        assert first_event == subscribed
        assert second_event is not None
        assert second_event.epoch == subscribed.epoch + 1
        assert second_event.subscription_id == subscribed.subscription_id
    finally:
        supervisor.shutdown()


def test_deriv_contract_07_history_and_candle_are_normalized(
    deriv_supervisor: ReadOnlyWorkerSupervisor,
) -> None:
    ticks, candles = deriv_supervisor.client.market_history(
        "frxEURUSD", style="candles", timeframe_seconds=60
    )
    assert ticks == ()
    assert candles[0].is_closed is True
    assert candles[0].timeframe_seconds == 60
    _, paged = deriv_supervisor.client.market_history(
        "frxEURUSD",
        style="candles",
        timeframe_seconds=60,
        end_epoch=1_700_000_100,
    )
    assert int(paged[0].close_time.timestamp()) == 1_700_000_100


def test_oversized_history_page_is_rejected_without_crashing_worker(
    deriv_supervisor: ReadOnlyWorkerSupervisor,
) -> None:
    with pytest.raises(WorkerDispatchError):
        deriv_supervisor.client.market_history("R_100", style="ticks", count=500)

    deriv_supervisor.client.ping()
    assert deriv_supervisor.health_state is WorkerHealthState.READY


def test_deriv_contract_08_subscription_is_cancelled(
    deriv_supervisor: ReadOnlyWorkerSupervisor,
) -> None:
    tick = deriv_supervisor.client.subscribe_market_ticks("frxEURUSD")
    assert deriv_supervisor.client.unsubscribe_market_ticks(tick.subscription_id) is True


@pytest.mark.parametrize("_repetition", range(3))
def test_deriv_contract_09_worker_crash_isolated_from_simulated_worker(
    _repetition: int,
) -> None:
    gate = HealthGate()
    simulated = WorkerSupervisor(gate, scenario=WorkerScenario.ACCEPT)
    data_worker = ReadOnlyWorkerSupervisor(
        gate,
        _spec("--scenario", FakeDerivScenario.CRASH_AFTER_HANDSHAKE.value),
        heartbeat_interval=0.02,
        heartbeat_timeout=0.02,
    )
    simulated.start()
    try:
        with suppress(ProtocolError, OSError):
            data_worker.start()
        deadline = time.monotonic() + 2.0
        while (
            data_worker.health_state
            not in {
                WorkerHealthState.DISCONNECTED,
                WorkerHealthState.INCOMPATIBLE,
            }
            and time.monotonic() < deadline
        ):
            time.sleep(0.01)
        simulated.client.ping(0.5)
        assert simulated.client.is_ready is True
        assert data_worker.health_state is not WorkerHealthState.READY
    finally:
        data_worker.shutdown()
        simulated.shutdown()


@pytest.mark.parametrize("_repetition", range(3))
def test_deriv_contract_10_invalid_schema_never_reaches_ready(_repetition: int) -> None:
    supervisor = ReadOnlyWorkerSupervisor(
        HealthGate(),
        _spec("--scenario", FakeDerivScenario.SCHEMA_CHANGED.value),
        handshake_timeout=1.0,
    )
    with pytest.raises(ProtocolError):
        supervisor.start()
    assert supervisor.health_state is not WorkerHealthState.READY
    supervisor.shutdown()
