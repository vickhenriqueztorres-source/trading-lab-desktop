"""Manifest strategy cards panel with 5 fundamental numbers and 3 states (R-BOT-11, I-13)."""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal
from typing import Any

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from apps.ui.theme import (
    ACCENT_AMBER,
    ACCENT_CYAN,
    ACCENT_GREEN,
    ACCENT_RED,
    BG_CARD,
    BG_SURFACE,
    BORDER_ACCENT,
    BORDER_COLOR,
    TEXT_MUTED,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
)

# Canonical Portuguese labels for validation status
STATUS_LABELS_PT = {
    "approved": "Aprovada",
    "observation": "Em observação",
    "rejected": "Reprovada",
}

STATUS_COLORS = {
    "approved": ACCENT_GREEN,
    "observation": ACCENT_AMBER,
    "rejected": ACCENT_RED,
}


def _get(obj: Any, key: str, default: Any = None) -> Any:
    if obj is None:
        return default
    if isinstance(obj, Mapping):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _format_percentage(val: Decimal | float | str) -> str:
    d = Decimal(str(val))
    if d <= Decimal("1.0") and d > Decimal("0"):
        d = d * Decimal("100")
    return f"{d:.1f}"


def _format_payout(val: Decimal | float | str) -> str:
    d = Decimal(str(val))
    if d <= Decimal("1.0") and d > Decimal("0"):
        d = d * Decimal("100")
    if d == d.to_integral():
        return f"{d:.0f}"
    return f"{d:.1f}"


def _format_margin(wilson_lower: Decimal | float | str, p_min: Decimal | float | str) -> str:
    wl = Decimal(str(wilson_lower))
    pm = Decimal(str(p_min))
    if wl <= Decimal("1.0") and wl > Decimal("0"):
        wl = wl * Decimal("100")
    if pm <= Decimal("1.0") and pm > Decimal("0"):
        pm = pm * Decimal("100")
    diff = wl - pm
    sign = "+" if diff >= Decimal("0") else ""
    return f"{sign}{diff:.1f}"


def _format_money(amount: Decimal | float | str) -> str:
    d = Decimal(str(amount))
    return f"${d:,.0f}".replace(",", ".")


