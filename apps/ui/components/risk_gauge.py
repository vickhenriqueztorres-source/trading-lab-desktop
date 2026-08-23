from __future__ import annotations

from PySide6.QtWidgets import QFrame, QLabel, QProgressBar, QVBoxLayout

from apps.ui.formatting import format_minor_units
from apps.ui.i18n import t
from apps.ui.theme import ACCENT_AMBER, ACCENT_CYAN, ACCENT_RED, TEXT_MUTED


class GlobalRiskGaugeWidget(QFrame):
    def __init__(self, parent: QFrame | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Card")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(8)

        self._title = QLabel(t("kpi.global_exposure"))
        self._title.setObjectName("Subtitle")
        layout.addWidget(self._title)

        self._value = QLabel("$ 0.00 / $ 0.00")
        self._value.setObjectName("ValueMono")
        layout.addWidget(self._value)

        self._bar = QProgressBar()
        self._bar.setRange(0, 100)
        self._bar.setValue(0)
        self._bar.setFixedHeight(8)
        self._bar.setTextVisible(False)
        layout.addWidget(self._bar)

        self._footer = QLabel(f"{t('kpi.risk_state')}: NORMAL")
        self._footer.setStyleSheet(f"color: {TEXT_MUTED}; font-size: 11px;")
        layout.addWidget(self._footer)

    def update_gauge(
        self,
        exposure_minor_units: int,
        max_exposure_minor_units: int,
        currency: str | None,
        risk_state: str,
    ) -> None:
        curr = (currency or "USD").upper()
        active_amt = format_minor_units(exposure_minor_units, curr)
        max_amt = format_minor_units(max_exposure_minor_units, curr)

        if max_exposure_minor_units > 0:
            ratio = min(100, (exposure_minor_units * 100) // max_exposure_minor_units)
            self._value.setText(f"{active_amt} / {max_amt}")
            self._bar.setValue(ratio)

            if ratio >= 90:
                self._bar.setStyleSheet(
                    f"QProgressBar::chunk {{ background-color: {ACCENT_RED}; }}"
                )
            elif ratio >= 70:
                self._bar.setStyleSheet(
                    f"QProgressBar::chunk {{ background-color: {ACCENT_AMBER}; }}"
                )
            else:
                self._bar.setStyleSheet(
                    f"QProgressBar::chunk {{ background-color: {ACCENT_CYAN}; }}"
                )
        else:
            self._value.setText(active_amt)
            self._bar.setValue(0)

        risk_trans = t(f"risk.{risk_state}")
        risk_text = risk_trans if f"risk.{risk_state}" not in risk_trans else risk_state
        self._footer.setText(f"{t('kpi.risk_state')}: {risk_text}")

    def retranslate(self) -> None:
        self._title.setText(t("kpi.global_exposure"))
