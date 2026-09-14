from __future__ import annotations

import sys
import tempfile
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from PySide6.QtWidgets import QApplication  # noqa: E402

from apps.ui.app import TradingLabMainWindow  # noqa: E402
from apps.ui.auth.login_window import LoginWindow  # noqa: E402
from apps.ui.onboarding import set_onboarding_done  # noqa: E402
from apps.ui.theme import get_application_stylesheet  # noqa: E402
from packages.protocol.ui_messages import (  # noqa: E402
    BrokerCardStatus,
    HealthGateStatus,
    OrderSummary,
    UiAccountMode,
    UiAuthStatusResponse,
    UiDerivAssetRank,
    UiDerivStrategyStatus,
    UiDigitRiskConfig,
    UiGlobalState,
    UiIqOptionAssetRank,
    UiIqOptionRiskConfig,
    UiLogLevel,
    UiOperationalLogEntry,
    UiProjectionSnapshot,
)


def create_mock_snapshot() -> UiProjectionSnapshot:
    b1 = BrokerCardStatus(
        broker="DERIV",
        account_mode=UiAccountMode.PRACTICE,
        is_connected=True,
        balance_minor_units=1045025,  # $10,450.25
        currency="USD",
        clock_synced=True,
        connection_label="Demo CR984125",
        clock_latency_ms=38,
    )
    b2 = BrokerCardStatus(
        broker="IQOPTION",
        account_mode=UiAccountMode.PRACTICE,
        is_connected=True,
        balance_minor_units=524080,  # $5,240.80
        currency="USD",
        clock_synced=True,
        connection_label="Practice",
        clock_latency_ms=45,
    )

    now = datetime.now(UTC)
    o1 = OrderSummary(
        order_id="TL-DERIV-98214",
        broker="DERIV",
        symbol="R_100",
        direction="DIGITDIFF",
        amount_minor_units=500,  # $5.00
        currency="USD",
        state="CLOSED",
        created_at_utc=now,
        realized_pnl_minor_units=48,  # +$0.48
    )
    o2 = OrderSummary(
        order_id="TL-IQ-44102",
        broker="IQOPTION",
        symbol="EURUSD-OTC",
        direction="CALL",
        amount_minor_units=1000,  # $10.00
        currency="USD",
        state="CLOSED",
        created_at_utc=now,
        realized_pnl_minor_units=850,  # +$8.50
    )
    o3 = OrderSummary(
        order_id="TL-DERIV-98215",
        broker="DERIV",
        symbol="R_50",
        direction="DIGITOVER",
        amount_minor_units=1000,
        currency="USD",
        state="OPEN",
        created_at_utc=now,
    )

    iq_ranks = (
        UiIqOptionAssetRank(
            symbol="EURUSD-OTC",
            display_name="EUR/USD (OTC)",
            rsi="26.4",
            direction="CALL",
            condition="OVERSOLD",
            selected=True,
            status="SIGNAL_READY",
        ),
        UiIqOptionAssetRank(
            symbol="GBPUSD-OTC",
            display_name="GBP/USD (OTC)",
            rsi="73.8",
            direction="PUT",
            condition="OVERBOUGHT",
            selected=False,
            status="MONITORING",
        ),
        UiIqOptionAssetRank(
            symbol="USDJPY",
            display_name="USD/JPY",
            rsi="48.2",
            direction=None,
            condition="NEUTRAL",
            selected=False,
            status="MONITORING",
        ),
        UiIqOptionAssetRank(
            symbol="EURJPY-OTC",
            display_name="EUR/JPY (OTC)",
            rsi="81.5",
            direction="PUT",
            condition="OVERBOUGHT",
            selected=False,
            status="MONITORING",
        ),
    )

    deriv_ranks = (
        UiDerivAssetRank(
            symbol="R_100",
            state="CANDIDATE",
            reason_code="EDGE_CONFIRMED",
            warmup_current=500,
            warmup_required=500,
            selected=True,
            strategy_id="selective-differs-edge",
            contract_type="DIGITDIFF",
            barrier=7,
            estimated_probability_pct="93.4",
            required_probability_pct="92.25",
            conservative_margin_pct="1.15",
            analysis_latency_microseconds=180,
        ),
        UiDerivAssetRank(
            symbol="R_50",
            state="WARMING_UP",
            reason_code="DATA_WARMUP",
            warmup_current=380,
            warmup_required=500,
            selected=False,
            analysis_latency_microseconds=150,
        ),
    )

    strat_statuses = (
        UiDerivStrategyStatus(
            strategy_id="selective-differs-edge",
            display_name="Selective Differs Edge",
            markets="R_100",
            lifecycle_status="ACTIVE",
            signal_state="SHADOW_SIGNAL",
            reason_code="STATISTICAL_EDGE_ACTIVE",
            warmup_current=500,
            warmup_required=500,
            last_signal_epoch=int(now.timestamp()),
            last_contract_type="DIGITDIFF",
            last_barrier=7,
            estimated_probability_pct="93.4",
            required_probability_pct="92.25",
            analysis_latency_microseconds=180,
        ),
    )

    logs = (
        UiOperationalLogEntry(
            occurred_at_utc=now,
            level=UiLogLevel.INFO,
            source="CORE",
            event_name="SESSION_STARTED",
            detail="Trading Core operational session initialized safely.",
        ),
        UiOperationalLogEntry(
            occurred_at_utc=now,
            level=UiLogLevel.INFO,
            source="IQOPTION",
            event_name="RADAR_SCAN",
            detail="15 OTC/Forex pairs analyzed. 1 signal candidate ready.",
        ),
        UiOperationalLogEntry(
            occurred_at_utc=now,
            level=UiLogLevel.INFO,
            source="DERIV",
            event_name="WARMUP_COMPLETED",
            detail="R_100 warmup 500/500 reached. Edge probability 93.4%.",
        ),
    )

    return UiProjectionSnapshot(
        global_state=UiGlobalState.READY,
        safe_stop_active=False,
        health_gates=(
            HealthGateStatus("HG_GLOBAL", True, None, "Operational"),
            HealthGateStatus("HG_DERIV", True, None, "Deriv Ready"),
            HealthGateStatus("HG_IQOPTION", True, None, "IQ Option Ready"),
        ),
        broker_cards=(b1, b2),
        active_orders=(o1, o2, o3),
        daily_pnl_minor_units=898,  # +$8.98
        daily_pnl_currency="USD",
        global_exposure_minor_units=1000,
        global_max_exposure_minor_units=50000,
        consecutive_losses=0,
        risk_state="NORMAL",
        digit_risk_config=UiDigitRiskConfig(
            stake_minor_units=500,
            daily_stop_loss_minor_units=5000,
            daily_take_profit_minor_units=15000,
            max_consecutive_losses=3,
            cooldown_seconds_after_loss=60.0,
            min_quantum_confidence_pct=Decimal("92.25"),
            selected_symbol="R_100",
            currency="USD",
            active_strategy_id="selective-differs-edge",
        ),
        iqoption_risk_config=UiIqOptionRiskConfig(
            symbol="AUTO",
            stake_minor_units=1000,
            daily_stop_loss_minor_units=5000,
            daily_take_profit_minor_units=20000,
            max_consecutive_losses=2,
            cooldown_seconds_after_loss=60,
            currency="USD",
        ),
        deriv_asset_ranking=deriv_ranks,
        iqoption_asset_ranking=iq_ranks,
        deriv_strategies=strat_statuses,
        deriv_bot_armed=False,
        iqoption_bot_armed=False,
        operational_logs=logs,
    )


