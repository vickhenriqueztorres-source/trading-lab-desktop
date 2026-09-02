from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QLabel,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from apps.ui.i18n import t
from packages.protocol import UiIqOptionRiskConfig


class IqOptionStrategyConfigWidget(QFrame):
    """Visible RSI selection and bounded Practice risk controls."""

    config_apply_requested = Signal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._projected_config: UiIqOptionRiskConfig | None = None
        self.setObjectName("Surface")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        self._title = QLabel()
        self._title.setObjectName("Title")
        layout.addWidget(self._title)
        self._notice = QLabel()
        self._notice.setWordWrap(True)
        self._notice.setObjectName("SafetyNotice")
        layout.addWidget(self._notice)

        form = QFormLayout()
        self._strategy = QComboBox()
        self._strategy.addItem("RSI 14 — Bounded Edge (1 Minuto)", "iqoption-rsi-demo")
        form.addRow(t("iq.risk.strategy"), self._strategy)
        self._symbol = QComboBox()
        self._symbol.addItem("⚡ SELEÇÃO AUTOMÁTICA (Todos os Ativos)", "AUTO")
        self._symbol.addItem("EUR/USD OTC", "EURUSD-OTC")
        self._symbol.addItem("GBP/USD OTC", "GBPUSD-OTC")
        self._symbol.addItem("USD/JPY OTC", "USDJPY-OTC")
        self._symbol.addItem("AUD/USD OTC", "AUDUSD-OTC")
        self._symbol.addItem("EUR/JPY OTC", "EURJPY-OTC")
        self._symbol.addItem("GBP/JPY OTC", "GBPJPY-OTC")
        self._symbol.addItem("AUD/CAD OTC", "AUDCAD-OTC")
        self._symbol.addItem("NZD/USD OTC", "NZDUSD-OTC")
        self._symbol.addItem("USD/CAD OTC", "USDCAD-OTC")
        self._symbol.addItem("USD/CHF OTC", "USDCHF-OTC")
        self._symbol.addItem("EUR/USD", "EURUSD")
        self._symbol.addItem("GBP/USD", "GBPUSD")
        self._symbol.addItem("USD/JPY", "USDJPY")
        self._symbol.addItem("AUD/USD", "AUDUSD")
        self._symbol.addItem("EUR/JPY", "EURJPY")
        form.addRow(t("iq.risk.asset"), self._symbol)
        self._stake = self._money_spin(1.00, 100.00, 1.00)
        form.addRow(t("iq.risk.stake"), self._stake)
        self._daily_stop = self._money_spin(0.01, 10_000.00, 10.00)
        form.addRow(t("iq.risk.daily_stop"), self._daily_stop)
        self._daily_take = self._money_spin(0.01, 10_000.00, 10.00)
        form.addRow(t("iq.risk.daily_take"), self._daily_take)
        self._losses = QSpinBox()
        self._losses.setRange(1, 10)
        form.addRow(t("iq.risk.losses"), self._losses)
        self._cooldown = QSpinBox()
        self._cooldown.setRange(0, 3600)
        self._cooldown.setSuffix(" s")
        form.addRow(t("iq.risk.cooldown"), self._cooldown)
        self._daily_trades = QSpinBox()
        self._daily_trades.setRange(1, 100)
        form.addRow(t("iq.risk.daily_trades"), self._daily_trades)
        layout.addLayout(form)

        self._apply = QPushButton()
        self._apply.setObjectName("PrimaryButton")
        self._apply.clicked.connect(self._emit_config)
        layout.addWidget(self._apply)
        self._status = QLabel()
        self._status.setWordWrap(True)
        self._status.setObjectName("Subtitle")
        layout.addWidget(self._status)
        self.set_config(UiIqOptionRiskConfig())
        self.retranslate()

    @staticmethod
    def _money_spin(minimum: float, maximum: float, value: float) -> QDoubleSpinBox:
        spin = QDoubleSpinBox()
        spin.setDecimals(2)
        spin.setRange(minimum, maximum)
        spin.setSingleStep(0.10)
        spin.setPrefix("USD ")
        spin.setValue(value)
        return spin

    def set_config(self, config: UiIqOptionRiskConfig) -> None:
        if config == self._projected_config:
            return
        self._projected_config = config
        self._strategy.setCurrentIndex(max(0, self._strategy.findData(config.strategy_id)))
        self._symbol.setCurrentIndex(max(0, self._symbol.findData(config.symbol)))
        self._stake.setValue(config.stake_minor_units / 100)
        self._daily_stop.setValue(config.daily_stop_loss_minor_units / 100)
        self._daily_take.setValue(config.daily_take_profit_minor_units / 100)
        self._losses.setValue(config.max_consecutive_losses)
        self._cooldown.setValue(config.cooldown_seconds_after_loss)
        self._daily_trades.setValue(config.max_daily_trades)

    def set_apply_result(self, accepted: bool, reason: str | None = None) -> None:
        if accepted:
            self._status.setText(t("iq.risk.applied"))
        else:
            self._status.setText(t("iq.risk.rejected", reason=reason or "UNKNOWN"))

    def retranslate(self) -> None:
        self._title.setText(t("iq.risk.title"))
        self._notice.setText(t("iq.risk.notice"))
        self._apply.setText(t("iq.risk.apply"))
        if not self._status.text():
            self._status.setText(t("iq.risk.ready"))

    def _emit_config(self) -> None:
        config = UiIqOptionRiskConfig(
            strategy_id=str(self._strategy.currentData()),
            symbol=str(self._symbol.currentData()),
            stake_minor_units=round(self._stake.value() * 100),
            daily_stop_loss_minor_units=round(self._daily_stop.value() * 100),
            daily_take_profit_minor_units=round(self._daily_take.value() * 100),
            max_consecutive_losses=self._losses.value(),
            cooldown_seconds_after_loss=self._cooldown.value(),
            max_daily_trades=self._daily_trades.value(),
        )
        self.config_apply_requested.emit(config)
