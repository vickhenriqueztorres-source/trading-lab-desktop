from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from PySide6.QtWidgets import QFrame, QLabel, QTabWidget, QVBoxLayout, QWidget

from apps.ui.components.broker_card import BrokerCardWidget
from apps.ui.components.order_table import OrderTableView
from apps.ui.formatting import format_minor_units
from apps.ui.i18n import t
from packages.protocol.ui_messages import BrokerCardStatus, OrderSummary, UiAccountMode


def _mode_text(mode: UiAccountMode) -> str:
    translated = t(f"mode.{mode.value}")
    return mode.value if translated.startswith("mode.") else translated


class BrokerWorkspaceWidget(QWidget):
    """Broker-isolated projection with honest, read-only configuration guidance."""

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
        self.orders = OrderTableView()
        status_layout.addWidget(self.orders, 1)
        self._tabs.addTab(status_page, "")

        configuration_page = QWidget()
        configuration_layout = QVBoxLayout(configuration_page)
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
        configuration_layout.addStretch()
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

    def update_orders(self, orders: Sequence[OrderSummary]) -> None:
        self.orders.update_orders(tuple(item for item in orders if item.broker == self.broker_key))

    def tab_label(self) -> str:
        if self._last_status is None:
            return self._display_name
        return f"{self._display_name} — {_mode_text(self._last_status.account_mode)}"

    def retranslate(self) -> None:
        self._tabs.setTabText(0, t("tabs.status"))
        self._tabs.setTabText(1, t("tabs.configuration"))
        self._intro.setText(t(self._intro_key))
        self._configuration_title.setText(t("config.read_only_title"))
        self._configuration_body.setText(t(self._configuration_key))
        self._scope.setText(f"{t('config.scope')}: {self._display_name}")
        if self._last_status is None:
            mode = t("config.waiting_projection")
        else:
            mode = _mode_text(self._last_status.account_mode)
        self._effective_mode.setText(f"{t('config.effective_mode')}: {mode}")
        self._real_mode_notice.setText(t("config.no_real_mode"))
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
