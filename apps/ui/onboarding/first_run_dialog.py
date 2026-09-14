"""First-run 3-step guided onboarding dialog."""

from __future__ import annotations

import contextlib
import json
from pathlib import Path

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from apps.ui.design import TOKENS, icon
from apps.ui.i18n import t

SETTINGS_FILENAME = "ui_settings.json"


def is_onboarding_done(profile_dir: Path | str | None) -> bool:
    """Return True if first-run onboarding has already been completed."""
    if not profile_dir:
        return False
    path = Path(profile_dir) / SETTINGS_FILENAME
    if not path.exists():
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return bool(data.get("onboarding_done", False))
    except Exception:
        return False


def set_onboarding_done(profile_dir: Path | str | None) -> None:
    """Persist onboarding_done=True into profile settings."""
    if not profile_dir:
        return
    path = Path(profile_dir) / SETTINGS_FILENAME
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {}
        if path.exists():
            with contextlib.suppress(Exception):
                data = json.loads(path.read_text(encoding="utf-8"))
        data["onboarding_done"] = True
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except Exception:
        pass


class FirstRunDialog(QDialog):
    """3-step modal onboarding walkthrough for first-time users (620x460)."""

    def __init__(
        self,
        profile_dir: Path | str | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._profile_dir = profile_dir
        self._current_step = 0

        self.setObjectName("FirstRunDialog")
        self.setFixedSize(620, 460)
        self.setWindowTitle(t("onboarding.title"))
        self.setWindowFlags(
            Qt.WindowType.Dialog
            | Qt.WindowType.CustomizeWindowHint
            | Qt.WindowType.WindowTitleHint
            | Qt.WindowType.WindowCloseButtonHint
        )
        self.setModal(True)

        self._apply_theme()
        self._build_ui()
        self._update_step_view()

    def _apply_theme(self) -> None:
        self.setStyleSheet(
            f"""
            QDialog#FirstRunDialog {{
                background-color: {TOKENS.BG_ROOT};
                color: {TOKENS.TEXT_PRIMARY};
            }}
            QLabel#StepIndicatorActive {{
                background-color: {TOKENS.ACCENT_PRIMARY};
                color: #000000;
                font-weight: 700;
                font-size: 12px;
                border-radius: 12px;
                min-width: 24px;
                max-width: 24px;
                min-height: 24px;
                max-height: 24px;
                qproperty-alignment: AlignCenter;
            }}
            QLabel#StepIndicatorInactive {{
                background-color: {TOKENS.BG_SURFACE};
                color: {TOKENS.TEXT_MUTED};
                font-weight: 600;
                font-size: 12px;
                border-radius: 12px;
                min-width: 24px;
                max-width: 24px;
                min-height: 24px;
                max-height: 24px;
                qproperty-alignment: AlignCenter;
            }}
            QLabel#StepDivider {{
                color: {TOKENS.BORDER_COLOR};
                font-size: 14px;
                font-weight: 700;
            }}
            QLabel#StepTitle {{
                color: {TOKENS.TEXT_PRIMARY};
                font-size: 20px;
                font-weight: 700;
                qproperty-alignment: AlignCenter;
            }}
            QLabel#StepText {{
                color: {TOKENS.TEXT_SECONDARY};
                font-size: 13px;
                line-height: 1.4;
                qproperty-alignment: AlignCenter;
            }}
            QPushButton#PrimaryButton {{
                background-color: {TOKENS.ACCENT_PRIMARY};
                color: #000000;
                font-weight: 700;
                font-size: 13px;
                border: none;
                border-radius: 6px;
                padding: 10px 24px;
                min-width: 100px;
            }}
            QPushButton#PrimaryButton:hover {{
                background-color: {TOKENS.BORDER_HOVER};
            }}
            QPushButton#GhostButton {{
                background-color: transparent;
                color: {TOKENS.TEXT_MUTED};
                font-weight: 600;
                font-size: 13px;
                border: none;
                padding: 10px 16px;
            }}
            QPushButton#GhostButton:hover {{
                color: {TOKENS.TEXT_PRIMARY};
            }}
            """
        )

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(36, 28, 36, 24)
        root.setSpacing(20)

        # 1. Top Step Indicator (1 · 2 · 3)
        indicator_layout = QHBoxLayout()
        indicator_layout.setSpacing(12)
        indicator_layout.addStretch()

        self._lbl_step1 = QLabel("1")
        self._lbl_step1.setObjectName("StepIndicatorActive")
        indicator_layout.addWidget(self._lbl_step1)

        self._lbl_div1 = QLabel("·")
        self._lbl_div1.setObjectName("StepDivider")
        indicator_layout.addWidget(self._lbl_div1)

        self._lbl_step2 = QLabel("2")
        self._lbl_step2.setObjectName("StepIndicatorInactive")
        indicator_layout.addWidget(self._lbl_step2)

        self._lbl_div2 = QLabel("·")
        self._lbl_div2.setObjectName("StepDivider")
        indicator_layout.addWidget(self._lbl_div2)

        self._lbl_step3 = QLabel("3")
        self._lbl_step3.setObjectName("StepIndicatorInactive")
        indicator_layout.addWidget(self._lbl_step3)

        indicator_layout.addStretch()
        root.addLayout(indicator_layout)

        # 2. Stacked Pages
        self._stack = QStackedWidget()
        self._stack.addWidget(self._create_step1_page())
        self._stack.addWidget(self._create_step2_page())
        self._stack.addWidget(self._create_step3_page())
        root.addWidget(self._stack, 1)

        # 3. Bottom Footer with Skip and Next/Finish
        footer = QHBoxLayout()
        footer.setSpacing(12)

        self._btn_skip = QPushButton(t("onboarding.skip"))
        self._btn_skip.setObjectName("GhostButton")
        self._btn_skip.clicked.connect(self._on_skip)
        footer.addWidget(self._btn_skip)

        footer.addStretch()

        self._btn_next = QPushButton(t("onboarding.next"))
        self._btn_next.setObjectName("PrimaryButton")
        self._btn_next.clicked.connect(self._on_next)
        footer.addWidget(self._btn_next)

        root.addLayout(footer)

    def _create_step1_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(20, 20, 20, 10)
        layout.setSpacing(16)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # Icon 48px nav-deriv in ACCENT_PRIMARY
        icon_lbl = QLabel()
        icon_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        deriv_ic = icon("nav-deriv", color=TOKENS.ACCENT_PRIMARY, size=48)
        pix = deriv_ic.pixmap(QSize(48, 48))
        icon_lbl.setPixmap(pix)
        layout.addWidget(icon_lbl)

        self._step1_title = QLabel(t("onboarding.step1_title"))
        self._step1_title.setObjectName("StepTitle")
        layout.addWidget(self._step1_title)

        self._step1_text = QLabel(t("onboarding.step1_text"))
        self._step1_text.setObjectName("StepText")
        self._step1_text.setWordWrap(True)
        self._step1_text.setMaximumWidth(460)
        layout.addWidget(self._step1_text)

        return page

    def _create_step2_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(20, 20, 20, 10)
        layout.setSpacing(16)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # Icon 48px shield in ACCENT_PRIMARY
        icon_lbl = QLabel()
        icon_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        shield_ic = icon("icon-shield", color=TOKENS.ACCENT_PRIMARY, size=48)
        pix = shield_ic.pixmap(QSize(48, 48))
        icon_lbl.setPixmap(pix)
        layout.addWidget(icon_lbl)

        self._step2_title = QLabel(t("onboarding.step2_title"))
        self._step2_title.setObjectName("StepTitle")
        layout.addWidget(self._step2_title)

        self._step2_text = QLabel(t("onboarding.step2_text"))
        self._step2_text.setObjectName("StepText")
        self._step2_text.setWordWrap(True)
        self._step2_text.setMaximumWidth(460)
        layout.addWidget(self._step2_text)

        return page

    def _create_step3_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(20, 20, 20, 10)
        layout.setSpacing(16)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # Icon 48px play in ACCENT_PRIMARY
        icon_lbl = QLabel()
        icon_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        play_ic = icon("icon-play", color=TOKENS.ACCENT_PRIMARY, size=48)
        pix = play_ic.pixmap(QSize(48, 48))
        icon_lbl.setPixmap(pix)
        layout.addWidget(icon_lbl)

        self._step3_title = QLabel(t("onboarding.step3_title"))
        self._step3_title.setObjectName("StepTitle")
        layout.addWidget(self._step3_title)

        self._step3_text = QLabel(t("onboarding.step3_text"))
        self._step3_text.setObjectName("StepText")
        self._step3_text.setWordWrap(True)
        self._step3_text.setMaximumWidth(460)
        layout.addWidget(self._step3_text)

        return page

    def _update_step_view(self) -> None:
        self._stack.setCurrentIndex(self._current_step)

        # Update step pills
        indicators = [self._lbl_step1, self._lbl_step2, self._lbl_step3]
        for idx, lbl in enumerate(indicators):
            if idx == self._current_step:
                lbl.setObjectName("StepIndicatorActive")
            else:
                lbl.setObjectName("StepIndicatorInactive")
            lbl.style().unpolish(lbl)
            lbl.style().polish(lbl)

        # Update next/finish button label
        if self._current_step == 2:
            self._btn_next.setText(t("onboarding.finish"))
        else:
            self._btn_next.setText(t("onboarding.next"))

    def _on_next(self) -> None:
        if self._current_step < 2:
            self._current_step += 1
            self._update_step_view()
        else:
            self._finish()

    def _on_skip(self) -> None:
        self._finish()

    def _finish(self) -> None:
        set_onboarding_done(self._profile_dir)
        self.accept()

    def closeEvent(self, event: QCloseEvent) -> None:
        set_onboarding_done(self._profile_dir)
        super().closeEvent(event)

    def retranslate(self) -> None:
        self.setWindowTitle(t("onboarding.title"))
        self._step1_title.setText(t("onboarding.step1_title"))
        self._step1_text.setText(t("onboarding.step1_text"))
        self._step2_title.setText(t("onboarding.step2_title"))
        self._step2_text.setText(t("onboarding.step2_text"))
        self._step3_title.setText(t("onboarding.step3_title"))
        self._step3_text.setText(t("onboarding.step3_text"))
        self._btn_skip.setText(t("onboarding.skip"))
        if self._current_step == 2:
            self._btn_next.setText(t("onboarding.finish"))
        else:
            self._btn_next.setText(t("onboarding.next"))
