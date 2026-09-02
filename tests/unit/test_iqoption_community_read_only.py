from __future__ import annotations

import json
import secrets
import socket
import threading
from collections.abc import Iterable
from datetime import UTC, datetime
from decimal import Decimal
from typing import cast
from uuid import uuid4

import pytest

from apps.core.worker_client import SocketWorkerClient
from apps.iqoption_connection_worker.server import IQOptionReadOnlyWorkerServer
from packages.brokers.iqoption.community_read_only import (
    IQOPTION_LOGIN_ROUTES,
    IQOPTION_WEBSOCKET_URLS,
    IQOptionAccountMode,
    IQOptionCommunityReadOnlySession,
    IQOptionConnectionSnapshot,
    IQOptionExternalError,
    _login,
    _websocket_factory,
)
from packages.domain.models import Broker
from packages.protocol import EndpointRole, Envelope, MessageType
from packages.protocol.transport import FramedSocket
from packages.security import SecretValue


class FakeLoginResponse:
    def __init__(
        self,
        status: int,
        *,
        body: dict[str, object] | None = None,
        cookies: tuple[str, ...] = (),
    ) -> None:
        self.status = status
        self._body = json.dumps(body or {}).encode("utf-8")
        self._cookies = cookies
        self.headers = self

    def read(self, _limit: int) -> bytes:
        return self._body

    def get_all(self, name: str, default: object = None) -> tuple[str, ...] | object:
        return self._cookies if name == "Set-Cookie" else default


class PlannedHttpsConnection:
    def __init__(
        self,
        host: str,
        timeout: float,
        *,
        outcomes: dict[tuple[str, str], FakeLoginResponse | Exception],
        calls: list[tuple[str, str, float]],
    ) -> None:
        self._host = host
        self._timeout = timeout
        self._outcomes = outcomes
        self._calls = calls
        self._path = ""

    def request(
        self,
        method: str,
        path: str,
        *,
        body: bytes,
        headers: dict[str, str],
    ) -> None:
        assert method == "POST"
        assert body
        assert headers["Content-Type"] == "application/x-www-form-urlencoded"
        self._path = path
        self._calls.append((self._host, path, self._timeout))

    def getresponse(self) -> FakeLoginResponse:
        outcome = self._outcomes[(self._host, self._path)]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    def close(self) -> None:
        return None


def _patch_login_routes(
    monkeypatch: pytest.MonkeyPatch,
    outcomes: dict[tuple[str, str], FakeLoginResponse | Exception],
) -> list[tuple[str, str, float]]:
    calls: list[tuple[str, str, float]] = []
    monkeypatch.setattr(
        "packages.brokers.iqoption.community_read_only.http.client.HTTPSConnection",
        lambda host, timeout: PlannedHttpsConnection(
            host,
            timeout,
            outcomes=outcomes,
            calls=calls,
        ),
    )
    return calls


def test_login_uses_current_main_api_route_first(monkeypatch: pytest.MonkeyPatch) -> None:
    assert IQOPTION_LOGIN_ROUTES[0] == ("api.iqoption.com", "/v2/login")
    session = secrets.token_urlsafe(24)
    calls = _patch_login_routes(
        monkeypatch,
        {
            IQOPTION_LOGIN_ROUTES[0]: FakeLoginResponse(
                200,
                cookies=(f"ssid={session}; Path=/; Secure",),
            )
        },
    )

    result = _login(
        "trader@example.com",
        SecretValue.from_text(secrets.token_urlsafe(24)),
        20.0,
    )

    assert result.reveal_text() == session
    assert [(host, path) for host, path, _timeout in calls] == [IQOPTION_LOGIN_ROUTES[0]]


