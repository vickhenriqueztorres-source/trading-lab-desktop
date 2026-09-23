"""Deterministic Pattern Reversal (2-Candle Engulfing) signal generator for IQ Option.

The strategy is intentionally pure: it consumes validated, closed candles and
returns a signal decision. It never selects an account, stake, or calls a
broker. Financial admission remains the Core's responsibility.

Rule:
- CALL: Previous body is bearish (prev.close < prev.open), current body is
  bullish (curr.close > curr.open), current body engulfs/covers previous body
  (curr.open <= prev.close and curr.close >= prev.open), current body size >= 1.2x
  previous body size, and current body occupies >= 30% of current candle's
  total amplitude (curr.high - curr.low).
- PUT (Mirrored): Previous body is bullish (prev.close > prev.open), current body
  is bearish (curr.close < curr.open), current body engulfs/covers previous body
  (curr.open >= prev.close and curr.close <= prev.open), current body size >= 1.2x
  previous body size, and current body occupies >= 30% of current candle's
  total amplitude (curr.high - curr.low).
- Otherwise: NONE (direction is None).
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

IQOPTION_PATTERN_REVERSAL_STRATEGY_ID = "iqoption-pattern-reversal"
IQOPTION_PATTERN_REVERSAL_STRATEGY_VERSION = "1.1.0"
IQOPTION_PATTERN_REVERSAL_TIMEFRAME_SECONDS = 60
IQOPTION_PATTERN_REVERSAL_MIN_BODY_RATIO = Decimal("1.2")  # >= 1.2x
IQOPTION_PATTERN_REVERSAL_MIN_RANGE_OCCUPANCY = Decimal("0.30")  # >= 30%
IQOPTION_PATTERN_REVERSAL_MAX_OPPOSITE_WICK = Decimal("0.25")  # <= 25% opposite wick
IQOPTION_PATTERN_REVERSAL_EXPIRY_CANDLES = 1
IQOPTION_PATTERN_REVERSAL_WARMUP_CANDLES = 2
IQOPTION_PATTERN_REVERSAL_ARTIFACT = b"IQOPTION_PATTERN_REVERSAL:ENGULF:1.2:WICK:0.25:EXPIRY:1:v2"


@dataclass(frozen=True, slots=True)
class PatternReversalDecision:
    direction: Direction | None
    duration_candles: int
    body_ratio: Decimal
    range_occupancy_pct: Decimal
    reason_code: str


class IQOptionPatternReversalStrategy:
    """2-candle engulfing reversal with body ratio, range checks, and wick filter."""

    def __init__(
        self,
        min_body_ratio: Decimal = IQOPTION_PATTERN_REVERSAL_MIN_BODY_RATIO,
        min_range_occupancy: Decimal = IQOPTION_PATTERN_REVERSAL_MIN_RANGE_OCCUPANCY,
        duration_candles: int = IQOPTION_PATTERN_REVERSAL_EXPIRY_CANDLES,
        max_opposite_wick: Decimal = IQOPTION_PATTERN_REVERSAL_MAX_OPPOSITE_WICK,
    ) -> None:
        if min_body_ratio <= 0:
            raise ValueError("Minimum body ratio must be positive")
        if min_range_occupancy <= 0 or min_range_occupancy > 1:
            raise ValueError("Minimum range occupancy must be between 0 and 1")
        if duration_candles <= 0:
            raise ValueError("Duration candles must be positive")
        if max_opposite_wick <= 0 or max_opposite_wick >= 1:
            raise ValueError("Maximum opposite wick must be between 0 and 1")
        self._min_body_ratio = min_body_ratio
        self._min_range_occupancy = min_range_occupancy
        self._duration_candles = duration_candles
        self._max_opposite_wick = max_opposite_wick

    @property
    def artifact_bytes(self) -> bytes:
        return IQOPTION_PATTERN_REVERSAL_ARTIFACT

    @property
    def warmup_required(self) -> int:
        return IQOPTION_PATTERN_REVERSAL_WARMUP_CANDLES

    @property
    def duration_candles(self) -> int:
        return self._duration_candles

    def evaluate_decision(
        self,
        candles: Sequence[MarketCandle],
        context: RuntimeContext,
    ) -> PatternReversalDecision:
        if context.broker is not Broker.IQ_OPTION:
            raise ValueError("Pattern Reversal strategy requires IQ Option")
        if context.timeframe_seconds != IQOPTION_PATTERN_REVERSAL_TIMEFRAME_SECONDS:
            raise ValueError("Pattern Reversal strategy requires closed 1-minute candles")
        if len(candles) < IQOPTION_PATTERN_REVERSAL_WARMUP_CANDLES:
            raise ValueError(
                "Pattern Reversal strategy is still warming up (requires at least 2 candles)"
            )
        if any(
            candle.broker is not Broker.IQ_OPTION
            or candle.broker_symbol != context.symbol
            or candle.timeframe_seconds != context.timeframe_seconds
            or not candle.is_closed
            for candle in candles
        ):
            raise ValueError("Pattern Reversal strategy received an invalid candle series")

        prev = candles[-2]
        curr = candles[-1]

        if prev.low <= 0 or prev.high <= 0 or curr.low <= 0 or curr.high <= 0:
            raise ValueError("Candle prices must be strictly positive")

        prev_body = abs(prev.close - prev.open)
        curr_body = abs(curr.close - curr.open)
        curr_range = curr.high - curr.low

        if curr_range <= 0 or prev_body <= 0:
            return PatternReversalDecision(
                direction=None,
                duration_candles=self._duration_candles,
                body_ratio=Decimal("0"),
                range_occupancy_pct=Decimal("0"),
                reason_code="ZERO_RANGE_OR_DOJI_PREV",
            )

        body_ratio = curr_body / prev_body
        range_occupancy = curr_body / curr_range

        # 1. Test CALL condition (Bullish Engulfing)
        # Prev bearish, Curr bullish, Engulfs previous body
        if (
            prev.close < prev.open
            and curr.close > curr.open
            and curr.open <= prev.close
            and curr.close >= prev.open
        ):
            if body_ratio < self._min_body_ratio:
                return PatternReversalDecision(
                    direction=None,
                    duration_candles=self._duration_candles,
                    body_ratio=body_ratio,
                    range_occupancy_pct=range_occupancy,
                    reason_code="BODY_RATIO_TOO_SMALL",
                )
            if range_occupancy < self._min_range_occupancy:
                return PatternReversalDecision(
                    direction=None,
                    duration_candles=self._duration_candles,
                    body_ratio=body_ratio,
                    range_occupancy_pct=range_occupancy,
                    reason_code="RANGE_OCCUPANCY_LOW",
                )

            upper_wick = curr.high - curr.close
            upper_wick_ratio = upper_wick / curr_range if curr_range > 0 else Decimal(0)

            if upper_wick_ratio > self._max_opposite_wick:
                return PatternReversalDecision(
                    direction=None,
                    duration_candles=self._duration_candles,
                    body_ratio=body_ratio,
                    range_occupancy_pct=range_occupancy,
                    reason_code="UPPER_WICK_REJECTION",
                )

            return PatternReversalDecision(
                direction=Direction.CALL,
                duration_candles=self._duration_candles,
                body_ratio=body_ratio,
                range_occupancy_pct=range_occupancy,
                reason_code="CALL_BULLISH_ENGULFING",
            )

        # 2. Test PUT condition (Bearish Engulfing)
        # Prev bullish, Curr bearish, Engulfs previous body
        if (
            prev.close > prev.open
            and curr.close < curr.open
            and curr.open >= prev.close
            and curr.close <= prev.open
        ):
            if body_ratio < self._min_body_ratio:
                return PatternReversalDecision(
                    direction=None,
                    duration_candles=self._duration_candles,
                    body_ratio=body_ratio,
                    range_occupancy_pct=range_occupancy,
                    reason_code="BODY_RATIO_TOO_SMALL",
                )
            if range_occupancy < self._min_range_occupancy:
                return PatternReversalDecision(
                    direction=None,
                    duration_candles=self._duration_candles,
                    body_ratio=body_ratio,
                    range_occupancy_pct=range_occupancy,
                    reason_code="RANGE_OCCUPANCY_LOW",
                )

            lower_wick = curr.close - curr.low
            lower_wick_ratio = lower_wick / curr_range if curr_range > 0 else Decimal(0)

            if lower_wick_ratio > self._max_opposite_wick:
                return PatternReversalDecision(
                    direction=None,
                    duration_candles=self._duration_candles,
                    body_ratio=body_ratio,
                    range_occupancy_pct=range_occupancy,
                    reason_code="LOWER_WICK_REJECTION",
                )

            return PatternReversalDecision(
                direction=Direction.PUT,
                duration_candles=self._duration_candles,
                body_ratio=body_ratio,
                range_occupancy_pct=range_occupancy,
                reason_code="PUT_BEARISH_ENGULFING",
            )

        return PatternReversalDecision(
            direction=None,
            duration_candles=self._duration_candles,
            body_ratio=body_ratio,
            range_occupancy_pct=range_occupancy,
            reason_code="NO_ENGULFING",
        )

    def evaluate(
        self,
        candles: Sequence[MarketCandle],
        context: RuntimeContext,
    ) -> Direction | None:
        return self.evaluate_decision(candles, context).direction


def iqoption_pattern_reversal_manifest(
    *,
    release_status: ReleaseStatus = ReleaseStatus.RELEASED,
) -> StrategyManifest:
    """Return the immutable catalog contract for the Pattern Reversal strategy."""

    return StrategyManifest(
        manifest_version=1,
        strategy_id=IQOPTION_PATTERN_REVERSAL_STRATEGY_ID,
        version=IQOPTION_PATTERN_REVERSAL_STRATEGY_VERSION,
        code_hash=hashlib.sha256(IQOPTION_PATTERN_REVERSAL_ARTIFACT).hexdigest(),
        supported_brokers=(Broker.IQ_OPTION,),
        supported_products=("BINARY_OPTION",),
        supported_timeframes=(IQOPTION_PATTERN_REVERSAL_TIMEFRAME_SECONDS,),
        required_data=(DataRequirement.CLOSED_CANDLES,),
        warmup_candles=IQOPTION_PATTERN_REVERSAL_WARMUP_CANDLES,
        parameter_schema=(),
        risk_class=RiskClass.ELEVATED,
        validation_report_id="iqoption-pattern-reversal-v1-validation",
        release_status=release_status,
        strategy_pack="iqoption-practice-candidates",
    )


__all__ = [
    "IQOPTION_PATTERN_REVERSAL_ARTIFACT",
    "IQOPTION_PATTERN_REVERSAL_EXPIRY_CANDLES",
    "IQOPTION_PATTERN_REVERSAL_MAX_OPPOSITE_WICK",
    "IQOPTION_PATTERN_REVERSAL_MIN_BODY_RATIO",
    "IQOPTION_PATTERN_REVERSAL_MIN_RANGE_OCCUPANCY",
    "IQOPTION_PATTERN_REVERSAL_STRATEGY_ID",
    "IQOPTION_PATTERN_REVERSAL_STRATEGY_VERSION",
    "IQOPTION_PATTERN_REVERSAL_TIMEFRAME_SECONDS",
    "IQOPTION_PATTERN_REVERSAL_WARMUP_CANDLES",
    "IQOptionPatternReversalStrategy",
    "PatternReversalDecision",
    "iqoption_pattern_reversal_manifest",
]