def main() -> None:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    app.setStyleSheet(get_application_stylesheet())

    output_dir = REPO_ROOT / "docs" / "screenshots" / "v2"
    output_dir.mkdir(parents=True, exist_ok=True)

    snapshot = create_mock_snapshot()
    mock_controller = MagicMock()
    mock_controller.connected = True
    mock_controller.snapshot = snapshot

    auth_status = UiAuthStatusResponse(
        authorized=True,
        status="AUTHORIZED",
        user_id_preview="trader****@gmail.com",
        plan="PRO",
        expires_at=(datetime.now(UTC) + timedelta(days=90)).strftime("%Y-%m-%d"),
        device_id="win-tl-device-8924b1",
    )
    mock_controller.auth_status = auth_status

    temp_profile = Path(tempfile.mkdtemp())
    set_onboarding_done(temp_profile)

    window = TradingLabMainWindow(mock_controller, profile_dir=temp_profile)
    window.resize(1280, 820)
    window.show()
    window._refresh_projection()
    window._account_page.update_auth_status(auth_status)
    app.processEvents()

    # 1. Resumen
    window._on_page_selected(0)
    app.processEvents()
    window.grab().save(str(output_dir / "01_resumen.png"))
    print("Generated 01_resumen.png")

    # 2. Deriv
    window._on_page_selected(1)
    app.processEvents()
    window.grab().save(str(output_dir / "02_deriv.png"))
    print("Generated 02_deriv.png")

    # 3. IQ Option
    window._on_page_selected(2)
    app.processEvents()
    window.grab().save(str(output_dir / "03_iqoption.png"))
    print("Generated 03_iqoption.png")

    # 4. Actividad
    window._on_page_selected(3)
    app.processEvents()
    window.grab().save(str(output_dir / "04_actividad.png"))
    print("Generated 04_actividad.png")

    # 5. Mi cuenta
    window._on_page_selected(4)
    window._account_page.update_auth_status(auth_status)
    window.repaint()
    app.processEvents()
    window.grab().save(str(output_dir / "05_mi_cuenta.png"))
    print("Generated 05_mi_cuenta.png")

    # 6. Login Window
    login_dialog = LoginWindow(mock_controller)
    login_dialog.resize(460, 520)
    login_dialog.show()
    login_dialog._txt_email.setText("trader.pro@tradinglab.com")
    app.processEvents()
    login_dialog.grab().save(str(output_dir / "06_login.png"))
    print("Generated 06_login.png")

    print("All screenshots generated successfully in docs/screenshots/v2/")


if __name__ == "__main__":
    main()
