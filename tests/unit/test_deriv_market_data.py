from __future__ import annotations

import json
import queue
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from apps.deriv_worker.demo_session import DemoDerivSession, SecretValue
from apps.deriv_worker.fake_transport import FakeDerivScenario, FakeDerivTransport
from apps.deriv_worker.mapper import (
    map_active_symbols,
    map_candle_history,
    map_clock,
    map_contracts,
    map_tick,
)
from apps.deriv_worker.public_session import PublicDerivSession
from apps.deriv_worker.request_allowlist import DerivOperation, validate_read_only_request
from apps.deriv_worker.schema import DerivErrorCategory, DerivWorkerError, parse_deriv_json
from apps.deriv_worker.subscriptions import SubscriptionManager
from apps.deriv_worker.websocket_client import (
    DerivWebSocketClient,
    ReadOnlyRetryPolicy,
    validate_deriv_ws_url,
)
from packages.domain.market import MarketDataHealthState

NOW = datetime(2026, 8, 20, 12, 0, tzinfo=UTC)


def _tick_payload(*, quote: object = "1.08501") -> dict[str, object]:
    return {
        "msg_type": "tick",
        "tick": {"epoch": 1_700_000_100, "quote": quote, "symbol": "frxEURUSD"},
        "subscription": {"id": "sub-123"},
    }


def test_active_symbols_current_schema_uses_decimal_and_tolerates_extra_fields() -> None:
    payload = {
        "msg_type": "active_symbols",
        "active_symbols": [
            {
                "underlying_symbol": "frxEURUSD",
                "underlying_symbol_name": "EUR/USD",
                "underlying_symbol_type": "forex",
                "market": "forex",
                "submarket": "major_pairs",
                "pip_size": "0.0001",
                "exchange_is_open": 1,
                "is_trading_suspended": 0,
                "unknown_future_field": {"safe": True},
            }
        ],
    }

    symbol = map_active_symbols(payload, NOW)[0]

    assert symbol.broker_symbol == "frxEURUSD"
    assert symbol.canonical_symbol is None
    assert symbol.pip_size == Decimal("0.0001")
    assert not isinstance(symbol.pip_size, float)


def test_missing_required_active_symbol_field_is_schema_incompatible() -> None:
    payload = {
        "msg_type": "active_symbols",
        "active_symbols": [
            {
                "underlying_symbol_name": "EUR/USD",
                "underlying_symbol_type": "forex",
                "market": "forex",
                "pip_size": "0.0001",
                "exchange_is_open": 1,
                "is_trading_suspended": 0,
            }
        ],
    }

    with pytest.raises(DerivWorkerError) as captured:
        map_active_symbols(payload, NOW)
    assert captured.value.category is DerivErrorCategory.SCHEMA_INCOMPATIBLE


def test_contract_tick_and_candle_mapping_are_normalized() -> None:
    contracts = map_contracts(
        {
            "msg_type": "contracts_for",
            "contracts_for": {
                "available": [{"contract_type": "CALL", "underlying_symbol": "frxEURUSD"}]
            },
        },
        "frxEURUSD",
    )
    tick = map_tick(_tick_payload(), NOW)
    candles = map_candle_history(
        {
            "msg_type": "candles",
            "candles": [
                {
                    "epoch": 1_700_000_000,
                    "open": "1.0",
                    "high": "1.2",
                    "low": "0.9",
                    "close": "1.1",
                }
            ],
        },
        "frxEURUSD",
        60,
        NOW,
    )

    assert contracts[0].contract_type == "CALL"
    assert contracts[0].duration_units == ()
    assert tick.quote == Decimal("1.08501")
    assert candles[0].is_closed is True
    assert all(not isinstance(value, float) for value in (tick.quote, candles[0].open))


