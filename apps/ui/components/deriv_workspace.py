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
from apps.ui.components.safe_stop_button import SafeStopButton
from apps.ui.design.icons import icon
from apps.ui.formatting import format_minor_units
from apps.ui.i18n import t
from apps.ui.theme import ACCENT_CYAN
from packages.protocol.ui_messages import (
    BrokerCardStatus,
    OrderSummary,
    UiAccountMode,
    UiBotWaitingStatus,
    UiDerivStrategyStatus,
    UiDigitRiskConfig,
)

_STRATEGIES: dict[str, tuple[str, str, str, str]] = {
    "tail-probability-edge": (
        "Quantum Prime",
        "Algoritmo cuantitativo con análisis de flujo dinámico y ejecución adaptativa.",
        "Bot 1 · Dígitos",
        "icon-play",
    ),
    "selective-differs-edge": (
        "Nexus Alpha",
        "Modelo probabilístico con filtrado sistemático y protección de capital.",
        "Bot 2 · Differs",
        "icon-shield",
    ),
    "parity-regime-edge": (
        "Titan Vector",
        "Motor algorítmico de micro-regímenes con confirmación multiventana.",
        "Bot 3 · Par/Impar",
        "icon-refresh",
    ),
    "payout-routed-differs-session": (
        "Horizon Shield",
        "Sistema de ejecución institucional con monitoreo y piso de seguridad.",
        "Bot 4 · Reserva",
        "icon-lock",
    ),
}


def _mode_text(mode: UiAccountMode) -> str:
    translated = t(f"mode.{mode.value}")
    return mode.value if translated.startswith("mode.") else translated