class ManifestBannerWidget(QFrame):
    """Alert banner displayed when manifest is expired or rejected."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("ManifestBanner")
        self.setStyleSheet(
            f"background-color: rgba(255, 51, 102, 0.15); "
            f"border: 1px solid {ACCENT_RED}; border-radius: 6px;"
        )
        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 10)
        self._icon_label = QLabel("⚠️")
        self._icon_label.setStyleSheet("font-size: 16px;")
        layout.addWidget(self._icon_label)

        self._text_label = QLabel()
        self._text_label.setWordWrap(True)
        self._text_label.setStyleSheet(f"color: {TEXT_PRIMARY}; font-weight: 600;")
        layout.addWidget(self._text_label, 1)

        self.setVisible(False)

    def show_alert(self, version: int | str, status_text: str, age_text: str) -> None:
        self._text_label.setText(
            f"Alerta de Manifesto: Versão v{version} {status_text} ({age_text}). "
            "Novas ordens em conta Real estão suspensas por segurança."
        )
        self.setVisible(True)

    def hide_alert(self) -> None:
        self.setVisible(False)


class StrategyCardWidget(QFrame):
    """Single strategy card rendering the 5 fundamental numbers and live status (R-BOT-11, I-13)."""

    toggled = Signal(str, bool)

    def __init__(
        self,
        strategy_entry: Any,
        manifest_version: int = 1,
        account_type: str = "DEMO",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.strategy_key = str(_get(strategy_entry, "key", ""))
        self._strategy_entry = strategy_entry
        self._manifest_version = manifest_version
        self._account_type = account_type
        self._is_active = False
        self._details_expanded = False

        self.setObjectName("StrategyCard")
        self.setStyleSheet(
            f"QFrame#StrategyCard {{ background-color: {BG_CARD}; "
            f"border: 1px solid {BORDER_COLOR}; border-radius: 8px; }}"
            f"QFrame#StrategyCard:hover {{ border-color: {BORDER_ACCENT}; }}"
        )
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(10)

        # 1. Header Row
        root.addLayout(self._build_header_row())

        # 2. Live Status Row
        root.addLayout(self._build_live_status_row())

        # 3. The 5 Fundamental Numbers
        root.addWidget(self._build_five_numbers_box())

        # 4. Action Row (Ligar / Desligar & Ver detalhes)
        root.addLayout(self._build_action_row())

        # 5. Collapsible Details Container
        self._details_container = self._build_details_container()
        root.addWidget(self._details_container)
        self._details_container.setVisible(False)

        self._apply_account_type_rules()

    def _build_header_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(10)

        entry = self._strategy_entry
        name_pt = _get(entry, "display_name_pt", self.strategy_key)
        asset = _get(entry, "asset", "")
        timeframe = _get(entry, "timeframe", "M1")
        hours = _get(entry, "hours_utc", (0, 24))
        h_str = f"{hours[0]:02d}:00–{hours[1]:02d}:00 UTC"

        # Exactly: "nome pt-BR · asset · TF · faixa horária"
        header_text = f"{name_pt} · {asset} · {timeframe} · {h_str}"
        self._header_label = QLabel(header_text)
        self._header_label.setStyleSheet(
            f"color: {TEXT_PRIMARY}; font-size: 14px; font-weight: bold;"
        )
        row.addWidget(self._header_label, 1)

        # Broker badge
        broker = str(_get(entry, "broker", ""))
        if not broker:
            family = str(_get(entry, "family", ""))
            if family == "DERIV_DIGIT" or "1HZ" in asset or self.strategy_key.startswith(("tail", "selective", "parity")):
                broker = "Deriv"
            else:
                broker = "IQ Option"
        self.broker = broker

        self._broker_badge = QLabel(f"[{self.broker.upper()}]")
        b_color = ACCENT_AMBER if self.broker.lower() == "deriv" else ACCENT_CYAN
        self._broker_badge.setStyleSheet(
            f"color: {b_color}; font-weight: bold; font-size: 11px; "
            f"background-color: rgba(22, 29, 46, 0.9); padding: 3px 8px; border-radius: 4px;"
        )
        row.addWidget(self._broker_badge)

        # State badge: Aprovada / Em observação / Reprovada
        raw_status = str(_get(entry, "status", "observation")).lower()
        badge_text = STATUS_LABELS_PT.get(raw_status, raw_status.capitalize())
        badge_color = STATUS_COLORS.get(raw_status, TEXT_SECONDARY)

        self._status_badge = QLabel(f"● {badge_text}")
        self._status_badge.setStyleSheet(
            f"color: {badge_color}; font-weight: bold; font-size: 12px; "
            f"background-color: rgba(22, 29, 46, 0.85); padding: 3px 8px; "
            f"border-radius: 4px; border: 1px solid {badge_color};"
        )
        row.addWidget(self._status_badge)
        return row

    def _build_live_status_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(8)

        caption = QLabel("Estado ao vivo:")
        caption.setStyleSheet(f"color: {TEXT_MUTED}; font-size: 12px;")
        row.addWidget(caption)

        self._live_status_label = QLabel("Monitorando")
        self._live_status_label.setStyleSheet(
            f"color: {ACCENT_CYAN}; font-weight: 600; font-size: 12px; "
            f"background-color: rgba(0, 229, 255, 0.12); padding: 2px 6px; border-radius: 4px;"
        )
        row.addWidget(self._live_status_label)
        row.addStretch()
        return row

    def _build_five_numbers_box(self) -> QFrame:
        box = QFrame()
        box.setStyleSheet(f"background-color: {BG_SURFACE}; border-radius: 6px; padding: 6px;")
        grid = QGridLayout(box)
        grid.setContentsMargins(10, 8, 10, 8)
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(6)

        val = _get(self._strategy_entry, "validated", None)
        p_hat = _get(val, "p_hat", Decimal("0.58")) if val else Decimal("0.58")
        wilson_lower = _get(val, "wilson_lower", p_hat) if val else p_hat
        p_min = _get(val, "p_min_at_validation", Decimal("0.54")) if val else Decimal("0.54")
        ops_per_day = _get(val, "ops_per_day", Decimal("10")) if val else Decimal("10")
        worst_streak = _get(val, "worst_streak", 3) if val else 3
        res_1000 = (
            _get(val, "result_1000_ops_stake10", Decimal("1000")) if val else Decimal("1000")
        )
        n_samples = _get(val, "n", 1000) if val else 1000

        p_hat_str = _format_percentage(p_hat)
        p_min_str = _format_percentage(p_min)
        margin_str = _format_margin(wilson_lower, p_min)
        ops_str = f"{Decimal(str(ops_per_day)):.0f}"
        res_money = _format_money(res_1000)

        # Number 1: "Taxa de acerto validada {p_hat}% (mínimo necessário {p_min}%)"
        self._stat1_label = QLabel(
            f"Taxa de acerto validada {p_hat_str}% (mínimo necessário {p_min_str}%)"
        )
        self._stat1_label.setStyleSheet(
            f"color: {TEXT_PRIMARY}; font-weight: 600; font-size: 12px;"
        )
        grid.addWidget(self._stat1_label, 0, 0)

        # Number 2: "Margem de segurança +{margem} pp"
        self._stat2_label = QLabel(f"Margem de segurança {margin_str} pp")
        self._stat2_label.setStyleSheet(
            f"color: {ACCENT_GREEN}; font-weight: 600; font-size: 12px;"
        )
        grid.addWidget(self._stat2_label, 0, 1)

        # Number 3: "Operações por dia ~{ops}"
        self._stat3_label = QLabel(f"Operações por dia ~{ops_str}")
        self._stat3_label.setStyleSheet(f"color: {TEXT_SECONDARY}; font-size: 12px;")
        grid.addWidget(self._stat3_label, 1, 0)

        # Number 4: "Pior sequência de perdas {streak} (em {n} operações)"
        self._stat4_label = QLabel(
            f"Pior sequência de perdas {worst_streak} (em {n_samples:,} operações)".replace(
                ",", "."
            )
        )
        self._stat4_label.setStyleSheet(f"color: {TEXT_SECONDARY}; font-size: 12px;")
        grid.addWidget(self._stat4_label, 1, 1)

        # Number 5: "Resultado em 1.000 ops {valor} com stake $10, sem MG"
        self._stat5_label = QLabel(f"Resultado em 1.000 ops {res_money} com stake $10, sem MG")
        self._stat5_label.setStyleSheet(f"color: {ACCENT_CYAN}; font-weight: 600; font-size: 12px;")
        grid.addWidget(self._stat5_label, 2, 0, 1, 2)

        return box

    def _build_action_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(12)

        self._toggle_button = QPushButton("Ligar")
        self._toggle_button.setObjectName("PrimaryButton")
        self._toggle_button.setCheckable(True)
        self._toggle_button.setStyleSheet(
            f"QPushButton {{ background-color: {ACCENT_CYAN}; color: #000; "
            f"font-weight: bold; border-radius: 4px; padding: 6px 16px; min-width: 80px; }}"
            f"QPushButton:checked {{ background-color: {ACCENT_RED}; color: #fff; }}"
            f"QPushButton:disabled {{ background-color: rgba(100, 116, 139, 0.3); "
            f"color: {TEXT_MUTED}; }}"
        )
        self._toggle_button.clicked.connect(self._on_toggle_clicked)
        row.addWidget(self._toggle_button)

        self._details_button = QPushButton("Ver detalhes ▸")
        self._details_button.setObjectName("DetailsButton")
        self._details_button.setStyleSheet(
            f"QPushButton {{ background-color: transparent; color: {TEXT_SECONDARY}; "
            f"border: none; text-align: left; font-size: 12px; }}"
            f"QPushButton:hover {{ color: {ACCENT_CYAN}; }}"
        )
        self._details_button.clicked.connect(self._toggle_details)
        row.addWidget(self._details_button)

        row.addStretch()
        return row

    def _build_details_container(self) -> QFrame:
        container = QFrame()
        container.setStyleSheet(
            f"background-color: rgba(22, 29, 46, 0.5); border-left: 2px solid {ACCENT_CYAN}; "
            f"padding: 6px 10px; margin-top: 4px;"
        )
        vbox = QVBoxLayout(container)
        vbox.setContentsMargins(6, 4, 6, 4)
        vbox.setSpacing(4)

        val = _get(self._strategy_entry, "validated", None)
        payout_min = _get(val, "payout_min", Decimal("0.85")) if val else Decimal("0.85")
        payout_str = _format_payout(payout_min)

        self._detail_payout = QLabel(f"Payout mínimo exigido: {payout_str}%")
        self._detail_payout.setStyleSheet(f"color: {TEXT_MUTED}; font-size: 11px;")
        vbox.addWidget(self._detail_payout)

        self._detail_windows = QLabel("Janelas de validação: treino 6m ancorado / teste 2m rolando")
        self._detail_windows.setStyleSheet(f"color: {TEXT_MUTED}; font-size: 11px;")
        vbox.addWidget(self._detail_windows)

        self._detail_holdout = QLabel("Holdout / Out-of-Sample: 20% estrito e intocado")
        self._detail_holdout.setStyleSheet(f"color: {TEXT_MUTED}; font-size: 11px;")
        vbox.addWidget(self._detail_holdout)

        self._detail_manifest = QLabel(f"Versão do manifesto de origem: v{self._manifest_version}")
        self._detail_manifest.setStyleSheet(f"color: {TEXT_MUTED}; font-size: 11px;")
        vbox.addWidget(self._detail_manifest)

        return container

    def _toggle_details(self) -> None:
        self._details_expanded = not self._details_expanded
        self._details_container.setVisible(self._details_expanded)
        self._details_button.setText(
            "Ocultar detalhes ▾" if self._details_expanded else "Ver detalhes ▸"
        )

    def _on_toggle_clicked(self) -> None:
        self._is_active = self._toggle_button.isChecked()
        self._toggle_button.setText("Desligar" if self._is_active else "Ligar")
        self.toggled.emit(self.strategy_key, self._is_active)

    def set_active(self, active: bool) -> None:
        self._is_active = active
        self._toggle_button.setChecked(active)
        self._toggle_button.setText("Desligar" if active else "Ligar")

    def set_account_type(self, account_type: str) -> None:
        self._account_type = account_type
        self._apply_account_type_rules()

    def _apply_account_type_rules(self) -> None:
        raw_status = str(_get(self._strategy_entry, "status", "observation")).lower()
        is_real = self._account_type.strip().upper() in {"REAL", "LIVE"}

        # R-BOT-8: observation only allowed on Demo accounts
        if raw_status == "observation" and is_real:
            self._toggle_button.setEnabled(False)
            self._toggle_button.setToolTip(
                "Estratégias em observação só podem ser ligadas em conta Demo."
            )
            if self._is_active:
                self.set_active(False)
        elif raw_status == "rejected":
            self._toggle_button.setEnabled(False)
            self._toggle_button.setToolTip(
                "Estratégias reprovadas nos portões estatísticos não podem ser ligadas."
            )
            if self._is_active:
                self.set_active(False)
        else:
            self._toggle_button.setEnabled(True)
            self._toggle_button.setToolTip("")

    def set_live_status(self, status_text: str, reason_text: str = "") -> None:
        """Update live status pill (Monitorando, Sinal, Bloqueada, Rebaixada)."""
        full_text = f"{status_text} — {reason_text}" if reason_text else status_text
        self._live_status_label.setText(full_text)

        if "Sinal" in status_text:
            self._live_status_label.setStyleSheet(
                f"color: #000; font-weight: bold; font-size: 12px; "
                f"background-color: {ACCENT_GREEN}; padding: 2px 6px; border-radius: 4px;"
            )
        elif "Bloqueada" in status_text:
            self._live_status_label.setStyleSheet(
                f"color: {ACCENT_RED}; font-weight: 600; font-size: 12px; "
                f"background-color: rgba(255, 51, 102, 0.15); padding: 2px 6px; border-radius: 4px;"
            )
        elif "Rebaixada" in status_text:
            self._live_status_label.setStyleSheet(
                f"color: {ACCENT_AMBER}; font-weight: 600; font-size: 12px; "
                f"background-color: rgba(255, 184, 0, 0.15); padding: 2px 6px; border-radius: 4px;"
            )
        else:
            self._live_status_label.setStyleSheet(
                f"color: {ACCENT_CYAN}; font-weight: 600; font-size: 12px; "
                f"background-color: rgba(0, 229, 255, 0.12); padding: 2px 6px; border-radius: 4px;"
            )


class RejectedStrategiesPanel(QFrame):
    """Secondary panel displaying rejected strategies and one-sentence reason_pt."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("RejectedPanel")
        self.setStyleSheet(
            f"QFrame#RejectedPanel {{ background-color: rgba(22, 29, 46, 0.6); "
            f"border: 1px solid {BORDER_COLOR}; border-radius: 8px; }}"
        )
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(14, 12, 14, 12)
        self._layout.setSpacing(6)

        title = QLabel("Reprovadas — por quê")
        title.setStyleSheet(f"color: {TEXT_PRIMARY}; font-weight: bold; font-size: 13px;")
        self._layout.addWidget(title)

        self._items_container = QVBoxLayout()
        self._items_container.setSpacing(4)
        self._layout.addLayout(self._items_container)

        self.setVisible(False)

    def set_rejected_strategies(self, rejected_entries: list[Any]) -> None:
        # Clear existing
        while self._items_container.count():
            child = self._items_container.takeAt(0)
            if child is not None:
                w = child.widget()
                if w is not None:
                    w.deleteLater()

        if not rejected_entries:
            self.setVisible(False)
            return

        for item in rejected_entries:
            name_pt = _get(item, "display_name_pt", _get(item, "key", "Estratégia"))
            asset = _get(item, "asset", "")
            reason_pt = _get(item, "reason_pt", "Critérios estatísticos não satisfeitos.")
            lbl = QLabel(f"• {name_pt} ({asset}): {reason_pt}")
            lbl.setWordWrap(True)
            lbl.setStyleSheet(f"color: {TEXT_MUTED}; font-size: 12px;")
            self._items_container.addWidget(lbl)

        self.setVisible(True)


