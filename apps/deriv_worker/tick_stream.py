from __future__ import annotations

import threading
import time
from collections.abc import Callable, Mapping
from datetime import UTC, datetime

from apps.deriv_worker.mapper import map_tick
from apps.deriv_worker.request_allowlist import DerivOperation
from apps.deriv_worker.websocket_client import DerivReadTransport
from packages.domain.market import MarketTick
from packages.market_data import DigitFrequencySnapshot, TickRingBuffer

_MAX_PLAUSIBLE_TRANSPORT_LATENCY_SECONDS = 60.0


class DerivTickStream:
    """Single-symbol, bounded DIGITDIFF statistics on the Deriv worker hot path."""

    def __init__(
        self,
        transport: DerivReadTransport,
        *,
        capacity: int = 500,
        request_timeout: float = 2.0,
        monotonic_clock: Callable[[], float] = time.perf_counter,
        wall_clock: Callable[[], float] = time.time,
    ) -> None:
        if request_timeout <= 0:
            raise ValueError("tick stream request timeout must be positive")
        self._transport = transport
        self._capacity = capacity
        self._request_timeout = request_timeout
        self._monotonic = monotonic_clock
        self._wall_anchor = wall_clock()
        self._monotonic_anchor = monotonic_clock()
        self._buffer = TickRingBuffer(capacity, monotonic_clock=monotonic_clock)
        self._lock = threading.RLock()
        self._symbol = ""
        self._last_identity: tuple[object, ...] | None = None
        self._last_transport_latency_microseconds = 0

    @property
    def symbol(self) -> str:
        with self._lock:
            return self._symbol

    @property
    def buffer(self) -> TickRingBuffer:
        return self._buffer

    def activate_symbol(self, symbol: str) -> None:
        if not symbol or len(symbol) > 32:
            raise ValueError("Deriv tick symbol is invalid")
        with self._lock:
            if symbol != self._symbol:
                self._symbol = symbol
                self._buffer = TickRingBuffer(
                    self._capacity,
                    monotonic_clock=self._monotonic,
                )
                self._last_transport_latency_microseconds = 0
                self._last_identity = None

    def subscribe(self, symbol: str) -> MarketTick:
        self.activate_symbol(symbol)
        response = self._transport.request(
            DerivOperation.TICKS,
            {"ticks": symbol, "subscribe": 1},
            timeout=self._request_timeout,
        )
        tick = map_tick(response, datetime.now(UTC))
        self.ingest_market_tick(tick)
        return tick

    def process_message(self, payload: Mapping[str, object]) -> MarketTick:
        tick = map_tick(payload, datetime.now(UTC))
        self.ingest_market_tick(tick)
        return tick

    def ingest_market_tick(self, tick: MarketTick) -> DigitFrequencySnapshot:
        with self._lock:
            if not self._symbol:
                self.activate_symbol(tick.broker_symbol)
            if tick.broker_symbol != self._symbol:
                raise ValueError("tick does not match the active DIGITDIFF symbol")
            if tick.identity == self._last_identity:
                return self.snapshot()
            arrived = self._monotonic()
            estimated_arrival_epoch = self._wall_anchor + (arrived - self._monotonic_anchor)
            latency_seconds = estimated_arrival_epoch - tick.epoch
            self._last_transport_latency_microseconds = (
                int(latency_seconds * 1_000_000)
                if 0 <= latency_seconds <= _MAX_PLAUSIBLE_TRANSPORT_LATENCY_SECONDS
                else 0
            )
            self._buffer.push_tick(tick.quote, tick.epoch)
            self._last_identity = tick.identity
            return self.snapshot()

    def snapshot(self) -> DigitFrequencySnapshot:
        with self._lock:
            percentages = self._buffer.get_frequency_percentage()
            counts = self._buffer.frequency_counts
            return DigitFrequencySnapshot(
                symbol=self._symbol or "UNSUBSCRIBED",
                total_ticks=sum(counts),
                frequency_counts=tuple(counts),
                frequency_percentages=tuple(percentages[digit] for digit in range(10)),
                transport_latency_microseconds=self._last_transport_latency_microseconds,
            )
