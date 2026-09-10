"""Minimal unofficial IQ Option authenticated connection.

The observed authentication and WebSocket flow is derived from the MIT-licensed
``victalejo/iqoptionapi`` read-only client at commit
``acac6e08333466ae188c7dfa7fd2a03174e34ca2``. Financial operations are exposed
only for an explicitly selected Practice balance; Real remains fail-closed.
"""

from __future__ import annotations

import http.client
import json
import queue
import threading
import time
from collections.abc import Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from http.cookies import SimpleCookie
from typing import Any, Protocol
from urllib.parse import urlencode
from uuid import uuid4

from websockets.exceptions import WebSocketException
from websockets.sync.client import connect as websocket_connect

from packages.domain.market import (
    BrokerAccountBalance,
    BrokerClockSnapshot,
    BrokerInstrument,
    BrokerInstrumentAvailability,
    BrokerInstrumentCatalog,
    BrokerInstrumentProduct,
    BrokerMarketKind,
    MarketCandle,
)
from packages.domain.models import Broker
from packages.security import SecretValue

IQOPTION_LOGIN_ROUTES: tuple[tuple[str, str], ...] = (
    # The maintained ``zagmi/iqbroker`` compatibility fork uses the dedicated
    # API host without the historical ``/api`` prefix.  Keep this first because
    # it is independent from the older auth host used by most community forks.
    ("api.iqoption.com", "/v2/login"),
    # The 2026 ``victalejo/iqoptionapi`` client and older forks use the main or
    # dedicated auth hosts.  They remain bounded compatibility fallbacks.
    ("iqoption.com", "/api/login/v2"),
    ("auth.iqoption.com", "/api/v2/login"),
    ("auth.iqoption.com", "/api/v1.0/login"),
)
IQOPTION_WEBSOCKET_URLS: tuple[str, ...] = (
    # ``ws.iqoption.com`` is the dedicated endpoint used by iqbroker and is
    # independently reachable when the main website edge is unavailable.
    "wss://ws.iqoption.com/echo/websocket",
    "wss://iqoption.com/echo/websocket",
)
IQOPTION_WEBSOCKET_RECONNECT_LIMIT = 5
IQOPTION_WEBSOCKET_RECONNECT_WINDOW_SECONDS = 15 * 60
IQOPTION_DIGITAL_CATALOG_RETRY_SECONDS = 15 * 60.0
IQOPTION_CLOCK_MAX_AGE_SECONDS = 30.0
IQOPTION_CLOCK_PROBE_INTERVAL_SECONDS = 10.0
IQOPTION_CLOCK_PROBE_TIMEOUT_SECONDS = 2.0
IQOPTION_CLOCK_REFRESH_TIMEOUT_SECONDS = 2.0

# Legacy bootstrap only. The first successful session catalogue atomically
# replaces this map; it is retained for old fixtures and initial compatibility.
IQOPTION_ACTIVE_IDS: dict[str, int] = {
    "EURUSD": 1,
    "EURJPY": 4,
    "GBPUSD": 5,
    "USDJPY": 6,
    "AUDCAD": 7,
    "NZDUSD": 8,
    "USDCHF": 72,
    "EURUSD-OTC": 76,
    "USDCHF-OTC": 78,
    "EURJPY-OTC": 79,
    "NZDUSD-OTC": 80,
    "GBPUSD-OTC": 81,
    "GBPJPY-OTC": 84,
    "USDJPY-OTC": 85,
    "AUDCAD-OTC": 86,
    "AUDUSD": 99,
    "USDCAD": 100,
}


class IQOptionAccountMode(StrEnum):
    PRACTICE = "practice"
    REAL = "real"

    @property
    def balance_type(self) -> int:
        return 4 if self is IQOptionAccountMode.PRACTICE else 1

    @property
    def domain_account_type(self) -> str:
        return "DEMO" if self is IQOptionAccountMode.PRACTICE else "REAL"


class IQOptionExternalError(RuntimeError):
    def __init__(
        self,
        reason_code: str,
        *,
        submission_not_sent: bool = False,
        details: Mapping[str, object] | None = None,
    ) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code
        self.submission_not_sent = submission_not_sent
        self.details = dict(details or {})


class IQOptionWebSocket(Protocol):
    def ping(self) -> threading.Event: ...

    def send(self, message: str) -> None: ...

    def recv(self, timeout: float | None = None) -> str | bytes: ...

    def close(self) -> None: ...


@dataclass(frozen=True, slots=True)
class IQOptionConnectionSnapshot:
    account_mode: IQOptionAccountMode
    balance: BrokerAccountBalance
    profile_confirmed: bool
    connected: bool


LoginFunction = Callable[[str, SecretValue, float], SecretValue]
WebSocketFactory = Callable[[], IQOptionWebSocket]


