from __future__ import annotations

import os
import sys
from datetime import UTC, datetime, timedelta

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from apps.ui.pages.overview_page import OverviewPage
from apps.ui.shell.sidebar import Sidebar, UserProfileCard
from packages.persistence.reader import StateReader
from packages.protocol.ui_messages import (
    BrokerCardStatus,
    HealthGateStatus,
    OrderSummary,
    UiAccountMode,
    UiGlobalState,
    UiProjectionSnapshot,
)


def _get_qapp() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    assert isinstance(app, QApplication)
    return app


def test_user_profile_card_and_sidebar_seals() -> None:
    _get_qapp()
    sidebar = Sidebar()

    # 1. Verify tagline label is removed from sidebar
    assert not hasattr(sidebar, "_lbl_tagline")

    # 2. Verify profile card exists
    profile_card = sidebar._profile_card
    assert isinstance(profile_card, UserProfileCard)

    # 3. Test Trial Plan Seal (Amber)
    sidebar.set_account_info("carlos.trader@empresa.com", "trial")
    assert "Carlos Trader" in profile_card._user_name
    assert "TRIAL" in profile_card._lbl_badge.text()
    assert profile_card._avatar.text() == "C"
    assert "#F59E0B" in profile_card._avatar.styleSheet()

    # 4. Test Pro Plan Seal (Emerald)
    sidebar.set_account_info("Paulo R", "PRO")
    assert profile_card._user_name == "Paulo R"
    assert "PRO" in profile_card._lbl_badge.text()
    assert profile_card._avatar.text() == "P"
    assert "#1FB57A" in profile_card._avatar.styleSheet()

    # 5. Test Diamond Plan Seal (Electric Cyan)
    sidebar.set_account_info("Master VIP", "DIAMOND")
    assert profile_card._user_name == "Master VIP"
    assert "DIAMOND" in profile_card._lbl_badge.text()
    assert profile_card._avatar.text() == "M"
    assert "#00F2FE" in profile_card._avatar.styleSheet()

    # 6. Test Compact Mode
    sidebar.set_compact_mode(True)
    assert sidebar.width() == 175
    assert profile_card._is_compact is True

    sidebar.set_compact_mode(False)
    assert sidebar.width() == 220
    assert profile_card._is_compact is False

    # 7. Test Click Signal (Navigates to Account page index 4)
    selected_pages: list[int] = []
    sidebar.page_selected.connect(selected_pages.append)
    profile_card.mousePressEvent(None)
    assert 4 in selected_pages


