from __future__ import annotations

import sys
from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication, QDialog

from apps.core.lifecycle_service import CoreLifecycleService
from apps.deriv_worker.fake_transport import FakeDerivTransport
from apps.deriv_worker.order_session import DerivLiveOrderSession
from apps.deriv_worker.schema import DerivWorkerError
from apps.launcher.deriv_login import DerivDemoLoginDialog
from packages.brokers.deriv.credentials import DerivCredentials, DerivCredentialVault
from packages.security import SecretValue


def test_deriv_credentials_round_trip_without_plaintext(tmp_path: Path) -> None:
    if sys.platform != "win32":
        pytest.skip("DPAPI credential vault is Windows-only")
    directory = tmp_path / "broker_credentials"
    vault = DerivCredentialVault(directory)
    original = DerivCredentials(
        account_id="DOT90004580",
        account_type="demo",
        access_token=SecretValue.from_text("test-token-never-store-as-plaintext"),
    )

    vault.save(original)
    restored = vault.load()

    assert restored is not None
    assert restored.account_id == "DOT90004580"
    assert restored.account_type == "demo"
    assert restored.access_token.reveal_text() == "test-token-never-store-as-plaintext"
    raw = b"".join(path.read_bytes() for path in directory.glob("*.vault"))
    assert b"test-token-never-store-as-plaintext" not in raw
    assert b"DOT90004580" not in raw


def test_login_dialog_requires_only_token_then_saves_selected_account() -> None:
    application = QApplication.instance() or QApplication([])

    class MemoryVault:
        saved: DerivCredentials | None = None

        def save(self, credentials: DerivCredentials) -> None:
            self.saved = credentials

    class RestFake:
        def get_accounts(self, token: object, app_id: str) -> dict[str, object]:
            del token
            assert app_id
            return {
                "data": [
                    {
                        "account_id": "DOT90004580",
                        "account_type": "demo",
                        "currency": "USD",
                        "status": "active",
                    }
                ]
            }

    vault = MemoryVault()
    dialog = DerivDemoLoginDialog(vault, rest_client=RestFake())  # type: ignore[arg-type]
    dialog.token.setText("secret-token")
    dialog._load_accounts()
    dialog.accounts.setCurrentIndex(1)

    dialog._save()

    assert dialog.result() == QDialog.DialogCode.Accepted
    assert vault.saved is not None
    assert vault.saved.account_id == "DOT90004580"
    assert vault.saved.account_type == "demo"
    assert vault.saved.access_token.reveal_text() == "secret-token"
    assert dialog.token.text() == ""
    application.processEvents()


def test_authenticated_demo_session_accepts_current_options_account_id_format() -> None:
    session = DerivLiveOrderSession(
        FakeDerivTransport(demo_authenticated=True),
        "DOT90004580",
        demo_authenticated=True,
    )
    assert session.demo_authenticated is True
    assert session.account_id == "DOT90004580"


def test_authenticated_real_order_session_is_rejected_before_transport() -> None:
    with pytest.raises(DerivWorkerError) as captured:
        DerivLiveOrderSession(
            FakeDerivTransport(demo_authenticated=True),
            "DOT_REAL_PLACEHOLDER",
            demo_authenticated=True,
            account_type="real",
        )

    assert captured.value.reason_code == "DERIV_REAL_ACCOUNT_FORBIDDEN"


def test_live_demo_worker_spec_requires_vault_and_financial_capabilities(tmp_path: Path) -> None:
    service = CoreLifecycleService(
        tmp_path,
        ("simulated", "deriv_read_only"),
        force_auth_simulation=True,
        deriv_transport="live-demo",
    )

    spec = service._deriv_spec()

    assert spec.allow_demo_financial_submission is True
    assert "--credential-vault-dir" in spec.extra_arguments
    assert str(tmp_path / "broker_credentials") in spec.extra_arguments


def test_live_real_worker_spec_is_explicit_and_separate(tmp_path: Path) -> None:
    service = CoreLifecycleService(
        tmp_path,
        ("simulated", "deriv_read_only"),
        force_auth_simulation=True,
        deriv_transport="live-real",
    )

    spec = service._deriv_spec()

    assert spec.allow_demo_financial_submission is False
    assert spec.allow_real_financial_submission is False
    assert spec.extra_arguments[:2] == ("--deriv-transport", "live-real")