def _login(email: str, password: SecretValue, timeout: float) -> SecretValue:
    if timeout <= 0:
        raise ValueError("IQ Option login timeout must be positive")
    body = urlencode({"identifier": email, "password": password.reveal_text()})
    encoded_body = body.encode("utf-8")
    deadline = time.monotonic() + timeout
    network_failures = 0
    unavailable_responses = 0
    for route_index, (host, path) in enumerate(IQOPTION_LOGIN_ROUTES):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            network_failures += 1
            break
        routes_left = len(IQOPTION_LOGIN_ROUTES) - route_index
        route_timeout = max(1.0, min(10.0, remaining / routes_left))
        connection = http.client.HTTPSConnection(host, timeout=route_timeout)
        try:
            connection.request(
                "POST",
                path,
                body=encoded_body,
                headers={
                    "Content-Type": "application/x-www-form-urlencoded",
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/120.0.0.0 Safari/537.36"
                    ),
                    "Accept": "application/json",
                },
            )
            response = connection.getresponse()
            response_body = response.read(65_536)
            if response.status == 429:
                raise IQOptionExternalError("IQOPTION_RATE_LIMITED")
            if response.status in {400, 401, 403}:
                raise IQOptionExternalError("IQOPTION_AUTH_FAILED")
            if response.status != 200:
                unavailable_responses += 1
                continue

            cookies = SimpleCookie()
            for raw_cookie in response.headers.get_all("Set-Cookie", []):
                cookies.load(raw_cookie)
            session_cookie = cookies.get("ssid")
            if session_cookie is not None and session_cookie.value:
                return SecretValue.from_text(session_cookie.value)

            try:
                payload = json.loads(response_body.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                payload = None
            if isinstance(payload, Mapping):
                if payload.get("code") == "verify":
                    raise IQOptionExternalError("IQOPTION_2FA_REQUIRED")
                raw_session = payload.get("ssid")
                data = payload.get("data")
                if raw_session is None and isinstance(data, Mapping):
                    raw_session = data.get("ssid")
                if isinstance(raw_session, str) and raw_session:
                    return SecretValue.from_text(raw_session)
            raise IQOptionExternalError("IQOPTION_AUTH_FAILED")
        except IQOptionExternalError:
            raise
        except (OSError, TimeoutError, http.client.HTTPException):
            network_failures += 1
        finally:
            connection.close()

    if network_failures and not unavailable_responses:
        raise IQOptionExternalError("IQOPTION_NETWORK_UNREACHABLE")
    raise IQOptionExternalError("IQOPTION_LOGIN_UNAVAILABLE")


def _websocket_factory() -> IQOptionWebSocket:
    last_error: Exception | None = None
    for url in IQOPTION_WEBSOCKET_URLS:
        try:
            return websocket_connect(
                url,
                open_timeout=10,
                close_timeout=3,
                ping_interval=20,
                ping_timeout=20,
                max_size=IQOPTION_MAX_MESSAGE_BYTES,
                max_queue=4,
                proxy=True,
            )
        except (OSError, TimeoutError, WebSocketException) as exc:
            last_error = exc
    raise IQOptionExternalError("IQOPTION_WEBSOCKET_UNAVAILABLE") from last_error


# Initialization contains the entire broker catalogue, not one asset. Bound the
# decoded message explicitly (including compressed frames); never disable limits.
IQOPTION_MAX_MESSAGE_BYTES = 8 * 1024 * 1024


class IQOptionCommunityReadOnlySession:
    """Authenticated account session with Practice-only financial operations."""

    def __init__(
        self,
        email: str,
        password: SecretValue,
        account_mode: IQOptionAccountMode,
        *,
        login: LoginFunction = _login,
        websocket_factory: WebSocketFactory = _websocket_factory,
        monotonic: Callable[[], float] = time.monotonic,
        wall_time: Callable[[], float] = time.time,
    ) -> None:
        self._email = email
        self._password = password
        self._account_mode = account_mode
        self._login = login
        self._websocket_factory = websocket_factory
        self._monotonic = monotonic
        self._wall_time = wall_time
        self._lock = threading.Lock()
        self._connect_lock = threading.Lock()
        self._clock_query_lock = threading.Lock()
        self._send_lock = threading.Lock()
        self._pending_lock = threading.Lock()
        self._betinfo_query_lock = threading.Lock()
        self._options_query_lock = threading.Lock()
        self._initialization_query_lock = threading.Lock()
        self._initialization_pending: tuple[str, queue.Queue[dict[str, Any]]] | None = None
        self._underlying_query_lock = threading.Lock()
        self._underlying_pending: tuple[str, queue.Queue[dict[str, Any]]] | None = None
        self._disconnect_reason = "IQOPTION_WEBSOCKET_UNAVAILABLE"
        self._stop = threading.Event()
        self._disconnected = threading.Event()
        self._websocket: IQOptionWebSocket | None = None
        self._reader: threading.Thread | None = None
        # SSID is deliberately process-memory-only.  It is reused after a
        # transport drop and is never serialized, logged or returned to Core.
        self._session_cookie: SecretValue | None = None
        self._websocket_reconnect_epochs: list[float] = []
        self._authenticated = False
        self._profile: dict[str, object] | None = None
        self._balances: list[dict[str, object]] | None = None
        self._server_epoch: Decimal | None = None
        self._server_epoch_received_at = 0.0
        self._server_epoch_monotonic = 0.0
        self._connected_at_monotonic = 0.0
        self._connection_generation = 0
        self._clock_round_trip: float | None = None
        self._clock_probe_at = float("-inf")
        self._clock_updated = threading.Event()
        self._last_rx_monotonic = float("-inf")
        self._pending: dict[str, queue.Queue[dict[str, Any]]] = {}
        self._contract_events: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=256)
        # Legacy api_game_betinfo responses do not reliably echo request_id.
        # Keep one serialized, bounded response lane and validate the exact
        # broker option id before accepting any result.
        self._betinfo_responses: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=4)
        self._options_responses: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=4)
        # Static ids are a bounded bootstrap for the first catalogue request.
        # Once a catalogue is observed, only the broker's current session map
        # is authoritative.
        self._active_ids = dict(IQOPTION_ACTIVE_IDS)
        self._catalog_refreshed = False
        self._catalog_generation = 0
        self._digital_catalog_response: dict[str, Any] | None = None
        self._digital_catalog_retry_after_mono = 0.0

    @property
    def is_connected(self) -> bool:
        return (
            self._authenticated and self._websocket is not None and not self._disconnected.is_set()
        )

    @property
    def disconnect_reason(self) -> str:
        return self._disconnect_reason

    def connect(self, timeout: float = 20.0) -> IQOptionConnectionSnapshot:
        if timeout <= 0:
            raise ValueError("IQ Option connection timeout must be positive")
        with self._connect_lock:
            if self.is_connected:
                return self.snapshot()
            cached_cookie = self._session_cookie
            if cached_cookie is not None:
                self._reserve_websocket_reconnect()
                try:
                    return self._connect_websocket(cached_cookie, timeout)
                except IQOptionExternalError as exc:
                    # Only an explicit broker rejection proves the SSID is no
                    # longer usable.  Network and timeout failures must not
                    # cause a second HTTP login storm.
                    if exc.reason_code != "IQOPTION_AUTH_FAILED":
                        raise
                    self._session_cookie = None

            session_cookie = self._login(self._email, self._password, timeout)
            self._session_cookie = session_cookie
            try:
                return self._connect_websocket(session_cookie, timeout)
            except IQOptionExternalError as exc:
                if exc.reason_code == "IQOPTION_AUTH_FAILED":
                    self._session_cookie = None
                raise

    def reconnect(self, timeout: float = 8.0) -> IQOptionConnectionSnapshot:
        """Recover only with the in-memory SSID; never perform HTTP login."""

        if timeout <= 0:
            raise ValueError("IQ Option reconnection timeout must be positive")
        with self._connect_lock:
            if self.is_connected:
                return self.snapshot()
            session_cookie = self._session_cookie
            if session_cookie is None:
                raise IQOptionExternalError("IQOPTION_AUTH_FAILED")
            self._reserve_websocket_reconnect()
            try:
                return self._connect_websocket(session_cookie, timeout)
            except IQOptionExternalError as exc:
                if exc.reason_code == "IQOPTION_AUTH_FAILED":
                    self._session_cookie = None
                raise

    def _connect_websocket(
        self,
        session_cookie: SecretValue,
        timeout: float,
    ) -> IQOptionConnectionSnapshot:
        self._close_transport(clear_session=False)
        self._connection_generation += 1
        with self._lock:
            self._authenticated = False
            self._profile = None
            self._balances = None
            self._server_epoch = None
            self._server_epoch_received_at = 0.0
            self._server_epoch_monotonic = 0.0
            self._clock_round_trip = None
            self._clock_probe_at = float("-inf")
            self._clock_updated.clear()
            self._last_rx_monotonic = float("-inf")
            self._active_ids = dict(IQOPTION_ACTIVE_IDS)
            self._catalog_refreshed = False
            self._digital_catalog_response = None
            self._digital_catalog_retry_after_mono = 0.0
        websocket = self._websocket_factory()
        self._websocket = websocket
        self._stop.clear()
        self._disconnected.clear()
        self._disconnect_reason = "IQOPTION_WEBSOCKET_UNAVAILABLE"
        try:
            self._send(
                {
                    "name": "authenticate",
                    "msg": {"ssid": session_cookie.reveal_text(), "protocol": 3},
                }
            )
            # HTTP login and WebSocket authentication are independent broker
            # phases. A slow HTTP response must not consume the confirmation
            # window for authenticated/profile/balance frames.
            deadline = self._monotonic() + timeout
            account_snapshot_requested = False
            while self._monotonic() < deadline:
                remaining = max(0.05, deadline - self._monotonic())
                try:
                    raw = websocket.recv(timeout=remaining)
                except TimeoutError as exc:
                    raise IQOptionExternalError("IQOPTION_AUTH_TIMEOUT") from exc
                self._handle_message(raw)
                with self._lock:
                    authenticated = self._authenticated
                    ready = (
                        authenticated and self._profile is not None and self._balances is not None
                    )
                if authenticated and not account_snapshot_requested:
                    self._request_account_snapshot()
                    account_snapshot_requested = True
                if ready:
                    break
            else:
                raise IQOptionExternalError("IQOPTION_AUTH_TIMEOUT")
            self._selected_balance()
        except Exception:
            self._close_transport(clear_session=False)
            raise

        self._connected_at_monotonic = self._monotonic()
        self._reader = threading.Thread(
            target=self._reader_loop,
            name="iqoption-read-only-receiver",
            daemon=True,
        )
        self._reader.start()
        return self.snapshot()

    def snapshot(self) -> IQOptionConnectionSnapshot:
        return IQOptionConnectionSnapshot(
            account_mode=self._account_mode,
            balance=self.get_balance(),
            profile_confirmed=self._profile is not None,
            connected=self.is_connected,
        )

    def get_balance(self) -> BrokerAccountBalance:
        raw = self._selected_balance()
        currency = raw.get("currency") or raw.get("currency_code")
        if not isinstance(currency, str):
            raise IQOptionExternalError("IQOPTION_BALANCE_INVALID")
        try:
            amount = Decimal(str(raw.get("amount")))
        except (InvalidOperation, ValueError) as exc:
            raise IQOptionExternalError("IQOPTION_BALANCE_INVALID") from exc
        if not amount.is_finite():
            raise IQOptionExternalError("IQOPTION_BALANCE_INVALID")
        minor_units = amount * Decimal(100)
        if minor_units != minor_units.to_integral_value():
            raise IQOptionExternalError("IQOPTION_BALANCE_PRECISION_UNSUPPORTED")
        return BrokerAccountBalance(
            balance_minor_units=int(minor_units),
            currency=currency,
            account_type=self._account_mode.domain_account_type,
            observed_at_utc=datetime.now(UTC),
        )

    def get_clock(self) -> BrokerClockSnapshot:
        """Fresh broker push time + bounded, correlated WebSocket Ping/Pong RTT.

        Connection setup is not an RTT measurement. A Pong proves transport
        latency, not server time: both it and a fresh broker timestamp are required.
        Cache only the probe for ten seconds; never substitute the PC's clock.
        """
        started = self._monotonic()
        with self._clock_query_lock:
            try:
                return self._get_clock_locked()
            except IQOptionExternalError as exc:
                exc.details.update(self._clock_diagnostics("BROKER_CLOCK_REQUEST", started))
                raise

    def _get_clock_locked(self) -> BrokerClockSnapshot:
        websocket = self._websocket
        if websocket is None or not self.is_connected:
            raise IQOptionExternalError("IQOPTION_WEBSOCKET_UNAVAILABLE")
        now = self._monotonic()
        if now - self._clock_probe_at >= IQOPTION_CLOCK_PROBE_INTERVAL_SECONDS:
            self._clock_probe_at = now
            probe_failed = False
            try:
                pong = websocket.ping()
                if not pong.wait(IQOPTION_CLOCK_PROBE_TIMEOUT_SECONDS):
                    probe_failed = True
            except (WebSocketException, OSError, RuntimeError) as exc:
                if not self.is_connected:
                    raise IQOptionExternalError("IQOPTION_WEBSOCKET_UNAVAILABLE") from exc
                probe_failed = True
            observed_round_trip = max(0.0, self._monotonic() - now)
            if not probe_failed or self._clock_round_trip is None:
                self._clock_round_trip = observed_round_trip
        if websocket is not self._websocket or not self.is_connected:
            raise IQOptionExternalError("IQOPTION_WEBSOCKET_UNAVAILABLE")
        invalid_reason = self._clock_invalid_reason()
        if invalid_reason is not None:
            # timeSync is an evidence refresh on the existing authenticated
            # socket. It never performs login, reconnect, or order submission.
            self._clock_updated.clear()
            self._send({"name": "timesync", "msg": int(self._wall_time() * 1000)})
            self._clock_updated.wait(IQOPTION_CLOCK_REFRESH_TIMEOUT_SECONDS)
            invalid_reason = self._clock_invalid_reason()
            if invalid_reason is not None:
                raise IQOptionExternalError(invalid_reason)
        if self._clock_round_trip is None:
            raise IQOptionExternalError("IQOPTION_CLOCK_PONG_TIMEOUT")
        with self._lock:
            server_epoch = self._server_epoch
            received_mono = self._server_epoch_monotonic
        now_mono = self._monotonic()
        now_wall = self._wall_time()
        elapsed = now_mono - received_mono
        if server_epoch is None:
            raise IQOptionExternalError("IQOPTION_CLOCK_NO_SAMPLE")
        projected_epoch = server_epoch + Decimal(str(elapsed))
        estimated_offset = projected_epoch - Decimal(str(now_wall))
        return BrokerClockSnapshot(
            server_epoch=int(projected_epoch),
            local_received_at=datetime.fromtimestamp(now_wall, UTC),
            round_trip_seconds=self._clock_round_trip,
            estimated_offset_seconds=estimated_offset,
        )

    def _clock_invalid_reason(self) -> str | None:
        with self._lock:
            server_epoch = self._server_epoch
            received_at = self._server_epoch_received_at
            received_mono = self._server_epoch_monotonic
        if server_epoch is None:
            return "IQOPTION_CLOCK_NO_SAMPLE"
        elapsed = self._monotonic() - received_mono
        if not 0 <= elapsed <= IQOPTION_CLOCK_MAX_AGE_SECONDS:
            return "IQOPTION_CLOCK_STALE"
        if abs((self._wall_time() - received_at) - elapsed) > 1.0:
            return "IQOPTION_CLOCK_WALL_JUMP"
        return None

    def _clock_diagnostics(self, operation: str, started: float) -> dict[str, object]:
        now = self._monotonic()
        with self._lock:
            sample_mono = self._server_epoch_monotonic
            has_sample = self._server_epoch is not None
        return {
            "operation": operation,
            "duration_ms": max(0, int((now - started) * 1000)),
            "sample_age_ms": max(0, int((now - sample_mono) * 1000)) if has_sample else None,
            "last_message_age_ms": (
                max(0, int((now - self._last_rx_monotonic) * 1000))
                if self._last_rx_monotonic != float("-inf")
                else None
            ),
            "connection_generation": self._connection_generation,
        }

    def get_candles(
        self,
        symbol: str,
        *,
        timeframe_seconds: int = 60,
        count: int = 20,
        end_epoch: int | None = None,
        timeout: float = 5.0,
    ) -> tuple[MarketCandle, ...]:
        """Fetch closed broker candles through the authenticated WebSocket."""

        if self._account_mode is not IQOptionAccountMode.PRACTICE:
            raise IQOptionExternalError("IQOPTION_REAL_MARKET_EXECUTION_FORBIDDEN")
        if timeframe_seconds not in {60, 300, 600, 900}:
            raise IQOptionExternalError("IQOPTION_TIMEFRAME_UNSUPPORTED")
        # History sizing belongs to the active strategy's warm-up contract.
        # Stateless families legitimately need fewer than the legacy RSI's 15.
        if type(count) is not int or not 1 <= count <= 200:
            raise IQOptionExternalError("IQOPTION_CANDLE_COUNT_INVALID")
        active_id = self._active_id(symbol)
        clock_epoch = end_epoch or self.get_clock().server_epoch
        response = self._request_message(
            {
                "name": "sendMessage",
                "msg": {
                    "name": "get-candles",
                    "version": "2.0",
                    "body": {
                        "active_id": active_id,
                        "size": timeframe_seconds,
                        "to": int(clock_epoch),
                        "count": count,
                    },
                },
            },
            expected_names=frozenset({"candles"}),
            timeout=timeout,
        )
        raw_msg = response.get("msg")
        raw_candles = raw_msg.get("candles") if isinstance(raw_msg, Mapping) else None
        if not isinstance(raw_candles, list):
            raise IQOptionExternalError("IQOPTION_CANDLES_INVALID")
        parsed: list[MarketCandle] = []
        for raw in raw_candles:
            if not isinstance(raw, Mapping):
                raise IQOptionExternalError("IQOPTION_CANDLES_INVALID")
            try:
                open_epoch = int(raw["from"])
                close_epoch = int(raw.get("to", open_epoch + timeframe_seconds))
                raw_volume = raw.get("volume")
                tick_volume: int | None = None
                if raw_volume is not None:
                    if isinstance(raw_volume, bool):
                        raise ValueError("invalid candle volume")
                    decimal_volume = Decimal(str(raw_volume))
                    if (
                        not decimal_volume.is_finite()
                        or decimal_volume < 0
                        or decimal_volume != decimal_volume.to_integral_value()
                    ):
                        raise ValueError("invalid candle volume")
                    tick_volume = int(decimal_volume)
                parsed.append(
                    MarketCandle(
                        broker=Broker.IQ_OPTION,
                        broker_symbol=symbol,
                        timeframe_seconds=timeframe_seconds,
                        open_time=datetime.fromtimestamp(open_epoch, tz=UTC),
                        close_time=datetime.fromtimestamp(close_epoch, tz=UTC),
                        open=Decimal(str(raw["open"])),
                        high=Decimal(str(raw.get("max", raw.get("high")))),
                        low=Decimal(str(raw.get("min", raw.get("low")))),
                        close=Decimal(str(raw["close"])),
                        is_closed=close_epoch <= int(clock_epoch),
                        tick_volume=tick_volume,
                    )
                )
            except (KeyError, TypeError, ValueError, InvalidOperation) as exc:
                raise IQOptionExternalError("IQOPTION_CANDLES_INVALID") from exc
        return tuple(candle for candle in parsed if candle.is_closed)

    def get_binary_payout(
        self, symbol: str, *, duration_minutes: int = 1, timeout: float = 2.0
    ) -> Decimal:
        """Read current turbo commission; no subscription, login or financial retry.

        Community API get-initialization-data v3, turbo.option.profit.commission.
        W = (100 - commission) / 100. Never substitute a historic settlement.
        """
        if self._account_mode is not IQOptionAccountMode.PRACTICE:
            raise IQOptionExternalError("IQOPTION_REAL_ACCOUNT_FORBIDDEN")
        if duration_minutes != 1:
            raise IQOptionExternalError("IQOPTION_OPERATION_UNSUPPORTED")
        response = self._request_initialization(timeout)
        try:
            actives = response["msg"]["turbo"]["actives"]
            if not isinstance(actives, Mapping):
                raise ValueError("invalid catalogue")
            matches = [
                (str(active_id), active)
                for active_id, active in actives.items()
                if isinstance(active, Mapping)
                and self._catalog_symbol(active.get("name")) == symbol.upper()
            ]
            if not matches:
                with self._lock:
                    previous_id = self._active_ids.get(symbol.upper())
                if previous_id is not None and str(previous_id) in actives:
                    raise ValueError("catalogue id no longer matches exact asset")
                raise IQOptionExternalError("IQOPTION_ACTIVE_UNAVAILABLE")
            if len(matches) != 1:
                raise ValueError("ambiguous exact asset")
            raw_active_id, active = matches[0]
            active_id = int(raw_active_id)
            with self._lock:
                self._active_ids[symbol.upper()] = active_id
            if active["enabled"] is False:
                raise IQOptionExternalError("IQOPTION_ACTIVE_UNAVAILABLE")
            if active["is_suspended"] is True:
                raise IQOptionExternalError("IQOPTION_ACTIVE_SUSPENDED")
            if active["enabled"] is not True or active["is_suspended"] is not False:
                raise ValueError("invalid availability flags")
            commission = Decimal(str(active["option"]["profit"]["commission"]))
            if not commission.is_finite() or not 0 <= commission < 100:
                raise ValueError("invalid commission")
            return (Decimal(100) - commission) / Decimal(100)
        except (KeyError, TypeError, ValueError, InvalidOperation, AttributeError) as exc:
            raise IQOptionExternalError("IQOPTION_PAYOUT_UNAVAILABLE") from exc

    def get_instrument_catalog(self, *, timeout: float = 8.0) -> BrokerInstrumentCatalog:
        """Discover Binary/Turbo/Digital instruments from the current session.

        The two broker catalogue routes are read-only. Binary/Turbo evidence is
        mandatory; Digital is retained as detection-only because the financial
        route implemented by this worker is Binary/Turbo-specific.
        """

        if timeout <= 0:
            raise ValueError("IQ Option catalogue timeout must be positive")
        initialization = self._request_initialization(timeout)
        instruments = self._parse_binary_instruments(initialization)
        unavailable_products: list[BrokerInstrumentProduct] = []
        with self._lock:
            underlying = self._digital_catalog_response
            digital_retry_after = self._digital_catalog_retry_after_mono
        if underlying is None and self._monotonic() >= digital_retry_after:
            try:
                # Each independent broker route receives its own bounded
                # response window. Sharing one deadline could falsely report
                # Digital unavailable after a slow initialization-data reply.
                underlying = self._request_underlying(timeout)
                with self._lock:
                    self._digital_catalog_response = underlying
                    self._digital_catalog_retry_after_mono = 0.0
            except IQOptionExternalError as exc:
                if exc.reason_code not in {
                    "IQOPTION_REQUEST_TIMEOUT",
                    "IQOPTION_CATALOG_INVALID",
                    "IQOPTION_RESPONSE_UNEXPECTED",
                }:
                    raise
                # A missing legacy route is product-scoped. Back off retries so
                # its full timeout cannot stall M1 candle/payout traffic once
                # per minute. A new authenticated connection resets this state.
                with self._lock:
                    self._digital_catalog_retry_after_mono = (
                        self._monotonic() + IQOPTION_DIGITAL_CATALOG_RETRY_SECONDS
                    )
        if underlying is not None:
            instruments.extend(self._parse_digital_instruments(underlying))
        else:
            unavailable_products.append(BrokerInstrumentProduct.DIGITAL)
        instruments.sort(key=lambda item: (item.broker_symbol, item.product.value, item.broker_id))
        active_ids: dict[str, int] = {}
        for item in instruments:
            if item.product is not BrokerInstrumentProduct.TURBO:
                continue
            try:
                active_ids[item.broker_symbol] = int(item.broker_id)
            except ValueError:
                continue
        with self._lock:
            self._active_ids = active_ids
            self._catalog_refreshed = True
            self._catalog_generation += 1
            generation = self._catalog_generation
        return BrokerInstrumentCatalog(
            generation=generation,
            observed_at_utc=datetime.now(UTC),
            instruments=tuple(instruments),
            unavailable_products=tuple(unavailable_products),
        )

    def _parse_binary_instruments(self, response: Mapping[str, object]) -> list[BrokerInstrument]:
        try:
            msg = response["msg"]
            if not isinstance(msg, Mapping):
                raise ValueError("invalid initialization payload")
        except KeyError as exc:
            raise IQOptionExternalError("IQOPTION_CATALOG_INVALID") from exc
        parsed: list[BrokerInstrument] = []
        for section, product in (
            ("turbo", BrokerInstrumentProduct.TURBO),
            ("binary", BrokerInstrumentProduct.BINARY),
        ):
            raw_section = msg.get(section)
            actives = raw_section.get("actives") if isinstance(raw_section, Mapping) else None
            if actives is None:
                continue
            if not isinstance(actives, Mapping):
                raise IQOptionExternalError("IQOPTION_CATALOG_INVALID")
            for raw_id, raw_active in actives.items():
                if not isinstance(raw_active, Mapping):
                    raise IQOptionExternalError("IQOPTION_CATALOG_INVALID")
                symbol = self._catalog_symbol(raw_active.get("name"))
                if symbol is None:
                    raise IQOptionExternalError("IQOPTION_CATALOG_INVALID")
                enabled = raw_active.get("enabled")
                suspended = raw_active.get("is_suspended")
                if type(enabled) is not bool or type(suspended) is not bool:
                    availability = BrokerInstrumentAvailability.UNKNOWN
                elif not enabled:
                    availability = BrokerInstrumentAvailability.DISABLED
                elif suspended:
                    availability = BrokerInstrumentAvailability.SUSPENDED
                else:
                    availability = BrokerInstrumentAvailability.OPEN
                turbo = product is BrokerInstrumentProduct.TURBO
                executable = (
                    turbo
                    and availability is BrokerInstrumentAvailability.OPEN
                    and self._account_mode is IQOptionAccountMode.PRACTICE
                )
                parsed.append(
                    BrokerInstrument(
                        broker=Broker.IQ_OPTION,
                        broker_id=str(raw_id),
                        broker_symbol=symbol,
                        display_name=self._display_name(symbol),
                        product=product,
                        market_kind=self._market_kind(symbol),
                        availability=availability,
                        duration_seconds=(60,) if turbo else (),
                        detectable=True,
                        analyzable=turbo,
                        quotable=executable,
                        executable=executable,
                    )
                )
        if not parsed:
            raise IQOptionExternalError("IQOPTION_CATALOG_INVALID")
        return parsed

    def _parse_digital_instruments(self, response: Mapping[str, object]) -> list[BrokerInstrument]:
        msg = response.get("msg")
        if not isinstance(msg, Mapping):
            raise IQOptionExternalError("IQOPTION_CATALOG_INVALID")
        raw_underlying = msg.get("underlying")
        if not isinstance(raw_underlying, list):
            raise IQOptionExternalError("IQOPTION_CATALOG_INVALID")
        now = self.get_clock().server_epoch
        parsed: list[BrokerInstrument] = []
        for raw in raw_underlying:
            if not isinstance(raw, Mapping):
                raise IQOptionExternalError("IQOPTION_CATALOG_INVALID")
            symbol = self._catalog_symbol(raw.get("underlying"))
            if symbol is None:
                raise IQOptionExternalError("IQOPTION_CATALOG_INVALID")
            schedule = raw.get("schedule")
            is_open = False
            schedule_valid = isinstance(schedule, list)
            if isinstance(schedule, list):
                for interval in schedule:
                    if not isinstance(interval, Mapping):
                        schedule_valid = False
                        break
                    opened = interval.get("open")
                    closed = interval.get("close")
                    if (
                        isinstance(opened, (int, Decimal))
                        and not isinstance(opened, bool)
                        and isinstance(closed, (int, Decimal))
                        and not isinstance(closed, bool)
                        and int(opened) <= now < int(closed)
                    ):
                        is_open = True
            availability = (
                BrokerInstrumentAvailability.OPEN
                if schedule_valid and is_open
                else BrokerInstrumentAvailability.CLOSED
                if schedule_valid
                else BrokerInstrumentAvailability.UNKNOWN
            )
            raw_id = raw.get("active_id", raw.get("id", symbol))
            parsed.append(
                BrokerInstrument(
                    broker=Broker.IQ_OPTION,
                    broker_id=str(raw_id),
                    broker_symbol=symbol,
                    display_name=self._display_name(symbol),
                    product=BrokerInstrumentProduct.DIGITAL,
                    market_kind=self._market_kind(symbol),
                    availability=availability,
                    duration_seconds=(),
                    detectable=True,
                    analyzable=False,
                    quotable=False,
                    executable=False,
                )
            )
        return parsed

    @staticmethod
    def _catalog_symbol(raw_name: object) -> str | None:
        if not isinstance(raw_name, str) or not raw_name.strip():
            return None
        return raw_name.rsplit(".", 1)[-1].strip().upper()

    @staticmethod
    def _market_kind(symbol: str) -> BrokerMarketKind:
        return BrokerMarketKind.OTC if symbol.endswith("-OTC") else BrokerMarketKind.REGULAR

    @staticmethod
    def _display_name(symbol: str) -> str:
        suffix = " OTC" if symbol.endswith("-OTC") else ""
        stem = symbol.removesuffix("-OTC")
        if len(stem) == 6 and stem.isalpha():
            return f"{stem[:3]}/{stem[3:]}{suffix}"
        return f"{stem}{suffix}"

    def request(
        self,
        name: str,
        msg: Mapping[str, Any],
        *,
        timeout: float = 5.0,
    ) -> dict[str, Any]:
        """Practice transport used by the isolated order/reconciliation sessions."""

        if self._account_mode is not IQOptionAccountMode.PRACTICE:
            raise IQOptionExternalError("IQOPTION_REAL_ACCOUNT_FORBIDDEN")
        if name == "buy":
            return self._buy_binary_option(msg, timeout=timeout)
        if name == "get_betinfo":
            return self._get_betinfo(msg, timeout=timeout)
        if name == "get_options":
            return self._get_options(msg, timeout=timeout)
        raise IQOptionExternalError("IQOPTION_OPERATION_UNSUPPORTED")

    def receive_contract(self, *, timeout: float = 0.1) -> dict[str, Any] | None:
        if timeout <= 0:
            raise ValueError("receive timeout must be positive")
        try:
            return self._contract_events.get(timeout=timeout)
        except queue.Empty:
            return None

    def close(self) -> None:
        self._close_transport(clear_session=True)

    def _close_transport(self, *, clear_session: bool) -> None:
        self._stop.set()
        websocket = self._websocket
        self._websocket = None
        if websocket is not None:
            with suppress(OSError, RuntimeError):
                websocket.close()
        reader = self._reader
        self._reader = None
        if reader is not None and reader is not threading.current_thread():
            reader.join(timeout=1.0)
        self._authenticated = False
        self._disconnected.set()
        if clear_session:
            self._session_cookie = None
            self._websocket_reconnect_epochs.clear()
        with self._pending_lock:
            self._pending.clear()

    def _reserve_websocket_reconnect(self) -> None:
        now = self._monotonic()
        boundary = now - IQOPTION_WEBSOCKET_RECONNECT_WINDOW_SECONDS
        self._websocket_reconnect_epochs = [
            item for item in self._websocket_reconnect_epochs if item > boundary
        ]
        if len(self._websocket_reconnect_epochs) >= IQOPTION_WEBSOCKET_RECONNECT_LIMIT:
            retry_after = max(
                0.0,
                self._websocket_reconnect_epochs[0]
                + IQOPTION_WEBSOCKET_RECONNECT_WINDOW_SECONDS
                - now,
            )
            raise IQOptionExternalError(
                "IQOPTION_WEBSOCKET_RECONNECT_LIMIT_REACHED",
                details={"retry_after_seconds": retry_after},
            )
        self._websocket_reconnect_epochs.append(now)

    def _selected_balance(self) -> dict[str, object]:
        with self._lock:
            balances = tuple(self._balances or ())
        for balance in balances:
            if balance.get("type") == self._account_mode.balance_type:
                return balance
        raise IQOptionExternalError("IQOPTION_ACCOUNT_MODE_UNAVAILABLE")

    def _reader_loop(self) -> None:
        websocket = self._websocket
        if websocket is None:
            return
        try:
            while not self._stop.is_set() and not self._disconnected.is_set():
                try:
                    raw = websocket.recv(timeout=1.0)
                except TimeoutError:
                    continue
                self._handle_message(raw)
        except (WebSocketException, OSError, RuntimeError, IQOptionExternalError) as exc:
            reason = "IQOPTION_WEBSOCKET_UNAVAILABLE"
            if isinstance(exc, IQOptionExternalError):
                reason = exc.reason_code
            elif any(
                getattr(getattr(exc, side, None), "code", None) == 1009 for side in ("sent", "rcvd")
            ):
                reason = "IQOPTION_RESPONSE_TOO_LARGE"
            self._fail_transport(reason)

    def _fail_transport(self, reason: str) -> None:
        self._disconnect_reason = reason
        self._disconnected.set()
        # Wake read-only waiters with a sanitised cause, without retrying a buy.
        with self._pending_lock:
            waiters = list(self._pending.values())
            if self._initialization_pending is not None:
                waiters.append(self._initialization_pending[1])
            if self._underlying_pending is not None:
                waiters.append(self._underlying_pending[1])
        for waiter in waiters:
            with suppress(queue.Full):
                waiter.put_nowait({"_transport_error": reason})

    def _handle_message(self, raw: str | bytes) -> None:
        self._last_rx_monotonic = self._monotonic()
        if len(raw if isinstance(raw, bytes) else raw.encode("utf-8")) > IQOPTION_MAX_MESSAGE_BYTES:
            raise IQOptionExternalError("IQOPTION_RESPONSE_TOO_LARGE")
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", errors="replace")
        try:
            message = json.loads(raw, parse_float=Decimal)
        except json.JSONDecodeError:
            return
        if not isinstance(message, Mapping):
            return
        name = message.get("name")
        if name == "initialization-data":
            # This legacy response may have no request_id. It gets a dedicated
            # single-flight lane, never the financial request correlator.
            with self._pending_lock:
                pending = self._initialization_pending
            if pending is not None and message.get("request_id") in (None, "", pending[0]):
                with suppress(queue.Full):
                    pending[1].put_nowait(dict(message))
            return
        if name == "underlying-list":
            # Same legacy behaviour as initialization-data: some compatible
            # servers omit request_id, so isolate it in its own single-flight lane.
            with self._pending_lock:
                pending = self._underlying_pending
            if pending is not None and message.get("request_id") in (None, "", pending[0]):
                with suppress(queue.Full):
                    pending[1].put_nowait(dict(message))
            return
        self._route_pending(message)
        if name == "api_game_betinfo_result":
            try:
                self._betinfo_responses.put_nowait(dict(message))
            except queue.Full:
                self._disconnected.set()
            return
        if name == "options":
            try:
                self._options_responses.put_nowait(dict(message))
            except queue.Full:
                self._disconnected.set()
            return
        if name == "authenticated":
            authenticated = bool(message.get("msg"))
            if not authenticated:
                raise IQOptionExternalError("IQOPTION_AUTH_FAILED")
            self._authenticated = True
            return
        if name == "heartbeat":
            self._send({"name": "heartbeat", "msg": message.get("msg")})
            self._record_server_time(message.get("msg"))
            return
        if name == "profile":
            profile = message.get("msg")
            if isinstance(profile, Mapping) and profile:
                with self._lock:
                    self._profile = dict(profile)
            return
        if name == "balances":
            balances = message.get("msg")
            if isinstance(balances, list):
                parsed = [dict(item) for item in balances if isinstance(item, Mapping)]
                with self._lock:
                    self._balances = parsed
            return
        if name in {"timeSync", "timesync"}:
            raw_epoch = message.get("msg")
            if isinstance(raw_epoch, Mapping):
                raw_epoch = raw_epoch.get("server_time") or raw_epoch.get("time")
            self._record_server_time(raw_epoch)
            return
        if name in {"option-opened", "option-closed"}:
            normalized = self._normalize_contract_event(name, message.get("msg"))
            if normalized is not None:
                try:
                    self._contract_events.put_nowait({"name": name, "msg": normalized})
                except queue.Full:
                    self._disconnected.set()

    def _record_server_time(self, raw_epoch: object) -> None:
        if isinstance(raw_epoch, bool) or not isinstance(raw_epoch, (int, float, Decimal, str)):
            return
        try:
            epoch = Decimal(str(raw_epoch).strip())
        except InvalidOperation:
            return
        if not epoch.is_finite() or epoch <= 0:
            return
        if epoch > 100_000_000_000:
            epoch /= 1_000
        with self._lock:
            self._server_epoch = epoch
            self._server_epoch_received_at = self._wall_time()
            self._server_epoch_monotonic = self._monotonic()
        self._clock_updated.set()

    def _send(self, payload: Mapping[str, object]) -> None:
        websocket = self._websocket
        if websocket is None:
            raise IQOptionExternalError("IQOPTION_WEBSOCKET_UNAVAILABLE")
        encoded = self._json_with_decimal_numbers(payload)
        try:
            with self._send_lock:
                websocket.send(encoded)
        except (WebSocketException, OSError, RuntimeError) as exc:
            self._disconnected.set()
            raise IQOptionExternalError("IQOPTION_WEBSOCKET_UNAVAILABLE") from exc

    def _request_account_snapshot(self) -> None:
        self._send(
            {
                "name": "sendMessage",
                "msg": {"name": "get-profile", "version": "1.0", "body": {}},
                "request_id": "iqoption-read-profile",
            }
        )
        self._send(
            {
                "name": "sendMessage",
                "msg": {"name": "get-balances", "version": "1.0", "body": {}},
                "request_id": "iqoption-read-balances",
            }
        )
        with suppress(Exception):
            self._send({"name": "timesync", "msg": int(self._wall_time() * 1000)})

    def _request_initialization(self, timeout: float) -> dict[str, Any]:
        if timeout <= 0:
            raise ValueError("IQ Option request timeout must be positive")
        # Waiting for the lane consumes the same deadline, not an extra timeout.
        started = self._monotonic()
        if not self._initialization_query_lock.acquire(timeout=timeout):
            raise IQOptionExternalError("IQOPTION_REQUEST_TIMEOUT")
        try:
            if not self.is_connected:
                raise IQOptionExternalError(self._disconnect_reason)
            request_id = f"tl-{uuid4()}"
            responses: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=1)
            with self._pending_lock:
                self._initialization_pending = (request_id, responses)
            self._send(
                {
                    "name": "sendMessage",
                    "request_id": request_id,
                    "msg": {"name": "get-initialization-data", "version": "3.0", "body": {}},
                }
            )
            try:
                response = responses.get(timeout=max(0.0, timeout - (self._monotonic() - started)))
            except queue.Empty as exc:
                # Without a wire ID a late reply cannot be distinguished from a
                # later request. Retire this generation; lifecycle owns recovery.
                self._fail_transport("IQOPTION_WEBSOCKET_UNAVAILABLE")
                raise IQOptionExternalError("IQOPTION_REQUEST_TIMEOUT") from exc
            if "_transport_error" in response:
                raise IQOptionExternalError(response["_transport_error"])
            if not self.is_connected:
                raise IQOptionExternalError(self._disconnect_reason)
            return response
        finally:
            with self._pending_lock:
                self._initialization_pending = None
            self._initialization_query_lock.release()

    def _request_underlying(self, timeout: float) -> dict[str, Any]:
        if timeout <= 0:
            raise ValueError("IQ Option request timeout must be positive")
        started = self._monotonic()
        if not self._underlying_query_lock.acquire(timeout=timeout):
            raise IQOptionExternalError("IQOPTION_REQUEST_TIMEOUT")
        try:
            if not self.is_connected:
                raise IQOptionExternalError(self._disconnect_reason)
            responses: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=1)
            with self._pending_lock:
                # This legacy endpoint is global in the community protocol and
                # current IQ Option sessions may ignore a request carrying an
                # application-generated request_id.  The dedicated lock makes
                # an uncorrelated request safe: there can be only one waiter.
                self._underlying_pending = ("", responses)
            self._send(
                {
                    "name": "sendMessage",
                    "msg": {
                        "name": "get-underlying-list",
                        "version": "2.0",
                        "body": {"type": "digital-option"},
                    },
                }
            )
            try:
                response = responses.get(timeout=max(0.0, timeout - (self._monotonic() - started)))
            except queue.Empty as exc:
                raise IQOptionExternalError("IQOPTION_REQUEST_TIMEOUT") from exc
            if "_transport_error" in response:
                raise IQOptionExternalError(response["_transport_error"])
            return response
        finally:
            with self._pending_lock:
                self._underlying_pending = None
            self._underlying_query_lock.release()

    def _request_message(
        self,
        payload: Mapping[str, object],
        *,
        expected_names: frozenset[str],
        timeout: float,
    ) -> dict[str, Any]:
        if timeout <= 0:
            raise ValueError("IQ Option request timeout must be positive")
        if not self.is_connected:
            raise IQOptionExternalError("IQOPTION_WEBSOCKET_UNAVAILABLE")
        request_id = f"tl-{uuid4()}"
        response_queue: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=1)
        with self._pending_lock:
            self._pending[request_id] = response_queue
        try:
            self._send({**payload, "request_id": request_id})
            try:
                response = response_queue.get(timeout=timeout)
            except queue.Empty as exc:
                raise IQOptionExternalError("IQOPTION_REQUEST_TIMEOUT") from exc
        finally:
            with self._pending_lock:
                self._pending.pop(request_id, None)
        if str(response.get("name")) not in expected_names:
            if "_transport_error" in response:
                raise IQOptionExternalError(response["_transport_error"])
            raise IQOptionExternalError("IQOPTION_RESPONSE_UNEXPECTED")
        return response

    def _route_pending(self, message: Mapping[str, object]) -> None:
        raw_request_id = message.get("request_id")
        if not isinstance(raw_request_id, str) or not raw_request_id:
            return
        with self._pending_lock:
            response_queue = self._pending.get(raw_request_id)
        if response_queue is None:
            return
        try:
            response_queue.put_nowait(dict(message))
        except queue.Full:
            return

    def _buy_binary_option(
        self,
        msg: Mapping[str, Any],
        *,
        timeout: float,
    ) -> dict[str, Any]:
        symbol = str(msg.get("active", ""))
        direction = str(msg.get("direction", "")).lower()
        price = Decimal(str(msg.get("price", "0")))
        duration = int(msg.get("duration", 1))
        if direction not in {"call", "put"} or price <= 0 or duration != 1:
            return {"status": False, "reason": "IQOPTION_ORDER_INVALID"}
        try:
            balance = self._selected_balance()
            balance_id = balance.get("id")
            if isinstance(balance_id, bool) or not isinstance(balance_id, int):
                raise IQOptionExternalError("IQOPTION_BALANCE_ID_INVALID")
            expiry = self._binary_expiration(duration)
            active_id = self._active_id(symbol)
        except IQOptionExternalError as exc:
            # This boundary is strictly before _request_message/_send.
            raise IQOptionExternalError(exc.reason_code, submission_not_sent=True) from exc
        response = self._request_message(
            {
                "name": "sendMessage",
                "msg": {
                    "name": "binary-options.open-option",
                    "version": "1.0",
                    "body": {
                        "price": price,
                        "active_id": active_id,
                        "expired": expiry,
                        "direction": direction,
                        "option_type_id": 3,
                        "user_balance_id": balance_id,
                    },
                },
            },
            expected_names=frozenset({"option"}),
            timeout=timeout,
        )
        raw = response.get("msg")
        if not isinstance(raw, Mapping):
            raise IQOptionExternalError("IQOPTION_ORDER_RESPONSE_INVALID")
        broker_id = raw.get("id")
        if broker_id is not None and raw.get("status") is False:
            raise IQOptionExternalError("IQOPTION_ORDER_RESPONSE_INVALID")
        if broker_id is None:
            # A missing id alone is not evidence of rejection after open-option.
            reason = raw.get("message")
            if isinstance(reason, str) and reason.strip() and raw.get("status") is False:
                return {"status": False, "reason": reason}
            raise IQOptionExternalError("IQOPTION_ORDER_RESPONSE_INVALID")
        return {"status": True, "id": str(broker_id), "result": dict(raw)}

    def _get_options(
        self,
        msg: Mapping[str, Any],
        *,
        timeout: float,
    ) -> dict[str, Any]:
        balance_id = self._selected_balance().get("id")
        if isinstance(balance_id, bool) or not isinstance(balance_id, int):
            raise IQOptionExternalError("IQOPTION_BALANCE_ID_INVALID")
        if timeout <= 0:
            raise ValueError("IQ Option request timeout must be positive")
        if not self.is_connected:
            raise IQOptionExternalError("IQOPTION_WEBSOCKET_UNAVAILABLE")
        with self._options_query_lock:
            while True:
                try:
                    self._options_responses.get_nowait()
                except queue.Empty:
                    break
            self._send(
                {
                    "name": "sendMessage",
                    "msg": {
                        "name": "get-options",
                        "body": {
                            "limit": 100,
                            "instrument_type": "binary,turbo",
                            "user_balance_id": balance_id,
                        },
                    },
                    "request_id": f"tl-{uuid4()}",
                }
            )
            try:
                response = self._options_responses.get(timeout=timeout)
            except queue.Empty as exc:
                raise IQOptionExternalError("IQOPTION_REQUEST_TIMEOUT") from exc
        wanted = str(msg.get("id", ""))
        wanted_client_ref = str(msg.get("client_order_id", ""))
        raw = response.get("msg")
        matched = self._find_exact_contract(raw, wanted, wanted_client_ref)
        if matched is not None:
            return {"isSuccessful": True, "result": matched}
        return {"isSuccessful": False, "message": "Option not found"}

    def _get_betinfo(
        self,
        msg: Mapping[str, Any],
        *,
        timeout: float,
    ) -> dict[str, Any]:
        """Query one exact binary option using the broker's legacy status route.

        The current community protocol exposes ``api_game_betinfo`` for an
        authoritative lookup by broker option id.  This is deliberately a
        read-only request and is used as the primary restart/reconciliation
        path because the recent-options list is neither complete nor stable.
        """

        raw_id = msg.get("id")
        if isinstance(raw_id, bool) or raw_id is None:
            return {"isSuccessful": False, "message": "Option id unavailable"}
        try:
            option_id = int(str(raw_id))
        except ValueError:
            return {"isSuccessful": False, "message": "Option id invalid"}
        if timeout <= 0:
            raise ValueError("IQ Option request timeout must be positive")
        if not self.is_connected:
            raise IQOptionExternalError("IQOPTION_WEBSOCKET_UNAVAILABLE")
        with self._betinfo_query_lock:
            while True:
                try:
                    self._betinfo_responses.get_nowait()
                except queue.Empty:
                    break
            self._send(
                {
                    "name": "api_game_betinfo",
                    "msg": {"currency": "USD", "id[0]": option_id},
                    "request_id": f"tl-{uuid4()}",
                }
            )
            try:
                response = self._betinfo_responses.get(timeout=timeout)
            except queue.Empty as exc:
                raise IQOptionExternalError("IQOPTION_REQUEST_TIMEOUT") from exc
        raw = response.get("msg")
        if isinstance(raw, Mapping) and raw.get("isSuccessful") is False:
            return {"isSuccessful": False, "message": "Option not found"}
        matched = self._find_exact_contract(raw, str(option_id), "")
        if matched is None:
            return {"isSuccessful": False, "message": "Option not found"}
        return {"isSuccessful": True, "result": matched}

    @classmethod
    def _find_exact_contract(
        cls,
        raw: object,
        wanted_id: str,
        wanted_client_ref: str,
    ) -> dict[str, Any] | None:
        """Extract only an exact id/client-ref match from known response containers."""

        if isinstance(raw, list):
            for item in raw:
                matched = cls._find_exact_contract(item, wanted_id, wanted_client_ref)
                if matched is not None:
                    return matched
            return None
        if not isinstance(raw, Mapping):
            return None

        item_id = raw.get("id", raw.get("option_id", raw.get("contract_id")))
        candidate_ids = item_id if isinstance(item_id, (list, tuple)) else (item_id,)
        exact_id = next(
            (candidate for candidate in candidate_ids if str(candidate) == wanted_id),
            None,
        )
        item_ref = str(raw.get("client_order_id", ""))
        id_matches = bool(wanted_id) and exact_id is not None
        ref_matches = bool(wanted_client_ref) and item_ref == wanted_client_ref
        if id_matches or ref_matches:
            normalized = dict(raw)
            normalized["id"] = exact_id if exact_id is not None else wanted_id
            return normalized

        for key in (
            "result",
            "data",
            "options",
            "open_options",
            "closed_options",
            "option",
        ):
            child = raw.get(key)
            if isinstance(child, Mapping) and wanted_id and wanted_id in child:
                keyed = child[wanted_id]
                if isinstance(keyed, Mapping):
                    return {"id": wanted_id, **dict(keyed)}
            matched = cls._find_exact_contract(child, wanted_id, wanted_client_ref)
            if matched is not None:
                return matched
        return None

    def _binary_expiration(self, duration: int) -> int:
        if duration != 1:
            raise IQOptionExternalError("IQOPTION_DURATION_UNSUPPORTED")
        server_epoch = self.get_clock().server_epoch
        seconds = server_epoch % 60
        minutes_ahead = 1 if seconds < 30 else 2
        return server_epoch - seconds + minutes_ahead * 60

    @staticmethod
    def _normalize_contract_event(
        name: object,
        raw: object,
    ) -> dict[str, Any] | None:
        if not isinstance(raw, Mapping):
            return None
        option_id = raw.get("option_id", raw.get("id"))
        if option_id is None:
            return None
        normalized = dict(raw)
        normalized["id"] = option_id
        if name == "option-opened":
            normalized["status"] = "open"
            return normalized
        try:
            amount = Decimal(str(raw.get("amount", "0")))
            profit = Decimal(str(raw.get("profit_amount", raw.get("win_amount", "0"))))
        except InvalidOperation:
            return None
        normalized["status"] = "win" if profit > amount else "loose"
        normalized["win"] = normalized["status"]
        normalized["win_amount"] = str(profit)
        return normalized

    @staticmethod
    def _json_with_decimal_numbers(payload: Mapping[str, object]) -> str:
        placeholders: dict[str, str] = {}

        def replace(value: object) -> object:
            if isinstance(value, Decimal):
                if not value.is_finite():
                    raise ValueError("non-finite decimal is not valid JSON")
                marker = f"__TL_DECIMAL_{uuid4().hex}__"
                placeholders[marker] = format(value, "f")
                return marker
            if isinstance(value, Mapping):
                return {str(key): replace(item) for key, item in value.items()}
            if isinstance(value, list):
                return [replace(item) for item in value]
            if isinstance(value, tuple):
                return [replace(item) for item in value]
            return value

        encoded = json.dumps(replace(payload), separators=(",", ":"))
        for marker, numeric in placeholders.items():
            encoded = encoded.replace(json.dumps(marker), numeric)
        return encoded

    def _active_id(self, symbol: str) -> int:
        with self._lock:
            active_id = self._active_ids.get(symbol.upper())
        if active_id is None:
            raise IQOptionExternalError("IQOPTION_SYMBOL_UNSUPPORTED")
        return active_id


__all__ = [
    "IQOptionAccountMode",
    "IQOptionCommunityReadOnlySession",
    "IQOptionConnectionSnapshot",
    "IQOptionExternalError",
    "IQOPTION_ACTIVE_IDS",
]
