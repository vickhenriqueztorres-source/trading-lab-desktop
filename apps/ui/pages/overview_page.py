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

from apps.ui.components.kpi_card import KpiCard
from apps.ui.design.icons import icon
from apps.ui.formatting import format_minor_units
from apps.ui.i18n import t
from apps.ui.theme import (
    ACCENT_AMBER,
    ACCENT_GREEN,
    ACCENT_PRIMARY,
    ACCENT_RED,
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


class OverviewPage(QWidget):
    """Institutional overview page following Trading Lab Design System v2."""

    configure_clicked = Signal()
    bot_toggle_clicked = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._raw_ranking: tuple[UiIqOptionAssetRank, ...] = ()
        self._active_broker = "Deriv"
        self._is_real_mode = False
        self._bot_running = False
        self._last_snapshot_time: str = "--:--:--"

        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(24, 20, 24, 20)
        content_layout.setSpacing(16)

        # 1. HeroCard (height ~130px, 4 columns separated by 1px vertical borders)
        self._hero = self._build_hero_card()
        content_layout.addWidget(self._hero)

        # 2. Row of 4 KpiCards
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

        content_layout.addLayout(kpis_layout)

        # 3. RadarCard (Market radar with search, filter, and 7-col table)
        self._radar_card = self._build_radar_card()
        content_layout.addWidget(self._radar_card, 1)

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

        self._lbl_strategy_name = QLabel("Digit Differs Edge")
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
            return

        self._empty_label.hide()
        self._radar_table.show()
        self._radar_table.setRowCount(len(filtered))

        for row, item in enumerate(filtered):
            # Col 0: Rank #
            rank_item = QTableWidgetItem(str(row + 1))
            rank_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            rank_item.setForeground(QColor(TEXT_MUTED))

            # Col 1: Asset name
            asset_item = QTableWidgetItem(item.display_name)
            font = asset_item.font()
            font.setBold(True)
            asset_item.setFont(font)
            asset_item.setTextAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
            asset_item.setToolTip(item.candidate_details or item.symbol)

            # Col 2: Price
            price_item = QTableWidgetItem("--")
            price_item.setTextAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight)
            price_item.setForeground(QColor(TEXT_MUTED))

            # Col 3: RSI (14)
            rsi_val = 50.0
            try:
                rsi_val = float(item.rsi) if item.rsi != "--" else 50.0
            except ValueError:
                rsi_val = 50.0

            rsi_item = QTableWidgetItem(f"{item.rsi}")
            rsi_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            rsi_item.setToolTip(t("radar.rsi_tip"))

            if item.rsi == "--":
                rsi_item.setForeground(QColor(TEXT_MUTED))
            elif rsi_val <= 30.0:
                rsi_item.setForeground(QColor(ACCENT_GREEN))
            elif rsi_val >= 70.0:
                rsi_item.setForeground(QColor(ACCENT_RED))
            else:
                rsi_item.setForeground(QColor(ACCENT_PRIMARY))

            # Col 4: Signal
            if item.direction == "CALL" or (item.rsi != "--" and rsi_val <= 30.0):
                sig_text = f"● {t('signal.call')}"
                sig_color = ACCENT_GREEN
            elif item.direction == "PUT" or (item.rsi != "--" and rsi_val >= 70.0):
                sig_text = f"● {t('signal.put')}"
                sig_color = ACCENT_RED
            else:
                sig_text = f"● {t('signal.none')}"
                sig_color = TEXT_MUTED

            sig_item = QTableWidgetItem(sig_text)
            sig_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            sig_item.setForeground(QColor(sig_color))

            # Col 5: Status
            if item.selected:
                st_text = "EM FOCO"
                st_color = ACCENT_PRIMARY
            elif item.status == "TRIGGERED":
                st_text = "SINAL OBSERVADO"
                st_color = ACCENT_AMBER
            elif item.status == "WARMING_UP":
                st_text = "AQUECENDO"
                st_color = ACCENT_AMBER
            elif item.status in {"DISCOVERY_ONLY", "WAITING_DATA"}:
                st_text = item.status
                st_color = TEXT_MUTED
            else:
                st_text = t("radar.monitoring")
                st_color = TEXT_MUTED

            st_item = QTableWidgetItem(st_text)
            st_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            st_item.setForeground(QColor(st_color))

            # Col 6: Updated
            upd_item = QTableWidgetItem(self._last_snapshot_time)
            upd_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            upd_item.setForeground(QColor(TEXT_MUTED))

            self._radar_table.setItem(row, 0, rank_item)
            self._radar_table.setItem(row, 1, asset_item)
            self._radar_table.setItem(row, 2, price_item)
            self._radar_table.setItem(row, 3, rsi_item)
            self._radar_table.setItem(row, 4, sig_item)
            self._radar_table.setItem(row, 5, st_item)
            self._radar_table.setItem(row, 6, upd_item)

    def update_projection(self, snapshot: UiProjectionSnapshot | None, controller: Any) -> None:
        connected = bool(controller.connected)
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
        iq_card = next((c for c in snapshot.broker_cards if c.broker == "IQOPTION"), None)

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
            self.primary_action_btn.setObjectName("danger")
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
            self.primary_action_btn.setObjectName("primary")
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
            self.primary_action_btn.setObjectName("primary")
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
            self.primary_action_btn.setObjectName("primary")
            self.primary_action_btn.setIcon(icon("icon-play", "#0A0F14", 16))

        self.primary_action_btn.setEnabled(connected)
        self.primary_action_btn.style().unpolish(self.primary_action_btn)
        self.primary_action_btn.style().polish(self.primary_action_btn)

        # 4 KPIs
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

        # Radar
        self._raw_ranking = snapshot.iqoption_asset_ranking
        self._on_radar_filter_changed()

    def retranslate(self) -> None:
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

        self._on_radar_filter_changed()


__all__ = ["OverviewPage"]
