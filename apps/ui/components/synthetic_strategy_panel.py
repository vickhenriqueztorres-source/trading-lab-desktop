from __future__ import annotations

from datetime import UTC, datetime
from typing import TypedDict

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QVBoxLayout,
    QWidget,
)

from apps.ui.components.digit_config_panel import DigitConfigPanelWidget
from apps.ui.theme import ACCENT_AMBER, ACCENT_CYAN, ACCENT_GREEN, TEXT_MUTED
from packages.protocol import UiDerivStrategyStatus, UiDigitRiskConfig


class _StrategyDetails(TypedDict):
    contract: str
    parameters: tuple[tuple[str, str], ...]
    evidence: str


_STRATEGY_DETAILS: dict[str, _StrategyDetails] = {
    "tail-probability-edge": {
        "contract": "DIGITOVER / DIGITUNDER · 1 tick",
        "parameters": (
            ("Contexto", "Paridade do dígito anterior"),
            ("Janelas", "200 · 350 · 500 ticks"),
            ("Barreiras", "Over 2/3/4 · Under 7/6/5"),
            ("Confirmação", "Limite Wilson de 99%"),
        ),
        "evidence": "Hipótese: concentração condicional nas caudas baixa ou alta",
    },
    "selective-differs-edge": {
        "contract": "DIGITDIFF · 1 tick",
        "parameters": (
            ("Seleção", "Dígito menos provável"),
            ("Janelas", "200 · 350 · 500 ticks"),
            ("Piso de pesquisa", "92,25% conservador"),
            ("Confirmação", "Mesmo dígito nas 3 janelas"),
        ),
        "evidence": "Hipótese: exclusão probabilística com correção de seleção",
    },
    "parity-regime-edge": {
        "contract": "DIGITEVEN / DIGITODD · 1 tick",
        "parameters": (
            ("Contexto", "Paridade do dígito anterior"),
            ("Janelas", "200 · 350 · 500 ticks"),
            ("Piso de pesquisa", "52,00% conservador"),
            ("Confirmação", "Mesmo regime nas 3 janelas"),
        ),
        "evidence": "Hipótese: dependência condicional entre par e ímpar",
    },
    "payout-routed-differs-session": {
        "contract": "DIGITDIFF · 1 tick",
        "parameters": (
            ("Sessão", "Símbolo ativo escolhido pelo cliente/ranking existente"),
            ("Barreira", "Fixa em 0"),
            ("Payout observado", "0,090000"),
            ("Piso de segurança", "0,088000"),
        ),
        "evidence": (
            "Sem histórico de dígitos; usa proposal fresca apenas como verificação de payout"
        ),
    },
}


