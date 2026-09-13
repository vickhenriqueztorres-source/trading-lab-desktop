"""First-class IQ Option Multi-Asset Workspace and Strategy Command Center."""

from __future__ import annotations

from collections.abc import Sequence

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from apps.ui.components.iqoption_asset_radar import IqOptionAssetRadarWidget
from apps.ui.components.iqoption_strategy_summary import IqOptionStrategySummaryWidget
from apps.ui.components.order_table import OrderTableView
from apps.ui.components.safe_stop_button import SafeStopButton
from apps.ui.formatting import format_minor_units
from apps.ui.i18n import t
from apps.ui.theme import ACCENT_AMBER, ACCENT_CYAN, ACCENT_GREEN
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

        # Page Header (Title + Subtitle)
        header = QVBoxLayout()
        header.setSpacing(4)
        self._page_title = QLabel(t("page.iqoption"))
        self._page_title.setObjectName("sectionTitle")
        self._page_title.setStyleSheet("font-size: 20px; font-weight: 700;")
        header.addWidget(self._page_title)
        self._page_subtitle = QLabel(t("page.iqoption_subtitle"))
        self._page_subtitle.setObjectName("hint")
        header.addWidget(self._page_subtitle)
        root.addLayout(header)

        # Card 1: Connection & Account Header
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

        root.addWidget(self._tabs, 1)
        self.retranslate()

    def _build_account_header(self) -> QFrame:
        frame = QFrame()
        frame.setObjectName("DerivHero")
        outer = QVBoxLayout(frame)
        outer.setContentsMargins(18, 12, 18, 12)
        outer.setSpacing(8)

        card_title_row = QHBoxLayout()
        card_title_row.setSpacing(8)
        self._card_conn_title = QLabel(t("card.connection"))
        self._card_conn_title.setObjectName("sectionTitle")
        self._card_conn_title.setStyleSheet("font-size: 14px; font-weight: 600;")
        card_title_row.addWidget(self._card_conn_title)
        self._card_conn_hint = QLabel(t("card.connection_hint"))
        self._card_conn_hint.setObjectName("hint")
        card_title_row.addWidget(self._card_conn_hint)
        card_title_row.addStretch()
        outer.addLayout(card_title_row)

        layout = QHBoxLayout()
        layout.setSpacing(14)

        # Title / Description
        identity = QVBoxLayout()
        identity.setSpacing(2)
        self._eyebrow = QLabel("IQ OPTION · MULTI-ASSET RADAR")
        self._eyebrow.setObjectName("Eyebrow")
        identity.addWidget(self._eyebrow)

        self._title = QLabel("RSI 14 Bounded Edge · M1")
        self._title.setObjectName("HeroTitle")
        identity.addWidget(self._title)

        self._description = QLabel(
            "Catálogo dinámico Binary/Digital, mercados regulares y OTC; "
            "ejecución instantánea protegida por capa stealth anti-detección."
        )
        self._description.setWordWrap(True)
        self._description.setObjectName("Subtitle")
        identity.addWidget(self._description)
        layout.addLayout(identity, 3)

        # Connection Pill
        self._connection_pill = QLabel()
        self._connection_pill.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._connection_pill.setObjectName("StatusPillOnline")
        layout.addWidget(self._connection_pill)

        # Account Mode
        account = QVBoxLayout()
        account.setSpacing(3)
        self._account_caption = QLabel(t("deriv.hub.account"))
        self._account_caption.setObjectName("Subtitle")
        account.addWidget(self._account_caption)
        self._account_mode = QLabel("—")
        self._account_mode.setObjectName("ValueMono")
        account.addWidget(self._account_mode)
        self._clock_status = QLabel("—")
        self._clock_status.setObjectName("Subtitle")
        account.addWidget(self._clock_status)
        layout.addLayout(account, 2)

        # Balance Section
        balance = QVBoxLayout()
        balance.setSpacing(3)
        self._balance_caption = QLabel(t("broker.balance"))
        self._balance_caption.setObjectName("Subtitle")
        balance.addWidget(self._balance_caption)
        self._balance_value = QLabel("—")
        self._balance_value.setObjectName("ValueMono")
        self._balance_value.setStyleSheet(f"color: {ACCENT_GREEN}; font-size: 16px;")
        balance.addWidget(self._balance_value)
        self._balance_freshness = QLabel(t("iq.balance.awaiting"))
        self._balance_freshness.setObjectName("Subtitle")
        balance.addWidget(self._balance_freshness)
        layout.addLayout(balance, 2)

        # Bot Automation Pill & Reason
        bot_box = QVBoxLayout()
        bot_box.setSpacing(3)
        self._automation_pill = QLabel(t("iq.status.active"))
        self._automation_pill.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._automation_pill.setObjectName("StatusPillOnline")
        bot_box.addWidget(self._automation_pill)

        self._automation_detail = QLabel(
            "AUTO SCAN: Monitor 15 OTC & Forex pairs (RSI < 30 / > 70)"
        )
        self._automation_detail.setObjectName("Subtitle")
        self._automation_detail.setWordWrap(True)
        self._automation_detail.setStyleSheet(f"color: {ACCENT_CYAN}; font-size: 11px;")
        bot_box.addWidget(self._automation_detail)
        layout.addLayout(bot_box, 3)

        # SafeStop button
        self._safe_stop_button = SafeStopButton()
        self._safe_stop_button.setObjectName("danger")
        self._safe_stop_button.safe_stop_triggered.connect(self.safe_stop_requested.emit)
        layout.addWidget(self._safe_stop_button)

        outer.addLayout(layout)
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

        # 3. Orders Table
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
        login_box = QFrame()
        login_box.setObjectName("Surface")
        l_layout = QVBoxLayout(login_box)
        l_layout.setContentsMargins(16, 14, 16, 14)
        l_layout.setSpacing(10)

        self._l_title = QLabel(t("iq_option.login.title"))
        self._l_title.setObjectName("Title")
        l_layout.addWidget(self._l_title)

        self._l_desc = QLabel(
            "Conéctate con seguridad a la cuenta de Entrenamiento (Practice) o Real. "
            "Tus credenciales están protegidas mediante el cofre DPAPI de Windows."
        )
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

        self._config_content_layout.addWidget(login_box)
        self._config_content_layout.addStretch()

        scroll.setWidget(content)
        return scroll

    def add_configuration_widget(self, widget: QWidget) -> None:
        stretch_index = self._config_content_layout.count() - 1
        self._config_content_layout.insertWidget(stretch_index, widget)

    def update_status(self, status: BrokerCardStatus) -> None:
        if status.broker != self.broker_key:
            return
        self._last_status = status

        if status.is_connected:
            self._connection_pill.setText(f"● {t('broker.connected')}")
            self._connection_pill.setObjectName("StatusPillOnline")
        else:
            self._connection_pill.setText(f"○ {t('broker.disconnected')}")
            self._connection_pill.setObjectName("StatusPillOffline")

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

    def update_orders(self, orders: Sequence[OrderSummary]) -> None:
        filtered = tuple(item for item in orders if "IQ" in item.broker.upper())
        self.orders.update_orders(filtered)
        if self.strategy_summary is not None:
            self.strategy_summary.update_orders(filtered)

    def update_iqoption_radar(self, ranking: Sequence[UiIqOptionAssetRank]) -> None:
        if self.asset_radar is not None:
            self.asset_radar.update_ranking(ranking)

    def update_iqoption_risk(self, config: UiIqOptionRiskConfig | None) -> None:
        if self.strategy_summary is not None:
            self.strategy_summary.update_config(config)

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
        self._tabs.setTabText(1, "⚙️ " + t("tabs.configuration"))
        self._safe_stop_button.retranslate()
        if self._iqoption_login_button is not None:
            self._iqoption_login_button.setText("🔑 " + t("iq_option.login.button"))
        if self._iqoption_login_status is not None and not self._iqoption_login_status.text():
            self._iqoption_login_status.setText(t("iq_option.login.status"))
        self.orders.retranslate()


__all__ = ["IqOptionWorkspaceWidget"]
