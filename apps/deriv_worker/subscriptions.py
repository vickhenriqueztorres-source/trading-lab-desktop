from __future__ import annotations

import queue
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum

from packages.domain.market import MarketDataHealthState, MarketTick


class SubscriptionState(StrEnum):
    ACTIVE = "ACTIVE"
    RESTORING = "RESTORING"
    CANCELLED = "CANCELLED"


@dataclass(frozen=True, slots=True)
class SubscriptionRecord:
    subscription_id: str
    request_id: str
    correlation_id: str
    symbol: str
    stream_type: str
    created_at: datetime
    state: SubscriptionState


class SubscriptionManager:
    def __init__(
        self,
        *,
        queue_size: int = 128,
        max_tick_gap_seconds: int = 2,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        if queue_size <= 0 or max_tick_gap_seconds <= 0:
            raise ValueError("subscription limits must be positive")
        self._records: dict[str, SubscriptionRecord] = {}
        self._symbol_index: dict[str, str] = {}
        self._ticks: queue.Queue[MarketTick] = queue.Queue(maxsize=queue_size)
        self._seen: set[tuple[object, ...]] = set()
        self._last_epoch: dict[str, int] = {}
        self._max_tick_gap_seconds = max_tick_gap_seconds
        self._monotonic = monotonic
        self._last_valid_tick_at: float | None = None
        self.health = MarketDataHealthState.WARMING_UP
        self.ticks_received = 0
        self.ticks_dropped = 0
        self.duplicates = 0
        self.late_ticks = 0

    @property
    def logical_count(self) -> int:
        return sum(
            record.state is not SubscriptionState.CANCELLED for record in self._records.values()
        )

    def register(
        self,
        tick: MarketTick,
        request_id: str,
        correlation_id: str | None = None,
    ) -> None:
        previous_id = self._symbol_index.get(tick.broker_symbol)
        if previous_id is not None and previous_id != tick.subscription_id:
            self._records.pop(previous_id, None)
        event_correlation_id = correlation_id or request_id
        self._records[tick.subscription_id] = SubscriptionRecord(
            subscription_id=tick.subscription_id,
            request_id=request_id,
            correlation_id=event_correlation_id,
            symbol=tick.broker_symbol,
            stream_type="ticks",
            created_at=tick.received_at,
            state=SubscriptionState.ACTIVE,
        )
        self._symbol_index[tick.broker_symbol] = tick.subscription_id

    def ingest(self, tick: MarketTick) -> MarketDataHealthState:
        self.ticks_received += 1
        identity = tuple(tick.identity)
        if identity in self._seen:
            self.duplicates += 1
            return self.health
        previous = self._last_epoch.get(tick.broker_symbol)
        if previous is not None and tick.epoch < previous:
            self.late_ticks += 1
            return self.health
        if previous is not None and tick.epoch > previous + self._max_tick_gap_seconds:
            self.health = MarketDataHealthState.GAPPED
        self._seen.add(identity)
        self._last_epoch[tick.broker_symbol] = tick.epoch
        self._last_valid_tick_at = self._monotonic()
        try:
            self._ticks.put_nowait(tick)
        except queue.Full:
            self.ticks_dropped += 1
            self.health = MarketDataHealthState.GAPPED
            return self.health
        if self.health is MarketDataHealthState.WARMING_UP:
            self.health = MarketDataHealthState.HEALTHY
        return self.health

    def evaluate_staleness(self, threshold_seconds: float) -> MarketDataHealthState:
        if threshold_seconds <= 0:
            raise ValueError("stale threshold must be positive")
        if self._last_valid_tick_at is None:
            return self.health
        if self._monotonic() - self._last_valid_tick_at > threshold_seconds:
            self.health = MarketDataHealthState.STALE
        return self.health

    def next_tick(self, timeout: float = 0.0) -> MarketTick:
        return self._ticks.get(timeout=timeout)

    def cancel(self, subscription_id: str) -> None:
        record = self._records.get(subscription_id)
        if record is None:
            return
        self._records[subscription_id] = replace(record, state=SubscriptionState.CANCELLED)

    def mark_restoring(self) -> tuple[SubscriptionRecord, ...]:
        active: list[SubscriptionRecord] = []
        for subscription_id, record in tuple(self._records.items()):
            if record.state is SubscriptionState.CANCELLED:
                continue
            restoring = replace(record, state=SubscriptionState.RESTORING)
            self._records[subscription_id] = restoring
            active.append(restoring)
        self.health = MarketDataHealthState.DISCONNECTED
        return tuple(active)

    def symbols_to_restore(self) -> tuple[str, ...]:
        return tuple(
            sorted(
                record.symbol
                for record in self._records.values()
                if record.state is SubscriptionState.RESTORING
            )
        )

    def correlation_for(self, subscription_id: str) -> str:
        record = self._records.get(subscription_id)
        return record.correlation_id if record is not None else f"market:{subscription_id}"