def test_login_falls_back_and_accepts_legacy_json_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = secrets.token_urlsafe(24)
    calls = _patch_login_routes(
        monkeypatch,
        {
            IQOPTION_LOGIN_ROUTES[0]: FakeLoginResponse(503),
            IQOPTION_LOGIN_ROUTES[1]: TimeoutError(),
            IQOPTION_LOGIN_ROUTES[2]: FakeLoginResponse(503),
            IQOPTION_LOGIN_ROUTES[3]: FakeLoginResponse(
                200,
                body={"data": {"ssid": session}},
            ),
        },
    )

    result = _login(
        "trader@example.com",
        SecretValue.from_text(secrets.token_urlsafe(24)),
        20.0,
    )

    assert result.reveal_text() == session
    assert [(host, path) for host, path, _timeout in calls] == list(IQOPTION_LOGIN_ROUTES)


def test_login_reports_network_unreachable_when_every_route_times_out(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _patch_login_routes(
        monkeypatch,
        {route: TimeoutError() for route in IQOPTION_LOGIN_ROUTES},
    )

    with pytest.raises(IQOptionExternalError, match="IQOPTION_NETWORK_UNREACHABLE"):
        _login(
            "trader@example.com",
            SecretValue.from_text(secrets.token_urlsafe(24)),
            20.0,
        )

    assert [(host, path) for host, path, _timeout in calls] == list(IQOPTION_LOGIN_ROUTES)


def test_login_does_not_spray_invalid_credentials_across_fallbacks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _patch_login_routes(
        monkeypatch,
        {IQOPTION_LOGIN_ROUTES[0]: FakeLoginResponse(401)},
    )

    with pytest.raises(IQOptionExternalError, match="IQOPTION_AUTH_FAILED"):
        _login(
            "trader@example.com",
            SecretValue.from_text(secrets.token_urlsafe(24)),
            20.0,
        )

    assert [(host, path) for host, path, _timeout in calls] == [IQOPTION_LOGIN_ROUTES[0]]


def test_login_does_not_retry_rate_limit_on_another_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _patch_login_routes(
        monkeypatch,
        {IQOPTION_LOGIN_ROUTES[0]: FakeLoginResponse(429)},
    )

    with pytest.raises(IQOptionExternalError, match="IQOPTION_RATE_LIMITED"):
        _login(
            "trader@example.com",
            SecretValue.from_text(secrets.token_urlsafe(24)),
            20.0,
        )

    assert [(host, path) for host, path, _timeout in calls] == [IQOPTION_LOGIN_ROUTES[0]]


def test_websocket_uses_dedicated_host_then_main_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    websocket = FakeWebSocket(())

    def connect(url: str, **_kwargs: object) -> FakeWebSocket:
        calls.append(url)
        if url == IQOPTION_WEBSOCKET_URLS[0]:
            raise OSError("dedicated endpoint unavailable")
        return websocket

    monkeypatch.setattr(
        "packages.brokers.iqoption.community_read_only.websocket_connect",
        connect,
    )

    assert _websocket_factory() is websocket
    assert calls == list(IQOPTION_WEBSOCKET_URLS)


class FakeWebSocket:
    def __init__(self, messages: Iterable[dict[str, object]]) -> None:
        self.messages = [json.dumps(item) for item in messages]
        self.sent: list[dict[str, object]] = []
        self.closed = False
        self._idle = threading.Event()

    def send(self, message: str) -> None:
        self.sent.append(json.loads(message))

    def recv(self, timeout: float | None = None) -> str:
        if self.messages:
            return self.messages.pop(0)
        self._idle.wait(min(timeout or 0.01, 0.01))
        raise TimeoutError

    def close(self) -> None:
        self.closed = True
        self._idle.set()


class RequestDrivenWebSocket(FakeWebSocket):
    """Mimic current sessions that require explicit account reads after auth."""

    def __init__(self) -> None:
        super().__init__([{"name": "authenticated", "msg": True}])

    def send(self, message: str) -> None:
        super().send(message)
        request = self.sent[-1]
        if request.get("name") != "sendMessage":
            return
        body = request.get("msg")
        if not isinstance(body, dict):
            return
        if body.get("name") == "get-profile":
            self.messages.append(json.dumps({"name": "profile", "msg": {"user_id": 42}}))
        if body.get("name") == "get-balances":
            self.messages.append(
                json.dumps(
                    {
                        "name": "balances",
                        "msg": [
                            {
                                "id": 2,
                                "type": 4,
                                "amount": "10000.00",
                                "currency": "USD",
                            }
                        ],
                    }
                )
            )


class TradingRequestWebSocket(FakeWebSocket):
    def __init__(self) -> None:
        super().__init__(_messages())

    def send(self, message: str) -> None:
        super().send(message)
        request = self.sent[-1]
        request_id = request.get("request_id")
        raw_message = request.get("msg")
        if not isinstance(raw_message, dict) or not isinstance(request_id, str):
            return
        name = raw_message.get("name")
        if name == "get-candles":
            self.messages.append(
                json.dumps(
                    {
                        "name": "candles",
                        "request_id": request_id,
                        "msg": {
                            "candles": [
                                {
                                    "from": 1_799_999_880,
                                    "to": 1_799_999_940,
                                    "open": "1.1000",
                                    "max": "1.1010",
                                    "min": "1.0990",
                                    "close": "1.1005",
                                }
                            ]
                        },
                    }
                )
            )
        elif name == "binary-options.open-option":
            self.messages.extend(
                [
                    json.dumps(
                        {
                            "name": "option",
                            "request_id": request_id,
                            "msg": {"id": 12345},
                        }
                    ),
                    json.dumps(
                        {
                            "name": "option-opened",
                            "msg": {"option_id": 12345, "amount": "1.00"},
                        }
                    ),
                ]
            )
        elif request.get("name") == "api_game_betinfo":
            raw_id = request.get("msg")
            option_id = raw_id.get("id[0]") if isinstance(raw_id, dict) else None
            self.messages.append(
                json.dumps(
                    {
                        "name": "api_game_betinfo_result",
                        "msg": {
                            "isSuccessful": True,
                            "result": {
                                "data": {
                                    str(option_id): {
                                        "option_id": option_id,
                                        "status": "win",
                                        "profit_amount": "1.85",
                                        "amount": "1.00",
                                    }
                                }
                            },
                        },
                    }
                )
            )
        elif name == "get-options":
            self.messages.append(
                json.dumps(
                    {
                        "name": "options",
                        "msg": {
                            "open_options": [],
                            "closed_options": [
                                {
                                    "id": [12345],
                                    "win": "win",
                                    "win_amount": "1.85",
                                    "amount": "1.00",
                                }
                            ],
                        },
                    }
                )
            )


def _messages() -> list[dict[str, object]]:
    return [
        {"name": "authenticated", "msg": True},
        {"name": "profile", "msg": {"user_id": 42}},
        {"name": "timeSync", "msg": 1_800_000_000_000},
        {
            "name": "balances",
            "msg": [
                {"id": 1, "type": 1, "amount": "125.40", "currency": "USD"},
                {"id": 2, "type": 4, "amount": "10000.00", "currency": "USD"},
            ],
        },
    ]


@pytest.mark.parametrize(
    ("mode", "expected_minor_units", "expected_type"),
    [
        (IQOptionAccountMode.PRACTICE, 1_000_000, "DEMO"),
        (IQOptionAccountMode.REAL, 12_540, "REAL"),
    ],
)
def test_read_only_session_authenticates_and_selects_explicit_balance(
    mode: IQOptionAccountMode,
    expected_minor_units: int,
    expected_type: str,
) -> None:
    websocket = FakeWebSocket(_messages())
    ephemeral_secret = SecretValue.from_text(secrets.token_urlsafe(24))
    auth_secret = SecretValue.from_text(secrets.token_urlsafe(24))

    def login(_email: str, credential: SecretValue, _timeout: float) -> SecretValue:
        assert credential is ephemeral_secret
        return auth_secret

    session = IQOptionCommunityReadOnlySession(
        "trader@example.com",
        ephemeral_secret,
        mode,
        login=login,
        websocket_factory=lambda: websocket,
        wall_time=lambda: 1_800_000_000,
    )
    try:
        snapshot = session.connect()
        assert snapshot.connected is True
        assert snapshot.profile_confirmed is True
        assert snapshot.balance.balance_minor_units == expected_minor_units
        assert snapshot.balance.account_type == expected_type
        assert session.get_clock().server_epoch == 1_800_000_000
        assert not hasattr(session, "submit_order")
        assert websocket.sent[0]["name"] == "authenticate"
    finally:
        session.close()
    assert websocket.closed is True


def test_websocket_authentication_gets_a_fresh_deadline_after_slow_http_login() -> None:
    websocket = FakeWebSocket(_messages())
    clock = [0.0]

    def login(_email: str, _credential: SecretValue, _timeout: float) -> SecretValue:
        clock[0] = 19.9
        return SecretValue.from_text(secrets.token_urlsafe(24))

    def open_websocket() -> FakeWebSocket:
        # This crosses the old end-to-end deadline.  Authentication must still
        # receive its own bounded phase because HTTP login already succeeded.
        clock[0] += 0.2
        return websocket

    session = IQOptionCommunityReadOnlySession(
        "trader@example.com",
        SecretValue.from_text(secrets.token_urlsafe(24)),
        IQOptionAccountMode.PRACTICE,
        login=login,
        websocket_factory=open_websocket,
        monotonic=lambda: clock[0],
        wall_time=lambda: 1_800_000_000,
    )
    try:
        snapshot = session.connect(timeout=20.0)
    finally:
        session.close()

    assert snapshot.connected is True
    assert snapshot.balance.account_type == "DEMO"


def test_websocket_reconnect_reuses_ssid_without_new_http_login() -> None:
    websockets = [FakeWebSocket(_messages()), FakeWebSocket(_messages())]
    login_calls: list[str] = []

    def login(email: str, _credential: SecretValue, _timeout: float) -> SecretValue:
        login_calls.append(email)
        return SecretValue.from_text("memory-only-ssid")

    session = IQOptionCommunityReadOnlySession(
        "trader@example.com",
        SecretValue.from_text(secrets.token_urlsafe(24)),
        IQOptionAccountMode.PRACTICE,
        login=login,
        websocket_factory=lambda: websockets.pop(0),
    )
    try:
        session.connect()
        session._disconnected.set()
        snapshot = session.reconnect()
        assert snapshot.connected is True
    finally:
        session.close()

    assert login_calls == ["trader@example.com"]


def test_concurrent_connect_calls_create_only_one_authenticated_websocket() -> None:
    websocket = FakeWebSocket(_messages())
    login_calls = 0
    websocket_calls = 0

    def login(_email: str, _credential: SecretValue, _timeout: float) -> SecretValue:
        nonlocal login_calls
        login_calls += 1
        return SecretValue.from_text("memory-only-ssid")

    def websocket_factory() -> FakeWebSocket:
        nonlocal websocket_calls
        websocket_calls += 1
        return websocket

    session = IQOptionCommunityReadOnlySession(
        "trader@example.com",
        SecretValue.from_text(secrets.token_urlsafe(24)),
        IQOptionAccountMode.PRACTICE,
        login=login,
        websocket_factory=websocket_factory,
    )
    results: list[IQOptionConnectionSnapshot] = []
    threads = [threading.Thread(target=lambda: results.append(session.connect())) for _ in range(8)]
    try:
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=2.0)
    finally:
        session.close()

    assert len(results) == 8
    assert login_calls == 1
    assert websocket_calls == 1


