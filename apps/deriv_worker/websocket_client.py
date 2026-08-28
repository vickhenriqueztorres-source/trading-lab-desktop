from __future__ import annotations

import json
import logging
import queue
import threading
import time
from collections.abc import Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Protocol

from websockets.exceptions import WebSocketException
from websockets.sync.client import ClientConnection, connect

from apps.deriv_worker.request_allowlist import (
    DerivOperation,
    validate_read_only_request,
)
from apps.deriv_worker.schema import (
    DerivErrorCategory,
    DerivWorkerError,
    parse_deriv_json,
)
from apps.deriv_worker.suspension import monotonic_gap_exceeds
from apps.deriv_worker.validators import PUBLIC_WS_URL, validate_deriv_ws_url

_LOGGER = logging.getLogger(__name__)
_MAX_CONSECUTIVE_PARSE_FAILURES = 5
HEARTBEAT_PING_IDLE_SECONDS = 15.0
HEARTBEAT_PONG_TIMEOUT_SECONDS = 10.0
HEARTBEAT_RX_STALL_SECONDS = 30.0
WATCHDOG_TICK_SECONDS = 1.0
SUSPEND_GAP_SECONDS = 10.0


def encode_deriv_json(value: object) -> str:
    """Encode outbound payloads while preserving Decimal values as JSON numbers."""

    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError("Deriv JSON decimal must be finite")
        return format(value, "f")
    if isinstance(value, float):
        raise TypeError("Deriv JSON payloads must not use binary floating-point numbers")
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, Mapping):
        items: list[str] = []
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError("Deriv JSON object keys must be strings")
            items.append(f"{json.dumps(key, ensure_ascii=False)}:{encode_deriv_json(item)}")
        return "{" + ",".join(items) + "}"
    if isinstance(value, (list, tuple)):
        return "[" + ",".join(encode_deriv_json(item) for item in value) + "]"
    raise TypeError(f"unsupported Deriv JSON value: {type(value).__name__}")


class DerivReadTransport(Protocol):
    def request(
        self,
        operation: DerivOperation,
        payload: Mapping[str, object],
        *,
        timeout: float,
    ) -> dict[str, object]: ...

    def receive(self, *, timeout: float) -> dict[str, object] | None: ...

    def receive_account(self, *, timeout: float) -> dict[str, object] | None: ...

    def receive_contract(self, *, timeout: float) -> dict[str, object] | None: ...

    def receive_proposal(self, *, timeout: float) -> dict[str, object] | None: ...

    def health_snapshot(self) -> TransportHealthSnapshot: ...

    def reconnect(self) -> None: ...

    def close(self) -> None: ...


@dataclass(frozen=True, slots=True)
class ReadOnlyRetryPolicy:
    max_attempts: int = 3
    base_delay_seconds: float = 0.05
    max_delay_seconds: float = 0.5

    def __post_init__(self) -> None:
        if self.max_attempts <= 0 or self.base_delay_seconds <= 0:
            raise ValueError("read-only retry policy is invalid")
        if self.max_delay_seconds < self.base_delay_seconds:
            raise ValueError("read-only retry upper bound is invalid")

    def delay(self, attempt: int, jitter: Callable[[float], float]) -> float:
        ceiling = min(self.base_delay_seconds * (2 ** max(0, attempt - 1)), self.max_delay_seconds)
        return float(min(self.max_delay_seconds, ceiling + jitter(ceiling * 0.2)))


class TransportRouteResult(StrEnum):
    DELIVERED = "DELIVERED"
    DROPPED_BACKPRESSURE = "DROPPED_BACKPRESSURE"
    DROPPED_UNKNOWN_TYPE = "DROPPED_UNKNOWN_TYPE"
    DELIVERED_RESPONSE = "DELIVERED_RESPONSE"


