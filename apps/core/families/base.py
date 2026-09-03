"""Base classes and helpers for strategy families (R-BOT-5)."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from apps.core.families.primitives.base import Candle, Indicator, Output
from packages.domain.market import MarketCandle
from packages.domain.models import Direction
from packages.strategies.models import RuntimeContext


def is_within_trading_hours(hours_utc: Sequence[int] | None, now_utc: datetime) -> bool:
    """Check if now_utc falls within hours_utc range."""
    if not hours_utc or len(hours_utc) < 2:
        return True
    hour = now_utc.astimezone(UTC).hour
    start_h, end_h = hours_utc[0], hours_utc[1]
    if start_h == end_h:
        return True
    if start_h < end_h:
        return start_h <= hour < end_h
    # Crosses midnight (e.g. 22 to 4)
    return hour >= start_h or hour < end_h


def agreed_direction(
    regime_out: Output | None,
    trigger_out: Output | None,
    confirm_out: Output | None,
    *,
    regime_allowed: bool = True,
) -> Direction | None:
    """Evaluate 3-primitive consensus agreement (Regime + Trigger + Confirm)."""
    if regime_out is None or trigger_out is None or confirm_out is None:
        return None
    if not regime_allowed:
        return None
    if not _regime_allows(regime_out):
        return None
    if trigger_out.direction in {"call", "put"} and trigger_out.direction == confirm_out.direction:
        if (
            regime_out.direction in {"call", "put"}
            and regime_out.direction != trigger_out.direction
        ):
            return None
        return Direction.CALL if trigger_out.direction == "call" else Direction.PUT
    return None


def _regime_allows(output: Output) -> bool:
    if output.direction in {"call", "put"}:
        return True
    return output.value is None or output.value > Decimal("0")


class FamilyStrategyBase:
    """Base strategy implementation for manifest-instantiated families."""

    family_name: str

    def __init__(
        self,
        strategy_key: str,
        params: dict[str, Any],
        hours_utc: Sequence[int] | None = None,
        asset: str = "",
        timeframe: str = "M1",
    ) -> None:
        self.strategy_key = strategy_key
        self.params = dict(params)
        self.hours_utc = tuple(hours_utc) if hours_utc else ()
        self.asset = asset
        self.timeframe = timeframe
        self._regime: Indicator
        self._trigger: Indicator
        self._confirm: Indicator
        self._warmup_required = 0

    @property
    def artifact_bytes(self) -> bytes:
        return f"BOT_FAMILY:{self.family_name}:{self.strategy_key}".encode()

    @property
    def warmup_required(self) -> int:
        return self._warmup_required

    def reset(self) -> None:
        self._regime.reset()
        self._trigger.reset()
        self._confirm.reset()

    def update_candle(self, candle: Candle) -> Direction | None:
        """Incremental candle feed returning directional consensus."""
        r_out = self._regime.update(candle)
        t_out = self._trigger.update(candle)
        c_out = self._confirm.update(candle)
        regime_allowed = self._check_composition_gate(r_out)
        return agreed_direction(r_out, t_out, c_out, regime_allowed=regime_allowed)

    def _check_composition_gate(self, regime_out: Output | None) -> bool:
        """Override in subclasses that have specific composition gates (F1, F4)."""
        return True

    def evaluate(
        self,
        candles: Sequence[MarketCandle],
        context: RuntimeContext,
    ) -> Direction | None:
        """Evaluate closed candle history, respecting trading hours filter."""
        if not candles:
            return None
        last_candle = candles[-1]
        close_time = last_candle.close_time.astimezone(UTC)
        if not is_within_trading_hours(self.hours_utc, close_time):
            return None

        self.reset()
        decision: Direction | None = None
        for mc in candles:
            ts = int(mc.close_time.timestamp())
            ts_aligned = ts - (ts % 60)
            c = Candle(
                ts=ts_aligned,
                o=mc.open,
                h=mc.high,
                l=mc.low,
                c=mc.close,
                tick_vol=1,
            )
            decision = self.update_candle(c)
        return decision