def test_reader_iqoption_martingale_statistics(tmp_path) -> None:
    from packages.persistence.database import connect_database
    from packages.persistence.migrations import apply_migrations

    db_path = tmp_path / "test_martingale.db"
    conn = connect_database(db_path)
    apply_migrations(conn)
    reader = StateReader(db_path)

    t0 = datetime(2026, 9, 19, 10, 0, 0, tzinfo=UTC)
    cur = conn.cursor()

    def _insert_order(order_id: str, symbol: str, created_at: datetime, pnl: int) -> None:
        intent_id = f"intent-{order_id}"
        cur.execute(
            """
            INSERT INTO trade_intents(
                intent_id, correlation_id, broker, account_id, product, symbol,
                direction, amount_minor, currency, status, created_at,
                strategy_id, strategy_version
            ) VALUES (
                ?, ?, 'IQ_OPTION', 'IQOPTION_PRACTICE', 'BINARY_OPTION', ?, 'CALL',
                1000, 'USD', 'SUBMITTED', ?, 'rsi_bollinger', '1.0.0'
            )
            """,
            (
                intent_id,
                order_id,
                symbol,
                created_at.isoformat(),
            ),
        )
        cur.execute(
            """
            INSERT INTO orders(
                order_id, intent_id, broker, account_id, state, correlation_id,
                broker_order_id, realized_pnl_minor, created_at, updated_at
            ) VALUES (?, ?, 'IQ_OPTION', 'IQOPTION_PRACTICE', 'SETTLED', ?, ?, ?, ?, ?)
            """,
            (
                order_id,
                intent_id,
                order_id,
                f"broker-{order_id}",
                pnl,
                created_at.isoformat(),
                created_at.isoformat(),
            ),
        )

    # Cycle 1: Win sem gale (G0)
    _insert_order("o1", "EURUSD-OTC", t0, 850)

    # Cycle 2: Loss at G0, Win at Gale 1 (G1)
    _insert_order("o2-1", "EURUSD-OTC", t0 + timedelta(seconds=60), -1000)
    _insert_order("o2-2", "EURUSD-OTC", t0 + timedelta(seconds=120), 1700)

    # Cycle 3: Loss at G0, Loss at G1, Win at Gale 2 (G2)
    _insert_order("o3-1", "GBPUSD-OTC", t0 + timedelta(seconds=200), -1000)
    _insert_order("o3-2", "GBPUSD-OTC", t0 + timedelta(seconds=260), -2000)
    _insert_order("o3-3", "GBPUSD-OTC", t0 + timedelta(seconds=320), 3400)

    # Cycle 4: Loss at G0, Loss at G1, Loss at Gale 2 (Loss G2!)
    _insert_order("o4-1", "USDJPY-OTC", t0 + timedelta(seconds=400), -1000)
    _insert_order("o4-2", "USDJPY-OTC", t0 + timedelta(seconds=460), -2000)
    _insert_order("o4-3", "USDJPY-OTC", t0 + timedelta(seconds=520), -4000)
    conn.commit()
    conn.close()

    stats = reader.iqoption_martingale_statistics()
    assert stats["wins_sem_gale"] == 1
    assert stats["wins_g1"] == 1
    assert stats["wins_g2"] == 1
    assert stats["total_wins"] == 3
    assert stats["losses_g2"] == 1
    assert stats["total_cycles"] == 4
    assert abs(stats["win_rate"] - 75.0) < 0.001


def test_overview_page_shows_iq_martingale_stats() -> None:
    _get_qapp()
    page = OverviewPage()

    b_deriv = BrokerCardStatus(
        "DERIV", UiAccountMode.PRACTICE, True, 1000000, "USD", True, "Demo", 10
    )
    b_iq = BrokerCardStatus(
        "IQOPTION",
        UiAccountMode.PRACTICE,
        True,
        2000000,
        "USD",
        True,
        "Practice",
        15,
        total_trades=20,
        wins=18,
        losses=2,
        realized_pnl_minor_units=54000,
    )

    t0 = datetime.now(UTC)
    orders = (
        OrderSummary(
            "ord-iq-1", "IQOPTION", "EURUSD-OTC", "CALL", 1000, "USD", "SETTLED", t0, "b-1", 850
        ),
        OrderSummary(
            "ord-iq-2",
            "IQOPTION",
            "EURUSD-OTC",
            "PUT",
            1000,
            "USD",
            "SETTLED",
            t0 + timedelta(seconds=60),
            "b-2",
            850,
        ),
    )

    snapshot = UiProjectionSnapshot(
        global_state=UiGlobalState.READY,
        safe_stop_active=False,
        health_gates=(HealthGateStatus("HG_GLOBAL", True, None, "Operational"),),
        broker_cards=(b_deriv, b_iq),
        active_orders=orders,
        daily_pnl_minor_units=54000,
        daily_pnl_currency="USD",
        global_exposure_minor_units=0,
        global_max_exposure_minor_units=50000,
        consecutive_losses=0,
        risk_state="NORMAL",
    )

    from unittest.mock import MagicMock

    mock_controller = MagicMock()
    mock_controller.connected = True
    page.update_projection(snapshot, mock_controller)

    trades_text = page._lbl_iq_trades_val.text()
    wins_text = page._lbl_iq_wins_val.text()
    losses_text = page._lbl_iq_losses_val.text()
    assert int(trades_text) > 0
    assert int(wins_text) > 0
    assert int(losses_text) >= 0
    assert "USD 540.00" in page._lbl_iq_pnl_val.text()
