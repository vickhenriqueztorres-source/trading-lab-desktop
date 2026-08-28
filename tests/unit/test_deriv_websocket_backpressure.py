from __future__ import annotations

import json
import queue
import threading
import time
from typing import cast

import pytest

from apps.deriv_worker.request_allowlist import DerivOperation, validate_read_only_request
from apps.deriv_worker.schema import DerivErrorCategory, DerivWorkerError
from apps.deriv_worker.websocket_client import (
    DerivWebSocketClient,
    TransportRouteResult,
)


class _Connection:
    def __init__(self) -> None:
        self.incoming: queue.Queue[object] = queue.Queue()
        self.sent: list[dict[str, object]] = []
        self.closed = False
        self.abort_count = 0
        self.fail_send = False

    def send(self, raw: str) -> None:
        request = json.loads(raw)
        self.sent.append(request)
        if self.fail_send:
            raise OSError("simulated send failure")
        if "req_id" not in request:
            return
        self.incoming.put(
            json.dumps(
                {
                    "msg_type": "ping",
                    "ping": "pong",
                    "req_id": request["req_id"],
                }
            )
        )

    def recv(self, timeout: float) -> str | bytes:
        try:
            return cast(str | bytes, self.incoming.get(timeout=timeout))
        except queue.Empty as exc:
            raise TimeoutError from exc

    def close(self) -> None:
        self.closed = True

    def abort(self) -> None:
        self.abort_count += 1
        self.closed = True


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> float:
        self.now += seconds
        return self.now


