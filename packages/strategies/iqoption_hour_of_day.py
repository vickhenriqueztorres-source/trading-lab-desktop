"""Deterministic HourOfDayConditional statistical signal generator for IQ Option.

Rule:
- Examines up to 2,000 previous candles M1.
- Filters and compares only candles with the same UTC hour as the current candle.
- Requires at least 60 directional observations (close != open).
- If >= 55% of the directional observations were bullish -> CALL.
- If >= 55% of the directional observations were bearish -> PUT.
- Otherwise -> NONE.
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

IQOPTION_HOUR_OF_DAY_STRATEGY_ID = "iqoption-hour-of-day"
IQOPTION_HOUR_OF_DAY_STRATEGY_VERSION = "1.0.0"
IQOPTION_HOUR_OF_DAY_TIMEFRAME_SECONDS = 60
IQOPTION_HOUR_OF_DAY_MIN_OBSERVATIONS = 60
IQOPTION_HOUR_OF_DAY_PROBABILITY_THRESHOLD = Decimal("0.55")
IQOPTION_HOUR_OF_DAY_MAX_LOOKBACK = 2000
IQOPTION_HOUR_OF_DAY_EXPIRY_CANDLES = 1
IQOPTION_HOUR_OF_DAY_WARMUP_CANDLES = 60
IQOPTION_HOUR_OF_DAY_ARTIFACT = b"IQOPTION_HOUR_OF_DAY:HIST:2000:MINOBS:60:TH:0.55:EXP1:v1"


@dataclass(frozen=True, slots=True)
class HourOfDayDecision:
    direction: Direction | None
    utc_hour: int
    directional_observations: int
    bullish_ratio: Decimal
    bearish_ratio: Decimal
    duration_candles: int
    reason_code: str


class IQOptionHourOfDayStrategy:
    """Hour-of-day conditional probabilistic edge strategy."""

    def __init__(
        self,
        min_observations: int = IQOPTION_HOUR_OF_DAY_MIN_OBSERVATIONS,
        probability_threshold: Decimal = IQOPTION_HOUR_OF_DAY_PROBABILITY_THRESHOLD,
        max_lookback: int = IQOPTION_HOUR_OF_DAY_MAX_LOOKBACK,
        duration_candles: int = IQOPTION_HOUR_OF_DAY_EXPIRY_CANDLES,
    ) -> None:
        if min_observations <= 0:
            raise ValueError("Minimum observations must be positive")
        if probability_threshold <= 0 or probability_threshold > 1:
            raise ValueError("Probability threshold must be between 0 and 1")
        if duration_candles <= 0:
            raise ValueError("Duration candles must be positive")
        self._min_observations = min_observations
        self._threshold = probability_threshold
        self._max_lookback = max_lookback
        self._duration_candles = duration_candles

    @property
    def artifact_bytes(self) -> bytes:
        return IQOPTION_HOUR_OF_DAY_ARTIFACT

    @property
    def warmup_required(self) -> int:
        return self._min_observations

    @property
    def duration_candles(self) -> int:
        return self._duration_candles

    def evaluate_decision(
        self,
        candles: Sequence[MarketCandle],
        context: RuntimeContext,
    ) -> HourOfDayDecision:
        if context.broker is not Broker.IQ_OPTION:
            raise ValueError("HourOfDay strategy requires IQ Option")
        if context.timeframe_seconds != IQOPTION_HOUR_OF_DAY_TIMEFRAME_SECONDS:
            raise ValueError("HourOfDay strategy requires closed 1-minute candles")
        if not candles:
            raise ValueError("HourOfDay strategy received empty candle series")
        if any(
            candle.broker is not Broker.IQ_OPTION
            or candle.broker_symbol != context.symbol
            or candle.timeframe_seconds != context.timeframe_seconds
            or not candle.is_closed
            for candle in candles
        ):
            raise ValueError("HourOfDay strategy received an invalid candle series")

        current_candle = candles[-1]
        target_utc_hour = current_candle.open_time.hour

        sample = candles[-self._max_lookback :]
        matching_hour = [c for c in sample if c.open_time.hour == target_utc_hour]
        directional = [c for c in matching_hour if c.close != c.open]
        total_obs = len(directional)

        if total_obs < self._min_observations:
            return HourOfDayDecision(
                direction=None,
                utc_hour=target_utc_hour,
                directional_observations=total_obs,
                bullish_ratio=Decimal(0),
                bearish_ratio=Decimal(0),
                duration_candles=self._duration_candles,
                reason_code="INSUFFICIENT_OBSERVATIONS",
            )

        bullish_count = sum(1 for c in directional if c.close > c.open)
        bearish_count = sum(1 for c in directional if c.close < c.open)
        total_dec = Decimal(total_obs)
        bullish_ratio = Decimal(bullish_count) / total_dec
        bearish_ratio = Decimal(bearish_count) / total_dec

        if bullish_ratio >= self._threshold:
            return HourOfDayDecision(
                direction=Direction.CALL,
                utc_hour=target_utc_hour,
                directional_observations=total_obs,
                bullish_ratio=bullish_ratio,
                bearish_ratio=bearish_ratio,
                duration_candles=self._duration_candles,
                reason_code="HOUR_CONDITIONAL_BULLISH_EDGE",
            )

        if bearish_ratio >= self._threshold:
            return HourOfDayDecision(
                direction=Direction.PUT,
                utc_hour=target_utc_hour,
                directional_observations=total_obs,
                bullish_ratio=bullish_ratio,
                bearish_ratio=bearish_ratio,
                duration_candles=self._duration_candles,
                reason_code="HOUR_CONDITIONAL_BEARISH_EDGE",
            )

        return HourOfDayDecision(
            direction=None,
            utc_hour=target_utc_hour,
            directional_observations=total_obs,
            bullish_ratio=bullish_ratio,
            bearish_ratio=bearish_ratio,
            duration_candles=self._duration_candles,
            reason_code="NO_SIGNAL",
        )

    def evaluate(
        self,
        candles: Sequence[MarketCandle],
        context: RuntimeContext,
    ) -> Direction | None:
        return self.evaluate_decision(candles, context).direction


def iqoption_hour_of_day_manifest(
    *,
    release_status: ReleaseStatus = ReleaseStatus.RELEASED,
) -> StrategyManifest:
    """Return the immutable catalog contract for the HourOfDay strategy."""
    return StrategyManifest(
        manifest_version=1,
        strategy_id=IQOPTION_HOUR_OF_DAY_STRATEGY_ID,
        version=IQOPTION_HOUR_OF_DAY_STRATEGY_VERSION,
        code_hash=hashlib.sha256(IQOPTION_HOUR_OF_DAY_ARTIFACT).hexdigest(),
        supported_brokers=(Broker.IQ_OPTION,),
        supported_products=("BINARY_OPTION",),
        supported_timeframes=(IQOPTION_HOUR_OF_DAY_TIMEFRAME_SECONDS,),
        required_data=(DataRequirement.CLOSED_CANDLES,),
        warmup_candles=IQOPTION_HOUR_OF_DAY_WARMUP_CANDLES,
        parameter_schema=(),
        risk_class=RiskClass.ELEVATED,
        validation_report_id="iqoption-hour-of-day-v1-validation",
        release_status=release_status,
        strategy_pack="iqoption-practice-candidates",
    )


__all__ = [
    "IQOPTION_HOUR_OF_DAY_ARTIFACT",
    "IQOPTION_HOUR_OF_DAY_EXPIRY_CANDLES",
    "IQOPTION_HOUR_OF_DAY_MAX_LOOKBACK",
    "IQOPTION_HOUR_OF_DAY_MIN_OBSERVATIONS",
    "IQOPTION_HOUR_OF_DAY_PROBABILITY_THRESHOLD",
    "IQOPTION_HOUR_OF_DAY_STRATEGY_ID",
    "IQOPTION_HOUR_OF_DAY_STRATEGY_VERSION",
    "IQOPTION_HOUR_OF_DAY_TIMEFRAME_SECONDS",
    "IQOPTION_HOUR_OF_DAY_WARMUP_CANDLES",
    "IQOptionHourOfDayStrategy",
    "HourOfDayDecision",
    "iqoption_hour_of_day_manifest",
]
