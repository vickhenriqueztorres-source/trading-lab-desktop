from __future__ import annotations

from collections.abc import Sequence

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from apps.ui.components.deriv_strategy_summary import DerivStrategySummaryWidget
from apps.ui.components.order_table import OrderTableView
from apps.ui.formatting import format_minor_units
from apps.ui.i18n import t
from apps.ui.theme import ACCENT_CYAN
from packages.protocol.ui_messages import (
    BrokerCardStatus,
    OrderSummary,
    UiAccountMode,
    UiDerivStrategyStatus,
    UiDigitRiskConfig,
)

_STRATEGIES = {
    "tail-probability-edge": (
        "Tail Probability Edge",
        "Over/Under adaptativo para concentração estatística em dígitos baixos ou altos.",
        "ESTRATÉGIA 1  ·  OVER / UNDER",
    ),
    "selective-differs-edge": (
        "Selective Differs Edge",
        "Digit Differs com seleção conservadora do dígito menos provável.",
        "ESTRATÉGIA 2  ·  DIGIT DIFFERS",
    ),
    "parity-regime-edge": (
        "Parity Regime Edge",
        "Even/Odd condicional para procurar dependência estável de paridade.",
        "ESTRATÉGIA 3  ·  EVEN / ODD",
    ),
}


def _mode_text(mode: UiAccountMode) -> str:
    translated = t(f"mode.{mode.value}")
    return mode.value if translated.startswith("mode.") else translated


