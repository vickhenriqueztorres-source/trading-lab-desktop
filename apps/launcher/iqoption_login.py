from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from packages.brokers.iqoption.credentials import IQOptionCredentials, IQOptionCredentialVault
from packages.security import SecretValue


class IQOptionPracticeLoginDialog(QDialog):
    """Isolated credential-entry dialog for an explicitly selected IQ account."""

    def __init__(self, vault: IQOptionCredentialVault) -> None:
        super().__init__()
        self._vault = vault
        self.setWindowTitle("Conectar IQ Option")
        self.setMinimumWidth(500)
        self.setWindowFlag(Qt.WindowType.WindowContextHelpButtonHint, False)

        layout = QVBoxLayout(self)
        title = QLabel("Acesso IQ Option")
        title.setStyleSheet("font-size: 18px; font-weight: 700;")
        layout.addWidget(title)
        explanation = QLabel(
            "Informe o e-mail e a senha da conta. As credenciais serão protegidas pelo cofre "
            "DPAPI do Windows e destinadas exclusivamente ao worker IQ Option."
        )
        explanation.setWordWrap(True)
        layout.addWidget(explanation)

        form = QFormLayout()
        self.email = QLineEdit()
        self.email.setPlaceholderText("seu-email@exemplo.com")
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.password.setPlaceholderText("Senha da IQ Option")
        form.addRow("E-mail", self.email)
        form.addRow("Senha", self.password)
        self.account_mode = QComboBox()
        self.account_mode.addItem("Practice / Demo", "practice")
        self.account_mode.addItem("Real — somente leitura", "real")
        form.addRow("Conta", self.account_mode)
        layout.addLayout(form)

        safety = QLabel(
            "A conta selecionada será autenticada e terá o saldo consultado. Envio automático "
            "de ordens Real permanece bloqueado nesta etapa."
        )
        safety.setWordWrap(True)
        layout.addWidget(safety)

        buttons = QDialogButtonBox()
        self.save_button = QPushButton("Conectar")
        cancel_button = QPushButton("Cancelar")
        buttons.addButton(self.save_button, QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.addButton(cancel_button, QDialogButtonBox.ButtonRole.RejectRole)
        layout.addWidget(buttons)
        self.save_button.clicked.connect(self._save)
        cancel_button.clicked.connect(self.reject)

    def _save(self) -> None:
        email = self.email.text().strip()
        password = self.password.text()
        try:
            credentials = IQOptionCredentials(
                email,
                SecretValue.from_text(password),
                account_mode=str(self.account_mode.currentData()),
            )
            self._vault.save(credentials)
        except (OSError, RuntimeError, ValueError):
            self.password.clear()
            QMessageBox.warning(
                self,
                "Credenciais inválidas",
                "Confira o e-mail e a senha. Não foi possível proteger os dados no Windows.",
            )
            return
        self.password.clear()
        self.accept()

    @property
    def selected_account_mode(self) -> str:
        return str(self.account_mode.currentData())


__all__ = ["IQOptionPracticeLoginDialog"]
