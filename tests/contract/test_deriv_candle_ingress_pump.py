from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from packages.brokers.deriv import (
    DerivCandleAdapter,
    DerivCandleHistoryPump,
    DerivCandleIngressBridge,
    DerivCandlePumpError,
)
from packages.domain.market import MarketCandle, MarketHistoryBatch
from packages.domain.models import Broker
from packages.market_data import CandleIngress, InMemoryCandleStore

NOW = datetime(2026, 8, 20, 12, tzinfo=UTC)


def market_candle(
    index: int,
    *,
    is_closed: bool = True,
    offset_seconds: int | None = None,
) -> MarketCandle:
    opened = datetime(2026, 8, 20, tzinfo=UTC) + timedelta(
        seconds=offset_seconds if offset_seconds is not None else index * 60
    )
    return MarketCandle(
        broker=Broker.DERIV,
        broker_symbol="frxEURUSD",
        timeframe_seconds=60,
        open_time=opened,
        close_time=opened + timedelta(seconds=60),
        open=Decimal("1.08490"),
        high=Decimal("1.08520"),
        low=Decimal("1.08480"),
        close=Decimal("1.08501"),
        is_closed=is_closed,
    )


class FakeHistorySource:
    def __init__(self, candles: tuple[MarketCandle, ...]) -> None:
        self.candles = candles
        self.calls = 0
        self.failure: RuntimeError | None = None

    def market_history_batch(
        self,
        symbol: str,
        *,
        style: str,
        count: int = 100,
        timeframe_seconds: int | None = None,
        end_epoch: int | None = None,
    ) -> MarketHistoryBatch:
        assert symbol == "frxEURUSD"
        assert style == "candles"
        assert timeframe_seconds == 60
        assert end_epoch is None
        self.calls += 1
        if self.failure is not None:
            raise self.failure
        return MarketHistoryBatch(
            response_message_id=f"response-{self.calls}",
            correlation_id=f"correlation-{self.calls}",
            causation_id=f"request-{self.calls}",
            ticks=(),
            candles=self.candles,
        )


def pump_for(source: FakeHistorySource, *, max_batch_size: int = 4) -> DerivCandleHistoryPump:
    return DerivCandleHistoryPump(
        source,
        DerivCandleIngressBridge(
            DerivCandleAdapter(frozenset({"frxEURUSD"})),
            CandleIngress(InMemoryCandleStore(max_candles=8)),
        ),
        max_batch_size=max_batch_size,
        now=lambda: NOW,
    )


def test_closed_partial_and_duplicate_history_are_explicit() -> None:
    source = FakeHistorySource((market_candle(0), market_candle(1, is_closed=False)))
    pump = pump_for(source)

    first = pump.backfill("frxEURUSD", 60, count=2)
    second = pump.backfill("frxEURUSD", 60, count=2)

    assert first.received_count == 2
    assert first.response_message_id == "response-1"
    assert first.correlation_id == "correlation-1"
    assert first.causation_id == "request-1"
    assert first.accepted_count == 1
    assert first.partial_count == 1
    assert first.has_quality_failure is False
    assert second.duplicate_count == 1
    assert second.partial_count == 1


def test_gap_and_batch_backpressure_fail_closed_without_hidden_retry() -> None:
    source = FakeHistorySource((market_candle(0), market_candle(2, offset_seconds=120)))
    pump = pump_for(source, max_batch_size=2)
    report = pump.backfill("frxEURUSD", 60, count=2)
    assert report.accepted_count == 1
    assert report.has_quality_failure is True

    with pytest.raises(DerivCandlePumpError) as saturated:
        pump.backfill("frxEURUSD", 60, count=3)
    assert saturated.value.reason_code == "DERIV_CANDLE_BACKPRESSURE"
    assert source.calls == 1

    source.failure = RuntimeError("FAKE_DISCONNECT")
    with pytest.raises(RuntimeError, match="FAKE_DISCONNECT"):
        pump.backfill("frxEURUSD", 60, count=2)
    assert source.calls == 2
    source.failure = None
    assert pump.backfill("frxEURUSD", 60, count=2).duplicate_count == 1
    assert source.calls == 3


def test_response_overflow_and_scope_mismatch_are_rejected() -> None:
    overflow = FakeHistorySource((market_candle(0), market_candle(1)))
    with pytest.raises(DerivCandlePumpError) as too_many:
        pump_for(overflow).backfill("frxEURUSD", 60, count=1)
    assert too_many.value.reason_code == "DERIV_CANDLE_BATCH_OVERFLOW"

    wrong_scope = FakeHistorySource(
        (
            MarketCandle(
                broker=Broker.DERIV,
                broker_symbol="R_100",
                timeframe_seconds=60,
                open_time=NOW - timedelta(minutes=2),
                close_time=NOW - timedelta(minutes=1),
                open=Decimal("100"),
                high=Decimal("101"),
                low=Decimal("99"),
                close=Decimal("100"),
                is_closed=True,
            ),
        )
    )
    with pytest.raises(DerivCandlePumpError) as mismatch:
        pump_for(wrong_scope).backfill("frxEURUSD", 60, count=1)
    assert mismatch.value.reason_code == "DERIV_CANDLE_HISTORY_SCOPE_MISMATCH"


def test_pump_source_has_no_strategy_financial_or_worker_dependency() -> None:
    source = Path(__file__).parents[2] / "packages" / "brokers" / "deriv" / "candle_pump.py"
    text = source.read_text(encoding="utf-8").casefold()
    forbidden = (
        "riskledger",
        "portfolioallocator",
        "orderintent",
        "submit_order",
        "state.db",
        "apps.deriv_worker",
        "websocket",
        "credential",
        "token",
    )
    assert not any(value in text for value in forbidden)