class DerivWorkspaceWidget(QWidget):
    """Multi-strategy Deriv command center with isolated strategy workspaces."""

    deriv_demo_connect_requested = Signal()
    strategy_selected = Signal(str)
    safe_stop_requested = Signal()
    bot_toggle_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.broker_key = "DERIV"
        self._last_status: BrokerCardStatus | None = None
        self._orders: tuple[OrderSummary, ...] = ()
        self._selected_strategy_id = "tail-probability-edge"
        self._strategy_statuses: dict[str, UiDerivStrategyStatus] = {}
        self._bot_enabled: bool = False

        # Root layout: clean margins and generous breathing room
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 10, 14, 10)
        root.setSpacing(8)

        # Retain labels for i18n & property compatibility without consuming vertical space
        self._page_title = QLabel(t("page.deriv"))
        self._page_title.setVisible(False)
        self._page_subtitle = QLabel(t("page.deriv_subtitle"))
        self._page_subtitle.setVisible(False)

        # Top Bar: Compact 54px broker status & control strip
        root.addWidget(self._build_account_header())

        # Main Body: Strategy Rail + Full-Height Tabs Workspace
        body = QHBoxLayout()
        body.setSpacing(10)
        body.addWidget(self._build_strategy_rail())
        body.addWidget(self._build_strategy_workspace(), 1)
        root.addLayout(body, 1)
        self.retranslate()

    def _build_account_header(self) -> QFrame:
        frame = QFrame()
        frame.setObjectName("DerivHero")
        frame.setFixedHeight(54)

        layout = QHBoxLayout(frame)
        layout.setContentsMargins(16, 0, 16, 0)
        layout.setSpacing(12)
        layout.setAlignment(Qt.AlignmentFlag.AlignVCenter)

        # 1. Connection Pill & Latency
        self._connection_pill = QLabel()
        self._connection_pill.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._connection_pill.setObjectName("StatusPillOffline")
        self._connection_pill.setMinimumWidth(110)
        self._connection_pill.setFixedHeight(28)
        layout.addWidget(self._connection_pill)

        self._clock_status = QLabel("—")
        self._clock_status.setObjectName("Subtitle")
        self._clock_status.setStyleSheet("font-size: 11px; color: #64748B;")
        layout.addWidget(self._clock_status)

        # Separator
        div1 = QFrame()
        div1.setFrameShape(QFrame.Shape.VLine)
        div1.setStyleSheet("color: #1F2D3A; max-height: 20px;")
        layout.addWidget(div1)

        # 2. Account Mode Badge
        self._account_caption = QLabel(t("deriv.hub.account"))
        self._account_caption.setVisible(False)
        self._account_mode = QLabel("—")
        self._account_mode.setObjectName("ValueMono")
        self._account_mode.setStyleSheet(
            "font-size: 11px; font-weight: 700; color: #F59E0B; "
            "background-color: rgba(245, 158, 11, 0.12); "
            "border: 1px solid rgba(245, 158, 11, 0.3); border-radius: 4px; padding: 3px 8px;"
        )
        layout.addWidget(self._account_mode)

        # Separator
        div2 = QFrame()
        div2.setFrameShape(QFrame.Shape.VLine)
        div2.setStyleSheet("color: #1F2D3A; max-height: 20px;")
        layout.addWidget(div2)

        # 3. Balance
        self._balance_caption = QLabel(t("broker.balance"))
        self._balance_caption.setStyleSheet(
            "font-size: 11px; font-weight: 700; color: #64748B; text-transform: uppercase;"
        )
        layout.addWidget(self._balance_caption)

        self._balance_value = QLabel("—")
        self._balance_value.setObjectName("ValueMono")
        self._balance_value.setStyleSheet(
            f"color: {ACCENT_CYAN}; font-size: 16px; font-weight: 800;"
        )
        layout.addWidget(self._balance_value)

        # Separator
        div3 = QFrame()
        div3.setFrameShape(QFrame.Shape.VLine)
        div3.setStyleSheet("color: #1F2D3A; max-height: 20px;")
        layout.addWidget(div3)

        # 4. Bot Automation Pill & Reason
        self._automation_pill = QLabel()
        self._automation_pill.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._automation_pill.setObjectName("StatusPillOffline")
        self._automation_pill.setMinimumWidth(136)
        self._automation_pill.setFixedHeight(26)
        layout.addWidget(self._automation_pill)

        self._automation_detail = QLabel()
        self._automation_detail.setObjectName("Subtitle")
        self._automation_detail.setStyleSheet("font-size: 11px; color: #94A3B8;")
        self._automation_detail.setMaximumWidth(380)
        layout.addWidget(self._automation_detail)

        layout.addStretch(1)

        self._deriv_connect_status = QLabel()
        self._deriv_connect_status.setObjectName("Subtitle")
        self._deriv_connect_status.setStyleSheet("font-size: 11px; color: #F59E0B;")
        self._deriv_connect_status.setVisible(False)
        layout.addWidget(self._deriv_connect_status)

        # 5. Connect / Switch Account Button
        self._deriv_connect_button = QPushButton()
        self._deriv_connect_button.setObjectName("secondary")
        self._deriv_connect_button.setFixedHeight(30)
        self._deriv_connect_button.setMinimumWidth(150)
        self._deriv_connect_button.setStyleSheet("font-size: 11px; padding: 2px 10px;")
        self._deriv_connect_button.clicked.connect(self.deriv_demo_connect_requested.emit)
        layout.addWidget(self._deriv_connect_button)

        # 6. Safe Stop Button
        self._safe_stop_button = SafeStopButton()
        self._safe_stop_button.setObjectName("danger")
        self._safe_stop_button.setFixedHeight(30)
        self._safe_stop_button.setText(t("btn.safe_stop_short"))
        self._safe_stop_button.setToolTip(t("btn.safe_stop"))
        self._safe_stop_button.setStyleSheet(
            "font-size: 10px; font-weight: 700; padding: 2px 10px;"
        )
        self._safe_stop_button.safe_stop_triggered.connect(self.safe_stop_requested.emit)
        layout.addWidget(self._safe_stop_button)

        # Retained hidden properties for compatibility
        self._card_conn_title = QLabel()
        self._card_conn_title.setVisible(False)
        self._card_conn_hint = QLabel()
        self._card_conn_hint.setVisible(False)

        return frame

    def _build_strategy_rail(self) -> QFrame:
        rail = QFrame()
        rail.setObjectName("StrategyRail")
        rail.setFixedWidth(240)
        layout = QVBoxLayout(rail)
        layout.setContentsMargins(10, 12, 10, 12)
        layout.setSpacing(6)

        self._card_strat_title = QLabel(t("card.strategy_catalog"))
        self._card_strat_title.setObjectName("sectionTitle")
        self._card_strat_title.setStyleSheet(
            "font-size: 11px; font-weight: 800; text-transform: uppercase; "
            "color: #64748B; letter-spacing: 0.8px; padding-left: 2px;"
        )
        layout.addWidget(self._card_strat_title)

        self._card_strat_hint = QLabel()
        self._card_strat_hint.setVisible(False)
        self._library_title = QLabel()
        self._library_title.setVisible(False)
        self._library_body = QLabel()
        self._library_body.setVisible(False)

        self._strategy_group = QButtonGroup(self)
        self._strategy_group.setExclusive(True)
        self._strategy_buttons: dict[str, QPushButton] = {}
        for strategy_id, (label, _description, _eyebrow, icon_name) in _STRATEGIES.items():
            button = QPushButton(f"{label}\nWAITING")
            button.setIcon(icon(icon_name, ACCENT_CYAN, 14))
            button.setObjectName(
                "StrategyButtonActive"
                if strategy_id == self._selected_strategy_id
                else "StrategyButton"
            )
            button.setCheckable(True)
            button.setFixedHeight(48)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
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
        self._portfolio_note.setStyleSheet("font-size: 10px; color: #475569; padding: 2px;")
        layout.addWidget(self._portfolio_note)

        self._real_mode_notice = QLabel()
        self._real_mode_notice.setWordWrap(True)
        self._real_mode_notice.setObjectName("SafetyNotice")
        self._real_mode_notice.setStyleSheet(
            "font-size: 10px; color: #64748B; border: none; background: transparent; padding: 2px;"
        )
        layout.addWidget(self._real_mode_notice)
        return rail

    def _build_strategy_workspace(self) -> QFrame:
        frame = QFrame()
        frame.setObjectName("Card")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(6)

        # Institutional Active Bot Header Banner
        bot_banner = QFrame()
        bot_banner.setObjectName("ActiveBotHeader")
        bot_banner.setStyleSheet(
            "QFrame#ActiveBotHeader {"
            "  background-color: #0E1624;"
            "  border: 1px solid #1E2D3D;"
            "  border-left: 4px solid #00E5FF;"
            "  border-radius: 6px;"
            "  padding: 6px 10px;"
            "}"
        )
        banner_layout = QHBoxLayout(bot_banner)
        banner_layout.setContentsMargins(8, 4, 10, 4)
        banner_layout.setSpacing(10)
        banner_layout.setAlignment(Qt.AlignmentFlag.AlignVCenter)

        # Big Bot Icon
        self._strategy_icon = QLabel()
        self._strategy_icon.setPixmap(icon("icon-play", ACCENT_CYAN, 20).pixmap(20, 20))
        self._strategy_icon.setStyleSheet("padding-right: 4px;")
        banner_layout.addWidget(self._strategy_icon)

        title_box = QVBoxLayout()
        title_box.setSpacing(2)
        title_box.setContentsMargins(0, 0, 0, 0)

        top_row = QHBoxLayout()
        top_row.setSpacing(8)
        top_row.setAlignment(Qt.AlignmentFlag.AlignVCenter)

        self._bot_selected_pill = QLabel(t("bot.selected_badge"))
        self._bot_selected_pill.setStyleSheet(
            "font-size: 9px; font-weight: 800; letter-spacing: 0.8px; color: #00E5FF; "
            "background-color: rgba(0, 229, 255, 0.12); border: 1px solid rgba(0, 229, 255, 0.3); "
            "border-radius: 3px; padding: 1px 6px;"
        )
        top_row.addWidget(self._bot_selected_pill)

        self._strategy_eyebrow = QLabel()
        self._strategy_eyebrow.setObjectName("Eyebrow")
        self._strategy_eyebrow.setStyleSheet(
            "font-size: 10px; font-weight: 700; color: #64748B; letter-spacing: 0.6px;"
        )
        top_row.addWidget(self._strategy_eyebrow)
        top_row.addStretch(1)
        title_box.addLayout(top_row)

        bottom_row = QHBoxLayout()
        bottom_row.setSpacing(8)
        bottom_row.setAlignment(Qt.AlignmentFlag.AlignVCenter)

        self._strategy_title = QLabel()
        self._strategy_title.setObjectName("HeroTitle")
        self._strategy_title.setStyleSheet("font-size: 13px; font-weight: 800; color: #FFFFFF;")
        bottom_row.addWidget(self._strategy_title)

        dash = QLabel("—")
        dash.setStyleSheet("color: #334155;")
        bottom_row.addWidget(dash)

        self._strategy_description = QLabel()
        self._strategy_description.setObjectName("Subtitle")
        self._strategy_description.setStyleSheet("font-size: 11px; color: #94A3B8;")
        bottom_row.addWidget(self._strategy_description, 1)

        title_box.addLayout(bottom_row)
        banner_layout.addLayout(title_box, 1)

        # Right: Signal State Pill
        self._strategy_signal_state = QLabel(f"● {t('bot.state.monitoring')}")
        self._strategy_signal_state.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._strategy_signal_state.setStyleSheet(
            "font-size: 10px; font-weight: 700; color: #00E5FF; "
            "background-color: rgba(0, 229, 255, 0.12); border: 1px solid rgba(0, 229, 255, 0.3); "
            "border-radius: 4px; padding: 4px 10px;"
        )
        banner_layout.addWidget(self._strategy_signal_state)

        # Contextual Bot Control Button
        self._context_bot_toggle_btn = QPushButton(t("bot.btn_turn_on"))
        self._context_bot_toggle_btn.setObjectName("ContextBotToggleBtn")
        self._context_bot_toggle_btn.setFixedHeight(30)
        self._context_bot_toggle_btn.setMinimumWidth(110)
        self._context_bot_toggle_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._context_bot_toggle_btn.setStyleSheet(
            "font-size: 11px; font-weight: 800; padding: 2px 12px; "
            "background-color: #00E5FF; color: #000000; border-radius: 4px;"
        )
        self._context_bot_toggle_btn.clicked.connect(self.bot_toggle_requested.emit)
        banner_layout.addWidget(self._context_bot_toggle_btn)

        layout.addWidget(bot_banner)

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
            self._deriv_connect_status.setVisible(False)
        else:
            self._connection_pill.setText(f"○ {t('broker.disconnected')}")
            self._connection_pill.setObjectName("StatusPillOffline")
            self._deriv_connect_status.setText(t("broker.disconnected_hint"))
            self._deriv_connect_status.setVisible(True)
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
        waiting_status: UiBotWaitingStatus | None = None,
    ) -> None:
        if real_mode:
            self._automation_pill.setText(t("bot.real_read_only"))
            self._automation_pill.setObjectName("StatusPillOffline")
        elif enabled and connected:
            labels = {
                "BOT_WAITING_FOR_NEW_TICK": t("bot.waiting_new_tick"),
                "BOT_WARMING_UP_TICKS": t("bot.warming_up_ticks"),
                "BOT_WAITING_FOR_STRATEGY_SIGNAL": t("bot.waiting_signal"),
                "BOT_NO_POSITIVE_NET_EDGE": t("bot.quality_filter"),
                "BOT_PERFORMANCE_COOLDOWN": t("bot.performance_cooldown"),
                "BOT_RISK_COOLDOWN_ACTIVE": t("bot.risk_cooldown"),
                "BOT_MARTINGALE_ASSET_PINNED": t("bot.martingale_pinned"),
                "BOT_MARTINGALE_PIN_RELEASED": t("bot.martingale_released"),
                "BOT_ORDER_IN_FLIGHT": t("bot.order_in_flight"),
                "BOT_ORDER_SUBMITTED": t("bot.order_submitted"),
            }
            self._automation_pill.setText(labels.get(reason, t("bot.demo_active")))
            self._automation_pill.setObjectName("StatusPillOnline")
        else:
            self._automation_pill.setText(t("bot.demo_paused"))
            self._automation_pill.setObjectName("StatusPillOffline")
        self._automation_pill.style().unpolish(self._automation_pill)
        self._automation_pill.style().polish(self._automation_pill)
        self._automation_pill.setToolTip(reason)
        self.update_context_bot_toggle(enabled)
        if waiting_status is None:
            self._automation_detail.setText("")
            self._automation_detail.setVisible(False)
        else:
            duration = t("bot.waiting_seconds", seconds=waiting_status.waiting_since_seconds)
            self._automation_detail.setText(f"{waiting_status.description} {duration}")
            self._automation_detail.setToolTip(waiting_status.reason_code)
            self._automation_detail.setVisible(True)

    def update_context_bot_toggle(self, enabled: bool) -> None:
        self._bot_enabled = enabled
        if not hasattr(self, "_context_bot_toggle_btn"):
            return
        if enabled:
            self._context_bot_toggle_btn.setText(t("bot.btn_pause"))
            self._context_bot_toggle_btn.setStyleSheet(
                "font-size: 11px; font-weight: 800; padding: 2px 12px; "
                "background-color: #EF4444; color: #FFFFFF; border-radius: 4px;"
            )
        else:
            self._context_bot_toggle_btn.setText(t("bot.btn_turn_on"))
            self._context_bot_toggle_btn.setStyleSheet(
                "font-size: 11px; font-weight: 800; padding: 2px 12px; "
                "background-color: #00E5FF; color: #000000; border-radius: 4px;"
            )

    def update_orders(self, orders: Sequence[OrderSummary]) -> None:
        self._orders = tuple(item for item in orders if item.broker == self.broker_key)
        self.orders.update_orders(self._orders)
        self.results.update_results(self._orders)

    @property
    def selected_strategy_id(self) -> str:
        return self._selected_strategy_id

    def update_strategy_statuses(self, statuses: Sequence[UiDerivStrategyStatus]) -> None:
        self._strategy_statuses = {item.strategy_id: item for item in statuses}
        status_labels = {
            "SHADOW_SIGNAL": "SIGNAL READY",
            "signal_detected": "SIGNAL READY",
            "MONITORING": "MONITORING",
            "monitoring": "MONITORING",
            "DATA_BLOCKED": "BLOCKED",
            "cooling_down": "COOLDOWN",
            "warming_up": "WARMING UP",
            "waiting_ticks": "WARMING UP",
        }
        for strategy_id, button in self._strategy_buttons.items():
            status = self._strategy_statuses.get(strategy_id)
            if status is None:
                suffix = "WAITING"
            elif (
                hasattr(status, "waiting_status")
                and status.waiting_status == UiBotWaitingStatus.ACTIVE_RUNNING
                and getattr(status, "signal_state", "") in ("monitoring", "MONITORING")
            ):
                suffix = "MONITORING"
            elif (
                hasattr(status, "waiting_status")
                and status.waiting_status
                in (
                    UiBotWaitingStatus.INSUFFICIENT_DATA,
                    UiBotWaitingStatus.WARMING_UP,
                    UiBotWaitingStatus.EVALUATING,
                )
                or getattr(status, "signal_state", "") in ("warming_up", "waiting_ticks")
            ):
                suffix = "WARMING UP"
            else:
                suffix = status_labels.get(getattr(status, "signal_state", ""), "WARMING UP")
            strat_info = _STRATEGIES.get(strategy_id)
            if strat_info:
                icon_name = strat_info[3]
                label = strat_info[0]
                button.setText(f"{label}\n{suffix}")
                button.setIcon(icon(icon_name, ACCENT_CYAN, 14))
        self._update_active_bot_signal_badge()

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
        title, description, eyebrow, icon_name = _STRATEGIES[strategy_id]
        if hasattr(self, "_strategy_icon"):
            self._strategy_icon.setPixmap(icon(icon_name, ACCENT_CYAN, 20).pixmap(20, 20))
        self._strategy_title.setText(title)
        self._strategy_description.setText(description)
        self._strategy_description.setToolTip(description)
        self._strategy_eyebrow.setText(eyebrow)
        self._update_active_bot_signal_badge()

    def _update_active_bot_signal_badge(self) -> None:
        if not hasattr(self, "_strategy_signal_state"):
            return
        status = self._strategy_statuses.get(self._selected_strategy_id)
        if status is None:
            self._strategy_signal_state.setText(f"○ {t('bot.state.ready')}")
            self._strategy_signal_state.setStyleSheet(
                "font-size: 10px; font-weight: 700; color: #64748B; "
                "background-color: rgba(100, 116, 139, 0.1); "
                "border: 1px solid rgba(100, 116, 139, 0.2); "
                "border-radius: 4px; padding: 4px 10px;"
            )
            return

        sig_state = getattr(status, "signal_state", "")
        if sig_state in ("signal_detected", "SHADOW_SIGNAL"):
            self._strategy_signal_state.setText(f"● {t('bot.state.signal')}")
            self._strategy_signal_state.setStyleSheet(
                "font-size: 10px; font-weight: 700; color: #10B981; "
                "background-color: rgba(16, 185, 129, 0.15); "
                "border: 1px solid rgba(16, 185, 129, 0.4); "
                "border-radius: 4px; padding: 4px 10px;"
            )
        elif (
            hasattr(status, "waiting_status")
            and status.waiting_status
            in (
                UiBotWaitingStatus.INSUFFICIENT_DATA,
                UiBotWaitingStatus.WARMING_UP,
                UiBotWaitingStatus.EVALUATING,
            )
            or sig_state in ("warming_up", "waiting_ticks")
        ):
            self._strategy_signal_state.setText(f"⏳ {t('bot.state.warming')}")
            self._strategy_signal_state.setStyleSheet(
                "font-size: 10px; font-weight: 700; color: #F59E0B; "
                "background-color: rgba(245, 158, 11, 0.12); "
                "border: 1px solid rgba(245, 158, 11, 0.3); "
                "border-radius: 4px; padding: 4px 10px;"
            )
        else:
            self._strategy_signal_state.setText(f"● {t('bot.state.monitoring')}")
            self._strategy_signal_state.setStyleSheet(
                "font-size: 10px; font-weight: 700; color: #00E5FF; "
                "background-color: rgba(0, 229, 255, 0.12); "
                "border: 1px solid rgba(0, 229, 255, 0.3); "
                "border-radius: 4px; padding: 4px 10px;"
            )

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
        self._page_title.setText(t("page.deriv"))
        self._page_subtitle.setText(t("page.deriv_subtitle"))
        self._card_conn_title.setText(t("card.connection"))
        self._card_conn_hint.setText(t("card.connection_hint"))
        self._card_strat_title.setText("🤖 " + t("card.strategy_catalog"))
        self._card_strat_hint.setText(t("card.strategy_hint"))
        self._account_caption.setText(t("deriv.hub.account"))
        self._balance_caption.setText(t("broker.balance"))
        self._deriv_connect_button.setText(t("deriv.connect.button"))
        self._safe_stop_button.retranslate()
        self._safe_stop_button.setText(t("btn.safe_stop_short"))
        self._safe_stop_button.setToolTip(t("btn.safe_stop"))
        if not self._deriv_connect_status.text():
            self._deriv_connect_status.setText(t("deriv.connect.status.ready"))
        self._library_title.setText(t("deriv.library.title"))
        self._library_body.setText(t("deriv.library.body"))
        self.update_strategy_statuses(tuple(self._strategy_statuses.values()))
        self._portfolio_note.setText(t("deriv.library.note"))
        if hasattr(self, "_bot_selected_pill"):
            self._bot_selected_pill.setText(t("bot.selected_badge"))
        title, description, eyebrow, icon_name = _STRATEGIES[self._selected_strategy_id]
        if hasattr(self, "_strategy_icon"):
            self._strategy_icon.setPixmap(icon(icon_name, ACCENT_CYAN, 20).pixmap(20, 20))
        self._strategy_eyebrow.setText(eyebrow)
        self._strategy_title.setText(title)
        self._strategy_description.setText(description)
        self._strategy_description.setToolTip(description)
        self._update_active_bot_signal_badge()
        if hasattr(self, "_context_bot_toggle_btn"):
            self.update_context_bot_toggle(getattr(self, "_bot_enabled", False))
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
