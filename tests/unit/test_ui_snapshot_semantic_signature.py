from __future__ import annotations

from datetime import UTC, datetime

from packages.protocol.ui_messages import (
    BrokerCardStatus,
    HealthGateStatus,
    OrderSummary,
    UiAccountMode,
    UiGlobalState,
    UiProjectionSnapshot,
)


def _sample_snapshot(
    *,
    balance_age: int | None = 5,
    latency: int | None = 42,
    balance: int | None = 100_00,
) -> UiProjectionSnapshot:
    card = BrokerCardStatus(
        broker="IQOPTION",
        account_mode=UiAccountMode.PRACTICE,
        is_connected=True,
        balance_minor_units=balance,
        currency="USD" if balance is not None else None,
        clock_synced=True,
        balance_age_seconds=balance_age,
        clock_latency_ms=latency,
    )
    gate = HealthGateStatus(
        gate_name="GLOBAL_ENTRY_GATE",
        is_open=True,
        reason_code=None,
        description="Operational",
    )
    return UiProjectionSnapshot(
        global_state=UiGlobalState.READY,
        safe_stop_active=False,
        health_gates=(gate,),
        broker_cards=(card,),
        active_orders=(),
        daily_pnl_minor_units=0,
        daily_pnl_currency="USD",
    )


def test_semantic_signature_ignores_volatile_balance_age_and_latency() -> None:
    snap1 = _sample_snapshot(balance_age=1, latency=30)
    snap2 = _sample_snapshot(balance_age=15, latency=120)

    # In dataclass equality, snap1 != snap2 due to volatile fields
    assert snap1 != snap2

    # In semantic signature, they must match identically so UI does not re-render
    assert snap1.semantic_signature() == snap2.semantic_signature()


def test_semantic_signature_detects_financial_changes() -> None:
    snap1 = _sample_snapshot(balance=100_00)
    snap2 = _sample_snapshot(balance=105_00)

    assert snap1.semantic_signature() != snap2.semantic_signature()


def test_semantic_signature_detects_order_changes() -> None:
    snap1 = _sample_snapshot()
    order = OrderSummary(
        order_id="ord-1",
        broker="IQOPTION",
        symbol="EURUSD",
        direction="CALL",
        amount_minor_units=10_00,
        currency="USD",
        state="OPEN",
        created_at_utc=datetime.now(UTC),
    )
    snap2 = UiProjectionSnapshot(
        global_state=snap1.global_state,
        safe_stop_active=snap1.safe_stop_active,
        health_gates=snap1.health_gates,
        broker_cards=snap1.broker_cards,
        active_orders=(order,),
        daily_pnl_minor_units=snap1.daily_pnl_minor_units,
        daily_pnl_currency=snap1.daily_pnl_currency,
    )

    assert snap1.semantic_signature() != snap2.semantic_signature()
