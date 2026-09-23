"""First-class IQ Option Multi-Asset Workspace and Strategy Command Center."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from apps.ui.components.iqoption_asset_radar import IqOptionAssetRadarWidget
from apps.ui.components.iqoption_strategy_summary import IqOptionStrategySummaryWidget
from apps.ui.components.manual_review_panel import ManualReviewPanel
from apps.ui.components.order_table import OrderTableView
from apps.ui.components.safe_stop_button import SafeStopButton
from apps.ui.formatting import format_minor_units
from apps.ui.i18n import t
from apps.ui.theme import ACCENT_AMBER, ACCENT_GREEN
from packages.protocol.ui_messages import (
    BrokerCardStatus,
    OrderSummary,
    UiAccountMode,
    UiBalanceQuality,
    UiIqOptionAssetRank,
    UiIqOptionExecutionMetrics,
    UiIqOptionRiskConfig,
)


def _mode_text(mode: UiAccountMode) -> str:
    translated = t(f"mode.{mode.value}")
    return mode.value if translated.startswith("mode.") else translated


def iqoption_bot_reason_text(reason: str) -> str:
    messages = {
        "IQOPTION_BOT_READY_FOR_CAPABILITY_CHECK": t("iq.reason.ready_for_capability_check"),
        "MD_CLOCK_UNTRUSTED": t("iq.reason.clock_untrusted"),
        "TRANSPORT_DOWN": t("iq.reason.transport_down"),
        "IQOPTION_BALANCE_STALE": t("iq.reason.balance_stale"),
        "IQOPTION_BOT_DISARMED": t("iq.reason.disarmed"),
        "IQOPTION_BOT_ARMED_RECONCILING": t("iq.reconciliation.automatic"),
        "IQOPTION_BOT_ARMED_REVIEW_REQUIRED": t("iq.reconciliation.inconclusive"),
        "HG_ORDER_UNKNOWN": t("iq.reconciliation.automatic"),
        "HG_RECONCILIATION_REQUIRED": t("iq.reconciliation.automatic"),
        "HG_RECONCILIATION_UNAVAILABLE": t("iq.reconciliation.automatic"),
        "HG_SETTLEMENT_UNKNOWN": t("iq.reconciliation.automatic"),
        "IQOPTION_ALL_MARKETS_CLOSED": t("iq.reason.all_markets_closed"),
        "IQOPTION_MARKET_CLOSED": t("iq.reason.market_closed"),
        "IQOPTION_SYMBOL_UNSUPPORTED": t("iq.reason.symbol_unsupported"),
        "IQOPTION_ACTIVE_SUSPENDED": t("iq.reason.asset_suspended"),
        "IQOPTION_ACTIVE_UNAVAILABLE": t("iq.reason.asset_unavailable"),
    }
    return messages.get(reason, reason)


class IqOptionWorkspaceWidget(QWidget):
    """First-class dedicated workspace for IQ Option RSI Multi-Asset trading."""

    iqoption_login_requested = Signal()
    safe_stop_requested = Signal()
    iqoption_bot_toggle_requested = Signal()

    @property
    def tabs(self) -> QTabWidget:
        return self._tabs

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.broker_key = "IQOPTION"
        self._display_name = "IQ Option"
        self._last_status: BrokerCardStatus | None = None
        self._orders: tuple[OrderSummary, ...] = ()
        self._bot_armed = False
        self._bot_reason = "IQOPTION_BOT_DISARMED"
        self._reconnect_remaining_seconds = 0
        self._reconnect_attempts = 0
        self._reconnect_timer = QTimer(self)
        self._reconnect_timer.setInterval(1_000)
        self._reconnect_timer.timeout.connect(self._tick_reconnect_countdown)

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 14, 18, 14)
        root.setSpacing(12)

        # Page Header (Title + Subtitle) - hidden to maximize vertical space
        self._page_title = QLabel(t("page.iqoption"))
        self._page_title.setObjectName("sectionTitle")
        self._page_title.setVisible(False)
        self._page_subtitle = QLabel(t("page.iqoption_subtitle"))
        self._page_subtitle.setObjectName("hint")
        self._page_subtitle.setVisible(False)

        # Card 1: Connection & Account Header (Compact 54px Status Toolbar)
        root.addWidget(self._build_account_header())

        # Main Workspace Tabs
        self._tabs = QTabWidget()
        self._tabs.setObjectName("IqOptionTabs")
        self._tabs.setDocumentMode(True)

        # Tab 1: Estado & Radar ao Vivo (Strategy Card)
        self._tabs.addTab(self._build_live_page(), "📊 " + t("tabs.status"))

        # Tab 2: Configuração de Risco & Parâmetros (Risk Card)
        self._configuration_layout = QVBoxLayout()
        self._configuration_layout.setContentsMargins(14, 14, 14, 14)
        self._configuration_layout.setSpacing(12)
        self._configuration_page = self._build_config_page()
        self._tabs.addTab(self._configuration_page, "⚙️ " + t("tabs.configuration"))
        self._config_widget: Any | None = None
        self._previous_tab_index: int = 0
        self._tabs.currentChanged.connect(self._on_tab_changed)

        root.addWidget(self._tabs, 1)
        self.retranslate()

    def _build_account_header(self) -> QFrame:
        frame = QFrame()
        frame.setObjectName("DerivHero")
        frame.setFixedHeight(54)
        bar = QHBoxLayout(frame)
        bar.setContentsMargins(16, 8, 16, 8)
        bar.setSpacing(12)
        bar.setAlignment(Qt.AlignmentFlag.AlignVCenter)

        # 1. Connection status pill & Clock latency
        conn_box = QHBoxLayout()
        conn_box.setSpacing(6)
        conn_box.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        self._connection_pill = QLabel()
        self._connection_pill.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._connection_pill.setObjectName("StatusPillOnline")
        self._connection_pill.setMinimumWidth(105)
        self._connection_pill.setFixedHeight(24)
        conn_box.addWidget(self._connection_pill)
        self._clock_status = QLabel("—")
        self._clock_status.setObjectName("Subtitle")
        self._clock_status.setStyleSheet("font-size: 11px; color: #64748B;")
        conn_box.addWidget(self._clock_status)
        bar.addLayout(conn_box)

        # Divider 1
        sep1 = QFrame()
        sep1.setFrameShape(QFrame.Shape.VLine)
        sep1.setStyleSheet("color: #334155;")
        bar.addWidget(sep1)

        # 2. Account Mode Pill
        self._account_mode = QLabel("—")
        self._account_mode.setObjectName("ValueMono")
        self._account_mode.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._account_mode.setStyleSheet(
            "font-size: 11px; font-weight: 700; color: #F59E0B; "
            "background: rgba(245, 158, 11, 0.12); border: 1px solid rgba(245, 158, 11, 0.3); "
            "border-radius: 4px; padding: 2px 8px;"
        )
        bar.addWidget(self._account_mode)

        # Divider 2
        sep2 = QFrame()
        sep2.setFrameShape(QFrame.Shape.VLine)
        sep2.setStyleSheet("color: #334155;")
        bar.addWidget(sep2)

        # 3. Balance Section
        bal_box = QHBoxLayout()
        bal_box.setSpacing(6)
        bal_box.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        self._balance_caption = QLabel(t("broker.balance") + ":")
        self._balance_caption.setObjectName("Subtitle")
        self._balance_caption.setStyleSheet(
            "font-size: 11px; font-weight: 700; color: #64748B; text-transform: uppercase;"
        )
        bal_box.addWidget(self._balance_caption)
        self._balance_value = QLabel("—")
        self._balance_value.setObjectName("ValueMono")
        self._balance_value.setStyleSheet(
            f"color: {ACCENT_GREEN}; font-size: 15px; font-weight: 800;"
        )
        bal_box.addWidget(self._balance_value)
        self._balance_freshness = QLabel(t("iq.balance.awaiting"))
        self._balance_freshness.setObjectName("Subtitle")
        self._balance_freshness.setStyleSheet("font-size: 10px; color: #64748B;")
        bal_box.addWidget(self._balance_freshness)
        bar.addLayout(bal_box)

        # Divider 3
        sep3 = QFrame()
        sep3.setFrameShape(QFrame.Shape.VLine)
        sep3.setStyleSheet("color: #334155;")
        bar.addWidget(sep3)

        # 4. Automation Pill & Detail
        auto_box = QHBoxLayout()
        auto_box.setSpacing(8)
        auto_box.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        self._automation_pill = QLabel(t("iq.status.active"))
        self._automation_pill.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._automation_pill.setObjectName("StatusPillOnline")
        self._automation_pill.setMinimumWidth(130)
        self._automation_pill.setFixedHeight(26)
        auto_box.addWidget(self._automation_pill)

        self._btn_bot_toggle = QPushButton(t("btn.bot.light_start"))
        self._btn_bot_toggle.setObjectName("LightBotToggle")
        self._btn_bot_toggle.setFixedHeight(26)
        self._btn_bot_toggle.setStyleSheet(
            "QPushButton { background: rgba(31, 181, 122, 0.12); color: #1FB57A; "
            "border: 1px solid #1FB57A; border-radius: 4px; padding: 2px 10px; "
            "font-size: 11px; font-weight: 700; } "
            "QPushButton:hover { background: rgba(31, 181, 122, 0.25); }"
        )
        self._btn_bot_toggle.clicked.connect(self.iqoption_bot_toggle_requested.emit)
        auto_box.addWidget(self._btn_bot_toggle)

        self._automation_detail = QLabel(t("iq.workspace.auto_scan_desc"))
        self._automation_detail.setObjectName("Subtitle")
        self._automation_detail.setStyleSheet("font-size: 11px; color: #94A3B8;")
        auto_box.addWidget(self._automation_detail)
        bar.addLayout(auto_box)

        bar.addStretch(1)

        # 5. Safe Stop Button
        self._safe_stop_button = SafeStopButton()
        self._safe_stop_button.setObjectName("danger")
        self._safe_stop_button.setFixedHeight(30)
        self._safe_stop_button.setText(t("btn.safe_stop_short"))
        self._safe_stop_button.setToolTip(t("btn.safe_stop"))
        self._safe_stop_button.setStyleSheet(
            "font-size: 10px; font-weight: 700; padding: 2px 10px;"
        )
        self._safe_stop_button.safe_stop_triggered.connect(self.safe_stop_requested.emit)
        bar.addWidget(self._safe_stop_button)

        # Kept for compatibility / headless checks (hidden)
        self._card_conn_title = QLabel(t("card.connection"))
        self._card_conn_title.setVisible(False)
        self._card_conn_hint = QLabel(t("card.connection_hint"))
        self._card_conn_hint.setVisible(False)
        self._eyebrow = QLabel(t("iq.workspace.hero_title"))
        self._eyebrow.setVisible(False)
        self._title = QLabel(t("iq.workspace.hero_subtitle"))
        self._title.setVisible(False)
        self._description = QLabel(t("iq.workspace.hero_desc"))
        self._description.setVisible(False)
        self._account_caption = QLabel(t("deriv.hub.account"))
        self._account_caption.setVisible(False)

        return frame

    def _build_live_page(self) -> QScrollArea:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)

        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(12)

        # Strategy Card Header
        strat_header = QHBoxLayout()
        self._card_strat_title = QLabel(t("card.strategy"))
        self._card_strat_title.setObjectName("sectionTitle")
        self._card_strat_title.setStyleSheet("font-size: 14px; font-weight: 600;")
        strat_header.addWidget(self._card_strat_title)
        self._card_strat_hint = QLabel(t("card.strategy_hint"))
        self._card_strat_hint.setObjectName("hint")
        strat_header.addWidget(self._card_strat_hint)
        strat_header.addStretch()
        layout.addLayout(strat_header)

        # 1. Strategy Summary & KPIs
        self.strategy_summary = IqOptionStrategySummaryWidget()
        layout.addWidget(self.strategy_summary)

        # 2. Multi-Asset Live Radar Table
        self.asset_radar = IqOptionAssetRadarWidget()
        layout.addWidget(self.asset_radar)

        # 3. Manual Review Panel (appears when orders require manual review)
        self.manual_review_panel = ManualReviewPanel()
        layout.addWidget(self.manual_review_panel)

        # 4. Orders Table
        self._orders_header = QLabel(t("orders.title") + " · IQ OPTION")
        self._orders_header.setObjectName("Title")
        layout.addWidget(self._orders_header)

        self.orders = OrderTableView()
        self.orders.setMinimumHeight(180)
        layout.addWidget(self.orders, 1)

        scroll.setWidget(content)
        return scroll

    def _build_config_page(self) -> QScrollArea:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)

        content = QWidget()
        self._config_content_layout = QVBoxLayout(content)
        self._config_content_layout.setContentsMargins(14, 14, 14, 14)
        self._config_content_layout.setSpacing(12)

        # Risk Card Header
        risk_header = QHBoxLayout()
        self._card_risk_title = QLabel(t("card.risk"))
        self._card_risk_title.setObjectName("sectionTitle")
        self._card_risk_title.setStyleSheet("font-size: 14px; font-weight: 600;")
        risk_header.addWidget(self._card_risk_title)
        self._card_risk_hint = QLabel(t("card.risk_hint"))
        self._card_risk_hint.setObjectName("hint")
        risk_header.addWidget(self._card_risk_hint)
        risk_header.addStretch()
        self._config_content_layout.addLayout(risk_header)

        # Login / Account Selector Box
        self._login_box = QFrame()
        self._login_box.setObjectName("Surface")
        l_layout = QVBoxLayout(self._login_box)
        l_layout.setContentsMargins(16, 14, 16, 14)
        l_layout.setSpacing(10)

        self._l_title = QLabel(t("iq_option.login.title"))
        self._l_title.setObjectName("Title")
        l_layout.addWidget(self._l_title)

        self._l_desc = QLabel(t("iq.workspace.login_box_desc"))
        self._l_desc.setWordWrap(True)
        self._l_desc.setObjectName("Subtitle")
        l_layout.addWidget(self._l_desc)

        self._iqoption_login_button = QPushButton("🔑 " + t("iq_option.login.button"))
        self._iqoption_login_button.setObjectName("PrimaryButton")
        self._iqoption_login_button.clicked.connect(self.iqoption_login_requested.emit)
        l_layout.addWidget(self._iqoption_login_button)

        self._iqoption_login_status = QLabel()
        self._iqoption_login_status.setWordWrap(True)
        self._iqoption_login_status.setObjectName("Subtitle")
        l_layout.addWidget(self._iqoption_login_status)

        self._config_content_layout.addWidget(self._login_box)
        self._config_content_layout.addStretch()

        scroll.setWidget(content)
        return scroll

    def add_configuration_widget(self, widget: QWidget) -> None:
        self._config_widget = widget
        if hasattr(widget, "dirty_state_changed"):
            widget.dirty_state_changed.connect(self._on_config_dirty_changed)
        stretch_index = self._config_content_layout.count() - 1
        self._config_content_layout.insertWidget(stretch_index, widget)

    def _on_config_dirty_changed(self, is_dirty: bool) -> None:
        if is_dirty:
            self._tabs.setTabText(1, "⚙️ " + t("tabs.configuration") + t("iq.risk.tab_pending"))
            self._tabs.tabBar().setTabTextColor(1, QColor("#F59E0B"))
        else:
            self._tabs.setTabText(1, "⚙️ " + t("tabs.configuration"))
            self._tabs.tabBar().setTabTextColor(1, QColor("#94A3B8"))

    def _on_tab_changed(self, new_index: int) -> None:
        if (
            self._previous_tab_index == 1
            and new_index != 1
            and self._config_widget is not None
            and hasattr(self._config_widget, "has_unsaved_changes")
            and self._config_widget.has_unsaved_changes()
        ):
            msg = QMessageBox(self)
            msg.setWindowTitle(t("iq.risk.dialog_title"))
            msg.setText(t("iq.risk.dialog_message"))
            msg.setIcon(QMessageBox.Icon.Warning)
            btn_save = msg.addButton(t("iq.risk.dialog_save"), QMessageBox.ButtonRole.AcceptRole)
            btn_discard = msg.addButton(
                t("iq.risk.dialog_discard"), QMessageBox.ButtonRole.DestructiveRole
            )
            msg.addButton(t("iq.risk.dialog_cancel"), QMessageBox.ButtonRole.RejectRole)
            msg.setDefaultButton(btn_save)
            msg.exec()
            clicked = msg.clickedButton()
            if clicked == btn_save:
                if hasattr(self._config_widget, "save_changes"):
                    self._config_widget.save_changes()
            elif clicked == btn_discard:
                if hasattr(self._config_widget, "discard_unsaved_changes"):
                    self._config_widget.discard_unsaved_changes()
            else:
                self._tabs.blockSignals(True)
                self._tabs.setCurrentIndex(1)
                self._tabs.blockSignals(False)
                return
        self._previous_tab_index = self._tabs.currentIndex()

    def update_status(self, status: BrokerCardStatus) -> None:
        if status.broker != self.broker_key:
            return
        self._last_status = status

        if status.is_connected:
            self._login_box.setVisible(False)
            self._connection_pill.setText(f"● {t('broker.connected')}")
            self._connection_pill.setObjectName("StatusPillOnline")
        else:
            self._login_box.setVisible(True)
            self._connection_pill.setText(f"○ {t('broker.disconnected')}")
            self._connection_pill.setObjectName("StatusPillOffline")
            if not self._iqoption_login_status.text():
                self._iqoption_login_status.setText(t("broker.disconnected_hint"))

        self._connection_pill.style().unpolish(self._connection_pill)
        self._connection_pill.style().polish(self._connection_pill)

        self._account_mode.setText(_mode_text(status.account_mode))
        if status.balance_minor_units is not None and status.currency is not None:
            self._balance_value.setText(
                format_minor_units(status.balance_minor_units, status.currency)
            )
        else:
            self._balance_value.setText("—")
        observed = status.balance_observed_at_utc
        observed_text = "" if observed is None else f"{observed:%H:%M:%S} UTC"
        age = status.balance_age_seconds
        age_text = "" if age is None else f" ({age}s)"
        retries = status.balance_retry_count or 0
        quality = status.balance_quality
        if quality is UiBalanceQuality.CONFIRMED or (
            quality is None and status.balance_is_fresh is True
        ):
            self._balance_freshness.setText(f"{t('iq.balance.confirmed')}{age_text}")
            self._balance_freshness.setStyleSheet(f"color: {ACCENT_GREEN};")
        elif quality is UiBalanceQuality.RETRYING:
            attempt_text = "" if retries <= 0 else f" · #{retries}"
            self._balance_freshness.setText(
                f"{t('iq.balance.last_confirmed')}{age_text}{attempt_text}"
            )
            self._balance_freshness.setStyleSheet(f"color: {ACCENT_AMBER};")
        elif quality is UiBalanceQuality.STALE or status.balance_is_fresh is False:
            self._balance_freshness.setText(f"{t('iq.balance.unconfirmed')}{age_text}")
            self._balance_freshness.setStyleSheet(f"color: {ACCENT_AMBER};")
        else:
            self._balance_freshness.setText(t("iq.balance.awaiting"))
            self._balance_freshness.setStyleSheet("")
        if observed_text:
            self._balance_freshness.setToolTip(f"IQ Option validated timestamp: {observed_text}")
        else:
            self._balance_freshness.setToolTip("")

        if status.clock_synced:
            lat = f" ({status.clock_latency_ms} ms)" if status.clock_latency_ms else ""
            self._clock_status.setText(f"⏱️ {t('broker.clock_synced')}{lat}")
        else:
            latency = status.clock_latency_ms
            detail = f" · {latency} ms" if latency is not None else ""
            self._clock_status.setText(f"⏱️ {t('broker.clock_untrusted')}{detail}")

    def update_bot_state(
        self,
        armed: bool,
        reason: str,
        *,
        entry_ready: bool | None = None,
        entry_blocker: str | None = None,
    ) -> None:
        self._bot_armed = armed
        self._bot_reason = reason
        if armed and entry_ready is not False:
            self._automation_pill.setText(t("iq.status.active"))
            self._automation_pill.setObjectName("StatusPillOnline")
        elif armed:
            market_reason = entry_blocker or reason
            if reason == "IQOPTION_BOT_ARMED_REVIEW_REQUIRED":
                self._automation_pill.setText(t("iq.status.review_required"))
            elif market_reason in {
                "IQOPTION_BOT_ARMED_RECONCILING",
                "HG_ORDER_UNKNOWN",
                "HG_RECONCILIATION_REQUIRED",
                "HG_RECONCILIATION_UNAVAILABLE",
                "HG_SETTLEMENT_UNKNOWN",
            }:
                self._automation_pill.setText(t("iq.status.checking_order"))
            elif reason in {
                "TRANSPORT_DOWN",
                "IQOPTION_CONNECTION_QUARANTINED",
                "IQOPTION_CONNECTION_IN_PROGRESS",
            }:
                self._automation_pill.setText(t("iq.status.reconnecting"))
            else:
                self._automation_pill.setText(t("iq.status.entries_blocked"))
            self._automation_pill.setObjectName("StatusPillOffline")
        else:
            self._automation_pill.setText(t("iq.status.standby"))
            self._automation_pill.setObjectName("StatusPillOffline")

        self._automation_pill.style().unpolish(self._automation_pill)
        self._automation_pill.style().polish(self._automation_pill)

        if hasattr(self, "_btn_bot_toggle"):
            if armed:
                self._btn_bot_toggle.setText(t("btn.bot.light_stop"))
                self._btn_bot_toggle.setStyleSheet(
                    "QPushButton { background: rgba(229, 72, 77, 0.12); color: #E5484D; "
                    "border: 1px solid #E5484D; border-radius: 4px; padding: 2px 10px; "
                    "font-size: 11px; font-weight: 700; } "
                    "QPushButton:hover { background: rgba(229, 72, 77, 0.25); }"
                )
            else:
                self._btn_bot_toggle.setText(t("btn.bot.light_start"))
                self._btn_bot_toggle.setStyleSheet(
                    "QPushButton { background: rgba(31, 181, 122, 0.12); color: #1FB57A; "
                    "border: 1px solid #1FB57A; border-radius: 4px; padding: 2px 10px; "
                    "font-size: 11px; font-weight: 700; } "
                    "QPushButton:hover { background: rgba(31, 181, 122, 0.25); }"
                )
        display_reason = (
            reason if reason == "IQOPTION_BOT_ARMED_REVIEW_REQUIRED" else entry_blocker or reason
        )
        self._automation_detail.setText(
            t("iq.status.entries_blocked_prefix", reason=iqoption_bot_reason_text(display_reason))
            if armed and entry_ready is False
            else iqoption_bot_reason_text(reason)
        )
        market_reason = entry_blocker or reason
        if market_reason == "IQOPTION_ACTIVE_SUSPENDED":
            self._automation_detail.setText(t("iq.reason.asset_suspended"))
        elif market_reason == "IQOPTION_ACTIVE_UNAVAILABLE":
            self._automation_detail.setText(t("iq.reason.asset_unavailable"))

    def set_controller(self, controller: Any) -> None:
        self.manual_review_panel.set_controller(controller)

    def update_orders(self, orders: Sequence[OrderSummary]) -> None:
        filtered = tuple(item for item in orders if "IQ" in item.broker.upper())
        self.orders.update_orders(filtered)
        self.manual_review_panel.update_orders(filtered)
        if self.strategy_summary is not None:
            self.strategy_summary.update_orders(filtered)

    def update_iqoption_radar(self, ranking: Sequence[UiIqOptionAssetRank]) -> None:
        if self.asset_radar is not None:
            self.asset_radar.update_ranking(ranking)

    def update_iqoption_risk(self, config: UiIqOptionRiskConfig | None) -> None:
        if self.strategy_summary is not None:
            self.strategy_summary.update_config(config)
        if config is not None and hasattr(self, "_automation_detail"):
            names = {
                "iqoption-hack-chino": "Hack Chino (5 Modelos)",
                "iqoption-liquidity-gap": "HFT Liquidity Gap (2m)",
                "iqoption-pattern-reversal": "HFT Pattern Reversal (1m)",
                "iqoption-extreme-rejection": "Varredura e Rejeição de Extremo (1m)",
                "iqoption-microtrend-scalper": "Microtrend Scalper · 3 Velas (1m)",
                "AUTO": "Radar Multi-Ativos (AUTO)",
            }
            name = names.get(config.strategy_id, config.strategy_id)
            asset_str = "Todos os Ativos (AUTO)" if config.symbol == "AUTO" else config.symbol
            stake_str = f"USD {config.stake_minor_units / 100:.2f}"
            self._automation_detail.setText(
                f"Bot Ativo: {name} · Mercado: {asset_str} · Entrada: {stake_str}"
            )

    def update_iqoption_metrics(self, metrics: UiIqOptionExecutionMetrics | None) -> None:
        if self.strategy_summary is not None:
            self.strategy_summary.update_metrics(metrics)

    def set_iqoption_login_busy(self, busy: bool, message: str | None = None) -> None:
        if self._iqoption_login_button is not None:
            self._iqoption_login_button.setEnabled(not busy)
        if message is not None and self._iqoption_login_status is not None:
            self._iqoption_login_status.setText(message)

    def set_iqoption_login_status(self, message: str) -> None:
        self._reconnect_timer.stop()
        self._reconnect_remaining_seconds = 0
        if self._iqoption_login_button is not None:
            self._iqoption_login_button.setText("🔑 " + t("iq_option.login.button"))
        self._iqoption_login_status.setText(message)

    def set_iqoption_reconnect_wait(self, seconds: int, attempts: int) -> None:
        self._reconnect_remaining_seconds = max(0, seconds)
        self._reconnect_attempts = max(0, attempts)
        if self._iqoption_login_button is not None:
            self._iqoption_login_button.setText(t("iq.reconnect.button_now"))
        self._render_reconnect_countdown()
        if self._reconnect_remaining_seconds > 0:
            self._reconnect_timer.start()

    def _tick_reconnect_countdown(self) -> None:
        self._reconnect_remaining_seconds = max(0, self._reconnect_remaining_seconds - 1)
        self._render_reconnect_countdown()
        if self._reconnect_remaining_seconds == 0:
            self._reconnect_timer.stop()

    def _render_reconnect_countdown(self) -> None:
        minutes, seconds = divmod(self._reconnect_remaining_seconds, 60)
        time_str = f"{minutes:02d}:{seconds:02d}"
        self._iqoption_login_status.setText(
            t("iq.reconnect.countdown", time=time_str, attempts=self._reconnect_attempts)
        )

    def tab_label(self) -> str:
        if self._last_status is None:
            return self._display_name
        return f"{self._display_name} — {_mode_text(self._last_status.account_mode)}"

    def retranslate(self) -> None:
        self._page_title.setText(t("page.iqoption"))
        self._page_subtitle.setText(t("page.iqoption_subtitle"))
        self._card_conn_title.setText(t("card.connection"))
        self._card_conn_hint.setText(t("card.connection_hint"))
        self._card_strat_title.setText(t("card.strategy"))
        self._card_strat_hint.setText(t("card.strategy_hint"))
        self._card_risk_title.setText(t("card.risk"))
        self._card_risk_hint.setText(t("card.risk_hint"))
        self._account_caption.setText(t("deriv.hub.account"))
        self._balance_caption.setText(t("broker.balance"))
        self._orders_header.setText(t("orders.title") + " · IQ OPTION")
        self._tabs.setTabText(0, "📊 " + t("tabs.status"))
        dirty = False
        if self._config_widget is not None and hasattr(self._config_widget, "has_unsaved_changes"):
            dirty = self._config_widget.has_unsaved_changes()
        self._on_config_dirty_changed(dirty)
        self._safe_stop_button.retranslate()
        self._safe_stop_button.setText(t("btn.safe_stop_short"))
        self._safe_stop_button.setToolTip(t("btn.safe_stop"))
        if self._iqoption_login_button is not None:
            self._iqoption_login_button.setText("🔑 " + t("iq_option.login.button"))
        if self._iqoption_login_status is not None and not self._iqoption_login_status.text():
            self._iqoption_login_status.setText(t("iq_option.login.status"))
        self.orders.retranslate()
        if self.asset_radar is not None:
            self.asset_radar.retranslate()


__all__ = ["IqOptionWorkspaceWidget"]
