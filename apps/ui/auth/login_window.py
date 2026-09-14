from __future__ import annotations

import os
import re
import threading

from PySide6.QtCore import QByteArray, QRectF, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import (
    QCloseEvent,
    QDesktopServices,
    QGuiApplication,
    QIcon,
    QKeyEvent,
    QKeySequence,
    QPainter,
    QPixmap,
)
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from apps.ui.auth.logic import (
    format_code,
    format_countdown,
    map_auth_error_key,
    mask_email,
    validate_email,
)
from apps.ui.controller import UiController
from apps.ui.design import TOKENS, asset_path, icon
from apps.ui.i18n import t
from packages.protocol import UiAuthStartLoginAck, UiAuthSubmitOtpAck

SUPPORT_RENEW_URL = os.environ.get("TRADING_LAB_RENEW_URL", "https://tradinglab.app/renew")
_CODE_EXPIRATION_SECONDS = 300
_RESEND_COOLDOWN_SECONDS = 30


def _render_svg_pixmap(asset_name: str, width: int, height: int) -> QPixmap:
    file_path = asset_path(asset_name)
    if not file_path.is_file():
        pix = QPixmap(width, height)
        pix.fill(Qt.GlobalColor.transparent)
        return pix

    app = QGuiApplication.instance()
    dpr = 1.0
    if app is not None and isinstance(app, QGuiApplication):
        screen = app.primaryScreen()
        if screen is not None:
            dpr = screen.devicePixelRatio()

    pix = QPixmap(int(width * dpr), int(height * dpr))
    pix.fill(Qt.GlobalColor.transparent)
    pix.setDevicePixelRatio(dpr)

    svg_data = QByteArray(file_path.read_bytes())
    renderer = QSvgRenderer(svg_data)
    if renderer.isValid():
        painter = QPainter(pix)
        renderer.render(painter, QRectF(0, 0, width, height))
        painter.end()
    return pix