def test_rejected_cached_ssid_performs_exactly_one_fresh_http_login() -> None:
    websockets = [
        FakeWebSocket(_messages()),
        FakeWebSocket([{"name": "authenticated", "msg": False}]),
        FakeWebSocket(_messages()),
    ]
    cookies = iter(("expired-ssid", "fresh-ssid"))
    login_calls = 0

    def login(_email: str, _credential: SecretValue, _timeout: float) -> SecretValue:
        nonlocal login_calls
        login_calls += 1
        return SecretValue.from_text(next(cookies))

    session = IQOptionCommunityReadOnlySession(
        "trader@example.com",
        SecretValue.from_text(secrets.token_urlsafe(24)),
        IQOptionAccountMode.PRACTICE,
        login=login,
        websocket_factory=lambda: websockets.pop(0),
    )
    try:
        session.connect()
        session._disconnected.set()
        snapshot = session.connect()
        assert snapshot.connected is True
    finally:
        session.close()

    assert login_calls == 2


def test_transient_cached_ssid_failure_never_falls_back_to_http_login() -> None:
    websockets = [FakeWebSocket(_messages()), FakeWebSocket(())]
    login_calls = 0

    def login(_email: str, _credential: SecretValue, _timeout: float) -> SecretValue:
        nonlocal login_calls
        login_calls += 1
        return SecretValue.from_text("memory-only-ssid")

    session = IQOptionCommunityReadOnlySession(
        "trader@example.com",
        SecretValue.from_text(secrets.token_urlsafe(24)),
        IQOptionAccountMode.PRACTICE,
        login=login,
        websocket_factory=lambda: websockets.pop(0),
    )
    try:
        session.connect()
        session._disconnected.set()
        with pytest.raises(IQOptionExternalError, match="IQOPTION_AUTH_TIMEOUT"):
            session.connect(timeout=0.1)
    finally:
        session.close()

    assert login_calls == 1


