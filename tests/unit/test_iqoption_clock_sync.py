from __future__ import annotations

import json
import threading
from decimal import Decimal

import pytest

from packages.brokers.iqoption.community_read_only import (
    IQOptionAccountMode,
    IQOptionCommunityReadOnlySession,
    IQOptionExternalError,
)
from packages.security import SecretValue
from tests.unit.test_iqoption_community_read_only import FakeWebSocket, _messages


class TimedSocket(FakeWebSocket):
    def __init__(self, clock: list[float], *, timestamp: bool = True) -> None:
        super().__init__([m for m in _messages() if timestamp or m["name"] != "timeSync"])
        self.clock = clock
        self.delay = 0.125
        self.pings = 0
        self.respond = True

    def ping(self) -> threading.Event:
        self.pings += 1
        self.clock[0] += self.delay
        self.clock[1] += self.delay
        result = threading.Event()
        if self.respond:
            result.set()
        return result


def make_session(clock: list[float], sock: TimedSocket) -> IQOptionCommunityReadOnlySession:
    def open_socket() -> TimedSocket:
        # Slow transport startup must not become the clock's permanent RTT.
        clock[0] += 8
        clock[1] += 8
        return sock

    return IQOptionCommunityReadOnlySession(
        "clock-test@example.invalid",
        SecretValue.from_text("test-only"),
        IQOptionAccountMode.PRACTICE,
        login=lambda *_: SecretValue.from_text("test-only-session"),
        websocket_factory=open_socket,
        monotonic=lambda: clock[0],
        wall_time=lambda: clock[1],
    )


@pytest.fixture
def connected():
    clock = [0.0, 1_799_999_992.0]
    sock = TimedSocket(clock)
    session = make_session(clock, sock)
    session.connect()
    try:
        yield session, sock, clock
    finally:
        session.close()


def test_slow_startup_uses_ping_rtt_and_caches_probe(connected) -> None:
    session, sock, _ = connected
    snapshot = session.get_clock()
    assert snapshot.is_synced
    assert snapshot.round_trip_milliseconds == 125
    assert session.get_clock().is_synced
    assert sock.pings == 1
    assert {r["name"] for r in sock.sent} <= {"authenticate", "sendMessage", "timesync"}


def test_excessive_rtt_stays_blocked_and_recovers_without_login(connected) -> None:
    session, sock, clock = connected
    sock.delay = 1.25
    assert not session.get_clock().is_synced
    clock[0] += 10
    clock[1] += 10
    sock.delay = 0.125
    assert session.get_clock().is_synced
    assert sum(r["name"] == "authenticate" for r in sock.sent) == 1


def test_missing_server_time_never_falls_back_to_local_clock() -> None:
    clock = [0.0, 1_799_999_992.0]
    sock = TimedSocket(clock, timestamp=False)
    session = make_session(clock, sock)
    session.connect()
    try:
        with pytest.raises(IQOptionExternalError, match="CLOCK_UNAVAILABLE"):
            session.get_clock()
    finally:
        session.close()


@pytest.mark.parametrize("advance", [(31, 31), (0, 5), (0, -5)])
def test_stale_source_or_wall_clock_jump_invalidates_clock(connected, advance) -> None:
    session, _, clock = connected
    assert session.get_clock().is_synced
    clock[0] += advance[0]
    clock[1] += advance[1]
    with pytest.raises(IQOptionExternalError, match="CLOCK_UNAVAILABLE"):
        session.get_clock()
    session._handle_message(json.dumps({"name": "timeSync", "msg": int(clock[1] * 1000)}))
    assert session.get_clock().is_synced


def test_precision_and_excessive_offset(connected) -> None:
    session, _, clock = connected
    clock[1] += 0.875
    session._handle_message('{"name":"timeSync","msg":1800000000875}')
    assert session.get_clock().estimated_offset_seconds == Decimal(0)
    session._handle_message(json.dumps({"name": "timeSync", "msg": int((clock[1] - 5) * 1000)}))
    assert not session.get_clock().is_synced


