from __future__ import annotations

import contextlib
import json
import subprocess
import sys
from pathlib import Path

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
    DerivAssetRadarWidget,
    DerivWorkspaceWidget,
    GlobalRiskGaugeWidget,
    HealthGatePillWidget,
    OrderTableView,
    ResultsDashboardWidget,
    SettingsWorkspaceWidget,
    SyntheticStrategyConfigWidget,
    SyntheticStrategyLiveWidget,
)
from apps.ui.controller import UiController
from apps.ui.formatting import format_minor_units
from apps.ui.i18n import I18nManager, t
from apps.ui.ipc_client import UiIpcError
from apps.ui.theme import (
    ACCENT_AMBER,
    ACCENT_CYAN,
    ACCENT_GREEN,
    ACCENT_RED,
    TEXT_MUTED,
    TEXT_SECONDARY,
    get_application_stylesheet,
)
from packages.protocol.ui_messages import (
    UiDigitRiskConfig,
    UiDigitRiskConfigStatus,
    UiGlobalState,
)
from packages.security import without_broker_credentials

APP_VERSION = "1.9.11"


def _window_title(mode: str) -> str:
    return f"{t('app.title')} v{APP_VERSION} — {mode}"


class TradingLabMainWindow(QMainWindow):
    """Professional Trading Lab Desktop UI (PySide6 / Qt 6)."""

    _TAB_OVERVIEW = 0
    _TAB_DERIV = 1
    _TAB_IQ_OPTION = 2
    _TAB_ACTIVITY = 3
    _TAB_SETTINGS = 4

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
        self._deriv_real_selected = False

        self.setWindowTitle(_window_title(t("app.practice_badge")))
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
        self._deriv_workspace = DerivWorkspaceWidget()
        self._main_tabs.addTab(self._deriv_workspace, "")
        self._deriv_workspace.deriv_demo_connect_requested.connect(self._on_connect_deriv_demo)
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
        self._results_dashboard = ResultsDashboardWidget()
        content_layout.addWidget(self._results_dashboard)
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

        self._lbl_version = QLabel(f"v{APP_VERSION}  ·  DIGIT EDGE")
        self._lbl_version.setObjectName("BadgeInfo")
        self._lbl_version.setStyleSheet(f"color: {ACCENT_CYAN}; font-size: 11px; font-weight: 800;")
        layout.addWidget(self._lbl_version)

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

        # One authoritative automation toggle. OFF is the Core Safe Stop state.
        self._btn_bot = QPushButton()
        self._btn_bot.clicked.connect(self._on_toggle_bot)
        layout.addWidget(self._btn_bot)
        self._update_bot_button()

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
        self.setWindowTitle(_window_title(t("app.practice_badge")))
        self._lbl_badge.setText(t("app.practice_badge"))
        self._lbl_subtitle.setText(t("app.practice_subtitle"))
        self._lbl_pnl_title.setText(t("kpi.daily_pnl"))
        self._lbl_pnl_detail.setText(t("kpi.pnl_detail"))
        self._lbl_state_title.setText(t("kpi.global_state"))
        self._btn_diag.setText("📦 " + t("btn.diagnostic"))
        self._btn_close.setText("🔒 " + t("btn.safe_close"))
        self._update_bot_button()
        self._risk_gauge.retranslate()
        self._health_pill_widget.retranslate()
        self._order_table_widget.retranslate()
        self._results_dashboard.retranslate()
        self._card_deriv.retranslate()
        self._card_iqoption.retranslate()
        self._deriv_workspace.retranslate()
        self._asset_radar_panel.retranslate()
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
                self._deriv_real_selected = card_data.account_mode.value == "REAL"
                self._card_deriv.update_card(card_data)
                self._deriv_workspace.update_status(card_data)
                if card_data.account_mode.value == "REAL":
                    self.setWindowTitle(_window_title("DINHEIRO REAL"))
                    self._lbl_badge.setText("DINHEIRO REAL")
                    self._lbl_badge.setStyleSheet(
                        f"background: {ACCENT_RED}; color: white; font-weight: 900; padding: 5px;"
                    )
                else:
                    self.setWindowTitle(_window_title(t("app.practice_badge")))
                    self._lbl_badge.setText(t("app.practice_badge"))
                    self._lbl_badge.setStyleSheet("")
            elif card_data.broker == "IQ_OPTION":
                self._card_iqoption.update_card(card_data)
                self._iqoption_workspace.update_status(card_data)
        self._retranslate_navigation()

        # 5. Update Health Gates
        self._health_pill_widget.update_gates(snapshot.health_gates)

        # 6. Update Orders
        self._order_table_widget.update_orders(snapshot.active_orders)
        self._results_dashboard.update_results(snapshot.active_orders)
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
        self._iqoption_workspace.update_orders(snapshot.active_orders)
        self._settings_workspace.update_risk_projection(
            snapshot.global_exposure_minor_units,
            snapshot.global_max_exposure_minor_units,
            snapshot.daily_pnl_currency,
            snapshot.risk_state,
        )
        self._deriv_workspace.update_strategy_statuses(snapshot.deriv_strategies)
        self._synthetic_live_panel.update_statuses(snapshot.deriv_strategies)
        self._asset_radar_panel.update_ranking(snapshot.deriv_asset_ranking)
        if snapshot.digit_risk_config is not None:
            self._deriv_workspace.set_execution_strategy(
                snapshot.digit_risk_config.active_strategy_id
            )
            self._synthetic_config_panel.set_strategy(snapshot.digit_risk_config.active_strategy_id)
            self._synthetic_live_panel.set_strategy(snapshot.digit_risk_config.active_strategy_id)
            self._synthetic_config_panel.set_risk_config(snapshot.digit_risk_config)
        self._synthetic_config_panel.set_cooldown_remaining(snapshot.cooldown_remaining_seconds)

        # 7. Bot state is authoritative from the Core Safe Stop projection.
        self._bot_enabled = not snapshot.safe_stop_active
        self._btn_bot.setEnabled(connected)
        self._update_bot_button()
        deriv_connected = any(
            card.broker == "DERIV" and card.is_connected for card in snapshot.broker_cards
        )
        self._deriv_workspace.update_automation_state(
            self._bot_enabled,
            deriv_connected,
            self._deriv_real_selected,
            snapshot.deriv_bot_reason,
        )

    def _update_bot_button(self) -> None:
        self._btn_bot.setText(t("btn.bot.stop") if self._bot_enabled else t("btn.bot.start"))
        self._btn_bot.setObjectName("SafeStopButton" if self._bot_enabled else "BotStartButton")
        self._btn_bot.style().unpolish(self._btn_bot)
        self._btn_bot.style().polish(self._btn_bot)

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
            ack = self._controller.update_digit_risk_config(config)
            accepted = ack.status is UiDigitRiskConfigStatus.OK
            self._synthetic_config_panel.set_apply_result(accepted, ack.reason_code)
            self._refresh_projection()
        except Exception as exc:
            self._synthetic_config_panel.set_apply_result(False, str(exc)[:64])

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

    def closeEvent(self, event: QCloseEvent) -> None:
        with contextlib.suppress(Exception):
            self._controller.request_safe_close()
        event.accept()
