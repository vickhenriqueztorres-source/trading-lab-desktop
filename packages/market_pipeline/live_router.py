from __future__ import annotations

import queue
from dataclasses import dataclass
from typing import Protocol

from packages.domain.market import MarketTick
from packages.domain.models import Broker
from packages.market_pipeline.live import LiveTickSource
from packages.market_pipeline.models import MarketSeriesId
from packages.observability import EventSink, NullEventSink


class SharedMarketTickRoutingError(RuntimeError):
    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


class SharedMarketTickBackpressure(SharedMarketTickRoutingError):
    pass


class SharedLiveTickSource(Protocol):
    def subscribe_market_ticks(self, symbol: str) -> MarketTick: ...

    def receive_market_tick(self, timeout: float) -> MarketTick | None: ...

    def unsubscribe_market_ticks(self, subscription_id: str) -> bool: ...


@dataclass(frozen=True, slots=True)
class RoutedMarketSeriesSnapshot:
    series_id: MarketSeriesId
    subscribed: bool
    queued_ticks: int
    received_ticks: int
    routed_ticks: int
    dropped_ticks: int


@dataclass(frozen=True, slots=True)
class SharedMarketTickRouterSnapshot:
    broker: Broker
    registered_series: int
    active_subscriptions: int
    source_ticks_received: int
    source_timeouts: int
    unroutable_ticks: int
    backpressure_count: int
    series: tuple[RoutedMarketSeriesSnapshot, ...]


@dataclass(slots=True)
class _RouteState:
    series_id: MarketSeriesId
    ticks: queue.Queue[MarketTick]
    subscription_id: str | None = None
    received_ticks: int = 0
    routed_ticks: int = 0
    dropped_ticks: int = 0


