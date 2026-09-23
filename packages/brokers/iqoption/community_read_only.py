"""Minimal unofficial IQ Option authenticated connection.

The observed authentication and WebSocket flow is derived from the MIT-licensed
``victalejo/iqoptionapi`` read-only client at commit
``acac6e08333466ae188c7dfa7fd2a03174e34ca2``. Financial operations are exposed
only for an explicitly selected Practice balance; Real remains fail-closed.
"""

from __future__ import annotations

import http.client
import json
import math
import queue
import threading
import time
from collections import OrderedDict
from collections.abc import Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from http.cookies import SimpleCookie
from typing import Any, Protocol
from urllib.parse import urlencode
from uuid import uuid4

from websockets.exceptions import WebSocketException
from websockets.sync.client import connect as websocket_connect

from packages.brokers.iqoption.result_parser import (
    IQOPTION_CLOSED_OPTIONS,
    IQOPTION_EVENT_NAME_KEY,
    IQOPTION_HISTORY_CONTAINER_KEY,
    IQOPTION_OPEN_OPTIONS,
)
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
IQOPTION_CLOCK_PROBE_TIMEOUT_SECONDS = 5.0
IQOPTION_CLOCK_REFRESH_TIMEOUT_SECONDS = 2.0
IQOPTION_BALANCE_REQUEST_TIMEOUT_SECONDS = 3.0
IQOPTION_BALANCE_MAX_AGE_SECONDS = 15.0
IQOPTION_LATE_ORDER_ACK_TTL_SECONDS = 180.0
IQOPTION_LATE_ORDER_ACK_CAPACITY = 64
IQOPTION_OPTIONS_HISTORY_LIMIT = 500
IQOPTION_AMBIGUOUS_SUBMIT_WINDOW_SECONDS = 20.0

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

IQOPTION_CRYPTO_NON_BINARY: frozenset[str] = frozenset(
    {
        "BTCUSD",
        "ETHUSD",
        "XRPUSD",
        "SOLUSD",
        "DOGEUSD",
        "LTCUSD",
        "BCHUSD",
        "EOSUSD",
        "TRXUSD",
        "XLMUSD",
        "DSHUSD",
        "BTGUSD",
        "ZECUSD",
        "ETCUSD",
        # Precious metals and commodities without 1m turbo candles
        "XAUUSD",
        "XAGUSD",
        "XPDUSD",
        "XPTUSD",
    }
)


@dataclass(frozen=True, slots=True)
class CanonicalActive:
    symbol: str
    broker_symbol: str
    active_id: int
    product_kind: str
    catalog_generation: int
    resolution_source: str
    resolved_at: datetime


