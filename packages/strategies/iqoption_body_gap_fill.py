"""Deterministic HFT10_BodyGapFill signal generator for IQ Option.

Rule:
- Compares bodies (not wicks) of two consecutive candles.
- If separation >= 0.25 * ATR(14):
    - Body gap up (not yet filled) -> PUT (aims for return to midpoint).
    - Body gap down (not yet filled) -> CALL (aims for return to midpoint).
- The state is active for at most 2 candles, and ceases to signal as soon as a
  subsequent candle close reaches or crosses the midpoint.
- Expiry: End of the first candle after signal (duration = 1 minute in M1).
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from packages.domain.market import MarketCandle
from packages.domain.models import Broker, Direction
from packages.strategies.models import RuntimeContext
from packages.strategy_catalog.models import (
    DataRequirement,
    ReleaseStatus,
    RiskClass,
    StrategyManifest,
)

IQOPTION_BODY_GAP_FILL_STRATEGY_ID = "iqoption-body-gap-fill"
IQOPTION_BODY_GAP_FILL_STRATEGY_VERSION = "1.0.0"
IQOPTION_BODY_GAP_FILL_TIMEFRAME_SECONDS = 60
IQOPTION_BODY_GAP_FILL_ATR_PERIOD = 14
IQOPTION_BODY_GAP_FILL_MIN_ATR_RATIO = Decimal("0.25")
IQOPTION_BODY_GAP_FILL_MAX_BARS = 2
IQOPTION_BODY_GAP_FILL_EXPIRY_CANDLES = 1
IQOPTION_BODY_GAP_FILL_WARMUP_CANDLES = 16
IQOPTION_BODY_GAP_FILL_ARTIFACT = b"IQOPTION_BODY_GAP_FILL:ATR14:RATIO0.25:BARS2:EXP1:v1"


@dataclass(frozen=True, slots=True)
class BodyGapFillDecision:
    direction: Direction | None
    atr: Decimal
    gap_separation: Decimal
    gap_midpoint: Decimal
    bars_since_gap: int
    duration_candles: int
    reason_code: str


def calculate_atr14(candles: Sequence[MarketCandle], period: int = 14) -> Decimal:
    """Calculate simple average true range over given period."""
    if len(candles) < period + 1:
        raise ValueError(f"ATR requires at least {period + 1} candles")
    recent = candles[-(period + 1) :]
    true_ranges: list[Decimal] = []
    for prev, curr in zip(recent[:-1], recent[1:], strict=True):
        high_low = curr.high - curr.low
        high_prev_close = abs(curr.high - prev.close)
        low_prev_close = abs(curr.low - prev.close)
        tr = max(high_low, high_prev_close, low_prev_close)
        true_ranges.append(tr)
    return sum(true_ranges) / Decimal(len(true_ranges))


class IQOptionBodyGapFillStrategy:
    """HFT10 Body Gap Fill: ATR-scaled body separation with midpoint return target."""

    def __init__(
        self,
        atr_period: int = IQOPTION_BODY_GAP_FILL_ATR_PERIOD,
        min_atr_ratio: Decimal = IQOPTION_BODY_GAP_FILL_MIN_ATR_RATIO,
        max_bars: int = IQOPTION_BODY_GAP_FILL_MAX_BARS,
        duration_candles: int = IQOPTION_BODY_GAP_FILL_EXPIRY_CANDLES,
    ) -> None:
        if atr_period <= 0:
            raise ValueError("ATR period must be positive")
        if min_atr_ratio <= 0:
            raise ValueError("Minimum ATR ratio must be positive")
        if max_bars <= 0:
            raise ValueError("Max bars must be positive")
        if duration_candles <= 0:
            raise ValueError("Duration candles must be positive")
        self._atr_period = atr_period
        self._min_atr_ratio = min_atr_ratio
        self._max_bars = max_bars
        self._duration_candles = duration_candles

    @property
    def artifact_bytes(self) -> bytes:
        return IQOPTION_BODY_GAP_FILL_ARTIFACT

    @property
    def warmup_required(self) -> int:
        return self._atr_period + 2

    @property
    def duration_candles(self) -> int:
        return self._duration_candles

    def evaluate_decision(
        self,
        candles: Sequence[MarketCandle],
        context: RuntimeContext,
    ) -> BodyGapFillDecision:
        if context.broker is not Broker.IQ_OPTION:
            raise ValueError("Body Gap Fill strategy requires IQ Option")
        if context.timeframe_seconds != IQOPTION_BODY_GAP_FILL_TIMEFRAME_SECONDS:
            raise ValueError("Body Gap Fill strategy requires closed 1-minute candles")
        min_warmup = self.warmup_required
        if len(candles) < min_warmup:
            raise ValueError(
                f"Body Gap Fill strategy still warming up (requires {min_warmup} candles)"
            )
        if any(
            candle.broker is not Broker.IQ_OPTION
            or candle.broker_symbol != context.symbol
            or candle.timeframe_seconds != context.timeframe_seconds
            or not candle.is_closed
            for candle in candles
        ):
            raise ValueError("Body Gap Fill strategy received an invalid candle series")

        atr = calculate_atr14(candles, period=self._atr_period)
        min_sep = self._min_atr_ratio * atr

        c0 = candles[-1]
        c1 = candles[-2]
        c0_min_body = min(c0.open, c0.close)
        c0_max_body = max(c0.open, c0.close)
        c1_min_body = min(c1.open, c1.close)
        c1_max_body = max(c1.open, c1.close)

        if c0_min_body > c1_max_body:
            sep0 = c0_min_body - c1_max_body
            if sep0 >= min_sep:
                midpoint = (c1_max_body + c0_min_body) / Decimal(2)
                if c0.close > midpoint:
                    return BodyGapFillDecision(
                        direction=Direction.PUT,
                        atr=atr,
                        gap_separation=sep0,
                        gap_midpoint=midpoint,
                        bars_since_gap=1,
                        duration_candles=self._duration_candles,
                        reason_code="BODY_GAP_UP_FILL_PUT",
                    )

        if c0_max_body < c1_min_body:
            sep0 = c1_min_body - c0_max_body
            if sep0 >= min_sep:
                midpoint = (c0_max_body + c1_min_body) / Decimal(2)
                if c0.close < midpoint:
                    return BodyGapFillDecision(
                        direction=Direction.CALL,
                        atr=atr,
                        gap_separation=sep0,
                        gap_midpoint=midpoint,
                        bars_since_gap=1,
                        duration_candles=self._duration_candles,
                        reason_code="BODY_GAP_DOWN_FILL_CALL",
                    )

        if len(candles) >= self._atr_period + 3:
            c2 = candles[-3]
            c2_min_body = min(c2.open, c2.close)
            c2_max_body = max(c2.open, c2.close)

            if c1_min_body > c2_max_body:
                sep1 = c1_min_body - c2_max_body
                if sep1 >= min_sep:
                    midpoint = (c2_max_body + c1_min_body) / Decimal(2)
                    if c0.close > midpoint:
                        return BodyGapFillDecision(
                            direction=Direction.PUT,
                            atr=atr,
                            gap_separation=sep1,
                            gap_midpoint=midpoint,
                            bars_since_gap=2,
                            duration_candles=self._duration_candles,
                            reason_code="BODY_GAP_UP_FILL_PUT_STEP2",
                        )

            if c1_max_body < c2_min_body:
                sep1 = c2_min_body - c1_max_body
                if sep1 >= min_sep:
                    midpoint = (c1_max_body + c2_min_body) / Decimal(2)
                    if c0.close < midpoint:
                        return BodyGapFillDecision(
                            direction=Direction.CALL,
                            atr=atr,
                            gap_separation=sep1,
                            gap_midpoint=midpoint,
                            bars_since_gap=2,
                            duration_candles=self._duration_candles,
                            reason_code="BODY_GAP_DOWN_FILL_CALL_STEP2",
                        )

        return BodyGapFillDecision(
            direction=None,
            atr=atr,
            gap_separation=Decimal(0),
            gap_midpoint=Decimal(0),
            bars_since_gap=0,
            duration_candles=self._duration_candles,
            reason_code="NO_SIGNAL",
        )

    def evaluate(
        self,
        candles: Sequence[MarketCandle],
        context: RuntimeContext,
    ) -> Direction | None:
        return self.evaluate_decision(candles, context).direction


def iqoption_body_gap_fill_manifest(
    *,
    release_status: ReleaseStatus = ReleaseStatus.RELEASED,
) -> StrategyManifest:
    """Return the immutable catalog contract for the Body Gap Fill strategy."""
    return StrategyManifest(
        manifest_version=1,
        strategy_id=IQOPTION_BODY_GAP_FILL_STRATEGY_ID,
        version=IQOPTION_BODY_GAP_FILL_STRATEGY_VERSION,
        code_hash=hashlib.sha256(IQOPTION_BODY_GAP_FILL_ARTIFACT).hexdigest(),
        supported_brokers=(Broker.IQ_OPTION,),
        supported_products=("BINARY_OPTION",),
        supported_timeframes=(IQOPTION_BODY_GAP_FILL_TIMEFRAME_SECONDS,),
        required_data=(DataRequirement.CLOSED_CANDLES,),
        warmup_candles=IQOPTION_BODY_GAP_FILL_WARMUP_CANDLES,
        parameter_schema=(),
        risk_class=RiskClass.STANDARD,
        validation_report_id="iqoption-body-gap-fill-v1-validation",
        release_status=release_status,
        strategy_pack="iqoption-practice-candidates",
    )


__all__ = [
    "IQOPTION_BODY_GAP_FILL_ARTIFACT",
    "IQOPTION_BODY_GAP_FILL_ATR_PERIOD",
    "IQOPTION_BODY_GAP_FILL_EXPIRY_CANDLES",
    "IQOPTION_BODY_GAP_FILL_MAX_BARS",
    "IQOPTION_BODY_GAP_FILL_MIN_ATR_RATIO",
    "IQOPTION_BODY_GAP_FILL_STRATEGY_ID",
    "IQOPTION_BODY_GAP_FILL_STRATEGY_VERSION",
    "IQOPTION_BODY_GAP_FILL_TIMEFRAME_SECONDS",
    "IQOPTION_BODY_GAP_FILL_WARMUP_CANDLES",
    "IQOptionBodyGapFillStrategy",
    "BodyGapFillDecision",
    "calculate_atr14",
    "iqoption_body_gap_fill_manifest",
]