class DigitBox(QLineEdit):
    """Single-digit input box with auto-advance, backspace and paste handling."""

    backspace_on_empty = Signal()
    pasted_text = Signal(str)

    def __init__(self, index: int, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.index = index
        self.setMaxLength(1)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setFixedSize(48, 54)
        self.setStyleSheet(
            f"""
            QLineEdit {{
                background-color: {TOKENS.BG_SURFACE};
                color: {TOKENS.TEXT_PRIMARY};
                border: 2px solid {TOKENS.BORDER_COLOR};
                border-radius: {TOKENS.RADIUS_MD}px;
                font-family: Consolas, "Liberation Mono", Menlo, Courier, monospace;
                font-size: 24px;
                font-weight: 700;
            }}
            QLineEdit:focus {{
                border-color: {TOKENS.ACCENT_PRIMARY};
                background-color: {TOKENS.BG_ELEVATED};
            }}
            """
        )

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Backspace and not self.text():
            self.backspace_on_empty.emit()
            return
        if event.matches(QKeySequence.StandardKey.Paste) or (
            event.modifiers() & Qt.KeyboardModifier.ControlModifier and event.key() == Qt.Key.Key_V
        ):
            clipboard = QGuiApplication.clipboard()
            text = clipboard.text() if clipboard else ""
            if text:
                self.pasted_text.emit(text)
                return
        super().keyPressEvent(event)


class LoginWindow(QDialog):
    """Modern 3-step OTP login dialog with email validation and device activation."""

    _start_login_finished = Signal(object)
    _submit_otp_finished = Signal(object)

    _STATE_EMAIL = 0
    _STATE_CODE = 1
    _STATE_ACTIVATING = 2

    def __init__(
        self,
        controller: UiController,
        parent: QWidget | None = None,
        *,
        initial_error: str | None = None,
    ) -> None:
        super().__init__(parent)
        self._controller = controller
        self._email = ""
        self._masked_email = ""
        self._challenge_id = ""
        self._countdown_seconds = _CODE_EXPIRATION_SECONDS
        self._resend_cooldown = _RESEND_COOLDOWN_SECONDS

        self.setWindowTitle(t("login.title"))
        self.setFixedSize(440, 560)
        self.setModal(True)

        app_icon = asset_path("app.ico")
        if app_icon.is_file():
            self.setWindowIcon(QIcon(str(app_icon)))

        self.setStyleSheet(
            f"""
            QDialog {{
                background-color: {TOKENS.BG_ROOT};
                color: {TOKENS.TEXT_PRIMARY};
            }}
            QLabel {{
                color: {TOKENS.TEXT_PRIMARY};
            }}
            QLabel#dialogTitle {{
                font-size: 22px;
                font-weight: 700;
                color: {TOKENS.TEXT_PRIMARY};
            }}
            QLabel#dialogSubtitle {{
                font-size: 13px;
                color: {TOKENS.TEXT_SECONDARY};
                line-height: 1.4;
            }}
            QLabel#fieldLabel {{
                font-size: 12px;
                font-weight: 600;
                color: {TOKENS.TEXT_SECONDARY};
            }}
            QLabel#errorLabel {{
                font-size: 12px;
                font-weight: 600;
                color: {TOKENS.ACCENT_RED};
                background: transparent;
            }}
            QLineEdit#inputEmail {{
                background-color: {TOKENS.BG_SURFACE};
                border: 1px solid {TOKENS.BORDER_COLOR};
                border-radius: {TOKENS.RADIUS_MD}px;
                color: {TOKENS.TEXT_PRIMARY};
                font-size: 14px;
                padding: 10px 14px;
            }}
            QLineEdit#inputEmail:focus {{
                border-color: {TOKENS.ACCENT_PRIMARY};
                background-color: {TOKENS.BG_ELEVATED};
            }}
            QPushButton#primaryButton {{
                background-color: {TOKENS.ACCENT_PRIMARY};
                color: #070B14;
                font-size: 14px;
                font-weight: 700;
                border: none;
                border-radius: {TOKENS.RADIUS_MD}px;
                padding: 12px 20px;
            }}
            QPushButton#primaryButton:hover {{
                background-color: #38BDF8;
            }}
            QPushButton#primaryButton:disabled {{
                background-color: {TOKENS.BG_SURFACE};
                color: {TOKENS.TEXT_MUTED};
                border: 1px solid {TOKENS.BORDER_COLOR};
            }}
            QPushButton#linkButton {{
                background: transparent;
                border: none;
                color: {TOKENS.ACCENT_PRIMARY};
                font-size: 12px;
                font-weight: 600;
                text-decoration: underline;
                padding: 4px 8px;
            }}
            QPushButton#linkButton:hover {{
                color: #38BDF8;
            }}
            QPushButton#linkButton:disabled {{
                color: {TOKENS.TEXT_MUTED};
            }}
            QPushButton#renewButton {{
                background-color: {TOKENS.ACCENT_AMBER};
                color: #070B14;
                font-size: 13px;
                font-weight: 700;
                border: none;
                border-radius: {TOKENS.RADIUS_MD}px;
                padding: 10px 16px;
            }}
            QPushButton#renewButton:hover {{
                background-color: #FBBF24;
            }}
            QProgressBar {{
                background-color: {TOKENS.BG_SURFACE};
                border: 1px solid {TOKENS.BORDER_COLOR};
                border-radius: 4px;
                height: 6px;
                text-align: center;
            }}
            QProgressBar::chunk {{
                background-color: {TOKENS.ACCENT_PRIMARY};
                border-radius: 3px;
            }}
            """
        )

        self._start_login_finished.connect(self._on_start_login_finished)
        self._submit_otp_finished.connect(self._on_submit_otp_finished)

        # Timers
        self._timer_countdown = QTimer(self)
        self._timer_countdown.setInterval(1000)
        self._timer_countdown.timeout.connect(self._on_tick_countdown)

        self._timer_resend = QTimer(self)
        self._timer_resend.setInterval(1000)
        self._timer_resend.timeout.connect(self._on_tick_resend)

        self._build_ui()

        if initial_error:
            self._show_email_error(t(map_auth_error_key(initial_error)))

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(36, 36, 36, 36)
        layout.setSpacing(0)

        self._stack = QStackedWidget(self)
        self._stack.addWidget(self._build_email_step())
        self._stack.addWidget(self._build_code_step())
        self._stack.addWidget(self._build_activating_step())
        layout.addWidget(self._stack)

    # -------------------------------------------------------------------------
    # Step 1: Email Form
    # -------------------------------------------------------------------------
    def _build_email_step(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Logo
        lbl_logo = QLabel()
        lbl_logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl_logo.setPixmap(_render_svg_pixmap("logo-wordmark.svg", 180, 42))
        layout.addWidget(lbl_logo)
        layout.addSpacing(28)

        # Title & Subtitle
        lbl_title = QLabel(t("login.title"))
        lbl_title.setObjectName("dialogTitle")
        lbl_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(lbl_title)
        layout.addSpacing(8)

        lbl_sub = QLabel(t("login.subtitle"))
        lbl_sub.setObjectName("dialogSubtitle")
        lbl_sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl_sub.setWordWrap(True)
        layout.addWidget(lbl_sub)
        layout.addSpacing(28)

        # Email field
        lbl_email = QLabel(t("login.email_label"))
        lbl_email.setObjectName("fieldLabel")
        layout.addWidget(lbl_email)
        layout.addSpacing(6)

        self._txt_email = QLineEdit()
        self._txt_email.setObjectName("inputEmail")
        self._txt_email.setPlaceholderText(t("login.email_placeholder"))
        self._txt_email.returnPressed.connect(self._handle_send_code)
        layout.addWidget(self._txt_email)
        layout.addSpacing(6)

        # Error label
        self._lbl_email_error = QLabel()
        self._lbl_email_error.setObjectName("errorLabel")
        self._lbl_email_error.setWordWrap(True)
        self._lbl_email_error.hide()
        layout.addWidget(self._lbl_email_error)

        layout.addStretch()

        # Send Code Button
        self._btn_send_code = QPushButton(t("login.send_code"))
        self._btn_send_code.setObjectName("primaryButton")
        self._btn_send_code.setFixedHeight(44)
        self._btn_send_code.clicked.connect(self._handle_send_code)
        layout.addWidget(self._btn_send_code)

        return widget

    # -------------------------------------------------------------------------
    # Step 2: 6-Digit Code
    # -------------------------------------------------------------------------
    def _build_code_step(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Shield Icon
        lbl_icon = QLabel()
        lbl_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl_icon.setPixmap(icon("icon-shield", TOKENS.ACCENT_PRIMARY, size=40).pixmap(40, 40))
        layout.addWidget(lbl_icon)
        layout.addSpacing(16)

        # Title
        lbl_title = QLabel(t("login.title"))
        lbl_title.setObjectName("dialogTitle")
        lbl_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(lbl_title)
        layout.addSpacing(8)

        # Sent to email
        self._lbl_code_sent = QLabel()
        self._lbl_code_sent.setObjectName("dialogSubtitle")
        self._lbl_code_sent.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._lbl_code_sent.setWordWrap(True)
        layout.addWidget(self._lbl_code_sent)
        layout.addSpacing(24)

        # 6-Digit input boxes
        boxes_container = QWidget()
        boxes_layout = QHBoxLayout(boxes_container)
        boxes_layout.setContentsMargins(0, 0, 0, 0)
        boxes_layout.setSpacing(8)
        boxes_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._digit_boxes: list[DigitBox] = []
        for i in range(6):
            box = DigitBox(i)
            box.textChanged.connect(lambda text, idx=i: self._on_digit_changed(idx, text))
            box.backspace_on_empty.connect(lambda idx=i: self._on_digit_backspace(idx))
            box.pasted_text.connect(self._on_digit_paste)
            box.returnPressed.connect(self._handle_verify_code)
            self._digit_boxes.append(box)
            boxes_layout.addWidget(box)

        layout.addWidget(boxes_container)
        layout.addSpacing(14)

        # Countdown label
        self._lbl_countdown = QLabel()
        self._lbl_countdown.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._lbl_countdown.setStyleSheet(f"font-size: 12px; color: {TOKENS.TEXT_MUTED};")
        layout.addWidget(self._lbl_countdown)
        layout.addSpacing(8)

        # Error label
        self._lbl_code_error = QLabel()
        self._lbl_code_error.setObjectName("errorLabel")
        self._lbl_code_error.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._lbl_code_error.setWordWrap(True)
        self._lbl_code_error.hide()
        layout.addWidget(self._lbl_code_error)

        # Renew button (only shown on license expiration)
        self._btn_renew = QPushButton(t("login.renew"))
        self._btn_renew.setObjectName("renewButton")
        self._btn_renew.hide()
        self._btn_renew.clicked.connect(self._on_open_renew_url)
        layout.addWidget(self._btn_renew)

        layout.addStretch()

        # Verify button
        self._btn_verify = QPushButton(t("login.verify"))
        self._btn_verify.setObjectName("primaryButton")
        self._btn_verify.setFixedHeight(44)
        self._btn_verify.clicked.connect(self._handle_verify_code)
        layout.addWidget(self._btn_verify)
        layout.addSpacing(12)

        # Resend & Change email actions
        links_row = QHBoxLayout()
        links_row.setContentsMargins(0, 0, 0, 0)
        links_row.setSpacing(12)

        self._btn_change_email = QPushButton(t("login.change_email"))
        self._btn_change_email.setObjectName("linkButton")
        self._btn_change_email.clicked.connect(self._handle_change_email)
        links_row.addWidget(self._btn_change_email)

        links_row.addStretch()

        self._btn_resend = QPushButton(t("login.resend"))
        self._btn_resend.setObjectName("linkButton")
        self._btn_resend.clicked.connect(self._handle_resend_code)
        links_row.addWidget(self._btn_resend)

        layout.addLayout(links_row)

        return widget

    # -------------------------------------------------------------------------
    # Step 3: Activating (Progress Bar)
    # -------------------------------------------------------------------------
    def _build_activating_step(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(20, 40, 20, 40)
        layout.setSpacing(0)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # Logo
        lbl_logo = QLabel()
        lbl_logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl_logo.setPixmap(_render_svg_pixmap("logo-wordmark.svg", 180, 42))
        layout.addWidget(lbl_logo)
        layout.addSpacing(36)

        # Title
        lbl_title = QLabel(t("login.activating"))
        lbl_title.setObjectName("dialogTitle")
        lbl_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(lbl_title)
        layout.addSpacing(12)

        # Hint
        lbl_hint = QLabel(t("login.activating_hint"))
        lbl_hint.setObjectName("dialogSubtitle")
        lbl_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl_hint.setWordWrap(True)
        layout.addWidget(lbl_hint)
        layout.addSpacing(32)

        # Native indeterminate progress bar (no timer animation loops)
        self._progress = QProgressBar()
        self._progress.setRange(0, 0)
        self._progress.setFixedWidth(280)
        layout.addWidget(self._progress, 0, Qt.AlignmentFlag.AlignCenter)

        layout.addStretch()
        return widget

    # -------------------------------------------------------------------------
    # Event Handlers & Navigation
    # -------------------------------------------------------------------------
    def _show_email_error(self, message: str) -> None:
        self._lbl_email_error.setText(message)
        self._lbl_email_error.show()

    def _hide_email_error(self) -> None:
        self._lbl_email_error.hide()

    def _show_code_error(self, message: str, is_license_expired: bool = False) -> None:
        self._lbl_code_error.setText(message)
        self._lbl_code_error.show()
        if is_license_expired:
            self._btn_renew.show()
        else:
            self._btn_renew.hide()

    def _hide_code_error(self) -> None:
        self._lbl_code_error.hide()
        self._btn_renew.hide()

    def _handle_send_code(self) -> None:
        self._hide_email_error()
        raw_email = self._txt_email.text().strip()
        if not validate_email(raw_email):
            self._show_email_error(t("login.err_invalid_email"))
            self._txt_email.setFocus()
            return

        self._email = raw_email
        self._btn_send_code.setEnabled(False)
        self._btn_send_code.setText(t("login.sending"))

        def run_start_login() -> None:
            try:
                ack = self._controller.auth_start_login(raw_email)
                self._start_login_finished.emit(ack)
            except Exception as exc:
                self._start_login_finished.emit(exc)

        threading.Thread(
            target=run_start_login,
            name="auth-start-login-thread",
            daemon=True,
        ).start()

    def _on_start_login_finished(self, result: object) -> None:
        self._btn_send_code.setEnabled(True)
        self._btn_send_code.setText(t("login.send_code"))

        if isinstance(result, Exception):
            self._show_email_error(t("login.err_unavailable"))
            return

        if isinstance(result, UiAuthStartLoginAck):
            if result.status == "PENDING_OTP":
                self._challenge_id = result.challenge_id or ""
                self._masked_email = result.user_id_preview or mask_email(self._email)
                self._lbl_code_sent.setText(t("login.code_sent_to", email=self._masked_email))
                self._switch_to_code_step()
            else:
                reason_key = map_auth_error_key(result.reason)
                self._show_email_error(t(reason_key))
        else:
            self._show_email_error(t("login.err_unavailable"))

    def _switch_to_code_step(self) -> None:
        self._hide_code_error()
        for box in self._digit_boxes:
            box.clear()

        self._countdown_seconds = _CODE_EXPIRATION_SECONDS
        self._resend_cooldown = _RESEND_COOLDOWN_SECONDS
        self._update_countdown_label()
        self._update_resend_button()

        self._timer_countdown.start()
        self._timer_resend.start()

        self._stack.setCurrentIndex(self._STATE_CODE)
        if self._digit_boxes:
            self._digit_boxes[0].setFocus()

    def _handle_change_email(self) -> None:
        self._timer_countdown.stop()
        self._timer_resend.stop()
        self._hide_code_error()
        self._stack.setCurrentIndex(self._STATE_EMAIL)
        self._txt_email.setFocus()
        self._txt_email.selectAll()

    def _handle_resend_code(self) -> None:
        if self._resend_cooldown > 0:
            return
        self._hide_code_error()
        self._btn_resend.setEnabled(False)

        def run_resend() -> None:
            try:
                ack = self._controller.auth_start_login(self._email)
                self._start_login_finished.emit(ack)
            except Exception as exc:
                self._start_login_finished.emit(exc)

        threading.Thread(
            target=run_resend,
            name="auth-resend-login-thread",
            daemon=True,
        ).start()

    def _on_digit_changed(self, index: int, text: str) -> None:
        clean = re.sub(r"[^0-9a-zA-Z]", "", text)
        if clean != text:
            self._digit_boxes[index].setText(clean)
            return

        if len(clean) == 1:
            if index < 5:
                self._digit_boxes[index + 1].setFocus()
                self._digit_boxes[index + 1].selectAll()
            else:
                self._btn_verify.setFocus()

    def _on_digit_backspace(self, index: int) -> None:
        if index > 0:
            prev_box = self._digit_boxes[index - 1]
            prev_box.setFocus()
            prev_box.selectAll()

    def _on_digit_paste(self, text: str) -> None:
        cleaned = re.sub(r"[^0-9a-zA-Z]", "", text)[:6]
        if not cleaned:
            return
        for i, ch in enumerate(cleaned):
            if i < len(self._digit_boxes):
                self._digit_boxes[i].setText(ch)
        if len(cleaned) == 6:
            self._btn_verify.setFocus()
        elif len(cleaned) < 6:
            self._digit_boxes[len(cleaned)].setFocus()

    def _handle_verify_code(self) -> None:
        self._hide_code_error()
        code = format_code([box.text() for box in self._digit_boxes])
        if len(code) != 6:
            self._show_code_error(t("login.err_otp_invalid"))
            for box in self._digit_boxes:
                if not box.text():
                    box.setFocus()
                    break
            return

        self._stack.setCurrentIndex(self._STATE_ACTIVATING)

        def run_submit_otp() -> None:
            try:
                ack = self._controller.auth_submit_otp(self._challenge_id, code)
                self._submit_otp_finished.emit(ack)
            except Exception as exc:
                self._submit_otp_finished.emit(exc)

        threading.Thread(
            target=run_submit_otp,
            name="auth-submit-otp-thread",
            daemon=True,
        ).start()

    def _on_submit_otp_finished(self, result: object) -> None:
        if isinstance(result, UiAuthSubmitOtpAck) and result.status == "AUTHORIZED":
            self._timer_countdown.stop()
            self._timer_resend.stop()
            self.accept()
            return

        # Verification failed -> back to code step
        self._stack.setCurrentIndex(self._STATE_CODE)
        if isinstance(result, Exception):
            self._show_code_error(t("login.err_unavailable"))
            return

        if isinstance(result, UiAuthSubmitOtpAck):
            reason = result.reason or "AUTH_OTP_INVALID"
            is_license_expired = reason == "AUTH_LICENSE_EXPIRED"
            reason_key = map_auth_error_key(reason)
            self._show_code_error(t(reason_key), is_license_expired=is_license_expired)
        else:
            self._show_code_error(t("login.err_unavailable"))

    def _on_tick_countdown(self) -> None:
        self._countdown_seconds -= 1
        if self._countdown_seconds <= 0:
            self._timer_countdown.stop()
            self._countdown_seconds = 0
            self._btn_verify.setEnabled(False)
            self._show_code_error(t("login.err_expired"))
        self._update_countdown_label()

    def _update_countdown_label(self) -> None:
        time_str = format_countdown(self._countdown_seconds)
        self._lbl_countdown.setText(t("login.code_expires_in", time=time_str))

    def _on_tick_resend(self) -> None:
        self._resend_cooldown -= 1
        if self._resend_cooldown <= 0:
            self._timer_resend.stop()
            self._resend_cooldown = 0
        self._update_resend_button()

    def _update_resend_button(self) -> None:
        if self._resend_cooldown > 0:
            self._btn_resend.setEnabled(False)
            self._btn_resend.setText(t("login.resend_in", seconds=self._resend_cooldown))
        else:
            self._btn_resend.setEnabled(True)
            self._btn_resend.setText(t("login.resend"))

    def _on_open_renew_url(self) -> None:
        QDesktopServices.openUrl(QUrl(SUPPORT_RENEW_URL))

    def closeEvent(self, event: QCloseEvent) -> None:
        self._timer_countdown.stop()
        self._timer_resend.stop()
        super().closeEvent(event)
