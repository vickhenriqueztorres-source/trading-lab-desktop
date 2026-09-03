"""Incremental replay simulator; approval path never uses vector scan (R-RES-5)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal

from primitives import Candle
from primitives.base import Direction, Indicator, Output
from primitives.registry import REGISTRY

from strategy_lab.research.candidate import Candidate
from strategy_lab.research.outcome import settle
from strategy_lab.research.payout_lookup import PayoutLookup


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
class UpdateTrace:
    indicator_name: str
    step_ts: int
    candle_ts: int


@dataclass(frozen=True)
class TradeLog:
    trades: tuple[Trade, ...]
    excluded_missing_payout: int
    update_trace: tuple[UpdateTrace, ...] = ()

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
) -> TradeLog:
    """Feed each primitive with candle t, decide at t, settle only afterward with t+1."""
    ordered = sorted(candles, key=lambda candle: candle.ts)
    indicators = [
        _make_indicator(candidate.regime, candidate, registry),
        _make_indicator(candidate.trigger, candidate, registry),
        _make_indicator(candidate.confirm, candidate, registry),
    ]
    trades: list[Trade] = []
    traces: list[UpdateTrace] = []
    excluded_missing_payout = 0
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
        direction = _agreed_direction(outputs)
        if direction == "none":
            continue
        payout = payout_lookup.payout(candidate.asset, candle.ts)
        if payout is None:
            excluded_missing_payout += 1
            continue
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
    return TradeLog(
        trades=tuple(trades),
        excluded_missing_payout=excluded_missing_payout,
        update_trace=tuple(traces),
    )


def _make_indicator(
    name: str,
    candidate: Candidate,
    registry: Mapping[str, type[Indicator]],
) -> Indicator:
    indicator_type = registry[name]
    return indicator_type(**candidate.params_for(name))


def _agreed_direction(outputs: list[Output | None]) -> Direction:
    if any(output is None for output in outputs):
        return "none"
    assert all(output is not None for output in outputs)
    regime, trigger, confirm = outputs
    assert regime is not None and trigger is not None and confirm is not None
    if not _regime_allows(regime):
        return "none"
    if trigger.direction in {"call", "put"} and trigger.direction == confirm.direction:
        if regime.direction in {"call", "put"} and regime.direction != trigger.direction:
            return "none"
        return trigger.direction
    return "none"


def _regime_allows(output: Output) -> bool:
    if output.direction in {"call", "put"}:
        return True
    return output.value is None or output.value > Decimal("0")
