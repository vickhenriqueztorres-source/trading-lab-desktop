"""Deterministic RSI signal generator for IQ Option Practice validation.

The strategy is intentionally pure: it consumes validated, closed candles and
returns a signal decision.  It never selects an account, stake, or calls a
broker.  Financial admission remains the Core's responsibility.
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

IQOPTION_RSI_STRATEGY_ID = "iqoption-rsi-demo"
IQOPTION_RSI_STRATEGY_VERSION = "1.0.0"
IQOPTION_RSI_TIMEFRAME_SECONDS = 60
IQOPTION_RSI_PERIOD = 14
IQOPTION_RSI_LOWER = Decimal("30")
IQOPTION_RSI_UPPER = Decimal("70")
IQOPTION_RSI_ARTIFACT = b"IQOPTION_RSI_DEMO:WILDER:14:30:70:v1"


@dataclass(frozen=True, slots=True)
class RsiDecision:
    rsi: Decimal
    direction: Direction | None
    reason_code: str


def calculate_wilder_rsi(
    closes: Sequence[Decimal],
    *,
    period: int = IQOPTION_RSI_PERIOD,
) -> Decimal:
    """Calculate the initial Wilder RSI from exactly validated price changes."""

    if period <= 0:
        raise ValueError("RSI period must be positive")
    if len(closes) < period + 1:
        raise ValueError("RSI requires period + 1 closing prices")
    selected = tuple(closes[-(period + 1) :])
    if any(not value.is_finite() or value <= 0 for value in selected):
        raise ValueError("RSI closing prices must be positive finite decimals")

    gains = Decimal(0)
    losses = Decimal(0)
    for previous, current in zip(selected[:-1], selected[1:], strict=True):
        change = current - previous
        if change > 0:
            gains += change
        elif change < 0:
            losses -= change

    average_gain = gains / Decimal(period)
    average_loss = losses / Decimal(period)
    if average_loss == 0:
        return Decimal("100") if average_gain > 0 else Decimal("50")
    if average_gain == 0:
        return Decimal("0")
    relative_strength = average_gain / average_loss
    return Decimal("100") - (Decimal("100") / (Decimal("1") + relative_strength))


class IQOptionRsiDemoStrategy:
    """RSI(14) candidate restricted to 1-minute IQ Option Practice candles."""

    @property
    def artifact_bytes(self) -> bytes:
        return IQOPTION_RSI_ARTIFACT

    def evaluate_decision(
        self,
        candles: Sequence[MarketCandle],
        context: RuntimeContext,
    ) -> RsiDecision:
        if context.broker is not Broker.IQ_OPTION:
            raise ValueError("RSI demo strategy requires IQ Option")
        if context.timeframe_seconds != IQOPTION_RSI_TIMEFRAME_SECONDS:
            raise ValueError("RSI demo strategy requires closed 1-minute candles")
        if len(candles) < IQOPTION_RSI_PERIOD + 1:
            raise ValueError("RSI demo strategy is still warming up")
        if any(
            candle.broker is not Broker.IQ_OPTION
            or candle.broker_symbol != context.symbol
            or candle.timeframe_seconds != context.timeframe_seconds
            or not candle.is_closed
            for candle in candles
        ):
            raise ValueError("RSI demo strategy received an invalid candle series")

        rsi = calculate_wilder_rsi(tuple(candle.close for candle in candles))
        if rsi < IQOPTION_RSI_LOWER:
            return RsiDecision(rsi, Direction.CALL, "RSI_OVERSOLD")
        if rsi > IQOPTION_RSI_UPPER:
            return RsiDecision(rsi, Direction.PUT, "RSI_OVERBOUGHT")
        return RsiDecision(rsi, None, "RSI_NEUTRAL")

    def evaluate(
        self,
        candles: Sequence[MarketCandle],
        context: RuntimeContext,
    ) -> Direction | None:
        return self.evaluate_decision(candles, context).direction


def iqoption_rsi_manifest(
    *,
    release_status: ReleaseStatus = ReleaseStatus.RELEASED,
) -> StrategyManifest:
    """Return the immutable catalog contract for the Practice RSI candidate."""

    return StrategyManifest(
        manifest_version=1,
        strategy_id=IQOPTION_RSI_STRATEGY_ID,
        version=IQOPTION_RSI_STRATEGY_VERSION,
        code_hash=hashlib.sha256(IQOPTION_RSI_ARTIFACT).hexdigest(),
        supported_brokers=(Broker.IQ_OPTION,),
        supported_products=("BINARY_OPTION",),
        supported_timeframes=(IQOPTION_RSI_TIMEFRAME_SECONDS,),
        required_data=(DataRequirement.CLOSED_CANDLES,),
        warmup_candles=IQOPTION_RSI_PERIOD + 1,
        parameter_schema=(),
        risk_class=RiskClass.ELEVATED,
        validation_report_id="iqoption-rsi-demo-v1-validation",
        release_status=release_status,
        strategy_pack="iqoption-practice-candidates",
    )


__all__ = [
    "IQOPTION_RSI_ARTIFACT",
    "IQOPTION_RSI_LOWER",
    "IQOPTION_RSI_PERIOD",
    "IQOPTION_RSI_STRATEGY_ID",
    "IQOPTION_RSI_STRATEGY_VERSION",
    "IQOPTION_RSI_TIMEFRAME_SECONDS",
    "IQOPTION_RSI_UPPER",
    "IQOptionRsiDemoStrategy",
    "RsiDecision",
    "calculate_wilder_rsi",
    "iqoption_rsi_manifest",
]
