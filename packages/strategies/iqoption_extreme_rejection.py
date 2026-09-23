"""Deterministic Extreme Sweep & Rejection signal generator for IQ Option.

Rule:
- Consumes validated, closed 1-minute candles.
- Defines max8 and min8 strictly using the preceding 8 candles (t-8 to t-1).
- A20: average candle range (High - Low) over the last 20 candles.
- CALL (Bullish liquidity sweep and rejection):
  - low_t < min8 - 0.10 * A20 (swept below 8-candle low by at least 10% of A20).
  - close_t > min8 (closed back above min8).
  - lower_wick_ratio_t >= 0.35 (lower wick is at least 35% of total candle range).
  - close_position_t >= 0.65 (close position is in upper 35% of the candle).
- PUT (Bearish liquidity sweep and rejection):
  - high_t > max8 + 0.10 * A20 (swept above 8-candle high by at least 10% of A20).
  - close_t < max8 (closed back below max8).
  - upper_wick_ratio_t >= 0.35 (upper wick is at least 35% of total candle range).
  - close_position_t <= 0.35 (close position is in lower 35% of the candle).
- Exclusion Filter:
  - Do NOT operate if |EMA(10)_t - EMA(30)_t| > 0.50 * A20 (indicates strong trending market).
- Expiry: End of the first candle after signal (1 minute in M1).
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from packages.domain.market import MarketCandle
from packages.domain.models import Broker, Direction
from packages.strategies.iqoption_indicators import calculate_average_range, calculate_ema
from packages.strategies.models import RuntimeContext
from packages.strategy_catalog.models import (
    DataRequirement,
    ReleaseStatus,
    RiskClass,
    StrategyManifest,
)

IQOPTION_EXTREME_REJECTION_STRATEGY_ID = "iqoption-extreme-rejection"
IQOPTION_EXTREME_REJECTION_STRATEGY_VERSION = "1.0.0"
IQOPTION_EXTREME_REJECTION_TIMEFRAME_SECONDS = 60
IQOPTION_EXTREME_REJECTION_REF_PERIOD = 8
IQOPTION_EXTREME_REJECTION_A20_PERIOD = 20
IQOPTION_EXTREME_REJECTION_SWEEP_PENETRATION = Decimal("0.10")
IQOPTION_EXTREME_REJECTION_MIN_WICK = Decimal("0.35")
IQOPTION_EXTREME_REJECTION_CALL_CLOSE_POS = Decimal("0.65")
IQOPTION_EXTREME_REJECTION_PUT_CLOSE_POS = Decimal("0.35")
IQOPTION_EXTREME_REJECTION_TREND_THRESHOLD = Decimal("0.50")
IQOPTION_EXTREME_REJECTION_EMA_FAST = 10
IQOPTION_EXTREME_REJECTION_EMA_SLOW = 30
IQOPTION_EXTREME_REJECTION_EXPIRY_CANDLES = 1
IQOPTION_EXTREME_REJECTION_WARMUP_CANDLES = 35
IQOPTION_EXTREME_REJECTION_ARTIFACT = b"IQOPTION_EXTREME_REJECTION:SWEEP8:A20:EMA10_30:v1"


@dataclass(frozen=True, slots=True)
class ExtremeRejectionDecision:
    direction: Direction | None
    max8: Decimal
    min8: Decimal
    a20: Decimal
    ema10: Decimal
    ema30: Decimal
    lower_wick_ratio: Decimal
    upper_wick_ratio: Decimal
    pos_close: Decimal
    duration_candles: int
    reason_code: str


class IQOptionExtremeRejectionStrategy:
    """Varredura e Rejeição de Extremo (Liquidity Sweep & Rejection)."""

    def __init__(
        self,
        ref_period: int = IQOPTION_EXTREME_REJECTION_REF_PERIOD,
        a20_period: int = IQOPTION_EXTREME_REJECTION_A20_PERIOD,
        sweep_penetration: Decimal = IQOPTION_EXTREME_REJECTION_SWEEP_PENETRATION,
        min_wick: Decimal = IQOPTION_EXTREME_REJECTION_MIN_WICK,
        call_close_pos: Decimal = IQOPTION_EXTREME_REJECTION_CALL_CLOSE_POS,
        put_close_pos: Decimal = IQOPTION_EXTREME_REJECTION_PUT_CLOSE_POS,
        trend_threshold: Decimal = IQOPTION_EXTREME_REJECTION_TREND_THRESHOLD,
        ema_fast: int = IQOPTION_EXTREME_REJECTION_EMA_FAST,
        ema_slow: int = IQOPTION_EXTREME_REJECTION_EMA_SLOW,
        duration_candles: int = IQOPTION_EXTREME_REJECTION_EXPIRY_CANDLES,
        warmup_candles: int = IQOPTION_EXTREME_REJECTION_WARMUP_CANDLES,
    ) -> None:
        if ref_period <= 0:
            raise ValueError("Reference period must be positive")
        if a20_period <= 0:
            raise ValueError("A20 period must be positive")
        if sweep_penetration <= 0:
            raise ValueError("Sweep penetration must be positive")
        if min_wick <= 0 or min_wick >= 1:
            raise ValueError("Min wick must be between 0 and 1")
        if duration_candles <= 0:
            raise ValueError("Duration candles must be positive")
        if ema_fast >= ema_slow:
            raise ValueError("Fast EMA period must be smaller than Slow EMA period")

        self._ref_period = ref_period
        self._a20_period = a20_period
        self._sweep_penetration = sweep_penetration
        self._min_wick = min_wick
        self._call_close_pos = call_close_pos
        self._put_close_pos = put_close_pos
        self._trend_threshold = trend_threshold
        self._ema_fast = ema_fast
        self._ema_slow = ema_slow
        self._duration_candles = duration_candles
        self._warmup_candles = warmup_candles

    @property
    def artifact_bytes(self) -> bytes:
        return IQOPTION_EXTREME_REJECTION_ARTIFACT

    @property
    def warmup_required(self) -> int:
        return self._warmup_candles

    @property
    def duration_candles(self) -> int:
        return self._duration_candles

    def evaluate_decision(
        self,
        candles: Sequence[MarketCandle],
        context: RuntimeContext,
    ) -> ExtremeRejectionDecision:
        if context.broker is not Broker.IQ_OPTION:
            raise ValueError("Extreme Rejection strategy requires IQ Option")
        if context.timeframe_seconds != IQOPTION_EXTREME_REJECTION_TIMEFRAME_SECONDS:
            raise ValueError("Extreme Rejection strategy requires closed 1-minute candles")
        if len(candles) < self._warmup_candles:
            raise ValueError(
                f"Extreme Rejection warming up (requires {self._warmup_candles} candles)"
            )
        if any(
            candle.broker is not Broker.IQ_OPTION
            or candle.broker_symbol != context.symbol
            or candle.timeframe_seconds != context.timeframe_seconds
            or not candle.is_closed
            for candle in candles
        ):
            raise ValueError("Extreme Rejection strategy received an invalid candle series")

        closes = tuple(candle.close for candle in candles)
        ema10 = calculate_ema(closes, self._ema_fast)
        ema30 = calculate_ema(closes, self._ema_slow)
        a20 = calculate_average_range(candles, period=self._a20_period)

        # Preceding 8 candles: from t-8 to t-1
        ref_candles = candles[-(self._ref_period + 1) : -1]
        max8 = max(c.high for c in ref_candles)
        min8 = min(c.low for c in ref_candles)

        c_t = candles[-1]
        amp_t = c_t.high - c_t.low

        lower_wick = min(c_t.open, c_t.close) - c_t.low
        lower_wick_ratio = lower_wick / amp_t if amp_t > 0 else Decimal(0)

        upper_wick = c_t.high - max(c_t.open, c_t.close)
        upper_wick_ratio = upper_wick / amp_t if amp_t > 0 else Decimal(0)

        pos_close_t = (
            (c_t.close - c_t.low) / amp_t if amp_t > 0 else Decimal("0.5")
        )

        # Exclusion filter: strong trend |EMA10 - EMA30| > 0.50 * A20
        ema_diff = abs(ema10 - ema30)
        if a20 > 0 and ema_diff > (self._trend_threshold * a20):
            return ExtremeRejectionDecision(
                direction=None,
                max8=max8,
                min8=min8,
                a20=a20,
                ema10=ema10,
                ema30=ema30,
                lower_wick_ratio=lower_wick_ratio,
                upper_wick_ratio=upper_wick_ratio,
                pos_close=pos_close_t,
                duration_candles=self._duration_candles,
                reason_code="EXCLUSION_TREND_TOO_STRONG",
            )

        penetration = self._sweep_penetration * a20

        # 1. Bullish sweep and rejection (CALL)
        # low_t < min8 - 0.10 * A20, close_t > min8, lower_wick >= 0.35, pos_close >= 0.65
        is_call_sweep = c_t.low < (min8 - penetration)
        is_call_return = c_t.close > min8
        is_call_wick = lower_wick_ratio >= self._min_wick
        is_call_pos = pos_close_t >= self._call_close_pos

        if is_call_sweep and is_call_return and is_call_wick and is_call_pos:
            return ExtremeRejectionDecision(
                direction=Direction.CALL,
                max8=max8,
                min8=min8,
                a20=a20,
                ema10=ema10,
                ema30=ema30,
                lower_wick_ratio=lower_wick_ratio,
                upper_wick_ratio=upper_wick_ratio,
                pos_close=pos_close_t,
                duration_candles=self._duration_candles,
                reason_code="EXTREME_REJECTION_SWEEP_CALL",
            )

        # 2. Bearish sweep and rejection (PUT)
        # high_t > max8 + 0.10 * A20, close_t < max8, upper_wick >= 0.35, pos_close <= 0.35
        is_put_sweep = c_t.high > (max8 + penetration)
        is_put_return = c_t.close < max8
        is_put_wick = upper_wick_ratio >= self._min_wick
        is_put_pos = pos_close_t <= self._put_close_pos

        if is_put_sweep and is_put_return and is_put_wick and is_put_pos:
            return ExtremeRejectionDecision(
                direction=Direction.PUT,
                max8=max8,
                min8=min8,
                a20=a20,
                ema10=ema10,
                ema30=ema30,
                lower_wick_ratio=lower_wick_ratio,
                upper_wick_ratio=upper_wick_ratio,
                pos_close=pos_close_t,
                duration_candles=self._duration_candles,
                reason_code="EXTREME_REJECTION_SWEEP_PUT",
            )

        return ExtremeRejectionDecision(
            direction=None,
            max8=max8,
            min8=min8,
            a20=a20,
            ema10=ema10,
            ema30=ema30,
            lower_wick_ratio=lower_wick_ratio,
            upper_wick_ratio=upper_wick_ratio,
            pos_close=pos_close_t,
            duration_candles=self._duration_candles,
            reason_code="NO_SIGNAL",
        )

    def evaluate(
        self,
        candles: Sequence[MarketCandle],
        context: RuntimeContext,
    ) -> Direction | None:
        return self.evaluate_decision(candles, context).direction


def iqoption_extreme_rejection_manifest(
    *,
    release_status: ReleaseStatus = ReleaseStatus.RELEASED,
) -> StrategyManifest:
    """Return the immutable catalog contract for the Extreme Rejection strategy."""
    return StrategyManifest(
        manifest_version=1,
        strategy_id=IQOPTION_EXTREME_REJECTION_STRATEGY_ID,
        version=IQOPTION_EXTREME_REJECTION_STRATEGY_VERSION,
        code_hash=hashlib.sha256(IQOPTION_EXTREME_REJECTION_ARTIFACT).hexdigest(),
        supported_brokers=(Broker.IQ_OPTION,),
        supported_products=("BINARY_OPTION",),
        supported_timeframes=(IQOPTION_EXTREME_REJECTION_TIMEFRAME_SECONDS,),
        required_data=(DataRequirement.CLOSED_CANDLES,),
        warmup_candles=IQOPTION_EXTREME_REJECTION_WARMUP_CANDLES,
        parameter_schema=(),
        risk_class=RiskClass.STANDARD,
        validation_report_id="iqoption-extreme-rejection-v1-validation",
        release_status=release_status,
        strategy_pack="iqoption-practice-candidates",
    )


__all__ = [
    "IQOPTION_EXTREME_REJECTION_A20_PERIOD",
    "IQOPTION_EXTREME_REJECTION_ARTIFACT",
    "IQOPTION_EXTREME_REJECTION_CALL_CLOSE_POS",
    "IQOPTION_EXTREME_REJECTION_EMA_FAST",
    "IQOPTION_EXTREME_REJECTION_EMA_SLOW",
    "IQOPTION_EXTREME_REJECTION_EXPIRY_CANDLES",
    "IQOPTION_EXTREME_REJECTION_MIN_WICK",
    "IQOPTION_EXTREME_REJECTION_PUT_CLOSE_POS",
    "IQOPTION_EXTREME_REJECTION_REF_PERIOD",
    "IQOPTION_EXTREME_REJECTION_STRATEGY_ID",
    "IQOPTION_EXTREME_REJECTION_STRATEGY_VERSION",
    "IQOPTION_EXTREME_REJECTION_SWEEP_PENETRATION",
    "IQOPTION_EXTREME_REJECTION_TIMEFRAME_SECONDS",
    "IQOPTION_EXTREME_REJECTION_TREND_THRESHOLD",
    "IQOPTION_EXTREME_REJECTION_WARMUP_CANDLES",
    "ExtremeRejectionDecision",
    "IQOptionExtremeRejectionStrategy",
    "iqoption_extreme_rejection_manifest",
]