def test_websocket_reconnect_limit_stops_repeated_external_attempts() -> None:
    created: list[FakeWebSocket] = []

    def websocket_factory() -> FakeWebSocket:
        websocket = FakeWebSocket(_messages())
        created.append(websocket)
        return websocket

    login_calls = 0

    def login(_email: str, _credential: SecretValue, _timeout: float) -> SecretValue:
        nonlocal login_calls
        login_calls += 1
        return SecretValue.from_text("memory-only-ssid")

    session = IQOptionCommunityReadOnlySession(
        "trader@example.com",
        SecretValue.from_text(secrets.token_urlsafe(24)),
        IQOptionAccountMode.PRACTICE,
        login=login,
        websocket_factory=websocket_factory,
    )
    try:
        session.connect()
        for _ in range(5):
            session._disconnected.set()
            session.connect()
        session._disconnected.set()
        with pytest.raises(
            IQOptionExternalError,
            match="IQOPTION_WEBSOCKET_RECONNECT_LIMIT_REACHED",
        ):
            session.reconnect()
        for _ in range(94):
            with pytest.raises(
                IQOptionExternalError,
                match="IQOPTION_WEBSOCKET_RECONNECT_LIMIT_REACHED",
            ):
                session.reconnect()
    finally:
        session.close()

    assert login_calls == 1
    assert len(created) == 6  # initial transport plus five bounded reconnects


