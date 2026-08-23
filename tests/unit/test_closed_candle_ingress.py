from __future__ import annotations

from typing import cast

from apps.core import CoreCandlePipeline, StrategyEntryPipeline, StrategyPipelineResult
from packages.domain.models import Broker
from packages.market_data import (
    CandleEnvelope,
    CandleIngress,
    CandleIngressStatus,
    FakeCandleSource,
    InMemoryCandleStore,
)


def envelope_for(
    close_time_ms: int,
    *,
    closed: bool = True,
    source_event_id: str | None = None,
) -> CandleEnvelope:
    return CandleEnvelope(
        broker=Broker.DERIV,
        symbol="EURUSD",
        timeframe_seconds=60,
        open_time_ms=close_time_ms - 60_000,
        close_time_ms=close_time_ms,
        open_units=100_000,
        high_units=102_000,
        low_units=98_000,
        close_units=101_000,
        price_scale=1_000,
        is_closed=closed,
        source="FAKE_CANDLE_SOURCE",
        source_event_id=source_event_id or f"event-{close_time_ms}",
        source_timestamp_ms=close_time_ms,
        received_timestamp_ms=close_time_ms + 5,
    )


def test_open_candle_never_enters_store() -> None:
    store = InMemoryCandleStore(max_candles=4)
    result = CandleIngress(store).ingest(envelope_for(120_000, closed=False))

    assert result.status is CandleIngressStatus.INVALID
    assert result.reason_code == "MARKET_CANDLE_NOT_CLOSED_OR_INVALID"
    assert store.range((Broker.DERIV, "EURUSD", 60)) == ()


def test_canonical_id_deduplicates_redelivery_with_different_source_event() -> None:
    store = InMemoryCandleStore(max_candles=4)
    ingress = CandleIngress(store)

    first = ingress.ingest(envelope_for(120_000, source_event_id="delivery-a"))
    duplicate = ingress.ingest(envelope_for(120_000, source_event_id="delivery-b"))

    assert first.status is CandleIngressStatus.ACCEPTED
    assert duplicate.status is CandleIngressStatus.DUPLICATE
    assert first.candle is not None and duplicate.candle is not None
    assert first.candle.candle_id == duplicate.candle.candle_id
    assert len(store.range(first.candle.series_key)) == 1


def test_close_order_is_preserved_and_older_candle_is_rejected() -> None:
    store = InMemoryCandleStore(max_candles=4)
    ingress = CandleIngress(store)
    first = ingress.ingest(envelope_for(120_000))
    second = ingress.ingest(envelope_for(180_000))
    older = ingress.ingest(envelope_for(60_000))

    assert first.status is CandleIngressStatus.ACCEPTED
    assert second.status is CandleIngressStatus.ACCEPTED
    assert older.status is CandleIngressStatus.OUT_OF_ORDER
    assert first.candle is not None
    assert [item.close_time_ms for item in store.range(first.candle.series_key)] == [
        120_000,
        180_000,
    ]


def test_gap_and_external_float_payload_fail_closed() -> None:
    ingress = CandleIngress(InMemoryCandleStore(max_candles=4))
    assert ingress.ingest(envelope_for(120_000)).status is CandleIngressStatus.ACCEPTED
    gap = ingress.ingest(envelope_for(240_000))
    assert gap.status is CandleIngressStatus.INVALID
    assert gap.reason_code == "CANDLE_GAP"

    payload: dict[str, object] = {
        "broker": "DERIV",
        "symbol": "EURUSD",
        "timeframe_seconds": 60,
        "open_time_ms": 240_000,
        "close_time_ms": 300_000,
        "open_units": 100_000.0,
        "high_units": 102_000,
        "low_units": 98_000,
        "close_units": 101_000,
        "price_scale": 1_000,
        "is_closed": True,
        "source": "FAKE_CANDLE_SOURCE",
        "source_event_id": "invalid-float",
        "source_timestamp_ms": 300_000,
        "received_timestamp_ms": 300_005,
    }
    assert ingress.ingest_external(payload).status is CandleIngressStatus.INVALID


def test_fake_source_is_bounded_and_preserves_delivery_order() -> None:
    candles = (envelope_for(120_000), envelope_for(180_000))
    assert FakeCandleSource(candles, max_candles=2).read() == candles


class _RecordingEntryPipeline:
    def __init__(self) -> None:
        self.calls = 0

    def process_batch(self, *_args: object, **_kwargs: object) -> StrategyPipelineResult:
        self.calls += 1
        return StrategyPipelineResult((), (), (), ())


def test_open_and_duplicate_deliveries_do_not_reach_strategy_pipeline() -> None:
    entries = _RecordingEntryPipeline()
    core = CoreCandlePipeline(
        CandleIngress(InMemoryCandleStore(max_candles=4)),
        cast(StrategyEntryPipeline, entries),
    )

    core.process(envelope_for(120_000, closed=False), (), (), entitled_packs=frozenset())
    core.process(
        envelope_for(120_000, source_event_id="closed-a"), (), (), entitled_packs=frozenset()
    )
    core.process(
        envelope_for(120_000, source_event_id="closed-b"), (), (), entitled_packs=frozenset()
    )

    assert entries.calls == 1