class SyntheticStrategyConfigWidget(QWidget):
    config_apply_requested = Signal(object)
    test_session_reset_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._strategy_id = "tail-probability-edge"
        root = QVBoxLayout(self)
        root.setContentsMargins(4, 6, 4, 4)
        root.setSpacing(0)

        header = QFrame()
        header.setObjectName("Card")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(14, 10, 14, 10)
        self._contract = QLabel()
        self._contract.setObjectName("ValueMono")
        header_layout.addWidget(self._contract, 1)
        self._mode = QLabel("VALIDAÇÃO DEMO · REAL SOMENTE LEITURA")
        self._mode.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._mode.setObjectName("StatusPillOffline")
        header_layout.addWidget(self._mode)
        root.addWidget(header)
        header.setVisible(False)

        parameters = QFrame()
        parameters.setObjectName("RiskSummary")
        grid = QGridLayout(parameters)
        grid.setContentsMargins(14, 10, 14, 10)
        grid.setHorizontalSpacing(20)
        grid.setVerticalSpacing(7)
        self._parameter_labels: list[tuple[QLabel, QLabel]] = []
        for row in range(4):
            name = QLabel()
            name.setObjectName("MetricCaption")
            value = QLabel()
            value.setObjectName("RiskMetricValue")
            grid.addWidget(name, row, 0)
            grid.addWidget(value, row, 1)
            self._parameter_labels.append((name, value))
        grid.setColumnStretch(1, 1)
        root.addWidget(parameters)
        parameters.setVisible(False)

        self._evidence = QLabel()
        self._evidence.setWordWrap(True)
        self._evidence.setObjectName("SafetyNotice")
        root.addWidget(self._evidence)
        self._evidence.setVisible(False)
        self.risk_panel = DigitConfigPanelWidget()
        self.risk_panel.config_apply_requested.connect(self.config_apply_requested.emit)
        self.risk_panel.test_session_reset_requested.connect(self.test_session_reset_requested.emit)
        root.addWidget(self.risk_panel, 1)
        self.set_strategy(self._strategy_id)

    def set_strategy(self, strategy_id: str, *, apply_execution_selection: bool = False) -> None:
        if strategy_id not in _STRATEGY_DETAILS:
            return
        self._strategy_id = strategy_id
        details = _STRATEGY_DETAILS[strategy_id]
        self._contract.setText(str(details["contract"]))
        for labels, values in zip(
            self._parameter_labels,
            details["parameters"],
            strict=True,
        ):
            labels[0].setText(str(values[0]).upper())
            labels[1].setText(str(values[1]))
        self._evidence.setText(
            f"{details['evidence']} · Bounded Martingale opcional, compartilhado pelas 3 "
            "estratégias e sempre validado pelo Risk Ledger. Ordens automáticas só podem ser "
            "enviadas na conta Demo conectada, com o bot ligado e um sinal confirmado."
        )
        self.risk_panel.setToolTip(
            f"{details['contract']} · {details['evidence']} · "
            "limites compartilhados pelas três estratégias"
        )
        self.risk_panel.set_active_strategy(
            strategy_id,
            apply=apply_execution_selection,
        )

    def set_risk_config(self, config: UiDigitRiskConfig) -> None:
        self.risk_panel.set_config(config)

    def set_cooldown_remaining(self, seconds: int) -> None:
        self.risk_panel.set_cooldown_remaining(seconds)

    def set_apply_result(self, accepted: bool, reason: str | None = None) -> None:
        self.risk_panel.set_apply_result(accepted, reason)