def test_disconnected_cache_is_not_a_clock(connected) -> None:
    session, _, _ = connected
    assert session.get_clock().is_synced
    session.close()
    with pytest.raises(IQOptionExternalError, match="CLOCK_UNAVAILABLE"):
        session.get_clock()


def test_pong_timeout_invalidates_cached_rtt(connected, monkeypatch) -> None:
    session, sock, clock = connected
    assert session.get_clock().is_synced
    clock[0] += 10
    clock[1] += 10
    sock.respond = False
    monkeypatch.setattr(
        "packages.brokers.iqoption.community_read_only.IQOPTION_CLOCK_PROBE_TIMEOUT_SECONDS", 0.001
    )
    for _ in range(2):
        with pytest.raises(IQOptionExternalError, match="CLOCK_UNAVAILABLE"):
            session.get_clock()
    assert sock.pings == 2  # bounded retry cadence, even after a failed probe


def test_reconnect_requires_new_timestamp_and_new_probe(connected) -> None:
    session, sock, _ = connected
    assert session.get_clock().is_synced
    session.close()
    sock.messages.extend(json.dumps(m) for m in _messages() if m["name"] != "timeSync")
    session.connect()
    with pytest.raises(IQOptionExternalError, match="CLOCK_UNAVAILABLE"):
        session.get_clock()
    assert sock.pings == 2


@pytest.mark.parametrize("failure", ["missing", "latency", "stale"])
def test_core_blocks_only_iq_and_resumes_on_fresh_clock_without_relogin(failure) -> None:
    from datetime import UTC, datetime, timedelta
    from types import SimpleNamespace

    from apps.core.health import HealthGate
    from apps.core.iqoption_auto_trader import IQOPTION_PRACTICE_ACCOUNT_ID, IqOptionAutoTrader
    from apps.core.iqoption_risk_config import IqOptionRiskConfig
    from apps.core.worker_client import DeliveryCertainty, WorkerDispatchError
    from packages.domain.market import BrokerClockSnapshot
    from packages.domain.models import Broker
    from packages.protocol import ProtocolErrorCode
    from tests.unit.test_iqoption_auto_trader import (
        FakeClient,
        FakeRuntime,
        _falling_prices,
        _make_candles,
    )

    now = datetime.now(UTC)
    mono = [100.0]

    class Client(FakeClient):
        healthy = False

        def broker_clock(self):
            if not self.healthy and failure == "missing":
                raise WorkerDispatchError(
                    ProtocolErrorCode.IQOPTION_CLOCK_UNAVAILABLE,
                    DeliveryCertainty.NOT_SENT,
                    "clock unavailable",
                )
            return BrokerClockSnapshot(
                int(now.timestamp()),
                now - timedelta(seconds=31 if not self.healthy and failure == "stale" else 0),
                8.0 if not self.healthy and failure == "latency" else 0.125,
                Decimal(0),
            )

    client = Client(_make_candles(_falling_prices()))
    runtime = FakeRuntime()
    runtime.health_gate = HealthGate()
    recovery = []
    trader = IqOptionAutoTrader(
        supervisor_provider=lambda: SimpleNamespace(client=client),
        runtime_provider=lambda: runtime,
        risk_config_provider=lambda: IqOptionRiskConfig(symbol="EURUSD-OTC"),
        operator_armed=lambda: True,
        utc_clock=lambda: now,
        monotonic=lambda: mono[0],
        recovery_notifier=recovery.append,
    )
    trader._evaluate_cycle()
    assert trader.status_reason == "MD_CLOCK_UNTRUSTED"
    assert not runtime.requests
    assert not runtime.health_gate.state_for(
        Broker.IQ_OPTION.value, IQOPTION_PRACTICE_ACCOUNT_ID
    ).is_open
    assert runtime.health_gate.state_for(Broker.DERIV.value, "demo").is_open
    assert not recovery
    client.healthy = True
    mono[0] += 11
    trader._evaluate_cycle()
    assert runtime.health_gate.state_for(
        Broker.IQ_OPTION.value, IQOPTION_PRACTICE_ACCOUNT_ID
    ).is_open
    assert len(runtime.requests) == 1  # simulated Core pipeline only
    assert not recovery