def test_read_only_session_rejects_unconfirmed_authentication() -> None:
    websocket = FakeWebSocket([{"name": "authenticated", "msg": False}])
    session = IQOptionCommunityReadOnlySession(
        "trader@example.com",
        SecretValue.from_text(secrets.token_urlsafe(24)),
        IQOptionAccountMode.PRACTICE,
        login=lambda _email, _credential, _timeout: SecretValue.from_text(
            secrets.token_urlsafe(24)
        ),
        websocket_factory=lambda: websocket,
    )

    with pytest.raises(IQOptionExternalError, match="IQOPTION_AUTH_FAILED"):
        session.connect()
    assert session.is_connected is False


def test_read_only_session_requests_profile_and_balances_after_authentication() -> None:
    websocket = RequestDrivenWebSocket()
    session = IQOptionCommunityReadOnlySession(
        "trader@example.com",
        SecretValue.from_text(secrets.token_urlsafe(24)),
        IQOptionAccountMode.PRACTICE,
        login=lambda _email, _credential, _timeout: SecretValue.from_text(
            secrets.token_urlsafe(24)
        ),
        websocket_factory=lambda: websocket,
    )
    try:
        snapshot = session.connect()
        account_reads = [
            item["msg"]["name"]
            for item in websocket.sent
            if item.get("name") == "sendMessage" and isinstance(item.get("msg"), dict)
        ]
        assert snapshot.connected is True
        assert account_reads == ["get-profile", "get-balances"]
        assert not any(item.get("name") in {"buy", "send-order"} for item in websocket.sent)
    finally:
        session.close()


