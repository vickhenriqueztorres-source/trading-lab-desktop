from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Self


@dataclass(frozen=True, slots=True)
class TradeOutcomeRecord:
    trade_id: str
    entry_epoch_ms: int
    exit_epoch_ms: int
    stake_minor_units: int
    payout_minor_units: int
    pnl_minor_units: int
    is_win: bool
    symbol: str = ""
    regime: str = "UNKNOWN"
    duration_seconds: int = 0

    def __post_init__(self) -> None:
        if not self.trade_id.strip():
            raise ValueError("trade_id cannot be empty")
        if self.exit_epoch_ms < self.entry_epoch_ms:
            raise ValueError("exit_epoch_ms cannot be before entry_epoch_ms")
        if self.stake_minor_units < 0 or self.payout_minor_units < 0:
            raise ValueError("stake and payout must be non-negative")
        if self.pnl_minor_units != self.payout_minor_units - self.stake_minor_units:
            raise ValueError("pnl_minor_units must equal payout_minor_units - stake_minor_units")
        if self.is_win != (self.pnl_minor_units > 0):
            raise ValueError("is_win must match (pnl_minor_units > 0)")
        if self.duration_seconds < 0:
            raise ValueError("duration_seconds must be non-negative")

    def to_payload(self) -> dict[str, Any]:
        return {
            "duration_seconds": self.duration_seconds,
            "entry_epoch_ms": self.entry_epoch_ms,
            "exit_epoch_ms": self.exit_epoch_ms,
            "is_win": self.is_win,
            "payout_minor_units": self.payout_minor_units,
            "pnl_minor_units": self.pnl_minor_units,
            "regime": self.regime,
            "stake_minor_units": self.stake_minor_units,
            "symbol": self.symbol,
            "trade_id": self.trade_id,
        }

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> Self:
        return cls(
            duration_seconds=int(payload.get("duration_seconds", 0)),
            entry_epoch_ms=int(payload["entry_epoch_ms"]),
            exit_epoch_ms=int(payload["exit_epoch_ms"]),
            is_win=bool(payload["is_win"]),
            payout_minor_units=int(payload["payout_minor_units"]),
            pnl_minor_units=int(payload["pnl_minor_units"]),
            regime=str(payload.get("regime", "UNKNOWN")),
            stake_minor_units=int(payload["stake_minor_units"]),
            symbol=str(payload.get("symbol", "")),
            trade_id=str(payload["trade_id"]),
        )


@dataclass(frozen=True, slots=True)
class StrategyPerformanceMetrics:
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate_decimal: Decimal
    gross_profit_minor_units: int
    gross_loss_minor_units: int
    net_profit_minor_units: int
    profit_factor_decimal: Decimal | None
    max_drawdown_minor_units: int
    max_drawdown_pct_decimal: Decimal
    expectancy_minor_units_decimal: Decimal
    average_duration_seconds: Decimal = Decimal("0")
    regime_distribution: Mapping[str, int] = field(default_factory=dict)

    def to_payload(self) -> dict[str, Any]:
        return {
            "average_duration_seconds": str(self.average_duration_seconds),
            "expectancy_minor_units_decimal": str(self.expectancy_minor_units_decimal),
            "gross_loss_minor_units": self.gross_loss_minor_units,
            "gross_profit_minor_units": self.gross_profit_minor_units,
            "losing_trades": self.losing_trades,
            "max_drawdown_minor_units": self.max_drawdown_minor_units,
            "max_drawdown_pct_decimal": str(self.max_drawdown_pct_decimal),
            "net_profit_minor_units": self.net_profit_minor_units,
            "profit_factor_decimal": (
                str(self.profit_factor_decimal) if self.profit_factor_decimal is not None else None
            ),
            "regime_distribution": dict(self.regime_distribution),
            "total_trades": self.total_trades,
            "win_rate_decimal": str(self.win_rate_decimal),
            "winning_trades": self.winning_trades,
        }

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> Self:
        pf = payload.get("profit_factor_decimal")
        return cls(
            average_duration_seconds=Decimal(str(payload.get("average_duration_seconds", "0"))),
            expectancy_minor_units_decimal=Decimal(str(payload["expectancy_minor_units_decimal"])),
            gross_loss_minor_units=int(payload["gross_loss_minor_units"]),
            gross_profit_minor_units=int(payload["gross_profit_minor_units"]),
            losing_trades=int(payload["losing_trades"]),
            max_drawdown_minor_units=int(payload["max_drawdown_minor_units"]),
            max_drawdown_pct_decimal=Decimal(str(payload["max_drawdown_pct_decimal"])),
            net_profit_minor_units=int(payload["net_profit_minor_units"]),
            profit_factor_decimal=Decimal(str(pf)) if pf is not None else None,
            regime_distribution=dict(payload.get("regime_distribution", {})),
            total_trades=int(payload["total_trades"]),
            win_rate_decimal=Decimal(str(payload["win_rate_decimal"])),
            winning_trades=int(payload["winning_trades"]),
        )


