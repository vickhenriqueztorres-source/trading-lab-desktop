from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from apps.ui.components.iqoption_workspace import IqOptionWorkspaceWidget


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def test_iq_workspace_reports_effective_gate_not_only_arm_flag(qapp: QApplication) -> None:
    workspace = IqOptionWorkspaceWidget()
    try:
        workspace.update_bot_state(
            True,
            "IQOPTION_BOT_ARMED",
            entry_ready=False,
            entry_blocker="PAYOUT_UNAVAILABLE",
        )

        assert "ENTRADAS BLOQUEADAS" in workspace._automation_pill.text()
        assert workspace._automation_detail.text() == "Entradas bloqueadas: PAYOUT_UNAVAILABLE"
    finally:
        workspace.close()


def test_iq_workspace_explains_rearm_after_reconnect(qapp: QApplication) -> None:
    workspace = IqOptionWorkspaceWidget()
    try:
        workspace.update_bot_state(False, "IQOPTION_CONNECTED_REARM_REQUIRED")

        assert "Clique em ‘Ligar Bot IQ Option’" in workspace._automation_detail.text()
        assert "IQOPTION_CONNECTED_REARM_REQUIRED" not in workspace._automation_detail.text()
    finally:
        workspace.close()


def test_iq_workspace_explains_clock_block_without_requesting_login(qapp: QApplication) -> None:
    workspace = IqOptionWorkspaceWidget()
    try:
        workspace.update_bot_state(
            True, "MD_CLOCK_UNTRUSTED", entry_ready=False, entry_blocker="MD_CLOCK_UNTRUSTED"
        )
        assert "Relógio da corretora" in workspace._automation_detail.text()
        assert "não é necessário refazer o login" in workspace._automation_detail.text()
        assert "ENTRADAS BLOQUEADAS" in workspace._automation_pill.text()
    finally:
        workspace.close()
