"""Incremental replay simulator; approval path never uses vector scan (R-RES-4/5)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Literal, cast

from primitives import Candle
from primitives.base import Direction, Indicator, Output
from primitives.registry import REGISTRY

from strategy_lab.research.candidate import Candidate
from strategy_lab.research.outcome import settle
from strategy_lab.research.payout_lookup import PayoutLookup

type ReplayStage = Literal[
    "OK",
    "WARMING_UP",
    "OUTSIDE_HOURS",
    "REGIME",
    "TRIGGER",
    "CONFIRM",
    "DISAGREE",
    "TICK_VOLUME_UNAVAILABLE",
    "NO_SIGNAL",
    "SETTLEMENT_GAP",
    "MISSING_PAYOUT",
]


@dataclass(frozen=True)
class Trade:
    ts: int
    asset: str
    direction: Direction
    won: bool
    payout_return_ratio: Decimal
    profit_ratio: Decimal
    update_count_at_signal: int


@dataclass(frozen=True)
class ReplaySignal:
    ts: int
    asset: str
    stage: ReplayStage
    direction: Direction
    regime_direction: Direction | None
    trigger_direction: Direction | None
    confirm_direction: Direction | None
    regime_value: Decimal | None
    trigger_value: Decimal | None
    confirm_value: Decimal | None


@dataclass(frozen=True)
class UpdateTrace:
    indicator_name: str
    step_ts: int
    candle_ts: int


@dataclass(frozen=True)
class TradeLog:
    trades: tuple[Trade, ...]
    excluded_missing_payout: int
    excluded_settlement_gap: int = 0
    update_trace: tuple[UpdateTrace, ...] = ()
    signal_trace: tuple[ReplaySignal, ...] = ()

    @property
    def wins(self) -> int:
        return sum(1 for trade in self.trades if trade.won)

    @property
    def losses(self) -> int:
        return len(self.trades) - self.wins

    @property
    def p_hat(self) -> Decimal:
        if not self.trades:
            return Decimal("0")
        return Decimal(self.wins) / Decimal(len(self.trades))


def replay_candidate(
    candidate: Candidate,
    candles: list[Candle],
    payout_lookup: PayoutLookup,
    *,
    registry: Mapping[str, type[Indicator]] = REGISTRY,
    trace_updates: bool = False,
    trace_signals: bool = False,
) -> TradeLog:
    """Feed candle t, decide at t, then settle only with the next complete TF bucket."""
    ordered = sorted(candles, key=lambda candle: candle.ts)
    indicators = [
        _make_indicator(candidate.regime, candidate, registry),
        _make_indicator(candidate.trigger, candidate, registry),
        _make_indicator(candidate.confirm, candidate, registry),
    ]
    warmup_required = max(indicator.warmup_required for indicator in indicators)
    trades: list[Trade] = []
    traces: list[UpdateTrace] = []
    signal_trace: list[ReplaySignal] = []
    excluded_missing_payout = 0
    excluded_settlement_gap = 0
    update_count = 0
    for index, candle in enumerate(ordered[:-1]):
        outputs: list[Output | None] = []
        for indicator in indicators:
            outputs.append(indicator.update(candle))
            update_count += 1
            if trace_updates:
                traces.append(
                    UpdateTrace(
                        indicator_name=indicator.name,
                        step_ts=candle.ts,
                        candle_ts=candle.ts,
                    )
                )

        direction: Direction
        stage: ReplayStage
        if (
            _requires_tick_volume(candidate)
            and index + 1 >= warmup_required
            and any(item.tick_vol is None for item in ordered[: index + 1])
        ):
            direction, stage = "none", "TICK_VOLUME_UNAVAILABLE"
        else:
            direction, stage = _evaluate_outputs(candidate, outputs, candle.ts)
        if direction != "none":
            expected_next_ts = candle.ts + _timeframe_seconds(candidate.tf)
            if ordered[index + 1].ts != expected_next_ts:
                excluded_settlement_gap += 1
                stage = "SETTLEMENT_GAP"
            else:
                payout = payout_lookup.payout(candidate.asset, candle.ts)
                if payout is None:
                    excluded_missing_payout += 1
                    stage = "MISSING_PAYOUT"
                else:
                    won = settle(direction, candle, ordered[index + 1])
                    trades.append(
                        Trade(
                            ts=candle.ts,
                            asset=candidate.asset,
                            direction=direction,
                            won=won,
                            payout_return_ratio=payout,
                            profit_ratio=payout if won else Decimal("-1"),
                            update_count_at_signal=update_count,
                        )
                    )

        if trace_signals:
            regime, trigger, confirm = _unpack_outputs(outputs)
            signal_trace.append(
                ReplaySignal(
                    ts=candle.ts,
                    asset=candidate.asset,
                    stage=stage,
                    direction=direction,
                    regime_direction=None if regime is None else regime.direction,
                    trigger_direction=None if trigger is None else trigger.direction,
                    confirm_direction=None if confirm is None else confirm.direction,
                    regime_value=None if regime is None else regime.value,
                    trigger_value=None if trigger is None else trigger.value,
                    confirm_value=None if confirm is None else confirm.value,
                )
            )

    return TradeLog(
        trades=tuple(trades),
        excluded_missing_payout=excluded_missing_payout,
        excluded_settlement_gap=excluded_settlement_gap,
        update_trace=tuple(traces),
        signal_trace=tuple(signal_trace),
    )


def _make_indicator(
    name: str,
    candidate: Candidate,
    registry: Mapping[str, type[Indicator]],
) -> Indicator:
    indicator_type = registry[name]
    if name == "session_window":
        start_h, end_h = candidate.hours
        constructor = cast(Any, indicator_type)
        return cast(
            Indicator,
            constructor(start_minute=start_h * 60, end_minute=end_h * 60),
        )
    return indicator_type(**candidate.params_for(name))


def _evaluate_outputs(
    candidate: Candidate, outputs: list[Output | None], ts: int
) -> tuple[Direction, ReplayStage]:
    if any(output is None for output in outputs):
        return "none", "WARMING_UP"
    regime, trigger, confirm = _unpack_outputs(outputs)
    assert regime is not None and trigger is not None and confirm is not None
    if not _within_hours(candidate.hours, ts):
        return "none", "OUTSIDE_HOURS"
    if _requires_tick_volume(candidate) and confirm.value is None and confirm.direction == "none":
        return "none", "TICK_VOLUME_UNAVAILABLE"
    if not _composition_gate_allows(candidate, regime):
        return "none", "REGIME"
    if not _regime_allows(regime):
        return "none", "REGIME"
    if trigger.direction == "none" and confirm.direction == "none":
        return "none", "NO_SIGNAL"
    if trigger.direction not in {"call", "put"}:
        return "none", "TRIGGER"
    if confirm.direction not in {"call", "put"}:
        return "none", "CONFIRM"
    if trigger.direction == confirm.direction:
        if regime.direction in {"call", "put"} and regime.direction != trigger.direction:
            return "none", "DISAGREE"
        return trigger.direction, "OK"
    return "none", "DISAGREE"


def _regime_allows(output: Output) -> bool:
    if output.direction in {"call", "put"}:
        return True
    return output.value is None or output.value > Decimal("0")


def _unpack_outputs(
    outputs: list[Output | None],
) -> tuple[Output | None, Output | None, Output | None]:
    if len(outputs) != 3:
        raise ValueError("RES_REPLAY_OUTPUT_SHAPE")
    return outputs[0], outputs[1], outputs[2]


def _composition_gate_allows(candidate: Candidate, regime_out: Output) -> bool:
    if candidate.family == "F1":
        threshold = _gate_decimal(candidate, "adx_max", Decimal("20"))
        return regime_out.value is not None and regime_out.value <= threshold
    if candidate.family == "F4":
        threshold = _gate_decimal(candidate, "width_ratio_max", Decimal("0.5"))
        return regime_out.value is not None and regime_out.value <= threshold
    return True


def _gate_decimal(candidate: Candidate, key: str, default: Decimal) -> Decimal:
    for params in candidate.params.values():
        value = params.get(key)
        if value is not None:
            return Decimal(str(value))
    return default


def _within_hours(hours: tuple[int, int], ts: int) -> bool:
    if len(hours) < 2:
        return True
    start_h, end_h = hours
    hour = (ts % 86400) // 3600
    if start_h == end_h:
        return True
    if start_h < end_h:
        return start_h <= hour < end_h
    return hour >= start_h or hour < end_h


def _requires_tick_volume(candidate: Candidate) -> bool:
    return "tick_volume_ratio" in {
        candidate.regime,
        candidate.trigger,
        candidate.confirm,
        *candidate.params.keys(),
    }


def _timeframe_seconds(timeframe: str) -> int:
    if timeframe == "M1":
        return 60
    if timeframe == "M5":
        return 300
    if timeframe == "M15":
        return 900
    raise ValueError("RES_TIMEFRAME_UNSUPPORTED")
