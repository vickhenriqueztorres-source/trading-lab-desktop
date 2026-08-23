from __future__ import annotations

import json
import queue
import threading
from collections.abc import Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass
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
from apps.deriv_worker.validators import PUBLIC_WS_URL, validate_deriv_ws_url


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


class DerivWebSocketClient:
    """Small TLS-validating Deriv transport; only accepts enumerated read-only operations."""

    def __init__(
        self,
        url: str = PUBLIC_WS_URL,
        *,
        demo_authenticated: bool = False,
        open_timeout: float = 5.0,
    ) -> None:
        self._url = validate_deriv_ws_url(url, expected_demo=demo_authenticated)
        self._demo_authenticated = demo_authenticated
        self._open_timeout = open_timeout
        self._connection: ClientConnection | None = None
        self._send_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._pending: dict[int, queue.Queue[dict[str, object] | BaseException]] = {}
        self._stream_events: queue.Queue[dict[str, object] | BaseException] = queue.Queue(
            maxsize=128
        )
        self._account_events: queue.Queue[dict[str, object] | BaseException] = queue.Queue(
            maxsize=32
        )
        self._contract_events: queue.Queue[dict[str, object] | BaseException] = queue.Queue(
            maxsize=64
        )
        self._reader_stop = threading.Event()
        self._reader: threading.Thread | None = None
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
        self._clear_stream_events()
        self._clear_account_events()
        self._clear_contract_events()
        self._reader = threading.Thread(
            target=self._reader_loop,
            args=(connection,),
            name="deriv-ws-reader",
            daemon=True,
        )
        self._reader.start()

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
                connection.send(json.dumps(request, separators=(",", ":")))
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

    def _reader_loop(self, connection: ClientConnection) -> None:
        try:
            while not self._reader_stop.is_set():
                try:
                    raw = connection.recv(timeout=0.2)
                except TimeoutError:
                    continue
                if not isinstance(raw, str):
                    raise DerivWorkerError(
                        DerivErrorCategory.SCHEMA_INCOMPATIBLE,
                        "DERIV_SCHEMA_INCOMPATIBLE",
                    )
                self._route_response(parse_deriv_json(raw))
        except BaseException as exc:
            if not self._reader_stop.is_set():
                self._fail_reader(exc)

    def _route_response(self, response: dict[str, object]) -> None:
        request_id = response.get("req_id")
        if isinstance(request_id, int):
            with self._state_lock:
                response_queue = self._pending.get(request_id)
            if response_queue is not None:
                response_queue.put_nowait(response)
                return
        if response.get("msg_type") == "tick" and isinstance(response.get("subscription"), dict):
            try:
                self._stream_events.put_nowait(response)
            except queue.Full as exc:
                raise DerivWorkerError(
                    DerivErrorCategory.SUBSCRIPTION_ERROR,
                    "DERIV_MARKET_EVENT_BACKPRESSURE",
                ) from exc
            return
        if response.get("msg_type") == "balance" and isinstance(response.get("subscription"), dict):
            try:
                self._account_events.put_nowait(response)
            except queue.Full as exc:
                raise DerivWorkerError(
                    DerivErrorCategory.SUBSCRIPTION_ERROR,
                    "DERIV_ACCOUNT_EVENT_BACKPRESSURE",
                ) from exc
            return
        if response.get("msg_type") == "proposal_open_contract":
            try:
                self._contract_events.put_nowait(response)
            except queue.Full as exc:
                raise DerivWorkerError(
                    DerivErrorCategory.SUBSCRIPTION_ERROR,
                    "DERIV_CONTRACT_EVENT_BACKPRESSURE",
                ) from exc
            return
        raise DerivWorkerError(
            DerivErrorCategory.SCHEMA_INCOMPATIBLE,
            "DERIV_SCHEMA_INCOMPATIBLE",
        )

    def _fail_reader(self, exc: BaseException) -> None:
        with self._state_lock:
            self._reader_error = exc
            connection = self._connection
            self._connection = None
            pending = tuple(self._pending.values())
            self._pending.clear()
        for response_queue in pending:
            with suppress(queue.Full):
                response_queue.put_nowait(exc)
        with suppress(queue.Full):
            self._stream_events.put_nowait(exc)
        with suppress(queue.Full):
            self._account_events.put_nowait(exc)
        if connection is not None:
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
        if reader is not None and reader is not threading.current_thread():
            reader.join(timeout=1.0)
        self._clear_stream_events()
        self._clear_account_events()
        self._clear_contract_events()

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
