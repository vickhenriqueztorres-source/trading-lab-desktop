from __future__ import annotations

import contextlib

from PySide6.QtCore import QTimer
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from apps.ui.components import (
    BrokerCardWidget,
    BrokerWorkspaceWidget,
    GlobalRiskGaugeWidget,
    HealthGatePillWidget,
    OrderTableView,
    SafeStopButton,
    SettingsWorkspaceWidget,
)
from apps.ui.controller import UiController
from apps.ui.formatting import format_minor_units
from apps.ui.i18n import I18nManager, t
from apps.ui.theme import (
    ACCENT_AMBER,
    ACCENT_CYAN,
    ACCENT_GREEN,
    ACCENT_RED,
    TEXT_MUTED,
    TEXT_SECONDARY,
    get_application_stylesheet,
)
from packages.protocol.ui_messages import UiGlobalState


class TradingLabMainWindow(QMainWindow):
    """Professional Trading Lab Desktop UI (PySide6 / Qt 6)."""

    _TAB_OVERVIEW = 0
    _TAB_DERIV = 1
    _TAB_IQ_OPTION = 2
    _TAB_ACTIVITY = 3
    _TAB_SETTINGS = 4

    def __init__(self, controller: UiController, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._controller = controller

        self.setWindowTitle(t("app.title") + " — " + t("app.practice_badge"))
        self.resize(1180, 780)
        self.setMinimumSize(960, 640)

        # Apply dark theme
        self.setStyleSheet(get_application_stylesheet())

        self._build_ui()

        # Timer for polling IPC projection
        self._timer = QTimer(self)
        self._timer.setInterval(300)
        self._timer.timeout.connect(self._refresh_projection)
        self._timer.start()

        # Subscribe to language changes
        I18nManager.subscribe(self._on_language_changed)

    def _build_ui(self) -> None:
        central_widget = QWidget(self)
        self.setCentralWidget(central_widget)

        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # 1. Header Bar
        main_layout.addWidget(self._create_header_bar())

        self._main_tabs = QTabWidget()
        self._main_tabs.setObjectName("MainNavigation")
        self._main_tabs.setAccessibleName("Trading Lab navigation")

        self._main_tabs.addTab(self._create_overview_page(), "")
        self._deriv_workspace = BrokerWorkspaceWidget(
            "DERIV",
            "Deriv",
            "broker.deriv.intro",
            "config.deriv.body",
        )
        self._main_tabs.addTab(self._deriv_workspace, "")
        self._iqoption_workspace = BrokerWorkspaceWidget(
            "IQ_OPTION",
            "IQ Option",
            "broker.iq_option.intro",
            "config.iq_option.body",
        )
        self._main_tabs.addTab(self._iqoption_workspace, "")
        self._main_tabs.addTab(self._create_activity_page(), "")
        self._settings_workspace = SettingsWorkspaceWidget()
        self._main_tabs.addTab(self._settings_workspace, "")
        main_layout.addWidget(self._main_tabs, 1)

        # Persistent emergency actions remain available on every tab.
        main_layout.addWidget(self._create_action_bar())
        self._retranslate_navigation()

    def _create_overview_page(self) -> QScrollArea:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)

        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(20, 16, 20, 16)
        content_layout.setSpacing(14)

        self._overview_intro = QLabel()
        self._overview_intro.setWordWrap(True)
        self._overview_intro.setObjectName("GuidanceText")
        content_layout.addWidget(self._overview_intro)

        content_layout.addLayout(self._create_kpis_row())
        content_layout.addLayout(self._create_broker_hub())
        self._health_pill_widget = HealthGatePillWidget()
        content_layout.addWidget(self._health_pill_widget)
        content_layout.addStretch()
        scroll.setWidget(content)
        return scroll

    def _create_activity_page(self) -> QScrollArea:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(14)
        self._activity_intro = QLabel()
        self._activity_intro.setWordWrap(True)
        self._activity_intro.setObjectName("GuidanceText")
        layout.addWidget(self._activity_intro)
        self._order_table_widget = OrderTableView()
        layout.addWidget(self._order_table_widget, 1)
        scroll.setWidget(content)
        return scroll

    def _create_header_bar(self) -> QFrame:
        header = QFrame()
        header.setObjectName("HeaderBar")
        layout = QHBoxLayout(header)
        layout.setContentsMargins(20, 10, 20, 10)
        layout.setSpacing(14)

        # Brand Title
        self._lbl_brand = QLabel("⚡ TRADING LAB")
        self._lbl_brand.setStyleSheet(
            f"color: {ACCENT_CYAN}; font-size: 18px; font-weight: 900; letter-spacing: 1px;"
        )
        layout.addWidget(self._lbl_brand)

        # Practice Mode Badge
        self._lbl_badge = QLabel(t("app.practice_badge"))
        self._lbl_badge.setObjectName("BadgePractice")
        layout.addWidget(self._lbl_badge)

        self._lbl_subtitle = QLabel(t("app.practice_subtitle"))
        self._lbl_subtitle.setStyleSheet(
            f"color: {TEXT_MUTED}; font-size: 11px; font-weight: bold;"
        )
        layout.addWidget(self._lbl_subtitle)

        layout.addStretch()

        # Core IPC Connection Status
        self._lbl_ipc_status = QLabel(f"● {t('app.status.connected')}")
        self._lbl_ipc_status.setStyleSheet(
            f"color: {ACCENT_GREEN}; font-weight: bold; font-size: 11px;"
        )
        layout.addWidget(self._lbl_ipc_status)

        # Language Switcher
        lang_box = QHBoxLayout()
        lang_box.setSpacing(4)

        self._btn_es = QPushButton("ES")
        self._btn_es.setObjectName("LangButton")
        self._btn_es.setCheckable(True)
        self._btn_es.setChecked(I18nManager.get_language() == "es")
        self._btn_es.clicked.connect(lambda: self._set_language("es"))
        lang_box.addWidget(self._btn_es)

        self._btn_en = QPushButton("EN")
        self._btn_en.setObjectName("LangButton")
        self._btn_en.setCheckable(True)
        self._btn_en.setChecked(I18nManager.get_language() == "en")
        self._btn_en.clicked.connect(lambda: self._set_language("en"))
        lang_box.addWidget(self._btn_en)

        layout.addLayout(lang_box)
        return header

    def _create_kpis_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(14)

        # KPI 1: Risk Exposure Gauge
        self._risk_gauge = GlobalRiskGaugeWidget()
        row.addWidget(self._risk_gauge, 2)

        # KPI 2: Daily P&L Card
        self._card_pnl = QFrame()
        self._card_pnl.setObjectName("Card")
        pnl_layout = QVBoxLayout(self._card_pnl)
        pnl_layout.setContentsMargins(16, 14, 16, 14)
        pnl_layout.setSpacing(8)

        self._lbl_pnl_title = QLabel(t("kpi.daily_pnl"))
        self._lbl_pnl_title.setObjectName("Subtitle")
        pnl_layout.addWidget(self._lbl_pnl_title)

        self._lbl_pnl_val = QLabel("$ 0.00 USD")
        self._lbl_pnl_val.setObjectName("ValueMono")
        self._lbl_pnl_val.setStyleSheet(f"color: {ACCENT_GREEN}; font-size: 20px;")
        pnl_layout.addWidget(self._lbl_pnl_val)

        self._lbl_pnl_detail = QLabel(t("kpi.pnl_detail"))
        self._lbl_pnl_detail.setStyleSheet(f"color: {TEXT_MUTED}; font-size: 11px;")
        pnl_layout.addWidget(self._lbl_pnl_detail)
        row.addWidget(self._card_pnl, 2)

        # KPI 3: System State Card
        self._card_state = QFrame()
        self._card_state.setObjectName("Card")
        state_layout = QVBoxLayout(self._card_state)
        state_layout.setContentsMargins(16, 14, 16, 14)
        state_layout.setSpacing(8)

        self._lbl_state_title = QLabel(t("kpi.global_state"))
        self._lbl_state_title.setObjectName("Subtitle")
        state_layout.addWidget(self._lbl_state_title)

        self._lbl_state_val = QLabel(t("state.READY"))
        self._lbl_state_val.setObjectName("ValueMono")
        self._lbl_state_val.setStyleSheet(f"color: {ACCENT_GREEN}; font-size: 16px;")
        state_layout.addWidget(self._lbl_state_val)

        self._lbl_consec_losses = QLabel(f"{t('kpi.consecutive_losses')}: 0")
        self._lbl_consec_losses.setStyleSheet(f"color: {TEXT_SECONDARY}; font-size: 11px;")
        state_layout.addWidget(self._lbl_consec_losses)
        row.addWidget(self._card_state, 2)

        return row

    def _create_broker_hub(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(14)

        self._card_deriv = BrokerCardWidget("Deriv")
        row.addWidget(self._card_deriv)

        self._card_iqoption = BrokerCardWidget("IQ Option")
        row.addWidget(self._card_iqoption)

        return row

    def _create_action_bar(self) -> QFrame:
        bar = QFrame()
        bar.setObjectName("HeaderBar")
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(20, 12, 20, 12)
        layout.setSpacing(12)

        # Safe Stop Emergency Button
        self._btn_safe_stop = SafeStopButton()
        self._btn_safe_stop.safe_stop_triggered.connect(self._on_safe_stop)
        layout.addWidget(self._btn_safe_stop)

        # Resume Button
        self._btn_resume = QPushButton(t("btn.resume"))
        self._btn_resume.setObjectName("PrimaryButton")
        self._btn_resume.clicked.connect(self._on_resume)
        layout.addWidget(self._btn_resume)

        # Diagnostic Export Button
        self._btn_diag = QPushButton("📦 " + t("btn.diagnostic"))
        self._btn_diag.clicked.connect(self._on_export_diagnostic)
        layout.addWidget(self._btn_diag)

        layout.addStretch()

        # Safe Close Button
        self._btn_close = QPushButton("🔒 " + t("btn.safe_close"))
        self._btn_close.clicked.connect(self.close)
        layout.addWidget(self._btn_close)

        return bar

    def _set_language(self, lang: str) -> None:
        I18nManager.set_language(lang)
        self._btn_es.setChecked(lang == "es")
        self._btn_en.setChecked(lang == "en")

    def _on_language_changed(self, lang: str) -> None:
        self.setWindowTitle(t("app.title") + " — " + t("app.practice_badge"))
        self._lbl_badge.setText(t("app.practice_badge"))
        self._lbl_subtitle.setText(t("app.practice_subtitle"))
        self._lbl_pnl_title.setText(t("kpi.daily_pnl"))
        self._lbl_pnl_detail.setText(t("kpi.pnl_detail"))
        self._lbl_state_title.setText(t("kpi.global_state"))
        self._btn_resume.setText(t("btn.resume"))
        self._btn_diag.setText("📦 " + t("btn.diagnostic"))
        self._btn_close.setText("🔒 " + t("btn.safe_close"))
        self._btn_safe_stop.retranslate()
        self._risk_gauge.retranslate()
        self._health_pill_widget.retranslate()
        self._order_table_widget.retranslate()
        self._card_deriv.retranslate()
        self._card_iqoption.retranslate()
        self._deriv_workspace.retranslate()
        self._iqoption_workspace.retranslate()
        self._settings_workspace.retranslate()
        self._retranslate_navigation()
        self._refresh_projection()

    def _retranslate_navigation(self) -> None:
        self._overview_intro.setText(t("overview.intro"))
        self._activity_intro.setText(t("activity.intro"))
        self._main_tabs.setTabText(self._TAB_OVERVIEW, t("tabs.overview"))
        self._main_tabs.setTabText(self._TAB_DERIV, self._deriv_workspace.tab_label())
        self._main_tabs.setTabText(self._TAB_IQ_OPTION, self._iqoption_workspace.tab_label())
        self._main_tabs.setTabText(self._TAB_ACTIVITY, t("tabs.activity"))
        self._main_tabs.setTabText(self._TAB_SETTINGS, t("tabs.settings"))

    def _refresh_projection(self) -> None:
        connected = self._controller.connected
        if connected:
            self._lbl_ipc_status.setText(f"● {t('app.status.connected')}")
            self._lbl_ipc_status.setStyleSheet(
                f"color: {ACCENT_GREEN}; font-weight: bold; font-size: 11px;"
            )
        else:
            self._lbl_ipc_status.setText(f"○ {t('app.status.disconnected')}")
            self._lbl_ipc_status.setStyleSheet(
                f"color: {ACCENT_RED}; font-weight: bold; font-size: 11px;"
            )

        snapshot = self._controller.snapshot
        if snapshot is None:
            return

        # 1. Update Global State
        state_key = f"state.{snapshot.global_state.value}"
        state_text = t(state_key)
        self._lbl_state_val.setText(state_text)
        if snapshot.global_state == UiGlobalState.READY:
            self._lbl_state_val.setStyleSheet(
                f"color: {ACCENT_GREEN}; font-size: 16px; font-weight: bold;"
            )
        elif snapshot.global_state == UiGlobalState.SAFE_STOPPED:
            self._lbl_state_val.setStyleSheet(
                f"color: {ACCENT_RED}; font-size: 16px; font-weight: bold;"
            )
        else:
            self._lbl_state_val.setStyleSheet(
                f"color: {ACCENT_AMBER}; font-size: 16px; font-weight: bold;"
            )

        self._lbl_consec_losses.setText(
            f"{t('kpi.consecutive_losses')}: {snapshot.consecutive_losses}"
        )

        # 2. Update Risk Gauge
        self._risk_gauge.update_gauge(
            snapshot.global_exposure_minor_units,
            snapshot.global_max_exposure_minor_units,
            snapshot.daily_pnl_currency,
            snapshot.risk_state,
        )

        # 3. Update P&L
        pnl_val = snapshot.daily_pnl_minor_units
        pnl_curr = (snapshot.daily_pnl_currency or "USD").upper()
        if pnl_val >= 0:
            self._lbl_pnl_val.setText(format_minor_units(pnl_val, pnl_curr, positive_sign=True))
            self._lbl_pnl_val.setStyleSheet(
                f"color: {ACCENT_GREEN}; font-size: 20px; font-weight: bold;"
            )
        else:
            self._lbl_pnl_val.setText(format_minor_units(pnl_val, pnl_curr))
            self._lbl_pnl_val.setStyleSheet(
                f"color: {ACCENT_RED}; font-size: 20px; font-weight: bold;"
            )

        # 4. Update Broker Cards
        for card_data in snapshot.broker_cards:
            if card_data.broker == "DERIV":
                self._card_deriv.update_card(card_data)
                self._deriv_workspace.update_status(card_data)
            elif card_data.broker == "IQ_OPTION":
                self._card_iqoption.update_card(card_data)
                self._iqoption_workspace.update_status(card_data)
        self._retranslate_navigation()

        # 5. Update Health Gates
        self._health_pill_widget.update_gates(snapshot.health_gates)

        # 6. Update Orders
        self._order_table_widget.update_orders(snapshot.active_orders)
        self._deriv_workspace.update_orders(snapshot.active_orders)
        self._iqoption_workspace.update_orders(snapshot.active_orders)
        self._settings_workspace.update_risk_projection(
            snapshot.global_exposure_minor_units,
            snapshot.global_max_exposure_minor_units,
            snapshot.daily_pnl_currency,
            snapshot.risk_state,
        )

        # 7. Button States
        self._btn_safe_stop.setEnabled(not snapshot.safe_stop_active)
        self._btn_resume.setEnabled(snapshot.safe_stop_active)

    def _on_safe_stop(self) -> None:
        try:
            self._controller.safe_stop()
            self._refresh_projection()
        except Exception as exc:
            QMessageBox.warning(
                self,
                t("error.safe_stop_title"),
                t("error.safe_stop_message", error=str(exc)),
            )

    def _on_resume(self) -> None:
        try:
            self._controller.resume()
            self._refresh_projection()
        except Exception as exc:
            QMessageBox.warning(
                self,
                t("error.resume_title"),
                t("error.resume_message", error=str(exc)),
            )

    def _on_export_diagnostic(self) -> None:
        try:
            resp = self._controller.generate_diagnostic()
            QMessageBox.information(
                self,
                t("diag.title"),
                t(
                    "diag.message",
                    path=resp.bundle_path,
                    size=resp.file_size_bytes,
                    sha256=resp.sha256_hash,
                ),
            )
        except Exception as exc:
            QMessageBox.critical(
                self,
                t("diag.error_title"),
                t("diag.error_message", error=str(exc)),
            )

    def closeEvent(self, event: QCloseEvent) -> None:
        with contextlib.suppress(Exception):
            self._controller.request_safe_close()
        event.accept()
