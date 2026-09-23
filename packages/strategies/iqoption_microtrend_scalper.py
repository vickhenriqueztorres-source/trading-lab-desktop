"""Deterministic Three-Candle Microtrend signal generator for IQ Option.

Rule:
- Consumes validated, closed 1-minute candles.
- Requires 3 consecutive directional candles (t-2, t-1, t):
  - CALL: 3 bullish candles (close > open).
  - PUT: 3 bearish candles (close < open).
- Mean relative body of the 3 candles >= 0.45:
  relative_body = |close - open| / (high - low).
- Close position on candle t:
  close_position = (close_t - low_t) / (high_t - low_t).
  - CALL: close_position >= 0.75.
  - PUT: close_position <= 0.25.
- RSI(5) on candle t:
  - CALL: 55 <= RSI(5) <= 80.
  - PUT: 20 <= RSI(5) <= 45.
- Exclusion Filter:
  - Do NOT operate if amplitude_t (high_t - low_t) > 2.5 * A20 (average range of last 20 candles).
- Selective Trend Filter:
  - CALL: EMA(10)_t > EMA(30)_t.
  - PUT: EMA(10)_t < EMA(30)_t.
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
from packages.strategies.iqoption_rsi import calculate_wilder_rsi
from packages.strategies.models import RuntimeContext
from packages.strategy_catalog.models import (
    DataRequirement,
    ReleaseStatus,
    RiskClass,
    StrategyManifest,
)

IQOPTION_MICROTREND_SCALPER_STRATEGY_ID = "iqoption-microtrend-scalper"
IQOPTION_MICROTREND_SCALPER_STRATEGY_VERSION = "2.0.0"
IQOPTION_MICROTREND_SCALPER_TIMEFRAME_SECONDS = 60
IQOPTION_MICROTREND_SCALPER_RSI_PERIOD = 5
IQOPTION_MICROTREND_SCALPER_MIN_BODY_STRENGTH = Decimal("0.45")
IQOPTION_MICROTREND_SCALPER_CALL_RSI_MIN = Decimal("55")
IQOPTION_MICROTREND_SCALPER_CALL_RSI_MAX = Decimal("80")
IQOPTION_MICROTREND_SCALPER_PUT_RSI_MIN = Decimal("20")
IQOPTION_MICROTREND_SCALPER_PUT_RSI_MAX = Decimal("45")
IQOPTION_MICROTREND_SCALPER_CALL_CLOSE_POS_MIN = Decimal("0.75")
IQOPTION_MICROTREND_SCALPER_PUT_CLOSE_POS_MAX = Decimal("0.25")
IQOPTION_MICROTREND_SCALPER_MAX_RANGE_FACTOR = Decimal("2.5")
IQOPTION_MICROTREND_SCALPER_A20_PERIOD = 20
IQOPTION_MICROTREND_SCALPER_EMA_FAST = 10
IQOPTION_MICROTREND_SCALPER_EMA_SLOW = 30
IQOPTION_MICROTREND_SCALPER_EXPIRY_CANDLES = 1
IQOPTION_MICROTREND_SCALPER_WARMUP_CANDLES = 35
IQOPTION_MICROTREND_SCALPER_ARTIFACT = b"IQOPTION_MICROTREND_SCALPER:3BARS:EMA10_30:RSI5:A20:v2"


@dataclass(frozen=True, slots=True)
class MicrotrendScalperDecision:
    direction: Direction | None
    rsi: Decimal
    mean_body_strength: Decimal
    is_consecutive_same_direction: bool
    duration_candles: int
    reason_code: str
    close_position: Decimal | None = None
    a20: Decimal | None = None
    ema10: Decimal | None = None
    ema30: Decimal | None = None


class IQOptionMicrotrendScalperStrategy:
    """Microtrend Scalper: 3 directional candles + body strength + RSI(5) + A20 cap + EMA filter."""

    def __init__(
        self,
        rsi_period: int = IQOPTION_MICROTREND_SCALPER_RSI_PERIOD,
        min_body_strength: Decimal = IQOPTION_MICROTREND_SCALPER_MIN_BODY_STRENGTH,
        call_rsi_min: Decimal = IQOPTION_MICROTREND_SCALPER_CALL_RSI_MIN,
        call_rsi_max: Decimal = IQOPTION_MICROTREND_SCALPER_CALL_RSI_MAX,
        put_rsi_min: Decimal = IQOPTION_MICROTREND_SCALPER_PUT_RSI_MIN,
        put_rsi_max: Decimal = IQOPTION_MICROTREND_SCALPER_PUT_RSI_MAX,
        call_close_pos_min: Decimal = IQOPTION_MICROTREND_SCALPER_CALL_CLOSE_POS_MIN,
        put_close_pos_max: Decimal = IQOPTION_MICROTREND_SCALPER_PUT_CLOSE_POS_MAX,
        max_range_factor: Decimal = IQOPTION_MICROTREND_SCALPER_MAX_RANGE_FACTOR,
        a20_period: int = IQOPTION_MICROTREND_SCALPER_A20_PERIOD,
        ema_fast: int = IQOPTION_MICROTREND_SCALPER_EMA_FAST,
        ema_slow: int = IQOPTION_MICROTREND_SCALPER_EMA_SLOW,
        duration_candles: int = IQOPTION_MICROTREND_SCALPER_EXPIRY_CANDLES,
        warmup_candles: int = IQOPTION_MICROTREND_SCALPER_WARMUP_CANDLES,
    ) -> None:
        if rsi_period <= 0:
            raise ValueError("RSI period must be positive")
        if min_body_strength <= 0 or min_body_strength >= 1:
            raise ValueError("Minimum body strength must be between 0 and 1")
        if duration_candles <= 0:
            raise ValueError("Duration candles must be positive")
        if ema_fast >= ema_slow:
            raise ValueError("Fast EMA period must be smaller than Slow EMA period")

        self._rsi_period = rsi_period
        self._min_body_strength = min_body_strength
        self._call_rsi_min = call_rsi_min
        self._call_rsi_max = call_rsi_max
        self._put_rsi_min = put_rsi_min
        self._put_rsi_max = put_rsi_max
        self._call_close_pos_min = call_close_pos_min
        self._put_close_pos_max = put_close_pos_max
        self._max_range_factor = max_range_factor
        self._a20_period = a20_period
        self._ema_fast = ema_fast
        self._ema_slow = ema_slow
        self._duration_candles = duration_candles
        self._warmup_candles = warmup_candles

    @property
    def artifact_bytes(self) -> bytes:
        return IQOPTION_MICROTREND_SCALPER_ARTIFACT

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
    ) -> MicrotrendScalperDecision:
        if context.broker is not Broker.IQ_OPTION:
            raise ValueError("Microtrend Scalper strategy requires IQ Option")
        if context.timeframe_seconds != IQOPTION_MICROTREND_SCALPER_TIMEFRAME_SECONDS:
            raise ValueError("Microtrend Scalper strategy requires closed 1-minute candles")
        if len(candles) < self._warmup_candles:
            raise ValueError(
                f"Microtrend Scalper warming up (requires {self._warmup_candles} candles)"
            )
        if any(
            candle.broker is not Broker.IQ_OPTION
            or candle.broker_symbol != context.symbol
            or candle.timeframe_seconds != context.timeframe_seconds
            or not candle.is_closed
            for candle in candles
        ):
            raise ValueError("Microtrend Scalper strategy received an invalid candle series")

        closes = tuple(candle.close for candle in candles)
        rsi_value = calculate_wilder_rsi(closes, period=self._rsi_period)
        ema10 = calculate_ema(closes, self._ema_fast)
        ema30 = calculate_ema(closes, self._ema_slow)
        a20 = calculate_average_range(candles, period=self._a20_period)

        c_t = candles[-1]
        c_t_range = c_t.high - c_t.low
        close_pos_t = (
            (c_t.close - c_t.low) / c_t_range if c_t_range > 0 else Decimal("0.5")
        )

        last_three = candles[-3:]
        strengths: list[Decimal] = []
        is_all_bullish = True
        is_all_bearish = True

        for c in last_three:
            if c.close > c.open:
                is_all_bearish = False
            elif c.close < c.open:
                is_all_bullish = False
            else:
                is_all_bullish = False
                is_all_bearish = False

            amplitude = c.high - c.low
            body = abs(c.close - c.open)
            strength = body / amplitude if amplitude > 0 else Decimal(0)
            strengths.append(strength)

        mean_strength = sum(strengths) / Decimal(3)
        same_dir = is_all_bullish or is_all_bearish
        has_strength = mean_strength >= self._min_body_strength

        # Exclusion filter: candle t amplitude exceeds 2.5 * A20
        if a20 > 0 and c_t_range > (self._max_range_factor * a20):
            return MicrotrendScalperDecision(
                direction=None,
                rsi=rsi_value,
                mean_body_strength=mean_strength,
                is_consecutive_same_direction=same_dir,
                duration_candles=self._duration_candles,
                reason_code="EXCLUSION_AMPLITUDE_EXCEEDED",
                close_position=close_pos_t,
                a20=a20,
                ema10=ema10,
                ema30=ema30,
            )

        # 1. Bullish evaluation
        if is_all_bullish and has_strength:
            if close_pos_t >= self._call_close_pos_min:
                if self._call_rsi_min <= rsi_value <= self._call_rsi_max:
                    if ema10 > ema30:
                        return MicrotrendScalperDecision(
                            direction=Direction.CALL,
                            rsi=rsi_value,
                            mean_body_strength=mean_strength,
                            is_consecutive_same_direction=True,
                            duration_candles=self._duration_candles,
                            reason_code="MICROTREND_BULLISH_CONTINUATION_CALL",
                            close_position=close_pos_t,
                            a20=a20,
                            ema10=ema10,
                            ema30=ema30,
                        )
                    else:
                        return MicrotrendScalperDecision(
                            direction=None,
                            rsi=rsi_value,
                            mean_body_strength=mean_strength,
                            is_consecutive_same_direction=True,
                            duration_candles=self._duration_candles,
                            reason_code="REJECTED_EMA_TREND_MISMATCH",
                            close_position=close_pos_t,
                            a20=a20,
                            ema10=ema10,
                            ema30=ema30,
                        )
                else:
                    return MicrotrendScalperDecision(
                        direction=None,
                        rsi=rsi_value,
                        mean_body_strength=mean_strength,
                        is_consecutive_same_direction=True,
                        duration_candles=self._duration_candles,
                        reason_code="REJECTED_RSI_OUT_OF_BOUNDS",
                        close_position=close_pos_t,
                        a20=a20,
                        ema10=ema10,
                        ema30=ema30,
                    )
            else:
                return MicrotrendScalperDecision(
                    direction=None,
                    rsi=rsi_value,
                    mean_body_strength=mean_strength,
                    is_consecutive_same_direction=True,
                    duration_candles=self._duration_candles,
                    reason_code="REJECTED_CLOSE_POSITION_TOO_LOW",
                    close_position=close_pos_t,
                    a20=a20,
                    ema10=ema10,
                    ema30=ema30,
                )

        # 2. Bearish evaluation
        if is_all_bearish and has_strength:
            if close_pos_t <= self._put_close_pos_max:
                if self._put_rsi_min <= rsi_value <= self._put_rsi_max:
                    if ema10 < ema30:
                        return MicrotrendScalperDecision(
                            direction=Direction.PUT,
                            rsi=rsi_value,
                            mean_body_strength=mean_strength,
                            is_consecutive_same_direction=True,
                            duration_candles=self._duration_candles,
                            reason_code="MICROTREND_BEARISH_CONTINUATION_PUT",
                            close_position=close_pos_t,
                            a20=a20,
                            ema10=ema10,
                            ema30=ema30,
                        )
                    else:
                        return MicrotrendScalperDecision(
                            direction=None,
                            rsi=rsi_value,
                            mean_body_strength=mean_strength,
                            is_consecutive_same_direction=True,
                            duration_candles=self._duration_candles,
                            reason_code="REJECTED_EMA_TREND_MISMATCH",
                            close_position=close_pos_t,
                            a20=a20,
                            ema10=ema10,
                            ema30=ema30,
                        )
                else:
                    return MicrotrendScalperDecision(
                        direction=None,
                        rsi=rsi_value,
                        mean_body_strength=mean_strength,
                        is_consecutive_same_direction=True,
                        duration_candles=self._duration_candles,
                        reason_code="REJECTED_RSI_OUT_OF_BOUNDS",
                        close_position=close_pos_t,
                        a20=a20,
                        ema10=ema10,
                        ema30=ema30,
                    )
            else:
                return MicrotrendScalperDecision(
                    direction=None,
                    rsi=rsi_value,
                    mean_body_strength=mean_strength,
                    is_consecutive_same_direction=True,
                    duration_candles=self._duration_candles,
                    reason_code="REJECTED_CLOSE_POSITION_TOO_HIGH",
                    close_position=close_pos_t,
                    a20=a20,
                    ema10=ema10,
                    ema30=ema30,
                )

        return MicrotrendScalperDecision(
            direction=None,
            rsi=rsi_value,
            mean_body_strength=mean_strength,
            is_consecutive_same_direction=same_dir,
            duration_candles=self._duration_candles,
            reason_code="NO_SIGNAL",
            close_position=close_pos_t,
            a20=a20,
            ema10=ema10,
            ema30=ema30,
        )

    def evaluate(
        self,
        candles: Sequence[MarketCandle],
        context: RuntimeContext,
    ) -> Direction | None:
        return self.evaluate_decision(candles, context).direction


def iqoption_microtrend_scalper_manifest(
    *,
    release_status: ReleaseStatus = ReleaseStatus.RELEASED,
) -> StrategyManifest:
    """Return the immutable catalog contract for the Microtrend Scalper strategy."""
    return StrategyManifest(
        manifest_version=1,
        strategy_id=IQOPTION_MICROTREND_SCALPER_STRATEGY_ID,
        version=IQOPTION_MICROTREND_SCALPER_STRATEGY_VERSION,
        code_hash=hashlib.sha256(IQOPTION_MICROTREND_SCALPER_ARTIFACT).hexdigest(),
        supported_brokers=(Broker.IQ_OPTION,),
        supported_products=("BINARY_OPTION",),
        supported_timeframes=(IQOPTION_MICROTREND_SCALPER_TIMEFRAME_SECONDS,),
        required_data=(DataRequirement.CLOSED_CANDLES,),
        warmup_candles=IQOPTION_MICROTREND_SCALPER_WARMUP_CANDLES,
        parameter_schema=(),
        risk_class=RiskClass.STANDARD,
        validation_report_id="iqoption-microtrend-scalper-v2-validation",
        release_status=release_status,
        strategy_pack="iqoption-practice-candidates",
    )


__all__ = [
    "IQOPTION_MICROTREND_SCALPER_A20_PERIOD",
    "IQOPTION_MICROTREND_SCALPER_ARTIFACT",
    "IQOPTION_MICROTREND_SCALPER_CALL_CLOSE_POS_MIN",
    "IQOPTION_MICROTREND_SCALPER_CALL_RSI_MAX",
    "IQOPTION_MICROTREND_SCALPER_CALL_RSI_MIN",
    "IQOPTION_MICROTREND_SCALPER_EMA_FAST",
    "IQOPTION_MICROTREND_SCALPER_EMA_SLOW",
    "IQOPTION_MICROTREND_SCALPER_EXPIRY_CANDLES",
    "IQOPTION_MICROTREND_SCALPER_MAX_RANGE_FACTOR",
    "IQOPTION_MICROTREND_SCALPER_MIN_BODY_STRENGTH",
    "IQOPTION_MICROTREND_SCALPER_PUT_CLOSE_POS_MAX",
    "IQOPTION_MICROTREND_SCALPER_PUT_RSI_MAX",
    "IQOPTION_MICROTREND_SCALPER_PUT_RSI_MIN",
    "IQOPTION_MICROTREND_SCALPER_RSI_PERIOD",
    "IQOPTION_MICROTREND_SCALPER_STRATEGY_ID",
    "IQOPTION_MICROTREND_SCALPER_STRATEGY_VERSION",
    "IQOPTION_MICROTREND_SCALPER_TIMEFRAME_SECONDS",
    "IQOPTION_MICROTREND_SCALPER_WARMUP_CANDLES",
    "IQOptionMicrotrendScalperStrategy",
    "MicrotrendScalperDecision",
    "iqoption_microtrend_scalper_manifest",
]