def _connected_client(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[DerivWebSocketClient, _Connection]:
    connection = _Connection()
    monkeypatch.setattr(
        "apps.deriv_worker.websocket_client.connect",
        lambda _url, *, open_timeout: connection,
    )
    client = DerivWebSocketClient()
    client.reconnect()
    return client, connection


def _tick(index: int) -> dict[str, object]:
    return {
        "msg_type": "tick",
        "tick": {"epoch": index, "quote": f"100.{index}", "symbol": "R_100"},
        "subscription": {"id": "ticks-1"},
    }


def _watchdog_client() -> tuple[DerivWebSocketClient, _Connection, _Clock]:
    clock = _Clock()
    connection = _Connection()
    client = DerivWebSocketClient(monotonic=clock)
    client._connection = cast(object, connection)  # type: ignore[assignment]
    return client, connection, clock


def _wait_until(predicate: object, *, timeout: float = 3.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if callable(predicate) and predicate():
            return
        time.sleep(0.005)
    raise AssertionError("condition did not become true before timeout")


def test_tick_queue_full_does_not_kill_connection(monkeypatch: pytest.MonkeyPatch) -> None:
    client, _connection = _connected_client(monkeypatch)
    try:
        for index in range(130):
            client._route_response(_tick(index))

        response = client.request(DerivOperation.PING, {"ping": 1}, timeout=1.0)
        available = []
        while (item := client.receive(timeout=0.001)) is not None:
            available.append(item)

        assert response["msg_type"] == "ping"
        assert client.health_snapshot().ticks_dropped_total == 2
        assert available[-1]["tick"] == _tick(129)["tick"]
    finally:
        client.close()


def test_unknown_msg_type_is_ignored() -> None:
    client = DerivWebSocketClient()
    result = client._route_response({"msg_type": "some_future_type", "secret": "not logged"})
    assert result is TransportRouteResult.DROPPED_UNKNOWN_TYPE
    assert client.health_snapshot().unknown_msg_type_total == 1
    assert client._reader_error is None


def test_ping_pong_frames_do_not_kill_connection() -> None:
    client = DerivWebSocketClient()
    assert client._route_response({"msg_type": "ping"}) is TransportRouteResult.DELIVERED
    assert client._route_response({"msg_type": "pong"}) is TransportRouteResult.DELIVERED
    assert client._reader_error is None


def test_error_without_req_id_is_ignored() -> None:
    client = DerivWebSocketClient()
    result = client._route_response(
        {"msg_type": "error", "error": {"code": "RateLimit", "message": "sensitive"}}
    )
    assert result is TransportRouteResult.DELIVERED
    assert client._reader_error is None


def test_tick_without_subscription_is_dropped_not_fatal() -> None:
    client = DerivWebSocketClient()
    result = client._route_response({"msg_type": "tick", "tick": {"epoch": 1}})
    assert result is TransportRouteResult.DROPPED_UNKNOWN_TYPE
    assert client.health_snapshot().unsigned_event_total == 1
    assert client._reader_error is None


def test_single_malformed_frame_is_dropped_and_recovered(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, connection = _connected_client(monkeypatch)
    try:
        connection.incoming.put("not-json")
        connection.incoming.put(json.dumps({"msg_type": "pong"}))
        _wait_until(lambda: client.health_snapshot().parse_failures_total == 1)
        response = client.request(DerivOperation.PING, {"ping": 1}, timeout=1.0)
        assert response["msg_type"] == "ping"
        assert client.health_snapshot().consecutive_parse_failures == 0
    finally:
        client.close()


def test_five_consecutive_malformed_frames_kill_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, connection = _connected_client(monkeypatch)
    try:
        for _ in range(5):
            connection.incoming.put("not-json")
        _wait_until(lambda: connection.closed)
        with pytest.raises(DerivWorkerError) as captured:
            client.receive_contract(timeout=0.1)
        assert captured.value.category is DerivErrorCategory.SCHEMA_INCOMPATIBLE
        assert client.health_snapshot().consecutive_parse_failures == 5
    finally:
        client.close()


def test_contract_event_overflow_does_not_drop_settlement_silently() -> None:
    client = DerivWebSocketClient()
    event = {"msg_type": "proposal_open_contract", "proposal_open_contract": {"id": 1}}
    for _ in range(256):
        assert client._route_response(event) is TransportRouteResult.DELIVERED

    result = client._route_response(event)

    assert result is TransportRouteResult.DROPPED_BACKPRESSURE
    assert client._contract_events.qsize() == 256
    assert client.health_snapshot().contract_events_overflow_total == 1
    assert client._reader_error is None


def test_fail_reader_notifies_contract_consumers() -> None:
    client = DerivWebSocketClient()
    for index in range(256):
        client._contract_events.put_nowait(
            {"msg_type": "proposal_open_contract", "proposal_open_contract": {"id": index}}
        )
    client._fail_reader(DerivWorkerError(DerivErrorCategory.NETWORK_ERROR, "DERIV_NETWORK_ERROR"))
    with pytest.raises(DerivWorkerError) as captured:
        client.receive_contract(timeout=0.0)
    assert captured.value.reason_code == "DERIV_NETWORK_ERROR"


def test_duplicate_req_id_response_does_not_kill_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _connection = _connected_client(monkeypatch)
    response_queue: queue.Queue[dict[str, object] | BaseException] = queue.Queue(maxsize=1)
    response_queue.put_nowait({"msg_type": "ping", "req_id": 7})
    client._pending[7] = response_queue
    result = client._route_response({"msg_type": "ping", "req_id": 7})
    assert result is TransportRouteResult.DELIVERED_RESPONSE
    assert client.health_snapshot().duplicate_response_total == 1
    assert client._reader_error is None
    try:
        response = client.request(DerivOperation.PING, {"ping": 1}, timeout=1.0)
        assert response["msg_type"] == "ping"
    finally:
        client.close()


def test_ten_thousand_ticks_with_slow_consumer_never_fail_reader() -> None:
    client = DerivWebSocketClient()
    for index in range(10_000):
        client._route_response(_tick(index))

    snapshot = client.health_snapshot()
    assert snapshot.ticks_dropped_total == 10_000 - 128
    assert client._reader_error is None
    latest = None
    while (item := client.receive(timeout=0.001)) is not None:
        latest = item
    assert latest is not None
    assert latest["tick"] == _tick(9_999)["tick"]


def test_half_open_connection_is_killed_by_rx_stall() -> None:
    client, connection, clock = _watchdog_client()
    clock.advance(31.0)
    client._last_watchdog_tick = 30.0

    assert client._watchdog_iteration(clock()) is False
    snapshot = client.health_snapshot()
    assert snapshot.last_kill_reason == "rx_stall"
    assert snapshot.heartbeat_kills_total == 1
    assert connection.closed is True
    assert client._connection is None


def test_ping_sent_after_idle_and_pong_restores_liveness() -> None:
    client, connection, clock = _watchdog_client()
    clock.advance(16.0)
    client._last_watchdog_tick = 15.0

    assert client._watchdog_iteration(clock()) is True
    assert connection.sent == [{"ping": 1}]
    assert client.health_snapshot().pings_sent_total == 1
    assert client._pending_ping_deadline == 26.0

    clock.advance(1.0)
    client._record_received_frame()
    client._route_response({"msg_type": "pong"})
    snapshot = client.health_snapshot()
    assert client._pending_ping_deadline is None
    assert snapshot.pongs_received_total == 1
    assert snapshot.heartbeat_kills_total == 0


def test_pong_timeout_kills_connection() -> None:
    client, _connection, clock = _watchdog_client()
    clock.advance(16.0)
    client._last_watchdog_tick = 15.0
    assert client._watchdog_iteration(clock()) is True

    clock.advance(11.0)
    client._last_watchdog_tick = 26.0
    assert client._watchdog_iteration(clock()) is False
    assert client.health_snapshot().last_kill_reason == "pong_timeout"


def test_continuous_ticks_never_trigger_ping_or_kill() -> None:
    client, connection, clock = _watchdog_client()
    for second in range(1, 301):
        clock.advance(1.0)
        if second % 2 == 0:
            client._record_received_frame()
            client._route_response(_tick(second))
        assert client._watchdog_iteration(clock()) is True

    snapshot = client.health_snapshot()
    assert connection.sent == []
    assert snapshot.pings_sent_total == 0
    assert snapshot.heartbeat_kills_total == 0


def test_any_frame_counts_as_liveness() -> None:
    client, _connection, clock = _watchdog_client()
    for second in range(1, 61):
        clock.advance(1.0)
        if second % 5 == 0:
            client._record_received_frame()
            client._route_response({"msg_type": "future_liveness_frame"})
        assert client._watchdog_iteration(clock()) is True

    snapshot = client.health_snapshot()
    assert snapshot.unknown_msg_type_total == 12
    assert snapshot.heartbeat_kills_total == 0


def test_suspend_gap_kills_immediately_without_ping() -> None:
    client, connection, clock = _watchdog_client()
    clock.advance(300.0)

    assert client._watchdog_iteration(clock()) is False
    snapshot = client.health_snapshot()
    assert snapshot.last_kill_reason == "system_suspend_detected"
    assert snapshot.suspend_detections_total == 1
    assert connection.sent == []


def test_ping_send_failure_kills_connection() -> None:
    client, connection, clock = _watchdog_client()
    connection.fail_send = True
    clock.advance(16.0)
    client._last_watchdog_tick = 15.0

    assert client._watchdog_iteration(clock()) is False
    assert client.health_snapshot().last_kill_reason == "ping_send_failed"


def test_clean_close_does_not_trigger_watchdog_kill(monkeypatch: pytest.MonkeyPatch) -> None:
    client, _connection = _connected_client(monkeypatch)
    client.close()

    assert client.health_snapshot().heartbeat_kills_total == 0
    assert client.receive(timeout=0.001) is None
    assert client.receive_account(timeout=0.0) is None
    assert client.receive_contract(timeout=0.0) is None


def test_kill_connection_is_idempotent() -> None:
    client, connection, _clock = _watchdog_client()
    barrier = threading.Barrier(3)
    errors: list[BaseException] = []

    def kill() -> None:
        try:
            barrier.wait()
            client._kill_connection("rx_stall")
        except BaseException as exc:
            errors.append(exc)

    first = threading.Thread(target=kill)
    second = threading.Thread(target=kill)
    first.start()
    second.start()
    barrier.wait()
    first.join()
    second.join()

    assert errors == []
    assert client.health_snapshot().heartbeat_kills_total == 1
    assert connection.abort_count == 1


def test_kill_notifies_all_consumer_queues_including_contracts() -> None:
    client, _connection, _clock = _watchdog_client()
    client._kill_connection("rx_stall")

    with pytest.raises(DerivWorkerError, match="DERIV_HEARTBEAT_TIMEOUT"):
        client.receive(timeout=0.001)
    with pytest.raises(DerivWorkerError, match="DERIV_HEARTBEAT_TIMEOUT"):
        client.receive_account(timeout=0.0)
    with pytest.raises(DerivWorkerError, match="DERIV_HEARTBEAT_TIMEOUT"):
        client.receive_contract(timeout=0.0)


def test_ping_is_accepted_by_read_only_allowlist() -> None:
    assert (
        validate_read_only_request(DerivOperation.PING, {"ping": 1}, demo_authenticated=False)
        is DerivOperation.PING
    )
    with pytest.raises(DerivWorkerError):
        validate_read_only_request(
            DerivOperation.BUY, {"buy": "proposal"}, demo_authenticated=False
        )


def test_watchdog_thread_is_daemon_and_exits(monkeypatch: pytest.MonkeyPatch) -> None:
    client, _connection = _connected_client(monkeypatch)
    watchdog = client._watchdog
    assert watchdog is not None
    assert watchdog.daemon is True
    assert watchdog.is_alive() is True

    client.close()

    assert watchdog.is_alive() is False
