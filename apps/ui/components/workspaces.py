from __future__ import annotations

from collections.abc import Sequence

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
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
from apps.ui.i18n import I18nManager, t
from packages.protocol.ui_messages import (
    BrokerCardStatus,
    OrderSummary,
    UiAccountMode,
    UiIqOptionAssetRank,
    UiIqOptionExecutionMetrics,
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

    def update_iqoption_metrics(self, metrics: UiIqOptionExecutionMetrics | None) -> None:
        if self.strategy_summary is not None:
            self.strategy_summary.update_metrics(metrics)

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


class SettingsWorkspaceWidget(QWidget):
    """Explains effective settings and provides operator controls for language and diagnostics."""

    diagnostic_requested = Signal()

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

        # Tab 0: General
        self._tabs.addTab(self._build_general_tab(), "")

        # Tab 1: Notifications
        self._tabs.addTab(self._build_notifications_tab(), "")

        # Tab 2: Diagnostics
        self._tabs.addTab(self._build_diagnostics_tab(), "")

        # Tab 3: About
        self._tabs.addTab(self._build_about_tab(), "")

        self._risk_effective: tuple[int, int, str | None, str] | None = None
        self.retranslate()

    def _build_general_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(12)

        card = QFrame()
        card.setObjectName("Surface")
        c_layout = QVBoxLayout(card)
        c_layout.setContentsMargins(16, 16, 16, 16)
        c_layout.setSpacing(10)

        self._gen_title = QLabel(t("settings.general"))
        self._gen_title.setObjectName("sectionTitle")
        self._gen_title.setStyleSheet("font-size: 16px; font-weight: 600;")
        c_layout.addWidget(self._gen_title)

        self._gen_hint = QLabel(t("settings.general_hint"))
        self._gen_hint.setObjectName("hint")
        c_layout.addWidget(self._gen_hint)

        lang_row = QHBoxLayout()
        lang_row.setSpacing(10)
        self._lang_label = QLabel(t("settings.language") + ":")
        self._lang_label.setObjectName("Subtitle")
        lang_row.addWidget(self._lang_label)

        self._lang_combo = QComboBox()
        self._lang_combo.addItem("Español (ES)", "es")
        self._lang_combo.addItem("English (EN)", "en")
        current_lang = I18nManager.get_language()
        idx = self._lang_combo.findData(current_lang)
        if idx >= 0:
            self._lang_combo.setCurrentIndex(idx)
        self._lang_combo.currentIndexChanged.connect(self._on_language_changed)
        lang_row.addWidget(self._lang_combo)
        lang_row.addStretch()
        c_layout.addLayout(lang_row)

        self._gen_scope = QLabel()
        self._gen_scope.setObjectName("Subtitle")
        c_layout.addWidget(self._gen_scope)

        layout.addWidget(card)
        layout.addStretch()
        return widget

    def _build_notifications_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(12)

        card = QFrame()
        card.setObjectName("Surface")
        c_layout = QVBoxLayout(card)
        c_layout.setContentsMargins(16, 16, 16, 16)
        c_layout.setSpacing(10)

        self._notif_title = QLabel(t("settings.notifications"))
        self._notif_title.setObjectName("sectionTitle")
        self._notif_title.setStyleSheet("font-size: 16px; font-weight: 600;")
        c_layout.addWidget(self._notif_title)

        self._notif_hint = QLabel(t("settings.notifications_hint"))
        self._notif_hint.setObjectName("hint")
        c_layout.addWidget(self._notif_hint)

        self._risk_effective_label = QLabel()
        self._risk_effective_label.setWordWrap(True)
        self._risk_effective_label.setObjectName("SafetyNotice")
        c_layout.addWidget(self._risk_effective_label)

        layout.addWidget(card)
        layout.addStretch()
        return widget

    def _build_diagnostics_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(12)

        card = QFrame()
        card.setObjectName("Surface")
        c_layout = QVBoxLayout(card)
        c_layout.setContentsMargins(16, 16, 16, 16)
        c_layout.setSpacing(12)

        self._diag_title = QLabel(t("settings.diagnostics"))
        self._diag_title.setObjectName("sectionTitle")
        self._diag_title.setStyleSheet("font-size: 16px; font-weight: 600;")
        c_layout.addWidget(self._diag_title)

        self._diag_hint = QLabel(t("settings.diagnostics_hint"))
        self._diag_hint.setObjectName("hint")
        c_layout.addWidget(self._diag_hint)

        self._btn_export_diag = QPushButton("📦 " + t("btn.diagnostic"))
        self._btn_export_diag.setObjectName("PrimaryButton")
        self._btn_export_diag.clicked.connect(self.diagnostic_requested.emit)
        c_layout.addWidget(self._btn_export_diag)

        layout.addWidget(card)
        layout.addStretch()
        return widget

    def _build_about_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(12)

        card = QFrame()
        card.setObjectName("Surface")
        c_layout = QVBoxLayout(card)
        c_layout.setContentsMargins(16, 16, 16, 16)
        c_layout.setSpacing(10)

        self._about_title = QLabel(t("settings.about"))
        self._about_title.setObjectName("sectionTitle")
        self._about_title.setStyleSheet("font-size: 16px; font-weight: 600;")
        c_layout.addWidget(self._about_title)

        self._about_hint = QLabel(t("settings.about_hint"))
        self._about_hint.setObjectName("hint")
        c_layout.addWidget(self._about_hint)

        self._version_label = QLabel(t("settings.app_version", version="1.9.11"))
        self._version_label.setObjectName("ValueMono")
        c_layout.addWidget(self._version_label)

        self._btn_support_telegram = QPushButton("✈️ " + t("support.telegram"))
        self._btn_support_telegram.setObjectName("secondary")
        self._btn_support_telegram.setToolTip(t("support.telegram_url"))
        c_layout.addWidget(self._btn_support_telegram)

        layout.addWidget(card)
        layout.addStretch()
        return widget

    @property
    def tabs(self) -> QTabWidget:
        return self._tabs

    def _on_language_changed(self, index: int) -> None:
        selected_lang = str(self._lang_combo.itemData(index))
        if (
            selected_lang in I18nManager.SUPPORTED_LANGUAGES
            and selected_lang != I18nManager.get_language()
        ):
            I18nManager.set_language(selected_lang)

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
        self._risk_effective_label.setText(
            t("settings.risk.projected", active=active, limit=limit, state=risk_state)
        )

    def retranslate(self) -> None:
        self._intro.setText(t("settings.intro"))
        self._tabs.setTabText(0, t("settings.general"))
        self._tabs.setTabText(1, t("settings.notifications"))
        self._tabs.setTabText(2, t("settings.diagnostics"))
        self._tabs.setTabText(3, t("settings.about"))

        self._gen_title.setText(t("settings.general"))
        self._gen_hint.setText(t("settings.general_hint"))
        self._lang_label.setText(t("settings.language") + ":")
        self._gen_scope.setText(t("settings.application.scope"))

        self._notif_title.setText(t("settings.notifications"))
        self._notif_hint.setText(t("settings.notifications_hint"))

        self._diag_title.setText(t("settings.diagnostics"))
        self._diag_hint.setText(t("settings.diagnostics_hint"))
        self._btn_export_diag.setText("📦 " + t("btn.diagnostic"))

        self._about_title.setText(t("settings.about"))
        self._about_hint.setText(t("settings.about_hint"))
        self._btn_support_telegram.setText("✈️ " + t("support.telegram"))
        self._btn_support_telegram.setToolTip(t("support.telegram_url"))

        # Sync combo index without triggering loop
        current = I18nManager.get_language()
        idx = self._lang_combo.findData(current)
        if idx >= 0 and self._lang_combo.currentIndex() != idx:
            self._lang_combo.blockSignals(True)
            self._lang_combo.setCurrentIndex(idx)
            self._lang_combo.blockSignals(False)

        self._update_risk_label()
