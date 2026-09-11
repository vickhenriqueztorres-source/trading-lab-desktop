from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from apps.core.iqoption_connection_safety import IQOptionMessageBudget
from apps.core.iqoption_series_hub import (
    IQOptionSeriesHub,
    IQOptionSeriesKey,
    IQOptionSeriesPriority,
    IQOptionSeriesReason,
    IQOptionSeriesRequest,
)
from packages.domain.market import MarketCandle
from packages.domain.models import Broker


class FakeClient:
    def __init__(self, candles: list[MarketCandle]) -> None:
        self.candles = candles
        self.requests: list[tuple[str, str, int, int]] = []

    def market_history(
        self,
        symbol: str,
        *,
        style: str,
        count: int,
        timeframe_seconds: int,
    ) -> tuple[list[object], list[MarketCandle]]:
        self.requests.append((symbol, style, count, timeframe_seconds))
        return [], list(self.candles)


def _key(
    asset: str = "EURUSD-OTC",
    *,
    generation: str = "g1",
    timeframe_seconds: int = 60,
) -> IQOptionSeriesKey:
    return IQOptionSeriesKey(
        broker=Broker.IQ_OPTION,
        account_id="IQOPTION_PRACTICE",
        product="BINARY_OPTION",
        generation=generation,
        asset=asset,
        timeframe_seconds=timeframe_seconds,
    )


def _candle(
    index: int,
    *,
    asset: str = "EURUSD-OTC",
    timeframe_seconds: int = 60,
    closed: bool = True,
    close: str = "1.1000",
) -> MarketCandle:
    open_time = datetime(2026, 1, 1, tzinfo=UTC) + timedelta(seconds=index * timeframe_seconds)
    close_price = Decimal(close) + Decimal(index) * Decimal("0.0001")
    return MarketCandle(
        broker=Broker.IQ_OPTION,
        broker_symbol=asset,
        timeframe_seconds=timeframe_seconds,
        open_time=open_time,
        close_time=open_time + timedelta(seconds=timeframe_seconds),
        open=close_price,
        high=close_price + Decimal("0.0002"),
        low=close_price - Decimal("0.0002"),
        close=close_price,
        is_closed=closed,
        tick_volume=10 + index,
    )


def _hub(
    *,
    now: datetime | None = None,
    budget_limit: int = 60,
    max_fetch_count: int = 1_000,
    max_queue_size: int = 128,
) -> IQOptionSeriesHub:
    return IQOptionSeriesHub(
        message_budget=IQOptionMessageBudget(limit=budget_limit),
        monotonic=lambda: 10.0,
        utc_clock=lambda: now or datetime(2026, 1, 1, 0, 10, 5, tzinfo=UTC),
        max_fetch_count=max_fetch_count,
        max_queue_size=max_queue_size,
    )


def test_same_series_same_close_deduplicates_fetch_for_many_recipes() -> None:
    key = _key()
    client = FakeClient([_candle(index) for index in range(20)])
    hub = _hub()

    first = hub.snapshot(client=client, key=key, warmup_required=15, close_epoch=123)
    second = hub.snapshot(client=client, key=key, warmup_required=15, close_epoch=123)
    third = hub.snapshot(client=client, key=key, warmup_required=10, close_epoch=123)

    assert first.ok
    assert second.ok
    assert third.ok
    assert client.requests == [("EURUSD-OTC", "candles", 18, 60)]
    assert hub.stats.fetches_sent == 1
    assert hub.stats.dedup_hits == 2


def test_required_recovery_candle_is_not_hidden_by_boundary_cache() -> None:
    key = _key()
    target = _candle(10).close_time
    stale_client = FakeClient([_candle(index) for index in range(10)])
    fresh_client = FakeClient([_candle(index) for index in range(11)])
    hub = _hub(now=target + timedelta(seconds=1))

    missing = hub.snapshot(
        client=stale_client,
        key=key,
        warmup_required=2,
        close_epoch=100,
        priority=IQOptionSeriesPriority.RECOVERY,
        required_close_time=target,
    )
    found = hub.snapshot(
        client=fresh_client,
        key=key,
        warmup_required=2,
        close_epoch=100,
        priority=IQOptionSeriesPriority.RECOVERY,
        required_close_time=target,
    )

    assert missing.reason is IQOptionSeriesReason.TARGET_CANDLE_UNAVAILABLE
    assert found.ok
    assert found.snapshot is not None
    assert found.snapshot.candles[-1].close_time == target
    assert len(stale_client.requests) == 1
    assert len(fresh_client.requests) == 1


def test_exact_asset_key_keeps_otc_and_spot_distinct() -> None:
    otc_client = FakeClient([_candle(index, asset="EURUSD-OTC") for index in range(5)])
    spot_client = FakeClient([_candle(index, asset="EURUSD") for index in range(5)])
    hub = _hub()

    assert hub.snapshot(client=otc_client, key=_key("EURUSD-OTC"), warmup_required=3).ok
    assert hub.snapshot(client=spot_client, key=_key("EURUSD"), warmup_required=3).ok

    assert otc_client.requests == [("EURUSD-OTC", "candles", 6, 60)]
    assert spot_client.requests == [("EURUSD", "candles", 6, 60)]
    assert hub.stats.fetches_sent == 2


