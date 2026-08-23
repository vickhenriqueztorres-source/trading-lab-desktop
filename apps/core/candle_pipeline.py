from __future__ import annotations

from dataclasses import dataclass

from apps.core.strategy_pipeline import (
    EntryPlan,
    StrategyBatchItem,
    StrategyEntryPipeline,
    StrategyPipelineResult,
)
from packages.domain.market import MarketCandle
from packages.market_data import (
    CandleEnvelope,
    CandleIngress,
    CandleIngressResult,
    CandleIngressStatus,
    datetime_from_epoch_ms,
)
from packages.strategies import RuntimeContext


def market_candle_from_closed(candle: object) -> MarketCandle:
    from packages.market_data import ClosedCandle

    if not isinstance(candle, ClosedCandle):
        raise TypeError("closed candle is required")
    open_price, high_price, low_price, close_price = candle.decimal_prices()
    return MarketCandle(
        broker=candle.broker,
        broker_symbol=candle.symbol,
        timeframe_seconds=candle.timeframe_seconds,
        open_time=datetime_from_epoch_ms(candle.open_time_ms),
        close_time=datetime_from_epoch_ms(candle.close_time_ms),
        open=open_price,
        high=high_price,
        low=low_price,
        close=close_price,
        is_closed=True,
    )


@dataclass(frozen=True, slots=True)
class CoreCandleResult:
    ingress: CandleIngressResult
    pipeline: StrategyPipelineResult | None


class CoreCandlePipeline:
    """The only bridge from validated market input to Strategy Runtime."""

    def __init__(self, ingress: CandleIngress, entries: StrategyEntryPipeline) -> None:
        self._ingress = ingress
        self._entries = entries

    def process(
        self,
        envelope: CandleEnvelope,
        contexts: tuple[RuntimeContext, ...],
        plans: tuple[EntryPlan, ...],
        *,
        entitled_packs: frozenset[str],
    ) -> CoreCandleResult:
        accepted = self._ingress.ingest(envelope)
        candle = accepted.candle
        if accepted.status is not CandleIngressStatus.ACCEPTED or candle is None:
            return CoreCandleResult(accepted, None)
        for context in contexts:
            if (
                context.broker is not candle.broker
                or context.symbol != candle.symbol
                or context.timeframe_seconds != candle.timeframe_seconds
            ):
                raise ValueError("runtime context does not match accepted candle series")
        market_candle = market_candle_from_closed(candle)
        pipeline = self._entries.process_batch(
            tuple(StrategyBatchItem(context, market_candle) for context in contexts),
            plans,
            entitled_packs=entitled_packs,
            now=market_candle.close_time,
        )
        return CoreCandleResult(accepted, pipeline)
