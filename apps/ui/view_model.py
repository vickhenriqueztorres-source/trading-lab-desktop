from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from packages.protocol import UiProjectionSnapshot


@dataclass(frozen=True, slots=True)
class DashboardViewModel:
    global_state: str
    health_description: str
    health_lines: tuple[str, ...]
    broker_lines: tuple[str, ...]
    order_lines: tuple[str, ...]
    daily_pnl: str
    can_safe_stop: bool
    can_resume: bool
    global_exposure: str = "INDISPONÍVEL"
    risk_state: str = "NORMAL"
    consecutive_losses: int = 0

    @classmethod
    def from_snapshot(cls, snapshot: UiProjectionSnapshot) -> DashboardViewModel:
        brokers = tuple(
            (
                f"{card.broker} | {card.account_mode.value} | "
                f"{'CONECTADO' if card.is_connected else 'DESCONECTADO'} "
                f"({card.connection_label}) | "
                f"saldo {cls._money(card.balance_minor_units, card.currency)} | "
                f"relógio {'OK' if card.clock_synced else 'NÃO COMPROVADO'}"
                f"{'' if card.clock_latency_ms is None else f' ({card.clock_latency_ms} ms)'}"
            )
            for card in snapshot.broker_cards
        )
        orders = tuple(
            f"{item.order_id}"
            f"{f' ({item.broker_order_id})' if item.broker_order_id else ''} | "
            f"{item.broker} {item.symbol} {item.direction} | "
            f"{cls._money(item.amount_minor_units, item.currency)} | {item.state}"
            for item in snapshot.active_orders
        ) or ("Nenhuma ordem persistida.",)
        pnl = cls._money(snapshot.daily_pnl_minor_units, snapshot.daily_pnl_currency)
        health_lines = tuple(
            f"{gate.gate_name} | {gate.reason_code or 'OPEN'} | {gate.description}"
            for gate in snapshot.health_gates
        )
        exp_curr = snapshot.daily_pnl_currency or "USD"
        exp_active = cls._money(snapshot.global_exposure_minor_units, exp_curr)
        exp_max = cls._money(snapshot.global_max_exposure_minor_units, exp_curr)
        global_exposure = f"{exp_active} / {exp_max}"
        return cls(
            global_state=snapshot.global_state.value,
            health_description=health_lines[0],
            health_lines=health_lines,
            broker_lines=brokers,
            order_lines=orders,
            daily_pnl=pnl,
            can_safe_stop=not snapshot.safe_stop_active,
            can_resume=snapshot.safe_stop_active,
            global_exposure=global_exposure,
            risk_state=snapshot.risk_state,
            consecutive_losses=snapshot.consecutive_losses,
        )

    @staticmethod
    def _money(minor_units: int | None, currency: str | None) -> str:
        if minor_units is None or currency is None:
            return "INDISPONÍVEL"
        amount = Decimal(minor_units) / Decimal(100)
        return f"{currency.upper()} {amount:.2f}"