@pytest.mark.parametrize("constant", ["NaN", "Infinity", "-Infinity"])
def test_non_finite_json_numbers_are_rejected(constant: str) -> None:
    with pytest.raises(DerivWorkerError) as captured:
        parse_deriv_json(f'{{"msg_type":"tick","quote":{constant}}}')
    assert captured.value.reason_code == "DERIV_SCHEMA_INCOMPATIBLE"


@pytest.mark.parametrize("quote", ["-1", "0", "NaN", "Infinity"])
def test_invalid_tick_quote_fails_closed(quote: str) -> None:
    with pytest.raises((DerivWorkerError, ValueError)):
        map_tick(_tick_payload(quote=quote), NOW)


@pytest.mark.parametrize(
    "url,reason",
    [
        (
            "wss://api.derivws.com/trading/v1/options/ws/real?otp=secret",
            "DERIV_REAL_WS_FORBIDDEN",
        ),
        (
            "wss://evil.example/trading/v1/options/ws/demo?otp=secret",
            "DERIV_WS_HOST_FORBIDDEN",
        ),
    ],
)
def test_real_or_untrusted_websocket_url_is_rejected(url: str, reason: str) -> None:
    with pytest.raises(DerivWorkerError) as captured:
        validate_deriv_ws_url(url, expected_demo=True)
    assert captured.value.reason_code == reason


def test_trading_opcode_is_rejected_before_transport() -> None:
    with pytest.raises(DerivWorkerError) as captured:
        validate_read_only_request(
            DerivOperation.TICKS,
            {"buy": "proposal-id", "ticks": "frxEURUSD"},
            demo_authenticated=False,
        )
    assert captured.value.reason_code == "DERIV_TRADING_OPERATION_DISABLED"


def test_token_and_otp_are_redacted() -> None:
    secret = SecretValue("not-a-real-token-fixture")
    assert "not-a-real" not in str(secret)
    assert "not-a-real" not in repr(secret)
    assert "REDACTED" in repr(secret)


class _TokenProvider:
    def get_access_token(self) -> SecretValue:
        return SecretValue("offline-placeholder")


class _RestFake:
    def __init__(self, *, selected_type: str = "demo", ws_path: str = "demo") -> None:
        self.selected_type = selected_type
        self.ws_path = ws_path
        self.otp_requests = 0

    def get_accounts(self, token: SecretValue, app_id: str) -> dict[str, object]:
        del token, app_id
        return {
            "data": [
                {"account_id": "real-ignored", "account_type": "real"},
                {"account_id": "selected", "account_type": self.selected_type},
            ]
        }

    def request_otp(self, token: SecretValue, app_id: str, account_id: str) -> dict[str, object]:
        del token, app_id, account_id
        self.otp_requests += 1
        return {
            "data": {
                "url": (
                    "wss://api.derivws.com/trading/v1/options/ws/"
                    f"{self.ws_path}?otp=ephemeral-placeholder"
                )
            }
        }


def test_demo_auth_selects_explicit_proven_demo_and_remains_read_only() -> None:
    rest = _RestFake()
    transport = FakeDerivTransport()
    session = DemoDerivSession(
        _TokenProvider(),
        rest,
        "offline-app-id",
        transport_factory=lambda _url: transport,
    )

    assert session.open("selected") is transport
    assert rest.otp_requests == 1
    assert session.capabilities.authenticated is True
    assert session.capabilities.can_trade is False
    session.close()


def test_demo_auth_rejects_real_account_before_otp() -> None:
    rest = _RestFake(selected_type="real")
    blocked: list[str] = []
    session = DemoDerivSession(
        _TokenProvider(),
        rest,
        "offline-app-id",
        on_forbidden_real=blocked.append,
    )

    with pytest.raises(DerivWorkerError) as captured:
        session.open("selected")
    assert captured.value.reason_code == "DERIV_REAL_ACCOUNT_FORBIDDEN"
    assert rest.otp_requests == 0
    assert blocked == ["DERIV_REAL_ACCOUNT_FORBIDDEN"]