def test_read_only_session_fails_closed_when_requested_balance_does_not_exist() -> None:
    websocket = FakeWebSocket(
        [
            {"name": "authenticated", "msg": True},
            {"name": "profile", "msg": {"user_id": 42}},
            {
                "name": "balances",
                "msg": [{"id": 2, "type": 4, "amount": "10000.00", "currency": "USD"}],
            },
        ]
    )
    session = IQOptionCommunityReadOnlySession(
        "trader@example.com",
        SecretValue.from_text(secrets.token_urlsafe(24)),
        IQOptionAccountMode.REAL,
        login=lambda _email, _credential, _timeout: SecretValue.from_text(
            secrets.token_urlsafe(24)
        ),
        websocket_factory=lambda: websocket,
    )

    with pytest.raises(IQOptionExternalError, match="IQOPTION_ACCOUNT_MODE_UNAVAILABLE"):
        session.connect()
    assert session.is_connected is False


def test_practice_session_fetches_broker_candles_and_opens_binary_option() -> None:
    websocket = TradingRequestWebSocket()
    session = IQOptionCommunityReadOnlySession(
        "trader@example.com",
        SecretValue.from_text(secrets.token_urlsafe(24)),
        IQOptionAccountMode.PRACTICE,
        login=lambda _email, _credential, _timeout: SecretValue.from_text(
            secrets.token_urlsafe(24)
        ),
        websocket_factory=lambda: websocket,
        wall_time=lambda: 1_800_000_000,
    )
    try:
        session.connect()
        candles = session.get_candles("EURUSD-OTC", count=20)
        order = session.request(
            "buy",
            {"active": "EURUSD-OTC", "direction": "call", "price": "1.00", "duration": 1},
        )
        event = session.receive_contract(timeout=1.0)
    finally:
        session.close()

    assert len(candles) == 1
    assert candles[0].broker is Broker.IQ_OPTION
    assert candles[0].broker_symbol == "EURUSD-OTC"
    assert candles[0].close == Decimal("1.1005")
    assert order == {"status": True, "id": "12345", "result": {"id": 12345}}
    assert event is not None
    assert event["msg"]["id"] == 12345
    sent_names = [
        raw["msg"]["name"]
        for raw in websocket.sent
        if raw.get("name") == "sendMessage" and isinstance(raw.get("msg"), dict)
    ]
    assert "get-candles" in sent_names
    assert "binary-options.open-option" in sent_names


def test_practice_session_queries_exact_binary_option_status_read_only() -> None:
    websocket = TradingRequestWebSocket()
    session = IQOptionCommunityReadOnlySession(
        "trader@example.com",
        SecretValue.from_text(secrets.token_urlsafe(24)),
        IQOptionAccountMode.PRACTICE,
        login=lambda _email, _credential, _timeout: SecretValue.from_text(
            secrets.token_urlsafe(24)
        ),
        websocket_factory=lambda: websocket,
        wall_time=lambda: 1_800_000_000,
    )
    try:
        session.connect()
        result = session.request("get_betinfo", {"id": 12345})
    finally:
        session.close()

    assert result["isSuccessful"] is True
    assert result["result"]["id"] == "12345"
    assert result["result"]["status"] == "win"
    status_requests = [raw for raw in websocket.sent if raw.get("name") == "api_game_betinfo"]
    assert len(status_requests) == 1
    assert status_requests[0]["msg"] == {"currency": "USD", "id[0]": 12345}


def test_practice_session_recent_options_accepts_response_without_request_id() -> None:
    websocket = TradingRequestWebSocket()
    session = IQOptionCommunityReadOnlySession(
        "trader@example.com",
        SecretValue.from_text(secrets.token_urlsafe(24)),
        IQOptionAccountMode.PRACTICE,
        login=lambda _email, _credential, _timeout: SecretValue.from_text(
            secrets.token_urlsafe(24)
        ),
        websocket_factory=lambda: websocket,
    )
    try:
        session.connect()
        result = session.request("get_options", {"id": 12345})
    finally:
        session.close()

    assert result["isSuccessful"] is True
    assert result["result"]["id"] == 12345