class SharedMarketTickRouter:
    """Demultiplexes one broker stream into bounded per-series live sources."""

    def __init__(
        self,
        broker: Broker,
        source: SharedLiveTickSource,
        *,
        max_series: int = 16,
        per_series_queue_size: int = 128,
        events: EventSink | None = None,
    ) -> None:
        if max_series <= 0 or per_series_queue_size <= 0:
            raise ValueError("shared tick router limits must be positive")
        self._broker = broker
        self._source = source
        self._max_series = max_series
        self._queue_size = per_series_queue_size
        self._events = events or NullEventSink()
        self._routes: dict[MarketSeriesId, _RouteState] = {}
        self._subscriptions: dict[str, MarketSeriesId] = {}
        self._source_ticks_received = 0
        self._source_timeouts = 0
        self._unroutable_ticks = 0
        self._backpressure_count = 0

    def register(self, series_id: MarketSeriesId) -> LiveTickSource:
        if series_id.broker is not self._broker:
            raise ValueError("shared tick router broker does not match series")
        if series_id in self._routes:
            raise ValueError("market series already registered in shared tick router")
        if len(self._routes) >= self._max_series:
            raise ValueError("shared tick router series limit exceeded")
        self._routes[series_id] = _RouteState(
            series_id=series_id,
            ticks=queue.Queue(maxsize=self._queue_size),
        )
        return RoutedLiveTickSource(self, series_id)

    def snapshot(self) -> SharedMarketTickRouterSnapshot:
        series = tuple(
            RoutedMarketSeriesSnapshot(
                series_id=route.series_id,
                subscribed=route.subscription_id is not None,
                queued_ticks=route.ticks.qsize(),
                received_ticks=route.received_ticks,
                routed_ticks=route.routed_ticks,
                dropped_ticks=route.dropped_ticks,
            )
            for route in self._routes.values()
        )
        return SharedMarketTickRouterSnapshot(
            broker=self._broker,
            registered_series=len(self._routes),
            active_subscriptions=len(self._subscriptions),
            source_ticks_received=self._source_ticks_received,
            source_timeouts=self._source_timeouts,
            unroutable_ticks=self._unroutable_ticks,
            backpressure_count=self._backpressure_count,
            series=series,
        )

    def subscribe(self, series_id: MarketSeriesId, symbol: str) -> MarketTick:
        route = self._route(series_id)
        if symbol != series_id.broker_symbol:
            raise SharedMarketTickRoutingError(
                "MD_SCOPE_MISMATCH",
                "subscription symbol does not match market series",
            )
        initial = self._source.subscribe_market_ticks(symbol)
        self._validate_tick(initial, series_id)
        existing = self._subscriptions.get(initial.subscription_id)
        if existing is not None and existing != series_id:
            raise SharedMarketTickRoutingError(
                "MD_SCOPE_MISMATCH",
                "subscription id is already owned by another market series",
            )
        if route.subscription_id is not None:
            self._subscriptions.pop(route.subscription_id, None)
        route.subscription_id = initial.subscription_id
        route.received_ticks += 1
        self._subscriptions[initial.subscription_id] = series_id
        self._events.emit(
            "shared_market_tick_series_subscribed",
            series_id=series_id.key,
            subscription_id=initial.subscription_id,
        )
        return initial

    def receive(self, series_id: MarketSeriesId, timeout: float) -> MarketTick | None:
        if timeout <= 0:
            raise ValueError("shared tick receive timeout must be positive")
        route = self._route(series_id)
        queued = self._get_queued(route)
        if queued is not None:
            return queued
        tick = self._source.receive_market_tick(timeout)
        if tick is None:
            self._source_timeouts += 1
            return None
        self._source_ticks_received += 1
        target = self._target_for(tick)
        if target == series_id:
            route.received_ticks += 1
            return tick
        self._enqueue(target, tick)
        return None

    def unsubscribe(self, series_id: MarketSeriesId, subscription_id: str) -> bool:
        route = self._route(series_id)
        if route.subscription_id != subscription_id:
            raise SharedMarketTickRoutingError(
                "MD_SCOPE_MISMATCH",
                "unsubscribe attempted against a foreign subscription",
            )
        try:
            return self._source.unsubscribe_market_ticks(subscription_id)
        finally:
            self._subscriptions.pop(subscription_id, None)
            route.subscription_id = None
            self._drain(route)
            self._events.emit(
                "shared_market_tick_series_unsubscribed",
                series_id=series_id.key,
                subscription_id=subscription_id,
            )

    def _route(self, series_id: MarketSeriesId) -> _RouteState:
        route = self._routes.get(series_id)
        if route is None:
            raise SharedMarketTickRoutingError(
                "MD_SCOPE_MISMATCH",
                "market series is not registered in shared tick router",
            )
        return route

    def _target_for(self, tick: MarketTick) -> MarketSeriesId:
        if tick.broker is not self._broker:
            self._unroutable_ticks += 1
            raise SharedMarketTickRoutingError(
                "MD_SCOPE_MISMATCH",
                "tick broker does not match shared router broker",
            )
        series_id = self._subscriptions.get(tick.subscription_id)
        if series_id is None:
            self._unroutable_ticks += 1
            raise SharedMarketTickRoutingError(
                "MD_SCOPE_MISMATCH",
                "tick subscription is unknown to shared router",
            )
        self._validate_tick(tick, series_id)
        return series_id

    def _validate_tick(self, tick: MarketTick, series_id: MarketSeriesId) -> None:
        if tick.broker is not series_id.broker or tick.broker_symbol != series_id.broker_symbol:
            self._unroutable_ticks += 1
            raise SharedMarketTickRoutingError(
                "MD_SCOPE_MISMATCH",
                "tick does not match the subscribed market series",
            )

    def _enqueue(self, series_id: MarketSeriesId, tick: MarketTick) -> None:
        route = self._route(series_id)
        try:
            route.ticks.put_nowait(tick)
        except queue.Full as exc:
            route.dropped_ticks += 1
            self._backpressure_count += 1
            self._events.emit(
                "shared_market_tick_backpressure",
                reason_code="MD_BACKPRESSURE",
                series_id=series_id.key,
            )
            raise SharedMarketTickBackpressure(
                "MD_BACKPRESSURE",
                "bounded per-series tick queue is full",
            ) from exc
        route.routed_ticks += 1

    @staticmethod
    def _get_queued(route: _RouteState) -> MarketTick | None:
        try:
            tick = route.ticks.get_nowait()
        except queue.Empty:
            return None
        route.received_ticks += 1
        return tick

    @staticmethod
    def _drain(route: _RouteState) -> None:
        while True:
            try:
                route.ticks.get_nowait()
            except queue.Empty:
                return


class RoutedLiveTickSource:
    def __init__(self, router: SharedMarketTickRouter, series_id: MarketSeriesId) -> None:
        self._router = router
        self._series_id = series_id

    def subscribe_market_ticks(self, symbol: str) -> MarketTick:
        return self._router.subscribe(self._series_id, symbol)

    def receive_market_tick(self, timeout: float) -> MarketTick | None:
        return self._router.receive(self._series_id, timeout)

    def unsubscribe_market_ticks(self, subscription_id: str) -> bool:
        return self._router.unsubscribe(self._series_id, subscription_id)
