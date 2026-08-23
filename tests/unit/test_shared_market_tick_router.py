from __future__ import annotations

import queue
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from packages.domain.market import MarketTick
from packages.domain.models import Broker
from packages.market_pipeline import (
    MarketSeriesId,
    SharedMarketTickBackpressure,
    SharedMarketTickRouter,
    SharedMarketTickRoutingError,
)


def series(symbol: str, timeframe: int = 60) -> MarketSeriesId:
    return MarketSeriesId(
        Broker.DERIV,
        symbol,
        symbol,
        "OPTION",
        timeframe,
    )


def tick(
    symbol: str,
    subscription_id: str,
    *,
    epoch: int = 1_800_000_000,
    broker: Broker = Broker.DERIV,
) -> MarketTick:
    return MarketTick(
        broker=broker,
        broker_symbol=symbol,
        epoch=epoch,
        quote=Decimal("1.2345"),
        received_at=datetime.fromtimestamp(epoch, UTC),
        subscription_id=subscription_id,
        source="FAKE_SHARED_STREAM",
    )


@dataclass
class FakeSharedSource:
    ticks: queue.Queue[MarketTick]

    def __init__(self) -> None:
        self.ticks = queue.Queue()
        self.subscriptions: dict[str, str] = {}
        self.unsubscribed: list[str] = []
        self.next_id = 0

    def subscribe_market_ticks(self, symbol: str) -> MarketTick:
        self.next_id += 1
        subscription_id = f"sub-{self.next_id}"
        self.subscriptions[subscription_id] = symbol
        return tick(symbol, subscription_id)

    def receive_market_tick(self, timeout: float) -> MarketTick | None:
        try:
            return self.ticks.get(timeout=timeout)
        except queue.Empty:
            return None

    def unsubscribe_market_ticks(self, subscription_id: str) -> bool:
        self.unsubscribed.append(subscription_id)
        self.subscriptions.pop(subscription_id, None)
        return True


def test_shared_router_routes_single_ipc_event_queue_into_registered_series() -> None:
    source = FakeSharedSource()
    eurusd = series("frxEURUSD")
    gbpusd = series("frxGBPUSD")
    router = SharedMarketTickRouter(Broker.DERIV, source, per_series_queue_size=4)
    eur_source = router.register(eurusd)
    gbp_source = router.register(gbpusd)

    first_eur = eur_source.subscribe_market_ticks("frxEURUSD")
    first_gbp = gbp_source.subscribe_market_ticks("frxGBPUSD")
    assert first_eur.broker_symbol == "frxEURUSD"
    assert first_gbp.broker_symbol == "frxGBPUSD"

    source.ticks.put(tick("frxGBPUSD", first_gbp.subscription_id, epoch=1_800_000_001))
    source.ticks.put(tick("frxEURUSD", first_eur.subscription_id, epoch=1_800_000_002))

    assert eur_source.receive_market_tick(0.01) is None
    routed_gbp = gbp_source.receive_market_tick(0.01)
    assert routed_gbp is not None
    assert routed_gbp.broker_symbol == "frxGBPUSD"
    routed_eur = eur_source.receive_market_tick(0.01)
    assert routed_eur is not None
    assert routed_eur.broker_symbol == "frxEURUSD"

    snapshot = router.snapshot()
    assert snapshot.registered_series == 2
    assert snapshot.active_subscriptions == 2
    assert snapshot.source_ticks_received == 2
    assert snapshot.source_timeouts == 0
    assert snapshot.backpressure_count == 0
    assert sum(item.received_ticks for item in snapshot.series) == 4


def test_shared_router_fails_closed_on_unknown_subscription_or_scope_mismatch() -> None:
    source = FakeSharedSource()
    eurusd = series("frxEURUSD")
    router = SharedMarketTickRouter(Broker.DERIV, source)
    eur_source = router.register(eurusd)
    initial = eur_source.subscribe_market_ticks("frxEURUSD")

    source.ticks.put(tick("frxEURUSD", "foreign-sub"))
    with pytest.raises(SharedMarketTickRoutingError, match="unknown"):
        eur_source.receive_market_tick(0.01)
    assert router.snapshot().unroutable_ticks == 1

    source.ticks.put(tick("frxGBPUSD", initial.subscription_id))
    with pytest.raises(SharedMarketTickRoutingError, match="does not match"):
        eur_source.receive_market_tick(0.01)
    assert router.snapshot().unroutable_ticks == 2


def test_shared_router_applies_bounded_per_series_backpressure() -> None:
    source = FakeSharedSource()
    eurusd = series("frxEURUSD")
    gbpusd = series("frxGBPUSD")
    router = SharedMarketTickRouter(Broker.DERIV, source, per_series_queue_size=1)
    eur_source = router.register(eurusd)
    gbp_source = router.register(gbpusd)
    eur_initial = eur_source.subscribe_market_ticks("frxEURUSD")
    gbp_initial = gbp_source.subscribe_market_ticks("frxGBPUSD")

    source.ticks.put(tick("frxGBPUSD", gbp_initial.subscription_id, epoch=1_800_000_001))
    source.ticks.put(tick("frxGBPUSD", gbp_initial.subscription_id, epoch=1_800_000_002))
    assert eur_source.receive_market_tick(0.01) is None
    with pytest.raises(SharedMarketTickBackpressure):
        eur_source.receive_market_tick(0.01)

    snapshot = router.snapshot()
    assert snapshot.backpressure_count == 1
    gbp_snapshot = next(item for item in snapshot.series if item.series_id == gbpusd)
    assert gbp_snapshot.queued_ticks == 1
    assert gbp_snapshot.dropped_ticks == 1
    assert eur_source.unsubscribe_market_ticks(eur_initial.subscription_id)
    assert source.unsubscribed == [eur_initial.subscription_id]


def test_shared_router_rejects_wrong_broker_and_registration_limits() -> None:
    source = FakeSharedSource()
    router = SharedMarketTickRouter(Broker.DERIV, source, max_series=1)
    router.register(series("frxEURUSD"))

    with pytest.raises(ValueError, match="limit"):
        router.register(series("frxGBPUSD"))
    with pytest.raises(ValueError, match="broker"):
        SharedMarketTickRouter(Broker.DERIV, source).register(
            MarketSeriesId(Broker.IQ_OPTION, "EURUSD", "EURUSD", "OPTION", 60)
        )
