from unittest.mock import Mock

from websockets.exceptions import WebSocketException

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