class ActiveIdentityResolver:
    """Bidirectional active identity resolver tied to the session catalog generation."""

    def __init__(
        self,
        active_ids: Mapping[str, int] | None = None,
        generation: int = 1,
        fallback_map: Mapping[str, int] | None = None,
    ) -> None:
        self._lock = threading.Lock()
        self._generation = generation
        self._symbol_to_id: dict[str, int] = {k.upper(): v for k, v in (active_ids or {}).items()}
        self._id_to_symbol: dict[int, str] = {v: k for k, v in self._symbol_to_id.items()}
        self._fallback = {k.upper(): v for k, v in (fallback_map or IQOPTION_ACTIVE_IDS).items()}
        self._fallback_rev: dict[int, str] = {v: k for k, v in self._fallback.items()}

    @property
    def generation(self) -> int:
        with self._lock:
            return self._generation

    def update_catalog(self, active_ids: Mapping[str, int], generation: int) -> None:
        with self._lock:
            self._generation = generation
            self._symbol_to_id = {k.upper(): v for k, v in active_ids.items()}
            self._id_to_symbol = {v: k for k, v in self._symbol_to_id.items()}

    def get_active_id(self, symbol: str) -> int | None:
        with self._lock:
            sym = symbol.strip().upper()
            active_id = self._symbol_to_id.get(sym)
            if active_id is not None:
                return active_id
            return self._fallback.get(sym)

    def get_symbol(self, active_id: int | str) -> str | None:
        try:
            aid = int(str(active_id).strip())
        except (ValueError, TypeError):
            return None
        with self._lock:
            sym = self._id_to_symbol.get(aid)
            if sym is not None:
                return sym
            return self._fallback_rev.get(aid)

    def resolve(
        self,
        raw_active: object,
        *,
        expected_symbol: str | None = None,
        product_kind: str = "turbo",
    ) -> CanonicalActive | None:
        if raw_active is None or isinstance(raw_active, bool):
            return None
        resolved_at = datetime.now(UTC)
        raw_str = str(raw_active).strip().upper()
        if not raw_str:
            return None

        # Case 1: raw_active is numeric active_id
        if raw_str.isdigit():
            aid = int(raw_str)
            # Active catalog of current generation takes strict precedence
            with self._lock:
                sym = self._id_to_symbol.get(aid)
            if sym is not None:
                source = (
                    "EXPECTED_SYMBOL_MATCH"
                    if expected_symbol and expected_symbol.strip().upper() == sym
                    else "CATALOG_ACTIVE_ID"
                )
                return CanonicalActive(
                    symbol=sym,
                    broker_symbol=raw_str,
                    active_id=aid,
                    product_kind=product_kind,
                    catalog_generation=self.generation,
                    resolution_source=source,
                    resolved_at=resolved_at,
                )
            # Fallback only when aid is not present in active catalog
            fallback_sym = self._fallback_rev.get(aid)
            if fallback_sym is not None:
                source = (
                    "EXPECTED_SYMBOL_MATCH"
                    if expected_symbol and expected_symbol.strip().upper() == fallback_sym
                    else "FALLBACK_ACTIVE_ID"
                )
                return CanonicalActive(
                    symbol=fallback_sym,
                    broker_symbol=raw_str,
                    active_id=aid,
                    product_kind=product_kind,
                    catalog_generation=self.generation,
                    resolution_source=source,
                    resolved_at=resolved_at,
                )
            return None

        # Case 2: raw_active is a symbol string (e.g. "EURUSD" or "EURUSD-OTC" or "SPX/GOLD")
        aid = self.get_active_id(raw_str)
        if aid is not None:
            return CanonicalActive(
                symbol=raw_str,
                broker_symbol=raw_str,
                active_id=aid,
                product_kind=product_kind,
                catalog_generation=self.generation,
                resolution_source="CATALOG_SYMBOL",
                resolved_at=resolved_at,
            )

        # Case 3: If expected_symbol matches raw_str directly even if not in active map
        if expected_symbol and raw_str == expected_symbol.strip().upper():
            return CanonicalActive(
                symbol=raw_str,
                broker_symbol=raw_str,
                active_id=0,
                product_kind=product_kind,
                catalog_generation=self.generation,
                resolution_source="SYMBOL_NAME_MATCH",
                resolved_at=resolved_at,
            )

        return None


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
                ping_interval=25,
                ping_timeout=40,
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
        initial_ssid: SecretValue | None = None,
        allow_http_login: bool = True,
        on_ssid_ready: Callable[[SecretValue], None] | None = None,
        on_ssid_invalid: Callable[[], None] | None = None,
        allow_real_trading: bool = False,
    ) -> None:
        self._email = email
        self._password = password
        self._account_mode = account_mode
        self._allow_real_trading = allow_real_trading
        self._login = login
        self._websocket_factory = websocket_factory
        self._monotonic = monotonic
        self._wall_time = wall_time
        self._allow_http_login = allow_http_login
        self._on_ssid_ready = on_ssid_ready
        self._on_ssid_invalid = on_ssid_invalid
        self._lock = threading.Lock()
        self._connect_lock = threading.Lock()
        self._clock_query_lock = threading.Lock()
        self._balance_query_lock = threading.Lock()
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
        # The worker may receive this only from its DPAPI CurrentUser vault.
        # It is never serialized over IPC, logged or returned to Core.
        self._session_cookie: SecretValue | None = initial_ssid
        self._websocket_reconnect_epochs: list[float] = []
        self._authenticated = False
        self._profile: dict[str, object] | None = None
        self._balances: list[dict[str, object]] | None = None
        self._selected_balance_identity: tuple[int, int, str] | None = None
        self._balance_observed_at_wall = 0.0
        self._balance_observed_at_monotonic = float("-inf")
        self._balance_generation = 0
        self._balance_revision = 0
        self._balance_source = "BROKER_SNAPSHOT"
        self._balance_pending: tuple[int, str, queue.Queue[dict[str, object]], int] | None = None
        self._server_epoch: Decimal | None = None
        self._server_epoch_received_at = 0.0
        self._server_epoch_monotonic = 0.0
        self._clock_sample_sequence = 0
        self._connected_at_monotonic = 0.0
        self._connection_generation = 0
        self._clock_round_trip: float | None = None
        self._clock_probe_at = float("-inf")
        self._clock_updated = threading.Event()
        self._last_rx_monotonic = float("-inf")
        self._pending: dict[str, queue.Queue[dict[str, Any]]] = {}
        # A submit timeout removes the synchronous waiter but must not discard
        # an ACK that arrives later.  This bounded generation-fenced registry
        # never resends the order; it only routes late identity evidence.
        self._late_order_requests: OrderedDict[str, tuple[int, float, str, str]] = OrderedDict()
        self._contract_events: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=256)
        # Legacy api_game_betinfo responses do not reliably echo request_id.
        # Keep one serialized, bounded response lane and validate the exact
        # broker option id before accepting any result.
        self._betinfo_responses: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=4)
        # ``get-options`` is a legacy endpoint that may omit request_id.  It
        # therefore owns one generation-fenced single-flight lane.  A timeout
        # invalidates the transport so a late uncorrelated frame can never be
        # consumed by a later reconciliation query.
        self._options_pending: tuple[int, str, queue.Queue[dict[str, Any]]] | None = None
        # Static ids are a bounded bootstrap for the first catalogue request.
        # Once a catalogue is observed, only the broker's current session map
        # is authoritative.
        self._active_ids = dict(IQOPTION_ACTIVE_IDS)
        self._catalog_refreshed = False
        self._catalog_generation = 0
        self._identity_resolver = ActiveIdentityResolver(self._active_ids, generation=0)
        self._digital_catalog_response: dict[str, Any] | None = None
        self._digital_catalog_retry_after_mono = 0.0

    @property
    def identity_resolver(self) -> ActiveIdentityResolver:
        return self._identity_resolver

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
            cached_rejected = False
            if cached_cookie is not None:
                self._reserve_websocket_reconnect()
                try:
                    snapshot = self._connect_websocket(cached_cookie, timeout)
                    self._notify_ssid_ready(cached_cookie)
                    return snapshot
                except IQOptionExternalError as exc:
                    # Only an explicit broker rejection proves the SSID is no
                    # longer usable.  Network and timeout failures must not
                    # cause a second HTTP login storm.
                    if exc.reason_code != "IQOPTION_AUTH_FAILED":
                        raise
                    self._session_cookie = None
                    self._notify_ssid_invalid()
                    cached_rejected = True

            if not self._allow_http_login:
                raise IQOptionExternalError(
                    "IQOPTION_AUTH_FAILED" if cached_rejected else "IQOPTION_SSID_UNAVAILABLE"
                )

            session_cookie = self._login(self._email, self._password, timeout)
            self._session_cookie = session_cookie
            try:
                snapshot = self._connect_websocket(session_cookie, timeout)
                self._notify_ssid_ready(session_cookie)
                return snapshot
            except IQOptionExternalError as exc:
                if exc.reason_code == "IQOPTION_AUTH_FAILED":
                    self._session_cookie = None
                    self._notify_ssid_invalid()
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
                snapshot = self._connect_websocket(session_cookie, timeout)
                self._notify_ssid_ready(session_cookie)
                return snapshot
            except IQOptionExternalError as exc:
                if exc.reason_code == "IQOPTION_AUTH_FAILED":
                    self._session_cookie = None
                    self._notify_ssid_invalid()
                raise

    def _notify_ssid_ready(self, session_cookie: SecretValue) -> None:
        callback = self._on_ssid_ready
        if callback is not None:
            callback(session_cookie)

    def _notify_ssid_invalid(self) -> None:
        callback = self._on_ssid_invalid
        if callback is not None:
            callback()

    def _connect_websocket(
        self,
        session_cookie: SecretValue,
        timeout: float,
    ) -> IQOptionConnectionSnapshot:
        self._close_transport(clear_session=False)
        self._connection_generation += 1
        connection_generation = self._connection_generation
        with self._lock:
            self._authenticated = False
            self._profile = None
            self._balances = None
            self._selected_balance_identity = None
            self._balance_observed_at_wall = 0.0
            self._balance_observed_at_monotonic = float("-inf")
            self._balance_generation = 0
            self._balance_revision = 0
            self._server_epoch = None
            self._server_epoch_received_at = 0.0
            self._server_epoch_monotonic = 0.0
            self._clock_round_trip = None
            self._clock_probe_at = float("-inf")
            self._clock_updated.clear()
            self._last_rx_monotonic = float("-inf")
            self._active_ids = dict(IQOPTION_ACTIVE_IDS)
            self._catalog_refreshed = False
            self._catalog_generation += 1
            self._identity_resolver.update_catalog(self._active_ids, self._catalog_generation)
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
                self._handle_message(raw, connection_generation=connection_generation)
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
            args=(websocket, connection_generation),
            name="iqoption-read-only-receiver",
            daemon=True,
        )
        self._reader.start()
        return self.snapshot()

    def snapshot(self) -> IQOptionConnectionSnapshot:
        return IQOptionConnectionSnapshot(
            account_mode=self._account_mode,
            balance=self._cached_balance(),
            profile_confirmed=self._profile is not None,
            connected=self.is_connected,
        )

    def get_balance(self) -> BrokerAccountBalance:
        """Return a newly observed balance from this WebSocket generation.

        The broker's initial ``balances`` frame is not treated as fresh forever.
        Each IPC balance read performs one bounded, single-flight
        ``get-balances`` request.  A validated full ``balances`` frame received
        in the same connection generation satisfies that single-flight read even
        when a legacy server omits or rewrites ``request_id``.  The receive
        timestamp is retained instead of being minted when Core reads the cache.
        """

        timeout = IQOPTION_BALANCE_REQUEST_TIMEOUT_SECONDS
        started = self._monotonic()
        responses: queue.Queue[dict[str, object]] | None = None
        if not self._balance_query_lock.acquire(timeout=timeout):
            raise IQOptionExternalError("IQOPTION_REQUEST_TIMEOUT")
        try:
            if not self.is_connected:
                raise IQOptionExternalError(self._disconnect_reason)
            generation = self._connection_generation
            with self._lock:
                starting_revision = self._balance_revision
            request_id = f"tl-{uuid4()}"
            responses = queue.Queue(maxsize=1)
            with self._pending_lock:
                self._balance_pending = (
                    generation,
                    request_id,
                    responses,
                    starting_revision,
                )
            self._send(
                {
                    "name": "sendMessage",
                    "msg": {"name": "get-balances", "version": "1.0", "body": {}},
                    "request_id": request_id,
                }
            )
            remaining = max(0.0, timeout - (self._monotonic() - started))
            try:
                response = responses.get(timeout=remaining)
            except queue.Empty as exc:
                with self._lock:
                    revision_advanced = (
                        self._balance_generation == generation
                        and self._balance_revision > starting_revision
                    )
                if revision_advanced:
                    return self._cached_balance()
                raise IQOptionExternalError("IQOPTION_REQUEST_TIMEOUT") from exc
            transport_error = response.get("_transport_error")
            if isinstance(transport_error, str):
                raise IQOptionExternalError(transport_error)
            if generation != self._connection_generation or not self.is_connected:
                raise IQOptionExternalError(self._disconnect_reason)
            return self._cached_balance()
        finally:
            with self._pending_lock:
                pending = self._balance_pending
                if pending is not None and pending[2] is responses:
                    self._balance_pending = None
            self._balance_query_lock.release()

    def _cached_balance(self) -> BrokerAccountBalance:
        with self._lock:
            raw = next(
                (
                    dict(item)
                    for item in self._balances or ()
                    if item.get("type") == self._account_mode.balance_type
                ),
                None,
            )
            observed_wall = self._balance_observed_at_wall
            observed_mono = self._balance_observed_at_monotonic
            generation = self._balance_generation
            current_generation = self._connection_generation
            revision = self._balance_revision
            source = self._balance_source
            connected = self.is_connected
        if raw is None:
            raise IQOptionExternalError("IQOPTION_ACCOUNT_MODE_UNAVAILABLE")
        age = self._monotonic() - observed_mono
        if (
            not connected
            or generation != current_generation
            or not math.isfinite(age)
            or not 0 <= age <= IQOPTION_BALANCE_MAX_AGE_SECONDS
            or not math.isfinite(observed_wall)
            or observed_wall <= 0
        ):
            raise IQOptionExternalError("IQOPTION_BALANCE_STALE")
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
            observed_at_utc=datetime.fromtimestamp(observed_wall, UTC),
            source_age_seconds=age,
            connection_generation=generation,
            revision=revision,
            source=source,
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
            source_age_seconds=elapsed,
            connection_generation=self._connection_generation,
            sample_sequence=self._clock_sample_sequence,
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
        timeout: float = 3.0,
    ) -> tuple[MarketCandle, ...]:
        """Fetch closed broker candles through the authenticated WebSocket."""

        if self._account_mode not in {IQOptionAccountMode.PRACTICE, IQOptionAccountMode.REAL}:
            raise IQOptionExternalError("IQOPTION_ACCOUNT_UNSUPPORTED")
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
        if self._account_mode not in {IQOptionAccountMode.PRACTICE, IQOptionAccountMode.REAL}:
            raise IQOptionExternalError("IQOPTION_ACCOUNT_UNSUPPORTED")
        if duration_minutes != 1:
            raise IQOptionExternalError("IQOPTION_OPERATION_UNSUPPORTED")
        response = self._request_initialization(timeout)
        try:
            msg = response.get("msg") if isinstance(response, Mapping) else None
            if not isinstance(msg, Mapping):
                raise ValueError("invalid catalogue")
            raw_turbo = msg.get("turbo")
            raw_binary = msg.get("binary")
            actives_turbo = raw_turbo.get("actives") if isinstance(raw_turbo, Mapping) else None
            actives_binary = raw_binary.get("actives") if isinstance(raw_binary, Mapping) else None
            if not isinstance(actives_turbo, Mapping) and not isinstance(actives_binary, Mapping):
                raise ValueError("invalid catalogue")

            matches: list[tuple[str, Mapping[str, Any]]] = []
            for candidate_actives in (actives_turbo, actives_binary):
                if not isinstance(candidate_actives, Mapping):
                    continue
                candidate_matches = [
                    (str(active_id), active)
                    for active_id, active in candidate_actives.items()
                    if isinstance(active, Mapping)
                    and self._catalog_symbol(active.get("name")) == symbol.upper()
                ]
                if candidate_matches:
                    matches = candidate_matches
                    break

            if not matches:
                with self._lock:
                    previous_id = self._active_ids.get(symbol.upper())
                all_actives: dict[str, Any] = {}
                if isinstance(actives_turbo, Mapping):
                    all_actives.update(actives_turbo)
                if isinstance(actives_binary, Mapping):
                    all_actives.update(actives_binary)
                if previous_id is not None and str(previous_id) in all_actives:
                    raise ValueError("catalogue id no longer matches exact asset")
                raise IQOptionExternalError("IQOPTION_ACTIVE_UNAVAILABLE")
            if len(matches) != 1:
                raise ValueError("ambiguous exact asset")
            raw_active_id, active = matches[0]
            active_id = int(raw_active_id)
            with self._lock:
                self._active_ids[symbol.upper()] = active_id
            if active.get("enabled") is False:
                raise IQOptionExternalError("IQOPTION_ACTIVE_UNAVAILABLE")
            if active.get("is_suspended") is True:
                raise IQOptionExternalError("IQOPTION_ACTIVE_SUSPENDED")
            if active.get("enabled") is not True or active.get("is_suspended") is not False:
                raise ValueError("invalid availability flags")
            option = active.get("option")
            profit = option.get("profit") if isinstance(option, Mapping) else active.get("profit")
            if not isinstance(profit, Mapping):
                raise ValueError("invalid profit mapping")
            commission = Decimal(str(profit["commission"]))
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
            if item.product not in {BrokerInstrumentProduct.TURBO, BrokerInstrumentProduct.BINARY}:
                continue
            try:
                if (
                    item.broker_symbol not in active_ids
                    or item.product is BrokerInstrumentProduct.TURBO
                ):
                    active_ids[item.broker_symbol] = int(item.broker_id)
            except ValueError:
                continue
        with self._lock:
            self._active_ids = active_ids
            self._catalog_refreshed = True
            self._catalog_generation += 1
            generation = self._catalog_generation
            self._identity_resolver.update_catalog(active_ids, generation=generation)
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
                elif symbol.removesuffix("-OTC") in IQOPTION_CRYPTO_NON_BINARY:
                    availability = BrokerInstrumentAvailability.DISABLED
                else:
                    availability = BrokerInstrumentAvailability.OPEN
                    # Binary options on IQ Option require an active profit configuration
                    # to be genuinely tradable (commission in [0, 100)).
                    option = raw_active.get("option")
                    profit = (
                        option.get("profit")
                        if isinstance(option, Mapping)
                        else raw_active.get("profit")
                    )
                    commission = (
                        profit.get("commission") if isinstance(profit, Mapping) else None
                    )
                    if commission is not None:
                        try:
                            comm_dec = Decimal(str(commission))
                            if not (comm_dec.is_finite() and 0 <= comm_dec < 100):
                                availability = BrokerInstrumentAvailability.DISABLED
                        except Exception:
                            availability = BrokerInstrumentAvailability.DISABLED
                turbo = product is BrokerInstrumentProduct.TURBO
                is_open = availability is BrokerInstrumentAvailability.OPEN
                valid_account = self._account_mode in {
                    IQOptionAccountMode.PRACTICE,
                    IQOptionAccountMode.REAL,
                }
                analyzable = turbo or is_open
                executable = is_open and valid_account
                parsed.append(
                    BrokerInstrument(
                        broker=Broker.IQ_OPTION,
                        broker_id=str(raw_id),
                        broker_symbol=symbol,
                        display_name=self._display_name(symbol),
                        product=product,
                        market_kind=self._market_kind(symbol),
                        availability=availability,
                        duration_seconds=(60,) if (turbo or is_open) else (),
                        detectable=True,
                        analyzable=analyzable,
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
        symbol = raw_name.rsplit(".", 1)[-1].strip().upper()
        if symbol.endswith("-OP"):
            return symbol.removesuffix("-OP")
        return symbol

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

        if self._account_mode not in {IQOptionAccountMode.PRACTICE, IQOptionAccountMode.REAL}:
            raise IQOptionExternalError("IQOPTION_ACCOUNT_UNSUPPORTED")
        if (
            name == "buy"
            and self._account_mode is IQOptionAccountMode.REAL
            and not self._allow_real_trading
        ):
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
            self._late_order_requests.clear()

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

    def _record_balances(
        self,
        raw_balances: list[object],
        *,
        connection_generation: int,
        observed_at_wall: float,
        observed_at_monotonic: float,
        requested_after_revision: int | None = None,
        request_correlated: bool = False,
    ) -> int | None:
        """Validate and atomically install one full account balance snapshot."""

        if connection_generation != self._connection_generation:
            return None
        parsed = [dict(item) for item in raw_balances if isinstance(item, Mapping)]
        selected = [item for item in parsed if item.get("type") == self._account_mode.balance_type]
        if not selected:
            # Preserve the historical ACCOUNT_MODE_UNAVAILABLE diagnosis during
            # login while withholding freshness evidence for a missing account.
            with self._lock:
                if connection_generation == self._connection_generation and self._balances is None:
                    self._balances = parsed
            return None
        if len(selected) != 1:
            raise IQOptionExternalError("IQOPTION_BALANCE_INVALID")
        normalized, identity = self._normalize_balance_record(selected[0])
        for index, item in enumerate(parsed):
            if item is selected[0]:
                parsed[index] = normalized
                break
        with self._lock:
            if connection_generation != self._connection_generation:
                return None
            previous_identity = self._selected_balance_identity
            if previous_identity is not None and requested_after_revision is None:
                # A full snapshot after login must belong to the current
                # single-flight refresh; otherwise it may be a retired reply.
                return None
            if (
                previous_identity is not None
                and request_correlated
                and (requested_after_revision != self._balance_revision)
            ):
                return None
            if (
                previous_identity is not None
                and self._balance_source == "BALANCE_PUSH"
                and (not request_correlated or requested_after_revision != self._balance_revision)
            ):
                # This legacy route can omit/rewrite request_id. Without exact
                # correlation it cannot prove that an arriving full snapshot
                # was taken after the newer push already installed above.
                return None
            if previous_identity is not None and previous_identity != identity:
                raise IQOptionExternalError("IQOPTION_BALANCE_INVALID")
            self._balances = parsed
            self._selected_balance_identity = identity
            self._balance_observed_at_wall = observed_at_wall
            self._balance_observed_at_monotonic = observed_at_monotonic
            self._balance_generation = connection_generation
            self._balance_revision += 1
            self._balance_source = "GET_BALANCES"
            return self._balance_revision

    def _record_balance_change(
        self,
        current_balance: Mapping[str, object],
        *,
        connection_generation: int,
        observed_at_wall: float,
        observed_at_monotonic: float,
    ) -> int | None:
        """Apply only a validated push for the explicitly selected balance id."""

        with self._lock:
            identity = self._selected_balance_identity
            if identity is None or connection_generation != self._connection_generation:
                return None
            balances = [dict(item) for item in self._balances or ()]
            raw_id = current_balance.get("id")
            if isinstance(raw_id, bool) or not isinstance(raw_id, int) or raw_id != identity[0]:
                # The broker can publish updates for other account modes. They are
                # outside the selected account scope and must not replace its value.
                return None
            selected_index = next(
                (
                    index
                    for index, item in enumerate(balances)
                    if item.get("id") == identity[0]
                    and item.get("type") == self._account_mode.balance_type
                ),
                None,
            )
            if selected_index is None:
                raise IQOptionExternalError("IQOPTION_BALANCE_INVALID")
            merged = {**balances[selected_index], **dict(current_balance)}
            normalized, pushed_identity = self._normalize_balance_record(merged)
            if pushed_identity != identity:
                raise IQOptionExternalError("IQOPTION_BALANCE_INVALID")
            balances[selected_index] = normalized
            self._balances = balances
            self._balance_observed_at_wall = observed_at_wall
            self._balance_observed_at_monotonic = observed_at_monotonic
            self._balance_generation = connection_generation
            self._balance_revision += 1
            self._balance_source = "BALANCE_PUSH"
            return self._balance_revision

    @staticmethod
    def _normalize_balance_record(
        raw: Mapping[str, object],
    ) -> tuple[dict[str, object], tuple[int, int, str]]:
        balance_id = raw.get("id")
        balance_type = raw.get("type")
        currency = raw.get("currency") or raw.get("currency_code")
        if (
            isinstance(balance_id, bool)
            or not isinstance(balance_id, int)
            or isinstance(balance_type, bool)
            or not isinstance(balance_type, int)
            or not isinstance(currency, str)
        ):
            raise IQOptionExternalError("IQOPTION_BALANCE_INVALID")
        normalized_currency = currency.strip().upper()
        if (
            len(normalized_currency) != 3
            or not normalized_currency.isascii()
            or not normalized_currency.isalpha()
        ):
            raise IQOptionExternalError("IQOPTION_BALANCE_INVALID")
        try:
            amount = Decimal(str(raw.get("amount")))
        except (InvalidOperation, ValueError) as exc:
            raise IQOptionExternalError("IQOPTION_BALANCE_INVALID") from exc
        if not amount.is_finite() or amount < 0:
            raise IQOptionExternalError("IQOPTION_BALANCE_INVALID")
        minor_units = amount * Decimal(100)
        if minor_units != minor_units.to_integral_value():
            raise IQOptionExternalError("IQOPTION_BALANCE_PRECISION_UNSUPPORTED")
        normalized = dict(raw)
        normalized["currency"] = normalized_currency
        normalized["amount"] = str(amount)
        return normalized, (balance_id, balance_type, normalized_currency)

    def _reader_loop(self, websocket: IQOptionWebSocket, connection_generation: int) -> None:
        try:
            while not self._stop.is_set() and not self._disconnected.is_set():
                try:
                    raw = websocket.recv(timeout=1.0)
                except TimeoutError:
                    continue
                self._handle_message(raw, connection_generation=connection_generation)
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
            if self._balance_pending is not None:
                waiters.append(self._balance_pending[2])
            if self._options_pending is not None:
                waiters.append(self._options_pending[2])
        for waiter in waiters:
            with suppress(queue.Full):
                waiter.put_nowait({"_transport_error": reason})

    def _handle_message(
        self,
        raw: str | bytes,
        *,
        connection_generation: int | None = None,
    ) -> None:
        if (
            connection_generation is not None
            and connection_generation != self._connection_generation
        ):
            return
        generation = self._connection_generation
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
            with self._pending_lock:
                options_pending = self._options_pending
            request_id = message.get("request_id")
            if (
                options_pending is not None
                and options_pending[0] == generation
                and request_id in (None, "", options_pending[1])
            ):
                with suppress(queue.Full):
                    options_pending[2].put_nowait(dict(message))
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
                with self._pending_lock:
                    balance_pending = self._balance_pending
                raw_request_id = message.get("request_id")
                revision = self._record_balances(
                    balances,
                    connection_generation=generation,
                    observed_at_wall=self._wall_time(),
                    observed_at_monotonic=self._last_rx_monotonic,
                    requested_after_revision=(
                        None if balance_pending is None else balance_pending[3]
                    ),
                    request_correlated=(
                        balance_pending is not None
                        and isinstance(raw_request_id, str)
                        and raw_request_id == balance_pending[1]
                    ),
                )
                if (
                    revision is not None
                    and balance_pending is not None
                    and balance_pending[0] == generation
                ):
                    # Balance reads have one dedicated in-flight lane.  Any newly
                    # validated full snapshot in this connection is authoritative
                    # observation evidence; requiring an echoed wire ID caused
                    # valid IQ legacy responses to be reported as timeouts.
                    with suppress(queue.Full):
                        balance_pending[2].put_nowait({"revision": revision})
            return
        if name in {
            "balance-changed",
            "internal-billing.auth-balance-changed",
            "internal-billing.balance-changed",
        }:
            raw_change = message.get("msg")
            current = raw_change.get("current_balance") if isinstance(raw_change, Mapping) else None
            if (
                isinstance(raw_change, Mapping)
                and not isinstance(current, Mapping)
                and not isinstance(current, bool)
                and isinstance(current, (int, float, Decimal, str))
            ):
                balance_id = raw_change.get("balance_id", raw_change.get("id"))
                current = {"id": balance_id, "amount": current}
            if isinstance(current, Mapping):
                revision = self._record_balance_change(
                    current,
                    connection_generation=generation,
                    observed_at_wall=self._wall_time(),
                    observed_at_monotonic=self._last_rx_monotonic,
                )
                with self._pending_lock:
                    balance_pending = self._balance_pending
                if (
                    revision is not None
                    and balance_pending is not None
                    and balance_pending[0] == generation
                ):
                    with suppress(queue.Full):
                        balance_pending[2].put_nowait({"revision": revision, "source": "push"})
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
            self._clock_sample_sequence += 1
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
        for event_name in (
            "internal-billing.auth-balance-changed",
            "internal-billing.balance-changed",
        ):
            self._send(
                {
                    "name": "subscribeMessage",
                    "msg": {
                        "name": event_name,
                        "version": "1.0",
                        "params": {"routingFilters": {}},
                    },
                    "request_id": f"tl-{uuid4()}",
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
        late_order_context: tuple[str, str] | None = None,
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
                # Atomically retire the synchronous waiter and, for a
                # potentially-sent order only, retain enough local routing
                # identity for a late ACK.  The wire command is never retried.
                with self._pending_lock:
                    self._pending.pop(request_id, None)
                    try:
                        response = response_queue.get_nowait()
                    except queue.Empty:
                        if late_order_context is not None:
                            self._remember_late_order_request_locked(
                                request_id,
                                late_order_context[0],
                                late_order_context[1],
                            )
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
            self._purge_late_order_requests_locked()
            late_context = (
                None
                if response_queue is not None
                else self._late_order_requests.pop(raw_request_id, None)
            )
        if response_queue is None:
            if late_context is not None:
                self._route_late_order_ack(message, late_context)
            return
        try:
            response_queue.put_nowait(dict(message))
        except queue.Full:
            return

    def _remember_late_order_request_locked(
        self,
        request_id: str,
        client_order_id: str,
        correlation_id: str,
    ) -> None:
        self._purge_late_order_requests_locked()
        self._late_order_requests[request_id] = (
            self._connection_generation,
            self._monotonic() + IQOPTION_LATE_ORDER_ACK_TTL_SECONDS,
            client_order_id,
            correlation_id,
        )
        while len(self._late_order_requests) > IQOPTION_LATE_ORDER_ACK_CAPACITY:
            self._late_order_requests.popitem(last=False)

    def _purge_late_order_requests_locked(self) -> None:
        now = self._monotonic()
        expired = [
            request_id
            for request_id, (generation, deadline, _, _) in self._late_order_requests.items()
            if generation != self._connection_generation or deadline < now
        ]
        for request_id in expired:
            self._late_order_requests.pop(request_id, None)

    def _route_late_order_ack(
        self,
        message: Mapping[str, object],
        context: tuple[int, float, str, str],
    ) -> None:
        generation, deadline, client_order_id, correlation_id = context
        if (
            generation != self._connection_generation
            or deadline < self._monotonic()
            or message.get("name") != "option"
        ):
            return
        raw = message.get("msg")
        if not isinstance(raw, Mapping) or raw.get("status") is False:
            return
        option_id = raw.get("id", raw.get("option_id"))
        if (
            isinstance(option_id, bool)
            or not isinstance(option_id, (int, str))
            or not str(option_id).isdigit()
            or int(str(option_id)) <= 0
        ):
            return
        normalized = self._normalize_contract_event(
            "option-opened",
            {
                **dict(raw),
                "id": option_id,
                "client_order_id": client_order_id,
                "correlation_id": correlation_id,
            },
        )
        if normalized is not None:
            with suppress(queue.Full):
                self._contract_events.put_nowait({"name": "option-opened", "msg": normalized})

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
            requested_expiry = msg.get("expiry_epoch")
            if requested_expiry is None:
                expiry = self._binary_expiration(duration)
            else:
                if (
                    isinstance(requested_expiry, bool)
                    or not isinstance(requested_expiry, int)
                    or requested_expiry <= 0
                    or requested_expiry % 60 != 0
                ):
                    raise IQOptionExternalError("IQOPTION_EXPIRY_INVALID")
                server_epoch = self.get_clock().server_epoch
                if requested_expiry <= server_epoch or requested_expiry - server_epoch > 120:
                    raise IQOptionExternalError("IQOPTION_ENTRY_WINDOW_MISSED")
                expiry = requested_expiry
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
            late_order_context=(
                str(msg.get("client_order_id", "")),
                str(msg.get("correlation_id", "")),
            ),
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
            generation = self._connection_generation
            request_id = f"tl-{uuid4()}"
            responses: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=1)
            with self._pending_lock:
                self._options_pending = (generation, request_id, responses)
            try:
                self._send(
                    {
                        "name": "sendMessage",
                        "msg": {
                            "name": "get-options",
                            "body": {
                                "limit": IQOPTION_OPTIONS_HISTORY_LIMIT,
                                "instrument_type": "binary,turbo",
                                "user_balance_id": balance_id,
                            },
                        },
                        "request_id": request_id,
                    }
                )
                try:
                    response = responses.get(timeout=timeout)
                except queue.Empty as exc:
                    # An id-less response arriving after this point cannot be
                    # distinguished from the next response on the same socket.
                    # Retire the generation; recovery reconnects read-only and
                    # never resubmits the original order.
                    self._fail_transport("IQOPTION_REQUEST_TIMEOUT")
                    raise IQOptionExternalError("IQOPTION_REQUEST_TIMEOUT") from exc
                if response.get("_transport_error"):
                    raise IQOptionExternalError(str(response["_transport_error"]))
            finally:
                with self._pending_lock:
                    if (
                        self._options_pending is not None
                        and self._options_pending[0] == generation
                        and self._options_pending[1] == request_id
                    ):
                        self._options_pending = None
        wanted = str(msg.get("id", ""))
        wanted_client_ref = str(msg.get("client_order_id", ""))
        raw = response.get("msg")
        matched = self._find_exact_contract(raw, wanted, wanted_client_ref)
        history_identity_complete = True
        ambiguous = False
        if matched is None and wanted_client_ref:
            matched, ambiguous, history_identity_complete = (
                self._find_unique_contract_by_fingerprint(raw, msg)
            )
        if matched is not None:
            return {"isSuccessful": True, "result": matched}
        if ambiguous:
            return {
                "isSuccessful": False,
                "message": "Ambiguous option match",
                "reason_code": "IQOPTION_RECONCILIATION_AMBIGUOUS_MATCH",
            }
        coverage = self._options_negative_coverage(
            raw,
            history_identity_complete=history_identity_complete,
            submitted_at=msg.get("submitted_at"),
            response_metadata=response,
        )
        return {
            "isSuccessful": False,
            "message": "Option not found",
            "not_found_coverage": coverage,
        }

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
        if "active" in matched:
            canonical = self._identity_resolver.resolve(matched["active"])
            if canonical is not None:
                matched["active_canonical"] = canonical.symbol
                matched["active_id"] = canonical.active_id
        return {"isSuccessful": True, "result": matched}

    @classmethod
    def _find_exact_contract(
        cls,
        raw: object,
        wanted_id: str,
        wanted_client_ref: str,
        history_container: str | None = None,
    ) -> dict[str, Any] | None:
        """Extract only an exact id/client-ref match from known response containers."""

        if isinstance(raw, list):
            for item in raw:
                matched = cls._find_exact_contract(
                    item,
                    wanted_id,
                    wanted_client_ref,
                    history_container,
                )
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
            if exact_id is not None:
                normalized["id"] = exact_id
            elif wanted_id:
                normalized["id"] = wanted_id
            else:
                first_cand = next((c for c in candidate_ids if c is not None and str(c)), None)
                if first_cand is not None:
                    normalized["id"] = str(first_cand)
            if history_container is not None:
                normalized[IQOPTION_HISTORY_CONTAINER_KEY] = history_container
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
            child_container = (
                IQOPTION_OPEN_OPTIONS
                if key == "open_options"
                else IQOPTION_CLOSED_OPTIONS
                if key == "closed_options"
                else history_container
            )
            if isinstance(child, Mapping) and wanted_id and wanted_id in child:
                keyed = child[wanted_id]
                if isinstance(keyed, Mapping):
                    normalized = {"id": wanted_id, **dict(keyed)}
                    if child_container is not None:
                        normalized[IQOPTION_HISTORY_CONTAINER_KEY] = child_container
                    return normalized
            matched = cls._find_exact_contract(
                child,
                wanted_id,
                wanted_client_ref,
                child_container,
            )
            if matched is not None:
                return matched
        return None

    def _find_unique_contract_by_fingerprint(
        self,
        raw: object,
        query: Mapping[str, Any],
    ) -> tuple[dict[str, Any] | None, bool, bool]:
        """Recover an old ACK only from one complete historical fingerprint.

        IQ's binary open call does not echo our durable client reference in old
        history rows.  A unique active/direction/stake/time match is therefore
        the strongest available positive identity.  Missing fields or multiple
        candidates never become negative proof.
        """

        submitted_at = self._history_datetime(query.get("submitted_at"))
        symbol = str(query.get("symbol", "")).strip().upper()
        direction = str(query.get("direction", "")).strip().lower()
        amount_minor = query.get("amount_minor")
        if (
            submitted_at is None
            or not symbol
            or direction not in {"call", "put"}
            or isinstance(amount_minor, bool)
            or not isinstance(amount_minor, int)
            or amount_minor <= 0
        ):
            return None, False, False
        try:
            expected_active_id = self._active_id(symbol)
        except IQOptionExternalError:
            return None, False, False

        containers = self._option_history_containers(raw)
        if containers is None:
            return None, False, False
        matches: list[dict[str, Any]] = []
        identity_complete = True
        for container_name, items in containers:
            for item in items:
                if not isinstance(item, Mapping):
                    identity_complete = False
                    continue
                created_at = self._history_datetime(
                    self._first_present(
                        item,
                        (
                            "created",
                            "created_at",
                            "created_time",
                            "open_time",
                            "open_time_msec",
                            "purchase_time",
                            "purchased_at",
                            "buy_time",
                        ),
                    )
                )
                if created_at is None:
                    identity_complete = False
                    continue
                if (
                    abs((created_at - submitted_at).total_seconds())
                    > IQOPTION_AMBIGUOUS_SUBMIT_WINDOW_SECONDS
                ):
                    continue
                item_id = self._history_contract_id(item)
                item_active = self._first_present(
                    item, ("active_id", "active", "activeId", "instrument_id", "asset_id")
                )
                item_direction = (
                    str(
                        self._first_present(
                            item,
                            ("dir", "direction", "option_type", "type"),
                        )
                        or ""
                    )
                    .strip()
                    .lower()
                )
                item_amount = self._history_money_minor(
                    self._first_present(item, ("amount", "price", "invest", "stake"))
                )
                item_currency = str(item.get("currency", "")).strip().upper()
                expected_currency = str(query.get("currency", "USD")).strip().upper()
                active_matches = str(item_active).strip().upper() in {
                    symbol,
                    str(expected_active_id),
                }
                if (
                    item_id is None
                    or item_active is None
                    or item_direction not in {"call", "put"}
                    or item_amount is None
                    or (item_currency and item_currency != expected_currency)
                ):
                    identity_complete = False
                    continue
                if active_matches and item_direction == direction and item_amount == amount_minor:
                    normalized = dict(item)
                    normalized["id"] = item_id
                    # The broker often puts a numeric active id in ``active``.
                    # It has already been validated above, so expose the
                    # canonical symbol to the financial evidence boundary.
                    normalized["active"] = symbol
                    normalized["direction"] = direction
                    normalized["currency"] = expected_currency
                    # IQ omits our client reference from legacy binary history.
                    # The transport adds it only after a unique, complete
                    # fingerprint match so the next boundary can verify which
                    # durable query this normalized row answers.
                    normalized["client_order_id"] = str(query["client_order_id"])
                    normalized["_iq_identity_source"] = "HISTORY_FINGERPRINT"
                    normalized[IQOPTION_HISTORY_CONTAINER_KEY] = container_name
                    matches.append(normalized)
        if len(matches) == 1 and identity_complete:
            return matches[0], False, identity_complete
        return (
            None,
            len(matches) > 1 or (len(matches) == 1 and not identity_complete),
            identity_complete,
        )

    @classmethod
    def _options_negative_coverage(
        cls,
        raw: object,
        *,
        history_identity_complete: bool,
        submitted_at: object = None,
        response_metadata: object = None,
    ) -> dict[str, object] | None:
        containers = cls._option_history_containers(raw)
        if containers is None or not history_identity_complete:
            return None
        by_name = {name: items for name, items in containers}
        open_items = by_name.get(IQOPTION_OPEN_OPTIONS)
        closed_items = by_name.get(IQOPTION_CLOSED_OPTIONS)
        # A full open portfolio is authoritative.  A non-empty closed history
        # is never called exhaustive merely because it is shorter than the
        # requested limit: broker hard caps and pagination can do that too.
        # It must either explicitly report no following page or contain valid
        # timestamps reaching beyond the entire submission identity window.
        portfolio_checked = open_items is not None
        has_more = cls._history_has_more(response_metadata)
        statement_checked = closed_items == [] or (closed_items is not None and has_more is False)
        submitted = cls._history_datetime(submitted_at)
        if closed_items and submitted is not None:
            created = tuple(
                timestamp
                for item in closed_items
                if isinstance(item, Mapping)
                and (
                    timestamp := cls._history_datetime(
                        cls._first_present(
                            item,
                            (
                                "created",
                                "created_at",
                                "created_time",
                                "open_time",
                                "open_time_msec",
                                "purchase_time",
                                "purchased_at",
                                "buy_time",
                            ),
                        )
                    )
                )
                is not None
            )
            if created:
                page_oldest = min(created)
                window_covered = page_oldest <= submitted - timedelta(
                    seconds=IQOPTION_AMBIGUOUS_SUBMIT_WINDOW_SECONDS
                )
                statement_checked = statement_checked or window_covered
        if not (statement_checked and portfolio_checked):
            return None
        return {
            "observed_at": datetime.now(UTC).isoformat(),
            "statement_checked": True,
            "portfolio_checked": True,
        }

    @classmethod
    def _history_has_more(cls, raw: object) -> bool | None:
        if not isinstance(raw, Mapping):
            return None
        for key in ("has_more", "hasMore"):
            value = raw.get(key)
            if isinstance(value, bool):
                return value
        for key in ("msg", "result", "data", "options", "pagination"):
            nested = cls._history_has_more(raw.get(key))
            if nested is not None:
                return nested
        return None

    @classmethod
    def _option_history_containers(
        cls,
        raw: object,
    ) -> tuple[tuple[str, list[object]], ...] | None:
        if not isinstance(raw, Mapping):
            return None
        open_items = raw.get("open_options")
        closed_items = raw.get("closed_options")
        if isinstance(open_items, list) and isinstance(closed_items, list):
            return (
                (IQOPTION_OPEN_OPTIONS, open_items),
                (IQOPTION_CLOSED_OPTIONS, closed_items),
            )
        for key in ("result", "data", "options"):
            nested = cls._option_history_containers(raw.get(key))
            if nested is not None:
                return nested
        return None

    @staticmethod
    def _first_present(item: Mapping[str, Any], names: tuple[str, ...]) -> object | None:
        for name in names:
            if name in item and item[name] is not None:
                value: object = item[name]
                return value
        return None

    @staticmethod
    def _history_contract_id(item: Mapping[str, Any]) -> str | None:
        raw = item.get("id", item.get("option_id", item.get("contract_id")))
        if isinstance(raw, (list, tuple)):
            raw = raw[0] if len(raw) == 1 else None
        if isinstance(raw, bool) or raw is None or not str(raw).isdigit():
            return None
        return str(raw)

    @staticmethod
    def _history_datetime(raw: object) -> datetime | None:
        if isinstance(raw, bool) or raw is None:
            return None
        if isinstance(raw, datetime):
            value = raw
        elif (
            isinstance(raw, (int, float, Decimal)) or str(raw).strip().replace(".", "", 1).isdigit()
        ):
            try:
                epoch = Decimal(str(raw).strip())
                if epoch > Decimal("100000000000000"):
                    epoch /= Decimal(1_000_000)
                elif epoch > Decimal("100000000000"):
                    epoch /= Decimal(1_000)
                value = datetime.fromtimestamp(float(epoch), UTC)
            except (InvalidOperation, OSError, OverflowError, ValueError):
                return None
        else:
            try:
                value = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
            except ValueError:
                return None
        if value.tzinfo is None or value.utcoffset() is None:
            return None
        return value.astimezone(UTC)

    @staticmethod
    def _history_money_minor(raw: object) -> int | None:
        if isinstance(raw, bool) or raw is None:
            return None
        try:
            value = Decimal(str(raw).strip()) * Decimal(100)
        except (InvalidOperation, ValueError):
            return None
        integral = value.to_integral_value()
        if not value.is_finite() or value < 0 or value != integral:
            return None
        return int(integral)

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
        normalized[IQOPTION_EVENT_NAME_KEY] = str(name)
        if name == "option-opened":
            normalized["status"] = "open"
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
    "ActiveIdentityResolver",
    "CanonicalActive",
    "IQOptionAccountMode",
    "IQOptionCommunityReadOnlySession",
    "IQOptionConnectionSnapshot",
    "IQOptionExternalError",
    "IQOPTION_ACTIVE_IDS",
]
