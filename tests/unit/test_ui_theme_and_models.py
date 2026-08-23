from __future__ import annotations

from datetime import UTC, datetime

from apps.ui.theme import (
    ACCENT_CYAN,
    ACCENT_GREEN,
    ACCENT_RED,
    BG_CARD,
    BG_ROOT,
    get_application_stylesheet,
)
from apps.ui.view_model import DashboardViewModel
from packages.protocol.ui_messages import (
    BrokerCardStatus,
    HealthGateStatus,
    OrderSummary,
    UiAccountMode,
    UiGlobalState,
    UiProjectionSnapshot,
)


def test_theme_stylesheet_contains_palette() -> None:
    qss = get_application_stylesheet()
    assert BG_ROOT in qss
    assert BG_CARD in qss
    assert ACCENT_CYAN in qss
    assert ACCENT_GREEN in qss
    assert ACCENT_RED in qss
    assert "QMainWindow" in qss
    assert "QPushButton#SafeStopButton" in qss


def test_view_model_projection_formatting() -> None:
    b1 = BrokerCardStatus("DERIV", UiAccountMode.PRACTICE, True, 1000000, "USD", True, "Demo", 45)
    b2 = BrokerCardStatus(
        "IQ_OPTION", UiAccountMode.PRACTICE, True, 500000, "USD", True, "Practice", 30
    )
    o1 = OrderSummary("ord-1", "DERIV", "R_100", "CALL", 1000, "USD", "OPEN", datetime.now(UTC))

    snapshot = UiProjectionSnapshot(
        global_state=UiGlobalState.READY,
        safe_stop_active=False,
        health_gates=(HealthGateStatus("HG_GLOBAL", True, None, "Operational"),),
        broker_cards=(b1, b2),
        active_orders=(o1,),
        daily_pnl_minor_units=15000,
        daily_pnl_currency="USD",
        global_exposure_minor_units=1000,
        global_max_exposure_minor_units=50000,
        consecutive_losses=0,
        risk_state="NORMAL",
    )

    vm = DashboardViewModel.from_snapshot(snapshot)

    assert vm.global_state == "READY"
    assert vm.daily_pnl == "USD 150.00"
    assert vm.can_safe_stop is True
    assert vm.can_resume is False
    assert len(vm.broker_lines) == 2
    assert "DERIV" in vm.broker_lines[0]
    assert "IQ_OPTION" in vm.broker_lines[1]
    assert len(vm.order_lines) == 1
    assert "R_100" in vm.order_lines[0]
