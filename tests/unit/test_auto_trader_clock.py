from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import Mock

from websockets.exceptions import WebSocketException

from apps.core.execution_state import ExecutionState
from apps.core.worker_client import DeliveryCertainty, WorkerDispatchError
from packages.domain.market import BrokerClockSnapshot
from packages.protocol import ProtocolErrorCode
from tests.unit.test_iqoption_execution_decoupled import _armed_trader


def test_websocket_exception_from_clock_never_notifies_session_failure() -> None:
    trader, client, runtime, transport, _, recoveries = _armed_trader()
    client.broker_clock = Mock(side_effect=WebSocketException("slow pong"))
    notify = Mock()
    trader._notify_session_failure = notify  # type: ignore[method-assign]

    trader._evaluate_cycle()

    notify.assert_not_called()
    assert recoveries == []
    assert transport.armed_intent
    assert trader.latest_clock is None
    assert any(
        name == "iqoption_clock_unavailable"
        and fields["reason_code"] == "IQOPTION_CLOCK_UNAVAILABLE"
        for name, fields in runtime.events
    )


def test_dead_websocket_reported_by_clock_requests_transport_recovery() -> None:
    trader, client, _, transport, safe_stop, recoveries = _armed_trader()
    client.broker_clock = Mock(
        side_effect=WorkerDispatchError(
            ProtocolErrorCode.IQOPTION_WEBSOCKET_UNAVAILABLE,
            DeliveryCertainty.NOT_SENT,
            "broker socket is unavailable",
        )
    )

    trader._evaluate_cycle()

    assert recoveries == ["IQOPTION_WEBSOCKET_UNAVAILABLE"]
    assert transport.state is ExecutionState.ARMED_DEGRADED
    assert transport.armed_intent
    safe_stop.assert_not_called()


def test_catalog_timeout_retires_transport_and_requests_one_recovery() -> None:
    trader, client, runtime, transport, safe_stop, recoveries = _armed_trader()
    client.iqoption_instrument_catalog = Mock(
        side_effect=WorkerDispatchError(
            ProtocolErrorCode.IQOPTION_REQUEST_TIMEOUT,
            DeliveryCertainty.NOT_SENT,
            "legacy initialization response timed out",
        )
    )
    supervisor = Mock(client=client)

    trader._refresh_instrument_catalog(supervisor, runtime)
    # Repeated observations from the same retired generation must not create a
    # competing recovery loop or consume another HTTP-login admission.
    trader._last_instrument_catalog_probe = float("-inf")
    trader._refresh_instrument_catalog(supervisor, runtime)

    assert recoveries == ["IQOPTION_REQUEST_TIMEOUT"]
    assert transport.state is ExecutionState.ARMED_DEGRADED
    assert transport.armed_intent
    safe_stop.assert_not_called()
    assert sum(name == "iqoption_instrument_catalog_failed" for name, _ in runtime.events) == 2


def test_catalog_timeout_stops_current_cycle_before_market_requests() -> None:
    trader, client, _, transport, _, recoveries = _armed_trader()
    now = datetime.now(UTC)
    client.broker_clock = Mock(
        return_value=BrokerClockSnapshot(int(now.timestamp()), now, 0.01, Decimal(0))
    )
    client.iqoption_instrument_catalog = Mock(
        side_effect=WorkerDispatchError(
            ProtocolErrorCode.IQOPTION_REQUEST_TIMEOUT,
            DeliveryCertainty.NOT_SENT,
            "legacy initialization response timed out",
        )
    )

    trader._evaluate_cycle()

    assert recoveries == ["IQOPTION_REQUEST_TIMEOUT"]
    assert transport.state is ExecutionState.ARMED_DEGRADED
    assert client.market_requests == []