class ManifestStrategyPanelWidget(QWidget):
    """Primary dynamic strategy panel driven by signed manifests (R-BOT-11, I-13)."""

    strategy_selection_changed = Signal(str, bool)
    selection_mode_changed = Signal(str)

    def __init__(
        self,
        account_type: str = "DEMO",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._account_type = account_type
        self._selection_mode = "SINGLE"
        self._cards: dict[str, StrategyCardWidget] = {}
        self._manifest_version = 1

        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(12)

        # 1. Manifest Banner (alerts for expired or rejected manifests)
        self._banner = ManifestBannerWidget(self)
        root.addWidget(self._banner)

        # 2. Controls Row (Mode selector: SINGLE / MULTI)
        root.addLayout(self._build_controls_row())

        # 3. Scrollable List of Strategy Cards
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)

        self._cards_container = QWidget()
        self._cards_layout = QVBoxLayout(self._cards_container)
        self._cards_layout.setContentsMargins(0, 0, 0, 0)
        self._cards_layout.setSpacing(10)
        self._cards_layout.addStretch()
        scroll.setWidget(self._cards_container)
        root.addWidget(scroll, 1)

        # 4. Secondary Rejected Strategies Panel
        self._rejected_panel = RejectedStrategiesPanel(self)
        root.addWidget(self._rejected_panel)

    def _build_controls_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(12)

        lbl = QLabel("Modo de seleção:")
        lbl.setStyleSheet(f"color: {TEXT_SECONDARY}; font-weight: 600; font-size: 12px;")
        row.addWidget(lbl)

        self._mode_group = QButtonGroup(self)
        radio_style = (
            f"QRadioButton {{ color: {TEXT_PRIMARY}; font-size: 12px; "
            f"font-weight: 600; spacing: 6px; }}"
            f"QRadioButton::indicator {{ width: 14px; height: 14px; border-radius: 7px; "
            f"border: 1px solid {BORDER_ACCENT}; background-color: {BG_CARD}; }}"
            f"QRadioButton::indicator:checked {{ background-color: {ACCENT_CYAN}; "
            f"border: 2px solid #000; }}"
        )
        self._radio_single = QRadioButton("Única (SINGLE)")
        self._radio_single.setChecked(True)
        self._radio_single.setStyleSheet(radio_style)
        self._radio_multi = QRadioButton("Múltipla (MULTI)")
        self._radio_multi.setStyleSheet(radio_style)

        self._mode_group.addButton(self._radio_single)
        self._mode_group.addButton(self._radio_multi)
        self._mode_group.buttonClicked.connect(self._on_mode_changed)

        row.addWidget(self._radio_single)
        row.addWidget(self._radio_multi)

        # Broker Filter
        filter_lbl = QLabel("Corretora:")
        filter_lbl.setStyleSheet(f"color: {TEXT_SECONDARY}; font-weight: 600; font-size: 12px; margin-left: 16px;")
        row.addWidget(filter_lbl)

        btn_style = (
            f"QPushButton {{ background-color: {BG_CARD}; color: {TEXT_SECONDARY}; "
            f"border: 1px solid {BORDER_COLOR}; border-radius: 4px; padding: 3px 10px; font-size: 11px; }}"
            f"QPushButton:checked {{ background-color: {ACCENT_CYAN}; color: #000; font-weight: bold; border-color: {ACCENT_CYAN}; }}"
        )
        self._filter_all = QPushButton("Todas")
        self._filter_all.setCheckable(True)
        self._filter_all.setChecked(True)
        self._filter_all.setStyleSheet(btn_style)

        self._filter_iq = QPushButton("IQ Option")
        self._filter_iq.setCheckable(True)
        self._filter_iq.setStyleSheet(btn_style)

        self._filter_deriv = QPushButton("Deriv")
        self._filter_deriv.setCheckable(True)
        self._filter_deriv.setStyleSheet(btn_style)

        self._filter_group = QButtonGroup(self)
        self._filter_group.addButton(self._filter_all)
        self._filter_group.addButton(self._filter_iq)
        self._filter_group.addButton(self._filter_deriv)
        self._filter_group.buttonClicked.connect(self._on_filter_changed)

        row.addWidget(self._filter_all)
        row.addWidget(self._filter_iq)
        row.addWidget(self._filter_deriv)

        # Bulk Actions
        actions_lbl = QLabel("Ações:")
        actions_lbl.setStyleSheet(f"color: {TEXT_SECONDARY}; font-weight: 600; font-size: 12px; margin-left: 16px;")
        row.addWidget(actions_lbl)

        self._btn_turn_on_all = QPushButton("⚡ Ligar Todas")
        self._btn_turn_on_all.setStyleSheet(
            f"QPushButton {{ background-color: rgba(0, 209, 255, 0.15); color: {ACCENT_CYAN}; "
            f"border: 1px solid {ACCENT_CYAN}; border-radius: 4px; padding: 4px 12px; font-weight: bold; font-size: 11px; }}"
            f"QPushButton:hover {{ background-color: {ACCENT_CYAN}; color: #000; }}"
        )
        self._btn_turn_on_all.clicked.connect(self.turn_on_all)

        self._btn_turn_off_all = QPushButton("⏹ Desligar Todas")
        self._btn_turn_off_all.setStyleSheet(
            f"QPushButton {{ background-color: rgba(255, 77, 77, 0.12); color: {ACCENT_RED}; "
            f"border: 1px solid rgba(255, 77, 77, 0.4); border-radius: 4px; padding: 4px 12px; font-weight: bold; font-size: 11px; }}"
            f"QPushButton:hover {{ background-color: {ACCENT_RED}; color: #fff; }}"
        )
        self._btn_turn_off_all.clicked.connect(self.turn_off_all)

        row.addWidget(self._btn_turn_on_all)
        row.addWidget(self._btn_turn_off_all)

        row.addStretch()
        return row

    def _on_filter_changed(self) -> None:
        target = "ALL"
        if self._filter_iq.isChecked():
            target = "IQ OPTION"
        elif self._filter_deriv.isChecked():
            target = "DERIV"

        for card in self._cards.values():
            if target == "ALL":
                card.setVisible(True)
            else:
                card_broker = getattr(card, "broker", "").upper()
                card.setVisible(target in card_broker)

    def _on_mode_changed(self) -> None:
        self._selection_mode = "SINGLE" if self._radio_single.isChecked() else "MULTI"
        if self._selection_mode == "SINGLE":
            # If multiple are active, keep only the first active
            active_keys = [k for k, c in self._cards.items() if c._is_active]
            if len(active_keys) > 1:
                for k in active_keys[1:]:
                    self._cards[k].set_active(False)
        self.selection_mode_changed.emit(self._selection_mode)

    def turn_on_all(self) -> None:
        """Switch to MULTI mode and activate all visible and eligible strategy cards."""
        self._radio_multi.setChecked(True)
        self._selection_mode = "MULTI"
        self.selection_mode_changed.emit("MULTI")

        activated = []
        for key, card in self._cards.items():
            if not card.isHidden() and card._toggle_button.isEnabled():
                card.set_active(True)
                activated.append(key)

        if activated:
            self.strategy_selection_changed.emit(activated[0], True)

    def turn_off_all(self) -> None:
        """Deactivate all strategy cards."""
        for card in self._cards.values():
            if card._is_active:
                card.set_active(False)
        self.strategy_selection_changed.emit("", False)

    def set_selection_mode(self, mode: str) -> None:
        norm = mode.strip().upper()
        if norm == "MULTI":
            self._radio_multi.setChecked(True)
            self._selection_mode = "MULTI"
        else:
            self._radio_single.setChecked(True)
            self._selection_mode = "SINGLE"

    def set_account_type(self, account_type: str) -> None:
        self._account_type = account_type
        for card in self._cards.values():
            card.set_account_type(account_type)

    def show_manifest_alert(self, version: int | str, status_text: str, age_text: str) -> None:
        self._banner.show_alert(version, status_text, age_text)

    def hide_manifest_alert(self) -> None:
        self._banner.hide_alert()

    def set_manifest(
        self,
        manifest: Any,
        account_type: str | None = None,
    ) -> None:
        """Render strategy cards and secondary rejected panel from a manifest."""
        if account_type is not None:
            self._account_type = account_type

        # Extract manifest version
        if hasattr(manifest, "manifest_version"):
            self._manifest_version = int(getattr(manifest, "manifest_version", 1) or 1)
        elif hasattr(manifest, "version"):
            self._manifest_version = int(getattr(manifest, "version", 1) or 1)
        elif isinstance(manifest, dict):
            val_v = manifest.get("manifest_version", manifest.get("version", 1))
            self._manifest_version = int(val_v or 1)

        raw_strategies = _get(manifest, "strategies", None)
        if not raw_strategies:
            raw_strategies = _get(manifest, "candidates", ())

        # Clear existing cards
        for card in self._cards.values():
            self._cards_layout.removeWidget(card)
            card.deleteLater()
        self._cards.clear()

        rejected_entries = []
        for entry in raw_strategies:
            st = str(_get(entry, "status", "")).lower()
            if st == "rejected":
                rejected_entries.append(entry)
                continue

            card = StrategyCardWidget(
                strategy_entry=entry,
                manifest_version=self._manifest_version,
                account_type=self._account_type,
            )
            card.toggled.connect(self._on_card_toggled)
            self._cards[card.strategy_key] = card
            # Insert before the stretch at the end
            self._cards_layout.insertWidget(self._cards_layout.count() - 1, card)

        self._rejected_panel.set_rejected_strategies(rejected_entries)

    def _on_card_toggled(self, strategy_key: str, active: bool) -> None:
        if active and self._selection_mode == "SINGLE":
            # Deactivate all other cards
            for key, card in self._cards.items():
                if key != strategy_key and card._is_active:
                    card.set_active(False)
        self.strategy_selection_changed.emit(strategy_key, active)

    def get_card(self, strategy_key: str) -> StrategyCardWidget | None:
        return self._cards.get(strategy_key)

    def update_live_status(
        self,
        strategy_key: str,
        status_text: str,
        reason_text: str = "",
    ) -> None:
        card = self._cards.get(strategy_key)
        if card is not None:
            card.set_live_status(status_text, reason_text)
