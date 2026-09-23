from __future__ import annotations

from datetime import datetime
from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from apps.ui.components.iqoption_strategy_summary import calculate_martingale_cycle_stats
from apps.ui.components.kpi_card import KpiCard
from apps.ui.components.terminal_button import TerminalButton
from apps.ui.design.icons import icon
from apps.ui.formatting import format_minor_units
from apps.ui.i18n import t
from apps.ui.theme import (
    ACCENT_AMBER,
    ACCENT_GREEN,
    ACCENT_PRIMARY,
    ACCENT_RED,
    BG_CARD,
    BG_SURFACE,
    BORDER_COLOR,
    FONT_MONO,
    RADIUS_SM,
    TEXT_MUTED,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
)
from packages.protocol import UiGlobalState, UiIqOptionAssetRank, UiProjectionSnapshot


def _create_vsep() -> QFrame:
    sep = QFrame()
    sep.setFrameShape(QFrame.Shape.VLine)
    sep.setFrameShadow(QFrame.Shadow.Plain)
    sep.setStyleSheet(
        f"background-color: {BORDER_COLOR}; max-width: 1px; min-width: 1px; border: none;"
    )
    return sep


def _set_style_if_changed(widget: QWidget, style: str) -> None:
    if widget.styleSheet() != style:
        widget.setStyleSheet(style)


