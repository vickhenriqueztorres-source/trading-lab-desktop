from __future__ import annotations

from typing import Any

from PySide6.QtCore import Signal
from PySide6.QtGui import QStandardItemModel
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
        self._entries: dict[str, dict[str, Any]] = {}
        self._practice = True
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
        self._mode = QComboBox()
        self._mode.addItems(["SINGLE", "AUTO"])
        form.addRow("Modo", self._mode)
        self._strategy = QComboBox()
        self._strategy.addItem("RSI 30/70 (não validado · apenas Demo)", "iqoption-rsi-demo")
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
        self._timeframe = QLabel("M1")
        form.addRow("Timeframe (manifesto · somente leitura)", self._timeframe)
        self._mode.currentIndexChanged.connect(self._sync_selection)
        self._strategy.currentIndexChanged.connect(self._sync_selection)
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

    def set_manifest(self, manifest: dict[str, Any]) -> None:
        selected = self._strategy.currentData()
        self._entries = {
            str(e["key"]): e
            for e in manifest.get("strategies", [])
            if isinstance(e, dict)
            and "key" in e
            and e.get("status") in {"approved", "observation"}
            and e.get("family") in {"F1", "F2", "F3", "F4", "F5"}
        }
        self._strategy.blockSignals(True)
        self._strategy.clear()
        self._strategy.addItem("RSI 30/70 (não validado · apenas Demo)", "iqoption-rsi-demo")
        for key, entry in sorted(self._entries.items()):
            self._strategy.addItem(str(entry.get("display_name_pt", key)), key)
        self._strategy.setCurrentIndex(max(0, self._strategy.findData(selected)))
        self._strategy.blockSignals(False)
        self._sync_selection()

    def set_account_type(self, account_type: str) -> None:
        self._practice = account_type.upper() in {"DEMO", "PRACTICE"}
        self._sync_selection()

    def _sync_selection(self) -> None:
        automatic = self._mode.currentText() == "AUTO"
        self._strategy.setEnabled(not automatic)
        entry = self._entries.get(str(self._strategy.currentData()))
        self._symbol.setEnabled(not automatic and entry is None)
        if automatic:
            self._symbol.setCurrentIndex(self._symbol.findData("AUTO"))
            self._timeframe.setText("Por candidato (manifesto)")
        elif entry is not None:
            asset = str(entry["asset"])
            if self._symbol.findData(asset) < 0:
                self._symbol.addItem(asset, asset)
            self._symbol.setCurrentIndex(self._symbol.findData(asset))
            self._timeframe.setText(str(entry["timeframe"]))
        else:
            if self._symbol.currentData() == "AUTO":
                self._symbol.setCurrentIndex(self._symbol.findData("EURUSD-OTC"))
            self._timeframe.setText("M1 · laboratório Demo")
        local = self._strategy.currentData() == "iqoption-rsi-demo"
        model = self._strategy.model()
        if isinstance(model, QStandardItemModel):
            local_item = model.item(0)
            if local_item is not None:
                local_item.setEnabled(self._practice and not automatic)
            for index in range(1, self._strategy.count()):
                recipe = self._entries.get(str(self._strategy.itemData(index)))
                option = model.item(index)
                if option is not None and recipe is not None:
                    option.setEnabled(self._practice or recipe.get("status") == "approved")
        self._apply.setEnabled(automatic or not local or self._practice)

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
        self._mode.blockSignals(True)
        self._strategy.blockSignals(True)
        self._mode.setCurrentText("AUTO" if config.symbol == "AUTO" else "SINGLE")
        if self._strategy.findData(config.strategy_id) < 0:
            self._strategy.addItem(config.strategy_id, config.strategy_id)
        self._strategy.setCurrentIndex(max(0, self._strategy.findData(config.strategy_id)))
        self._symbol.setCurrentIndex(max(0, self._symbol.findData(config.symbol)))
        self._stake.setValue(config.stake_minor_units / 100)
        self._daily_stop.setValue(config.daily_stop_loss_minor_units / 100)
        self._daily_take.setValue(config.daily_take_profit_minor_units / 100)
        self._losses.setValue(config.max_consecutive_losses)
        self._cooldown.setValue(config.cooldown_seconds_after_loss)
        self._daily_trades.setValue(config.max_daily_trades)
        self._mode.blockSignals(False)
        self._strategy.blockSignals(False)
        self._sync_selection()
        if (
            config.symbol != "AUTO"
            and config.strategy_id != "iqoption-rsi-demo"
            and config.strategy_id not in self._entries
        ):
            self._timeframe.setText({60: "M1", 300: "M5", 900: "M15"}[config.timeframe_seconds])

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
        self._sync_selection()
        automatic = self._mode.currentText() == "AUTO"
        if (
            not automatic
            and self._strategy.currentData() == "iqoption-rsi-demo"
            and not self._practice
        ):
            return
        entry = self._entries.get(str(self._strategy.currentData()))
        config = UiIqOptionRiskConfig(
            strategy_id="AUTO" if automatic else str(self._strategy.currentData()),
            symbol="AUTO" if automatic else str(self._symbol.currentData()),
            timeframe_seconds=60
            if entry is None or automatic
            else {"M1": 60, "M5": 300, "M15": 900}[entry["timeframe"]],
            stake_minor_units=round(self._stake.value() * 100),
            daily_stop_loss_minor_units=round(self._daily_stop.value() * 100),
            daily_take_profit_minor_units=round(self._daily_take.value() * 100),
            max_consecutive_losses=self._losses.value(),
            cooldown_seconds_after_loss=self._cooldown.value(),
            max_daily_trades=self._daily_trades.value(),
        )
        self.config_apply_requested.emit(config)
