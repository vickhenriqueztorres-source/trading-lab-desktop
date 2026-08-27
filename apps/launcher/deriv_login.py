from __future__ import annotations

import sys
from collections.abc import Mapping
from decimal import Decimal
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
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

from apps.deriv_worker.demo_session import DerivOptionsRestClient
from apps.deriv_worker.demo_session import SecretValue as TransportSecret
from apps.deriv_worker.schema import DerivWorkerError
from packages.brokers.deriv.credentials import DerivCredentials, DerivCredentialVault
from packages.brokers.deriv.product_config import deriv_product_app_id
from packages.security import SecretValue


class DerivDemoLoginDialog(QDialog):
    """Broker login helper; the token is written directly to the DPAPI vault."""

    def __init__(
        self,
        vault: DerivCredentialVault,
        *,
        rest_client: DerivOptionsRestClient | None = None,
    ) -> None:
        super().__init__()
        self._vault = vault
        self._rest_client = rest_client or DerivOptionsRestClient(timeout=15.0)
        self.setWindowTitle("Conectar à Deriv")
        self.setMinimumWidth(500)
        self.setWindowFlag(Qt.WindowType.WindowContextHelpButtonHint, False)

        layout = QVBoxLayout(self)
        title = QLabel("Conectar conta Deriv")
        title.setStyleSheet("font-size: 18px; font-weight: 700;")
        layout.addWidget(title)
        explanation = QLabel(
            "Cole somente o API Token/PAT. O aplicativo localizará automaticamente suas contas "
            "Demo e Real. O token será protegido pelo cofre DPAPI do Windows."
        )
        explanation.setWordWrap(True)
        layout.addWidget(explanation)

        form = QFormLayout()
        self.token = QLineEdit()
        self.token.setEchoMode(QLineEdit.EchoMode.Password)
        self.token.setPlaceholderText("PAT com permissão trade")
        self.accounts = QComboBox()
        self.accounts.setEnabled(False)
        form.addRow("API Token", self.token)
        form.addRow("Conta", self.accounts)
        layout.addLayout(form)

        self.load_accounts_button = QPushButton("Validar token e carregar contas")
        layout.addWidget(self.load_accounts_button)

        self.confirm_real = QCheckBox(
            "Confirmo que a conta selecionada usa dinheiro real e aceito esse risco."
        )
        self.confirm_real.setVisible(False)
        layout.addWidget(self.confirm_real)
        self.real_confirmation = QLineEdit()
        self.real_confirmation.setPlaceholderText("Digite REAL para confirmar")
        self.real_confirmation.setVisible(False)
        layout.addWidget(self.real_confirmation)

        note = QLabel(
            "O tipo da conta será confirmado pela API oficial. A conta Real nunca será "
            "selecionada automaticamente."
        )
        note.setWordWrap(True)
        layout.addWidget(note)

        buttons = QDialogButtonBox()
        self.connect_button = QPushButton("Conectar conta selecionada")
        self.connect_button.setEnabled(False)
        self.saved_button = QPushButton("Usar credenciais salvas")
        self.offline_button = QPushButton("Continuar sem Deriv")
        buttons.addButton(self.connect_button, QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.addButton(self.saved_button, QDialogButtonBox.ButtonRole.ActionRole)
        buttons.addButton(self.offline_button, QDialogButtonBox.ButtonRole.RejectRole)
        layout.addWidget(buttons)
        self.connect_button.clicked.connect(self._save)
        self.load_accounts_button.clicked.connect(self._load_accounts)
        self.accounts.currentIndexChanged.connect(self._account_changed)
        self.saved_button.clicked.connect(self._use_saved)
        self.offline_button.clicked.connect(self.reject)
        self._load_saved_summary()

    def _load_saved_summary(self) -> None:
        try:
            saved = self._vault.load()
        except (AttributeError, OSError, RuntimeError, ValueError):
            saved = None
        self.saved_button.setVisible(saved is not None)
        if saved is not None:
            mode = "REAL" if saved.account_type == "real" else "DEMO"
            self.saved_button.setText(f"Usar conta {mode} salva")

    def _use_saved(self) -> None:
        try:
            configured = self._vault.is_configured()
        except (AttributeError, OSError, RuntimeError, ValueError):
            configured = False
        if not configured:
            QMessageBox.warning(self, "Credenciais indisponíveis", "Preencha os três campos.")
            return
        self.accept()

    def _save(self) -> None:
        token_text = self.token.text().strip()
        selected = self.accounts.currentData()
        if not token_text or not isinstance(selected, dict):
            QMessageBox.warning(self, "Dados incompletos", "Valide o token e escolha uma conta.")
            return
        account_id = selected.get("account_id")
        account_type = selected.get("account_type")
        if not isinstance(account_id, str) or account_type not in {"demo", "real"}:
            QMessageBox.critical(self, "Conta inválida", "A resposta da Deriv não é válida.")
            return
        if account_type == "real" and (
            not self.confirm_real.isChecked()
            or self.real_confirmation.text().strip().upper() != "REAL"
        ):
            QMessageBox.warning(
                self,
                "Confirmação de dinheiro real",
                "Marque a confirmação e digite REAL para conectar essa conta.",
            )
            return
        try:
            self._vault.save(
                DerivCredentials(
                    account_id=account_id,
                    account_type=account_type,
                    access_token=SecretValue.from_text(token_text),
                )
            )
        except (OSError, RuntimeError, ValueError):
            QMessageBox.critical(
                self,
                "Falha no cofre",
                "Não foi possível proteger as credenciais no Windows.",
            )
            return
        self.token.clear()
        self.accept()

    def _load_accounts(self) -> None:
        token_text = self.token.text().strip()
        if not token_text:
            QMessageBox.warning(self, "Token necessário", "Cole o API Token da Deriv.")
            return
        self.load_accounts_button.setEnabled(False)
        try:
            payload = self._rest_client.get_accounts(
                TransportSecret(token_text), deriv_product_app_id()
            )
            raw_accounts = payload.get("data")
            if not isinstance(raw_accounts, list):
                raise ValueError("DERIV_ACCOUNT_LIST_INVALID")
            accounts: list[Mapping[str, object]] = []
            for item in raw_accounts:
                if not isinstance(item, dict):
                    raise ValueError("DERIV_ACCOUNT_LIST_INVALID")
                account_id = item.get("account_id")
                account_type = item.get("account_type")
                if (
                    isinstance(account_id, str)
                    and account_id.strip()
                    and account_type in {"demo", "real"}
                    and item.get("status", "active") == "active"
                ):
                    accounts.append(item)
            if not accounts:
                raise ValueError("DERIV_NO_ACTIVE_OPTIONS_ACCOUNT")
        except (DerivWorkerError, OSError, RuntimeError, ValueError):
            self.token.clear()
            QMessageBox.critical(
                self,
                "Token não autorizado",
                "A Deriv não aceitou o token. Confirme a permissão trade e tente novamente.",
            )
            self.load_accounts_button.setEnabled(True)
            return
        self.accounts.clear()
        self.accounts.addItem("Selecione uma conta…", None)
        for account in sorted(accounts, key=lambda item: item["account_type"] == "real"):
            account_type = str(account["account_type"])
            label = "DEMO" if account_type == "demo" else "REAL — DINHEIRO REAL"
            currency = str(account.get("currency", "USD")).upper()
            balance = account.get("balance")
            balance_text = ""
            if isinstance(balance, (int, Decimal)) and not isinstance(balance, bool):
                balance_text = f" — {currency} {balance}"
            self.accounts.addItem(
                f"{label} — {account['account_id']}{balance_text}",
                {"account_id": account["account_id"], "account_type": account_type},
            )
        self.accounts.setEnabled(True)
        self.connect_button.setEnabled(False)
        self.load_accounts_button.setEnabled(True)
        self._account_changed()

    def _account_changed(self) -> None:
        selected = self.accounts.currentData()
        is_real = isinstance(selected, dict) and selected.get("account_type") == "real"
        self.connect_button.setEnabled(isinstance(selected, dict))
        self.confirm_real.setVisible(is_real)
        self.real_confirmation.setVisible(is_real)
        if not is_real:
            self.confirm_real.setChecked(False)
            self.real_confirmation.clear()


def prompt_for_deriv_demo(profile_dir: Path) -> bool:
    application = QApplication.instance()
    owns_application = application is None
    if application is None:
        application = QApplication(sys.argv[:1])
    vault = DerivCredentialVault(Path(profile_dir) / "broker_credentials")
    dialog = DerivDemoLoginDialog(vault)
    accepted = dialog.exec() == QDialog.DialogCode.Accepted
    if owns_application:
        application.quit()
    return accepted


def show_deriv_login_error() -> None:
    application = QApplication.instance()
    owns_application = application is None
    if application is None:
        application = QApplication(sys.argv[:1])
    QMessageBox.critical(
        None,
        "Falha ao conectar à Deriv",
        "A Deriv não confirmou a conta selecionada. Verifique o token, a permissão trade e a "
        "conexão com a internet. As credenciais foram removidas para permitir nova tentativa.",
    )
    if owns_application:
        application.quit()