@dataclass(frozen=True, slots=True)
class TransportHealthSnapshot:
    ticks_dropped_total: int
    balance_dropped_total: int
    contract_events_overflow_total: int
    proposal_events_overflow_total: int
    unknown_msg_type_total: int
    parse_failures_total: int
    consecutive_parse_failures: int
    last_drop_monotonic: float | None
    duplicate_response_total: int = 0
    unsigned_event_total: int = 0
    last_rx_age_seconds: float = 0.0
    pings_sent_total: int = 0
    pongs_received_total: int = 0
    heartbeat_kills_total: int = 0
    last_kill_reason: str | None = None
    last_kill_monotonic: float | None = None
    suspend_detections_total: int = 0


class DerivWebSocketClient:
    """Small TLS-validating Deriv transport; only accepts enumerated read-only operations."""

    def __init__(
        self,
        url: str = PUBLIC_WS_URL,
        *,
        demo_authenticated: bool = False,
        account_type: str | None = None,
        open_timeout: float = 5.0,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._url = validate_deriv_ws_url(
            url,
            expected_account_type=account_type,
            expected_demo=None if account_type is not None else demo_authenticated,
        )
        self._demo_authenticated = demo_authenticated
        self._open_timeout = open_timeout
        self._monotonic = monotonic
        self._connection: ClientConnection | None = None
        self._send_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._failure_lock = threading.RLock()
        self._pending: dict[int, queue.Queue[dict[str, object] | BaseException]] = {}
        self._stream_events: queue.Queue[dict[str, object] | BaseException] = queue.Queue(
            maxsize=128
        )
        self._account_events: queue.Queue[dict[str, object] | BaseException] = queue.Queue(
            maxsize=32
        )
        self._contract_events: queue.Queue[dict[str, object] | BaseException] = queue.Queue(
            maxsize=256
        )
        self._proposal_events: queue.Queue[dict[str, object] | BaseException] = queue.Queue(
            maxsize=256
        )
        self._health_lock = threading.Lock()
        self._ticks_dropped_total = 0
        self._balance_dropped_total = 0
        self._contract_events_overflow_total = 0
        self._proposal_events_overflow_total = 0
        self._unknown_msg_type_total = 0
        self._parse_failures_total = 0
        self._consecutive_parse_failures = 0
        self._last_drop_monotonic: float | None = None
        self._duplicate_response_total = 0
        self._unsigned_event_total = 0
        now = self._monotonic()
        self._last_rx_monotonic = now
        self._pending_ping_deadline: float | None = None
        self._last_watchdog_tick = now
        self._pings_sent_total = 0
        self._pongs_received_total = 0
        self._heartbeat_kills_total = 0
        self._last_kill_reason: str | None = None
        self._last_kill_monotonic: float | None = None
        self._suspend_detections_total = 0
        self._failure_recorded = False
        self._reader_stop = threading.Event()
        self._reader: threading.Thread | None = None
        self._watchdog: threading.Thread | None = None
        self._reader_error: BaseException | None = None
        self._next_request_id = 1

    def reconnect(self) -> None:
        self.close()
        try:
            connection = connect(self._url, open_timeout=self._open_timeout)
        except (OSError, TimeoutError, WebSocketException) as exc:
            raise DerivWorkerError(
                DerivErrorCategory.NETWORK_ERROR,
                "DERIV_NETWORK_ERROR",
            ) from exc
        with self._state_lock:
            self._connection = connection
            self._reader_error = None
            self._reader_stop.clear()
            self._pending.clear()
        now = self._monotonic()
        with self._health_lock:
            self._consecutive_parse_failures = 0
            self._last_rx_monotonic = now
            self._pending_ping_deadline = None
            self._last_watchdog_tick = now
        with self._failure_lock:
            self._failure_recorded = False
        self._clear_stream_events()
        self._clear_account_events()
        self._clear_contract_events()
        self._clear_proposal_events()
        self._reader = threading.Thread(
            target=self._reader_loop,
            args=(connection,),
            name="deriv-ws-reader",
            daemon=True,
        )
        self._watchdog = threading.Thread(
            target=self._watchdog_loop,
            name="deriv-ws-watchdog",
            daemon=True,
        )
        self._reader.start()
        self._watchdog.start()

    def request(
        self,
        operation: DerivOperation,
        payload: Mapping[str, object],
        *,
        timeout: float,
    ) -> dict[str, object]:
        validate_read_only_request(
            operation,
            payload,
            demo_authenticated=self._demo_authenticated,
        )
        if timeout <= 0:
            raise ValueError("Deriv request timeout must be positive")
        with self._state_lock:
            connection = self._connection
            error = self._reader_error
        if connection is None:
            if error is not None:
                raise self._as_worker_error(error)
            self.reconnect()
            with self._state_lock:
                connection = self._connection
        if connection is None:
            raise AssertionError("Deriv connection was not created")
        request_id = self._next_request_id
        self._next_request_id += 1
        response_queue: queue.Queue[dict[str, object] | BaseException] = queue.Queue(maxsize=1)
        with self._state_lock:
            self._pending[request_id] = response_queue
        request = {**payload, "req_id": request_id}
        try:
            with self._send_lock:
                connection.send(encode_deriv_json(request))
            item = response_queue.get(timeout=timeout)
        except queue.Empty as exc:
            raise DerivWorkerError(
                DerivErrorCategory.NETWORK_ERROR,
                "DERIV_REQUEST_TIMEOUT",
            ) from exc
        except DerivWorkerError:
            raise
        except (OSError, TimeoutError, WebSocketException) as exc:
            self._fail_reader(
                DerivWorkerError(DerivErrorCategory.NETWORK_ERROR, "DERIV_NETWORK_ERROR")
            )
            raise DerivWorkerError(
                DerivErrorCategory.NETWORK_ERROR,
                "DERIV_NETWORK_ERROR",
            ) from exc
        finally:
            with self._state_lock:
                self._pending.pop(request_id, None)
        if isinstance(item, BaseException):
            raise self._as_worker_error(item)
        return item

    def receive(self, *, timeout: float) -> dict[str, object] | None:
        if timeout <= 0:
            raise ValueError("Deriv stream receive timeout must be positive")
        try:
            item = self._stream_events.get(timeout=timeout)
        except queue.Empty:
            return None
        if isinstance(item, BaseException):
            raise self._as_worker_error(item)
        return item

    def receive_account(self, *, timeout: float) -> dict[str, object] | None:
        if timeout < 0:
            raise ValueError("Deriv account receive timeout cannot be negative")
        try:
            item = self._account_events.get(timeout=timeout)
        except queue.Empty:
            return None
        if isinstance(item, BaseException):
            raise self._as_worker_error(item)
        return item

    def receive_contract(self, *, timeout: float) -> dict[str, object] | None:
        if timeout < 0:
            raise ValueError("Deriv contract receive timeout cannot be negative")
        try:
            item = self._contract_events.get(timeout=timeout)
        except queue.Empty:
            return None
        if isinstance(item, BaseException):
            raise self._as_worker_error(item)
        return item

    def receive_proposal(self, *, timeout: float) -> dict[str, object] | None:
        if timeout < 0:
            raise ValueError("Deriv proposal receive timeout cannot be negative")
        try:
            item = self._proposal_events.get(timeout=timeout)
        except queue.Empty:
            return None
        if isinstance(item, BaseException):
            raise self._as_worker_error(item)
        return item

    def health_snapshot(self) -> TransportHealthSnapshot:
        now = self._monotonic()
        with self._health_lock:
            return TransportHealthSnapshot(
                ticks_dropped_total=self._ticks_dropped_total,
                balance_dropped_total=self._balance_dropped_total,
                contract_events_overflow_total=self._contract_events_overflow_total,
                proposal_events_overflow_total=self._proposal_events_overflow_total,
                unknown_msg_type_total=self._unknown_msg_type_total,
                parse_failures_total=self._parse_failures_total,
                consecutive_parse_failures=self._consecutive_parse_failures,
                last_drop_monotonic=self._last_drop_monotonic,
                duplicate_response_total=self._duplicate_response_total,
                unsigned_event_total=self._unsigned_event_total,
                last_rx_age_seconds=max(0.0, now - self._last_rx_monotonic),
                pings_sent_total=self._pings_sent_total,
                pongs_received_total=self._pongs_received_total,
                heartbeat_kills_total=self._heartbeat_kills_total,
                last_kill_reason=self._last_kill_reason,
                last_kill_monotonic=self._last_kill_monotonic,
                suspend_detections_total=self._suspend_detections_total,
            )

    def _reader_loop(self, connection: ClientConnection) -> None:
        while not self._reader_stop.is_set():
            try:
                raw = connection.recv(timeout=0.2)
            except TimeoutError:
                continue
            except (OSError, WebSocketException) as exc:
                if not self._reader_stop.is_set():
                    self._fail_reader(exc)
                return
            self._record_received_frame()
            if not isinstance(raw, str):
                if self._record_parse_failure():
                    self._fail_reader(self._schema_error())
                    return
                continue
            try:
                response = parse_deriv_json(raw)
            except DerivWorkerError:
                if self._record_parse_failure():
                    self._fail_reader(self._schema_error())
                    return
                continue
            with self._health_lock:
                self._consecutive_parse_failures = 0
            self._route_response(response)

    def _route_response(self, response: dict[str, object]) -> TransportRouteResult:
        request_id = response.get("req_id")
        if isinstance(request_id, int):
            with self._state_lock:
                response_queue = self._pending.get(request_id)
            if response_queue is not None:
                try:
                    response_queue.put_nowait(response)
                except queue.Full:
                    self._record_drop("duplicate_response")
                return TransportRouteResult.DELIVERED_RESPONSE
        msg_type = response.get("msg_type")
        if msg_type == "tick":
            if not isinstance(response.get("subscription"), dict):
                self._record_drop("unsigned_event")
                return TransportRouteResult.DROPPED_UNKNOWN_TYPE
            dropped = self._put_latest(self._stream_events, response)
            if dropped:
                self._record_drop("tick")
                return TransportRouteResult.DROPPED_BACKPRESSURE
            return TransportRouteResult.DELIVERED
        if msg_type == "balance":
            if not isinstance(response.get("subscription"), dict):
                self._record_drop("unsigned_event")
                return TransportRouteResult.DROPPED_UNKNOWN_TYPE
            dropped = self._put_latest(self._account_events, response)
            if dropped:
                self._record_drop("balance")
                return TransportRouteResult.DROPPED_BACKPRESSURE
            return TransportRouteResult.DELIVERED
        if msg_type == "proposal_open_contract":
            try:
                self._contract_events.put_nowait(response)
            except queue.Full:
                try:
                    self._contract_events.put(response, timeout=1.0)
                except queue.Full:
                    self._record_drop("contract_event")
                    return TransportRouteResult.DROPPED_BACKPRESSURE
            return TransportRouteResult.DELIVERED
        if msg_type == "proposal":
            if not isinstance(response.get("subscription"), dict):
                self._record_drop("unsigned_event")
                return TransportRouteResult.DROPPED_UNKNOWN_TYPE
            try:
                self._proposal_events.put_nowait(response)
            except queue.Full:
                try:
                    self._proposal_events.put(response, timeout=1.0)
                except queue.Full:
                    self._record_drop("proposal_event")
                    return TransportRouteResult.DROPPED_BACKPRESSURE
            return TransportRouteResult.DELIVERED
        if msg_type in {"ping", "pong"}:
            if msg_type == "pong":
                with self._health_lock:
                    self._pongs_received_total += 1
            return TransportRouteResult.DELIVERED
        if msg_type == "error":
            error = response.get("error")
            code = error.get("code") if isinstance(error, dict) else None
            _LOGGER.info("ignored unpaired Deriv error code=%s", self._safe_log_value(code))
            return TransportRouteResult.DELIVERED
        self._record_drop("unknown_msg_type")
        _LOGGER.debug("ignored Deriv msg_type=%s", self._safe_log_value(msg_type))
        return TransportRouteResult.DROPPED_UNKNOWN_TYPE

    def _fail_reader(self, exc: BaseException, *, close_connection: bool = True) -> None:
        with self._failure_lock:
            if self._failure_recorded:
                return
            self._failure_recorded = True
            with self._state_lock:
                self._reader_error = exc
                connection = self._connection
                self._connection = None
                pending = tuple(self._pending.values())
                self._pending.clear()
            for response_queue in pending:
                with suppress(queue.Full):
                    response_queue.put_nowait(exc)
            self._force_queue_error(self._stream_events, exc)
            self._force_queue_error(self._account_events, exc)
            self._force_queue_error(self._contract_events, exc)
            self._force_queue_error(self._proposal_events, exc)
            if close_connection and connection is not None:
                with suppress(Exception):
                    connection.close()

    @staticmethod
    def _as_worker_error(exc: BaseException) -> DerivWorkerError:
        if isinstance(exc, DerivWorkerError):
            return exc
        return DerivWorkerError(DerivErrorCategory.NETWORK_ERROR, "DERIV_NETWORK_ERROR")

    def close(self) -> None:
        self._reader_stop.set()
        with self._state_lock:
            connection = self._connection
            self._connection = None
            pending = tuple(self._pending.values())
            self._pending.clear()
        for response_queue in pending:
            with suppress(queue.Full):
                response_queue.put_nowait(
                    DerivWorkerError(DerivErrorCategory.NETWORK_ERROR, "DERIV_NETWORK_ERROR")
                )
        if connection is not None:
            connection.close()
        reader = self._reader
        self._reader = None
        watchdog = self._watchdog
        self._watchdog = None
        if reader is not None and reader is not threading.current_thread():
            reader.join(timeout=1.0)
        if watchdog is not None and watchdog is not threading.current_thread():
            watchdog.join(timeout=1.0)
        self._clear_stream_events()
        self._clear_account_events()
        self._clear_contract_events()
        self._clear_proposal_events()

    def _clear_stream_events(self) -> None:
        while True:
            try:
                self._stream_events.get_nowait()
            except queue.Empty:
                return

    def _clear_account_events(self) -> None:
        while True:
            try:
                self._account_events.get_nowait()
            except queue.Empty:
                return

    def _clear_contract_events(self) -> None:
        while True:
            try:
                self._contract_events.get_nowait()
            except queue.Empty:
                return

    def _clear_proposal_events(self) -> None:
        while True:
            try:
                self._proposal_events.get_nowait()
            except queue.Empty:
                return

    def _record_parse_failure(self) -> bool:
        with self._health_lock:
            self._parse_failures_total += 1
            self._consecutive_parse_failures += 1
            self._last_drop_monotonic = self._monotonic()
            return self._consecutive_parse_failures >= _MAX_CONSECUTIVE_PARSE_FAILURES

    def _record_drop(self, kind: str) -> None:
        with self._health_lock:
            if kind == "tick":
                self._ticks_dropped_total += 1
            elif kind == "balance":
                self._balance_dropped_total += 1
            elif kind == "contract_event":
                self._contract_events_overflow_total += 1
            elif kind == "proposal_event":
                self._proposal_events_overflow_total += 1
            elif kind == "unknown_msg_type":
                self._unknown_msg_type_total += 1
            elif kind == "duplicate_response":
                self._duplicate_response_total += 1
            elif kind == "unsigned_event":
                self._unsigned_event_total += 1
            else:
                raise AssertionError("unknown transport health counter")
            self._last_drop_monotonic = self._monotonic()

    def _record_received_frame(self) -> None:
        with self._health_lock:
            self._last_rx_monotonic = self._monotonic()
            self._pending_ping_deadline = None

    def _watchdog_loop(self) -> None:
        while not self._reader_stop.wait(WATCHDOG_TICK_SECONDS):
            if not self._watchdog_iteration():
                return

    def _watchdog_iteration(self, now: float | None = None) -> bool:
        if self._reader_stop.is_set():
            return False
        with self._failure_lock:
            if self._failure_recorded:
                return False
        observed = self._monotonic() if now is None else now
        with self._health_lock:
            last_tick = self._last_watchdog_tick
            self._last_watchdog_tick = observed
            last_rx = self._last_rx_monotonic
            pending_deadline = self._pending_ping_deadline
        if monotonic_gap_exceeds(
            observed,
            last_tick,
            max_gap_seconds=SUSPEND_GAP_SECONDS,
        ):
            with self._health_lock:
                self._suspend_detections_total += 1
            self._kill_connection("system_suspend_detected")
            return False
        if observed - last_rx > HEARTBEAT_RX_STALL_SECONDS:
            self._kill_connection("rx_stall")
            return False
        if pending_deadline is not None and observed > pending_deadline:
            self._kill_connection("pong_timeout")
            return False
        should_ping = observed - last_rx > HEARTBEAT_PING_IDLE_SECONDS and pending_deadline is None
        if should_ping:
            return self._send_watchdog_ping(observed)
        return True

    def _send_watchdog_ping(self, now: float) -> bool:
        payload = {"ping": 1}
        validate_read_only_request(
            DerivOperation.PING,
            payload,
            demo_authenticated=self._demo_authenticated,
        )
        with self._state_lock:
            connection = self._connection
        if connection is None:
            self._kill_connection("ping_send_failed")
            return False
        try:
            with self._send_lock:
                with self._health_lock:
                    self._pending_ping_deadline = now + HEARTBEAT_PONG_TIMEOUT_SECONDS
                connection.send(encode_deriv_json(payload))
        except (OSError, TimeoutError, WebSocketException):
            self._kill_connection("ping_send_failed")
            return False
        with self._health_lock:
            self._pings_sent_total += 1
        return True

    def _kill_connection(self, reason: str) -> None:
        if self._reader_stop.is_set():
            return
        with self._failure_lock:
            if self._reader_stop.is_set() or self._failure_recorded:
                return
            now = self._monotonic()
            with self._health_lock:
                rx_age = max(0.0, now - self._last_rx_monotonic)
                self._heartbeat_kills_total += 1
                self._last_kill_reason = reason
                self._last_kill_monotonic = now
            _LOGGER.warning(
                "Deriv heartbeat killed connection reason=%s rx_age_seconds=%.3f",
                self._safe_log_value(reason),
                rx_age,
            )
            with self._state_lock:
                connection = self._connection
            if connection is not None:
                self._abort_connection(connection)
            self._fail_reader(
                DerivWorkerError(
                    DerivErrorCategory.NETWORK_ERROR,
                    "DERIV_HEARTBEAT_TIMEOUT",
                ),
                close_connection=False,
            )

    @staticmethod
    def _abort_connection(connection: ClientConnection) -> None:
        abort = getattr(connection, "abort", None)
        if callable(abort):
            try:
                abort()
            except Exception:
                pass
            else:
                return
        raw_socket = getattr(connection, "socket", None)
        close_socket = getattr(raw_socket, "close", None)
        if callable(close_socket):
            try:
                close_socket()
            except Exception:
                pass
            else:
                return

        def close_without_blocking_shutdown() -> None:
            with suppress(Exception):
                connection.close()

        closer = threading.Thread(
            target=close_without_blocking_shutdown,
            name="deriv-ws-abort-fallback",
            daemon=True,
        )
        closer.start()
        closer.join(timeout=0.2)

    @staticmethod
    def _put_latest(
        target: queue.Queue[dict[str, object] | BaseException],
        response: dict[str, object],
    ) -> bool:
        dropped = False
        while True:
            try:
                target.put_nowait(response)
                return dropped
            except queue.Full:
                try:
                    target.get_nowait()
                except queue.Empty:
                    continue
                dropped = True

    @staticmethod
    def _force_queue_error(
        target: queue.Queue[dict[str, object] | BaseException],
        exc: BaseException,
    ) -> None:
        while True:
            try:
                target.get_nowait()
            except queue.Empty:
                break
        while True:
            try:
                target.put_nowait(exc)
                return
            except queue.Full:
                with suppress(queue.Empty):
                    target.get_nowait()

    @staticmethod
    def _safe_log_value(value: object) -> str:
        if not isinstance(value, str):
            return "<missing>"
        sanitized = "".join(
            character for character in value[:64] if character.isalnum() or character in "_-."
        )
        return sanitized or "<invalid>"

    @staticmethod
    def _schema_error() -> DerivWorkerError:
        return DerivWorkerError(
            DerivErrorCategory.SCHEMA_INCOMPATIBLE,
            "DERIV_SCHEMA_INCOMPATIBLE",
        )
