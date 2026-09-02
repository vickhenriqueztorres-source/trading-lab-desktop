"""Strategy summary and outcome KPI cards for IQ Option."""

from __future__ import annotations

from collections.abc import Sequence

from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from apps.ui.formatting import format_minor_units
from apps.ui.theme import ACCENT_CYAN, ACCENT_GREEN, ACCENT_RED, TEXT_MUTED
from packages.protocol import OrderSummary, UiIqOptionRiskConfig


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
        self._net_title, self._net_val = self._create_kpi_card(
            outcomes, "RESULTADO LÍQUIDO", "$0.00", ACCENT_CYAN
        )
        self._gain_title, self._gain_val = self._create_kpi_card(
            outcomes, "TOTAL GANHOS", "$0.00", ACCENT_GREEN
        )
        self._loss_title, self._loss_val = self._create_kpi_card(
            outcomes, "TOTAL PERDAS", "$0.00", ACCENT_RED
        )
        self._win_title, self._win_val = self._create_kpi_card(
            outcomes, "ASSERTIVIDADE", "—", ACCENT_CYAN
        )
        root.addLayout(outcomes)

        # Strategy Info Banner
        banner = QFrame()
        banner.setObjectName("Surface")
        banner_layout = QHBoxLayout(banner)
        banner_layout.setContentsMargins(14, 10, 14, 10)
        banner_layout.setSpacing(12)

        info_col = QVBoxLayout()
        info_title = QLabel("ESTRATÉGIA IQ OPTION · RSI 14 BOUNDED EDGE")
        info_title.setObjectName("Title")
        info_col.addWidget(info_title)

        self._strategy_desc = QLabel(
            "Timeframe: 1M  ·  Regra: CALL (RSI < 30 Sobrevenda) | PUT (RSI > 70 Sobrecompra)"
            "  ·  Execução: Instantânea"
        )
        self._strategy_desc.setObjectName("Subtitle")
        self._strategy_desc.setWordWrap(True)
        info_col.addWidget(self._strategy_desc)
        banner_layout.addLayout(info_col, 1)

        self._mode_pill = QLabel("SELEÇÃO AUTOMÁTICA")
        self._mode_pill.setObjectName("StatusPillOnline")
        banner_layout.addWidget(self._mode_pill)

        root.addWidget(banner)

    @staticmethod
    def _create_kpi_card(
        layout: QHBoxLayout, title: str, initial_value: str, color: str
    ) -> tuple[QLabel, QLabel]:
        card = QFrame()
        card.setObjectName("Surface")
        c_layout = QVBoxLayout(card)
        c_layout.setContentsMargins(12, 10, 12, 10)
        c_layout.setSpacing(4)

        t_lbl = QLabel(title)
        t_lbl.setObjectName("Subtitle")
        t_lbl.setStyleSheet(f"color: {TEXT_MUTED}; font-size: 10px; font-weight: bold;")
        c_layout.addWidget(t_lbl)

        v_lbl = QLabel(initial_value)
        v_lbl.setObjectName("ValueMono")
        v_lbl.setStyleSheet(f"color: {color}; font-size: 16px; font-weight: bold;")
        c_layout.addWidget(v_lbl)

        layout.addWidget(card, 1)
        return t_lbl, v_lbl

    def update_orders(self, orders: Sequence[OrderSummary]) -> None:
        self._orders = tuple(orders)
        iq_orders = [o for o in self._orders if "IQ" in o.broker.upper()]

        settled = [
            order
            for order in iq_orders
            if order.state == "SETTLED" and order.realized_pnl_minor_units is not None
        ]
        if not settled:
            self._net_val.setText("$0.00")
            self._gain_val.setText("$0.00")
            self._loss_val.setText("$0.00")
            self._win_val.setText("—")
            return

        gains = sum(
            order.realized_pnl_minor_units or 0
            for order in settled
            if (order.realized_pnl_minor_units or 0) > 0
        )
        losses = sum(
            abs(order.realized_pnl_minor_units or 0)
            for order in settled
            if (order.realized_pnl_minor_units or 0) < 0
        )
        net = gains - losses
        wins = sum(1 for order in settled if (order.realized_pnl_minor_units or 0) > 0)
        win_rate = (wins / len(settled)) * 100.0 if settled else 0.0

        curr = settled[0].currency or "USD"
        self._net_val.setText(format_minor_units(net, curr, positive_sign=True))
        color_val = ACCENT_GREEN if net >= 0 else ACCENT_RED
        self._net_val.setStyleSheet(f"color: {color_val}; font-size: 16px; font-weight: bold;")
        self._gain_val.setText(format_minor_units(gains, curr))
        self._loss_val.setText(format_minor_units(losses, curr))
        self._win_val.setText(f"{win_rate:.1f}% ({wins}/{len(settled)})")

    def update_config(self, config: UiIqOptionRiskConfig | None) -> None:
        self._risk_config = config
        if config is None:
            return
        if config.symbol == "AUTO":
            self._mode_pill.setText("SELEÇÃO AUTOMÁTICA")
            self._mode_pill.setObjectName("StatusPillOnline")
        else:
            self._mode_pill.setText(f"ATIVO: {config.symbol}")
            self._mode_pill.setObjectName("StatusPillOnline")
        self._mode_pill.style().unpolish(self._mode_pill)
        self._mode_pill.style().polish(self._mode_pill)


__all__ = ["IqOptionStrategySummaryWidget"]