def calculate_performance_metrics(
    trades: Sequence[TradeOutcomeRecord],
    initial_capital_minor_units: int,
) -> StrategyPerformanceMetrics:
    if initial_capital_minor_units <= 0:
        raise ValueError("initial_capital_minor_units must be positive")

    total_trades = len(trades)
    if total_trades == 0:
        return StrategyPerformanceMetrics(
            total_trades=0,
            winning_trades=0,
            losing_trades=0,
            win_rate_decimal=Decimal("0.0"),
            gross_profit_minor_units=0,
            gross_loss_minor_units=0,
            net_profit_minor_units=0,
            profit_factor_decimal=None,
            max_drawdown_minor_units=0,
            max_drawdown_pct_decimal=Decimal("0.0"),
            expectancy_minor_units_decimal=Decimal("0.0"),
            average_duration_seconds=Decimal("0.0"),
            regime_distribution={},
        )

    winning_trades = 0
    losing_trades = 0
    gross_profit = 0
    gross_loss = 0
    net_profit = 0
    total_duration = 0
    regimes: dict[str, int] = {}

    current_equity = initial_capital_minor_units
    hwm = initial_capital_minor_units
    max_dd = 0
    max_dd_pct = Decimal("0.0")

    for trade in trades:
        pnl = trade.pnl_minor_units
        net_profit += pnl
        current_equity += pnl

        if trade.is_win:
            winning_trades += 1
            gross_profit += pnl
        else:
            losing_trades += 1
            gross_loss += abs(pnl)

        total_duration += trade.duration_seconds
        regime = trade.regime or "UNKNOWN"
        regimes[regime] = regimes.get(regime, 0) + 1

        if current_equity > hwm:
            hwm = current_equity

        drawdown = hwm - current_equity
        if drawdown > max_dd:
            max_dd = drawdown
            if hwm > 0:
                max_dd_pct = Decimal(drawdown) / Decimal(hwm)

    win_rate = Decimal(winning_trades) / Decimal(total_trades)
    expectancy = Decimal(net_profit) / Decimal(total_trades)
    profit_factor = Decimal(gross_profit) / Decimal(gross_loss) if gross_loss > 0 else None
    avg_duration = Decimal(total_duration) / Decimal(total_trades)

    return StrategyPerformanceMetrics(
        total_trades=total_trades,
        winning_trades=winning_trades,
        losing_trades=losing_trades,
        win_rate_decimal=win_rate,
        gross_profit_minor_units=gross_profit,
        gross_loss_minor_units=gross_loss,
        net_profit_minor_units=net_profit,
        profit_factor_decimal=profit_factor,
        max_drawdown_minor_units=max_dd,
        max_drawdown_pct_decimal=max_dd_pct,
        expectancy_minor_units_decimal=expectancy,
        average_duration_seconds=avg_duration,
        regime_distribution=regimes,
    )