def test_real_session_rejects_financial_operation_before_network_send() -> None:
    websocket = FakeWebSocket(_messages())
    session = IQOptionCommunityReadOnlySession(
        "trader@example.com",
        SecretValue.from_text(secrets.token_urlsafe(24)),
        IQOptionAccountMode.REAL,
        login=lambda _email, _credential, _timeout: SecretValue.from_text(
            secrets.token_urlsafe(24)
        ),
        websocket_factory=lambda: websocket,
    )
    try:
        session.connect()
        sent_before = len(websocket.sent)
        with pytest.raises(IQOptionExternalError, match="IQOPTION_REAL_ACCOUNT_FORBIDDEN"):
            session.request(
                "buy",
                {"active": "EURUSD", "direction": "call", "price": "1.00", "duration": 1},
            )
        assert len(websocket.sent) == sent_before
    finally:
        session.close()


def test_iqoption_real_worker_has_no_financial_capability_or_order_route() -> None:
    session = cast(
        IQOptionCommunityReadOnlySession,
        object(),
    )
    server = IQOptionReadOnlyWorkerServer(
        "127.0.0.1",
        0,
        1,
        session,
        connection_mode="REAL_AUTH_READ_ONLY",
    )
    request = Envelope(
        protocol_version=1,
        message_id=str(uuid4()),
        correlation_id=str(uuid4()),
        causation_id=None,
        source=EndpointRole.CORE,
        target=EndpointRole.IQOPTION_WORKER,
        message_type=MessageType.ORDER_SUBMIT,
        created_at_utc=datetime.now(UTC),
        deadline_at=None,
        payload={},
    )

    message_type, payload = server._dispatch(request)

    assert server._capabilities.can_submit_orders is False
    assert server._capabilities.supports_order_status_query is False
    assert message_type is MessageType.ERROR
    assert payload == {"reason_code": "WORKER_CAPABILITY_DENIED"}


def test_iqoption_worker_connects_to_core_listener_before_handshake() -> None:
    """The worker must dial the supervisor; the two sides must not both listen."""

    class DummySession:
        is_connected = False

        def close(self) -> None:
            return None

    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = int(listener.getsockname()[1])
    server = IQOptionReadOnlyWorkerServer(
        "127.0.0.1",
        port,
        1,
        cast(IQOptionCommunityReadOnlySession, DummySession()),
        connection_mode="DEMO_AUTH_READ_ONLY",
    )
    result: list[int] = []
    thread = threading.Thread(target=lambda: result.append(server.run()))
    thread.start()
    listener.settimeout(2.0)
    connection, _ = listener.accept()
    client = SocketWorkerClient.handshake(
        FramedSocket(connection),
        timeout_seconds=2.0,
        expected_worker_role=EndpointRole.IQOPTION_WORKER,
        expected_broker="IQOPTION",
    )
    try:
        assert client.capabilities.can_submit_orders is True
    finally:
        client.shutdown(1.0)
        listener.close()
        thread.join(timeout=2.0)
    assert result == [0]


def test_worker_never_repeats_http_login_after_first_connection_attempt() -> None:
    class SessionSpy:
        is_connected = False

        def __init__(self) -> None:
            self.connect_calls = 0
            self.reconnect_calls = 0

        def connect(self) -> None:
            self.connect_calls += 1
            self.is_connected = True

        def reconnect(self, *, timeout: float) -> None:
            assert timeout == 8.0
            self.reconnect_calls += 1
            self.is_connected = True

    session = SessionSpy()
    server = IQOptionReadOnlyWorkerServer(
        "127.0.0.1",
        1,
        1,
        cast(IQOptionCommunityReadOnlySession, session),
        connection_mode="DEMO_AUTH_FINANCIAL",
    )

    server._ensure_connected()
    session.is_connected = False
    server._ensure_connected()

    assert session.connect_calls == 1
    assert session.reconnect_calls == 1