def test_demo_auth_rejects_real_ready_url_before_connect() -> None:
    rest = _RestFake(ws_path="real")
    blocked: list[str] = []
    session = DemoDerivSession(
        _TokenProvider(),
        rest,
        "offline-app-id",
        on_forbidden_real=blocked.append,
    )

    with pytest.raises(DerivWorkerError) as captured:
        session.open("selected")
    assert captured.value.reason_code == "DERIV_REAL_WS_FORBIDDEN"
    assert blocked == ["DERIV_REAL_WS_FORBIDDEN"]


def test_rate_limit_uses_bounded_injected_backoff_without_busy_loop() -> None:
    transport = FakeDerivTransport(FakeDerivScenario.RATE_LIMIT)
    delays: list[float] = []
    session = PublicDerivSession(
        transport,
        retry_policy=ReadOnlyRetryPolicy(
            max_attempts=2,
            base_delay_seconds=0.1,
            max_delay_seconds=0.2,
        ),
        sleeper=delays.append,
        jitter=lambda _ceiling: 0.0,
    )

    session.connect()

    assert delays == [0.1]
    assert transport.reconnect_count == 2


def test_read_only_timeout_retry_is_bounded_and_has_no_busy_loop() -> None:
    transport = FakeDerivTransport(FakeDerivScenario.SLOW_RESPONSE)
    delays: list[float] = []
    session = PublicDerivSession(
        transport,
        retry_policy=ReadOnlyRetryPolicy(
            max_attempts=3,
            base_delay_seconds=0.1,
            max_delay_seconds=0.2,
        ),
        sleeper=delays.append,
        jitter=lambda _ceiling: 0.0,
    )

    with pytest.raises(DerivWorkerError) as captured:
        session.ping()
    assert captured.value.reason_code == "DERIV_REQUEST_TIMEOUT"
    assert transport.operation_counts[DerivOperation.PING] == 3
    assert delays == [0.1, 0.2]


class _FakeWebSocketConnection:
    def __init__(self) -> None:
        self.incoming: queue.Queue[str] = queue.Queue()
        self.sent: list[dict[str, object]] = []
        self.closed = False

    def send(self, raw: str) -> None:
        request = json.loads(raw)
        self.sent.append(request)
        self.incoming.put(
            json.dumps(
                {
                    "msg_type": "tick",
                    "req_id": request["req_id"],
                    "tick": {"epoch": 1_700_000_100, "quote": "1.08501", "symbol": "frxEURUSD"},
                    "subscription": {"id": "sub-1"},
                }
            )
        )

    def recv(self, timeout: float) -> str:
        try:
            return self.incoming.get(timeout=timeout)
        except queue.Empty as exc:
            raise TimeoutError from exc

    def close(self) -> None:
        self.closed = True


def test_websocket_reader_multiplexes_request_response_and_stream_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = _FakeWebSocketConnection()

    def _connect(url: str, *, open_timeout: float) -> _FakeWebSocketConnection:
        del url, open_timeout
        return connection

    monkeypatch.setattr("apps.deriv_worker.websocket_client.connect", _connect)
    client = DerivWebSocketClient()
    try:
        response = client.request(
            DerivOperation.TICKS,
            {"ticks": "frxEURUSD", "subscribe": 1},
            timeout=1.0,
        )
        connection.incoming.put(
            json.dumps(
                {
                    "msg_type": "tick",
                    "tick": {
                        "epoch": 1_700_000_101,
                        "quote": "1.08502",
                        "symbol": "frxEURUSD",
                    },
                    "subscription": {"id": "sub-1"},
                }
            )
        )
        event = client.receive(timeout=1.0)
    finally:
        client.close()

    assert response["req_id"] == 1
    assert connection.sent == [{"ticks": "frxEURUSD", "subscribe": 1, "req_id": 1}]
    assert event is not None
    assert event["msg_type"] == "tick"
    assert connection.closed is True


