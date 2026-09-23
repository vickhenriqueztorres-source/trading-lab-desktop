from __future__ import annotations

import contextlib
import json
import subprocess
import sys
import threading
from pathlib import Path

from PySide6.QtCore import QTimer, Signal
from PySide6.QtGui import QCloseEvent, QGuiApplication, QIcon, QResizeEvent
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from apps.ui.components import (
    BrokerCardWidget,
    DerivAssetRadarWidget,
    DerivWorkspaceWidget,
    GlobalRiskGaugeWidget,
    HealthGatePillWidget,
    IqOptionStrategyConfigWidget,
    IqOptionWorkspaceWidget,
    NoScrollConfigFilter,
    ResultsDashboardWidget,
    SettingsWorkspaceWidget,
    SyntheticStrategyConfigWidget,
    SyntheticStrategyLiveWidget,
)
from apps.ui.components.iqoption_workspace import iqoption_bot_reason_text
from apps.ui.controller import UiController
from apps.ui.design import asset_path
from apps.ui.formatting import format_minor_units
from apps.ui.i18n import I18nManager, t
from apps.ui.ipc_client import UiIpcError
from apps.ui.pages import AccountPage, ActivityPage, OverviewPage
from apps.ui.shell import BottomBar, Sidebar, TopBar
from apps.ui.theme import (
    ACCENT_AMBER,
    ACCENT_GREEN,
    ACCENT_RED,
    get_application_stylesheet,
)
from packages.protocol.ui_messages import (
    UiAuthStatusResponse,
    UiDigitRiskConfig,
    UiDigitRiskConfigStatus,
    UiGlobalState,
    UiIqOptionLoginAck,
    UiIqOptionRiskConfig,
    UiProjectionSnapshot,
)
from packages.security import without_broker_credentials

APP_VERSION = "1.9.18"


def _window_title(mode: str) -> str:
    return f"{t('app.title')} v{APP_VERSION} — {mode}"


class _MainTabsCompat:
    """Compatibility shim for headless tests expecting QTabWidget interface."""

    def __init__(self, window: TradingLabMainWindow) -> None:
        self._w = window
        self._tab_texts: dict[int, str] = {}

    def setCurrentIndex(self, index: int) -> None:
        self._w._on_page_selected(index)

    def currentIndex(self) -> int:
        return self._w._pages.currentIndex()

    def count(self) -> int:
        return self._w._pages.count()

    def tabText(self, index: int) -> str:
        if index in self._tab_texts:
            return self._tab_texts[index]
        if index == self._w._PAGE_OVERVIEW:
            return t("tabs.overview")
        if index == self._w._PAGE_DERIV:
            return self._w._deriv_workspace.tab_label()
        if index == self._w._PAGE_IQ_OPTION:
            return self._w._iqoption_workspace.tab_label()
        if index == self._w._PAGE_ACTIVITY:
            return t("tabs.activity")
        if index == self._w._PAGE_ACCOUNT:
            return t("nav.account")
        if index == self._w._PAGE_SETTINGS:
            return t("tabs.settings")
        return ""

    def setTabText(self, index: int, text: str) -> None:
        self._tab_texts[index] = text

    def widget(self, index: int) -> QWidget | None:
        return self._w._pages.widget(index)


