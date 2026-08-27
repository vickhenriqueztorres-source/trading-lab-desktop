from __future__ import annotations

import pytest

from apps.core.health import CoreHealthGate, HealthGate, HealthGateSnapshot
from packages.persistence.health import DatabaseFailureReason, DatabaseHealth


def test_health_gate_alias() -> None:
    assert CoreHealthGate is HealthGate


def test_initial_state_open() -> None:
    gate = HealthGate()
    assert gate.global_state.is_open is True
    assert gate.global_state.reason_code is None
    assert gate.state.is_open is True
    assert gate.state.reason_code is None

    can_enter, reason = gate.can_enter_order("DERIV", "VRTC1001")
    assert can_enter is True
    assert reason is None

    gate.ensure_open("DERIV", "VRTC1001")
    gate.ensure_open()


def test_scoped_broker_health_isolation() -> None:
    gate = HealthGate()

    # IQ Option becomes disconnected
    gate.register_broker_health("IQOPTION", "PRACTICE_01", is_ready=False)

    # IQ Option is blocked
    can_iq, reason_iq = gate.can_enter_order("IQOPTION", "PRACTICE_01")
    assert can_iq is False
    assert reason_iq == "HG_WORKER_DISCONNECTED"
    with pytest.raises(RuntimeError, match="Health Gate blocked: HG_WORKER_DISCONNECTED"):
        gate.ensure_open("IQOPTION", "PRACTICE_01")

    # Deriv is NOT blocked (strict cross-broker isolation)
    can_deriv, reason_deriv = gate.can_enter_order("DERIV", "VRTC1001")
    assert can_deriv is True
    assert reason_deriv is None
    gate.ensure_open("DERIV", "VRTC1001")

    # Global state remains open because no global blocker was added
    assert gate.global_state.is_open is True
    assert gate.global_state.reason_code is None

    # Reconnect IQ Option
    gate.register_broker_health("IQOPTION", "PRACTICE_01", is_ready=True)
    can_iq_now, reason_iq_now = gate.can_enter_order("IQOPTION", "PRACTICE_01")
    assert can_iq_now is True
    assert reason_iq_now is None


def test_global_blocker_blocks_all_brokers() -> None:
    gate = HealthGate()

    # Register both brokers healthy
    gate.register_broker_health("DERIV", "VRTC1001", is_ready=True)
    gate.register_broker_health("IQOPTION", "PRACTICE_01", is_ready=True)

    # Apply global safe stop
    gate.block("HG_SAFE_STOP")

    assert gate.global_state.is_open is False
    assert gate.global_state.reason_code == "HG_SAFE_STOP"

    # Both brokers are blocked by global safe stop
    can_deriv, reason_deriv = gate.can_enter_order("DERIV", "VRTC1001")
    assert can_deriv is False
    assert reason_deriv == "HG_SAFE_STOP"

    can_iq, reason_iq = gate.can_enter_order("IQOPTION", "PRACTICE_01")
    assert can_iq is False
    assert reason_iq == "HG_SAFE_STOP"

    # Clear safe stop
    gate.clear_if("HG_SAFE_STOP")
    can_deriv_2, _ = gate.can_enter_order("DERIV", "VRTC1001")
    can_iq_2, _ = gate.can_enter_order("IQOPTION", "PRACTICE_01")
    assert can_deriv_2 is True
    assert can_iq_2 is True


def test_broker_market_data_blocker_applies_to_financial_account_only() -> None:
    gate = HealthGate()
    gate.block_scope("DERIV", "market-data", "MD_CLOCK_UNTRUSTED")

    assert gate.can_enter_order("DERIV", "VRTC1001") == (
        False,
        "MD_CLOCK_UNTRUSTED",
    )
    assert gate.can_enter_order("IQOPTION", "PRACTICE_01") == (True, None)

    gate.clear_scope("DERIV", "market-data", "MD_CLOCK_UNTRUSTED")
    assert gate.can_enter_order("DERIV", "VRTC1001") == (True, None)


def test_database_health_failure_blocks_globally() -> None:
    db_health = DatabaseHealth()
    gate = HealthGate(db_health)

    gate.fail_database(DatabaseFailureReason.DB_WRITE_FAILED)

    assert gate.global_state.is_open is False
    assert gate.global_state.reason_code == "DB_WRITE_FAILED"

    can_deriv, reason = gate.can_enter_order("DERIV", "VRTC1001")
    assert can_deriv is False
    assert reason == "DB_WRITE_FAILED"


def test_health_gate_snapshot() -> None:
    gate = HealthGate()
    gate.block_scope("DERIV", "VRTC1001", "HG_ORDER_UNKNOWN")
    gate.block_scope("IQOPTION", "PRACTICE_01", "HG_WORKER_DISCONNECTED")

    snapshot = gate.get_snapshot()
    assert isinstance(snapshot, HealthGateSnapshot)
    assert snapshot.global_state.is_open is True
    assert ("DERIV", "VRTC1001") in snapshot.scoped_states
    assert snapshot.scoped_states[("DERIV", "VRTC1001")].is_open is False
    assert snapshot.scoped_states[("DERIV", "VRTC1001")].reason_code == "HG_ORDER_UNKNOWN"
    iq_scope_state = snapshot.scoped_states[("IQOPTION", "PRACTICE_01")]
    assert iq_scope_state.is_open is False
    assert iq_scope_state.reason_code == "HG_WORKER_DISCONNECTED"
    assert "HG_ORDER_UNKNOWN" in snapshot.active_blockers
    assert "HG_WORKER_DISCONNECTED" in snapshot.active_blockers
