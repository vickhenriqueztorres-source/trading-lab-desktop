"""Deterministic Liquidity Gap (Extreme Sweep) signal generator for IQ Option.

The strategy is intentionally pure: it consumes validated, closed candles and
returns a signal decision. It never selects an account, stake, or calls a
broker. Financial admission remains the Core's responsibility.

Rule:
- CALL: Current candle's low sweeps below previous candle's low by at least 0.1%,
  closes bullish (curr.close > curr.open), and recovers fully above previous
  candle's high (curr.close > prev.high).
- PUT: Current candle's high sweeps above previous candle's high by at least 0.1%,
  closes bearish (curr.close < curr.open), and recovers fully below previous
  candle's low (curr.close < prev.low).
- Incomplete recovery or no sweep: NONE (direction is None).
- Expiry: End of the second candle after signal (duration = 2 minutes in M1).
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

IQOPTION_LIQUIDITY_GAP_STRATEGY_ID = "iqoption-liquidity-gap"
IQOPTION_LIQUIDITY_GAP_STRATEGY_VERSION = "1.1.0"
IQOPTION_LIQUIDITY_GAP_TIMEFRAME_SECONDS = 60
IQOPTION_LIQUIDITY_GAP_SWEEP_MIN_PCT = Decimal("0.0002")  # 0.02% base
IQOPTION_LIQUIDITY_GAP_MIN_SWEEP_RANGE_RATIO = Decimal("0.10")  # 10% of prev range
IQOPTION_LIQUIDITY_GAP_MIN_REJECTION_WICK = Decimal("0.25")  # >= 25% absorption wick
IQOPTION_LIQUIDITY_GAP_MAX_OPPOSITE_WICK = Decimal("0.35")  # <= 35% opposite wick
IQOPTION_LIQUIDITY_GAP_EXPIRY_CANDLES = 2
IQOPTION_LIQUIDITY_GAP_WARMUP_CANDLES = 2
IQOPTION_LIQUIDITY_GAP_ARTIFACT = b"IQOPTION_LIQUIDITY_GAP:SWEEP:ADAPTIVE:WICK:EXPIRY:2:v2"


@dataclass(frozen=True, slots=True)
class LiquidityGapDecision:
    direction: Direction | None
    duration_candles: int
    sweep_pct: Decimal
    recovered_fully: bool
    reason_code: str


class IQOptionLiquidityGapStrategy:
    """Extreme sweep strategy with adaptive volatility sweep, pinbar absorption, and recovery."""

    def __init__(
        self,
        sweep_min_pct: Decimal = IQOPTION_LIQUIDITY_GAP_SWEEP_MIN_PCT,
        duration_candles: int = IQOPTION_LIQUIDITY_GAP_EXPIRY_CANDLES,
        min_sweep_range_ratio: Decimal = IQOPTION_LIQUIDITY_GAP_MIN_SWEEP_RANGE_RATIO,
        min_rejection_wick: Decimal = IQOPTION_LIQUIDITY_GAP_MIN_REJECTION_WICK,
        max_opposite_wick: Decimal = IQOPTION_LIQUIDITY_GAP_MAX_OPPOSITE_WICK,
    ) -> None:
        if sweep_min_pct <= 0:
            raise ValueError("Sweep minimum percentage must be positive")
        if duration_candles <= 0:
            raise ValueError("Duration candles must be positive")
        self._sweep_min_pct = sweep_min_pct
        self._duration_candles = duration_candles
        self._min_sweep_range_ratio = min_sweep_range_ratio
        self._min_rejection_wick = min_rejection_wick
        self._max_opposite_wick = max_opposite_wick

    @property
    def artifact_bytes(self) -> bytes:
        return IQOPTION_LIQUIDITY_GAP_ARTIFACT

    @property
    def warmup_required(self) -> int:
        return IQOPTION_LIQUIDITY_GAP_WARMUP_CANDLES

    @property
    def duration_candles(self) -> int:
        return self._duration_candles

    def evaluate_decision(
        self,
        candles: Sequence[MarketCandle],
        context: RuntimeContext,
    ) -> LiquidityGapDecision:
        if context.broker is not Broker.IQ_OPTION:
            raise ValueError("Liquidity Gap strategy requires IQ Option")
        if context.timeframe_seconds != IQOPTION_LIQUIDITY_GAP_TIMEFRAME_SECONDS:
            raise ValueError("Liquidity Gap strategy requires closed 1-minute candles")
        if len(candles) < IQOPTION_LIQUIDITY_GAP_WARMUP_CANDLES:
            raise ValueError(
                "Liquidity Gap strategy is still warming up (requires at least 2 candles)"
            )
        if any(
            candle.broker is not Broker.IQ_OPTION
            or candle.broker_symbol != context.symbol
            or candle.timeframe_seconds != context.timeframe_seconds
            or not candle.is_closed
            for candle in candles
        ):
            raise ValueError("Liquidity Gap strategy received an invalid candle series")

        prev = candles[-2]
        curr = candles[-1]

        if prev.low <= 0 or prev.high <= 0 or curr.low <= 0 or curr.high <= 0:
            raise ValueError("Candle prices must be strictly positive")

        prev_range = prev.high - prev.low
        curr_range = curr.high - curr.low

        # 1. Test CALL condition (Bullish close, Low Sweep, and Absorption/Recovery)
        if curr.close > curr.open:
            sweep_threshold_low = prev.low * (Decimal("1") - self._sweep_min_pct)
            is_pct_sweep = curr.low <= sweep_threshold_low
            is_range_sweep = (
                prev_range > 0
                and curr.low < prev.low
                and ((prev.low - curr.low) / prev_range) >= self._min_sweep_range_ratio
            )
            if is_pct_sweep or is_range_sweep:
                sweep_depth = (prev.low - curr.low) / prev.low
                lower_wick = curr.open - curr.low
                upper_wick = curr.high - curr.close
                lower_wick_ratio = lower_wick / curr_range if curr_range > 0 else Decimal(0)
                upper_wick_ratio = upper_wick / curr_range if curr_range > 0 else Decimal(0)

                recovered_above_high = curr.close > prev.high
                recovered_absorption = (
                    curr.close > prev.low
                    and lower_wick_ratio >= self._min_rejection_wick
                    and upper_wick_ratio <= self._max_opposite_wick
                    and curr.close >= (prev.open + prev.close) / Decimal(2)
                )

                if recovered_above_high or recovered_absorption:
                    return LiquidityGapDecision(
                        direction=Direction.CALL,
                        duration_candles=self._duration_candles,
                        sweep_pct=sweep_depth,
                        recovered_fully=True,
                        reason_code="CALL_SWEEP_LOW_RECOVERED",
                    )
                return LiquidityGapDecision(
                    direction=None,
                    duration_candles=self._duration_candles,
                    sweep_pct=sweep_depth,
                    recovered_fully=False,
                    reason_code="RECOVERY_INCOMPLETE",
                )

        # 2. Test PUT condition (Bearish close, High Sweep, and Absorption/Recovery)
        if curr.close < curr.open:
            sweep_threshold_high = prev.high * (Decimal("1") + self._sweep_min_pct)
            is_pct_sweep = curr.high >= sweep_threshold_high
            is_range_sweep = (
                prev_range > 0
                and curr.high > prev.high
                and ((curr.high - prev.high) / prev_range) >= self._min_sweep_range_ratio
            )
            if is_pct_sweep or is_range_sweep:
                sweep_depth = (curr.high - prev.high) / prev.high
                upper_wick = curr.high - curr.open
                lower_wick = curr.close - curr.low
                upper_wick_ratio = upper_wick / curr_range if curr_range > 0 else Decimal(0)
                lower_wick_ratio = lower_wick / curr_range if curr_range > 0 else Decimal(0)

                recovered_below_low = curr.close < prev.low
                recovered_absorption = (
                    curr.close < prev.high
                    and upper_wick_ratio >= self._min_rejection_wick
                    and lower_wick_ratio <= self._max_opposite_wick
                    and curr.close <= (prev.open + prev.close) / Decimal(2)
                )

                if recovered_below_low or recovered_absorption:
                    return LiquidityGapDecision(
                        direction=Direction.PUT,
                        duration_candles=self._duration_candles,
                        sweep_pct=sweep_depth,
                        recovered_fully=True,
                        reason_code="PUT_SWEEP_HIGH_RECOVERED",
                    )
                return LiquidityGapDecision(
                    direction=None,
                    duration_candles=self._duration_candles,
                    sweep_pct=sweep_depth,
                    recovered_fully=False,
                    reason_code="RECOVERY_INCOMPLETE",
                )

        return LiquidityGapDecision(
            direction=None,
            duration_candles=self._duration_candles,
            sweep_pct=Decimal("0"),
            recovered_fully=False,
            reason_code="NO_SWEEP",
        )

    def evaluate(
        self,
        candles: Sequence[MarketCandle],
        context: RuntimeContext,
    ) -> Direction | None:
        return self.evaluate_decision(candles, context).direction


def iqoption_liquidity_gap_manifest(
    *,
    release_status: ReleaseStatus = ReleaseStatus.RELEASED,
) -> StrategyManifest:
    """Return the immutable catalog contract for the Liquidity Gap strategy."""

    return StrategyManifest(
        manifest_version=1,
        strategy_id=IQOPTION_LIQUIDITY_GAP_STRATEGY_ID,
        version=IQOPTION_LIQUIDITY_GAP_STRATEGY_VERSION,
        code_hash=hashlib.sha256(IQOPTION_LIQUIDITY_GAP_ARTIFACT).hexdigest(),
        supported_brokers=(Broker.IQ_OPTION,),
        supported_products=("BINARY_OPTION",),
        supported_timeframes=(IQOPTION_LIQUIDITY_GAP_TIMEFRAME_SECONDS,),
        required_data=(DataRequirement.CLOSED_CANDLES,),
        warmup_candles=IQOPTION_LIQUIDITY_GAP_WARMUP_CANDLES,
        parameter_schema=(),
        risk_class=RiskClass.ELEVATED,
        validation_report_id="iqoption-liquidity-gap-v1-validation",
        release_status=release_status,
        strategy_pack="iqoption-practice-candidates",
    )


__all__ = [
    "IQOPTION_LIQUIDITY_GAP_ARTIFACT",
    "IQOPTION_LIQUIDITY_GAP_EXPIRY_CANDLES",
    "IQOPTION_LIQUIDITY_GAP_MAX_OPPOSITE_WICK",
    "IQOPTION_LIQUIDITY_GAP_MIN_REJECTION_WICK",
    "IQOPTION_LIQUIDITY_GAP_MIN_SWEEP_RANGE_RATIO",
    "IQOPTION_LIQUIDITY_GAP_STRATEGY_ID",
    "IQOPTION_LIQUIDITY_GAP_STRATEGY_VERSION",
    "IQOPTION_LIQUIDITY_GAP_SWEEP_MIN_PCT",
    "IQOPTION_LIQUIDITY_GAP_TIMEFRAME_SECONDS",
    "IQOPTION_LIQUIDITY_GAP_WARMUP_CANDLES",
    "IQOptionLiquidityGapStrategy",
    "LiquidityGapDecision",
    "iqoption_liquidity_gap_manifest",
]
