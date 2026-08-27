from __future__ import annotations

import queue
import random
import time
from collections.abc import Callable, Mapping
from datetime import UTC, datetime

from apps.deriv_worker.mapper import (
    map_active_symbols,
    map_candle_history,
    map_clock,
    map_contracts,
    map_tick,
    map_tick_history,
)
from apps.deriv_worker.request_allowlist import DerivOperation
from apps.deriv_worker.schema import DerivErrorCategory, DerivWorkerError, validate_response
from apps.deriv_worker.subscriptions import SubscriptionManager
from apps.deriv_worker.websocket_client import DerivReadTransport, ReadOnlyRetryPolicy
from packages.domain.market import (
    BrokerCapabilities,
    BrokerClockSnapshot,
    BrokerConnectionMode,
    ContractMetadata,
    MarketCandle,
    MarketDataHealthState,
    MarketSymbol,
    MarketTick,
)
from packages.domain.models import Broker

SUPPORTED_CANDLE_TIMEFRAMES = (
    60,
    120,
    180,
    300,
    600,
    900,
    1800,
    3600,
    7200,
    14400,
    28800,
    86400,
)


class PublicDerivSession:
    def __init__(
        self,
        transport: DerivReadTransport,
        *,
        subscriptions: SubscriptionManager | None = None,
        retry_policy: ReadOnlyRetryPolicy | None = None,
        monotonic: Callable[[], float] = time.monotonic,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
        sleeper: Callable[[float], None] = time.sleep,
        jitter: Callable[[float], float] = lambda ceiling: random.uniform(0.0, ceiling),
        request_timeout: float = 2.0,
    ) -> None:
        self._transport = transport
        self.subscriptions = subscriptions or SubscriptionManager()
        self._retry = retry_policy or ReadOnlyRetryPolicy()
        self._monotonic = monotonic
        self._now = now
        self._sleeper = sleeper
        self._jitter = jitter
        self._request_timeout = request_timeout
        self.health = MarketDataHealthState.DISCONNECTED
        self.messages_received = 0
        self.reconnect_count = 0
        self.schema_errors = 0
        self.last_clock: BrokerClockSnapshot | None = None
        self.symbols: tuple[MarketSymbol, ...] = ()
        self._last_monotonic_observation = self._monotonic()

    @property
    def transport(self) -> DerivReadTransport:
        return self._transport

    @property
    def capabilities(self) -> BrokerCapabilities:
        return BrokerCapabilities(
            broker=Broker.DERIV,
            connection_mode=BrokerConnectionMode.PUBLIC_READ_ONLY,
            authenticated=False,
            can_trade=False,
            supports_ticks=True,
            supports_tick_history=True,
            supports_candles=True,
            supports_active_symbols=True,
            supports_contract_metadata=True,
            supports_server_time=True,
            supported_timeframes=SUPPORTED_CANDLE_TIMEFRAMES,
        )

    def connect(self) -> None:
        self.health = MarketDataHealthState.WARMING_UP
        self._transport.reconnect()
        self._last_monotonic_observation = self._monotonic()
        self.ping()
        self.last_clock = self.clock()
        self.symbols = self.active_symbols()
        self.health = MarketDataHealthState.HEALTHY

    def ping(self) -> float:
        started = self._monotonic()
        response = self._read_request(DerivOperation.PING, {"ping": 1})
        validate_response(response, "ping")
        return self._monotonic() - started

    def clock(self) -> BrokerClockSnapshot:
        started = self._monotonic()
        response = self._read_request(DerivOperation.TIME, {"time": 1})
        duration = self._monotonic() - started
        snapshot = map_clock(response, self._now(), duration)
        self.last_clock = snapshot
        return snapshot

    def active_symbols(self) -> tuple[MarketSymbol, ...]:
        response = self._read_request(
            DerivOperation.ACTIVE_SYMBOLS,
            {"active_symbols": "brief"},
        )
        symbols = map_active_symbols(response, self._now())
        self.symbols = symbols
        return symbols

    def contracts_list(self) -> tuple[str, ...]:
        response = self._read_request(
            DerivOperation.CONTRACTS_LIST,
            {"contracts_list": 1},
        )
        validate_response(response, "contracts_list")
        raw = response.get("contracts_list")
        if not isinstance(raw, list) or not all(isinstance(item, str) and item for item in raw):
            self.schema_errors += 1
            self.health = MarketDataHealthState.INCOMPATIBLE
            raise DerivWorkerError(
                DerivErrorCategory.SCHEMA_INCOMPATIBLE,
                "DERIV_SCHEMA_INCOMPATIBLE",
            )
        return tuple(raw)

    def contracts_for(self, symbol: str) -> tuple[ContractMetadata, ...]:
        response = self._read_request(
            DerivOperation.CONTRACTS_FOR,
            {"contracts_for": symbol},
        )
        return map_contracts(response, symbol)

    def subscribe_ticks(self, symbol: str, *, correlation_id: str | None = None) -> MarketTick:
        response = self._read_request(
            DerivOperation.TICKS,
            {"ticks": symbol, "subscribe": 1},
        )
        tick = map_tick(response, self._now())
        if tick.broker_symbol != symbol:
            raise DerivWorkerError(
                DerivErrorCategory.SUBSCRIPTION_ERROR,
                "DERIV_SUBSCRIPTION_IDENTITY_MISMATCH",
            )
        self.subscriptions.register(tick, f"ticks:{symbol}", correlation_id=correlation_id)
        self.subscriptions.ingest(tick)
        return tick

    def ingest_tick(self, payload: Mapping[str, object]) -> MarketTick:
        tick = map_tick(payload, self._now())
        health = self.subscriptions.ingest(tick)
        if health is MarketDataHealthState.GAPPED:
            self.health = MarketDataHealthState.GAPPED
        return tick

    def next_queued_tick(self, *, timeout: float) -> MarketTick | None:
        try:
            return self.subscriptions.next_tick(timeout=timeout)
        except queue.Empty:
            return None

    def drain_stream_once(self, *, timeout: float) -> bool:
        payload = self._transport.receive(timeout=timeout)
        if payload is None:
            return False
        self.messages_received += 1
        self.ingest_tick(payload)
        return True

    def next_stream_tick(self, *, timeout: float) -> MarketTick | None:
        queued = self.next_queued_tick(timeout=0.0)
        if queued is not None:
            return queued
        self.drain_stream_once(timeout=timeout)
        return self.next_queued_tick(timeout=0.0)

    def event_correlation_id(self, tick: MarketTick) -> str:
        return self.subscriptions.correlation_for(tick.subscription_id)

    def detect_suspension(self, *, max_gap_seconds: float) -> bool:
        if max_gap_seconds <= 0:
            raise ValueError("suspension gap threshold must be positive")
        now = self._monotonic()
        elapsed = now - self._last_monotonic_observation
        self._last_monotonic_observation = now
        if elapsed <= max_gap_seconds:
            return False
        self.subscriptions.mark_restoring()
        self.health = MarketDataHealthState.STALE
        return True

    def tick_history(
        self,
        symbol: str,
        *,
        count: int = 100,
        end_epoch: int | None = None,
    ) -> tuple[MarketTick, ...]:
        if count <= 0 or count > 1000:
            raise ValueError("tick history count is outside the bounded range")
        if end_epoch is not None and end_epoch <= 0:
            raise ValueError("tick history end epoch must be positive")
        response = self._read_request(
            DerivOperation.TICKS_HISTORY,
            {
                "ticks_history": symbol,
                "count": count,
                "end": "latest" if end_epoch is None else end_epoch,
                "style": "ticks",
            },
        )
        unique: dict[tuple[object, ...], MarketTick] = {}
        for tick in map_tick_history(response, symbol, self._now()):
            unique[tuple(tick.identity)] = tick
        return tuple(sorted(unique.values(), key=lambda item: item.epoch))

    def candle_history(
        self,
        symbol: str,
        timeframe_seconds: int,
        *,
        count: int = 100,
        end_epoch: int | None = None,
    ) -> tuple[MarketCandle, ...]:
        if timeframe_seconds not in SUPPORTED_CANDLE_TIMEFRAMES:
            raise ValueError("unsupported Deriv candle timeframe")
        if end_epoch is not None and end_epoch <= 0:
            raise ValueError("candle history end epoch must be positive")
        response = self._read_request(
            DerivOperation.TICKS_HISTORY,
            {
                "ticks_history": symbol,
                "count": count,
                "end": "latest" if end_epoch is None else end_epoch,
                "style": "candles",
                "granularity": timeframe_seconds,
            },
        )
        return map_candle_history(response, symbol, timeframe_seconds, self._now())

    def unsubscribe(self, subscription_id: str) -> bool:
        response = self._read_request(
            DerivOperation.FORGET,
            {"forget": subscription_id},
        )
        validate_response(response, "forget")
        forgotten = response.get("forget")
        if forgotten not in (0, 1):
            raise DerivWorkerError(
                DerivErrorCategory.SCHEMA_INCOMPATIBLE,
                "DERIV_SCHEMA_INCOMPATIBLE",
            )
        self.subscriptions.cancel(subscription_id)
        return forgotten == 1

    def reconnect(self) -> None:
        self.health = MarketDataHealthState.DISCONNECTED
        self.subscriptions.mark_restoring()
        self.reconnect_count += 1
        self._transport.reconnect()
        self._last_monotonic_observation = self._monotonic()
        self.ping()
        self.last_clock = self.clock()
        self.symbols = self.active_symbols()
        symbols = self.subscriptions.symbols_to_restore()
        for symbol in symbols:
            self.tick_history(symbol, count=100)
            self.subscribe_ticks(symbol)
        self.health = MarketDataHealthState.HEALTHY

    def close(self) -> None:
        try:
            for symbol_id in tuple(
                record.subscription_id for record in self.subscriptions.mark_restoring()
            ):
                self.unsubscribe(symbol_id)
        finally:
            self._transport.close()
            self.health = MarketDataHealthState.DISCONNECTED

    def _read_request(
        self, operation: DerivOperation, payload: Mapping[str, object]
    ) -> dict[str, object]:
        last_error: DerivWorkerError | None = None
        for attempt in range(1, self._retry.max_attempts + 1):
            try:
                response = self._transport.request(
                    operation,
                    payload,
                    timeout=self._request_timeout,
                )
                self.messages_received += 1
                return response
            except DerivWorkerError as exc:
                last_error = exc
                if exc.category is DerivErrorCategory.SCHEMA_INCOMPATIBLE:
                    self.schema_errors += 1
                    self.health = MarketDataHealthState.INCOMPATIBLE
                    raise
                if exc.category not in {
                    DerivErrorCategory.NETWORK_ERROR,
                    DerivErrorCategory.RATE_LIMITED,
                    DerivErrorCategory.SERVER_ERROR,
                }:
                    raise
                self.health = MarketDataHealthState.DISCONNECTED
                if attempt == self._retry.max_attempts:
                    break
                self._sleeper(self._retry.delay(attempt, self._jitter))
                self._transport.reconnect()
        if last_error is None:
            raise AssertionError("bounded read-only retry did not run")
        raise last_error
