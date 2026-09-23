from __future__ import annotations

from datetime import UTC, datetime

from PySide6.QtCore import QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from apps.launcher.build_defaults import get_support_contact_url, get_support_renew_url
from apps.ui.auth.logic import mask_email
from apps.ui.design import TOKENS
from apps.ui.i18n import t
from packages.protocol import UiAuthStatusResponse

SUPPORT_RENEW_URL = get_support_renew_url()
SUPPORT_CONTACT_URL = get_support_contact_url()


class AccountPage(QWidget):
    """Account management page displaying subscription, device, and session info."""

    sign_out_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("AccountPage")
        self._auth_status: UiAuthStatusResponse | None = None

        self.setStyleSheet(
            f"""
            QWidget#AccountPage {{
                background-color: transparent;
            }}
            QFrame#card {{
                background-color: {TOKENS.BG_CARD};
                border: 1px solid {TOKENS.BORDER_COLOR};
                border-radius: {TOKENS.RADIUS_LG}px;
            }}
            QLabel#pageTitle {{
                color: {TOKENS.TEXT_PRIMARY};
                font-size: 22px;
                font-weight: 700;
            }}
            QLabel#pageSubtitle {{
                color: {TOKENS.TEXT_SECONDARY};
                font-size: 13px;
            }}
            QLabel#cardTitle {{
                color: {TOKENS.TEXT_PRIMARY};
                font-size: 16px;
                font-weight: 700;
            }}
            QLabel#cardHint {{
                color: {TOKENS.TEXT_SECONDARY};
                font-size: 12px;
            }}
            QLabel#kpiValue {{
                color: {TOKENS.TEXT_PRIMARY};
                font-size: 15px;
                font-weight: 600;
            }}
            QLabel#planBadge {{
                background-color: {TOKENS.BG_SURFACE};
                border: 1px solid {TOKENS.ACCENT_PRIMARY};
                color: {TOKENS.ACCENT_PRIMARY};
                font-size: 11px;
                font-weight: 700;
                border-radius: 4px;
                padding: 3px 8px;
            }}
            QLabel#deviceBadge {{
                background-color: {TOKENS.BG_SURFACE};
                border: 1px solid {TOKENS.ACCENT_GREEN};
                color: {TOKENS.ACCENT_GREEN};
                font-size: 11px;
                font-weight: 700;
                border-radius: 4px;
                padding: 3px 8px;
            }}
            QPushButton#secondary {{
                background-color: {TOKENS.BG_SURFACE};
                border: 1px solid {TOKENS.BORDER_ACCENT};
                border-radius: {TOKENS.RADIUS_MD}px;
                color: {TOKENS.TEXT_PRIMARY};
                font-size: 13px;
                font-weight: 600;
                padding: 8px 16px;
                min-width: 140px;
            }}
            QPushButton#secondary:hover {{
                background-color: {TOKENS.BG_ELEVATED};
                border-color: {TOKENS.BORDER_HOVER};
                color: #FFFFFF;
            }}
            QPushButton#secondary:disabled {{
                background-color: {TOKENS.BG_CARD};
                border: 1px solid {TOKENS.BORDER_COLOR};
                color: {TOKENS.TEXT_MUTED};
            }}
            QPushButton#danger {{
                background-color: {TOKENS.BG_SURFACE};
                border: 1px solid {TOKENS.ACCENT_RED};
                border-radius: {TOKENS.RADIUS_MD}px;
                color: {TOKENS.ACCENT_RED};
                font-size: 13px;
                font-weight: 600;
                padding: 8px 16px;
                min-width: 140px;
            }}
            QPushButton#danger:hover {{
                background-color: rgba(229, 72, 77, 0.15);
            }}
            QProgressBar {{
                background-color: {TOKENS.BG_SURFACE};
                border: 1px solid {TOKENS.BORDER_COLOR};
                border-radius: 4px;
                height: 6px;
            }}
            QProgressBar::chunk {{
                background-color: {TOKENS.ACCENT_PRIMARY};
                border-radius: 3px;
            }}
            """
        )

        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)

        # Header
        self._lbl_title = QLabel(t("account.title"))
        self._lbl_title.setObjectName("pageTitle")
        layout.addWidget(self._lbl_title)

        self._lbl_sub = QLabel(t("account.subtitle"))
        self._lbl_sub.setObjectName("pageSubtitle")
        layout.addWidget(self._lbl_sub)

        # Card 1: Subscription
        layout.addWidget(self._build_subscription_card())

        # Card 2: Device
        layout.addWidget(self._build_device_card())

        # Card 3: Session
        layout.addWidget(self._build_session_card())

        # Card 4: Support
        layout.addWidget(self._build_support_card())

        layout.addStretch()

    def _build_subscription_card(self) -> QWidget:
        card = QFrame()
        card.setObjectName("card")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(8)

        # Top row: title + badge
        header = QHBoxLayout()
        self._lbl_sub_title = QLabel(t("account.subscription"))
        self._lbl_sub_title.setObjectName("cardTitle")
        header.addWidget(self._lbl_sub_title)

        self._lbl_plan_badge = QLabel(t("account.plan_none"))
        self._lbl_plan_badge.setObjectName("planBadge")
        header.addWidget(self._lbl_plan_badge)
        header.addStretch()

        self._btn_renew = QPushButton(t("account.renew"))
        self._btn_renew.setObjectName("secondary")
        self._btn_renew.clicked.connect(self._on_renew_clicked)
        header.addWidget(self._btn_renew)
        layout.addLayout(header)

        # Hint
        self._lbl_sub_hint = QLabel(t("account.subscription_hint"))
        self._lbl_sub_hint.setObjectName("cardHint")
        layout.addWidget(self._lbl_sub_hint)

        # Expiration text
        self._lbl_expires = QLabel("—")
        self._lbl_expires.setObjectName("kpiValue")
        layout.addWidget(self._lbl_expires)

        # Progress bar
        self._progress_sub = QProgressBar()
        self._progress_sub.setFixedHeight(6)
        self._progress_sub.setTextVisible(False)
        self._progress_sub.setRange(0, 100)
        self._progress_sub.setValue(100)
        layout.addWidget(self._progress_sub)

        return card

    def _build_device_card(self) -> QWidget:
        card = QFrame()
        card.setObjectName("card")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(8)

        # Header row
        header = QHBoxLayout()
        self._lbl_dev_title = QLabel(t("account.device"))
        self._lbl_dev_title.setObjectName("cardTitle")
        header.addWidget(self._lbl_dev_title)

        self._lbl_dev_badge = QLabel(t("account.this_device"))
        self._lbl_dev_badge.setObjectName("deviceBadge")
        header.addWidget(self._lbl_dev_badge)
        header.addStretch()
        layout.addLayout(header)

        # Hint
        self._lbl_dev_hint = QLabel(t("account.device_hint"))
        self._lbl_dev_hint.setObjectName("cardHint")
        layout.addWidget(self._lbl_dev_hint)

        # Device ID
        self._lbl_dev_id = QLabel("—")
        self._lbl_dev_id.setObjectName("kpiValue")
        layout.addWidget(self._lbl_dev_id)

        # Limit notice
        self._lbl_dev_limit = QLabel(t("account.device_limit_hint"))
        self._lbl_dev_limit.setObjectName("cardHint")
        self._lbl_dev_limit.setWordWrap(True)
        layout.addWidget(self._lbl_dev_limit)

        return card

    def _build_session_card(self) -> QWidget:
        card = QFrame()
        card.setObjectName("card")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(8)

        # Header row
        header = QHBoxLayout()
        self._lbl_sess_title = QLabel(t("account.session"))
        self._lbl_sess_title.setObjectName("cardTitle")
        header.addWidget(self._lbl_sess_title)
        header.addStretch()

        self._btn_sign_out = QPushButton(t("account.sign_out"))
        self._btn_sign_out.setObjectName("danger")
        self._btn_sign_out.clicked.connect(self._on_sign_out_clicked)
        header.addWidget(self._btn_sign_out)
        layout.addLayout(header)

        # Hint
        self._lbl_sess_hint = QLabel(t("account.session_hint"))
        self._lbl_sess_hint.setObjectName("cardHint")
        layout.addWidget(self._lbl_sess_hint)

        # Masked email
        self._lbl_email_val = QLabel(t("account.not_signed_in"))
        self._lbl_email_val.setObjectName("kpiValue")
        layout.addWidget(self._lbl_email_val)

        return card

    def _build_support_card(self) -> QWidget:
        card = QFrame()
        card.setObjectName("card")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(8)

        # Header row
        header = QHBoxLayout()
        self._lbl_supp_title = QLabel(t("account.support"))
        self._lbl_supp_title.setObjectName("cardTitle")
        header.addWidget(self._lbl_supp_title)
        header.addStretch()

        self._btn_contact = QPushButton(t("account.contact_support"))
        self._btn_contact.setObjectName("secondary")
        self._btn_contact.clicked.connect(self._on_contact_support_clicked)
        header.addWidget(self._btn_contact)
        layout.addLayout(header)

        # Hint
        self._lbl_supp_hint = QLabel(t("account.support_hint"))
        self._lbl_supp_hint.setObjectName("cardHint")
        layout.addWidget(self._lbl_supp_hint)

        return card

    # -------------------------------------------------------------------------
    # Updates & Actions
    # -------------------------------------------------------------------------
    def update_auth_status(self, status: UiAuthStatusResponse | None) -> None:
        self._auth_status = status
        if status is None or status.status != "AUTHORIZED":
            self._lbl_plan_badge.setText(t("account.plan_none"))
            self._lbl_expires.setText("—")
            self._progress_sub.setValue(0)
            self._lbl_dev_id.setText("—")
            self._lbl_email_val.setText(t("account.not_signed_in"))
            return

        # Plan
        plan = status.plan or "PRO"
        plan_key = f"account.plan_{plan.lower()}"
        self._lbl_plan_badge.setText(t(plan_key))

        # Email
        masked = status.user_id_preview or mask_email("user@tradinglab.app")
        self._lbl_email_val.setText(masked)

        # Device ID (truncated for neat display)
        dev = status.device_id or "local-device"
        display_dev = dev if len(dev) <= 24 else f"{dev[:12]}...{dev[-8:]}"
        self._lbl_dev_id.setText(display_dev)

        # Expiration
        if status.expires_at:
            try:
                # Parse ISO date
                clean_iso = status.expires_at.replace("Z", "+00:00")
                exp_dt = datetime.fromisoformat(clean_iso)
                if exp_dt.tzinfo is None:
                    exp_dt = exp_dt.replace(tzinfo=UTC)
                now_dt = datetime.now(UTC)
                delta = exp_dt - now_dt
                days = max(0, delta.days)
                date_str = exp_dt.strftime("%Y-%m-%d")
                if days == 0:
                    self._lbl_expires.setText(t("account.expires_today", date=date_str))
                    self._progress_sub.setValue(5)
                else:
                    self._lbl_expires.setText(t("account.expires_in", days=days, date=date_str))
                    pct = min(100, max(10, int((days / 30) * 100)))
                    self._progress_sub.setValue(pct)
            except Exception:
                self._lbl_expires.setText(status.expires_at[:10])
                self._progress_sub.setValue(100)
        else:
            self._lbl_expires.setText(t("account.status_active"))
            self._progress_sub.setValue(100)

    def retranslate(self) -> None:
        self._lbl_title.setText(t("account.title"))
        self._lbl_sub.setText(t("account.subtitle"))
        self._lbl_sub_title.setText(t("account.subscription"))
        self._lbl_sub_hint.setText(t("account.subscription_hint"))
        self._btn_renew.setText(t("account.renew"))
        self._lbl_dev_title.setText(t("account.device"))
        self._lbl_dev_badge.setText(t("account.this_device"))
        self._lbl_dev_hint.setText(t("account.device_hint"))
        self._lbl_dev_limit.setText(t("account.device_limit_hint"))
        self._lbl_sess_title.setText(t("account.session"))
        self._lbl_sess_hint.setText(t("account.session_hint"))
        self._btn_sign_out.setText(t("account.sign_out"))
        self._lbl_supp_title.setText(t("account.support"))
        self._lbl_supp_hint.setText(t("account.support_hint"))
        self._btn_contact.setText(t("account.contact_support"))
        self.update_auth_status(self._auth_status)

    def _on_renew_clicked(self) -> None:
        QDesktopServices.openUrl(QUrl(SUPPORT_RENEW_URL))

    def _on_contact_support_clicked(self) -> None:
        QDesktopServices.openUrl(QUrl(SUPPORT_CONTACT_URL))

    def _on_sign_out_clicked(self) -> None:
        self.sign_out_requested.emit()
