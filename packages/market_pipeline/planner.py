from __future__ import annotations

from dataclasses import dataclass

from packages.market_pipeline.models import MarketSeriesId, TrustedClosedHorizon


@dataclass(frozen=True, slots=True)
class BackfillPlan:
    series_id: MarketSeriesId
    generation: int
    start_close_epoch_ms: int
    end_close_epoch_ms: int
    count: int
    overlap_candles: int
    trusted_closed_horizon_ms: int

    @property
    def end_epoch_seconds(self) -> int:
        return self.end_close_epoch_ms // 1_000


class BackfillPlanner:
    def __init__(
        self,
        *,
        max_candles_per_batch: int = 500,
        backfill_overlap_candles: int = 2,
    ) -> None:
        if max_candles_per_batch <= 0:
            raise ValueError("backfill batch size must be positive")
        if not 1 <= backfill_overlap_candles <= max_candles_per_batch:
            raise ValueError("backfill overlap is outside the bounded batch")
        self.max_candles_per_batch = max_candles_per_batch
        self.backfill_overlap_candles = backfill_overlap_candles

    def plan(
        self,
        series_id: MarketSeriesId,
        *,
        generation: int,
        horizon: TrustedClosedHorizon,
        last_durable_close_ms: int | None,
        durable_closed_candles: int,
        required_closed_candles: int,
        force_overlap: bool = False,
    ) -> BackfillPlan | None:
        if generation < 0 or durable_closed_candles < 0 or required_closed_candles <= 0:
            raise ValueError("backfill planner state is invalid")
        timeframe_ms = series_id.timeframe_seconds * 1_000
        trusted = horizon.close_epoch_ms
        if last_durable_close_ms is not None:
            if (trusted - last_durable_close_ms) % timeframe_ms != 0:
                raise ValueError("durable boundary and trusted horizon have different phases")
            missing = max(0, (trusted - last_durable_close_ms) // timeframe_ms)
            warmup_missing = max(0, required_closed_candles - durable_closed_candles)
            if missing == 0 and warmup_missing == 0:
                if not force_overlap:
                    return None
                count = min(self.backfill_overlap_candles, durable_closed_candles)
                if count <= 0:
                    return None
                end_close = last_durable_close_ms
                start_close = end_close - (count - 1) * timeframe_ms
                return BackfillPlan(
                    series_id=series_id,
                    generation=generation,
                    start_close_epoch_ms=start_close,
                    end_close_epoch_ms=end_close,
                    count=count,
                    overlap_candles=count,
                    trusted_closed_horizon_ms=trusted,
                )
            advance = min(
                max(missing, warmup_missing),
                self.max_candles_per_batch - self.backfill_overlap_candles,
            )
            if advance <= 0:
                raise ValueError("backfill batch cannot advance beyond overlap")
            count = advance + self.backfill_overlap_candles
            end_close = min(trusted, last_durable_close_ms + advance * timeframe_ms)
            start_close = end_close - (count - 1) * timeframe_ms
        else:
            available = trusted // timeframe_ms
            if available <= 0:
                return None
            target = min(required_closed_candles, available)
            count = min(target, self.max_candles_per_batch)
            target_start = trusted - (target - 1) * timeframe_ms
            end_close = target_start + (count - 1) * timeframe_ms
            start_close = target_start
        return BackfillPlan(
            series_id=series_id,
            generation=generation,
            start_close_epoch_ms=start_close,
            end_close_epoch_ms=end_close,
            count=count,
            overlap_candles=self.backfill_overlap_candles,
            trusted_closed_horizon_ms=trusted,
        )
