from __future__ import annotations

from collections.abc import Sequence

from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from apps.ui.formatting import format_minor_units
from apps.ui.i18n import t
from apps.ui.theme import ACCENT_CYAN, ACCENT_GREEN, ACCENT_RED, TEXT_MUTED
from packages.protocol import OrderSummary, UiDigitRiskConfig


class DerivStrategySummaryWidget(QWidget):
    """Compact, no-scroll strategy outcome and risk control surface."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._orders: tuple[OrderSummary, ...] = ()
        self._risk: tuple[
            int,
            int,
            str | None,
            str,
            int,
            UiDigitRiskConfig | None,
            int,
            int,
            int,
            int,
        ] = (
            0,
            0,
            None,
            "NORMAL",
            0,
            None,
            0,
            0,
            0,
            0,
        )

        root = QVBoxLayout(self)
        root.setContentsMargins(4, 8, 4, 4)
        root.setSpacing(8)

        outcomes = QHBoxLayout()
        outcomes.setSpacing(8)
        self._net = self._outcome_card(outcomes)
        self._gain = self._outcome_card(outcomes)
        self._loss = self._outcome_card(outcomes)
        self._win_rate = self._outcome_card(outcomes)
        root.addLayout(outcomes)

        self._risk_frame = QFrame()
        self._risk_frame.setObjectName("RiskSummary")
        self._risk_frame.setMinimumHeight(205)
        self._risk_frame.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding,
        )
        risk_root = QVBoxLayout(self._risk_frame)
        risk_root.setContentsMargins(16, 12, 16, 12)
        risk_root.setSpacing(10)

        risk_header = QHBoxLayout()
        self._risk_title = QLabel()
        self._risk_title.setObjectName("Title")
        risk_header.addWidget(self._risk_title)
        self._scope = QLabel()
        self._scope.setObjectName("Subtitle")
        risk_header.addWidget(self._scope, 1)
        self._risk_state = QLabel()
        self._risk_state.setObjectName("StatusPillOnline")
        risk_header.addWidget(self._risk_state)
        risk_root.addLayout(risk_header)

        metrics = QGridLayout()
        metrics.setContentsMargins(0, 0, 0, 0)
        metrics.setHorizontalSpacing(10)
        metrics.setVerticalSpacing(10)
        self._risk_metric_frames: list[QFrame] = []
        self._exposure = self._risk_metric(metrics, 0)
        self._stop_loss = self._risk_metric(metrics, 1)
        self._take_profit = self._risk_metric(metrics, 2)
        self._consecutive = self._risk_metric(metrics, 3)
        self._cooldown = self._risk_metric(metrics, 4)
        self._stake = self._risk_metric(metrics, 5)
        risk_root.addLayout(metrics, 1)

        self._exposure_bar = QProgressBar()
        self._exposure_bar.setRange(0, 1000)
        self._exposure_bar.setValue(0)
        self._exposure_bar.setTextVisible(False)
        self._exposure_bar.setFixedHeight(8)
        risk_root.addWidget(self._exposure_bar)
        root.addWidget(self._risk_frame, 1)
        self.retranslate()

    @staticmethod
    def _outcome_card(layout: QHBoxLayout) -> tuple[QLabel, QLabel, QLabel]:
        frame = QFrame()
        frame.setObjectName("OutcomeCard")
        frame.setMinimumHeight(68)
        box = QVBoxLayout(frame)
        box.setContentsMargins(11, 7, 11, 7)
        box.setSpacing(1)
        caption = QLabel()
        caption.setObjectName("MetricCaption")
        value = QLabel("—")
        value.setObjectName("MetricValue")
        detail = QLabel()
        detail.setObjectName("MetricDetail")
        box.addWidget(caption)
        box.addWidget(value)
        box.addWidget(detail)
        layout.addWidget(frame, 1)
        return caption, value, detail

    def _risk_metric(self, layout: QGridLayout, index: int) -> tuple[QLabel, QLabel]:
        frame = QFrame()
        frame.setObjectName("RiskMetricCard")
        box = QVBoxLayout(frame)
        box.setContentsMargins(12, 8, 12, 8)
        box.setSpacing(4)
        caption = QLabel()
        caption.setObjectName("MetricCaption")
        value = QLabel("—")
        value.setObjectName("RiskMetricValue")
        box.addWidget(caption)
        box.addWidget(value)
        box.addStretch()
        row, column = divmod(index, 3)
        layout.addWidget(frame, row, column)
        layout.setColumnStretch(column, 1)
        layout.setRowStretch(row, 1)
        self._risk_metric_frames.append(frame)
        return caption, value

    def update_results(self, orders: Sequence[OrderSummary]) -> None:
        self._orders = tuple(orders)
        settled = tuple(
            item
            for item in self._orders
            if item.state == "SETTLED" and item.realized_pnl_minor_units is not None
        )
        gains = tuple(item for item in settled if (item.realized_pnl_minor_units or 0) > 0)
        losses = tuple(item for item in settled if (item.realized_pnl_minor_units or 0) < 0)
        decided = len(gains) + len(losses)
        win_rate = len(gains) * 100 / decided if decided else 0.0
        currencies = {item.currency for item in settled}

        if len(currencies) == 1:
            currency = next(iter(currencies))
            gain_amount = sum(item.realized_pnl_minor_units or 0 for item in gains)
            loss_amount = sum(item.realized_pnl_minor_units or 0 for item in losses)
            net_amount = gain_amount + loss_amount
            self._net[1].setText(format_minor_units(net_amount, currency, positive_sign=True))
            self._gain[1].setText(format_minor_units(gain_amount, currency, positive_sign=True))
            self._loss[1].setText(format_minor_units(loss_amount, currency))
            self._net[1].setStyleSheet(f"color: {ACCENT_GREEN if net_amount >= 0 else ACCENT_RED};")
            self._gain[1].setStyleSheet(f"color: {ACCENT_GREEN};")
            self._loss[1].setStyleSheet(f"color: {ACCENT_RED};")
        else:
            value = t("results.mixed_currency") if currencies else "—"
            for metric in (self._net, self._gain, self._loss):
                metric[1].setText(value)
                metric[1].setStyleSheet(f"color: {TEXT_MUTED};")

        self._gain[2].setText(t("deriv.summary.operations", count=len(gains)))
        self._loss[2].setText(t("deriv.summary.operations", count=len(losses)))
        self._net[2].setText(t("deriv.summary.settled", count=len(settled)))
        self._win_rate[1].setText(f"{win_rate:.1f}%")
        self._win_rate[1].setStyleSheet(f"color: {ACCENT_CYAN};")
        self._win_rate[2].setText(t("deriv.summary.decided", count=decided))
        self._scope.setText(t("deriv.summary.scope", count=len(settled)))

    def update_risk(
        self,
        exposure_minor_units: int,
        max_exposure_minor_units: int,
        currency: str | None,
        risk_state: str,
        consecutive_losses: int,
        config: UiDigitRiskConfig | None,
        cooldown_seconds: int,
        martingale_step: int = 0,
        next_stake_minor_units: int = 0,
        projected_sequence_loss_minor_units: int = 0,
    ) -> None:
        self._risk = (
            exposure_minor_units,
            max_exposure_minor_units,
            currency,
            risk_state,
            consecutive_losses,
            config,
            cooldown_seconds,
            martingale_step,
            next_stake_minor_units,
            projected_sequence_loss_minor_units,
        )
        current_currency = (currency or (config.currency if config is not None else "USD")).upper()
        exposure = format_minor_units(exposure_minor_units, current_currency)
        maximum = format_minor_units(max_exposure_minor_units, current_currency)
        self._exposure[1].setText(f"{exposure} / {maximum}")
        ratio = (
            min(1000, exposure_minor_units * 1000 // max_exposure_minor_units)
            if max_exposure_minor_units > 0
            else 0
        )
        self._exposure_bar.setValue(ratio)

        if config is None:
            self._stop_loss[1].setText("—")
            self._take_profit[1].setText("—")
            self._consecutive[1].setText(str(consecutive_losses))
            self._stake[1].setText("—")
        else:
            self._stop_loss[1].setText(
                format_minor_units(config.daily_stop_loss_minor_units, config.currency)
            )
            self._take_profit[1].setText(
                format_minor_units(config.daily_take_profit_minor_units, config.currency)
            )
            self._consecutive[1].setText(f"{consecutive_losses} / {config.max_consecutive_losses}")
            displayed_stake = (
                next_stake_minor_units if next_stake_minor_units > 0 else config.stake_minor_units
            )
            self._stake[1].setText(
                (
                    f"{format_minor_units(displayed_stake, config.currency)} · "
                    f"M{martingale_step}/{config.martingale_max_steps}"
                )
                if config.martingale_enabled
                else format_minor_units(displayed_stake, config.currency)
            )
            self._stake[1].setToolTip(
                (
                    "Bounded Martingale · perda máxima projetada "
                    + format_minor_units(
                        projected_sequence_loss_minor_units,
                        config.currency,
                    )
                )
                if config.martingale_enabled
                else "Stake fixa"
            )
        self._cooldown[1].setText(
            t("deriv.summary.cooldown_active", seconds=cooldown_seconds)
            if cooldown_seconds > 0
            else t("deriv.summary.ready")
        )

        translated = t(f"risk.{risk_state}")
        state_text = translated if not translated.startswith("risk.") else risk_state
        self._risk_state.setText(state_text)
        healthy = risk_state == "NORMAL"
        self._risk_state.setObjectName("StatusPillOnline" if healthy else "StatusPillOffline")
        self._risk_state.style().unpolish(self._risk_state)
        self._risk_state.style().polish(self._risk_state)

    def retranslate(self) -> None:
        self._net[0].setText(t("deriv.summary.net"))
        self._gain[0].setText(t("deriv.summary.gain"))
        self._loss[0].setText(t("deriv.summary.loss"))
        self._win_rate[0].setText(t("deriv.summary.win_rate"))
        self._risk_title.setText(t("deriv.summary.risk_title"))
        for metric, key in (
            (self._exposure, "deriv.summary.exposure"),
            (self._stop_loss, "deriv.summary.stop_loss"),
            (self._take_profit, "deriv.summary.take_profit"),
            (self._consecutive, "deriv.summary.consecutive"),
            (self._cooldown, "deriv.summary.cooldown"),
            (self._stake, "deriv.summary.stake"),
        ):
            metric[0].setText(t(key))
        self.update_results(self._orders)
        self.update_risk(*self._risk)
