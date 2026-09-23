from __future__ import annotations

from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal, InvalidOperation

from PySide6.QtCore import QRegularExpression, Qt, QTimer, Signal
from PySide6.QtGui import QRegularExpressionValidator
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
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
_DIFFERS_SESSION_ID = "payout-routed-differs-session"
_DIFFERS_SESSION_EXPECTED_EV_RATIO = Decimal("-0.019")


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


def _expected_differs_session_toll_minor_units(stake_minor_units: int) -> int:
    toll = Decimal(stake_minor_units) * abs(_DIFFERS_SESSION_EXPECTED_EV_RATIO)
    return int(toll.to_integral_value(rounding=ROUND_CEILING))


class DigitConfigPanelWidget(QFrame):
    """Validated DIGITDIFF risk editor; persistence remains exclusively in the Core."""

    config_apply_requested = Signal(object)
    test_session_reset_requested = Signal()

    def __init__(self, parent: QFrame | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Surface")
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._dirty = False
        self._loading = False
        self._active_strategy_id = "tail-probability-edge"
        self._enabled_strategy_ids = {
            "tail-probability-edge",
            "selective-differs-edge",
            "parity-regime-edge",
            _DIFFERS_SESSION_ID,
        }
        # These are Core-owned safety bounds.  They remain part of the persisted
        # configuration, but are intentionally not editable in the operator UI.
        self._martingale_max_steps = 2
        self._martingale_max_stake_minor_units = 5000

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 8, 14, 8)
        root.setSpacing(6)

        # 1. Heading: Title + Disclaimer on left, Auto-select on right
        heading = QHBoxLayout()
        heading.setSpacing(12)
        title_box = QVBoxLayout()
        title_box.setSpacing(1)
        self.title = QLabel()
        self.title.setObjectName("Title")
        self.title.setStyleSheet("font-size: 13px; font-weight: 800; color: #FFFFFF;")
        title_box.addWidget(self.title)

        self.disclaimer = QLabel()
        self.disclaimer.setWordWrap(True)
        self.disclaimer.setObjectName("GuidanceText")
        self.disclaimer.setStyleSheet("font-size: 11px; color: #64748B;")
        title_box.addWidget(self.disclaimer)
        heading.addLayout(title_box, 1)

        self.auto_symbol_input = QCheckBox()
        self.auto_symbol_input.setChecked(True)
        self.auto_symbol_input.toggled.connect(self._auto_symbol_changed)
        heading.addWidget(self.auto_symbol_input)
        root.addLayout(heading)

        # 2. Strategy Selector Row
        strategy_row = QHBoxLayout()
        strategy_row.setSpacing(10)
        self.selection_mode_input = QComboBox()
        self.selection_mode_input.setMinimumWidth(195)
        self.selection_mode_input.addItem(t("strategy.mode_single"), "single")
        self.selection_mode_input.addItem(t("strategy.mode_multi"), "multi")
        self.selection_mode_input.addItem(t("strategy.mode_stress"), "stress")
        self.selection_mode_input.currentIndexChanged.connect(self._selection_mode_changed)
        strategy_row.addWidget(self.selection_mode_input)

        self.stress_mode_input = QCheckBox(t("strategy.stress_checkbox"))
        self.stress_mode_input.setChecked(False)
        self.stress_mode_input.setToolTip(t("strategy.stress_tooltip"))
        self.stress_mode_input.toggled.connect(self._stress_mode_changed)
        self.stress_mode_input.setVisible(False)
        strategy_row.addWidget(self.stress_mode_input)

        self._strategy_inputs: dict[str, QCheckBox] = {}
        for strategy_id, display_label in (
            ("tail-probability-edge", "Quantum Prime"),
            ("selective-differs-edge", "Nexus Alpha"),
            ("parity-regime-edge", "Titan Vector"),
            (_DIFFERS_SESSION_ID, "Horizon Shield"),
        ):
            checkbox = QCheckBox(display_label)
            checkbox.setChecked(strategy_id == self._active_strategy_id)
            checkbox.toggled.connect(
                lambda checked, selected=strategy_id: self._strategy_checkbox_changed(
                    selected, checked
                )
            )
            self._strategy_inputs[strategy_id] = checkbox
            strategy_row.addWidget(checkbox)
        strategy_row.addStretch()
        root.addLayout(strategy_row)

        self._dirty_banner = QLabel()
        self._dirty_banner.setObjectName("DirtyBanner")
        self._dirty_banner.setStyleSheet(
            "background-color: rgba(245, 158, 11, 0.12); "
            "border: 1px solid rgba(245, 158, 11, 0.40); "
            "color: #F59E0B; "
            "border-radius: 4px; "
            "padding: 4px 8px; "
            "font-size: 11px; "
            "font-weight: 600;"
        )
        self._dirty_banner.setVisible(False)
        root.addWidget(self._dirty_banner)

        # 3. Form Layout: Clean rows with dedicated field containers to eliminate layout collapse
        def _make_field(lbl: QLabel, input_widget: QWidget) -> QWidget:
            box = QWidget()
            box.setFixedHeight(50)
            box_layout = QVBoxLayout(box)
            box_layout.setContentsMargins(0, 0, 0, 0)
            box_layout.setSpacing(2)
            lbl.setFixedHeight(14)
            box_layout.addWidget(lbl)
            input_widget.setFixedHeight(34)
            box_layout.addWidget(input_widget)
            return box

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

        self.stake_input.setToolTip(t("risk.stake_tip"))
        self.stop_loss_input.setToolTip(t("risk.daily_stop_tip"))
        self.take_profit_input.setToolTip(t("risk.daily_take_profit_tip"))

        self.max_losses_input = QSpinBox()
        self.max_losses_input.setRange(1, 5)
        self.max_losses_input.setValue(1)
        self.max_losses_input.setToolTip(t("risk.max_consecutive_losses_tip"))
        self.max_losses_input.valueChanged.connect(self._mark_dirty_and_validate)

        self.cooldown_input = QComboBox()
        for seconds in (10, 30, 60):
            self.cooldown_input.addItem(f"{seconds} s", float(seconds))
        self.cooldown_input.setCurrentIndex(1)
        self.cooldown_input.setToolTip(t("risk.cooldown_tip"))
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
        self.confidence_slider.setFixedHeight(24)
        self.confidence_slider.setToolTip(t("risk.confidence_tip"))
        self.confidence_slider.valueChanged.connect(self._confidence_changed)
        self.confidence_value = QLabel("92.5 %")
        self.confidence_value.setObjectName("ValueMono")
        self.confidence_value.setStyleSheet("font-size: 12px; font-weight: 800; color: #00E5FF;")

        self._labels = [QLabel() for _ in range(7)]
        for label in self._labels:
            label.setObjectName("MetricCaption")
            label.setStyleSheet(
                "font-size: 10px; font-weight: 700; color: #94A3B8; text-transform: uppercase;"
            )
            label.setWordWrap(False)

        conf_box = QWidget()
        conf_box.setFixedHeight(50)
        conf_box_layout = QVBoxLayout(conf_box)
        conf_box_layout.setContentsMargins(0, 0, 0, 0)
        conf_box_layout.setSpacing(2)
        self._labels[6].setFixedHeight(14)
        conf_box_layout.addWidget(self._labels[6])
        confidence_input_row = QHBoxLayout()
        confidence_input_row.setSpacing(8)
        confidence_input_row.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        confidence_input_row.addWidget(self.confidence_slider, 1)
        confidence_input_row.addWidget(self.confidence_value)
        c_wrapper = QWidget()
        c_wrapper.setFixedHeight(34)
        c_wrapper.setLayout(confidence_input_row)
        conf_box_layout.addWidget(c_wrapper)

        row1 = QHBoxLayout()
        row1.setSpacing(14)
        row1.addWidget(_make_field(self._labels[5], self.symbol_input), 1)
        row1.addWidget(_make_field(self._labels[0], self.stake_input), 1)
        row1.addWidget(_make_field(self._labels[1], self.stop_loss_input), 1)
        row1.addWidget(_make_field(self._labels[2], self.take_profit_input), 1)
        root.addLayout(row1)

        row2 = QHBoxLayout()
        row2.setSpacing(14)
        row2.addWidget(_make_field(self._labels[3], self.max_losses_input), 1)
        row2.addWidget(_make_field(self._labels[4], self.cooldown_input), 1)
        row2.addWidget(conf_box, 2)
        root.addLayout(row2)

        # 4. Martingale Settings Frame
        martingale = QFrame()
        martingale.setObjectName("RiskSummary")
        martingale_layout = QHBoxLayout(martingale)
        martingale_layout.setContentsMargins(12, 6, 12, 6)
        martingale_layout.setSpacing(16)

        self.martingale_enabled_input = QCheckBox()
        self.martingale_enabled_input.setMinimumWidth(125)
        self.martingale_enabled_input.setSizePolicy(
            QSizePolicy.Policy.Preferred,
            QSizePolicy.Policy.Fixed,
        )
        self.martingale_enabled_input.setToolTip(t("MARTINGALE_ENABLED_LABEL"))
        self.martingale_enabled_input.toggled.connect(self._martingale_changed)
        martingale_layout.addWidget(self.martingale_enabled_input)

        self._martingale_warning = QLabel()
        self._martingale_warning.setObjectName("MartingaleRiskWarning")
        self._martingale_warning.setStyleSheet(
            "background-color: rgba(239, 68, 68, 0.15); "
            "border: 1px solid rgba(239, 68, 68, 0.40); "
            "color: #EF4444; "
            "border-radius: 4px; "
            "padding: 2px 6px; "
            "font-size: 10px; "
            "font-weight: 700;"
        )
        self._martingale_warning.setVisible(False)
        martingale_layout.addWidget(self._martingale_warning)

        self.martingale_multiplier_input = QComboBox()
        self.martingale_multiplier_input.addItem(t("MARTINGALE_AUTO_OPTION"), "2.00")
        self.martingale_multiplier_input.setEnabled(False)
        self.martingale_multiplier_input.setMinimumHeight(34)
        self.martingale_multiplier_input.currentIndexChanged.connect(self._mark_dirty_and_validate)

        self._martingale_labels = [QLabel()]
        self._martingale_labels[0].setObjectName("MetricCaption")
        self._martingale_labels[0].setStyleSheet(
            "font-size: 10px; font-weight: 700; color: #94A3B8; text-transform: uppercase;"
        )

        multiplier_box = QHBoxLayout()
        multiplier_box.setSpacing(8)
        multiplier_box.addWidget(self._martingale_labels[0])
        multiplier_box.addWidget(self.martingale_multiplier_input, 1)
        martingale_layout.addLayout(multiplier_box, 1)
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
            projection_label.setStyleSheet("font-size: 11px; color: #64748B;")

        self.validation_status = QLabel()
        self.validation_status.setWordWrap(True)
        self.validation_status.setStyleSheet("font-size: 11px; font-weight: 700; color: #10B981;")

        self.apply_button = QPushButton()
        self.apply_button.setObjectName("PrimaryButton")
        self.apply_button.setFixedHeight(34)
        self.apply_button.clicked.connect(self._apply)
        self.reset_session_button = QPushButton()
        self.reset_session_button.setObjectName("SecondaryButton")
        self.reset_session_button.setFixedHeight(34)
        self.reset_session_button.clicked.connect(self.test_session_reset_requested.emit)
        footer = QHBoxLayout()
        footer.setSpacing(14)
        projection = QVBoxLayout()
        projection.setSpacing(3)
        projection.addWidget(self.risk_projection)
        projection.addWidget(self.martingale_projection)
        projection.addWidget(self.cooldown_status)
        footer.addLayout(projection, 2)
        footer.addWidget(self.validation_status, 1)
        footer.addWidget(self.reset_session_button)
        footer.addWidget(self.apply_button)
        root.addLayout(footer)
        root.addStretch()

        self.retranslate()
        self.symbol_input.setEnabled(not self.auto_symbol_input.isChecked())
        self._martingale_changed()
        self._dirty = False
        self._dirty_banner.setVisible(False)
        self.apply_button.setStyleSheet("")
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
                enabled_strategy_ids=frozenset(
                    set(self._strategy_inputs)
                    if self.selection_mode_input.currentData() == "stress"
                    else self._enabled_strategy_ids
                ),
                selection_mode=str(self.selection_mode_input.currentData()),
                stress_test_all_strategies_enabled=False,
                martingale_enabled=self.martingale_enabled_input.isChecked(),
                martingale_multiplier=Decimal(str(self.martingale_multiplier_input.currentData())),
                martingale_max_steps=self._martingale_max_steps,
                # The hidden cap follows the operator-visible daily stop.  The Core
                # still applies global/per-symbol exposure and remaining-loss limits.
                martingale_max_stake_minor_units=stop,
            )
        except ValueError:
            return None

    def set_config(self, config: UiDigitRiskConfig) -> None:
        if self._dirty:
            return
        self._loading = True
        self._active_strategy_id = config.active_strategy_id
        self._enabled_strategy_ids = set(config.enabled_strategy_ids)
        mode = config.selection_mode
        mode_index = self.selection_mode_input.findData(mode)
        if mode_index >= 0:
            self.selection_mode_input.setCurrentIndex(mode_index)
        self.stress_mode_input.setChecked(mode == "stress")
        for strategy_id, checkbox in self._strategy_inputs.items():
            checkbox.setChecked(
                mode == "stress"
                or strategy_id
                in ({config.active_strategy_id} if mode == "single" else self._enabled_strategy_ids)
            )
        self.stake_input.setText(_money_text(config.stake_minor_units))
        self.stop_loss_input.setText(_money_text(config.daily_stop_loss_minor_units))
        self.take_profit_input.setText(_money_text(config.daily_take_profit_minor_units))
        self._martingale_max_stake_minor_units = config.daily_stop_loss_minor_units
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
        self._martingale_changed()
        self._loading = False
        self._dirty = False
        self._dirty_banner.setVisible(False)
        self.apply_button.setStyleSheet("")
        self._validate()

    def set_active_strategy(self, strategy_id: str, *, apply: bool = False) -> None:
        if (
            strategy_id
            not in {
                "tail-probability-edge",
                "selective-differs-edge",
                "parity-regime-edge",
                _DIFFERS_SESSION_ID,
            }
            or strategy_id == self._active_strategy_id
        ):
            return
        self._active_strategy_id = strategy_id
        if self.selection_mode_input.currentData() == "single":
            self._loading = True
            for item_id, checkbox in self._strategy_inputs.items():
                checkbox.setChecked(item_id == self._active_strategy_id)
            self._loading = False
        if apply:
            self._mark_dirty_and_validate()
            config = self.current_config()
            if config is not None:
                self.config_apply_requested.emit(config)
        else:
            self._validate()

    def set_cooldown_remaining(self, seconds: int) -> None:
        self.cooldown_status.setText(
            t("DIGIT_COOLDOWN_ACTIVE", seconds=seconds)
            if seconds > 0
            else t("DIGIT_COOLDOWN_READY")
        )

    def _stress_mode_changed(self, checked: bool) -> None:
        if self._loading:
            return
        if checked:
            self._loading = True
            stress_index = self.selection_mode_input.findData("stress")
            if stress_index >= 0:
                self.selection_mode_input.setCurrentIndex(stress_index)
            self._enabled_strategy_ids = set(self._strategy_inputs)
            for checkbox in self._strategy_inputs.values():
                checkbox.setChecked(True)
            self._loading = False
        elif self.selection_mode_input.currentData() == "stress":
            self._set_selection_mode("single")
        self._mark_dirty_and_validate()

    def _strategy_selection_changed(self) -> None:
        if self._loading:
            return
        self._enabled_strategy_ids = {
            strategy_id
            for strategy_id, checkbox in self._strategy_inputs.items()
            if checkbox.isChecked()
        }
        all_enabled = len(self._enabled_strategy_ids) == len(self._strategy_inputs)
        self._loading = True
        self.stress_mode_input.setChecked(all_enabled)
        self._loading = False
        self._mark_dirty_and_validate()

    def _strategy_checkbox_changed(self, strategy_id: str, checked: bool) -> None:
        if self._loading:
            return
        if self.selection_mode_input.currentData() == "single" and checked:
            self._loading = True
            self._active_strategy_id = strategy_id
            for item_id, checkbox in self._strategy_inputs.items():
                if item_id != strategy_id:
                    checkbox.setChecked(False)
            self._loading = False
        elif self.selection_mode_input.currentData() == "single" and not checked:
            if not any(item.isChecked() for item in self._strategy_inputs.values()):
                self._active_strategy_id = ""
        self._strategy_selection_changed()

    def _set_selection_mode(self, mode: str) -> None:
        index = self.selection_mode_input.findData(mode)
        if index < 0:
            return
        self._loading = True
        self.selection_mode_input.setCurrentIndex(index)
        self.stress_mode_input.setChecked(mode == "stress")
        if mode == "single":
            for item_id, checkbox in self._strategy_inputs.items():
                checkbox.setChecked(item_id == self._active_strategy_id)
        elif mode == "stress":
            for checkbox in self._strategy_inputs.values():
                checkbox.setChecked(True)
        self._loading = False

    def _selection_mode_changed(self, _index: int) -> None:
        if self._loading:
            return
        mode = str(self.selection_mode_input.currentData())
        self._set_selection_mode(mode)
        self._mark_dirty_and_validate()

    def _execution_strategy_ids(self) -> set[str]:
        mode = str(self.selection_mode_input.currentData())
        if mode == "stress":
            return set(self._strategy_inputs)
        if mode == "single":
            return {self._active_strategy_id} if self._active_strategy_id else set()
        return set(self._enabled_strategy_ids)

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
            self._dirty_banner.setVisible(False)
            self.apply_button.setStyleSheet("")
            original = t("APPLY_CONFIG_BTN")
            self.apply_button.setText("✓ " + t("DIGIT_CONFIG_APPLIED"))
            QTimer.singleShot(1400, lambda: self.apply_button.setText(original))

    def retranslate(self) -> None:
        self.title.setText(t("DIGIT_STRATEGY_TITLE"))
        self.disclaimer.setText(t("DIGIT_CONFIDENCE_DISCLAIMER"))
        self._dirty_banner.setText(t("risk.dirty_warning"))
        self._martingale_warning.setText(t("risk.martingale_warning"))
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
        if self.martingale_multiplier_input.count() > 0:
            self.martingale_multiplier_input.setItemText(0, t("MARTINGALE_AUTO_OPTION"))
        self.apply_button.setText(t("APPLY_CONFIG_BTN"))
        self.reset_session_button.setText(t("RESET_DEMO_SESSION_BTN"))

        # Retranslate selection modes & stress checkbox
        mode_keys = {
            "single": "strategy.mode_single",
            "multi": "strategy.mode_multi",
            "stress": "strategy.mode_stress",
        }
        for i in range(self.selection_mode_input.count()):
            data = str(self.selection_mode_input.itemData(i))
            if data in mode_keys:
                self.selection_mode_input.setItemText(i, t(mode_keys[data]))
        self.stress_mode_input.setText(t("strategy.stress_checkbox"))
        self.stress_mode_input.setToolTip(t("strategy.stress_tooltip"))

        # Retranslate risk tooltips
        self.stake_input.setToolTip(t("risk.stake_tip"))
        self.stop_loss_input.setToolTip(t("risk.daily_stop_tip"))
        self.take_profit_input.setToolTip(t("risk.daily_take_profit_tip"))
        self.max_losses_input.setToolTip(t("risk.max_consecutive_losses_tip"))
        self.cooldown_input.setToolTip(t("risk.cooldown_tip"))
        self.confidence_slider.setToolTip(t("risk.confidence_tip"))

        self._validate()

    def _confidence_changed(self, value: int) -> None:
        self.confidence_value.setText(f"{Decimal(value) / Decimal(10):.1f} %")
        self._mark_dirty_and_validate()

    def _mark_dirty_and_validate(self) -> None:
        if self._loading:
            return
        self._dirty = True
        self._dirty_banner.setVisible(True)
        self.apply_button.setStyleSheet(
            "background-color: #00E5FF; color: #000000; font-weight: 800;"
        )
        self._validate()

    def _auto_symbol_changed(self, enabled: bool) -> None:
        self.symbol_input.setEnabled(not enabled)
        self.symbol_input.setToolTip(t("AUTO_SYMBOL_HELP") if enabled else "")
        self._mark_dirty_and_validate()

    def _martingale_changed(self) -> None:
        enabled = self.martingale_enabled_input.isChecked()
        self.martingale_multiplier_input.setEnabled(False)
        self._martingale_warning.setVisible(enabled)
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
            stake = _minor_units(self.stake_input.text())
            stop = _minor_units(self.stop_loss_input.text())
            take = _minor_units(self.take_profit_input.text())
            if stake is None or stake < 35:
                reason = t("DIGIT_CONFIG_STAKE_INVALID")
            elif stop is None or stop <= 0:
                reason = t("DIGIT_CONFIG_STOP_INVALID")
            elif take is None or take <= 0:
                reason = t("DIGIT_CONFIG_TAKE_INVALID")
            else:
                reason = t("DIGIT_CONFIG_INVALID")
            self.validation_status.setText(reason)
            self.validation_status.setStyleSheet(f"color: {ACCENT_RED}; font-weight: 700;")
            self.risk_projection.setText(t("DIGIT_RISK_PROJECTION_UNAVAILABLE"))
            self.martingale_projection.setText(t("MARTINGALE_PROJECTION_UNAVAILABLE"))
            return
        ratio = Decimal(config.daily_take_profit_minor_units) / Decimal(
            config.daily_stop_loss_minor_units
        )
        self.validation_status.setText(t("DIGIT_CONFIG_VALID"))
        self.validation_status.setStyleSheet(f"color: {border}; font-weight: 700;")
        risk_projection = t("DIGIT_RISK_PROJECTION", ratio=f"{ratio:.2f}")
        if _DIFFERS_SESSION_ID in config.enabled_strategy_ids:
            risk_projection = (
                risk_projection
                + " · "
                + t(
                    "DIFFERS_SESSION_EXPECTED_TOLL",
                    amount=_money_text(
                        _expected_differs_session_toll_minor_units(config.stake_minor_units)
                    ),
                )
            )
        self.risk_projection.setText(risk_projection)
        if not config.martingale_enabled:
            self.martingale_projection.setText(t("MARTINGALE_DISABLED_STATUS"))
            return
        example_ratio = (
            Decimal("0.10")
            if config.active_strategy_id == "selective-differs-edge"
            else Decimal("0.90")
        )
        recovery = int(
            (Decimal(config.stake_minor_units) / example_ratio).quantize(
                Decimal("1"),
                rounding=ROUND_HALF_UP,
            )
        )
        remaining_stop = max(0, config.daily_stop_loss_minor_units - config.stake_minor_units)
        self.martingale_projection.setText(
            t(
                "MARTINGALE_PROJECTION",
                recovery=_money_text(recovery),
                ratio=f"{example_ratio * 100:.0f}",
                remaining=_money_text(remaining_stop),
            )
        )

    def _apply(self) -> None:
        config = self.current_config()
        if config is not None:
            self.config_apply_requested.emit(config)
