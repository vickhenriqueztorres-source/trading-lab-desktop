"""Interactive Multi-Asset RSI Radar for IQ Option."""

from __future__ import annotations

from collections.abc import Sequence

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from apps.ui.i18n import t
from apps.ui.theme import ACCENT_AMBER, ACCENT_CYAN, ACCENT_GREEN, ACCENT_RED, TEXT_MUTED
from packages.protocol import UiIqOptionAssetRank


class IqOptionAssetRadarWidget(QWidget):
    """Real-time Multi-Asset Scanner & Radar for IQ Option RSI strategy."""

    @staticmethod
    def _update_cell(
        item: QTableWidgetItem | None,
        text: str,
        *,
        color: str | None = None,
        align: Qt.AlignmentFlag | None = None,
        tooltip: str | None = None,
    ) -> QTableWidgetItem:
        if item is None:
            item = QTableWidgetItem(text)
            if color:
                item.setForeground(QColor(color))
            if align is not None:
                item.setTextAlignment(align)
            if tooltip:
                item.setToolTip(tooltip)
            return item
        if item.text() != text:
            item.setText(text)
        if color:
            qcolor = QColor(color)
            if item.foreground().color() != qcolor:
                item.setForeground(qcolor)
        if align is not None and item.textAlignment() != align:
            item.setTextAlignment(align)
        if tooltip is not None and item.toolTip() != tooltip:
            item.setToolTip(tooltip)
        return item

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._ranking: tuple[UiIqOptionAssetRank, ...] = ()
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(8)

        header = QFrame()
        header.setObjectName("RiskSummary")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(14, 10, 14, 10)

        titles = QVBoxLayout()
        self._title = QLabel(t("radar.title"))
        self._title.setObjectName("Title")
        titles.addWidget(self._title)

        self._subtitle = QLabel(t("radar.subtitle"))
        self._subtitle.setObjectName("Subtitle")
        self._subtitle.setWordWrap(True)
        titles.addWidget(self._subtitle)
        header_layout.addLayout(titles, 1)

        self._state = QLabel(t("radar.monitoring"))
        self._state.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._state.setObjectName("StatusPillOnline")
        header_layout.addWidget(self._state)
        root.addWidget(header)

        self._table = QTableWidget(0, 5)
        self._table.setObjectName("AssetRadarTable")
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        self._table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._table.verticalHeader().setVisible(False)
        self._table.verticalHeader().setDefaultSectionSize(28)
        self._table.verticalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
        self._table.setAlternatingRowColors(True)
        self._table.setMinimumHeight(180)
        self._table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        self._table.setHorizontalHeaderLabels(
            [
                t("radar.col_asset"),
                t("radar.col_rsi"),
                t("radar.col_signal"),
                t("radar.col_condition"),
                t("radar.col_status"),
            ]
        )

        table_header = self._table.horizontalHeader()
        table_header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        table_header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        table_header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        table_header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        table_header.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        root.addWidget(self._table)

        self._notice = QLabel(t("radar.rsi_tip"))
        self._notice.setWordWrap(True)
        self._notice.setStyleSheet(f"color: {TEXT_MUTED}; font-size: 11px;")
        root.addWidget(self._notice)
        # Do not invent market values before the Core publishes an authoritative
        # snapshot. An empty table is materially different from neutral data.
        self.update_ranking(())

    def update_ranking(self, ranking: Sequence[UiIqOptionAssetRank]) -> None:
        if ranking and tuple(ranking) == self._ranking:
            return
        if not ranking:
            self._ranking = ()
            self._table.clearSpans()
            self._table.setRowCount(1)
            self._table.setSpan(0, 0, 1, 5)
            empty_item = QTableWidgetItem(t("radar.empty"))
            empty_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            empty_item.setForeground(QColor(TEXT_MUTED))
            self._table.setItem(0, 0, empty_item)
            self._state.setText("STANDBY")
            self._table.setFixedHeight(80)
            return
        self._table.clearSpans()
        self._ranking = tuple(ranking)
        if self._table.rowCount() != len(self._ranking):
            self._table.setRowCount(len(self._ranking))

        triggered = next((item for item in self._ranking if item.status == "TRIGGERED"), None)
        selected = next((item for item in self._ranking if item.selected), None)
        no_evidence = all(
            item.rsi == "--" and item.status in {"WAITING_DATA", "NO_EVIDENCE", "MONITORING"}
            for item in self._ranking
        )
        catalog_evidence = all(item.source == "IQOPTION_SESSION_CATALOG" for item in self._ranking)

        target_obj = "StatusPillOnline"
        if catalog_evidence:
            self._state.setText(f"{t('radar.col_status')}: {len(self._ranking)}")
        elif no_evidence:
            self._state.setText(t("radar.monitoring"))
        elif triggered is not None:
            self._state.setText(f"{t('radar.col_signal')} · {triggered.display_name}")
        elif selected is not None and selected.symbol != "AUTO":
            self._state.setText(f"{t('radar.col_asset')}: {selected.display_name}")
        else:
            self._state.setText(t("radar.monitoring"))

        if self._state.objectName() != target_obj:
            self._state.setObjectName(target_obj)
            self._state.style().unpolish(self._state)
            self._state.style().polish(self._state)

        self._table.setUpdatesEnabled(False)
        try:
            for row, item in enumerate(self._ranking):
                # Column 0: Symbol display name
                sym_item = self._update_cell(
                    self._table.item(row, 0),
                    item.display_name,
                    align=Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                    tooltip=item.candidate_details,
                )
                if self._table.item(row, 0) is not sym_item:
                    self._table.setItem(row, 0, sym_item)

            # Column 1: RSI Value
            rsi_val = float(item.rsi) if item.rsi.replace(".", "", 1).isdigit() else 50.0
            if rsi_val <= 30.0:
                rsi_color = ACCENT_GREEN
            elif rsi_val >= 70.0:
                rsi_color = ACCENT_RED
            else:
                rsi_color = ACCENT_CYAN
            rsi_item = self._update_cell(
                self._table.item(row, 1),
                f"{item.rsi}",
                color=rsi_color,
                align=Qt.AlignmentFlag.AlignCenter,
            )
            if self._table.item(row, 1) is not rsi_item:
                self._table.setItem(row, 1, rsi_item)

            # Column 2: Direction / Signal
            if item.status == "WARMING_UP":
                sig_text = "CALENTANDO"
                sig_color = ACCENT_AMBER
            elif item.status == "DISCOVERY_ONLY":
                sig_text = "DETECTADO"
                sig_color = TEXT_MUTED
            elif item.status == "TICK_VOLUME_UNAVAILABLE":
                sig_text = "SIN VOLUMEN"
                sig_color = ACCENT_AMBER
            elif item.direction == "CALL":
                if item.status == "TRIGGERED":
                    sig_text = "▲ CALL"
                    sig_color = ACCENT_GREEN
                else:
                    sig_text = "▲ CALL"
                    sig_color = ACCENT_CYAN
            elif item.direction == "PUT":
                if item.status == "TRIGGERED":
                    sig_text = "▼ PUT"
                    sig_color = ACCENT_RED
                else:
                    sig_text = "▼ PUT"
                    sig_color = ACCENT_AMBER
            else:
                sig_text = "—"
                sig_color = TEXT_MUTED

            sig_item = self._update_cell(
                self._table.item(row, 2),
                sig_text,
                color=sig_color,
                align=Qt.AlignmentFlag.AlignCenter,
            )
            if self._table.item(row, 2) is not sig_item:
                self._table.setItem(row, 2, sig_item)

            # Column 3: Condition / Zone
            if item.status in {
                "READ_ONLY_PROBE",
                "CORRECT_CONFIGURATION",
                "MANUAL_REVIEW",
            } or item.condition in {
                "NO_CANDIDATE",
                "ASSET_MISMATCH",
                "OUTSIDE_HOURS",
                "DEMO_ONLY",
                "STATUS_NOT_ELIGIBLE",
            }:
                cond_text = item.candidate_details
                cond_color = ACCENT_AMBER
            elif item.condition == "OUTSIDE_HOURS":
                cond_text = "Esperando horario UTC de la estrategia"
                cond_color = ACCENT_AMBER
            elif item.condition == "OVERSOLD":
                cond_text = "SOBREVENTA (< 30)"
                cond_color = ACCENT_GREEN
            elif item.condition == "OVERBOUGHT":
                cond_text = "SOBRECOMPRA (> 70)"
                cond_color = ACCENT_RED
            elif item.condition.startswith("AQUECENDO "):
                cond_text = item.condition.replace("AQUECENDO", "Calentando", 1)
                cond_color = ACCENT_AMBER
            elif item.condition in {"IQOPTION_ACTIVE_SUSPENDED", "IQOPTION_ACTIVE_UNAVAILABLE"}:
                cond_text = (
                    "Suspendido por el broker · opciones turbo"
                    if item.condition == "IQOPTION_ACTIVE_SUSPENDED"
                    else "Ausente o desactivado en catálogo turbo"
                )
                cond_color = ACCENT_AMBER
            elif item.condition == "VOLUME_INDISPONIVEL":
                cond_text = "Volumen de ticks no disponible"
                cond_color = ACCENT_AMBER
            elif item.condition == "OPEN_READ_ONLY":
                cond_text = "Abierto · producto sin ejecución habilitada"
                cond_color = ACCENT_AMBER
            elif item.condition == "MARKET_CLOSED":
                cond_text = "Cerrado en esta sesión del broker"
                cond_color = TEXT_MUTED
            elif item.condition in {"SEM_RESPOSTA", "TIMEOUT", "DATA_UNAVAILABLE"}:
                cond_text = "Sem resposta da corretora"
                cond_color = ACCENT_AMBER
            elif item.condition in {
                "REGIME",
                "TRIGGER",
                "CONFIRM",
                "DISAGREE",
                "SIGNAL_CONFLICT",
                "NO_SIGNAL",
                "OUTSIDE_HOURS",
            }:
                cond_text = f"SIN SEÑAL · {item.condition}"
                cond_color = TEXT_MUTED
            else:
                cond_text = "ZONA NEUTRA (30 — 70)"
                cond_color = TEXT_MUTED

            cond_item = self._update_cell(
                self._table.item(row, 3),
                cond_text,
                color=cond_color,
                align=Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                tooltip=item.candidate_details,
            )
            if self._table.item(row, 3) is not cond_item:
                self._table.setItem(row, 3, cond_item)

            # Column 4: Status
            if item.status in {"READ_ONLY_PROBE", "CORRECT_CONFIGURATION", "MANUAL_REVIEW"}:
                status_text = {
                    "READ_ONLY_PROBE": t("radar.status_verifying"),
                    "CORRECT_CONFIGURATION": t("radar.status_fix_params"),
                    "MANUAL_REVIEW": t("radar.status_manual_review"),
                }[item.status]
                status_color = ACCENT_AMBER
            elif item.status in {
                "NO_CANDIDATE",
                "ASSET_MISMATCH",
                "OUTSIDE_HOURS",
                "DEMO_ONLY",
                "STATUS_NOT_ELIGIBLE",
            }:
                status_text = item.status
                status_color = ACCENT_AMBER
            elif item.status in {"TIMEOUT", "DATA_UNAVAILABLE", "SKIPPED"}:
                status_text = "TIMEOUT"
                status_color = ACCENT_AMBER
            elif item.status == "MARKET_UNAVAILABLE":
                status_text = t("radar.status_market_unavailable")
                status_color = ACCENT_AMBER
            elif item.status == "DISCOVERY_ONLY":
                status_text = t("radar.status_discovery")
                status_color = TEXT_MUTED
            elif item.status == "WARMING_UP":
                status_text = t("radar.status_warming_up")
                status_color = ACCENT_AMBER
            elif item.status == "TICK_VOLUME_UNAVAILABLE":
                status_text = t("radar.status_awaiting_volume")
                status_color = ACCENT_AMBER
            elif item.status == "TRIGGERED":
                status_text = t("radar.status_triggered_unsent")
                status_color = ACCENT_AMBER
            elif item.selected:
                status_text = t("radar.status_focus")
                status_color = ACCENT_CYAN
            else:
                status_text = t("radar.monitoring")
                status_color = TEXT_MUTED

            status_item = self._update_cell(
                self._table.item(row, 4),
                status_text,
                color=status_color,
                align=Qt.AlignmentFlag.AlignCenter,
                tooltip=item.candidate_details,
            )
            if self._table.item(row, 4) is not status_item:
                self._table.setItem(row, 4, status_item)
        finally:
            self._table.setUpdatesEnabled(True)

        header_h = self._table.horizontalHeader().height() or 28
        total_h = header_h + len(self._ranking) * 28 + 6
        self._table.setFixedHeight(min(max(total_h, 180), 420))

    def retranslate(self) -> None:
        self._title.setText(t("radar.title"))
        self._subtitle.setText(t("radar.subtitle"))
        self._notice.setText(t("radar.rsi_tip"))
        self._table.setHorizontalHeaderLabels(
            [
                t("radar.col_asset"),
                t("radar.col_rsi"),
                t("radar.col_signal"),
                t("radar.col_condition"),
                t("radar.col_status"),
            ]
        )
        if self._ranking:
            self.update_ranking(self._ranking)


__all__ = ["IqOptionAssetRadarWidget"]
