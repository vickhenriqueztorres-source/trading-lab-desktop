"""Manual review panel for operator inspection and audited resolution of stuck orders."""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from apps.ui.formatting import format_minor_units
from apps.ui.i18n import t
from apps.ui.theme import ACCENT_AMBER, ACCENT_GREEN, ACCENT_RED, BG_ELEVATED, TEXT_MUTED
from packages.protocol.ui_messages import OrderSummary

if TYPE_CHECKING:
    from apps.ui.controller import UiController


class SettlementResolutionDialog(QDialog):
    """Dialog to gather settlement evidence from operator."""

    def __init__(
        self,
        order: OrderSummary,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(t("orders.manual_review.settle_title", "Confirmar Execução da Ordem"))
        self.setMinimumWidth(440)

        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        info_label = QLabel(
            f"<b>Ordem:</b> {order.order_id}<br>"
            f"<b>Broker:</b> {order.broker} | <b>Ativo:</b> {order.symbol}<br>"
            f"<b>Direção:</b> {order.direction} | <b>Valor:</b> "
            f"{format_minor_units(order.amount_minor_units, order.currency)}"
        )
        info_label.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(info_label)

        # Broker Contract / Order ID
        layout.addWidget(
            QLabel(
                t("orders.manual_review.broker_order_id", "ID do Contrato / Ordem na Corretora:")
            )
        )
        self.txt_broker_id = QLineEdit()
        self.txt_broker_id.setPlaceholderText("ex: 1489201948")
        if order.broker_order_id:
            self.txt_broker_id.setText(order.broker_order_id)
        layout.addWidget(self.txt_broker_id)

        # Realized PnL (Lucro / Prejuízo em unidades monetárias)
        layout.addWidget(
            QLabel(
                t("orders.manual_review.realized_pnl", "Resultado Financeiro Líquido (USD/BRL):")
            )
        )
        self.spin_pnl = QDoubleSpinBox()
        self.spin_pnl.setRange(-100_000.0, 100_000.0)
        self.spin_pnl.setDecimals(2)
        self.spin_pnl.setSingleStep(0.5)
        self.spin_pnl.setValue(0.0)
        layout.addWidget(self.spin_pnl)

        # Operator Notes
        layout.addWidget(
            QLabel(
                t("orders.manual_review.operator_notes", "Justificativa / Evidência do Operador:")
            )
        )
        self.txt_notes = QLineEdit()
        self.txt_notes.setPlaceholderText(
            "ex: Verificado no extrato da plataforma web; ordem foi WIN"
        )
        self.txt_notes.setText("Confirmado no extrato do broker")
        layout.addWidget(self.txt_notes)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    @property
    def broker_order_id(self) -> str:
        return self.txt_broker_id.text().strip() or "MANUAL_CONFIRMED"

    @property
    def realized_pnl_minor_units(self) -> int:
        return int(round(self.spin_pnl.value() * 100))

    @property
    def reason(self) -> str:
        return self.txt_notes.text().strip() or "MANUAL_SETTLEMENT_CONFIRMED"


class RejectionResolutionDialog(QDialog):
    """Dialog to confirm that an order never executed on broker."""

    def __init__(
        self,
        order: OrderSummary,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(t("orders.manual_review.reject_title", "Confirmar Ordem Não Executada"))
        self.setMinimumWidth(440)

        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        stake_str = format_minor_units(order.amount_minor_units, order.currency)
        info_label = QLabel(
            "<b>Atenção:</b> Esta ação confirmará que a ordem não existe na corretora "
            "e liberará a reserva financeira de risco.<br><br>"
            f"<b>Ordem:</b> {order.order_id}<br>"
            f"<b>Broker:</b> {order.broker} | <b>Ativo:</b> {order.symbol}<br>"
            f"<b>Stake:</b> {stake_str}"
        )
        info_label.setTextFormat(Qt.TextFormat.RichText)
        info_label.setWordWrap(True)
        layout.addWidget(info_label)

        # Operator Notes
        layout.addWidget(
            QLabel(t("orders.manual_review.operator_notes", "Justificativa do Operador:"))
        )
        self.txt_notes = QLineEdit()
        self.txt_notes.setText("Confirmado no extrato: ordem inexistente na corretora")
        layout.addWidget(self.txt_notes)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    @property
    def reason(self) -> str:
        return self.txt_notes.text().strip() or "CONFIRMED_NOT_EXECUTED_ON_BROKER"


class ManualReviewPanel(QFrame):
    """Warning panel displaying orders requiring manual operator review."""

    def __init__(
        self,
        controller: UiController | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._controller = controller
        self._orders: tuple[OrderSummary, ...] = ()
        self.setObjectName("ManualReviewPanel")
        self.setStyleSheet(
            f"QFrame#ManualReviewPanel {{"
            f"  background: {BG_ELEVATED};"
            f"  border: 2px solid {ACCENT_AMBER};"
            f"  border-radius: 8px;"
            f"}}"
        )

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(10)

        # Header with warning badge
        header = QHBoxLayout()
        header.setSpacing(8)

        self._icon_label = QLabel("⚠")
        self._icon_label.setStyleSheet(
            f"color: {ACCENT_AMBER}; font-size: 18px; font-weight: bold;"
        )
        header.addWidget(self._icon_label)

        self._title = QLabel(t("orders.manual_review.header", "Ordens em Revisão Manual Requerida"))
        self._title.setStyleSheet(f"color: {ACCENT_AMBER}; font-size: 14px; font-weight: 700;")
        header.addWidget(self._title)

        header.addStretch()
        root.addLayout(header)

        self._desc = QLabel(
            t(
                "orders.manual_review.description",
                "A reconciliação automática encontrou divergências para as ordens abaixo. "
                "Para proteger sua conta, novas entradas estão bloqueadas até a resolução deste "
                "estado financeiro. Consulte o histórico da corretora e confirme a liquidação "
                "ou a rejeição.",
            )
        )
        self._desc.setWordWrap(True)
        self._desc.setStyleSheet(f"color: {TEXT_MUTED}; font-size: 11px;")
        root.addWidget(self._desc)

        # Table
        self._table = QTableWidget()
        self._table.setColumnCount(7)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.verticalHeader().setVisible(False)
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self._table.setHorizontalHeaderLabels(
            [
                t("orders.col.id", "ID"),
                t("orders.col.broker", "Broker"),
                t("orders.col.symbol", "Ativo"),
                t("orders.col.direction", "Direção"),
                t("orders.col.amount", "Stake"),
                t("orders.col.time", "Horário"),
                t("orders.manual_review.actions", "Ações de Recuperação"),
            ]
        )
        root.addWidget(self._table)

        # Start hidden until orders in MANUAL_REVIEW arrive
        self.setVisible(False)

    def set_controller(self, controller: UiController) -> None:
        self._controller = controller

    def update_orders(self, orders: Sequence[OrderSummary]) -> None:
        review_orders = tuple(
            o for o in orders if o.state == "MANUAL_REVIEW" or o.reconciliation_review_required
        )
        if review_orders == self._orders:
            return
        self._orders = review_orders

        if not review_orders:
            self._table.setRowCount(0)
            self.setVisible(False)
            return

        self.setVisible(True)
        self._table.setRowCount(len(review_orders))

        for row, ord_item in enumerate(review_orders):
            # ID
            short_id = (
                ord_item.order_id[:16] + "..." if len(ord_item.order_id) > 16 else ord_item.order_id
            )
            id_item = QTableWidgetItem(short_id)
            id_item.setToolTip(ord_item.order_id)
            self._table.setItem(row, 0, id_item)

            # Broker
            self._table.setItem(row, 1, QTableWidgetItem(ord_item.broker))

            # Symbol
            self._table.setItem(row, 2, QTableWidgetItem(ord_item.symbol))

            # Direction
            dir_item = QTableWidgetItem(ord_item.direction)
            dir_item.setForeground(
                QColor(ACCENT_GREEN if ord_item.direction.upper() == "CALL" else ACCENT_RED)
            )
            self._table.setItem(row, 3, dir_item)

            # Amount
            amt_str = format_minor_units(ord_item.amount_minor_units, ord_item.currency)
            self._table.setItem(row, 4, QTableWidgetItem(amt_str))

            # Time
            self._table.setItem(
                row, 5, QTableWidgetItem(ord_item.created_at_utc.strftime("%H:%M:%S"))
            )

            # Actions widget
            action_widget = QWidget()
            action_box = QHBoxLayout(action_widget)
            action_box.setContentsMargins(4, 2, 4, 2)
            action_box.setSpacing(6)

            btn_recover = QPushButton(
                t("orders.manual_review.btn_recover", "⟳ Consultar e Recuperar")
            )
            btn_recover.setStyleSheet(
                f"background: rgba(230, 162, 60, 0.2); color: {ACCENT_AMBER}; "
                f"border: 1px solid {ACCENT_AMBER}; border-radius: 4px; "
                "padding: 2px 8px; font-weight: bold;"
            )
            btn_recover.clicked.connect(
                lambda checked=False, o=ord_item: self._on_recover_clicked(o)
            )
            action_box.addWidget(btn_recover)

            btn_settle = QPushButton(t("orders.manual_review.btn_settle", "✓ Liquidar"))
            btn_settle.setStyleSheet(
                f"background: rgba(31, 181, 122, 0.2); color: {ACCENT_GREEN}; "
                f"border: 1px solid {ACCENT_GREEN}; border-radius: 4px; "
                "padding: 2px 8px; font-weight: bold;"
            )
            btn_settle.clicked.connect(lambda checked=False, o=ord_item: self._on_settle_clicked(o))
            action_box.addWidget(btn_settle)

            btn_reject = QPushButton(t("orders.manual_review.btn_reject", "✗ Não Executou"))
            btn_reject.setStyleSheet(
                f"background: rgba(229, 72, 77, 0.2); color: {ACCENT_RED}; "
                f"border: 1px solid {ACCENT_RED}; border-radius: 4px; "
                "padding: 2px 8px; font-weight: bold;"
            )
            btn_reject.clicked.connect(lambda checked=False, o=ord_item: self._on_reject_clicked(o))
            action_box.addWidget(btn_reject)

            self._table.setCellWidget(row, 6, action_widget)

    def _on_recover_clicked(self, order: OrderSummary) -> None:
        if self._controller is None:
            QMessageBox.warning(self, "Aviso", "Controlador UI não disponível.")
            return

        try:
            ack = self._controller.resolve_order(
                order.order_id,
                "QUERY_AND_RECOVER",
                broker_order_id=order.broker_order_id,
                reason="OPERATOR_QUERY_AND_RECOVER_REQUESTED",
                operator="UI_OPERATOR",
            )
            if ack.accepted:
                QMessageBox.information(
                    self,
                    "Reconciliação Concluída",
                    f"Ordem {order.order_id} consultada. Novo estado: {ack.new_state}.",
                )
            else:
                QMessageBox.warning(
                    self,
                    "Resultado da Reconciliação",
                    f"Não foi possível recuperar a ordem automaticamente: {ack.reason_code}. "
                    "Use a liquidação ou confirmação manual.",
                )
        except Exception as exc:
            QMessageBox.critical(self, "Erro", f"Erro ao acionar recuperação: {exc}")

    def _on_settle_clicked(self, order: OrderSummary) -> None:
        if self._controller is None:
            QMessageBox.warning(self, "Aviso", "Controlador UI não disponível.")
            return

        dialog = SettlementResolutionDialog(order, parent=self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            try:
                ack = self._controller.resolve_order(
                    order.order_id,
                    "SETTLE",
                    realized_pnl_minor_units=dialog.realized_pnl_minor_units,
                    broker_order_id=dialog.broker_order_id,
                    reason=dialog.reason,
                    operator="UI_OPERATOR",
                )
                if ack.accepted:
                    QMessageBox.information(
                        self,
                        "Sucesso",
                        f"Ordem {order.order_id} resolvida como SETTLED com sucesso. "
                        "A reserva financeira foi liberada.",
                    )
                else:
                    QMessageBox.warning(
                        self,
                        "Falha na Resolução",
                        f"Não foi possível resolver a ordem: {ack.reason_code}",
                    )
            except Exception as exc:
                QMessageBox.critical(self, "Erro", f"Erro ao comunicar resolução: {exc}")

    def _on_reject_clicked(self, order: OrderSummary) -> None:
        if self._controller is None:
            QMessageBox.warning(self, "Aviso", "Controlador UI não disponível.")
            return

        dialog = RejectionResolutionDialog(order, parent=self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            try:
                ack = self._controller.resolve_order(
                    order.order_id,
                    "REJECT",
                    broker_order_id=order.broker_order_id,
                    reason=dialog.reason,
                    operator="UI_OPERATOR",
                )
                if ack.accepted:
                    QMessageBox.information(
                        self,
                        "Sucesso",
                        f"Ordem {order.order_id} confirmada como REJECTED. "
                        "A reserva de risco foi liberada.",
                    )
                else:
                    QMessageBox.warning(
                        self,
                        "Falha na Resolução",
                        f"Não foi possível rejeitar a ordem: {ack.reason_code}",
                    )
            except Exception as exc:
                QMessageBox.critical(self, "Erro", f"Erro ao comunicar resolução: {exc}")
