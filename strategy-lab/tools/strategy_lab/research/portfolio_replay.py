"""Portfolio-level executable-frequency replay (CAT-11, R-RES-4/5/9/11).

This module consumes already-replayed decision opportunities and applies the
public execution contract that matters for a client bot: deterministic
arbitration, closed-candle timing, latency/TTL, payout availability, capital
limits, cooldown, simulated execution failures, and exactly one order in flight.

It is intentionally data-only. It never imports the desktop bot and never talks
to a broker; external execution must be proven elsewhere.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal, cast

from primitives.base import Direction

MONEY_ZERO = Decimal("0")
ONE_DAY_SECONDS = Decimal(86_400)
DEFAULT_STAKE = Decimal("1")
DEFAULT_CAPITAL = Decimal("1000")
DEFAULT_SIGNAL_TTL_S = 30


class PortfolioDecisionReason(StrEnum):
    """Stable reason codes for each portfolio replay opportunity."""

    ELIGIBLE = "ELIGIBLE"
    CONFLICT = "CONFLICT"
    ORDER_IN_FLIGHT = "ORDER_IN_FLIGHT"
    MISSING_PAYOUT = "MISSING_PAYOUT"
    OUTSIDE_HOURS = "OUTSIDE_HOURS"
    WARMUP = "WARMUP"
    STALE = "STALE"
    RISK = "RISK"
    DEADLINE_EXPIRED = "DEADLINE_EXPIRED"
    EXECUTION = "EXECUTION"
    RECONNECTING = "RECONNECTING"
    CANDLE_DELAYED = "CANDLE_DELAYED"
    INVALID_DIRECTION = "INVALID_DIRECTION"


type BlockingReason = Literal[
    "CONFLICT",
    "ORDER_IN_FLIGHT",
    "MISSING_PAYOUT",
    "OUTSIDE_HOURS",
    "WARMUP",
    "STALE",
    "RISK",
    "DEADLINE_EXPIRED",
    "EXECUTION",
    "RECONNECTING",
    "CANDLE_DELAYED",
    "INVALID_DIRECTION",
]


@dataclass(frozen=True, slots=True)
class PortfolioOpportunity:
    """A single replayed signal candidate before portfolio arbitration."""

    recipe_key: str
    asset: str
    timeframe_s: int
    signal_ts: int
    direction: Direction
    payout_return_ratio: Decimal | None
    won: bool | None
    duration_s: int = 60
    stake: Decimal | None = None
    priority: int = 0
    pre_gate_reason: PortfolioDecisionReason | None = None
    execution_failure_reason: PortfolioDecisionReason | None = None
    source_case_id: str | None = None

    def __post_init__(self) -> None:
        if not self.recipe_key or not self.asset:
            raise ValueError("RES_PORTFOLIO_OPPORTUNITY_IDENTITY_INVALID")
        if self.timeframe_s <= 0 or self.duration_s <= 0:
            raise ValueError("RES_PORTFOLIO_TIMEFRAME_INVALID")
        if self.signal_ts <= 0:
            raise ValueError("RES_PORTFOLIO_SIGNAL_TS_INVALID")
        if self.direction not in {"call", "put", "none"}:
            raise ValueError("RES_PORTFOLIO_DIRECTION_INVALID")
        if self.payout_return_ratio is not None and self.payout_return_ratio < MONEY_ZERO:
            raise ValueError("RES_PORTFOLIO_PAYOUT_INVALID")
        if self.stake is not None and self.stake <= MONEY_ZERO:
            raise ValueError("RES_PORTFOLIO_STAKE_INVALID")
        if self.pre_gate_reason == PortfolioDecisionReason.ELIGIBLE:
            raise ValueError("RES_PORTFOLIO_PRE_GATE_REASON_INVALID")
        if self.execution_failure_reason == PortfolioDecisionReason.ELIGIBLE:
            raise ValueError("RES_PORTFOLIO_EXECUTION_REASON_INVALID")

    @property
    def opportunity_id(self) -> str:
        return _stable_sha256(
            {
                "asset": self.asset,
                "direction": self.direction,
                "recipe_key": self.recipe_key,
                "signal_ts": self.signal_ts,
                "source_case_id": self.source_case_id,
                "timeframe_s": self.timeframe_s,
            }
        )


@dataclass(frozen=True, slots=True)
class PortfolioReplayConfig:
    """Execution assumptions used by the portfolio replay."""

    snapshot_id: str
    seed: int
    starting_capital: Decimal = DEFAULT_CAPITAL
    stake: Decimal = DEFAULT_STAKE
    stop_loss_abs: Decimal | None = None
    take_profit_abs: Decimal | None = None
    max_consecutive_losses: int | None = None
    cooldown_after_loss_s: int = 0
    execution_delay_s: int = 0
    signal_ttl_s: int = DEFAULT_SIGNAL_TTL_S
    max_orders_in_flight: int = 1
    period_start_ts: int | None = None
    period_end_ts: int | None = None

    def __post_init__(self) -> None:
        if not self.snapshot_id:
            raise ValueError("RES_PORTFOLIO_SNAPSHOT_REQUIRED")
        if self.starting_capital <= MONEY_ZERO or self.stake <= MONEY_ZERO:
            raise ValueError("RES_PORTFOLIO_CAPITAL_INVALID")
        if self.max_orders_in_flight != 1:
            raise ValueError("RES_PORTFOLIO_ONE_ORDER_REQUIRED")
        if self.cooldown_after_loss_s < 0 or self.execution_delay_s < 0 or self.signal_ttl_s < 0:
            raise ValueError("RES_PORTFOLIO_TIMING_INVALID")
        if self.max_consecutive_losses is not None and self.max_consecutive_losses <= 0:
            raise ValueError("RES_PORTFOLIO_LOSS_LIMIT_INVALID")
        if (
            self.period_start_ts is not None
            and self.period_end_ts is not None
            and self.period_end_ts <= self.period_start_ts
        ):
            raise ValueError("RES_PORTFOLIO_PERIOD_INVALID")


@dataclass(frozen=True, slots=True)
class PortfolioDecision:
    opportunity_id: str
    recipe_key: str
    asset: str
    timeframe_s: int
    signal_ts: int
    direction: Direction
    reason: PortfolioDecisionReason
    execution_ts: int | None = None
    expiry_ts: int | None = None
    pnl: Decimal = MONEY_ZERO
    balance_after: Decimal | None = None
    detail: str = ""

    @property
    def executed(self) -> bool:
        return self.reason == PortfolioDecisionReason.ELIGIBLE


@dataclass(frozen=True, slots=True)
class PortfolioTrade:
    opportunity_id: str
    recipe_key: str
    asset: str
    timeframe_s: int
    signal_ts: int
    execution_ts: int
    expiry_ts: int
    direction: Direction
    stake: Decimal
    payout_return_ratio: Decimal
    won: bool
    pnl: Decimal
    balance_after: Decimal


@dataclass(frozen=True, slots=True)
class RecipeContribution:
    recipe_key: str
    signals: int
    executed: int
    blocked: Mapping[PortfolioDecisionReason, int]
    pnl: Decimal
    marginal_executable_delta: int = 0


@dataclass(frozen=True, slots=True)
class PortfolioReplayResult:
    config: PortfolioReplayConfig
    decisions: tuple[PortfolioDecision, ...]
    trades: tuple[PortfolioTrade, ...]
    period_start_ts: int
    period_end_ts: int
    signals_total: int
    rejections_by_reason: Mapping[PortfolioDecisionReason, int]
    raw_signals_per_day: Decimal
    executable_operations_per_day: Decimal
    executed_by_utc_hour: Mapping[int, int]
    idle_seconds: int
    overlap_events: int
    concentration_by_recipe: Mapping[str, Decimal]
    concentration_by_asset: Mapping[str, Decimal]
    contribution_by_recipe: Mapping[str, RecipeContribution]
    violations: tuple[str, ...]
    reproducibility_hash: str

    @property
    def executed_total(self) -> int:
        return len(self.trades)

    @property
    def net_pnl(self) -> Decimal:
        total = MONEY_ZERO
        for trade in self.trades:
            total += trade.pnl
        return total

    def to_dict(self) -> dict[str, object]:
        """Return a canonical JSON-ready report."""
        return {
            "config": {
                "cooldown_after_loss_s": self.config.cooldown_after_loss_s,
                "execution_delay_s": self.config.execution_delay_s,
                "max_consecutive_losses": self.config.max_consecutive_losses,
                "max_orders_in_flight": self.config.max_orders_in_flight,
                "period_end_ts": self.period_end_ts,
                "period_start_ts": self.period_start_ts,
                "seed": self.config.seed,
                "signal_ttl_s": self.config.signal_ttl_s,
                "snapshot_id": self.config.snapshot_id,
                "stake": _decimal_text(self.config.stake),
                "starting_capital": _decimal_text(self.config.starting_capital),
                "stop_loss_abs": _optional_decimal_text(self.config.stop_loss_abs),
                "take_profit_abs": _optional_decimal_text(self.config.take_profit_abs),
            },
            "decisions": [_decision_payload(decision) for decision in self.decisions],
            "executed_by_utc_hour": {
                str(k): v for k, v in sorted(self.executed_by_utc_hour.items())
            },
            "executable_operations_per_day": _decimal_text(self.executable_operations_per_day),
            "idle_seconds": self.idle_seconds,
            "net_pnl": _decimal_text(self.net_pnl),
            "overlap_events": self.overlap_events,
            "raw_signals_per_day": _decimal_text(self.raw_signals_per_day),
            "rejections_by_reason": {
                reason.value: count for reason, count in sorted(self.rejections_by_reason.items())
            },
            "signals_total": self.signals_total,
            "trades": [_trade_payload(trade) for trade in self.trades],
            "violations": list(self.violations),
            "concentration_by_asset": {
                key: _decimal_text(val) for key, val in sorted(self.concentration_by_asset.items())
            },
            "concentration_by_recipe": {
                key: _decimal_text(val) for key, val in sorted(self.concentration_by_recipe.items())
            },
            "contribution_by_recipe": {
                key: {
                    "blocked": {reason.value: n for reason, n in sorted(item.blocked.items())},
                    "executed": item.executed,
                    "marginal_executable_delta": item.marginal_executable_delta,
                    "pnl": _decimal_text(item.pnl),
                    "signals": item.signals,
                }
                for key, item in sorted(self.contribution_by_recipe.items())
            },
            "reproducibility_hash": self.reproducibility_hash,
        }


def run_portfolio_replay(
    opportunities: Sequence[PortfolioOpportunity],
    config: PortfolioReplayConfig,
    *,
    compute_marginal: bool = True,
) -> PortfolioReplayResult:
    """Replay executable portfolio frequency from raw opportunities."""
    ordered = sorted(opportunities, key=_opportunity_sort_key)
    decisions: list[PortfolioDecision] = []
    trades: list[PortfolioTrade] = []
    balance = config.starting_capital
    net_pnl = MONEY_ZERO
    consecutive_losses = 0
    cooldown_until: int | None = None
    open_until: int | None = None

    for opportunity in _arbitrate_opportunities(ordered, decisions):
        execution_ts = opportunity.signal_ts + config.execution_delay_s
        expiry_ts = opportunity.signal_ts + opportunity.duration_s
        stake = opportunity.stake or config.stake
        blocking_reason = _execution_block_reason(
            opportunity,
            config,
            execution_ts=execution_ts,
            expiry_ts=expiry_ts,
            open_until=open_until,
            cooldown_until=cooldown_until,
            net_pnl=net_pnl,
            consecutive_losses=consecutive_losses,
        )
        if blocking_reason is not None:
            decisions.append(
                _blocked_decision(opportunity, blocking_reason, execution_ts, expiry_ts)
            )
            continue

        assert opportunity.payout_return_ratio is not None
        assert opportunity.won is not None
        pnl = stake * opportunity.payout_return_ratio if opportunity.won else -stake
        balance += pnl
        net_pnl += pnl
        if opportunity.won:
            consecutive_losses = 0
        else:
            consecutive_losses += 1
            if config.cooldown_after_loss_s > 0:
                cooldown_until = expiry_ts + config.cooldown_after_loss_s
        open_until = expiry_ts
        trade = PortfolioTrade(
            opportunity_id=opportunity.opportunity_id,
            recipe_key=opportunity.recipe_key,
            asset=opportunity.asset,
            timeframe_s=opportunity.timeframe_s,
            signal_ts=opportunity.signal_ts,
            execution_ts=execution_ts,
            expiry_ts=expiry_ts,
            direction=opportunity.direction,
            stake=stake,
            payout_return_ratio=opportunity.payout_return_ratio,
            won=opportunity.won,
            pnl=pnl,
            balance_after=balance,
        )
        trades.append(trade)
        decisions.append(
            PortfolioDecision(
                opportunity_id=opportunity.opportunity_id,
                recipe_key=opportunity.recipe_key,
                asset=opportunity.asset,
                timeframe_s=opportunity.timeframe_s,
                signal_ts=opportunity.signal_ts,
                direction=opportunity.direction,
                reason=PortfolioDecisionReason.ELIGIBLE,
                execution_ts=execution_ts,
                expiry_ts=expiry_ts,
                pnl=pnl,
                balance_after=balance,
            )
        )

    period_start, period_end = _period_bounds(ordered, config)
    period_seconds = max(period_end - period_start, 1)
    duration_days = Decimal(period_seconds) / ONE_DAY_SECONDS
    rejections = Counter(decision.reason for decision in decisions if not decision.executed)
    contribution = _contribution_by_recipe(decisions, trades)
    if compute_marginal:
        contribution = _with_marginal_contribution(ordered, config, len(trades), contribution)

    result_without_hash = _result_from_parts(
        config=config,
        decisions=tuple(decisions),
        trades=tuple(trades),
        period_start_ts=period_start,
        period_end_ts=period_end,
        signals_total=len(opportunities),
        rejections_by_reason=dict(rejections),
        raw_signals_per_day=Decimal(len(opportunities)) / duration_days,
        executable_operations_per_day=Decimal(len(trades)) / duration_days,
        executed_by_utc_hour=_executed_by_hour(trades),
        idle_seconds=_idle_seconds(trades, period_start, period_end),
        overlap_events=sum(
            rejections.get(reason, 0)
            for reason in (
                PortfolioDecisionReason.CONFLICT,
                PortfolioDecisionReason.ORDER_IN_FLIGHT,
            )
        ),
        concentration_by_recipe=_concentration([trade.recipe_key for trade in trades]),
        concentration_by_asset=_concentration([trade.asset for trade in trades]),
        contribution_by_recipe=contribution,
        violations=_one_order_violations(trades),
        reproducibility_hash="",
    )
    report_payload = result_without_hash.to_dict()
    report_payload.pop("reproducibility_hash", None)
    return replace(
        result_without_hash,
        reproducibility_hash=_stable_sha256(report_payload),
    )


def compare_portfolio_sizes(
    opportunities: Sequence[PortfolioOpportunity],
    config: PortfolioReplayConfig,
    *,
    sizes: Sequence[int] = (10, 20, 30, 50),
) -> dict[int, PortfolioReplayResult]:
    """Replay deterministic portfolio prefixes for CAT-11 10/20/30/50 comparison."""
    recipe_keys = sorted({opportunity.recipe_key for opportunity in opportunities})
    results: dict[int, PortfolioReplayResult] = {}
    for size in sizes:
        selected = set(recipe_keys[:size])
        subset = [
            opportunity for opportunity in opportunities if opportunity.recipe_key in selected
        ]
        results[size] = run_portfolio_replay(subset, config)
    return results


def opportunities_from_replay_contract(
    contract_path: Path,
    *,
    default_stake: Decimal = DEFAULT_STAKE,
) -> tuple[PortfolioOpportunity, ...]:
    """Load CAT-04 public replay vectors into portfolio opportunities."""
    raw = json.loads(contract_path.read_text(encoding="utf-8"))
    opportunities: list[PortfolioOpportunity] = []
    for case in cast(list[dict[str, Any]], raw["cases"]):
        recipe_key = (
            f"{case['family'].lower()}:{case['asset']}:{case['tf']}:{case['candidate_hash'][:8]}"
        )
        tf_seconds = int(case["tf_seconds"])
        for trade in cast(list[dict[str, Any]], case["expected_trades"]):
            opportunities.append(
                PortfolioOpportunity(
                    recipe_key=recipe_key,
                    asset=str(trade["asset"]),
                    timeframe_s=tf_seconds,
                    signal_ts=int(trade["ts"]),
                    direction=cast(Direction, trade["direction"]),
                    payout_return_ratio=Decimal(str(trade["payout_return_ratio"])),
                    won=bool(trade["won"]),
                    duration_s=tf_seconds,
                    stake=default_stake,
                    source_case_id=str(case["id"]),
                )
            )
    return tuple(sorted(opportunities, key=_opportunity_sort_key))


def portfolio_result_to_markdown(result: PortfolioReplayResult) -> str:
    """Human-readable, reproducible CAT-11 report."""
    lines = [
        "# Portfolio Executable Replay",
        "",
        f"Snapshot: `{result.config.snapshot_id}` · Seed: `{result.config.seed}`",
        f"Hash reproduzível: `{result.reproducibility_hash}`",
        "",
        f"- Sinais brutos: {result.signals_total}",
        f"- Operações executáveis: {result.executed_total}",
        f"- Sinais/dia: {_decimal_text(result.raw_signals_per_day)}",
        f"- Operações executáveis/dia: {_decimal_text(result.executable_operations_per_day)}",
        f"- Tempo ocioso: {result.idle_seconds}s",
        f"- Sobreposição/conflito: {result.overlap_events}",
        f"- P&L simulado: {_decimal_text(result.net_pnl)}",
        "",
        "## Bloqueios",
        "",
    ]
    if result.rejections_by_reason:
        for reason, count in sorted(result.rejections_by_reason.items()):
            lines.append(f"- `{reason.value}`: {count}")
    else:
        lines.append("- Nenhum")
    lines.extend(["", "## Distribuição por hora UTC", ""])
    if result.executed_by_utc_hour:
        for hour, count in sorted(result.executed_by_utc_hour.items()):
            lines.append(f"- {hour:02d}:00: {count}")
    else:
        lines.append("- Nenhuma operação executável")
    return "\n".join(lines)


def _arbitrate_opportunities(
    opportunities: Sequence[PortfolioOpportunity],
    decisions: list[PortfolioDecision],
) -> tuple[PortfolioOpportunity, ...]:
    selected: list[PortfolioOpportunity] = []
    for _, signal_group in _group_by_signal_ts(opportunities):
        after_pregate: list[PortfolioOpportunity] = []
        for opportunity in signal_group:
            if opportunity.direction not in {"call", "put"}:
                decisions.append(
                    _blocked_decision(
                        opportunity,
                        PortfolioDecisionReason.INVALID_DIRECTION,
                        opportunity.signal_ts,
                        opportunity.signal_ts + opportunity.duration_s,
                    )
                )
                continue
            if opportunity.pre_gate_reason is not None:
                decisions.append(
                    _blocked_decision(
                        opportunity,
                        opportunity.pre_gate_reason,
                        opportunity.signal_ts,
                        opportunity.signal_ts + opportunity.duration_s,
                    )
                )
                continue
            after_pregate.append(opportunity)

        for context_group in _group_by_context(after_pregate):
            directions = {opportunity.direction for opportunity in context_group}
            if len(directions) > 1:
                for opportunity in context_group:
                    decisions.append(
                        _blocked_decision(
                            opportunity,
                            PortfolioDecisionReason.CONFLICT,
                            opportunity.signal_ts,
                            opportunity.signal_ts + opportunity.duration_s,
                            detail="opposite_directions_same_asset_timeframe",
                        )
                    )
                continue
            winner, *duplicates = sorted(context_group, key=_arbitration_sort_key)
            selected.append(winner)
            for duplicate in duplicates:
                decisions.append(
                    _blocked_decision(
                        duplicate,
                        PortfolioDecisionReason.CONFLICT,
                        duplicate.signal_ts,
                        duplicate.signal_ts + duplicate.duration_s,
                        detail="duplicate_same_asset_timeframe_direction",
                    )
                )
    return tuple(sorted(selected, key=_opportunity_sort_key))


def _execution_block_reason(
    opportunity: PortfolioOpportunity,
    config: PortfolioReplayConfig,
    *,
    execution_ts: int,
    expiry_ts: int,
    open_until: int | None,
    cooldown_until: int | None,
    net_pnl: Decimal,
    consecutive_losses: int,
) -> PortfolioDecisionReason | None:
    if execution_ts > opportunity.signal_ts + config.signal_ttl_s or execution_ts >= expiry_ts:
        return PortfolioDecisionReason.DEADLINE_EXPIRED
    if open_until is not None and execution_ts < open_until:
        return PortfolioDecisionReason.ORDER_IN_FLIGHT
    if cooldown_until is not None and execution_ts < cooldown_until:
        return PortfolioDecisionReason.RISK
    if _risk_limit_reached(config, net_pnl, consecutive_losses):
        return PortfolioDecisionReason.RISK
    if opportunity.payout_return_ratio is None:
        return PortfolioDecisionReason.MISSING_PAYOUT
    if opportunity.won is None:
        return PortfolioDecisionReason.EXECUTION
    if opportunity.execution_failure_reason is not None:
        return opportunity.execution_failure_reason
    return None


def _risk_limit_reached(
    config: PortfolioReplayConfig,
    net_pnl: Decimal,
    consecutive_losses: int,
) -> bool:
    if config.stop_loss_abs is not None and net_pnl <= -config.stop_loss_abs:
        return True
    if config.take_profit_abs is not None and net_pnl >= config.take_profit_abs:
        return True
    return (
        config.max_consecutive_losses is not None
        and consecutive_losses >= config.max_consecutive_losses
    )


def _blocked_decision(
    opportunity: PortfolioOpportunity,
    reason: PortfolioDecisionReason,
    execution_ts: int | None,
    expiry_ts: int | None,
    *,
    detail: str = "",
) -> PortfolioDecision:
    return PortfolioDecision(
        opportunity_id=opportunity.opportunity_id,
        recipe_key=opportunity.recipe_key,
        asset=opportunity.asset,
        timeframe_s=opportunity.timeframe_s,
        signal_ts=opportunity.signal_ts,
        direction=opportunity.direction,
        reason=reason,
        execution_ts=execution_ts,
        expiry_ts=expiry_ts,
        detail=detail,
    )


def _group_by_signal_ts(
    opportunities: Sequence[PortfolioOpportunity],
) -> Iterable[tuple[int, tuple[PortfolioOpportunity, ...]]]:
    by_ts: dict[int, list[PortfolioOpportunity]] = defaultdict(list)
    for opportunity in opportunities:
        by_ts[opportunity.signal_ts].append(opportunity)
    for ts in sorted(by_ts):
        yield ts, tuple(sorted(by_ts[ts], key=_opportunity_sort_key))


def _group_by_context(
    opportunities: Sequence[PortfolioOpportunity],
) -> Iterable[tuple[PortfolioOpportunity, ...]]:
    by_context: dict[tuple[str, int, int], list[PortfolioOpportunity]] = defaultdict(list)
    for opportunity in opportunities:
        by_context[(opportunity.asset, opportunity.timeframe_s, opportunity.signal_ts)].append(
            opportunity
        )
    for key in sorted(by_context):
        yield tuple(sorted(by_context[key], key=_arbitration_sort_key))


def _opportunity_sort_key(opportunity: PortfolioOpportunity) -> tuple[int, int, str, int, str, str]:
    return (
        opportunity.signal_ts,
        -opportunity.priority,
        opportunity.asset,
        opportunity.timeframe_s,
        opportunity.recipe_key,
        opportunity.direction,
    )


def _arbitration_sort_key(opportunity: PortfolioOpportunity) -> tuple[int, str, str]:
    return (-opportunity.priority, opportunity.recipe_key, opportunity.opportunity_id)


def _period_bounds(
    opportunities: Sequence[PortfolioOpportunity],
    config: PortfolioReplayConfig,
) -> tuple[int, int]:
    if config.period_start_ts is not None and config.period_end_ts is not None:
        return config.period_start_ts, config.period_end_ts
    if not opportunities:
        start = config.period_start_ts or 1
        end = config.period_end_ts or start + 1
        return start, end
    start = config.period_start_ts or min(opportunity.signal_ts for opportunity in opportunities)
    end = config.period_end_ts or max(
        opportunity.signal_ts + opportunity.duration_s for opportunity in opportunities
    )
    return start, max(end, start + 1)


def _executed_by_hour(trades: Sequence[PortfolioTrade]) -> dict[int, int]:
    by_hour = Counter((trade.execution_ts % 86_400) // 3_600 for trade in trades)
    return dict(sorted(by_hour.items()))


def _idle_seconds(trades: Sequence[PortfolioTrade], period_start: int, period_end: int) -> int:
    if period_end <= period_start:
        return 0
    busy = 0
    last_busy_end: int | None = None
    for trade in sorted(trades, key=lambda item: (item.execution_ts, item.expiry_ts)):
        start = max(period_start, trade.execution_ts)
        end = min(period_end, trade.expiry_ts)
        if end <= start:
            continue
        if last_busy_end is not None and start < last_busy_end:
            start = last_busy_end
        if end > start:
            busy += end - start
            last_busy_end = end
    return max(period_end - period_start - busy, 0)


def _one_order_violations(trades: Sequence[PortfolioTrade]) -> tuple[str, ...]:
    violations: list[str] = []
    ordered = sorted(trades, key=lambda trade: trade.execution_ts)
    for prev, current in zip(ordered, ordered[1:], strict=False):
        if current.execution_ts < prev.expiry_ts:
            violations.append(f"{current.opportunity_id}:overlaps:{prev.opportunity_id}")
    return tuple(violations)


def _concentration(values: Sequence[str]) -> dict[str, Decimal]:
    if not values:
        return {}
    counts = Counter(values)
    total = Decimal(len(values))
    return {key: Decimal(count) / total for key, count in sorted(counts.items())}


def _contribution_by_recipe(
    decisions: Sequence[PortfolioDecision],
    trades: Sequence[PortfolioTrade],
) -> dict[str, RecipeContribution]:
    recipes = sorted({decision.recipe_key for decision in decisions})
    trade_pnl: defaultdict[str, Decimal] = defaultdict(lambda: MONEY_ZERO)
    trade_counts = Counter[str]()
    for trade in trades:
        trade_counts[trade.recipe_key] += 1
        trade_pnl[trade.recipe_key] += trade.pnl
    by_recipe: dict[str, RecipeContribution] = {}
    for recipe in recipes:
        blocked = Counter(
            decision.reason
            for decision in decisions
            if decision.recipe_key == recipe and not decision.executed
        )
        by_recipe[recipe] = RecipeContribution(
            recipe_key=recipe,
            signals=sum(1 for decision in decisions if decision.recipe_key == recipe),
            executed=trade_counts[recipe],
            blocked=dict(blocked),
            pnl=trade_pnl[recipe],
        )
    return by_recipe


def _with_marginal_contribution(
    opportunities: Sequence[PortfolioOpportunity],
    config: PortfolioReplayConfig,
    base_executed: int,
    contribution: Mapping[str, RecipeContribution],
) -> dict[str, RecipeContribution]:
    out: dict[str, RecipeContribution] = {}
    for recipe, current in contribution.items():
        without = [opportunity for opportunity in opportunities if opportunity.recipe_key != recipe]
        replay_without = run_portfolio_replay(without, config, compute_marginal=False)
        out[recipe] = RecipeContribution(
            recipe_key=current.recipe_key,
            signals=current.signals,
            executed=current.executed,
            blocked=current.blocked,
            pnl=current.pnl,
            marginal_executable_delta=base_executed - replay_without.executed_total,
        )
    return out


def _result_from_parts(
    *,
    config: PortfolioReplayConfig,
    decisions: tuple[PortfolioDecision, ...],
    trades: tuple[PortfolioTrade, ...],
    period_start_ts: int,
    period_end_ts: int,
    signals_total: int,
    rejections_by_reason: Mapping[PortfolioDecisionReason, int],
    raw_signals_per_day: Decimal,
    executable_operations_per_day: Decimal,
    executed_by_utc_hour: Mapping[int, int],
    idle_seconds: int,
    overlap_events: int,
    concentration_by_recipe: Mapping[str, Decimal],
    concentration_by_asset: Mapping[str, Decimal],
    contribution_by_recipe: Mapping[str, RecipeContribution],
    violations: tuple[str, ...],
    reproducibility_hash: str,
) -> PortfolioReplayResult:
    return PortfolioReplayResult(
        config=config,
        decisions=decisions,
        trades=trades,
        period_start_ts=period_start_ts,
        period_end_ts=period_end_ts,
        signals_total=signals_total,
        rejections_by_reason=rejections_by_reason,
        raw_signals_per_day=raw_signals_per_day,
        executable_operations_per_day=executable_operations_per_day,
        executed_by_utc_hour=executed_by_utc_hour,
        idle_seconds=idle_seconds,
        overlap_events=overlap_events,
        concentration_by_recipe=concentration_by_recipe,
        concentration_by_asset=concentration_by_asset,
        contribution_by_recipe=contribution_by_recipe,
        violations=violations,
        reproducibility_hash=reproducibility_hash,
    )


def _decision_payload(decision: PortfolioDecision) -> dict[str, object]:
    return {
        "asset": decision.asset,
        "balance_after": _optional_decimal_text(decision.balance_after),
        "detail": decision.detail,
        "direction": decision.direction,
        "execution_ts": decision.execution_ts,
        "expiry_ts": decision.expiry_ts,
        "opportunity_id": decision.opportunity_id,
        "pnl": _decimal_text(decision.pnl),
        "reason": decision.reason.value,
        "recipe_key": decision.recipe_key,
        "signal_ts": decision.signal_ts,
        "timeframe_s": decision.timeframe_s,
    }


def _trade_payload(trade: PortfolioTrade) -> dict[str, object]:
    return {
        "asset": trade.asset,
        "balance_after": _decimal_text(trade.balance_after),
        "direction": trade.direction,
        "execution_ts": trade.execution_ts,
        "expiry_ts": trade.expiry_ts,
        "opportunity_id": trade.opportunity_id,
        "payout_return_ratio": _decimal_text(trade.payout_return_ratio),
        "pnl": _decimal_text(trade.pnl),
        "recipe_key": trade.recipe_key,
        "signal_ts": trade.signal_ts,
        "stake": _decimal_text(trade.stake),
        "timeframe_s": trade.timeframe_s,
        "won": trade.won,
    }


def _stable_sha256(payload: Mapping[str, object]) -> str:
    raw = json.dumps(
        _jsonable(payload),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _jsonable(value: object) -> object:
    if isinstance(value, Decimal):
        return _decimal_text(value)
    if isinstance(value, StrEnum):
        return value.value
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, tuple | list):
        return [_jsonable(item) for item in value]
    if hasattr(value, "to_dict"):
        return cast(Any, value).to_dict()
    return value


def _decimal_text(value: Decimal) -> str:
    return format(value.normalize(), "f")


def _optional_decimal_text(value: Decimal | None) -> str | None:
    return None if value is None else _decimal_text(value)