def test_reconnect_generation_invalidates_old_series_snapshot() -> None:
    hub = _hub()
    first_client = FakeClient([_candle(index, close="1.1000") for index in range(5)])
    second_client = FakeClient([_candle(index, close="1.2000") for index in range(5)])

    first = hub.snapshot(
        client=first_client,
        key=_key(generation="worker-1"),
        warmup_required=3,
        close_epoch=1,
    )
    second = hub.snapshot(
        client=second_client,
        key=_key(generation="worker-2"),
        warmup_required=3,
        close_epoch=1,
    )

    assert first.snapshot is not None
    assert second.snapshot is not None
    assert first.snapshot.candles[-1].close != second.snapshot.candles[-1].close
    assert len(first_client.requests) == 1
    assert len(second_client.requests) == 1


def test_partial_incompatible_duplicate_out_of_order_gap_and_correction_are_recorded() -> None:
    key = _key()
    duplicate = _candle(4)
    corrected = _candle(4, close="1.3000")
    client = FakeClient(
        [
            _candle(5),
            _candle(3, closed=False),
            _candle(1, asset="EURUSD"),
            _candle(1),
            duplicate,
            duplicate,
            corrected,
        ]
    )
    hub = _hub()

    outcome = hub.snapshot(client=client, key=key, warmup_required=2, close_epoch=10)

    assert outcome.snapshot is not None
    assert [item.close_time for item in outcome.snapshot.candles] == sorted(
        item.close_time for item in outcome.snapshot.candles
    )
    assert outcome.snapshot.gaps_detected == 2
    assert outcome.snapshot.corrections_detected == 1
    assert hub.stats.partial_rejected == 1
    assert hub.stats.incompatible_rejected == 1
    assert hub.stats.duplicates_rejected == 1
    assert hub.stats.out_of_order_batches == 1


def test_historical_correction_on_next_fetch_replaces_snapshot() -> None:
    key = _key()
    hub = _hub()
    first_client = FakeClient([_candle(index, close="1.1000") for index in range(5)])
    second_client = FakeClient([_candle(index, close="1.1000") for index in range(4)])
    second_client.candles.append(_candle(4, close="1.4000"))

    assert hub.snapshot(client=first_client, key=key, warmup_required=3, close_epoch=1).ok
    outcome = hub.snapshot(client=second_client, key=key, warmup_required=3, close_epoch=2)

    assert outcome.snapshot is not None
    assert outcome.snapshot.corrections_detected == 1
    assert outcome.snapshot.candles[-1].close == Decimal("1.4004")


def test_warmup_above_capacity_rejects_explicitly_without_silent_120_cut() -> None:
    client = FakeClient([_candle(index) for index in range(120)])
    hub = _hub(max_fetch_count=50)

    outcome = hub.snapshot(client=client, key=_key(), warmup_required=60, close_epoch=1)

    assert outcome.reason is IQOptionSeriesReason.WARMUP_CAPACITY_EXCEEDED
    assert client.requests == []
    assert hub.stats.warmup_capacity_rejected == 1


def test_message_budget_fail_closed_without_fetch() -> None:
    client = FakeClient([_candle(index) for index in range(5)])
    hub = _hub(budget_limit=1)

    assert hub.snapshot(client=client, key=_key(), warmup_required=3, close_epoch=1).ok
    blocked = hub.snapshot(client=client, key=_key("GBPUSD-OTC"), warmup_required=3, close_epoch=1)

    assert blocked.reason is IQOptionSeriesReason.MESSAGE_BUDGET_EXHAUSTED
    assert len(client.requests) == 1
    assert hub.stats.budget_denied == 1


def test_full_queue_is_reported_without_fetch() -> None:
    client = FakeClient([_candle(index) for index in range(5)])
    hub = _hub(max_queue_size=0)

    outcome = hub.snapshot(client=client, key=_key(), warmup_required=3, close_epoch=1)

    assert outcome.reason is IQOptionSeriesReason.SERIES_QUEUE_FULL
    assert client.requests == []
    assert hub.stats.queue_full == 1


def test_scheduler_fairness_for_16_assets_and_3_timeframes() -> None:
    symbols = tuple(f"ASSET{index}-OTC" for index in range(16))
    requests = tuple(
        IQOptionSeriesRequest(
            key=_key(symbol, timeframe_seconds=timeframe),
            warmup_required=20,
            close_epoch=100,
            priority=IQOptionSeriesPriority.STEADY,
        )
        for symbol in symbols
        for timeframe in (60, 300, 900)
    )
    hub = _hub(max_queue_size=len(requests))

    first_round = hub.schedule(requests)
    second_round = hub.schedule(tuple(reversed(requests)))

    assert len(first_round) == 48
    assert len({(item.key.asset, item.key.timeframe_seconds) for item in first_round}) == 48
    assert len(second_round) == 48
    assert len({(item.key.asset, item.key.timeframe_seconds) for item in second_round}) == 48


def test_series_hub_does_not_expose_financial_submission_api() -> None:
    forbidden = {"buy", "sell", "submit", "submit_order", "order", "place_order"}

    assert not forbidden.intersection(dir(IQOptionSeriesHub))
