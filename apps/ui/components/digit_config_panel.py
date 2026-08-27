from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from PySide6.QtCore import QRegularExpression, Qt, QTimer, Signal
from PySide6.QtGui import QRegularExpressionValidator
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QSlider,
    QSpinBox,
    QVBoxLayout,
)

from apps.ui.i18n import t
from apps.ui.theme import ACCENT_GREEN, ACCENT_RED
from packages.protocol import UiDigitRiskConfig

_SYMBOLS = (
    "R_10",
    "R_25",
    "R_50",
    "R_75",
    "R_100",
    "1HZ10V",
    "1HZ25V",
    "1HZ50V",
    "1HZ75V",
    "1HZ100V",
)
_MONEY_PATTERN = QRegularExpression(r"^\d{0,7}(?:[\.,]\d{0,2})?$")


def _minor_units(text: str) -> int | None:
    try:
        amount = Decimal(text.strip().replace(",", "."))
    except InvalidOperation:
        return None
    if not amount.is_finite() or amount < 0:
        return None
    minor = (amount * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return int(minor)


def _money_text(minor_units: int) -> str:
    return f"{Decimal(minor_units) / Decimal(100):.2f}"


class DigitConfigPanelWidget(QFrame):
    """Validated DIGITDIFF risk editor; persistence remains exclusively in the Core."""

    config_apply_requested = Signal(object)

    def __init__(self, parent: QFrame | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Surface")
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._dirty = False
        self._loading = False
        self._active_strategy_id = "tail-probability-edge"
        # These are Core-owned safety bounds.  They remain part of the persisted
        # configuration, but are intentionally not editable in the operator UI.
        self._martingale_max_steps = 2
        self._martingale_max_stake_minor_units = 400

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 10, 14, 10)
        root.setSpacing(8)
        heading = QHBoxLayout()
        heading.setSpacing(12)
        self.title = QLabel()
        self.title.setObjectName("Title")
        heading.addWidget(self.title)

        self.auto_symbol_input = QCheckBox()
        self.auto_symbol_input.setChecked(True)
        self.auto_symbol_input.toggled.connect(self._auto_symbol_changed)

        self.disclaimer = QLabel()
        self.disclaimer.setWordWrap(True)
        self.disclaimer.setMinimumWidth(0)
        self.disclaimer.setSizePolicy(
            QSizePolicy.Policy.Ignored,
            QSizePolicy.Policy.Preferred,
        )
        self.disclaimer.setObjectName("GuidanceText")
        heading.addWidget(self.disclaimer, 1)
        heading.addWidget(self.auto_symbol_input)
        root.addLayout(heading)

        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(8)
        root.addLayout(grid)

        validator = QRegularExpressionValidator(_MONEY_PATTERN, self)
        self.stake_input = QLineEdit("1.00")
        self.stop_loss_input = QLineEdit("50.00")
        self.take_profit_input = QLineEdit("30.00")
        for field in (
            self.stake_input,
            self.stop_loss_input,
            self.take_profit_input,
        ):
            field.setValidator(validator)
            field.setPlaceholderText("0.00")
            field.setAccessibleDescription("USD")
            field.textChanged.connect(self._mark_dirty_and_validate)
            field.setMinimumWidth(0)
            field.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        self.max_losses_input = QSpinBox()
        self.max_losses_input.setRange(1, 5)
        self.max_losses_input.setValue(1)
        self.max_losses_input.valueChanged.connect(self._mark_dirty_and_validate)

        self.cooldown_input = QComboBox()
        for seconds in (10, 30, 60):
            self.cooldown_input.addItem(f"{seconds} s", float(seconds))
        self.cooldown_input.setCurrentIndex(1)
        self.cooldown_input.currentIndexChanged.connect(self._mark_dirty_and_validate)

        self.symbol_input = QComboBox()
        self.symbol_input.addItems(_SYMBOLS)
        self.symbol_input.setCurrentText("R_100")
        self.symbol_input.currentIndexChanged.connect(self._mark_dirty_and_validate)

        self.confidence_slider = QSlider(Qt.Orientation.Horizontal)
        self.confidence_slider.setRange(900, 980)
        self.confidence_slider.setSingleStep(5)
        self.confidence_slider.setPageStep(10)
        self.confidence_slider.setValue(925)
        self.confidence_slider.valueChanged.connect(self._confidence_changed)
        self.confidence_value = QLabel("92.5 %")
        self.confidence_value.setObjectName("ValueMono")

        self._labels = [QLabel() for _ in range(7)]
        for label in self._labels:
            label.setWordWrap(True)
            label.setMinimumWidth(0)
        confidence_row = QGridLayout()
        confidence_row.addWidget(self.confidence_slider, 0, 0)
        confidence_row.addWidget(self.confidence_value, 0, 1)
        fields = (
            (self._labels[5], self.symbol_input, 0, 0),
            (self._labels[0], self.stake_input, 0, 1),
            (self._labels[1], self.stop_loss_input, 0, 2),
            (self._labels[2], self.take_profit_input, 0, 3),
            (self._labels[3], self.max_losses_input, 1, 0),
            (self._labels[4], self.cooldown_input, 1, 1),
        )
        for label, widget, row, column in fields:
            field_box = QVBoxLayout()
            field_box.setSpacing(3)
            field_box.addWidget(label)
            field_box.addWidget(widget)
            grid.addLayout(field_box, row, column)
        confidence = QVBoxLayout()
        confidence.setSpacing(3)
        confidence.addWidget(self._labels[6])
        confidence.addLayout(confidence_row)
        grid.addLayout(confidence, 1, 2, 1, 2)
        for column in range(4):
            grid.setColumnStretch(column, 1)

        martingale = QFrame()
        martingale.setObjectName("RiskSummary")
        martingale_grid = QGridLayout(martingale)
        martingale_grid.setContentsMargins(12, 8, 12, 8)
        martingale_grid.setHorizontalSpacing(12)
        self.martingale_enabled_input = QCheckBox()
        self.martingale_enabled_input.setMinimumWidth(0)
        self.martingale_enabled_input.setSizePolicy(
            QSizePolicy.Policy.Preferred,
            QSizePolicy.Policy.Fixed,
        )
        self.martingale_enabled_input.setToolTip(
            "Martingale Delimitado compartilhado pelas três estratégias"
        )
        self.martingale_enabled_input.toggled.connect(self._martingale_changed)
        martingale_grid.addWidget(self.martingale_enabled_input, 0, 0)

        self.martingale_multiplier_input = QComboBox()
        for multiplier in ("1.25", "1.50", "2.00", "2.50", "3.00"):
            self.martingale_multiplier_input.addItem(f"{multiplier}×", multiplier)
        self.martingale_multiplier_input.setCurrentIndex(2)
        self.martingale_multiplier_input.currentIndexChanged.connect(self._mark_dirty_and_validate)

        self._martingale_labels = [QLabel()]
        for label in self._martingale_labels:
            label.setWordWrap(True)
            label.setMinimumWidth(0)
            label.setSizePolicy(
                QSizePolicy.Policy.Ignored,
                QSizePolicy.Policy.Preferred,
            )
        multiplier_box = QVBoxLayout()
        multiplier_box.setSpacing(2)
        multiplier_box.addWidget(self._martingale_labels[0])
        multiplier_box.addWidget(self.martingale_multiplier_input)
        martingale_grid.addLayout(multiplier_box, 0, 1)
        martingale_grid.setColumnStretch(0, 1)
        martingale_grid.setColumnStretch(1, 1)
        root.addWidget(martingale)

        self.risk_projection = QLabel()
        self.risk_projection.setObjectName("Subtitle")
        self.martingale_projection = QLabel()
        self.martingale_projection.setObjectName("Subtitle")
        self.cooldown_status = QLabel()
        self.cooldown_status.setObjectName("Subtitle")

        for projection_label in (
            self.risk_projection,
            self.martingale_projection,
            self.cooldown_status,
        ):
            projection_label.setWordWrap(True)
            projection_label.setMinimumWidth(0)
            projection_label.setSizePolicy(
                QSizePolicy.Policy.Ignored,
                QSizePolicy.Policy.Preferred,
            )

        self.validation_status = QLabel()
        self.validation_status.setWordWrap(True)
        self.validation_status.setMinimumWidth(0)
        self.validation_status.setSizePolicy(
            QSizePolicy.Policy.Ignored,
            QSizePolicy.Policy.Preferred,
        )

        self.apply_button = QPushButton()
        self.apply_button.setObjectName("PrimaryButton")
        self.apply_button.clicked.connect(self._apply)
        footer = QHBoxLayout()
        footer.setSpacing(14)
        projection = QVBoxLayout()
        projection.setSpacing(2)
        projection.addWidget(self.risk_projection)
        projection.addWidget(self.martingale_projection)
        projection.addWidget(self.cooldown_status)
        footer.addLayout(projection, 1)
        footer.addWidget(self.validation_status, 1)
        footer.addWidget(self.apply_button)
        root.addLayout(footer)
        root.addStretch()

        self.retranslate()
        self.symbol_input.setEnabled(not self.auto_symbol_input.isChecked())
        self._martingale_changed()
        self._dirty = False
        self._validate()

    def current_config(self) -> UiDigitRiskConfig | None:
        stake = _minor_units(self.stake_input.text())
        stop = _minor_units(self.stop_loss_input.text())
        take = _minor_units(self.take_profit_input.text())
        if stake is None or stop is None or take is None:
            return None
        try:
            return UiDigitRiskConfig(
                stake_minor_units=stake,
                daily_stop_loss_minor_units=stop,
                daily_take_profit_minor_units=take,
                max_consecutive_losses=self.max_losses_input.value(),
                cooldown_seconds_after_loss=float(self.cooldown_input.currentData()),
                min_quantum_confidence_pct=Decimal(self.confidence_slider.value()) / Decimal(10),
                selected_symbol=self.symbol_input.currentText(),
                currency="USD",
                auto_select_symbol=self.auto_symbol_input.isChecked(),
                active_strategy_id=self._active_strategy_id,
                martingale_enabled=self.martingale_enabled_input.isChecked(),
                martingale_multiplier=Decimal(str(self.martingale_multiplier_input.currentData())),
                martingale_max_steps=self._martingale_max_steps,
                martingale_max_stake_minor_units=self._martingale_max_stake_minor_units,
            )
        except ValueError:
            return None

    def set_config(self, config: UiDigitRiskConfig) -> None:
        if self._dirty:
            return
        self._loading = True
        self._active_strategy_id = config.active_strategy_id
        self.stake_input.setText(_money_text(config.stake_minor_units))
        self.stop_loss_input.setText(_money_text(config.daily_stop_loss_minor_units))
        self.take_profit_input.setText(_money_text(config.daily_take_profit_minor_units))
        self._martingale_max_stake_minor_units = config.martingale_max_stake_minor_units
        self._martingale_max_steps = config.martingale_max_steps
        self.max_losses_input.setValue(config.max_consecutive_losses)
        self.symbol_input.setCurrentText(config.selected_symbol)
        self.auto_symbol_input.setChecked(config.auto_select_symbol)
        self.symbol_input.setEnabled(not config.auto_select_symbol)
        self.confidence_slider.setValue(int(config.min_quantum_confidence_pct * 10))
        cooldown_index = self.cooldown_input.findData(config.cooldown_seconds_after_loss)
        if cooldown_index >= 0:
            self.cooldown_input.setCurrentIndex(cooldown_index)
        self.martingale_enabled_input.setChecked(config.martingale_enabled)
        multiplier_index = self.martingale_multiplier_input.findData(
            str(config.martingale_multiplier)
        )
        if multiplier_index >= 0:
            self.martingale_multiplier_input.setCurrentIndex(multiplier_index)
        self._loading = False
        self._martingale_changed()
        self._validate()

    def set_active_strategy(self, strategy_id: str, *, apply: bool = False) -> None:
        if (
            strategy_id
            not in {
                "tail-probability-edge",
                "selective-differs-edge",
                "parity-regime-edge",
            }
            or strategy_id == self._active_strategy_id
        ):
            return
        self._active_strategy_id = strategy_id
        self._mark_dirty_and_validate()
        if apply:
            config = self.current_config()
            if config is not None:
                self.config_apply_requested.emit(config)

    def set_cooldown_remaining(self, seconds: int) -> None:
        self.cooldown_status.setText(
            t("DIGIT_COOLDOWN_ACTIVE", seconds=seconds)
            if seconds > 0
            else t("DIGIT_COOLDOWN_READY")
        )

    def set_apply_result(self, accepted: bool, reason: str | None = None) -> None:
        self.validation_status.setText(
            t("DIGIT_CONFIG_APPLIED")
            if accepted
            else t("DIGIT_CONFIG_REJECTED", reason=reason or "REJECTED")
        )
        self.validation_status.setStyleSheet(
            f"color: {ACCENT_GREEN if accepted else ACCENT_RED}; font-weight: 700;"
        )
        if accepted:
            self._dirty = False
            original = t("APPLY_CONFIG_BTN")
            self.apply_button.setText("✓ " + t("DIGIT_CONFIG_APPLIED"))
            QTimer.singleShot(1400, lambda: self.apply_button.setText(original))

    def retranslate(self) -> None:
        self.title.setText(t("DIGIT_STRATEGY_TITLE"))
        self.disclaimer.setText(t("DIGIT_CONFIDENCE_DISCLAIMER"))
        keys = (
            "STAKE_LABEL",
            "STOP_LOSS_LABEL",
            "TAKE_PROFIT_LABEL",
            "CONSECUTIVE_LOSS_LABEL",
            "COOLDOWN_LABEL",
            "DIGIT_SYMBOL_LABEL",
            "CONFIDENCE_LABEL",
        )
        for label, key in zip(self._labels, keys, strict=True):
            label.setText(t(key))
        self.martingale_enabled_input.setText(t("MARTINGALE_ENABLED_LABEL"))
        self.auto_symbol_input.setText(t("AUTO_SYMBOL_LABEL"))
        for label, key in zip(
            self._martingale_labels,
            ("MARTINGALE_MULTIPLIER_LABEL",),
            strict=True,
        ):
            label.setText(t(key))
        self.apply_button.setText(t("APPLY_CONFIG_BTN"))
        self._validate()

    def _confidence_changed(self, value: int) -> None:
        self.confidence_value.setText(f"{Decimal(value) / Decimal(10):.1f} %")
        self._mark_dirty_and_validate()

    def _mark_dirty_and_validate(self) -> None:
        if not self._loading:
            self._dirty = True
        self._validate()

    def _auto_symbol_changed(self, enabled: bool) -> None:
        self.symbol_input.setEnabled(not enabled)
        self.symbol_input.setToolTip(t("AUTO_SYMBOL_HELP") if enabled else "")
        self._mark_dirty_and_validate()

    def _martingale_changed(self) -> None:
        enabled = self.martingale_enabled_input.isChecked()
        self.martingale_multiplier_input.setEnabled(enabled)
        if enabled:
            minimum_losses = self._martingale_max_steps + 1
            if self.max_losses_input.value() < minimum_losses:
                self.max_losses_input.setValue(minimum_losses)
        self._mark_dirty_and_validate()

    def _validate(self) -> None:
        config = self.current_config()
        valid = config is not None
        self.apply_button.setEnabled(valid)
        border = ACCENT_GREEN if valid else ACCENT_RED
        for field, minimum in (
            (self.stake_input, 35),
            (self.stop_loss_input, 1),
            (self.take_profit_input, 1),
        ):
            field_valid = (_minor_units(field.text()) or 0) >= minimum
            field.setStyleSheet(f"border: 1px solid {ACCENT_GREEN if field_valid else ACCENT_RED};")
        if config is None:
            self.validation_status.setText(t("DIGIT_CONFIG_INVALID"))
            self.validation_status.setStyleSheet(f"color: {ACCENT_RED}; font-weight: 700;")
            self.risk_projection.setText(t("DIGIT_RISK_PROJECTION_UNAVAILABLE"))
            self.martingale_projection.setText(t("MARTINGALE_PROJECTION_UNAVAILABLE"))
            return
        ratio = Decimal(config.daily_take_profit_minor_units) / Decimal(
            config.daily_stop_loss_minor_units
        )
        self.validation_status.setText(t("DIGIT_CONFIG_VALID"))
        self.validation_status.setStyleSheet(f"color: {border}; font-weight: 700;")
        self.risk_projection.setText(t("DIGIT_RISK_PROJECTION", ratio=f"{ratio:.2f}"))
        if not config.martingale_enabled:
            self.martingale_projection.setText(t("MARTINGALE_DISABLED_STATUS"))
            return
        stakes = tuple(
            min(
                int(
                    (
                        Decimal(config.stake_minor_units) * (config.martingale_multiplier**step)
                    ).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
                ),
                config.martingale_max_stake_minor_units,
            )
            for step in range(config.martingale_max_steps + 1)
        )
        stake_text = " → ".join(_money_text(value) for value in stakes)
        self.martingale_projection.setText(
            t(
                "MARTINGALE_PROJECTION",
                sequence=stake_text,
                loss=_money_text(sum(stakes)),
            )
        )

    def _apply(self) -> None:
        config = self.current_config()
        if config is not None:
            self.config_apply_requested.emit(config)
