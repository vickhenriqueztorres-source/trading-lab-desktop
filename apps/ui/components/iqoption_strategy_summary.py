"""Strategy summary and outcome KPI cards for IQ Option."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from apps.ui.formatting import format_minor_units
from apps.ui.i18n import I18nManager, t
from apps.ui.theme import ACCENT_CYAN, ACCENT_GREEN, ACCENT_RED, TEXT_MUTED
from packages.protocol import OrderSummary, UiIqOptionExecutionMetrics, UiIqOptionRiskConfig


@dataclass(frozen=True, slots=True)
class IqOptionMartingaleStats:
    """Consolidated Martingale cycle statistics for IQ Option."""

    total_cycles: int
    wins_sem_gale: int
    wins_g1: int
    wins_g2: int
    total_wins: int
    losses_g2: int
    losses_other: int
    total_losses: int
    win_rate: float
    net_pnl_minor: int
    gross_gains_minor: int
    gross_losses_minor: int


def calculate_martingale_cycle_stats(
    orders: Sequence[OrderSummary],
) -> IqOptionMartingaleStats:
    """Group settled IQ Option orders into Martingale cycles and calculate assertiveness."""

    settled = sorted(
        [
            o
            for o in orders
            if "IQ" in o.broker.upper()
            and o.state == "SETTLED"
            and o.realized_pnl_minor_units is not None
        ],
        key=lambda x: x.created_at_utc,
    )
    if not settled:
        return IqOptionMartingaleStats(
            total_cycles=0,
            wins_sem_gale=0,
            wins_g1=0,
            wins_g2=0,
            total_wins=0,
            losses_g2=0,
            losses_other=0,
            total_losses=0,
            win_rate=0.0,
            net_pnl_minor=0,
            gross_gains_minor=0,
            gross_losses_minor=0,
        )

    cycles: list[list[OrderSummary]] = []
    current_cycle: list[OrderSummary] = []

    for order in settled:
        if not current_cycle:
            current_cycle.append(order)
            continue

        prev = current_cycle[-1]
        time_diff = (order.created_at_utc - prev.created_at_utc).total_seconds()

        is_recovery = (
            (prev.realized_pnl_minor_units or 0) < 0
            and order.symbol == prev.symbol
            and 0 <= time_diff <= 180
            and len(current_cycle) < 3
        )

        if is_recovery:
            current_cycle.append(order)
        else:
            cycles.append(current_cycle)
            current_cycle = [order]

    if current_cycle:
        cycles.append(current_cycle)

    wins_sem_gale = 0
    wins_g1 = 0
    wins_g2 = 0
    losses_g2 = 0
    losses_other = 0

    gross_gains = 0
    gross_losses = 0

    for order in settled:
        pnl = order.realized_pnl_minor_units or 0
        if pnl > 0:
            gross_gains += pnl
        elif pnl < 0:
            gross_losses += abs(pnl)

    for cycle in cycles:
        last_order = cycle[-1]
        step = len(cycle) - 1
        pnl = last_order.realized_pnl_minor_units or 0

        if pnl > 0:
            if step == 0:
                wins_sem_gale += 1
            elif step == 1:
                wins_g1 += 1
            else:
                wins_g2 += 1
        else:
            if step == 2:
                losses_g2 += 1
            else:
                losses_other += 1

    total_wins = wins_sem_gale + wins_g1 + wins_g2
    effective_losses = (
        losses_g2 if (wins_g1 > 0 or wins_g2 > 0 or losses_g2 > 0) else (losses_g2 + losses_other)
    )
    total_effective_cycles = total_wins + losses_g2
    if total_effective_cycles == 0 and losses_other > 0:
        total_effective_cycles = total_wins + losses_other
        effective_losses = losses_other

    win_rate = (total_wins / total_effective_cycles * 100.0) if total_effective_cycles > 0 else 0.0

    return IqOptionMartingaleStats(
        total_cycles=total_effective_cycles,
        wins_sem_gale=wins_sem_gale,
        wins_g1=wins_g1,
        wins_g2=wins_g2,
        total_wins=total_wins,
        losses_g2=losses_g2,
        losses_other=losses_other,
        total_losses=effective_losses,
        win_rate=win_rate,
        net_pnl_minor=gross_gains - gross_losses,
        gross_gains_minor=gross_gains,
        gross_losses_minor=gross_losses,
    )


class IqOptionStrategySummaryWidget(QWidget):
    """Visual KPI and strategy summary cards for IQ Option."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._orders: tuple[OrderSummary, ...] = ()
        self._risk_config: UiIqOptionRiskConfig | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(10)

        # 4 Outcome KPI Cards
        outcomes = QHBoxLayout()
        outcomes.setSpacing(8)
        self._net_title, self._net_val, self._net_sub = self._create_kpi_card(
            outcomes, t("iq.kpi.net_profit"), "$0.00", ACCENT_CYAN
        )
        self._gain_title, self._gain_val, self._gain_sub = self._create_kpi_card(
            outcomes, t("iq.kpi.total_wins"), "0", ACCENT_GREEN
        )
        self._loss_title, self._loss_val, self._loss_sub = self._create_kpi_card(
            outcomes, t("iq.kpi.total_losses"), "0", ACCENT_RED
        )
        self._win_title, self._win_val, self._win_sub = self._create_kpi_card(
            outcomes, t("iq.kpi.win_rate"), "—", ACCENT_CYAN
        )
        root.addLayout(outcomes)

        # Strategy Info Banner
        banner = QFrame()
        banner.setObjectName("Surface")
        banner_layout = QHBoxLayout(banner)
        banner_layout.setContentsMargins(14, 10, 14, 10)
        banner_layout.setSpacing(12)

        info_col = QVBoxLayout()
        self._info_title = QLabel(t("iq.strategy.title"))
        self._info_title.setObjectName("Title")
        self._info_title.setStyleSheet("font-size: 15px; font-weight: 700; color: #F8FAFC;")
        info_col.addWidget(self._info_title)

        self._strategy_desc = QLabel(t("iq.strategy.desc"))
        self._strategy_desc.setObjectName("Subtitle")
        self._strategy_desc.setWordWrap(True)
        self._strategy_desc.setStyleSheet("font-size: 11px; color: #94A3B8;")
        info_col.addWidget(self._strategy_desc)
        banner_layout.addLayout(info_col, 1)

        self._mode_pill = QLabel(t("iq.strategy.auto_select"))
        self._mode_pill.setObjectName("StatusPillOnline")
        banner_layout.addWidget(self._mode_pill)

        root.addWidget(banner)

        self._evidence = QLabel(t("iq.strategy.evidence_wait"))
        self._evidence.setObjectName("GuidanceText")
        self._evidence.setWordWrap(True)
        root.addWidget(self._evidence)

    @staticmethod
    def _create_kpi_card(
        layout: QHBoxLayout,
        title: str,
        initial_value: str,
        color: str,
        initial_sub: str = "—",
    ) -> tuple[QLabel, QLabel, QLabel]:
        card = QFrame()
        card.setObjectName("Surface")
        c_layout = QVBoxLayout(card)
        c_layout.setContentsMargins(12, 8, 12, 8)
        c_layout.setSpacing(2)

        t_lbl = QLabel(title)
        t_lbl.setObjectName("Subtitle")
        t_lbl.setStyleSheet(f"color: {TEXT_MUTED}; font-size: 10px; font-weight: bold;")
        c_layout.addWidget(t_lbl)

        v_lbl = QLabel(initial_value)
        v_lbl.setObjectName("ValueMono")
        v_lbl.setStyleSheet(f"color: {color}; font-size: 18px; font-weight: 800;")
        c_layout.addWidget(v_lbl)

        s_lbl = QLabel(initial_sub)
        s_lbl.setObjectName("Subtitle")
        s_lbl.setStyleSheet(f"color: {TEXT_MUTED}; font-size: 10px; font-weight: 600;")
        c_layout.addWidget(s_lbl)

        layout.addWidget(card, 1)
        return t_lbl, v_lbl, s_lbl

    def update_orders(self, orders: Sequence[OrderSummary]) -> None:
        self._orders = tuple(orders)
        stats = calculate_martingale_cycle_stats(self._orders)

        iq_orders = [o for o in self._orders if "IQ" in o.broker.upper()]
        settled = [
            order
            for order in iq_orders
            if order.state == "SETTLED" and order.realized_pnl_minor_units is not None
        ]
        if not settled:
            self._net_val.setText("$0.00")
            self._net_sub.setText("—")
            self._gain_val.setText("0")
            self._gain_sub.setText("Sem Gale: 0 · G1: 0 · G2: 0")
            self._loss_val.setText("0")
            self._loss_sub.setText("Loss Gale 2: 0")
            self._win_val.setText("—")
            self._win_sub.setText("—")
            return

        curr = settled[0].currency or "USD"
        self._net_val.setText(format_minor_units(stats.net_pnl_minor, curr, positive_sign=True))
        color_val = ACCENT_GREEN if stats.net_pnl_minor >= 0 else ACCENT_RED
        self._net_val.setStyleSheet(f"color: {color_val}; font-size: 18px; font-weight: bold;")
        pos_str = format_minor_units(stats.gross_gains_minor, curr)
        neg_str = format_minor_units(stats.gross_losses_minor, curr)
        self._net_sub.setText(f"+{pos_str} / -{neg_str}")

        self._gain_val.setText(str(stats.total_wins))
        self._gain_sub.setText(
            t("iq.kpi.wins_detail", g0=stats.wins_sem_gale, g1=stats.wins_g1, g2=stats.wins_g2)
        )
        self._gain_val.setToolTip(f"Ganhos brutos: {pos_str}")

        self._loss_val.setText(str(stats.total_losses))
        self._loss_sub.setText(t("iq.kpi.loss_detail", loss=stats.losses_g2))
        self._loss_val.setToolTip(f"Perdas brutas: {neg_str}")

        self._win_val.setText(f"{stats.win_rate:.1f}% ({stats.total_wins}/{stats.total_cycles})")
        win_color = ACCENT_GREEN if stats.win_rate >= 50.0 else ACCENT_RED
        self._win_val.setStyleSheet(f"color: {win_color}; font-size: 18px; font-weight: 800;")
        self._win_sub.setText(
            t(
                "iq.kpi.win_rate_detail",
                g0=stats.wins_sem_gale,
                g1=stats.wins_g1,
                g2=stats.wins_g2,
                loss=stats.losses_g2,
            )
        )

    def update_config(self, config: UiIqOptionRiskConfig | None) -> None:
        self._risk_config = config
        if config is None:
            return

        strat_id = config.strategy_id
        if strat_id == "iqoption-hack-chino":
            title = "🤖 Bot Selecionado: Hack Chino (4 Cenários + 5 Modelos Quant)"
            desc = (
                "4 Gatilhos (Rejeição, Rompimento, Bollinger+RSI7, Pullback EMA) · "
                "Comitê Quant 24/7 (Bayesiano, Markov, Logística, k-NN, Regimes) · P ≥ 56.5%"
            )
            pill = "🤖 Hack Chino (4 Cenários + 5 Modelos)"
        elif strat_id == "iqoption-liquidity-gap":
            title = "🌊 Bot Selecionado: HFT Liquidity Gap"
            desc = (
                "Varredura Adaptativa · Absorção Institucional (Pavio ≥ 25%) · "
                "Timeframe M1 · Expiração 2 min"
            )
            pill = "🌊 Liquidity Gap (2m)"
        elif strat_id == "iqoption-pattern-reversal":
            title = "🔄 Bot Selecionado: HFT Pattern Reversal"
            desc = (
                "Engolfo de 2 Candles · Filtro de Pavio Oposto ≤ 25% · "
                "Timeframe M1 · Expiração 1 min"
            )
            pill = "🔄 Pattern Reversal (1m)"
        elif strat_id == "iqoption-extreme-rejection":
            title = "🎯 Bot Selecionado: Varredura e Rejeição de Extremo"
            desc = (
                "Varredura das últimas 8 velas (0.10 × A20) · Retorno e Pavio ≥ 35% · "
                "Filtro |EMA10 - EMA30| ≤ 0.50 × A20 · Expiração 1 min"
            )
            pill = "🎯 Rejeição de Extremo (1m)"
        elif strat_id == "iqoption-microtrend-scalper":
            title = "⚡ Bot Selecionado: Microtrend Scalper (3 Velas)"
            desc = (
                "3 Velas Consecutivas (Corpo ≥ 0.45) · RSI(5) Calibrado · "
                "Teto 2.5 × A20 · Alinhamento EMA10/EMA30 · Expiração 1 min"
            )
            pill = "⚡ Microtrend Scalper (1m)"
        elif strat_id == "AUTO" or config.symbol == "AUTO":
            title = "🌐 Bot Selecionado: Radar Multi-Ativos (AUTO)"
            desc = (
                "Varredura algorítmica contínua de pares abertos · "
                "Disparo no 1º sinal técnico · Expiração 1 min"
            )
            pill = "⚡ RADAR MULTI-ATIVOS"
        else:
            title = f"🤖 Bot Selecionado: {strat_id}"
            desc = (
                f"Ativo: {config.symbol} · Timeframe: {config.timeframe_seconds}s · "
                f"Expiração: {config.duration_seconds}s"
            )
            pill = f"🎯 {config.symbol}"

        self._info_title.setText(title)
        self._strategy_desc.setText(desc)
        self._mode_pill.setText(pill)
        self._mode_pill.setObjectName("StatusPillOnline")
        self._mode_pill.style().unpolish(self._mode_pill)
        self._mode_pill.style().polish(self._mode_pill)

    def update_metrics(self, metrics: UiIqOptionExecutionMetrics | None) -> None:
        if metrics is None:
            self._evidence.setText(t("iq.strategy.evidence_unavailable"))
            return
        lang = I18nManager.get_language()
        is_es = lang == "es"
        source_label = "Fuente" if is_es else "Source"
        mode_label = "Modo" if is_es else "Mode"
        cat_label = "Catálogo" if is_es else "Catalog"
        rev_label = "Revisión" if is_es else "Revision"
        series_label = "Series"
        nodes_label = "Nodos" if is_es else "Nodes"
        reuse_label = "Reúso" if is_es else "Reuse"
        p95_label = "Decisión p95" if is_es else "Decision p95"
        evid_label = "Evidencia n/OOS" if is_es else "Evidence n/OOS"
        wait_prefix = "esperando" if is_es else "waiting"

        wait = (
            f" · {wait_prefix} {metrics.waiting_reason} ({metrics.waiting_seconds}s)"
            if metrics.waiting_reason
            else ""
        )
        revision = metrics.manifest_revision or "n/a"
        ev_n = metrics.evidence_n if metrics.evidence_n is not None else "—"
        ev_oos = metrics.evidence_oos if metrics.evidence_oos is not None else "—"
        parts = [
            f"{source_label}: {metrics.source}",
            f"{mode_label}: {metrics.mode}",
            f"{cat_label}: {metrics.catalog_status}",
            f"{rev_label}: {revision[:16]}",
            f"{series_label}: {metrics.series_count}",
            f"{nodes_label}: {metrics.unique_indicator_nodes}",
            f"{reuse_label}: {metrics.cache_reuse_hits}",
            f"{p95_label}: {metrics.decision_p95_ms} ms",
            f"{evid_label}: {ev_n}/{ev_oos}{wait}",
        ]
        self._evidence.setText(" · ".join(parts))


__all__ = [
    "IqOptionStrategySummaryWidget",
    "IqOptionMartingaleStats",
    "calculate_martingale_cycle_stats",
]
