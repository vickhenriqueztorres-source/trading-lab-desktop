from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QFrame,
    QLabel,
    QPushButton,
    QScrollArea,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from apps.ui.components.broker_card import BrokerCardWidget
from apps.ui.components.iqoption_asset_radar import IqOptionAssetRadarWidget
from apps.ui.components.iqoption_strategy_summary import IqOptionStrategySummaryWidget
from apps.ui.components.order_table import OrderTableView
from apps.ui.formatting import format_minor_units
from apps.ui.i18n import t
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


class BrokerWorkspaceWidget(QWidget):
    """Broker-isolated projection with honest, read-only configuration guidance."""

    deriv_demo_connect_requested = Signal()
    iqoption_login_requested = Signal()

    def __init__(
        self,
        broker_key: str,
        display_name: str,
        intro_key: str,
        configuration_key: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.broker_key = broker_key
        self._display_name = display_name
        self._intro_key = intro_key
        self._configuration_key = configuration_key
        self._last_status: BrokerCardStatus | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(12)

        self._tabs = QTabWidget()
        self._tabs.setAccessibleName(display_name)
        layout.addWidget(self._tabs)

        status_scroll = QScrollArea()
        status_scroll.setWidgetResizable(True)
        status_scroll.setFrameShape(QFrame.Shape.NoFrame)
        status_page = QWidget()
        status_layout = QVBoxLayout(status_page)
        status_layout.setContentsMargins(14, 14, 14, 14)
        status_layout.setSpacing(12)
        self._intro = QLabel()
        self._intro.setWordWrap(True)
        self._intro.setObjectName("GuidanceText")
        status_layout.addWidget(self._intro)
        self.card = BrokerCardWidget(display_name)
        status_layout.addWidget(self.card)

        self.strategy_summary: IqOptionStrategySummaryWidget | None = None
        self.asset_radar: IqOptionAssetRadarWidget | None = None
        if broker_key == "IQOPTION":
            self.strategy_summary = IqOptionStrategySummaryWidget()
            status_layout.addWidget(self.strategy_summary)
            self.asset_radar = IqOptionAssetRadarWidget()
            status_layout.addWidget(self.asset_radar)

        self.orders = OrderTableView()
        status_layout.addWidget(self.orders, 1)
        status_scroll.setWidget(status_page)
        self._tabs.addTab(status_scroll, "")

        configuration_page = QScrollArea()
        configuration_page.setWidgetResizable(True)
        configuration_page.setFrameShape(QFrame.Shape.NoFrame)
        configuration_content = QWidget()
        configuration_layout = QVBoxLayout(configuration_content)
        configuration_layout.setContentsMargins(14, 14, 14, 14)
        configuration_layout.setSpacing(12)
        guidance = QFrame()
        guidance.setObjectName("Surface")
        guidance_layout = QVBoxLayout(guidance)
        guidance_layout.setContentsMargins(16, 16, 16, 16)
        guidance_layout.setSpacing(10)
        self._configuration_title = QLabel()
        self._configuration_title.setObjectName("Title")
        guidance_layout.addWidget(self._configuration_title)
        self._configuration_body = QLabel()
        self._configuration_body.setWordWrap(True)
        self._configuration_body.setObjectName("GuidanceText")
        guidance_layout.addWidget(self._configuration_body)
        self._deriv_connect_button: QPushButton | None = None
        self._deriv_connect_status: QLabel | None = None
        self._iqoption_login_button: QPushButton | None = None
        self._iqoption_login_status: QLabel | None = None
        self._iqoption_login_title: QLabel | None = None
        if broker_key == "DERIV":
            self._deriv_connect_button = QPushButton()
            self._deriv_connect_button.setObjectName("PrimaryButton")
            self._deriv_connect_button.clicked.connect(self.deriv_demo_connect_requested.emit)
            guidance_layout.addWidget(self._deriv_connect_button)
            self._deriv_connect_status = QLabel()
            self._deriv_connect_status.setWordWrap(True)
            self._deriv_connect_status.setObjectName("Subtitle")
            guidance_layout.addWidget(self._deriv_connect_status)
        elif broker_key == "IQOPTION":
            login_frame = QFrame()
            login_frame.setObjectName("LoginSurface")
            login_layout = QVBoxLayout(login_frame)
            login_layout.setContentsMargins(12, 12, 12, 12)
            login_layout.setSpacing(8)
            login_title = QLabel()
            login_title.setObjectName("Title")
            login_layout.addWidget(login_title)
            self._iqoption_login_title = login_title
            self._iqoption_login_button = QPushButton()
            self._iqoption_login_button.setObjectName("PrimaryButton")
            self._iqoption_login_button.clicked.connect(self.iqoption_login_requested.emit)
            login_layout.addWidget(self._iqoption_login_button)
            self._iqoption_login_status = QLabel()
            self._iqoption_login_status.setWordWrap(True)
            self._iqoption_login_status.setObjectName("Subtitle")
            login_layout.addWidget(self._iqoption_login_status)
            guidance_layout.addWidget(login_frame)
        self._scope = QLabel()
        self._scope.setObjectName("Subtitle")
        guidance_layout.addWidget(self._scope)
        self._effective_mode = QLabel()
        self._effective_mode.setObjectName("ValueMono")
        guidance_layout.addWidget(self._effective_mode)
        self._real_mode_notice = QLabel()
        self._real_mode_notice.setWordWrap(True)
        self._real_mode_notice.setObjectName("SafetyNotice")
        guidance_layout.addWidget(self._real_mode_notice)
        configuration_layout.addWidget(guidance)
        self._configuration_layout = configuration_layout
        configuration_layout.addStretch()
        configuration_page.setWidget(configuration_content)
        self._tabs.addTab(configuration_page, "")
        self.retranslate()

    @property
    def tabs(self) -> QTabWidget:
        return self._tabs

    def update_status(self, status: BrokerCardStatus) -> None:
        if status.broker != self.broker_key:
            raise ValueError("broker projection does not match workspace")
        self._last_status = status
        self.card.update_card(status)
        self._effective_mode.setText(
            f"{t('config.effective_mode')}: {_mode_text(status.account_mode)}"
        )
        self._real_mode_notice.setText(
            t("config.real_mode_active")
            if status.account_mode is UiAccountMode.REAL
            else t("config.real_mode_available")
        )

    def update_orders(self, orders: Sequence[OrderSummary]) -> None:
        filtered = tuple(item for item in orders if item.broker == self.broker_key)
        self.orders.update_orders(filtered)
        if self.strategy_summary is not None:
            self.strategy_summary.update_orders(filtered)

    def update_iqoption_radar(self, ranking: Sequence[UiIqOptionAssetRank]) -> None:
        if self.asset_radar is not None:
            self.asset_radar.update_ranking(ranking)

    def update_iqoption_risk(self, config: UiIqOptionRiskConfig | None) -> None:
        if self.strategy_summary is not None:
            self.strategy_summary.update_config(config)

    def set_deriv_connect_busy(self, busy: bool, message: str | None = None) -> None:
        if self._deriv_connect_button is None or self._deriv_connect_status is None:
            return
        self._deriv_connect_button.setEnabled(not busy)
        self._deriv_connect_status.setText(message or t("deriv.connect.status.ready"))

    def set_iqoption_login_busy(self, busy: bool, message: str | None = None) -> None:
        if self._iqoption_login_button is None or self._iqoption_login_status is None:
            return
        self._iqoption_login_button.setEnabled(not busy)
        if message is not None:
            self._iqoption_login_status.setText(message)

    def set_iqoption_login_status(self, message: str) -> None:
        if self._iqoption_login_status is not None:
            self._iqoption_login_status.setText(message)

    def tab_label(self) -> str:
        if self._last_status is None:
            return self._display_name
        return f"{self._display_name} — {_mode_text(self._last_status.account_mode)}"

    def add_configuration_widget(self, widget: QWidget) -> None:
        stretch_index = self._configuration_layout.count() - 1
        self._configuration_layout.insertWidget(stretch_index, widget)

    def retranslate(self) -> None:
        self._tabs.setTabText(0, t("tabs.status"))
        self._tabs.setTabText(1, t("tabs.configuration"))
        self._intro.setText(t(self._intro_key))
        self._configuration_title.setText(t("config.read_only_title"))
        self._configuration_body.setText(t(self._configuration_key))
        if self._deriv_connect_button is not None:
            self._deriv_connect_button.setText(t("deriv.connect.button"))
        if self._deriv_connect_status is not None and not self._deriv_connect_status.text():
            self._deriv_connect_status.setText(t("deriv.connect.status.ready"))
        if self._iqoption_login_title is not None:
            self._iqoption_login_title.setText(t("iq_option.login.title"))
        if self._iqoption_login_button is not None:
            self._iqoption_login_button.setText(t("iq_option.login.button"))
        if self._iqoption_login_status is not None and not self._iqoption_login_status.text():
            self._iqoption_login_status.setText(t("iq_option.login.status"))
        self._scope.setText(f"{t('config.scope')}: {self._display_name}")
        if self._last_status is None:
            mode = t("config.waiting_projection")
        else:
            mode = _mode_text(self._last_status.account_mode)
        self._effective_mode.setText(f"{t('config.effective_mode')}: {mode}")
        self._real_mode_notice.setText(t("config.real_mode_available"))
        self.card.retranslate()
        self.orders.retranslate()


@dataclass(frozen=True, slots=True)
class _SettingsPage:
    tab_key: str
    title_key: str
    body_key: str
    scope_key: str
    effective_key: str


class SettingsWorkspaceWidget(QWidget):
    """Explains effective settings without inventing unconfirmed write commands."""

    _PAGES = (
        _SettingsPage(
            "settings.application.tab",
            "settings.application.title",
            "settings.application.body",
            "settings.application.scope",
            "settings.application.effective",
        ),
        _SettingsPage(
            "settings.risk.tab",
            "settings.risk.title",
            "settings.risk.body",
            "settings.risk.scope",
            "settings.risk.effective",
        ),
        _SettingsPage(
            "settings.strategies.tab",
            "settings.strategies.title",
            "settings.strategies.body",
            "settings.strategies.scope",
            "settings.strategies.effective",
        ),
        _SettingsPage(
            "settings.support.tab",
            "settings.support.title",
            "settings.support.body",
            "settings.support.scope",
            "settings.support.effective",
        ),
    )

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(12)
        self._intro = QLabel()
        self._intro.setWordWrap(True)
        self._intro.setObjectName("GuidanceText")
        layout.addWidget(self._intro)
        self._tabs = QTabWidget()
        self._tabs.setAccessibleName("Settings")
        layout.addWidget(self._tabs, 1)
        self._labels: list[tuple[QLabel, QLabel, QLabel, QLabel]] = []
        for _page in self._PAGES:
            widget = QWidget()
            page_layout = QVBoxLayout(widget)
            page_layout.setContentsMargins(14, 14, 14, 14)
            page_layout.setSpacing(12)
            panel = QFrame()
            panel.setObjectName("Surface")
            panel_layout = QVBoxLayout(panel)
            panel_layout.setContentsMargins(16, 16, 16, 16)
            panel_layout.setSpacing(10)
            title = QLabel()
            title.setObjectName("Title")
            body = QLabel()
            body.setWordWrap(True)
            body.setObjectName("GuidanceText")
            scope = QLabel()
            scope.setObjectName("Subtitle")
            effective = QLabel()
            effective.setWordWrap(True)
            effective.setObjectName("SafetyNotice")
            for label in (title, body, scope, effective):
                panel_layout.addWidget(label)
            page_layout.addWidget(panel)
            page_layout.addStretch()
            self._tabs.addTab(widget, "")
            self._labels.append((title, body, scope, effective))
        self._risk_effective: tuple[int, int, str | None, str] | None = None
        self.retranslate()

    @property
    def tabs(self) -> QTabWidget:
        return self._tabs

    def update_risk_projection(
        self,
        exposure_minor_units: int,
        max_exposure_minor_units: int,
        currency: str | None,
        risk_state: str,
    ) -> None:
        self._risk_effective = (
            exposure_minor_units,
            max_exposure_minor_units,
            currency,
            risk_state,
        )
        self._update_risk_label()

    def _update_risk_label(self) -> None:
        if self._risk_effective is None:
            return
        exposure, maximum, currency, risk_state = self._risk_effective
        normalized_currency = (currency or "USD").upper()
        active = format_minor_units(exposure, normalized_currency)
        limit = format_minor_units(maximum, normalized_currency)
        self._labels[1][3].setText(
            t("settings.risk.projected", active=active, limit=limit, state=risk_state)
        )

    def retranslate(self) -> None:
        self._intro.setText(t("settings.intro"))
        for index, page in enumerate(self._PAGES):
            self._tabs.setTabText(index, t(page.tab_key))
            title, body, scope, effective = self._labels[index]
            title.setText(t(page.title_key))
            body.setText(t(page.body_key))
            scope.setText(t(page.scope_key))
            effective.setText(t(page.effective_key))
        self._update_risk_label()
