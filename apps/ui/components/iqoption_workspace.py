"""First-class IQ Option Multi-Asset Workspace and Strategy Command Center."""

from __future__ import annotations

from collections.abc import Sequence

from PySide6.QtCore import Qt, Signal
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
from apps.ui.formatting import format_minor_units
from apps.ui.i18n import t
from apps.ui.theme import ACCENT_CYAN, ACCENT_GREEN
from packages.protocol.ui_messages import (
    BrokerCardStatus,
    OrderSummary,
    UiAccountMode,
    UiIqOptionAssetRank,
    UiIqOptionRiskConfig,
)


def _mode_text(mode: UiAccountMode) -> str:
    translated = t(f"mode.{mode.value}")
    return mode.value if translated.startswith("mode.") else translated


class IqOptionWorkspaceWidget(QWidget):
    """First-class dedicated workspace for IQ Option RSI Multi-Asset trading."""

    iqoption_login_requested = Signal()

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

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 14, 18, 14)
        root.setSpacing(12)

        # 1. Top Account Hero Header
        root.addWidget(self._build_account_header())

        # 2. Main Workspace Tabs
        self._tabs = QTabWidget()
        self._tabs.setObjectName("IqOptionTabs")
        self._tabs.setDocumentMode(True)

        # Tab 1: Estado & Radar ao Vivo
        self._tabs.addTab(self._build_live_page(), "📊 " + t("tabs.status"))

        # Tab 2: Configuração de Risco & Parâmetros
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

        layout = QHBoxLayout()
        layout.setSpacing(14)

        # Title / Description
        identity = QVBoxLayout()
        identity.setSpacing(2)
        self._eyebrow = QLabel("IQ OPTION · LABORATÓRIO MULTI-ATIVOS")
        self._eyebrow.setObjectName("Eyebrow")
        identity.addWidget(self._eyebrow)

        self._title = QLabel("RSI 14 Bounded Edge · M1")
        self._title.setObjectName("HeroTitle")
        identity.addWidget(self._title)

        self._description = QLabel(
            "Varredura simultânea de todos os ativos OTC e Forex com execução instantânea."
        )
        self._description.setWordWrap(True)
        self._description.setObjectName("Subtitle")
        identity.addWidget(self._description)
        layout.addLayout(identity, 3)

        # Connection Pill
        self._connection_pill = QLabel("● CONECTADO")
        self._connection_pill.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._connection_pill.setObjectName("StatusPillOnline")
        layout.addWidget(self._connection_pill)

        # Account Mode
        account = QVBoxLayout()
        account.setSpacing(3)
        self._account_caption = QLabel("MODO DA CONTA")
        self._account_caption.setObjectName("Subtitle")
        account.addWidget(self._account_caption)
        self._account_mode = QLabel("PRACTICE (TREINAMENTO)")
        self._account_mode.setObjectName("ValueMono")
        account.addWidget(self._account_mode)
        self._clock_status = QLabel("⏱️ Sincronizado")
        self._clock_status.setObjectName("Subtitle")
        account.addWidget(self._clock_status)
        layout.addLayout(account, 2)

        # Balance Section
        balance = QVBoxLayout()
        balance.setSpacing(3)
        self._balance_caption = QLabel("SALDO DISPONÍVEL")
        self._balance_caption.setObjectName("Subtitle")
        balance.addWidget(self._balance_caption)
        self._balance_value = QLabel("$ 10,000.00 USD")
        self._balance_value.setObjectName("ValueMono")
        self._balance_value.setStyleSheet(f"color: {ACCENT_GREEN}; font-size: 16px;")
        balance.addWidget(self._balance_value)
        layout.addLayout(balance, 2)

        # Bot Automation Pill & Reason
        bot_box = QVBoxLayout()
        bot_box.setSpacing(3)
        self._automation_pill = QLabel("AUTO TRADER: PRONTO")
        self._automation_pill.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._automation_pill.setObjectName("StatusPillOnline")
        bot_box.addWidget(self._automation_pill)

        self._automation_detail = QLabel("AUTO SCAN: Monitorando 15 ativos (RSI < 30 / > 70)")
        self._automation_detail.setObjectName("Subtitle")
        self._automation_detail.setWordWrap(True)
        self._automation_detail.setStyleSheet(f"color: {ACCENT_CYAN}; font-size: 11px;")
        bot_box.addWidget(self._automation_detail)
        layout.addLayout(bot_box, 3)

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

        # 1. Strategy Summary & KPIs
        self.strategy_summary = IqOptionStrategySummaryWidget()
        layout.addWidget(self.strategy_summary)

        # 2. Multi-Asset Live Radar Table
        self.asset_radar = IqOptionAssetRadarWidget()
        layout.addWidget(self.asset_radar)

        # 3. Orders Table
        orders_header = QLabel("HISTÓRICO DE ORDENS · IQ OPTION")
        orders_header.setObjectName("Title")
        layout.addWidget(orders_header)

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

        # Login / Account Selector Box
        login_box = QFrame()
        login_box.setObjectName("Surface")
        l_layout = QVBoxLayout(login_box)
        l_layout.setContentsMargins(16, 14, 16, 14)
        l_layout.setSpacing(10)

        l_title = QLabel("CONEXÃO & CONTA IQ OPTION")
        l_title.setObjectName("Title")
        l_layout.addWidget(l_title)

        l_desc = QLabel(
            "Conecte-se com segurança à conta de Treinamento (Practice) ou Real. "
            "Suas credenciais são protegidas via cofre DPAPI do Windows."
        )
        l_desc.setWordWrap(True)
        l_desc.setObjectName("Subtitle")
        l_layout.addWidget(l_desc)

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
            self._connection_pill.setText("● CONECTADO")
            self._connection_pill.setObjectName("StatusPillOnline")
        else:
            self._connection_pill.setText("○ DESCONECTADO")
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

        if status.clock_synced:
            lat = f" ({status.clock_latency_ms} ms)" if status.clock_latency_ms else ""
            self._clock_status.setText(f"⏱️ Sincronizado{lat}")
        else:
            self._clock_status.setText("⏱️ Aguardando Sync")

    def update_bot_state(self, armed: bool, reason: str) -> None:
        self._bot_armed = armed
        self._bot_reason = reason
        if armed:
            self._automation_pill.setText("● BOT ATIVO")
            self._automation_pill.setObjectName("StatusPillOnline")
        else:
            self._automation_pill.setText("○ BOT EM ESPERA")
            self._automation_pill.setObjectName("StatusPillOffline")

        self._automation_pill.style().unpolish(self._automation_pill)
        self._automation_pill.style().polish(self._automation_pill)
        self._automation_detail.setText(reason)

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

    def set_iqoption_login_busy(self, busy: bool, message: str | None = None) -> None:
        if self._iqoption_login_button is not None:
            self._iqoption_login_button.setEnabled(not busy)
        if message is not None and self._iqoption_login_status is not None:
            self._iqoption_login_status.setText(message)

    def set_iqoption_login_status(self, message: str) -> None:
        self._iqoption_login_status.setText(message)

    def tab_label(self) -> str:
        if self._last_status is None:
            return self._display_name
        return f"{self._display_name} — {_mode_text(self._last_status.account_mode)}"

    def retranslate(self) -> None:
        self._tabs.setTabText(0, "📊 " + t("tabs.status"))
        self._tabs.setTabText(1, "⚙️ " + t("tabs.configuration"))
        if self._iqoption_login_button is not None:
            self._iqoption_login_button.setText("🔑 " + t("iq_option.login.button"))
        if self._iqoption_login_status is not None and not self._iqoption_login_status.text():
            self._iqoption_login_status.setText(t("iq_option.login.status"))
        self.orders.retranslate()


__all__ = ["IqOptionWorkspaceWidget"]