class TradingLabMainWindow(QMainWindow):
    """Professional Trading Lab Desktop UI (PySide6 / Qt 6)."""

    _iqoption_saved_login_finished = Signal(object)
    _iqoption_manual_login_finished = Signal(object)

    _PAGE_OVERVIEW = 0
    _PAGE_DERIV = 1
    _PAGE_IQ_OPTION = 2
    _PAGE_ACTIVITY = 3
    _PAGE_ACCOUNT = 4
    _PAGE_SETTINGS = 5

    # Backwards-compatibility aliases for existing contract tests
    _TAB_OVERVIEW = _PAGE_OVERVIEW
    _TAB_DERIV = _PAGE_DERIV
    _TAB_IQ_OPTION = _PAGE_IQ_OPTION
    _TAB_STRATEGIES = 3
    _TAB_ACTIVITY = _PAGE_ACTIVITY
    _TAB_SETTINGS = _PAGE_SETTINGS

    def __init__(
        self,
        controller: UiController,
        parent: QWidget | None = None,
        *,
        profile_dir: Path | None = None,
    ) -> None:
        super().__init__(parent)
        self._controller = controller
        self._profile_dir = Path(profile_dir or "data/profiles/default")
        self._bot_enabled = False
        self._iqoption_bot_enabled = False
        self._deriv_real_selected = False
        self._iqoption_saved_login_started = False
        self._auth_status: UiAuthStatusResponse | None = None
        self._auth_poll_counter = 0

        self.setWindowTitle(_window_title(t("app.practice_badge")))
        app_icon_file = asset_path("app.ico")
        if app_icon_file.is_file():
            self.setWindowIcon(QIcon(str(app_icon_file)))
        self.resize(1180, 780)
        self.setMinimumSize(960, 640)

        # State cache and flags
        self._last_snapshot: UiProjectionSnapshot | None = None
        self._last_snapshot_sig: tuple | None = None
        self._last_connected: bool | None = None
        self._last_auth_status: object | None = None
        self._initial_refresh_done = False

        # Apply dark theme
        self.setStyleSheet(get_application_stylesheet())

        self._build_ui()
        self._fit_to_available_screen()

        # Prevent mouse wheel from inadvertently altering configuration inputs
        q_app = QApplication.instance()
        if q_app is not None:
            self._no_scroll_filter = NoScrollConfigFilter(self)
            q_app.installEventFilter(self._no_scroll_filter)

        # Timer for polling IPC projection
        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._refresh_projection)
        self._timer.start()

        self._iqoption_saved_login_finished.connect(self._on_iqoption_saved_login_finished)
        self._iqoption_manual_login_finished.connect(self._on_iqoption_manual_login_finished)
        QTimer.singleShot(300, self._start_iqoption_saved_login)

        # Subscribe to language changes
        I18nManager.subscribe(self._on_language_changed)

    def _fit_to_available_screen(self) -> None:
        screen = self.screen() or QGuiApplication.primaryScreen()
        if screen is None:
            return
        available = screen.availableGeometry()
        margin = 8
        max_width = max(self.minimumWidth(), available.width() - margin)
        max_height = max(self.minimumHeight(), available.height() - margin)
        width = min(self.width(), max_width)
        height = min(self.height(), max_height)
        x = available.x() + max(0, (available.width() - width) // 2)
        y = available.y() + max(0, (available.height() - height) // 2)
        self.setGeometry(x, y, width, height)

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        is_compact = event.size().width() < 1050 or event.size().height() < 720
        if hasattr(self, "_sidebar") and hasattr(self._sidebar, "set_compact_mode"):
            self._sidebar.set_compact_mode(is_compact)
        if hasattr(self, "_overview_page") and hasattr(self._overview_page, "set_compact_mode"):
            self._overview_page.set_compact_mode(is_compact)

    def _build_ui(self) -> None:
        central_widget = QWidget(self)
        central_widget.setObjectName("root")
        self.setCentralWidget(central_widget)

        root_layout = QHBoxLayout(central_widget)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        # 1. Left Sidebar (fixed 220px)
        self._sidebar = Sidebar(self)
        self._sidebar.page_selected.connect(self._on_page_selected)
        root_layout.addWidget(self._sidebar)

        # 2. Right Content Column (TopBar + QStackedWidget + BottomBar)
        content_column = QWidget()
        content_layout = QVBoxLayout(content_column)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)

        # TopBar (fixed 56px)
        self._topbar = TopBar(self)
        self._topbar.account_clicked.connect(lambda: self._on_page_selected(self._PAGE_ACCOUNT))

        # Language switcher inside TopBar
        lang_container = QWidget()
        lang_box = QHBoxLayout(lang_container)
        lang_box.setContentsMargins(0, 0, 0, 0)
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

        self._topbar.add_right_widget(lang_container)
        content_layout.addWidget(self._topbar)

        # Pages Stacked Widget (wrapped with balanced margins)
        pages_container = QWidget()
        pages_container.setObjectName("PagesContainer")
        pages_container.setStyleSheet("background-color: transparent;")
        pages_layout = QVBoxLayout(pages_container)
        pages_layout.setContentsMargins(16, 12, 16, 12)
        pages_layout.setSpacing(0)

        self._pages = QStackedWidget(self)
        self._pages.setObjectName("MainPages")

        # Page 0: Overview
        self._overview_page = OverviewPage()
        self._overview_page.configure_clicked.connect(
            lambda: self._on_page_selected(self._PAGE_SETTINGS)
        )
        self._overview_page.bot_toggle_clicked.connect(self._on_overview_bot_toggle)
        self._overview_page.deriv_bot_toggle_clicked.connect(self._on_toggle_bot)
        self._overview_page.iqoption_bot_toggle_clicked.connect(self._on_toggle_iqoption_bot)
        self._pages.addWidget(self._overview_page)

        # Page 1: Deriv Workspace
        self._deriv_workspace = DerivWorkspaceWidget()
        self._deriv_workspace.deriv_demo_connect_requested.connect(self._on_connect_deriv_demo)
        self._deriv_workspace.safe_stop_requested.connect(self._on_safe_stop)
        self._deriv_workspace.bot_toggle_requested.connect(self._on_toggle_bot)
        self._synthetic_config_panel = SyntheticStrategyConfigWidget()
        self._asset_radar_panel = DerivAssetRadarWidget()
        self._synthetic_live_panel = SyntheticStrategyLiveWidget()
        self._deriv_workspace.set_configuration_widget(self._synthetic_config_panel)
        self._deriv_workspace.set_live_widget(self._asset_radar_panel)
        self._deriv_workspace.set_live_widget(self._synthetic_live_panel)
        self._deriv_workspace.strategy_selected.connect(self._on_strategy_selected)
        self._synthetic_config_panel.config_apply_requested.connect(
            self._on_digit_risk_config_apply
        )
        self._synthetic_config_panel.test_session_reset_requested.connect(
            self._on_reset_digit_test_session
        )
        self._pages.addWidget(self._deriv_workspace)

        # Page 2: IQ Option Workspace
        self._iqoption_workspace = IqOptionWorkspaceWidget()
        self._iqoption_workspace.set_controller(self._controller)
        self._iqoption_workspace.iqoption_login_requested.connect(self._on_iqoption_login)
        self._iqoption_workspace.safe_stop_requested.connect(self._on_safe_stop)
        self._iqoption_workspace.iqoption_bot_toggle_requested.connect(self._on_toggle_iqoption_bot)
        self._iqoption_config_panel = IqOptionStrategyConfigWidget()
        self._iqoption_config_panel.config_apply_requested.connect(
            self._on_iqoption_risk_config_apply
        )
        self._iqoption_workspace.add_configuration_widget(self._iqoption_config_panel)
        self._pages.addWidget(self._iqoption_workspace)

        # Page 3: Activity
        self._pages.addWidget(self._create_activity_page())

        # Page 4: Account
        self._account_page = AccountPage()
        self._account_page.sign_out_requested.connect(self._on_sign_out_requested)
        self._pages.addWidget(self._account_page)

        # Page 5: Settings
        self._settings_workspace = SettingsWorkspaceWidget()
        self._settings_workspace.diagnostic_requested.connect(self._on_export_diagnostic)
        self._pages.addWidget(self._settings_workspace)

        pages_layout.addWidget(self._pages)
        content_layout.addWidget(pages_container, 1)

        # BottomBar (fixed 72px)
        self._bottombar = BottomBar(self)

        # Action Buttons (managed by BottomBar)
        self._btn_deriv_bot = QPushButton()
        self._btn_deriv_bot.clicked.connect(self._on_toggle_bot)
        self._btn_bot = self._btn_deriv_bot
        self._btn_iqoption_bot = QPushButton()
        self._btn_iqoption_bot.clicked.connect(self._on_toggle_iqoption_bot)
        self._update_bot_buttons()

        self._btn_diag = QPushButton("📦 " + t("btn.diagnostic"))
        self._btn_diag.setObjectName("secondary")
        self._btn_diag.clicked.connect(self._on_export_diagnostic)

        self._btn_close = QPushButton("🔒 " + t("btn.safe_close"))
        self._btn_close.setObjectName("danger")
        self._btn_close.clicked.connect(self.close)

        self._bottombar.add_permanent_action(self._btn_diag)
        self._bottombar.add_permanent_action(self._btn_close)
        content_layout.addWidget(self._bottombar)

        root_layout.addWidget(content_column, 1)

        # Backwards compatibility attributes for headless test contracts
        self._lbl_ipc_status = QLabel(f"● {t('app.status.connected')}")
        self._lbl_version = QLabel(f"v{APP_VERSION}  ·  DIGIT EDGE")
        self._lbl_badge = QLabel(t("app.practice_badge"))
        self._lbl_subtitle = QLabel(t("app.practice_subtitle"))
        self._main_tabs = _MainTabsCompat(self)
        self._lbl_pnl_val = self._overview_page._lbl_net_profit_val
        self._card_deriv = BrokerCardWidget("Deriv")
        self._card_iqoption = BrokerCardWidget("IQ Option")
        self._health_pill_widget = HealthGatePillWidget()
        self._results_dashboard = ResultsDashboardWidget()
        self._risk_gauge = GlobalRiskGaugeWidget()
        self._lbl_pnl_title = QLabel()
        self._lbl_pnl_detail = QLabel()
        self._lbl_state_title = QLabel()
        self._lbl_state_val = QLabel()
        self._lbl_consec_losses = QLabel()
        self._overview_intro = QLabel()

        self._on_page_selected(0)
        self._retranslate_navigation()
        QTimer.singleShot(150, self._maybe_show_onboarding)

    def _maybe_show_onboarding(self) -> None:
        import os

        if os.environ.get("QT_QPA_PLATFORM") == "offscreen":
            return
        from apps.ui.onboarding import FirstRunDialog, is_onboarding_done

        if not is_onboarding_done(self._profile_dir):
            dialog = FirstRunDialog(self._profile_dir, parent=self)
            dialog.exec()

    def _on_overview_bot_toggle(self) -> None:
        if self._overview_page._active_broker == "IQ Option":
            self._on_toggle_iqoption_bot()
        else:
            self._on_toggle_bot()

    def _create_overview_page(self) -> QWidget:
        return self._overview_page

    def _create_activity_page(self) -> QWidget:
        self._activity_page = ActivityPage()
        self._activity_page.set_controller(self._controller)
        self._order_table_widget = self._activity_page.order_table
        self._log_terminal = self._activity_page.log_terminal
        self._activity_tabs = self._activity_page.tabs
        self._activity_intro = self._activity_page.intro_label
        return self._activity_page

    def _on_sign_out_requested(self) -> None:
        answer = QMessageBox.question(
            self,
            t("account.sign_out_confirm_title"),
            t("account.sign_out_confirm"),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        with contextlib.suppress(Exception):
            self._controller.auth_sign_out()
        self._auth_status = None
        self._topbar.set_account_info(None, None)
        self._sidebar.set_account_info(None, None)
        self._account_page.update_auth_status(None)

        from apps.ui.auth import LoginWindow

        self.hide()
        login_dialog = LoginWindow(self._controller, self)
        if login_dialog.exec() == 1:
            with contextlib.suppress(Exception):
                self._auth_status = self._controller.auth_status()
            if self._auth_status:
                self._topbar.set_account_info(
                    self._auth_status.user_id_preview, self._auth_status.plan
                )
                self._sidebar.set_account_info(
                    self._auth_status.user_id_preview, self._auth_status.plan
                )
                self._account_page.update_auth_status(self._auth_status)
            self.show()
        else:
            self.close()

    def _on_page_selected(self, index: int) -> None:
        if not (0 <= index < self._pages.count()):
            return
        if (
            self._pages.currentIndex() == self._PAGE_IQ_OPTION
            and index != self._PAGE_IQ_OPTION
            and hasattr(self, "_iqoption_config_panel")
            and hasattr(self._iqoption_config_panel, "has_unsaved_changes")
            and self._iqoption_config_panel.has_unsaved_changes()
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
                self._iqoption_config_panel.save_changes()
            elif clicked == btn_discard:
                self._iqoption_config_panel.discard_unsaved_changes()
            else:
                self._sidebar.set_current_page(self._PAGE_IQ_OPTION)
                return
        self._pages.setCurrentIndex(index)
        self._sidebar.set_current_page(index)
        self._update_topbar_title(index)
        self._update_bottombar_primary_action(index)
        if self._last_snapshot is not None:
            self._update_page(index, self._last_snapshot)

    def _update_topbar_title(self, index: int | None = None) -> None:
        if index is None:
            index = self._pages.currentIndex()
        if index == self._PAGE_OVERVIEW:
            self._topbar.set_title(t("nav.overview"), "nav.overview")
        elif index == self._PAGE_DERIV:
            self._topbar.set_title(t("page.deriv.pro_title"), "page.deriv.pro_title")
        elif index == self._PAGE_IQ_OPTION:
            self._topbar.set_title(t("page.iqoption.pro_title"), "page.iqoption.pro_title")
        elif index == self._PAGE_ACTIVITY:
            self._topbar.set_title(t("nav.activity"), "nav.activity")
        elif index == self._PAGE_ACCOUNT:
            self._topbar.set_title(t("nav.account"), "nav.account")
        elif index == self._PAGE_SETTINGS:
            self._topbar.set_title(t("nav.settings"), "nav.settings")

    def _update_bottombar_primary_action(self, index: int) -> None:
        if index == self._PAGE_OVERVIEW:
            self._bottombar.set_primary_action(self._overview_page.primary_action_btn)
        elif index == self._PAGE_DERIV:
            self._bottombar.set_primary_action(self._btn_deriv_bot)
        elif index == self._PAGE_IQ_OPTION:
            self._bottombar.set_primary_action(self._btn_iqoption_bot)
        else:
            self._bottombar.set_primary_action(None)

    def _set_language(self, lang: str) -> None:
        I18nManager.set_language(lang)
        self._btn_es.setChecked(lang == "es")
        self._btn_en.setChecked(lang == "en")

    def _on_language_changed(self, lang: str) -> None:
        self._last_snapshot = None
        self._last_connected = None
        self._last_auth_status = None
        self._initial_refresh_done = False
        self.setWindowTitle(_window_title(t("app.practice_badge")))
        self._lbl_badge.setText(t("app.practice_badge"))
        self._lbl_subtitle.setText(t("app.practice_subtitle"))
        self._lbl_pnl_title.setText(t("kpi.daily_pnl"))
        self._lbl_pnl_detail.setText(t("kpi.pnl_detail"))
        self._lbl_state_title.setText(t("kpi.global_state"))
        self._btn_diag.setText("📦 " + t("btn.diagnostic"))
        self._btn_close.setText("🔒 " + t("btn.safe_close"))
        self._btn_es.setChecked(lang == "es")
        self._btn_en.setChecked(lang == "en")
        self._update_bot_buttons()
        self._risk_gauge.retranslate()
        self._health_pill_widget.retranslate()
        self._order_table_widget.retranslate()
        self._log_terminal.retranslate()
        self._results_dashboard.retranslate()
        self._card_deriv.retranslate()
        self._card_iqoption.retranslate()
        self._deriv_workspace.retranslate()
        self._asset_radar_panel.retranslate()
        self._iqoption_workspace.retranslate()
        self._iqoption_config_panel.retranslate()
        self._settings_workspace.retranslate()
        self._activity_page.retranslate()
        self._account_page.retranslate()
        self._overview_page.retranslate()
        self._retranslate_navigation()
        self._refresh_projection()

    def _retranslate_navigation(self) -> None:
        self._overview_intro.setText(t("overview.intro"))
        self._activity_intro.setText(t("activity.intro"))
        self._main_tabs.setTabText(self._PAGE_OVERVIEW, t("tabs.overview"))
        self._main_tabs.setTabText(self._PAGE_DERIV, self._deriv_workspace.tab_label())
        self._main_tabs.setTabText(self._PAGE_IQ_OPTION, self._iqoption_workspace.tab_label())
        self._main_tabs.setTabText(self._PAGE_ACTIVITY, t("tabs.activity"))
        self._main_tabs.setTabText(self._PAGE_ACCOUNT, t("nav.account"))
        self._main_tabs.setTabText(self._PAGE_SETTINGS, t("tabs.settings"))
        self._activity_tabs.setTabText(0, t("activity.orders_tab"))
        self._activity_tabs.setTabText(1, t("activity.logs_tab"))
        self._sidebar.retranslate()
        self._topbar.retranslate()
        self._bottombar.retranslate()
        self._update_topbar_title()

    @staticmethod
    def _set_style_if_changed(widget: QWidget, style: str) -> None:
        if widget.styleSheet() != style:
            widget.setStyleSheet(style)

    def _refresh_projection(self) -> None:
        connected = self._controller.connected
        if connected != self._last_connected:
            self._topbar.set_core_connected(connected)
            if connected:
                self._lbl_ipc_status.setText(f"● {t('app.status.connected')}")
                self._set_style_if_changed(
                    self._lbl_ipc_status,
                    f"color: {ACCENT_GREEN}; font-weight: bold; font-size: 11px;",
                )
            else:
                self._lbl_ipc_status.setText(f"○ {t('app.status.disconnected')}")
                self._set_style_if_changed(
                    self._lbl_ipc_status,
                    f"color: {ACCENT_RED}; font-weight: bold; font-size: 11px;",
                )

        # 0. Check background-cached auth status (never block the Qt GUI thread with socket I/O)
        new_auth = getattr(self._controller, "cached_auth_status", None)
        if new_auth is not None and new_auth != self._last_auth_status:
            self._auth_status = new_auth
            self._last_auth_status = new_auth
            user_preview = getattr(self._auth_status, "user_id_preview", "")
            plan = getattr(self._auth_status, "plan", "")
            if isinstance(user_preview, str) and isinstance(plan, str):
                self._topbar.set_account_info(user_preview, plan)
                self._sidebar.set_account_info(user_preview, plan)
                self._account_page.update_auth_status(self._auth_status)

        snapshot = self._controller.snapshot
        snapshot_sig = snapshot.semantic_signature() if snapshot is not None else None
        if snapshot_sig == self._last_snapshot_sig and connected == self._last_connected:
            return

        self._last_connected = connected
        self._last_snapshot = snapshot
        self._last_snapshot_sig = snapshot_sig

        if snapshot is None:
            self._overview_page.update_projection(None, self._controller)
            self._bottombar.set_system_ready(False)
            return

        self._bottombar.set_system_ready(snapshot.global_state == UiGlobalState.READY)

        # 1. Update Global State
        state_key = f"state.{snapshot.global_state.value}"
        state_text = t(state_key)
        self._lbl_state_val.setText(state_text)
        if snapshot.global_state == UiGlobalState.READY:
            self._set_style_if_changed(
                self._lbl_state_val,
                f"color: {ACCENT_GREEN}; font-size: 16px; font-weight: bold;",
            )
        elif snapshot.global_state == UiGlobalState.SAFE_STOPPED:
            self._set_style_if_changed(
                self._lbl_state_val,
                f"color: {ACCENT_RED}; font-size: 16px; font-weight: bold;",
            )
        else:
            self._set_style_if_changed(
                self._lbl_state_val,
                f"color: {ACCENT_AMBER}; font-size: 16px; font-weight: bold;",
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
            self._set_style_if_changed(
                self._lbl_pnl_val,
                f"color: {ACCENT_GREEN}; font-size: 20px; font-weight: bold;",
            )
        else:
            self._lbl_pnl_val.setText(format_minor_units(pnl_val, pnl_curr))
            self._set_style_if_changed(
                self._lbl_pnl_val,
                f"color: {ACCENT_RED}; font-size: 20px; font-weight: bold;",
            )

        # 4. Update Broker Cards
        for card_data in snapshot.broker_cards:
            if card_data.broker == "DERIV":
                self._deriv_real_selected = card_data.account_mode.value == "REAL"
                self._card_deriv.update_card(card_data)
                self._deriv_workspace.update_status(card_data)
                self._main_tabs.setTabText(self._PAGE_DERIV, self._deriv_workspace.tab_label())
                if card_data.account_mode.value == "REAL":
                    self.setWindowTitle(_window_title(t("mode.REAL")))
                    self._lbl_badge.setText(t("mode.REAL"))
                    self._set_style_if_changed(
                        self._lbl_badge,
                        f"background: {ACCENT_RED}; color: white; font-weight: 900; padding: 5px;",
                    )
                else:
                    self.setWindowTitle(_window_title(t("app.practice_badge")))
                    self._lbl_badge.setText(t("app.practice_badge"))
                    self._set_style_if_changed(self._lbl_badge, "")
            elif card_data.broker == "IQOPTION":
                self._card_iqoption.update_card(card_data)
                self._iqoption_workspace.update_status(card_data)
                self._main_tabs.setTabText(
                    self._PAGE_IQ_OPTION, self._iqoption_workspace.tab_label()
                )

        # 5. Update Health Gates
        self._health_pill_widget.update_gates(snapshot.health_gates)

        # 6. Update Workspace Pages (Full on initial load; active page only on subsequent ticks)
        if not self._initial_refresh_done:
            self._initial_refresh_done = True
            for page_idx in range(self._pages.count()):
                self._update_page(page_idx, snapshot)
        else:
            self._update_page(self._pages.currentIndex(), snapshot)

        # 7. Each broker state is authoritative from its own Core projection.
        self._bot_enabled = snapshot.deriv_bot_armed
        self._iqoption_bot_enabled = snapshot.iqoption_bot_armed
        self._btn_deriv_bot.setEnabled(connected)
        self._btn_iqoption_bot.setEnabled(connected)
        self._btn_iqoption_bot.setToolTip(
            iqoption_bot_reason_text(
                snapshot.iqoption_entry_blocker or snapshot.iqoption_bot_reason
            )
        )
        self._update_bot_buttons()
        deriv_connected = any(
            card.broker == "DERIV" and card.is_connected for card in snapshot.broker_cards
        )
        self._deriv_workspace.update_automation_state(
            self._bot_enabled,
            deriv_connected,
            self._deriv_real_selected,
            snapshot.deriv_bot_reason,
            snapshot.deriv_bot_waiting_status,
        )

    def _update_page(self, index: int, snapshot: UiProjectionSnapshot) -> None:
        if index == self._PAGE_OVERVIEW:
            self._overview_page.update_projection(snapshot, self._controller)
        elif index == self._PAGE_DERIV:
            self._deriv_workspace.update_orders(snapshot.active_orders)
            self._deriv_workspace.update_risk(
                snapshot.global_exposure_minor_units,
                snapshot.global_max_exposure_minor_units,
                snapshot.daily_pnl_currency,
                snapshot.risk_state,
                snapshot.consecutive_losses,
                snapshot.digit_risk_config,
                snapshot.cooldown_remaining_seconds,
                snapshot.digit_martingale_step,
                snapshot.digit_next_stake_minor_units,
                snapshot.digit_projected_sequence_loss_minor_units,
            )
            self._deriv_workspace.update_strategy_statuses(snapshot.deriv_strategies)
            self._synthetic_live_panel.update_statuses(snapshot.deriv_strategies)
            self._asset_radar_panel.update_ranking(snapshot.deriv_asset_ranking)
            if snapshot.digit_risk_config is not None:
                self._deriv_workspace.set_execution_strategy(
                    snapshot.digit_risk_config.active_strategy_id
                )
                self._synthetic_config_panel.set_strategy(
                    snapshot.digit_risk_config.active_strategy_id
                )
                self._synthetic_live_panel.set_strategy(
                    snapshot.digit_risk_config.active_strategy_id
                )
                self._synthetic_config_panel.set_risk_config(snapshot.digit_risk_config)
            self._synthetic_config_panel.set_cooldown_remaining(snapshot.cooldown_remaining_seconds)
        elif index == self._PAGE_IQ_OPTION:
            self._iqoption_workspace.update_orders(snapshot.active_orders)
            self._iqoption_workspace.update_iqoption_radar(snapshot.iqoption_asset_ranking)
            self._iqoption_config_panel.set_available_assets(snapshot.iqoption_asset_ranking)
            self._iqoption_workspace.update_iqoption_risk(snapshot.iqoption_risk_config)
            self._iqoption_workspace.update_iqoption_metrics(snapshot.iqoption_execution_metrics)
            self._iqoption_workspace.update_bot_state(
                snapshot.iqoption_bot_armed,
                snapshot.iqoption_bot_reason,
                entry_ready=snapshot.iqoption_entry_ready,
                entry_blocker=snapshot.iqoption_entry_blocker,
            )
            if snapshot.iqoption_risk_config is not None:
                self._iqoption_config_panel.set_config(snapshot.iqoption_risk_config)
            iq_card = next(
                (c for c in snapshot.broker_cards if c.broker in {"IQ_OPTION", "IQOPTION"}),
                None,
            )
            self._iqoption_config_panel.set_account_type(
                "UNKNOWN" if iq_card is None else iq_card.account_mode.value
            )
        elif index == self._PAGE_ACTIVITY:
            self._activity_page.update_orders(snapshot.active_orders)
            self._log_terminal.update_entries(snapshot.operational_logs)
            self._results_dashboard.update_results(snapshot.active_orders)
        elif index == self._PAGE_ACCOUNT:
            if self._auth_status is not None:
                self._account_page.update_auth_status(self._auth_status)
        elif index == self._PAGE_SETTINGS:
            self._settings_workspace.update_risk_projection(
                snapshot.global_exposure_minor_units,
                snapshot.global_max_exposure_minor_units,
                snapshot.daily_pnl_currency,
                snapshot.risk_state,
            )

    def _update_bot_buttons(self) -> None:
        deriv_text = t("btn.bot.deriv.stop") if self._bot_enabled else t("btn.bot.deriv.start")
        deriv_obj = "SafeStopButton" if self._bot_enabled else "BotStartButton"
        if self._btn_deriv_bot.text() != deriv_text:
            self._btn_deriv_bot.setText(deriv_text)
        if self._btn_deriv_bot.objectName() != deriv_obj:
            self._btn_deriv_bot.setObjectName(deriv_obj)
            self._btn_deriv_bot.style().unpolish(self._btn_deriv_bot)
            self._btn_deriv_bot.style().polish(self._btn_deriv_bot)

        iq_text = t("btn.bot.iq.stop") if self._iqoption_bot_enabled else t("btn.bot.iq.start")
        iq_obj = "SafeStopButton" if self._iqoption_bot_enabled else "BotStartButton"
        if self._btn_iqoption_bot.text() != iq_text:
            self._btn_iqoption_bot.setText(iq_text)
        if self._btn_iqoption_bot.objectName() != iq_obj:
            self._btn_iqoption_bot.setObjectName(iq_obj)
            self._btn_iqoption_bot.style().unpolish(self._btn_iqoption_bot)
            self._btn_iqoption_bot.style().polish(self._btn_iqoption_bot)

        if hasattr(self, "_deriv_workspace") and hasattr(
            self._deriv_workspace, "update_context_bot_toggle"
        ):
            self._deriv_workspace.update_context_bot_toggle(self._bot_enabled)

    def _update_bot_button(self) -> None:
        """Compatibility shim retained for existing UI tests."""
        self._update_bot_buttons()

    def _on_toggle_bot(self) -> None:
        if self._bot_enabled:
            self._on_safe_stop()
            return
        if self._deriv_real_selected:
            QMessageBox.warning(
                self,
                t("bot.real.confirm_title"),
                t("bot.real.confirm_message"),
            )
            return
        self._on_resume()

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

    def _on_toggle_iqoption_bot(self) -> None:
        try:
            if (
                not self._iqoption_bot_enabled
                and hasattr(self, "_iqoption_config_panel")
                and hasattr(self._iqoption_config_panel, "has_unsaved_changes")
                and self._iqoption_config_panel.has_unsaved_changes()
            ):
                self._iqoption_config_panel.save_changes()

            ack = self._controller.control_iqoption_bot(not self._iqoption_bot_enabled)
            self._refresh_projection()
            automatic_recovery = {
                "HG_ORDER_UNKNOWN",
                "HG_RECONCILIATION_REQUIRED",
                "HG_RECONCILIATION_UNAVAILABLE",
                "HG_SETTLEMENT_UNKNOWN",
            }
            if not ack.accepted and ack.reason_code not in automatic_recovery:
                QMessageBox.warning(
                    self,
                    "IQ Option",
                    t("error.resume_blocked_message", reason=ack.reason_code),
                )
        except Exception as exc:
            QMessageBox.warning(self, "IQ Option", str(exc))

    def _on_iqoption_risk_config_apply(self, config: UiIqOptionRiskConfig) -> None:
        try:
            was_armed = self._iqoption_bot_enabled
            if was_armed:
                self._controller.control_iqoption_bot(False)
            ack = self._controller.update_iqoption_risk_config(config)
            if ack.accepted and was_armed:
                rearm_ack = self._controller.control_iqoption_bot(True)
                self._iqoption_config_panel.set_apply_result(
                    rearm_ack.accepted,
                    rearm_ack.reason_code,
                    rearmed=rearm_ack.accepted,
                )
            else:
                self._iqoption_config_panel.set_apply_result(
                    ack.accepted,
                    ack.reason_code,
                    rearmed=False,
                )
            self._refresh_projection()
        except Exception as exc:
            self._iqoption_config_panel.set_apply_result(False, str(exc)[:64])

    def _on_resume(self) -> None:
        try:
            ack = self._controller.resume()
            self._refresh_projection()
            if not ack.accepted:
                QMessageBox.warning(
                    self,
                    t("error.resume_title"),
                    t("error.resume_blocked_message", reason=ack.reason_code),
                )
        except Exception as exc:
            QMessageBox.warning(
                self,
                t("error.resume_title"),
                t("error.resume_message", error=str(exc)),
            )

    def _on_strategy_selected(self, strategy_id: str) -> None:
        """Changing strategy is an execution change, so it always disarms first."""

        if self._bot_enabled:
            self._on_safe_stop()
        self._synthetic_config_panel.set_strategy(
            strategy_id,
            apply_execution_selection=True,
        )
        self._synthetic_live_panel.set_strategy(strategy_id)

    def _on_digit_risk_config_apply(self, config: UiDigitRiskConfig) -> None:
        try:
            if self._bot_enabled:
                self._on_safe_stop()
            ack = self._controller.update_digit_risk_config(config)
            accepted = ack.status is UiDigitRiskConfigStatus.OK
            self._synthetic_config_panel.set_apply_result(accepted, ack.reason_code)
            self._refresh_projection()
        except Exception as exc:
            self._synthetic_config_panel.set_apply_result(False, str(exc)[:64])

    def _on_reset_digit_test_session(self) -> None:
        answer = QMessageBox.question(
            self,
            t("demo.reset.title"),
            t("demo.reset.confirm"),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            ack = self._controller.reset_digit_test_session()
            self._refresh_projection()
            if ack.accepted:
                QMessageBox.information(self, t("demo.reset.title"), t("demo.reset.success"))
            else:
                QMessageBox.warning(
                    self,
                    t("demo.reset.title"),
                    t("demo.reset.rejected", reason=ack.reason_code),
                )
        except Exception as exc:
            QMessageBox.warning(
                self,
                t("demo.reset.title"),
                t("demo.reset.rejected", reason=str(exc)[:64]),
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

    def _on_connect_deriv_demo(self) -> None:
        self._deriv_workspace.set_deriv_connect_busy(True, "Abrindo conexão protegida…")
        command = [
            sys.executable,
            "-m",
            "apps.deriv_login_helper",
            "--vault-dir",
            str(self._profile_dir / "broker_credentials"),
        ]
        try:
            helper_cwd = (
                Path(sys.executable).resolve().parent
                if getattr(sys, "frozen", False)
                else Path(__file__).resolve().parents[2]
            )

            result = subprocess.run(
                command,
                cwd=helper_cwd,
                env=without_broker_credentials(),
                capture_output=True,
                text=True,
                check=False,
            )
            if result.returncode != 0:
                self._deriv_workspace.set_deriv_connect_busy(False)
                return
            response = json.loads(result.stdout.strip().splitlines()[-1])
            if response != {"status": "saved"}:
                raise ValueError("DERIV_LOGIN_HELPER_INVALID")
            self._deriv_workspace.set_deriv_connect_busy(True, "Conectando à Deriv…")
            ack = self._controller.connect_deriv_demo()
            if not ack.accepted:
                raise RuntimeError(ack.reason_code)
            self._deriv_workspace.set_deriv_connect_busy(
                False, "Conta Deriv conectada com segurança."
            )
            self._refresh_projection()
        except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
            self._deriv_workspace.set_deriv_connect_busy(
                False, "Não foi possível conectar. Confira os dados e tente novamente."
            )
            internal_channel_error = isinstance(exc, UiIpcError)
            reason_code = str(exc)
            friendly_reasons = {
                "DERIV_CONNECTION_TIMEOUT": (
                    "A Deriv demorou para responder. O aplicativo repetiu a conexão "
                    "automaticamente, mas o limite de tempo foi atingido."
                ),
                "DERIV_NETWORK_ERROR": (
                    "A conexão de internet com a Deriv ficou indisponível durante a autenticação."
                ),
                "DERIV_AUTH_FAILED": (
                    "A Deriv recusou o token. Gere um PAT com as permissões Ler e Operar."
                ),
                "DERIV_DEMO_ACCOUNT_NOT_FOUND": (
                    "A conta Demo escolhida não pertence ao token informado."
                ),
                "DERIV_ACCOUNT_TYPE_MISMATCH": (
                    "O tipo de conta escolhido não corresponde à conta autorizada pelo token."
                ),
            }
            error_message = (
                "A conta foi salva, mas o canal interno não respondeu. Feche e abra o "
                "Trading Lab; a credencial protegida será reutilizada."
                if internal_channel_error
                else friendly_reasons.get(
                    reason_code,
                    "A conta não foi confirmada. Verifique o token, a permissão trade "
                    "e a internet.",
                )
            )
            QMessageBox.warning(
                self,
                "Falha ao conectar à Deriv",
                f"{error_message}\n\nCódigo: {exc}",
            )

    def _on_iqoption_login(self) -> None:
        self._iqoption_workspace.set_iqoption_login_busy(True, "Abrindo conexão protegida…")
        connection_started = False
        command = [
            sys.executable,
            "-m",
            "apps.iqoption_login_helper",
            "--vault-dir",
            str(self._profile_dir / "broker_credentials"),
        ]
        try:
            helper_cwd = (
                Path(sys.executable).resolve().parent
                if getattr(sys, "frozen", False)
                else Path(__file__).resolve().parents[2]
            )
            result = subprocess.run(
                command,
                cwd=helper_cwd,
                env=without_broker_credentials(),
                capture_output=True,
                text=True,
                check=False,
            )
            if result.returncode != 0:
                return
            response = json.loads(result.stdout.strip().splitlines()[-1])
            if not isinstance(response, dict) or response.get("status") != "saved":
                raise ValueError("IQOPTION_LOGIN_HELPER_INVALID")
            account_mode = response.get("account_mode")
            if account_mode not in {"practice", "real"}:
                raise ValueError("IQOPTION_LOGIN_HELPER_INVALID_MODE")
            connection_started = True
            self._iqoption_workspace.set_iqoption_login_busy(
                True,
                "Conectando à IQ Option… A interface continuará disponível.",
            )

            def connect() -> None:
                try:
                    result: object = self._controller.login_iqoption(account_mode)
                except (OSError, RuntimeError, ValueError, UiIpcError) as exc:
                    result = exc
                self._iqoption_manual_login_finished.emit((account_mode, result))

            threading.Thread(
                target=connect,
                name="iqoption-manual-login",
                daemon=True,
            ).start()
        except (OSError, RuntimeError, ValueError, json.JSONDecodeError, UiIpcError) as exc:
            self._iqoption_workspace.set_iqoption_login_status(
                "Não foi possível processar as credenciais. Tente novamente."
            )
            QMessageBox.warning(self, "Login IQ Option", str(exc))
        finally:
            if not connection_started:
                self._iqoption_workspace.set_iqoption_login_busy(False)

    def _on_iqoption_manual_login_finished(self, result: object) -> None:
        self._iqoption_workspace.set_iqoption_login_busy(False)
        if not isinstance(result, tuple) or len(result) != 2:
            self._iqoption_workspace.set_iqoption_login_status(
                "A conexão terminou sem uma resposta válida do Core."
            )
            return
        account_mode, outcome = result
        if isinstance(outcome, UiIqOptionLoginAck) and outcome.connected:
            self._refresh_projection()
            connected_message = (
                "IQ Option Practice conectada."
                if account_mode == "practice"
                else "IQ Option Real conectada em modo somente leitura."
            )
            self._iqoption_workspace.set_iqoption_login_status(connected_message)
            QMessageBox.information(self, "IQ Option", connected_message)
            return

        reason_code = (
            outcome.reason_code if isinstance(outcome, UiIqOptionLoginAck) else str(outcome)
        )
        error_message = {
            "IQOPTION_AUTH_FAILED": "E-mail ou senha recusados pela IQ Option.",
            "IQOPTION_2FA_REQUIRED": "A conta exige autenticação em dois fatores.",
            "IQOPTION_RATE_LIMITED": "Muitas tentativas. Aguarde e tente novamente.",
            "IQOPTION_CONNECTION_QUARANTINED": (
                "Reconexão automática em andamento. O bot continua armado; "
                "use Reconectar agora para uma tentativa manual independente."
            ),
            "IQOPTION_MANUAL_LOGIN_THROTTLED": (
                "Reconectar agora já foi solicitado. Aguarde até 2 minutos para repetir."
            ),
            "IQOPTION_CONNECTION_SAFETY_STATE_INVALID": (
                "O estado local de proteção da conexão não pôde ser validado. "
                "A conexão foi bloqueada com segurança; exporte o diagnóstico."
            ),
            "IQOPTION_WEBSOCKET_RECONNECT_LIMIT_REACHED": (
                "O limite preventivo de reconexões da sessão foi atingido. "
                "Aguarde antes de reconectar."
            ),
            "IQOPTION_ACCOUNT_MODE_UNAVAILABLE": (
                "A conta selecionada não está disponível neste cadastro."
            ),
            "IQOPTION_LOGIN_UNAVAILABLE": "O serviço de login da IQ Option está indisponível.",
            "IQOPTION_NETWORK_UNREACHABLE": (
                "O botão funcionou, mas este computador não alcançou os servidores HTTP "
                "oficiais da IQ Option na porta 443. A tentativa foi encerrada sem enviar ordem."
            ),
            "IQOPTION_WEBSOCKET_UNAVAILABLE": "A sessão da IQ Option não pôde ser aberta.",
            "IQOPTION_AUTH_TIMEOUT": "A IQ Option não confirmou a sessão dentro do prazo.",
            "IQOPTION_PAYOUT_UNAVAILABLE": (
                "A IQ Option não forneceu payout válido para este ativo."
            ),
            "IQOPTION_REQUEST_TIMEOUT": (
                "A consulta à IQ Option não recebeu resposta dentro do prazo."
            ),
            "IQOPTION_RESPONSE_TOO_LARGE": "A resposta da IQ Option excedeu o limite de tamanho.",
            "IQOPTION_EXTERNAL_ERROR": (
                "A IQ Option retornou uma falha não reconhecida pelo conector."
            ),
            "IQOPTION_CONNECTION_IN_PROGRESS": (
                "Já existe uma recuperação da IQ Option em andamento. "
                "Aguarde a conclusão indicada nesta tela."
            ),
        }.get(reason_code, f"Não foi possível conectar: {reason_code}")
        self._iqoption_workspace.set_iqoption_login_status(error_message)
        if isinstance(outcome, UiIqOptionLoginAck) and outcome.retry_after_seconds > 0:
            self._iqoption_workspace.set_iqoption_reconnect_wait(
                outcome.retry_after_seconds,
                outcome.attempts_in_window,
            )
        if reason_code in {
            "IQOPTION_CONNECTION_QUARANTINED",
            "IQOPTION_MANUAL_LOGIN_THROTTLED",
            "IQOPTION_WEBSOCKET_RECONNECT_LIMIT_REACHED",
            "IQOPTION_CONNECTION_IN_PROGRESS",
        }:
            return
        QMessageBox.warning(self, "Login IQ Option", error_message)

    def _start_iqoption_saved_login(self) -> None:
        """Reconnect a persisted Practice session without reopening the password dialog."""

        if self._iqoption_saved_login_started:
            return
        snapshot = self._controller.snapshot
        if snapshot is None:
            # The Core may still be opening an authoritative recovery session.
            # Never start a second broker login before the first projection
            # identifies whether a durable IQ order already owns recovery.
            QTimer.singleShot(1_000, self._start_iqoption_saved_login)
            return
        if snapshot is not None and any(
            card.broker == "IQOPTION" and card.is_connected for card in snapshot.broker_cards
        ):
            return
        if snapshot is not None and any(
            order.broker == "IQOPTION"
            and order.state in {"ACCEPTED", "OPEN", "UNKNOWN", "SETTLEMENT_UNKNOWN"}
            for order in snapshot.active_orders
        ):
            # Durable order recovery belongs to the Core. A second UI-triggered
            # login would race the authoritative reconciliation connection.
            return
        self._iqoption_saved_login_started = True
        self._iqoption_workspace.set_iqoption_login_busy(True, t("iq_option.login.reconnecting"))

        def reconnect() -> None:
            try:
                result: object = self._controller.login_iqoption("saved", source="auto")
            except (OSError, RuntimeError, ValueError, UiIpcError) as exc:
                result = exc
            self._iqoption_saved_login_finished.emit(result)

        threading.Thread(
            target=reconnect,
            name="iqoption-saved-login",
            daemon=True,
        ).start()

    def _on_iqoption_saved_login_finished(self, result: object) -> None:
        self._iqoption_workspace.set_iqoption_login_busy(False)
        if isinstance(result, UiIqOptionLoginAck) and result.connected:
            self._refresh_projection()
            self._iqoption_workspace.set_iqoption_login_status(t("iq_option.login.reconnected"))
            return
        reason = result.reason_code if isinstance(result, UiIqOptionLoginAck) else str(result)
        if reason == "IQOPTION_CREDENTIALS_NOT_CONFIGURED":
            self._iqoption_workspace.set_iqoption_login_status(t("iq_option.login.status"))
        elif reason == "IQOPTION_SAVED_REAL_REQUIRES_CONFIRMATION":
            self._iqoption_workspace.set_iqoption_login_status(
                t("iq_option.login.real_confirmation")
            )
        else:
            self._iqoption_workspace.set_iqoption_login_status(t("iq_option.login.saved_failed"))

    def closeEvent(self, event: QCloseEvent) -> None:
        with contextlib.suppress(Exception):
            self._controller.control_iqoption_bot(False)
        with contextlib.suppress(Exception):
            self._controller.safe_stop()
        with contextlib.suppress(Exception):
            self._controller.request_safe_close()
        event.accept()