@pytest.mark.parametrize("_repetition", range(3))
def test_subscription_queue_gap_duplicates_late_and_stale_are_explicit(
    _repetition: int,
) -> None:
    clock = [10.0]
    manager = SubscriptionManager(
        queue_size=1,
        max_tick_gap_seconds=1,
        monotonic=lambda: clock[0],
    )
    first = map_tick(_tick_payload(), NOW)
    manager.register(first, "request-1")
    assert manager.ingest(first) is MarketDataHealthState.HEALTHY
    assert manager.ingest(first) is MarketDataHealthState.HEALTHY
    assert manager.duplicates == 1
    later_payload = _tick_payload()
    later_payload["tick"] = {
        "epoch": first.epoch + 3,
        "quote": "1.08502",
        "symbol": "frxEURUSD",
    }
    later = map_tick(later_payload, NOW)
    assert manager.ingest(later) is MarketDataHealthState.GAPPED
    assert manager.ticks_dropped == 1
    earlier_payload = _tick_payload()
    earlier_payload["tick"] = {
        "epoch": first.epoch + 2,
        "quote": "1.08503",
        "symbol": "frxEURUSD",
    }
    manager.ingest(map_tick(earlier_payload, NOW))
    assert manager.late_ticks == 1
    clock[0] = 20.0
    assert manager.evaluate_staleness(5.0) is MarketDataHealthState.STALE


@pytest.mark.parametrize("_repetition", range(3))
def test_reconnect_backfills_and_restores_one_logical_subscription(
    _repetition: int,
) -> None:
    transport = FakeDerivTransport()
    session = PublicDerivSession(transport)
    session.connect()
    session.subscribe_ticks("frxEURUSD")

    for _attempt in range(3):
        session.reconnect()
        assert session.subscriptions.logical_count == 1
    assert transport.operation_counts[DerivOperation.TICKS_HISTORY] == 3


@pytest.mark.parametrize("_repetition", range(3))
def test_stream_pump_ingests_continuous_ticks_after_subscription(_repetition: int) -> None:
    transport = FakeDerivTransport()
    session = PublicDerivSession(transport)
    session.connect()
    first = session.subscribe_ticks("frxEURUSD", correlation_id="corr-stream")
    assert session.next_stream_tick(timeout=1.0) == first
    transport.emit_tick(
        epoch=first.epoch + 1,
        quote="1.08502",
        symbol=first.broker_symbol,
        subscription_id=first.subscription_id,
    )

    streamed = session.next_stream_tick(timeout=1.0)

    assert streamed is not None
    assert streamed.epoch == first.epoch + 1
    assert session.event_correlation_id(streamed) == "corr-stream"
    assert session.subscriptions.ticks_received == 2


@pytest.mark.parametrize("_repetition", range(3))
def test_windows_suspend_gap_invalidates_quotes_until_reconnect(_repetition: int) -> None:
    clock = [10.0]
    transport = FakeDerivTransport()
    session = PublicDerivSession(transport, monotonic=lambda: clock[0])
    session.connect()
    session.subscribe_ticks("frxEURUSD")
    clock[0] = 80.0

    assert session.detect_suspension(max_gap_seconds=30.0) is True
    assert session.health is MarketDataHealthState.STALE
    assert session.subscriptions.symbols_to_restore() == ("frxEURUSD",)

    session.reconnect()
    assert session.health is MarketDataHealthState.HEALTHY
    assert transport.operation_counts[DerivOperation.TICKS_HISTORY] == 1


def test_server_clock_offset_uses_decimal_and_monotonic_duration() -> None:
    snapshot = map_clock(
        {"msg_type": "time", "time": 1_700_000_100},
        datetime.fromtimestamp(1_700_000_000, UTC),
        0.2,
    )
    assert snapshot.round_trip_seconds == 0.2
    assert snapshot.estimated_offset_seconds == Decimal("100.1")