class OverviewPage(QWidget):
    """Institutional overview page following Trading Lab Design System v2."""

    configure_clicked = Signal()
    bot_toggle_clicked = Signal()
    deriv_bot_toggle_clicked = Signal()
    iqoption_bot_toggle_clicked = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._raw_ranking: tuple[UiIqOptionAssetRank, ...] = ()
        self._active_broker = "Deriv"
        self._is_real_mode = False
        self._bot_running = False
        self._last_snapshot: UiProjectionSnapshot | None = None
        self._last_snapshot_sig: tuple[Any, ...] | None = None
        self._last_connected: bool | None = None
        self._last_snapshot_time: str = "--:--:--"
        self._last_radar_sig: tuple[Any, ...] | None = None
        self._last_orders_sig: tuple[Any, ...] | None = None

        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        content = QWidget()
        self._content_layout = QVBoxLayout(content)
        self._content_layout.setContentsMargins(24, 20, 24, 20)
        self._content_layout.setSpacing(16)

        # 1. Dual-Broker Master Section (Bots: Deriv + IQ Option Side-by-Side no topo)
        self._dual_cards = self._build_dual_broker_cards()
        self._content_layout.addWidget(self._dual_cards)

        # 2. HeroCard (height ~130px, 4 columns separated by 1px vertical borders)
        self._hero = self._build_hero_card()
        self._content_layout.addWidget(self._hero)

        # 3. Row of 4 KpiCards
        kpis_layout = QHBoxLayout()
        kpis_layout.setSpacing(14)

        self._kpi_trades = KpiCard(
            title=t("kpi.total_trades"),
            value="0",
            delta=t("kpi.today_delta", value="+0"),
            tooltip=t("kpi.total_trades_tip"),
        )
        kpis_layout.addWidget(self._kpi_trades)

        self._kpi_wins = KpiCard(
            title=t("kpi.wins"),
            value="0",
            show_gauge=True,
            gauge_percent=0.0,
            gauge_color=ACCENT_GREEN,
            tooltip=t("kpi.wins_tip"),
        )
        kpis_layout.addWidget(self._kpi_wins)

        self._kpi_losses = KpiCard(
            title=t("kpi.losses"),
            value="0",
            show_gauge=True,
            gauge_percent=0.0,
            gauge_color=ACCENT_RED,
            tooltip=t("kpi.losses_tip"),
        )
        kpis_layout.addWidget(self._kpi_losses)

        self._kpi_profit = KpiCard(
            title=t("kpi.net_profit"),
            value="$ 0.00 USD",
            delta=t("kpi.today_delta", value="+0.00"),
            tooltip=t("kpi.net_profit_tip"),
        )
        kpis_layout.addWidget(self._kpi_profit)
        self._lbl_net_profit_val = self._kpi_profit._lbl_value

        self._content_layout.addLayout(kpis_layout)

        # 4. Consolidated Active Orders Card
        self._orders_card = self._build_active_orders_card()
        self._content_layout.addWidget(self._orders_card)

        # 5. RadarCard (Market radar with search, filter, and 7-col table)
        self._radar_card = self._build_radar_card()
        self._content_layout.addWidget(self._radar_card, 1)

        scroll.setWidget(content)
        root_layout.addWidget(scroll)

        # Primary Action Button for BottomBar
        self.primary_action_btn = QPushButton()
        self.primary_action_btn.setObjectName("primary")
        self.primary_action_btn.setText(t("action.start_bot", broker=self._active_broker.upper()))
        self.primary_action_btn.setIcon(icon("icon-play", "#0A0F14", 16))
        self.primary_action_btn.clicked.connect(self.bot_toggle_clicked.emit)

    def _build_hero_card(self) -> QFrame:
        hero = QFrame()
        hero.setObjectName("card")
        hero.setMinimumHeight(125)
        hero.setMaximumHeight(140)

        layout = QHBoxLayout(hero)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Column 1: Active Strategy & Broker
        col1 = QWidget()
        col1_layout = QVBoxLayout(col1)
        col1_layout.setContentsMargins(18, 14, 18, 14)
        col1_layout.setSpacing(4)

        self._lbl_strategy_label = QLabel(t("overview.active_strategy"))
        self._lbl_strategy_label.setObjectName("kpiLabel")
        self._lbl_strategy_label.setStyleSheet(
            f"color: {TEXT_SECONDARY}; font-size: 11px; font-weight: 600; "
            f"text-transform: uppercase;"
        )
        col1_layout.addWidget(self._lbl_strategy_label)

        self._lbl_strategy_name = QLabel("—")
        self._lbl_strategy_name.setStyleSheet(
            f"font-size: 18px; font-weight: 700; color: {TEXT_PRIMARY};"
        )
        col1_layout.addWidget(self._lbl_strategy_name)

        chips_row = QHBoxLayout()
        chips_row.setSpacing(6)
        chips_row.setContentsMargins(0, 2, 0, 0)

        self._lbl_broker_chip = QLabel("Deriv")
        self._lbl_broker_chip.setStyleSheet(
            f"background-color: {BG_SURFACE}; color: {TEXT_SECONDARY}; "
            f"border: 1px solid {BORDER_COLOR}; border-radius: 4px; "
            f"padding: 2px 8px; font-size: 11px; font-weight: 600;"
        )
        chips_row.addWidget(self._lbl_broker_chip)

        self._lbl_mode_chip = QLabel(t("mode.practice"))
        self._lbl_mode_chip.setStyleSheet(
            f"background-color: transparent; color: {ACCENT_AMBER}; "
            f"border: 1px solid {ACCENT_AMBER}; border-radius: 4px; "
            f"padding: 2px 8px; font-size: 11px; font-weight: 700;"
        )
        self._lbl_mode_chip.setToolTip(t("mode.practice_tip"))
        chips_row.addWidget(self._lbl_mode_chip)

        self._btn_configure = QPushButton(t("overview.configure"))
        self._btn_configure.setObjectName("secondary")
        self._btn_configure.setStyleSheet(
            "padding: 2px 10px; font-size: 11px; min-height: 22px; "
            "max-height: 22px; border-radius: 4px;"
        )
        self._btn_configure.clicked.connect(self.configure_clicked.emit)
        chips_row.addWidget(self._btn_configure)
        chips_row.addStretch()

        col1_layout.addLayout(chips_row)
        layout.addWidget(col1, 3)

        layout.addWidget(_create_vsep())

        # Column 2: Trading Core Status
        col2 = QWidget()
        col2_layout = QVBoxLayout(col2)
        col2_layout.setContentsMargins(18, 14, 18, 14)
        col2_layout.setSpacing(4)

        self._lbl_core_state_label = QLabel(t("overview.state"))
        self._lbl_core_state_label.setObjectName("kpiLabel")
        self._lbl_core_state_label.setStyleSheet(
            f"color: {TEXT_SECONDARY}; font-size: 11px; font-weight: 600; "
            f"text-transform: uppercase;"
        )
        col2_layout.addWidget(self._lbl_core_state_label)

        self._lbl_core_status_val = QLabel("CONECTADO")
        self._lbl_core_status_val.setStyleSheet(
            f"font-size: 18px; font-weight: 700; color: {ACCENT_GREEN};"
        )
        col2_layout.addWidget(self._lbl_core_status_val)

        self._lbl_core_subtitle = QLabel(t("overview.core_operational"))
        self._lbl_core_subtitle.setStyleSheet(f"color: {TEXT_MUTED}; font-size: 11px;")
        col2_layout.addWidget(self._lbl_core_subtitle)
        col2_layout.addStretch()

        layout.addWidget(col2, 2)

        layout.addWidget(_create_vsep())

        # Column 3: Balance
        col3 = QWidget()
        col3_layout = QVBoxLayout(col3)
        col3_layout.setContentsMargins(18, 14, 18, 14)
        col3_layout.setSpacing(4)

        self._lbl_balance_label = QLabel(f"{t('overview.balance')} ({t('mode.practice')})")
        self._lbl_balance_label.setObjectName("kpiLabel")
        self._lbl_balance_label.setStyleSheet(
            f"color: {TEXT_SECONDARY}; font-size: 11px; font-weight: 600; "
            f"text-transform: uppercase;"
        )
        col3_layout.addWidget(self._lbl_balance_label)

        self._lbl_balance_val = QLabel("$ 0.00 USD")
        self._lbl_balance_val.setStyleSheet(
            f"font-family: {FONT_MONO}; font-size: 20px; font-weight: 700; color: {TEXT_PRIMARY};"
        )
        col3_layout.addWidget(self._lbl_balance_val)

        self._lbl_updated_time = QLabel(t("overview.updated_at", time="--:--:--"))
        self._lbl_updated_time.setStyleSheet(f"color: {TEXT_MUTED}; font-size: 11px;")
        col3_layout.addWidget(self._lbl_updated_time)
        col3_layout.addStretch()

        layout.addWidget(col3, 2)

        layout.addWidget(_create_vsep())

        # Column 4: Bot Status
        col4 = QWidget()
        col4_layout = QVBoxLayout(col4)
        col4_layout.setContentsMargins(18, 14, 18, 14)
        col4_layout.setSpacing(4)

        self._lbl_bot_state_label = QLabel(t("overview.bot_state"))
        self._lbl_bot_state_label.setObjectName("kpiLabel")
        self._lbl_bot_state_label.setStyleSheet(
            f"color: {TEXT_SECONDARY}; font-size: 11px; font-weight: 600; "
            f"text-transform: uppercase;"
        )
        col4_layout.addWidget(self._lbl_bot_state_label)

        bot_status_row = QHBoxLayout()
        bot_status_row.setSpacing(6)
        bot_status_row.setContentsMargins(0, 0, 0, 0)

        self._lbl_bot_icon = QLabel()
        self._lbl_bot_icon.setPixmap(icon("icon-clock", ACCENT_AMBER, 16).pixmap(16, 16))
        bot_status_row.addWidget(self._lbl_bot_icon)

        self._lbl_bot_status_val = QLabel(t("bot.idle"))
        self._lbl_bot_status_val.setStyleSheet(
            f"font-size: 16px; font-weight: 700; color: {ACCENT_AMBER};"
        )
        self._lbl_bot_status_val.setToolTip(t("bot.state_tip"))
        bot_status_row.addWidget(self._lbl_bot_status_val)
        bot_status_row.addStretch()
        col4_layout.addLayout(bot_status_row)

        self._lbl_bot_hint = QLabel(t("bot.idle_hint"))
        self._lbl_bot_hint.setStyleSheet(f"color: {TEXT_MUTED}; font-size: 11px;")
        col4_layout.addWidget(self._lbl_bot_hint)
        col4_layout.addStretch()

        layout.addWidget(col4, 2)

        return hero

    def _build_dual_broker_cards(self) -> QWidget:
        container = QWidget()
        layout = QHBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)

        # 1. DERIV CARD
        self._card_deriv = QFrame()
        self._card_deriv.setObjectName("card")
        self._card_deriv.setStyleSheet(
            f"QFrame#card {{ background-color: {BG_CARD}; "
            f"border: 1px solid {BORDER_COLOR}; border-radius: {RADIUS_SM}px; }}"
        )
        deriv_lay = QVBoxLayout(self._card_deriv)
        deriv_lay.setContentsMargins(18, 14, 18, 14)
        deriv_lay.setSpacing(10)

        # Header row: Logo + Title + Mode Chip + Status Badge
        d_hdr = QHBoxLayout()
        d_hdr.setSpacing(8)
        self._lbl_deriv_logo = QLabel()
        self._lbl_deriv_logo.setPixmap(icon("logo-deriv-official", "", 20).pixmap(20, 20))
        d_hdr.addWidget(self._lbl_deriv_logo)
        self._lbl_deriv_card_title = QLabel(t("overview.deriv_panel_title"))
        self._lbl_deriv_card_title.setStyleSheet(
            f"font-size: 15px; font-weight: 700; color: {TEXT_PRIMARY};"
        )
        d_hdr.addWidget(self._lbl_deriv_card_title)
        d_hdr.addStretch()

        self._lbl_deriv_mode_badge = QLabel(t("mode.practice"))
        self._lbl_deriv_mode_badge.setStyleSheet(
            f"border: 1px solid {ACCENT_AMBER}; color: {ACCENT_AMBER}; "
            f"border-radius: 4px; padding: 2px 8px; font-size: 11px; font-weight: 700;"
        )
        d_hdr.addWidget(self._lbl_deriv_mode_badge)

        self._lbl_deriv_conn_badge = QLabel(t("broker.connected"))
        self._lbl_deriv_conn_badge.setStyleSheet(
            f"background-color: rgba(31, 181, 122, 0.15); color: {ACCENT_GREEN}; "
            f"border-radius: 4px; padding: 2px 8px; font-size: 11px; font-weight: 700;"
        )
        d_hdr.addWidget(self._lbl_deriv_conn_badge)

        self._btn_deriv_action = TerminalButton(
            text=t("overview.deriv_bot_action_start"),
            variant="primary",
            icon_name="icon-play",
        )
        self._btn_deriv_action.debounced_clicked.connect(self.deriv_bot_toggle_clicked.emit)
        self._btn_deriv_action.debounced_clicked.connect(self.bot_toggle_clicked.emit)
        d_hdr.addWidget(self._btn_deriv_action)
        deriv_lay.addLayout(d_hdr)

        # Operational status & wait reason
        self._lbl_deriv_op_status = QLabel(t("operational.connected_bot_off"))
        self._lbl_deriv_op_status.setStyleSheet(
            f"font-size: 14px; font-weight: 700; color: {TEXT_PRIMARY};"
        )
        deriv_lay.addWidget(self._lbl_deriv_op_status)

        self._lbl_deriv_op_hint = QLabel(t("bot.idle_hint"))
        self._lbl_deriv_op_hint.setStyleSheet(f"font-size: 11px; color: {TEXT_MUTED};")
        deriv_lay.addWidget(self._lbl_deriv_op_hint)

        # Separator line
        sep1 = QFrame()
        sep1.setFrameShape(QFrame.Shape.HLine)
        sep1.setStyleSheet(f"background-color: {BORDER_COLOR}; max-height: 1px; border: none;")
        deriv_lay.addWidget(sep1)

        # Metrics 3-column row (Saldo, Exposição, Lucro Líquido)
        d_metrics = QHBoxLayout()
        d_metrics.setSpacing(12)

        # Saldo
        col_b = QVBoxLayout()
        col_b.setSpacing(2)
        lbl_b_title = QLabel(t("overview.confirmed_balance"))
        lbl_b_title.setStyleSheet(f"font-size: 11px; color: {TEXT_SECONDARY}; font-weight: 600;")
        col_b.addWidget(lbl_b_title)
        self._lbl_deriv_bal_val = QLabel("$ 0.00 USD")
        self._lbl_deriv_bal_val.setStyleSheet(
            f"font-size: 16px; font-weight: 700; color: {TEXT_PRIMARY}; font-family: {FONT_MONO};"
        )
        col_b.addWidget(self._lbl_deriv_bal_val)
        d_metrics.addLayout(col_b, 1)

        # Exposición / Órdenes
        col_e = QVBoxLayout()
        col_e.setSpacing(2)
        lbl_e_title = QLabel(t("overview.exposure_label"))
        lbl_e_title.setStyleSheet(f"font-size: 11px; color: {TEXT_SECONDARY}; font-weight: 600;")
        col_e.addWidget(lbl_e_title)
        self._lbl_deriv_exp_val = QLabel("$ 0.00 USD")
        self._lbl_deriv_exp_val.setStyleSheet(
            f"font-size: 14px; font-weight: 600; color: {TEXT_PRIMARY}; font-family: {FONT_MONO};"
        )
        col_e.addWidget(self._lbl_deriv_exp_val)
        d_metrics.addLayout(col_e, 1)

        # Lucro Líquido (Resultado Real da Deriv)
        col_p = QVBoxLayout()
        col_p.setSpacing(2)
        self._lbl_deriv_pnl_title = QLabel(t("overview.net_profit_label"))
        self._lbl_deriv_pnl_title.setStyleSheet(
            f"font-size: 11px; color: {TEXT_SECONDARY}; font-weight: 600;"
        )
        col_p.addWidget(self._lbl_deriv_pnl_title)
        self._lbl_deriv_pnl_val = QLabel("$ 0.00 USD")
        self._lbl_deriv_pnl_val.setStyleSheet(
            f"font-size: 14px; font-weight: 700; color: {ACCENT_GREEN}; font-family: {FONT_MONO};"
        )
        col_p.addWidget(self._lbl_deriv_pnl_val)
        d_metrics.addLayout(col_p, 1)

        deriv_lay.addLayout(d_metrics)

        # Dedicated stats row: Total Ops, Wins, Losses, Win Rate
        d_stats_frame = QFrame()
        d_stats_frame.setStyleSheet(
            f"background-color: {BG_SURFACE}; border: 1px solid {BORDER_COLOR}; border-radius: 6px;"
        )
        d_stats_lay = QHBoxLayout(d_stats_frame)
        d_stats_lay.setContentsMargins(10, 6, 10, 6)
        d_stats_lay.setSpacing(8)

        col_st_t = QVBoxLayout()
        col_st_t.setSpacing(2)
        self._lbl_deriv_trades_title = QLabel(t("overview.stats_trades"))
        self._lbl_deriv_trades_title.setStyleSheet(
            f"font-size: 10px; color: {TEXT_MUTED}; font-weight: 600; text-transform: uppercase;"
        )
        col_st_t.addWidget(self._lbl_deriv_trades_title)
        self._lbl_deriv_trades_val = QLabel("0")
        self._lbl_deriv_trades_val.setStyleSheet(
            f"font-size: 13px; font-weight: 700; color: {TEXT_PRIMARY}; font-family: {FONT_MONO};"
        )
        col_st_t.addWidget(self._lbl_deriv_trades_val)
        d_stats_lay.addLayout(col_st_t, 1)

        col_st_w = QVBoxLayout()
        col_st_w.setSpacing(2)
        self._lbl_deriv_wins_title = QLabel(t("overview.stats_wins"))
        self._lbl_deriv_wins_title.setStyleSheet(
            f"font-size: 10px; color: {TEXT_MUTED}; font-weight: 600; text-transform: uppercase;"
        )
        col_st_w.addWidget(self._lbl_deriv_wins_title)
        self._lbl_deriv_wins_val = QLabel("0")
        self._lbl_deriv_wins_val.setStyleSheet(
            f"font-size: 13px; font-weight: 700; color: {ACCENT_GREEN}; font-family: {FONT_MONO};"
        )
        col_st_w.addWidget(self._lbl_deriv_wins_val)
        d_stats_lay.addLayout(col_st_w, 1)

        col_st_l = QVBoxLayout()
        col_st_l.setSpacing(2)
        self._lbl_deriv_losses_title = QLabel(t("overview.stats_losses"))
        self._lbl_deriv_losses_title.setStyleSheet(
            f"font-size: 10px; color: {TEXT_MUTED}; font-weight: 600; text-transform: uppercase;"
        )
        col_st_l.addWidget(self._lbl_deriv_losses_title)
        self._lbl_deriv_losses_val = QLabel("0")
        self._lbl_deriv_losses_val.setStyleSheet(
            f"font-size: 13px; font-weight: 700; color: {ACCENT_RED}; font-family: {FONT_MONO};"
        )
        col_st_l.addWidget(self._lbl_deriv_losses_val)
        d_stats_lay.addLayout(col_st_l, 1)

        col_st_r = QVBoxLayout()
        col_st_r.setSpacing(2)
        self._lbl_deriv_winrate_title = QLabel(t("overview.stats_winrate"))
        self._lbl_deriv_winrate_title.setStyleSheet(
            f"font-size: 10px; color: {TEXT_MUTED}; font-weight: 600; text-transform: uppercase;"
        )
        col_st_r.addWidget(self._lbl_deriv_winrate_title)
        self._lbl_deriv_winrate_val = QLabel("0.0%")
        self._lbl_deriv_winrate_val.setStyleSheet(
            f"font-size: 13px; font-weight: 700; color: {ACCENT_PRIMARY}; font-family: {FONT_MONO};"
        )
        col_st_r.addWidget(self._lbl_deriv_winrate_val)
        d_stats_lay.addLayout(col_st_r, 1)

        deriv_lay.addWidget(d_stats_frame)

        # Strategy footer row
        d_action_row = QHBoxLayout()
        d_action_row.setSpacing(8)
        self._lbl_deriv_strat_info = QLabel(t("overview.deriv_strategy_default"))
        self._lbl_deriv_strat_info.setStyleSheet(f"font-size: 12px; color: {TEXT_MUTED};")
        d_action_row.addWidget(self._lbl_deriv_strat_info, 1)
        deriv_lay.addLayout(d_action_row)

        layout.addWidget(self._card_deriv, 1)

        # 2. IQ OPTION CARD
        self._card_iq = QFrame()
        self._card_iq.setObjectName("card")
        self._card_iq.setStyleSheet(
            f"QFrame#card {{ background-color: {BG_CARD}; "
            f"border: 1px solid {BORDER_COLOR}; border-radius: {RADIUS_SM}px; }}"
        )
        iq_lay = QVBoxLayout(self._card_iq)
        iq_lay.setContentsMargins(18, 14, 18, 14)
        iq_lay.setSpacing(10)

        # Header row: Logo + Title + Mode Chip + Status Badge
        iq_hdr = QHBoxLayout()
        iq_hdr.setSpacing(8)
        self._lbl_iq_logo = QLabel()
        self._lbl_iq_logo.setPixmap(icon("logo-iqoption-official", "", 20).pixmap(20, 20))
        iq_hdr.addWidget(self._lbl_iq_logo)
        self._lbl_iq_card_title = QLabel(t("overview.iq_panel_title"))
        self._lbl_iq_card_title.setStyleSheet(
            f"font-size: 15px; font-weight: 700; color: {TEXT_PRIMARY};"
        )
        iq_hdr.addWidget(self._lbl_iq_card_title)
        iq_hdr.addStretch()

        self._lbl_iq_mode_badge = QLabel(t("mode.practice"))
        self._lbl_iq_mode_badge.setStyleSheet(
            f"border: 1px solid {ACCENT_AMBER}; color: {ACCENT_AMBER}; "
            f"border-radius: 4px; padding: 2px 8px; font-size: 11px; font-weight: 700;"
        )
        iq_hdr.addWidget(self._lbl_iq_mode_badge)

        self._lbl_iq_conn_badge = QLabel(t("broker.connected"))
        self._lbl_iq_conn_badge.setStyleSheet(
            f"background-color: rgba(31, 181, 122, 0.15); color: {ACCENT_GREEN}; "
            f"border-radius: 4px; padding: 2px 8px; font-size: 11px; font-weight: 700;"
        )
        iq_hdr.addWidget(self._lbl_iq_conn_badge)

        self._btn_iq_action = TerminalButton(
            text=t("overview.iq_bot_action_start"),
            variant="primary",
            icon_name="icon-play",
        )
        self._btn_iq_action.debounced_clicked.connect(self.iqoption_bot_toggle_clicked.emit)
        self._btn_iq_action.debounced_clicked.connect(self.bot_toggle_clicked.emit)
        iq_hdr.addWidget(self._btn_iq_action)
        iq_lay.addLayout(iq_hdr)

        # Operational status & wait reason
        self._lbl_iq_op_status = QLabel(t("operational.connected_bot_off"))
        self._lbl_iq_op_status.setStyleSheet(
            f"font-size: 14px; font-weight: 700; color: {TEXT_PRIMARY};"
        )
        iq_lay.addWidget(self._lbl_iq_op_status)

        self._lbl_iq_op_hint = QLabel(t("bot.idle_hint"))
        self._lbl_iq_op_hint.setStyleSheet(f"font-size: 11px; color: {TEXT_MUTED};")
        iq_lay.addWidget(self._lbl_iq_op_hint)

        # Separator line
        sep2 = QFrame()
        sep2.setFrameShape(QFrame.Shape.HLine)
        sep2.setStyleSheet(f"background-color: {BORDER_COLOR}; max-height: 1px; border: none;")
        iq_lay.addWidget(sep2)

        # Metrics 3-column row (Saldo, Exposição, Lucro Líquido)
        iq_metrics = QHBoxLayout()
        iq_metrics.setSpacing(12)

        # Saldo
        col_iq_b = QVBoxLayout()
        col_iq_b.setSpacing(2)
        lbl_iq_b_title = QLabel(t("overview.confirmed_balance"))
        lbl_iq_b_title.setStyleSheet(f"font-size: 11px; color: {TEXT_SECONDARY}; font-weight: 600;")
        col_iq_b.addWidget(lbl_iq_b_title)
        self._lbl_iq_bal_val = QLabel("$ 0.00 USD")
        self._lbl_iq_bal_val.setStyleSheet(
            f"font-size: 16px; font-weight: 700; color: {TEXT_PRIMARY}; font-family: {FONT_MONO};"
        )
        col_iq_b.addWidget(self._lbl_iq_bal_val)
        iq_metrics.addLayout(col_iq_b, 1)

        # Exposición / Órdenes
        col_iq_e = QVBoxLayout()
        col_iq_e.setSpacing(2)
        lbl_iq_e_title = QLabel(t("overview.exposure_label"))
        lbl_iq_e_title.setStyleSheet(f"font-size: 11px; color: {TEXT_SECONDARY}; font-weight: 600;")
        col_iq_e.addWidget(lbl_iq_e_title)
        self._lbl_iq_exp_val = QLabel("$ 0.00 USD")
        self._lbl_iq_exp_val.setStyleSheet(
            f"font-size: 14px; font-weight: 600; color: {TEXT_PRIMARY}; font-family: {FONT_MONO};"
        )
        col_iq_e.addWidget(self._lbl_iq_exp_val)
        iq_metrics.addLayout(col_iq_e, 1)

        # Lucro Líquido (Resultado Real da IQ Option)
        col_iq_p = QVBoxLayout()
        col_iq_p.setSpacing(2)
        self._lbl_iq_pnl_title = QLabel(t("overview.net_profit_label"))
        self._lbl_iq_pnl_title.setStyleSheet(
            f"font-size: 11px; color: {TEXT_SECONDARY}; font-weight: 600;"
        )
        col_iq_p.addWidget(self._lbl_iq_pnl_title)
        self._lbl_iq_pnl_val = QLabel("$ 0.00 USD")
        self._lbl_iq_pnl_val.setStyleSheet(
            f"font-size: 14px; font-weight: 700; color: {ACCENT_GREEN}; font-family: {FONT_MONO};"
        )
        col_iq_p.addWidget(self._lbl_iq_pnl_val)
        iq_metrics.addLayout(col_iq_p, 1)

        iq_lay.addLayout(iq_metrics)

        # Dedicated stats row: Total Ops, Wins, Losses, Win Rate
        iq_stats_frame = QFrame()
        iq_stats_frame.setStyleSheet(
            f"background-color: {BG_SURFACE}; border: 1px solid {BORDER_COLOR}; border-radius: 6px;"
        )
        iq_stats_lay = QHBoxLayout(iq_stats_frame)
        iq_stats_lay.setContentsMargins(10, 6, 10, 6)
        iq_stats_lay.setSpacing(8)

        col_iq_st_t = QVBoxLayout()
        col_iq_st_t.setSpacing(2)
        self._lbl_iq_trades_title = QLabel(t("overview.stats_trades"))
        self._lbl_iq_trades_title.setStyleSheet(
            f"font-size: 10px; color: {TEXT_MUTED}; font-weight: 600; text-transform: uppercase;"
        )
        col_iq_st_t.addWidget(self._lbl_iq_trades_title)
        self._lbl_iq_trades_val = QLabel("0")
        self._lbl_iq_trades_val.setStyleSheet(
            f"font-size: 13px; font-weight: 700; color: {TEXT_PRIMARY}; font-family: {FONT_MONO};"
        )
        col_iq_st_t.addWidget(self._lbl_iq_trades_val)
        iq_stats_lay.addLayout(col_iq_st_t, 1)

        col_iq_st_w = QVBoxLayout()
        col_iq_st_w.setSpacing(2)
        self._lbl_iq_wins_title = QLabel(t("overview.stats_wins"))
        self._lbl_iq_wins_title.setStyleSheet(
            f"font-size: 10px; color: {TEXT_MUTED}; font-weight: 600; text-transform: uppercase;"
        )
        col_iq_st_w.addWidget(self._lbl_iq_wins_title)
        self._lbl_iq_wins_val = QLabel("0")
        self._lbl_iq_wins_val.setStyleSheet(
            f"font-size: 13px; font-weight: 700; color: {ACCENT_GREEN}; font-family: {FONT_MONO};"
        )
        col_iq_st_w.addWidget(self._lbl_iq_wins_val)
        iq_stats_lay.addLayout(col_iq_st_w, 1)

        col_iq_st_l = QVBoxLayout()
        col_iq_st_l.setSpacing(2)
        self._lbl_iq_losses_title = QLabel(t("overview.stats_losses"))
        self._lbl_iq_losses_title.setStyleSheet(
            f"font-size: 10px; color: {TEXT_MUTED}; font-weight: 600; text-transform: uppercase;"
        )
        col_iq_st_l.addWidget(self._lbl_iq_losses_title)
        self._lbl_iq_losses_val = QLabel("0")
        self._lbl_iq_losses_val.setStyleSheet(
            f"font-size: 13px; font-weight: 700; color: {ACCENT_RED}; font-family: {FONT_MONO};"
        )
        col_iq_st_l.addWidget(self._lbl_iq_losses_val)
        iq_stats_lay.addLayout(col_iq_st_l, 1)

        col_iq_st_r = QVBoxLayout()
        col_iq_st_r.setSpacing(2)
        self._lbl_iq_winrate_title = QLabel(t("overview.stats_winrate"))
        self._lbl_iq_winrate_title.setStyleSheet(
            f"font-size: 10px; color: {TEXT_MUTED}; font-weight: 600; text-transform: uppercase;"
        )
        col_iq_st_r.addWidget(self._lbl_iq_winrate_title)
        self._lbl_iq_winrate_val = QLabel("0.0%")
        self._lbl_iq_winrate_val.setStyleSheet(
            f"font-size: 13px; font-weight: 700; color: {ACCENT_PRIMARY}; font-family: {FONT_MONO};"
        )
        col_iq_st_r.addWidget(self._lbl_iq_winrate_val)
        iq_stats_lay.addLayout(col_iq_st_r, 1)

        iq_lay.addWidget(iq_stats_frame)

        # Strategy footer row
        iq_action_row = QHBoxLayout()
        iq_action_row.setSpacing(8)
        self._lbl_iq_strat_info = QLabel(t("overview.iq_strategy_default"))
        self._lbl_iq_strat_info.setStyleSheet(f"font-size: 12px; color: {TEXT_MUTED};")
        iq_action_row.addWidget(self._lbl_iq_strat_info, 1)
        iq_lay.addLayout(iq_action_row)

        layout.addWidget(self._card_iq, 1)

        return container

    def _build_active_orders_card(self) -> QFrame:
        card = QFrame()
        card.setObjectName("card")
        card.setStyleSheet(
            f"QFrame#card {{ background-color: {BG_CARD}; "
            f"border: 1px solid {BORDER_COLOR}; border-radius: {RADIUS_SM}px; }}"
        )
        card_lay = QVBoxLayout(card)
        card_lay.setContentsMargins(18, 14, 18, 14)
        card_lay.setSpacing(10)

        # Header
        hdr = QHBoxLayout()
        self._lbl_orders_title = QLabel(t("overview.open_orders_title"))
        self._lbl_orders_title.setStyleSheet(
            f"font-size: 15px; font-weight: 700; color: {TEXT_PRIMARY};"
        )
        hdr.addWidget(self._lbl_orders_title)
        hdr.addStretch()
        self._lbl_orders_count = QLabel(t("overview.active_orders_count", count=0))
        self._lbl_orders_count.setStyleSheet(f"font-size: 12px; color: {TEXT_MUTED};")
        hdr.addWidget(self._lbl_orders_count)
        card_lay.addLayout(hdr)

        # Empty state label
        self._lbl_no_orders = QLabel(t("overview.no_open_orders"))
        self._lbl_no_orders.setStyleSheet(
            f"color: {TEXT_MUTED}; font-size: 12px; padding: 18px; "
            f"background-color: {BG_SURFACE}; border-radius: {RADIUS_SM}px;"
        )
        self._lbl_no_orders.setAlignment(Qt.AlignmentFlag.AlignCenter)
        card_lay.addWidget(self._lbl_no_orders)

        # Active orders table
        self._table_active_orders = QTableWidget(0, 6)
        self._table_active_orders.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table_active_orders.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        self._table_active_orders.verticalHeader().setVisible(False)
        self._table_active_orders.setAlternatingRowColors(True)
        self._table_active_orders.setMinimumHeight(120)
        self._table_active_orders.setHorizontalHeaderLabels(
            [
                t("overview.col_broker"),
                t("overview.col_asset"),
                t("overview.col_direction"),
                t("overview.col_stake"),
                t("overview.col_opened_at"),
                t("overview.col_status"),
            ]
        )
        self._table_active_orders.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )
        self._table_active_orders.hide()
        card_lay.addWidget(self._table_active_orders)

        return card

    def _build_radar_card(self) -> QFrame:
        card = QFrame()
        card.setObjectName("card")

        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(20, 16, 20, 16)
        card_layout.setSpacing(14)

        # Header with Title, Subtitle, Search Input, and Category Filter
        header_row = QHBoxLayout()
        header_row.setSpacing(12)

        titles_col = QVBoxLayout()
        titles_col.setSpacing(2)

        self._radar_title = QLabel(t("radar.title"))
        self._radar_title.setStyleSheet(
            f"font-size: 16px; font-weight: 700; color: {TEXT_PRIMARY};"
        )
        titles_col.addWidget(self._radar_title)

        self._radar_subtitle = QLabel(t("radar.subtitle"))
        self._radar_subtitle.setStyleSheet(f"font-size: 12px; color: {TEXT_MUTED};")
        titles_col.addWidget(self._radar_subtitle)
        header_row.addLayout(titles_col, 1)

        # Search field
        self._search_input = QLineEdit()
        self._search_input.setPlaceholderText(t("radar.search_placeholder"))
        self._search_input.setFixedWidth(200)
        self._search_input.textChanged.connect(self._on_radar_filter_changed)
        header_row.addWidget(self._search_input)

        # Category combo
        self._filter_combo = QComboBox()
        self._filter_combo.setFixedWidth(130)
        self._filter_combo.addItem(t("radar.filter_all"))
        self._filter_combo.addItem(t("radar.filter_forex"))
        self._filter_combo.addItem(t("radar.filter_otc"))
        self._filter_combo.currentIndexChanged.connect(self._on_radar_filter_changed)
        header_row.addWidget(self._filter_combo)

        card_layout.addLayout(header_row)

        # 7-Column Table
        self._radar_table = QTableWidget(0, 7)
        self._radar_table.setObjectName("AssetRadarTable")
        self._radar_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._radar_table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        self._radar_table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._radar_table.verticalHeader().setVisible(False)
        self._radar_table.setAlternatingRowColors(True)
        self._radar_table.setMinimumHeight(260)

        headers = [
            t("radar.col_rank"),
            t("radar.col_asset"),
            t("radar.col_price"),
            t("radar.col_rsi"),
            t("radar.col_signal"),
            t("radar.col_status"),
            t("radar.col_updated"),
        ]
        self._radar_table.setHorizontalHeaderLabels(headers)

        th = self._radar_table.horizontalHeader()
        th.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        self._radar_table.setColumnWidth(0, 45)
        th.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        th.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        th.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        th.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        th.setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)
        th.setSectionResizeMode(6, QHeaderView.ResizeMode.ResizeToContents)

        rsi_hdr = self._radar_table.horizontalHeaderItem(3)
        if rsi_hdr is not None:
            rsi_hdr.setToolTip(t("radar.rsi_tip"))

        card_layout.addWidget(self._radar_table)

        # Empty State
        self._empty_label = QLabel(t("radar.empty"))
        self._empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_label.setStyleSheet(
            f"color: {TEXT_MUTED}; font-size: 13px; padding: 48px 16px; "
            f"background-color: {BG_SURFACE}; border-radius: {RADIUS_SM}px; "
            f"border: 1px dashed {BORDER_COLOR};"
        )
        card_layout.addWidget(self._empty_label)

        self._radar_table.hide()
        self._empty_label.show()

        return card

    def _on_radar_filter_changed(self) -> None:
        query = self._search_input.text().strip().lower()
        filter_idx = self._filter_combo.currentIndex()

        filtered: list[UiIqOptionAssetRank] = []
        for item in self._raw_ranking:
            # Filter category
            symbol_upper = item.symbol.upper()
            if filter_idx == 1 and "OTC" in symbol_upper:
                continue
            if filter_idx == 2 and "OTC" not in symbol_upper:
                continue

            # Search query
            if (
                query
                and query not in item.display_name.lower()
                and query not in item.symbol.lower()
            ):
                continue

            filtered.append(item)

        if not filtered:
            self._radar_table.setRowCount(0)
            self._radar_table.hide()
            self._empty_label.show()
            self._last_radar_sig = ()
            return

        radar_sig = tuple(
            (item.symbol, item.rsi, item.direction, item.status, item.selected, item.display_name)
            for item in filtered
        )
        if self._last_radar_sig == radar_sig:
            return
        self._last_radar_sig = radar_sig

        self._empty_label.hide()
        self._radar_table.show()
        target_rows = len(filtered)
        self._radar_table.setUpdatesEnabled(False)
        try:
            if self._radar_table.rowCount() != target_rows:
                self._radar_table.setRowCount(target_rows)

            for row, item in enumerate(filtered):
                # Col 0: Rank #
                rank_str = str(row + 1)
                rank_item = self._radar_table.item(row, 0)
                if rank_item is None:
                    rank_item = QTableWidgetItem(rank_str)
                    rank_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                    rank_item.setForeground(QColor(TEXT_MUTED))
                    self._radar_table.setItem(row, 0, rank_item)
                elif rank_item.text() != rank_str:
                    rank_item.setText(rank_str)

                # Col 1: Asset name
                asset_item = self._radar_table.item(row, 1)
                if asset_item is None:
                    asset_item = QTableWidgetItem(item.display_name)
                    font = asset_item.font()
                    font.setBold(True)
                    asset_item.setFont(font)
                    asset_item.setTextAlignment(
                        Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft
                    )
                    self._radar_table.setItem(row, 1, asset_item)
                elif asset_item.text() != item.display_name:
                    asset_item.setText(item.display_name)
                asset_tip = item.candidate_details or item.symbol
                if asset_item.toolTip() != asset_tip:
                    asset_item.setToolTip(asset_tip)

                # Col 2: Price
                price_item = self._radar_table.item(row, 2)
                if price_item is None:
                    price_item = QTableWidgetItem("--")
                    price_item.setTextAlignment(
                        Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight
                    )
                    price_item.setForeground(QColor(TEXT_MUTED))
                    self._radar_table.setItem(row, 2, price_item)

                # Col 3: RSI (14)
                rsi_val = 50.0
                try:
                    rsi_val = float(item.rsi) if item.rsi != "--" else 50.0
                except ValueError:
                    rsi_val = 50.0

                rsi_item = self._radar_table.item(row, 3)
                if rsi_item is None:
                    rsi_item = QTableWidgetItem(f"{item.rsi}")
                    rsi_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                    rsi_item.setToolTip(t("radar.rsi_tip"))
                    self._radar_table.setItem(row, 3, rsi_item)
                elif rsi_item.text() != str(item.rsi):
                    rsi_item.setText(str(item.rsi))

                if item.rsi == "--":
                    rsi_item.setForeground(QColor(TEXT_MUTED))
                elif rsi_val <= 30.0:
                    rsi_item.setForeground(QColor(ACCENT_GREEN))
                elif rsi_val >= 70.0:
                    rsi_item.setForeground(QColor(ACCENT_RED))
                else:
                    rsi_item.setForeground(QColor(ACCENT_PRIMARY))

                # Col 4: Signal (from strategy direction, never inferred solely from RSI)
                if item.direction == "CALL":
                    sig_text = t("signal.call")
                    sig_color = ACCENT_GREEN
                elif item.direction == "PUT":
                    sig_text = t("signal.put")
                    sig_color = ACCENT_RED
                else:
                    sig_text = t("signal.none")
                    sig_color = TEXT_MUTED

                sig_item = self._radar_table.item(row, 4)
                if sig_item is None:
                    sig_item = QTableWidgetItem(sig_text)
                    sig_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                    sig_item.setForeground(QColor(sig_color))
                    self._radar_table.setItem(row, 4, sig_item)
                else:
                    if sig_item.text() != sig_text:
                        sig_item.setText(sig_text)
                    sig_item.setForeground(QColor(sig_color))

                # Col 5: Status
                if item.selected:
                    st_text = t("radar.status_focus")
                    st_color = ACCENT_PRIMARY
                elif item.status == "TRIGGERED":
                    st_text = t("radar.status_triggered")
                    st_color = ACCENT_AMBER
                elif item.status == "WARMING_UP":
                    st_text = t("radar.status_warming_up")
                    st_color = ACCENT_AMBER
                elif item.status in {"DISCOVERY_ONLY", "WAITING_DATA"}:
                    st_text = item.status
                    st_color = TEXT_MUTED
                else:
                    st_text = t("radar.monitoring")
                    st_color = TEXT_MUTED

                st_item = self._radar_table.item(row, 5)
                if st_item is None:
                    st_item = QTableWidgetItem(st_text)
                    st_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                    st_item.setForeground(QColor(st_color))
                    self._radar_table.setItem(row, 5, st_item)
                else:
                    if st_item.text() != st_text:
                        st_item.setText(st_text)
                    st_item.setForeground(QColor(st_color))

                # Col 6: Updated
                upd_item = self._radar_table.item(row, 6)
                if upd_item is None:
                    upd_item = QTableWidgetItem(self._last_snapshot_time)
                    upd_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                    upd_item.setForeground(QColor(TEXT_MUTED))
                    self._radar_table.setItem(row, 6, upd_item)
                elif upd_item.text() != self._last_snapshot_time:
                    upd_item.setText(self._last_snapshot_time)
        finally:
            self._radar_table.setUpdatesEnabled(True)

    def update_projection(self, snapshot: UiProjectionSnapshot | None, controller: Any) -> None:
        connected = bool(controller.connected)
        snapshot_sig = snapshot.semantic_signature() if snapshot is not None else None
        if snapshot_sig == self._last_snapshot_sig and connected == self._last_connected:
            return
        self._last_snapshot = snapshot
        self._last_snapshot_sig = snapshot_sig
        self._last_connected = connected

        now_str = datetime.now().strftime("%H:%M:%S")
        self._last_snapshot_time = now_str

        # Core status column
        if connected:
            self._lbl_core_status_val.setText("CONECTADO")
            self._lbl_core_status_val.setStyleSheet(
                f"font-size: 18px; font-weight: 700; color: {ACCENT_GREEN};"
            )
            self._lbl_core_subtitle.setText(t("overview.core_operational"))
        else:
            self._lbl_core_status_val.setText("DESCONECTADO")
            self._lbl_core_status_val.setStyleSheet(
                f"font-size: 18px; font-weight: 700; color: {ACCENT_RED};"
            )
            self._lbl_core_subtitle.setText(t("overview.core_disconnected"))

        if snapshot is None:
            self._raw_ranking = ()
            self._on_radar_filter_changed()
            return

        # Determine active broker and mode
        deriv_card = next((c for c in snapshot.broker_cards if c.broker == "DERIV"), None)
        iq_card = next((c for c in snapshot.broker_cards if "IQ" in c.broker.upper()), None)

        if snapshot.iqoption_bot_armed or (
            iq_card and iq_card.is_connected and (not deriv_card or not deriv_card.is_connected)
        ):
            self._active_broker = "IQ Option"
            active_card = iq_card
        else:
            self._active_broker = "Deriv"
            active_card = deriv_card

        self._lbl_broker_chip.setText(self._active_broker)

        is_real = active_card is not None and active_card.account_mode.value == "REAL"
        self._is_real_mode = is_real

        mode_text = t("mode.REAL") if is_real else t("mode.practice")
        mode_color = ACCENT_RED if is_real else ACCENT_AMBER
        mode_tip = t("mode.real_tip") if is_real else t("mode.practice_tip")

        self._lbl_mode_chip.setText(mode_text)
        self._lbl_mode_chip.setStyleSheet(
            f"background-color: transparent; color: {mode_color}; border: 1px solid {mode_color}; "
            f"border-radius: 4px; padding: 2px 8px; font-size: 11px; font-weight: 700;"
        )
        self._lbl_mode_chip.setToolTip(mode_tip)

        # Active strategy name
        if self._active_broker == "IQ Option":
            strat_name = "RSI Bounded Edge"
        else:
            strat_id = (
                snapshot.digit_risk_config.active_strategy_id
                if snapshot.digit_risk_config
                else "tail-probability-edge"
            )
            strat_names = {
                "tail-probability-edge": "Tail Probability Edge",
                "selective-differs-edge": "Selective Differs Edge",
                "parity-regime-edge": "Parity Regime Edge",
            }
            strat_name = strat_names.get(strat_id, strat_id.replace("-", " ").title())
        self._lbl_strategy_name.setText(strat_name)

        # Balance
        bal_minor = (active_card.balance_minor_units or 0) if active_card is not None else 0
        raw_curr = active_card.currency if active_card is not None else None
        bal_curr = (raw_curr or "USD").upper()
        self._lbl_balance_label.setText(f"{t('overview.balance')} ({mode_text})")
        self._lbl_balance_val.setText(format_minor_units(bal_minor, bal_curr))
        self._lbl_updated_time.setText(t("overview.updated_at", time=now_str))

        # Bot status & Primary action button
        bot_running = snapshot.deriv_bot_armed or snapshot.iqoption_bot_armed
        self._bot_running = bot_running

        target_btn_obj = "danger" if bot_running else "primary"
        if bot_running:
            self._lbl_bot_status_val.setText(t("bot.running"))
            self._lbl_bot_status_val.setStyleSheet(
                f"font-size: 16px; font-weight: 700; color: {ACCENT_GREEN};"
            )
            self._lbl_bot_hint.setText(t("bot.running_hint"))
            self._lbl_bot_icon.setPixmap(icon("icon-clock", ACCENT_GREEN, 16).pixmap(16, 16))

            self.primary_action_btn.setText(
                t("action.stop_bot", broker=self._active_broker.upper())
            )
            self.primary_action_btn.setIcon(icon("icon-stop", ACCENT_RED, 16))
        elif snapshot.global_state == UiGlobalState.SAFE_STOPPED:
            self._lbl_bot_status_val.setText(t("bot.stopped"))
            self._lbl_bot_status_val.setStyleSheet(
                f"font-size: 16px; font-weight: 700; color: {TEXT_MUTED};"
            )
            self._lbl_bot_hint.setText(t("bot.stopped_hint"))
            self._lbl_bot_icon.setPixmap(icon("icon-clock", TEXT_MUTED, 16).pixmap(16, 16))

            self.primary_action_btn.setText(
                t("action.start_bot", broker=self._active_broker.upper())
            )
            self.primary_action_btn.setIcon(icon("icon-play", "#0A0F14", 16))
        elif snapshot.iqoption_entry_blocker or not connected:
            self._lbl_bot_status_val.setText(t("bot.error"))
            self._lbl_bot_status_val.setStyleSheet(
                f"font-size: 16px; font-weight: 700; color: {ACCENT_RED};"
            )
            self._lbl_bot_hint.setText(t("bot.error_hint"))
            self._lbl_bot_icon.setPixmap(icon("icon-clock", ACCENT_RED, 16).pixmap(16, 16))

            self.primary_action_btn.setText(
                t("action.start_bot", broker=self._active_broker.upper())
            )
            self.primary_action_btn.setIcon(icon("icon-play", "#0A0F14", 16))
        else:
            self._lbl_bot_status_val.setText(t("bot.idle"))
            self._lbl_bot_status_val.setStyleSheet(
                f"font-size: 16px; font-weight: 700; color: {ACCENT_AMBER};"
            )
            self._lbl_bot_hint.setText(t("bot.idle_hint"))
            self._lbl_bot_icon.setPixmap(icon("icon-clock", ACCENT_AMBER, 16).pixmap(16, 16))

            self.primary_action_btn.setText(
                t("action.start_bot", broker=self._active_broker.upper())
            )
            self.primary_action_btn.setIcon(icon("icon-play", "#0A0F14", 16))

        if self.primary_action_btn.objectName() != target_btn_obj:
            self.primary_action_btn.setObjectName(target_btn_obj)
            self.primary_action_btn.style().unpolish(self.primary_action_btn)
            self.primary_action_btn.style().polish(self.primary_action_btn)
        self.primary_action_btn.setEnabled(connected)

        # Dual Broker Cards Reference
        d_card = next((c for c in snapshot.broker_cards if c.broker.upper() == "DERIV"), None)
        i_card = next((c for c in snapshot.broker_cards if "IQ" in c.broker.upper()), None)

        d_trades = d_card.total_trades if d_card else 0
        i_trades = i_card.total_trades if i_card else 0
        d_wins = d_card.wins if d_card else 0
        i_wins = i_card.wins if i_card else 0
        d_losses = d_card.losses if d_card else 0
        i_losses = i_card.losses if i_card else 0

        # 4 KPIs
        db_trades = d_trades + i_trades
        if db_trades > 0:
            total_trades = db_trades
            wins = d_wins + i_wins
            losses = d_losses + i_losses
        else:
            settled = [
                o
                for o in snapshot.active_orders
                if o.state == "SETTLED" and o.realized_pnl_minor_units is not None
            ]
            total_trades = len(settled)
            wins = sum((o.realized_pnl_minor_units or 0) > 0 for o in settled)
            losses = sum((o.realized_pnl_minor_units or 0) < 0 for o in settled)

        decided = wins + losses
        win_rate = (wins * 100.0 / decided) if decided else 0.0
        loss_rate = (losses * 100.0 / decided) if decided else 0.0

        pnl_val = snapshot.daily_pnl_minor_units
        pnl_curr = (snapshot.daily_pnl_currency or "USD").upper()

        self._kpi_trades.set_data(
            value=str(total_trades),
            delta=t("kpi.today_delta", value=f"+{total_trades}"),
        )
        self._kpi_wins.set_data(
            value=str(wins),
            gauge_percent=win_rate,
            gauge_color=ACCENT_GREEN,
        )
        self._kpi_losses.set_data(
            value=str(losses),
            gauge_percent=loss_rate,
            gauge_color=ACCENT_RED,
        )

        if pnl_val >= 0:
            formatted_pnl = format_minor_units(pnl_val, pnl_curr, positive_sign=True)
            pnl_color = ACCENT_GREEN
        else:
            formatted_pnl = format_minor_units(pnl_val, pnl_curr)
            pnl_color = ACCENT_RED

        self._kpi_profit.set_data(
            value=formatted_pnl,
            value_color=pnl_color,
            delta=t("kpi.today_delta", value=formatted_pnl),
            delta_color=pnl_color,
        )

        # Deriv Card Update
        if d_card is not None and d_card.is_connected:
            self._lbl_deriv_conn_badge.setText(t("broker.connected"))
            self._lbl_deriv_conn_badge.setStyleSheet(
                f"background-color: rgba(31, 181, 122, 0.15); color: {ACCENT_GREEN}; "
                f"border-radius: 4px; padding: 2px 8px; font-size: 11px; font-weight: 700;"
            )
            d_curr = (d_card.currency or "USD").upper()
            self._lbl_deriv_bal_val.setText(
                format_minor_units(d_card.balance_minor_units or 0, d_curr)
            )
            is_d_real = d_card.account_mode.value == "REAL"
            mode_color = ACCENT_RED if is_d_real else ACCENT_AMBER
            self._lbl_deriv_mode_badge.setText(t("mode.REAL") if is_d_real else t("mode.practice"))
            _set_style_if_changed(
                self._lbl_deriv_mode_badge,
                f"border: 1px solid {mode_color}; color: {mode_color}; "
                f"border-radius: 4px; padding: 2px 8px; font-size: 11px; font-weight: 700;",
            )
        else:
            self._lbl_deriv_conn_badge.setText(t("broker.disconnected"))
            _set_style_if_changed(
                self._lbl_deriv_conn_badge,
                f"background-color: rgba(229, 72, 77, 0.15); color: {ACCENT_RED}; "
                f"border-radius: 4px; padding: 2px 8px; font-size: 11px; font-weight: 700;",
            )
            self._lbl_deriv_bal_val.setText("--")

        deriv_open_orders = [
            o
            for o in snapshot.active_orders
            if o.broker.upper() == "DERIV" and o.state != "SETTLED"
        ]
        deriv_exposure = sum(o.amount_minor_units for o in deriv_open_orders)
        self._lbl_deriv_exp_val.setText(
            f"{len(deriv_open_orders)} · {format_minor_units(deriv_exposure, 'USD')}"
        )

        # Deriv Real PnL (Lucro Líquido Real)
        d_pnl = d_card.realized_pnl_minor_units if d_card is not None else 0
        d_pnl_curr = (d_card.currency if d_card and d_card.currency else "USD").upper()
        if d_pnl >= 0:
            self._lbl_deriv_pnl_val.setText(
                format_minor_units(d_pnl, d_pnl_curr, positive_sign=True)
            )
            _set_style_if_changed(
                self._lbl_deriv_pnl_val,
                (
                    f"font-size: 14px; font-weight: 700; "
                    f"color: {ACCENT_GREEN}; font-family: {FONT_MONO};"
                ),
            )
        else:
            self._lbl_deriv_pnl_val.setText(format_minor_units(d_pnl, d_pnl_curr))
            _set_style_if_changed(
                self._lbl_deriv_pnl_val,
                (
                    f"font-size: 14px; font-weight: 700; "
                    f"color: {ACCENT_RED}; font-family: {FONT_MONO};"
                ),
            )

        # Deriv Dedicated Trading Statistics
        self._lbl_deriv_trades_val.setText(str(d_trades))
        self._lbl_deriv_wins_val.setText(str(d_wins))
        self._lbl_deriv_losses_val.setText(str(d_losses))
        d_decided = d_wins + d_losses
        d_winrate = (d_wins * 100.0 / d_decided) if d_decided > 0 else 0.0
        self._lbl_deriv_winrate_val.setText(f"{d_winrate:.1f}%")

        if snapshot.deriv_bot_armed:
            self._lbl_deriv_op_status.setText(t("operational.bot_armed_waiting"))
            _set_style_if_changed(
                self._lbl_deriv_op_status,
                f"font-size: 14px; font-weight: 700; color: {ACCENT_GREEN};",
            )
            self._lbl_deriv_op_hint.setText(snapshot.deriv_bot_reason or t("bot.running_hint"))
            self._btn_deriv_action.update_label(t("overview.deriv_bot_action_stop"), "icon-stop")
            self._btn_deriv_action._variant = "danger"
            self._btn_deriv_action._apply_styling()
        else:
            self._lbl_deriv_op_status.setText(t("operational.connected_bot_off"))
            _set_style_if_changed(
                self._lbl_deriv_op_status,
                f"font-size: 14px; font-weight: 700; color: {TEXT_PRIMARY};",
            )
            self._lbl_deriv_op_hint.setText(
                snapshot.deriv_bot_reason or t("operational.bot_paused")
            )
            self._btn_deriv_action.update_label(t("overview.deriv_bot_action_start"), "icon-play")
            self._btn_deriv_action._variant = "primary"
            self._btn_deriv_action._apply_styling()

        # IQ Option Card Update
        if i_card is not None and i_card.is_connected:
            self._lbl_iq_conn_badge.setText(t("broker.connected"))
            _set_style_if_changed(
                self._lbl_iq_conn_badge,
                f"background-color: rgba(31, 181, 122, 0.15); color: {ACCENT_GREEN}; "
                f"border-radius: 4px; padding: 2px 8px; font-size: 11px; font-weight: 700;",
            )
            i_curr = (i_card.currency or "USD").upper()
            self._lbl_iq_bal_val.setText(
                format_minor_units(i_card.balance_minor_units or 0, i_curr)
            )
            is_i_real = i_card.account_mode.value == "REAL"
            iq_mode_color = ACCENT_RED if is_i_real else ACCENT_AMBER
            self._lbl_iq_mode_badge.setText(t("mode.REAL") if is_i_real else t("mode.practice"))
            _set_style_if_changed(
                self._lbl_iq_mode_badge,
                f"border: 1px solid {iq_mode_color}; color: {iq_mode_color}; "
                f"border-radius: 4px; padding: 2px 8px; font-size: 11px; font-weight: 700;",
            )
        else:
            self._lbl_iq_conn_badge.setText(t("broker.disconnected"))
            _set_style_if_changed(
                self._lbl_iq_conn_badge,
                f"background-color: rgba(229, 72, 77, 0.15); color: {ACCENT_RED}; "
                f"border-radius: 4px; padding: 2px 8px; font-size: 11px; font-weight: 700;",
            )
            self._lbl_iq_bal_val.setText("--")

        iq_open_orders = [
            o for o in snapshot.active_orders if "IQ" in o.broker.upper() and o.state != "SETTLED"
        ]
        iq_exposure = sum(o.amount_minor_units for o in iq_open_orders)
        self._lbl_iq_exp_val.setText(
            f"{len(iq_open_orders)} · {format_minor_units(iq_exposure, 'USD')}"
        )

        # IQ Option Real PnL (Lucro Líquido Real)
        i_pnl = i_card.realized_pnl_minor_units if i_card is not None else 0
        i_pnl_curr = (i_card.currency if i_card and i_card.currency else "USD").upper()
        if i_pnl >= 0:
            self._lbl_iq_pnl_val.setText(format_minor_units(i_pnl, i_pnl_curr, positive_sign=True))
            _set_style_if_changed(
                self._lbl_iq_pnl_val,
                (
                    f"font-size: 14px; font-weight: 700; "
                    f"color: {ACCENT_GREEN}; font-family: {FONT_MONO};"
                ),
            )
        else:
            self._lbl_iq_pnl_val.setText(format_minor_units(i_pnl, i_pnl_curr))
            _set_style_if_changed(
                self._lbl_iq_pnl_val,
                (
                    f"font-size: 14px; font-weight: 700; "
                    f"color: {ACCENT_RED}; font-family: {FONT_MONO};"
                ),
            )

        # IQ Option Dedicated Trading Statistics
        if i_trades > 0:
            self._lbl_iq_trades_val.setText(str(i_trades))
            self._lbl_iq_wins_val.setText(str(i_wins))
            self._lbl_iq_losses_val.setText(str(i_losses))
            i_decided = i_wins + i_losses
            i_winrate = (i_wins * 100.0 / i_decided) if i_decided > 0 else 0.0
            self._lbl_iq_winrate_val.setText(f"{i_winrate:.1f}%")
            self._lbl_iq_winrate_val.setToolTip(f"Total Wins: {i_wins} | Loss Gale 2: {i_losses}")
            self._lbl_iq_wins_val.setToolTip(f"Total Wins: {i_wins}")
            self._lbl_iq_losses_val.setToolTip(f"Loss Gale 2: {i_losses}")
        else:
            iq_all_orders = [o for o in snapshot.active_orders if "IQ" in o.broker.upper()]
            iq_m_stats = calculate_martingale_cycle_stats(iq_all_orders)
            if iq_m_stats.total_cycles > 0:
                self._lbl_iq_trades_val.setText(str(iq_m_stats.total_cycles))
                self._lbl_iq_wins_val.setText(str(iq_m_stats.total_wins))
                self._lbl_iq_losses_val.setText(str(iq_m_stats.losses_g2))
                self._lbl_iq_winrate_val.setText(f"{iq_m_stats.win_rate:.1f}%")
                self._lbl_iq_winrate_val.setToolTip(
                    f"G0: {iq_m_stats.wins_sem_gale} | G1: {iq_m_stats.wins_g1} | "
                    f"G2: {iq_m_stats.wins_g2} | Loss G2: {iq_m_stats.losses_g2}"
                )
                self._lbl_iq_wins_val.setToolTip(
                    f"Sem Gale: {iq_m_stats.wins_sem_gale} | "
                    f"Gale 1: {iq_m_stats.wins_g1} | "
                    f"Gale 2: {iq_m_stats.wins_g2}"
                )
                self._lbl_iq_losses_val.setToolTip(f"Loss Gale 2: {iq_m_stats.losses_g2}")
            else:
                self._lbl_iq_trades_val.setText("0")
                self._lbl_iq_wins_val.setText("0")
                self._lbl_iq_losses_val.setText("0")
                self._lbl_iq_winrate_val.setText("0.0%")
            if iq_m_stats.total_cycles > 0:
                self._lbl_iq_winrate_val.setToolTip(
                    f"G0: {iq_m_stats.wins_sem_gale} | G1: {iq_m_stats.wins_g1} | "
                    f"G2: {iq_m_stats.wins_g2} | Loss G2: {iq_m_stats.losses_g2}"
                )
                self._lbl_iq_wins_val.setToolTip(
                    f"Sem Gale: {iq_m_stats.wins_sem_gale} | "
                    f"Gale 1: {iq_m_stats.wins_g1} | "
                    f"Gale 2: {iq_m_stats.wins_g2}"
                )
                self._lbl_iq_losses_val.setToolTip(f"Loss Gale 2: {iq_m_stats.losses_g2}")
            else:
                self._lbl_iq_winrate_val.setToolTip(
                    f"Total Wins: {i_wins} | Loss Gale 2: {i_losses}"
                )
                self._lbl_iq_wins_val.setToolTip(f"Total Wins: {i_wins}")
                self._lbl_iq_losses_val.setToolTip(f"Loss Gale 2: {i_losses}")

        if snapshot.iqoption_bot_armed:
            self._lbl_iq_op_status.setText(t("operational.bot_armed_waiting"))
            self._lbl_iq_op_status.setStyleSheet(
                f"font-size: 14px; font-weight: 700; color: {ACCENT_GREEN};"
            )
            self._lbl_iq_op_hint.setText(snapshot.iqoption_bot_reason or t("bot.running_hint"))
            self._btn_iq_action.update_label(t("overview.iq_bot_action_stop"), "icon-stop")
            self._btn_iq_action._variant = "danger"
            self._btn_iq_action._apply_styling()
        else:
            self._lbl_iq_op_status.setText(t("operational.connected_bot_off"))
            self._lbl_iq_op_status.setStyleSheet(
                f"font-size: 14px; font-weight: 700; color: {TEXT_PRIMARY};"
            )
            self._lbl_iq_op_hint.setText(
                snapshot.iqoption_bot_reason or t("operational.bot_paused")
            )
            self._btn_iq_action.update_label(t("overview.iq_bot_action_start"), "icon-play")
            self._btn_iq_action._variant = "primary"
            self._btn_iq_action._apply_styling()

        if snapshot.iqoption_risk_config is not None and hasattr(self, "_lbl_iq_strat_info"):
            strat_id = snapshot.iqoption_risk_config.strategy_id
            strat_names = {
                "iqoption-hack-chino": "Hack Chino · 5 Modelos (M1 · Exp 1m)",
                "iqoption-liquidity-gap": "HFT Liquidity Gap (M1 · Exp 2m)",
                "iqoption-pattern-reversal": "HFT Pattern Reversal (M1 · Exp 1m)",
                "iqoption-extreme-rejection": "Varredura e Rejeição de Extremo (M1 · Exp 1m)",
                "iqoption-microtrend-scalper": "Microtendência 3 Velas (M1 · Exp 1m)",
                "AUTO": "Radar Multi-Ativos (AUTO)",
            }
            s_name = strat_names.get(strat_id, strat_id)
            self._lbl_iq_strat_info.setText(f"Bot: {s_name}")

        # Update Consolidated Active Orders
        all_open_orders = [o for o in snapshot.active_orders if o.state != "SETTLED"]
        orders_sig = tuple(
            (o.order_id, o.state, o.amount_minor_units, o.direction, o.symbol)
            for o in all_open_orders
        )
        if self._last_orders_sig != orders_sig:
            self._last_orders_sig = orders_sig
            self._lbl_orders_count.setText(
                t("overview.active_orders_count", count=len(all_open_orders))
            )
            if all_open_orders:
                self._lbl_no_orders.hide()
                self._table_active_orders.show()
                self._table_active_orders.setUpdatesEnabled(False)
                try:
                    self._table_active_orders.setRowCount(len(all_open_orders))
                    for row_idx, ord_item in enumerate(all_open_orders):
                        b_item = QTableWidgetItem(ord_item.broker)
                        b_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)

                        sym_item = QTableWidgetItem(ord_item.symbol)
                        sym_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)

                        dir_item = QTableWidgetItem(ord_item.direction)
                        dir_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                        dir_item.setForeground(
                            QColor(ACCENT_GREEN if ord_item.direction == "CALL" else ACCENT_RED)
                        )

                        stake_str = format_minor_units(
                            ord_item.amount_minor_units, ord_item.currency
                        )
                        stk_item = QTableWidgetItem(stake_str)
                        stk_item.setTextAlignment(
                            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight
                        )

                        time_str = ord_item.created_at_utc.strftime("%H:%M:%S")
                        tm_item = QTableWidgetItem(time_str)
                        tm_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)

                        state_item = QTableWidgetItem(ord_item.state)
                        state_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)

                        self._table_active_orders.setItem(row_idx, 0, b_item)
                        self._table_active_orders.setItem(row_idx, 1, sym_item)
                        self._table_active_orders.setItem(row_idx, 2, dir_item)
                        self._table_active_orders.setItem(row_idx, 3, stk_item)
                        self._table_active_orders.setItem(row_idx, 4, tm_item)
                        self._table_active_orders.setItem(row_idx, 5, state_item)
                finally:
                    self._table_active_orders.setUpdatesEnabled(True)
            else:
                self._lbl_no_orders.show()
                self._table_active_orders.hide()

        # Radar
        if snapshot.iqoption_asset_ranking != self._raw_ranking:
            self._raw_ranking = snapshot.iqoption_asset_ranking
            self._on_radar_filter_changed()

    def retranslate(self) -> None:
        self._last_snapshot = None
        self._last_snapshot_sig = None
        self._last_connected = None
        self._last_radar_sig = None
        self._last_orders_sig = None
        self._lbl_strategy_label.setText(t("overview.active_strategy"))
        self._btn_configure.setText(t("overview.configure"))
        self._lbl_core_state_label.setText(t("overview.state"))
        self._lbl_bot_state_label.setText(t("overview.bot_state"))
        self._lbl_bot_status_val.setToolTip(t("bot.state_tip"))

        mode_text = t("mode.REAL") if self._is_real_mode else t("mode.practice")
        mode_tip = t("mode.real_tip") if self._is_real_mode else t("mode.practice_tip")
        self._lbl_mode_chip.setText(mode_text)
        self._lbl_mode_chip.setToolTip(mode_tip)
        self._lbl_balance_label.setText(f"{t('overview.balance')} ({mode_text})")

        self._kpi_trades.set_data(title=t("kpi.total_trades"), tooltip=t("kpi.total_trades_tip"))
        self._kpi_wins.set_data(title=t("kpi.wins"), tooltip=t("kpi.wins_tip"))
        self._kpi_losses.set_data(title=t("kpi.losses"), tooltip=t("kpi.losses_tip"))
        self._kpi_profit.set_data(title=t("kpi.net_profit"), tooltip=t("kpi.net_profit_tip"))

        self._radar_title.setText(t("radar.title"))
        self._radar_subtitle.setText(t("radar.subtitle"))
        self._search_input.setPlaceholderText(t("radar.search_placeholder"))
        self._empty_label.setText(t("radar.empty"))

        # Dual cards and Orders
        self._lbl_deriv_card_title.setText(t("overview.deriv_panel_title"))
        self._lbl_iq_card_title.setText(t("overview.iq_panel_title"))
        self._lbl_orders_title.setText(t("overview.open_orders_title"))
        self._lbl_no_orders.setText(t("overview.no_open_orders"))
        self._table_active_orders.setHorizontalHeaderLabels(
            [
                t("overview.col_broker"),
                t("overview.col_asset"),
                t("overview.col_direction"),
                t("overview.col_stake"),
                t("overview.col_opened_at"),
                t("overview.col_status"),
            ]
        )

        # Combo items
        current_idx = self._filter_combo.currentIndex()
        self._filter_combo.blockSignals(True)
        self._filter_combo.clear()
        self._filter_combo.addItem(t("radar.filter_all"))
        self._filter_combo.addItem(t("radar.filter_forex"))
        self._filter_combo.addItem(t("radar.filter_otc"))
        self._filter_combo.setCurrentIndex(current_idx)
        self._filter_combo.blockSignals(False)

        # Table headers
        headers = [
            t("radar.col_rank"),
            t("radar.col_asset"),
            t("radar.col_price"),
            t("radar.col_rsi"),
            t("radar.col_signal"),
            t("radar.col_status"),
            t("radar.col_updated"),
        ]
        self._radar_table.setHorizontalHeaderLabels(headers)
        rsi_hdr = self._radar_table.horizontalHeaderItem(3)
        if rsi_hdr is not None:
            rsi_hdr.setToolTip(t("radar.rsi_tip"))

        # Primary action button
        if self._bot_running:
            self.primary_action_btn.setText(
                t("action.stop_bot", broker=self._active_broker.upper())
            )
        else:
            self.primary_action_btn.setText(
                t("action.start_bot", broker=self._active_broker.upper())
            )

        # Broker Cards statistics labels
        self._lbl_deriv_pnl_title.setText(t("overview.net_profit_label"))
        self._lbl_deriv_trades_title.setText(t("overview.stats_trades"))
        self._lbl_deriv_wins_title.setText(t("overview.stats_wins"))
        self._lbl_deriv_losses_title.setText(t("overview.stats_losses"))
        self._lbl_deriv_winrate_title.setText(t("overview.stats_winrate"))

        self._lbl_iq_pnl_title.setText(t("overview.net_profit_label"))
        self._lbl_iq_trades_title.setText(t("overview.stats_trades"))
        self._lbl_iq_wins_title.setText(t("overview.stats_wins"))
        self._lbl_iq_losses_title.setText(t("overview.stats_losses"))
        self._lbl_iq_winrate_title.setText(t("overview.stats_winrate"))

        self._on_radar_filter_changed()

    def set_compact_mode(self, is_compact: bool) -> None:
        """Adapts padding and sizing when the main window is minimized / reduced in size."""
        if is_compact:
            self._content_layout.setContentsMargins(12, 10, 12, 10)
            self._content_layout.setSpacing(10)
            self._hero.setMinimumHeight(105)
            self._hero.setMaximumHeight(120)
        else:
            self._content_layout.setContentsMargins(24, 20, 24, 20)
            self._content_layout.setSpacing(16)
            self._hero.setMinimumHeight(125)
            self._hero.setMaximumHeight(140)


__all__ = ["OverviewPage"]