class SyntheticStrategyLiveWidget(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._strategy_id = "tail-probability-edge"
        self._statuses: dict[str, UiDerivStrategyStatus] = {}

        root = QVBoxLayout(self)
        root.setContentsMargins(4, 4, 4, 4)
        root.setSpacing(8)

        status_frame = QFrame()
        status_frame.setObjectName("Card")
        status_layout = QHBoxLayout(status_frame)
        status_layout.setContentsMargins(14, 10, 14, 10)
        self._state = QLabel("AGUARDANDO DADOS")
        self._state.setObjectName("StatusPillOffline")
        self._state.setAlignment(Qt.AlignmentFlag.AlignCenter)
        status_layout.addWidget(self._state)
        self._market = QLabel("—")
        self._market.setObjectName("ValueMono")
        status_layout.addWidget(self._market, 1)
        self._reason = QLabel("STRATEGY_WAITING_FOR_MARKET_DATA")
        self._reason.setObjectName("Subtitle")
        status_layout.addWidget(self._reason)
        root.addWidget(status_frame)

        warmup = QFrame()
        warmup.setObjectName("RiskSummary")
        warmup_layout = QVBoxLayout(warmup)
        warmup_layout.setContentsMargins(14, 10, 14, 10)
        self._warmup_text = QLabel("Aquecimento 0 / 0")
        self._warmup_text.setObjectName("MetricCaption")
        warmup_layout.addWidget(self._warmup_text)
        self._warmup = QProgressBar()
        self._warmup.setRange(0, 1000)
        self._warmup.setTextVisible(False)
        self._warmup.setFixedHeight(7)
        warmup_layout.addWidget(self._warmup)
        root.addWidget(warmup)

        signal = QFrame()
        signal.setObjectName("Card")
        grid = QGridLayout(signal)
        grid.setContentsMargins(14, 10, 14, 10)
        self._signal_time = self._metric(grid, 0, "ÚLTIMO SINAL SHADOW")
        self._signal_contract = self._metric(grid, 1, "CONTRATO / BARREIRA")
        self._signal_probability = self._metric(grid, 2, "PROB. CONSERVADORA / PISO")
        self._analysis_latency = self._metric(grid, 3, "LATÊNCIA DE ANÁLISE")
        root.addWidget(signal)

        notice = QLabel(
            "O motor analisa os ticks da Deriv e pode comprar contratos somente na conta Demo. "
            "Sem vantagem estatística conservadora, ele permanece monitorando e não força "
            "uma entrada."
        )
        notice.setWordWrap(True)
        notice.setStyleSheet(f"color: {TEXT_MUTED};")
        root.addWidget(notice)
        root.addStretch()

    @staticmethod
    def _metric(layout: QGridLayout, column: int, caption: str) -> QLabel:
        title = QLabel(caption)
        title.setObjectName("MetricCaption")
        value = QLabel("—")
        value.setObjectName("RiskMetricValue")
        layout.addWidget(title, 0, column)
        layout.addWidget(value, 1, column)
        layout.setColumnStretch(column, 1)
        return value

    def set_strategy(self, strategy_id: str) -> None:
        if strategy_id not in _STRATEGY_DETAILS:
            return
        self._strategy_id = strategy_id
        self._render()

    def update_statuses(self, statuses: tuple[UiDerivStrategyStatus, ...]) -> None:
        self._statuses = {item.strategy_id: item for item in statuses}
        self._render()

    def _render(self) -> None:
        status = self._statuses.get(self._strategy_id)
        if status is None:
            self._state.setText("AGUARDANDO DADOS")
            self._state.setObjectName("StatusPillOffline")
            self._market.setText("—")
            self._reason.setText("STRATEGY_WAITING_FOR_MARKET_DATA")
            self._warmup_text.setText("Aquecimento 0 / 0")
            self._warmup.setValue(0)
            for label in (
                self._signal_time,
                self._signal_contract,
                self._signal_probability,
                self._analysis_latency,
            ):
                label.setText("—")
            return
        labels = {
            "WARMING_UP": ("AQUECENDO", "StatusPillOffline", ACCENT_AMBER),
            "MONITORING": ("MONITORANDO", "StatusPillOnline", ACCENT_CYAN),
            "SHADOW_SIGNAL": ("SINAL DETECTADO", "StatusPillOnline", ACCENT_GREEN),
            "DATA_BLOCKED": ("DADOS BLOQUEADOS", "StatusPillOffline", ACCENT_AMBER),
        }
        state_text, object_name, _color = labels.get(
            status.signal_state,
            (status.signal_state, "StatusPillOffline", ACCENT_AMBER),
        )
        self._state.setText(state_text)
        self._state.setObjectName(object_name)
        self._state.style().unpolish(self._state)
        self._state.style().polish(self._state)
        self._market.setText(status.markets)
        self._reason.setText(status.reason_code)
        self._warmup_text.setText(f"Aquecimento {status.warmup_current} / {status.warmup_required}")
        progress = (
            0
            if status.warmup_required <= 0
            else int(status.warmup_current * 1000 / status.warmup_required)
        )
        self._warmup.setValue(progress)
        if status.last_signal_epoch is None:
            signal_time = "—"
        else:
            signal_time = datetime.fromtimestamp(status.last_signal_epoch, tz=UTC).strftime(
                "%d/%m %H:%M:%S UTC"
            )
        self._signal_time.setText(signal_time)
        contract = status.last_contract_type or "—"
        if status.last_barrier is not None:
            contract = f"{contract} · {status.last_barrier}"
        self._signal_contract.setText(contract)
        if status.estimated_probability_pct is None:
            probability = "—"
        else:
            probability = (
                f"{status.estimated_probability_pct}% / {status.required_probability_pct or '—'}%"
            )
        self._signal_probability.setText(probability)
        self._analysis_latency.setText(f"{status.analysis_latency_microseconds} µs")