class DerivWorkspaceWidget(QWidget):
    """Multi-strategy Deriv command center with isolated strategy workspaces."""

    deriv_demo_connect_requested = Signal()
    strategy_selected = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.broker_key = "DERIV"
        self._last_status: BrokerCardStatus | None = None
        self._orders: tuple[OrderSummary, ...] = ()
        self._selected_strategy_id = "tail-probability-edge"
        self._strategy_statuses: dict[str, UiDerivStrategyStatus] = {}

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 16, 20, 16)
        root.setSpacing(10)
        root.addWidget(self._build_account_header())

        body = QHBoxLayout()
        body.setSpacing(14)
        body.addWidget(self._build_strategy_rail())
        body.addWidget(self._build_strategy_workspace(), 1)
        root.addLayout(body, 1)
        self.retranslate()

    def _build_account_header(self) -> QFrame:
        frame = QFrame()
        frame.setObjectName("DerivHero")
        outer = QVBoxLayout(frame)
        outer.setContentsMargins(18, 10, 18, 10)
        outer.setSpacing(8)
        layout = QHBoxLayout()
        layout.setSpacing(14)

        identity = QVBoxLayout()
        identity.setSpacing(2)
        self._strategy_eyebrow = QLabel()
        self._strategy_eyebrow.setObjectName("Eyebrow")
        identity.addWidget(self._strategy_eyebrow)
        self._strategy_title = QLabel()
        self._strategy_title.setObjectName("HeroTitle")
        identity.addWidget(self._strategy_title)
        self._strategy_description = QLabel()
        self._strategy_description.setWordWrap(True)
        self._strategy_description.setObjectName("Subtitle")
        identity.addWidget(self._strategy_description)
        layout.addLayout(identity, 3)

        self._connection_pill = QLabel()
        self._connection_pill.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._connection_pill.setObjectName("StatusPillOffline")
        layout.addWidget(self._connection_pill)

        account = QVBoxLayout()
        account.setSpacing(3)
        self._account_caption = QLabel()
        self._account_caption.setObjectName("Subtitle")
        account.addWidget(self._account_caption)
        self._account_mode = QLabel("—")
        self._account_mode.setObjectName("ValueMono")
        account.addWidget(self._account_mode)
        self._clock_status = QLabel("—")
        self._clock_status.setObjectName("Subtitle")
        account.addWidget(self._clock_status)
        layout.addLayout(account, 1)

        balance = QVBoxLayout()
        balance.setSpacing(3)
        self._balance_caption = QLabel()
        self._balance_caption.setObjectName("Subtitle")
        balance.addWidget(self._balance_caption)
        self._balance_value = QLabel("—")
        self._balance_value.setObjectName("ValueMono")
        self._balance_value.setStyleSheet(f"color: {ACCENT_CYAN};")
        balance.addWidget(self._balance_value)
        self._deriv_connect_status = QLabel()
        self._deriv_connect_status.setObjectName("Subtitle")
        self._deriv_connect_status.setVisible(False)
        balance.addWidget(self._deriv_connect_status)
        layout.addLayout(balance, 1)

        self._automation_pill = QLabel()
        self._automation_pill.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._automation_pill.setObjectName("StatusPillOffline")
        layout.addWidget(self._automation_pill)

        self._deriv_connect_button = QPushButton()
        self._deriv_connect_button.setObjectName("PrimaryButton")
        self._deriv_connect_button.clicked.connect(self.deriv_demo_connect_requested.emit)
        layout.addWidget(self._deriv_connect_button)
        outer.addLayout(layout)

        self._real_mode_notice = QLabel()
        self._real_mode_notice.setWordWrap(True)
        self._real_mode_notice.setObjectName("SafetyNotice")
        outer.addWidget(self._real_mode_notice)
        return frame

    def _build_strategy_rail(self) -> QFrame:
        rail = QFrame()
        rail.setObjectName("StrategyRail")
        rail.setFixedWidth(238)
        layout = QVBoxLayout(rail)
        layout.setContentsMargins(12, 14, 12, 14)
        layout.setSpacing(6)

        self._library_title = QLabel()
        self._library_title.setObjectName("Title")
        layout.addWidget(self._library_title)
        self._library_body = QLabel()
        self._library_body.setWordWrap(True)
        self._library_body.setObjectName("Subtitle")
        layout.addWidget(self._library_body)

        self._strategy_group = QButtonGroup(self)
        self._strategy_group.setExclusive(True)
        self._strategy_buttons: dict[str, QPushButton] = {}
        for strategy_id, (label, _description, _eyebrow) in _STRATEGIES.items():
            button = QPushButton(label)
            button.setObjectName(
                "StrategyButtonActive"
                if strategy_id == self._selected_strategy_id
                else "StrategyButton"
            )
            button.setCheckable(True)
            button.setMinimumHeight(50)
            button.clicked.connect(
                lambda _checked=False, selected=strategy_id: self._select_strategy(selected)
            )
            self._strategy_group.addButton(button)
            self._strategy_buttons[strategy_id] = button
            layout.addWidget(button)
        self._strategy_buttons[self._selected_strategy_id].setChecked(True)

        layout.addStretch()
        self._portfolio_note = QLabel()
        self._portfolio_note.setWordWrap(True)
        self._portfolio_note.setObjectName("RailNote")
        layout.addWidget(self._portfolio_note)
        return rail

    def _build_strategy_workspace(self) -> QFrame:
        frame = QFrame()
        frame.setObjectName("Card")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(8, 6, 8, 8)
        layout.setSpacing(0)

        self._tabs = QTabWidget()
        self._tabs.setObjectName("StrategyTabs")
        self._tabs.setDocumentMode(True)
        self._tabs.addTab(self._build_summary_page(), "")
        self._tabs.addTab(self._build_widget_host("configuration"), "")
        self._tabs.addTab(self._build_scroll_host("live"), "")
        self._tabs.addTab(self._build_orders_page(), "")
        layout.addWidget(self._tabs, 1)
        return frame

    def _build_summary_page(self) -> QWidget:
        self.results = DerivStrategySummaryWidget()
        return self.results

    def _build_scroll_host(self, key: str) -> QScrollArea:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        content = QWidget()
        host = QVBoxLayout(content)
        host.setContentsMargins(4, 12, 4, 4)
        host.setSpacing(12)
        host.addStretch()
        if key == "configuration":
            self._configuration_host = host
        else:
            self._live_host = host
        scroll.setWidget(content)
        return scroll

    def _build_widget_host(self, key: str) -> QWidget:
        content = QWidget()
        host = QVBoxLayout(content)
        host.setContentsMargins(4, 8, 4, 4)
        host.setSpacing(8)
        host.addStretch()
        if key == "configuration":
            self._configuration_host = host
        return content

    def _build_orders_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(4, 12, 4, 4)
        self.orders = OrderTableView()
        layout.addWidget(self.orders)
        return page

    @property
    def tabs(self) -> QTabWidget:
        return self._tabs

    def set_configuration_widget(self, widget: QWidget) -> None:
        self._configuration_host.insertWidget(self._configuration_host.count() - 1, widget)

    def set_live_widget(self, widget: QWidget) -> None:
        self._live_host.insertWidget(self._live_host.count() - 1, widget)

    def update_status(self, status: BrokerCardStatus) -> None:
        if status.broker != self.broker_key:
            raise ValueError("broker projection does not match Deriv workspace")
        self._last_status = status
        mode = _mode_text(status.account_mode)
        self._account_mode.setText(mode)
        if status.balance_minor_units is not None and status.currency is not None:
            self._balance_value.setText(
                format_minor_units(status.balance_minor_units, status.currency)
            )
        else:
            self._balance_value.setText(t("broker.unavailable"))
        if status.is_connected:
            self._connection_pill.setText(f"● {t('broker.connected')}")
            self._connection_pill.setObjectName("StatusPillOnline")
        else:
            self._connection_pill.setText(f"○ {t('broker.disconnected')}")
            self._connection_pill.setObjectName("StatusPillOffline")
        self._connection_pill.style().unpolish(self._connection_pill)
        self._connection_pill.style().polish(self._connection_pill)
        clock = t("broker.clock_synced") if status.clock_synced else t("broker.clock_untrusted")
        latency = f" · {status.clock_latency_ms} ms" if status.clock_latency_ms is not None else ""
        self._clock_status.setText(f"{clock}{latency}")
        self._real_mode_notice.setText(
            t("config.real_mode_active")
            if status.account_mode is UiAccountMode.REAL
            else t("config.real_mode_available")
        )

    def update_automation_state(
        self,
        enabled: bool,
        connected: bool,
        real_mode: bool,
        reason: str = "",
    ) -> None:
        if real_mode:
            self._automation_pill.setText("○ CONTA REAL SOMENTE LEITURA")
            self._automation_pill.setObjectName("StatusPillOffline")
        elif enabled and connected:
            labels = {
                "BOT_WAITING_FOR_NEW_TICK": "● BOT ATIVO · aguardando sinal novo",
                "BOT_WARMING_UP_TICKS": "● BOT ATIVO · aquecendo dados",
                "BOT_WAITING_FOR_STRATEGY_SIGNAL": "● BOT ATIVO · aguardando sinal",
                "BOT_NO_POSITIVE_NET_EDGE": "● BOT ATIVO · filtro de qualidade",
                "BOT_PERFORMANCE_COOLDOWN": "● BOT ATIVO · pausa temporária de desempenho",
                "BOT_ORDER_IN_FLIGHT": "● BOT ATIVO · operação em andamento",
                "BOT_ORDER_SUBMITTED": "● BOT ATIVO · ordem enviada",
            }
            self._automation_pill.setText(labels.get(reason, "● BOT DEMO ATIVO"))
            self._automation_pill.setObjectName("StatusPillOnline")
        else:
            self._automation_pill.setText("○ BOT DEMO PAUSADO")
            self._automation_pill.setObjectName("StatusPillOffline")
        self._automation_pill.style().unpolish(self._automation_pill)
        self._automation_pill.style().polish(self._automation_pill)
        self._automation_pill.setToolTip(reason)

    def update_orders(self, orders: Sequence[OrderSummary]) -> None:
        self._orders = tuple(item for item in orders if item.broker == self.broker_key)
        self.orders.update_orders(self._orders)
        self.results.update_results(self._orders)

    @property
    def selected_strategy_id(self) -> str:
        return self._selected_strategy_id

    def update_strategy_statuses(self, statuses: Sequence[UiDerivStrategyStatus]) -> None:
        self._strategy_statuses = {item.strategy_id: item for item in statuses}
        for strategy_id, button in self._strategy_buttons.items():
            status = self._strategy_statuses.get(strategy_id)
            if status is None:
                suffix = "AGUARDANDO"
            elif status.signal_state == "SHADOW_SIGNAL":
                suffix = "SINAL DEMO ELEGÍVEL"
            elif status.signal_state == "MONITORING":
                suffix = "MONITORANDO"
            elif status.signal_state == "DATA_BLOCKED":
                suffix = "BLOQUEADA"
            else:
                suffix = "AQUECENDO"
            button.setText(f"{_STRATEGIES[strategy_id][0]}\n{suffix}")

    def _select_strategy(self, strategy_id: str) -> None:
        if strategy_id not in _STRATEGIES:
            return
        self._select_strategy_visual(strategy_id)
        self.strategy_selected.emit(strategy_id)

    def set_execution_strategy(self, strategy_id: str) -> None:
        """Sync a persisted execution strategy without sending a new UI command."""

        if strategy_id not in _STRATEGIES or strategy_id == self._selected_strategy_id:
            return
        self._select_strategy_visual(strategy_id)

    def _select_strategy_visual(self, strategy_id: str) -> None:
        self._selected_strategy_id = strategy_id
        for item_id, button in self._strategy_buttons.items():
            button.setObjectName(
                "StrategyButtonActive" if item_id == strategy_id else "StrategyButton"
            )
            button.setChecked(item_id == strategy_id)
            button.style().unpolish(button)
            button.style().polish(button)
        title, description, eyebrow = _STRATEGIES[strategy_id]
        self._strategy_title.setText(title)
        self._strategy_description.setText(description)
        self._strategy_eyebrow.setText(eyebrow)

    def update_risk(
        self,
        exposure_minor_units: int,
        max_exposure_minor_units: int,
        currency: str | None,
        risk_state: str,
        consecutive_losses: int,
        config: UiDigitRiskConfig | None,
        cooldown_seconds: int,
        martingale_step: int = 0,
        next_stake_minor_units: int = 0,
        projected_sequence_loss_minor_units: int = 0,
    ) -> None:
        self.results.update_risk(
            exposure_minor_units,
            max_exposure_minor_units,
            currency,
            risk_state,
            consecutive_losses,
            config,
            cooldown_seconds,
            martingale_step,
            next_stake_minor_units,
            projected_sequence_loss_minor_units,
        )

    def set_deriv_connect_busy(self, busy: bool, message: str | None = None) -> None:
        self._deriv_connect_button.setEnabled(not busy)
        self._deriv_connect_status.setText(message or t("deriv.connect.status.ready"))

    def tab_label(self) -> str:
        if self._last_status is None:
            return "Deriv"
        return f"Deriv — {_mode_text(self._last_status.account_mode)}"

    def retranslate(self) -> None:
        self._account_caption.setText(t("deriv.hub.account"))
        self._balance_caption.setText(t("broker.balance"))
        self._deriv_connect_button.setText(t("deriv.connect.button"))
        if not self._deriv_connect_status.text():
            self._deriv_connect_status.setText(t("deriv.connect.status.ready"))
        self._library_title.setText(t("deriv.library.title"))
        self._library_body.setText(t("deriv.library.body"))
        self.update_strategy_statuses(tuple(self._strategy_statuses.values()))
        self._portfolio_note.setText(t("deriv.library.note"))
        title, description, eyebrow = _STRATEGIES[self._selected_strategy_id]
        self._strategy_eyebrow.setText(eyebrow)
        self._strategy_title.setText(title)
        self._strategy_description.setText(description)
        self._tabs.setTabText(0, t("deriv.strategy.tabs.overview"))
        self._tabs.setTabText(1, t("deriv.strategy.tabs.parameters"))
        self._tabs.setTabText(2, t("deriv.strategy.tabs.live"))
        self._tabs.setTabText(3, t("deriv.strategy.tabs.operations"))
        if self._last_status is None:
            self._connection_pill.setText(f"○ {t('broker.disconnected')}")
            self._account_mode.setText(t("config.waiting_projection"))
            self._real_mode_notice.setText(t("config.real_mode_available"))
        else:
            self.update_status(self._last_status)
        self.orders.retranslate()
        self.results.retranslate()
