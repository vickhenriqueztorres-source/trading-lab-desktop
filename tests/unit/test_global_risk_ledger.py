from __future__ import annotations

from datetime import timedelta
from uuid import uuid4

import pytest

from apps.core.health import HealthGate
from apps.core.risk import (
    ActiveExposurePort,
    GlobalRiskConfig,
    RestoredExposure,
    RiskLedger,
    RiskLimitExceededError,
    RiskState,
    canonicalize_symbol,
)
from packages.domain.models import Broker, Direction, Money, OrderRequest, utc_now


def _make_request(
    broker: Broker,
    account_id: str,
    symbol: str,
    amount_minor: int,
    direction: Direction = Direction.CALL,
) -> OrderRequest:
    return OrderRequest(
        correlation_id=str(uuid4()),
        broker=broker,
        account_id=account_id,
        strategy_id="strat_test",
        strategy_version="1.0.0",
        product="BINARY_OPTION",
        symbol=symbol,
        direction=direction,
        amount=Money(amount_minor, "USD"),
        deadline_at=utc_now() + timedelta(seconds=10),
    )


class MutableExposurePort(ActiveExposurePort):
    def __init__(self) -> None:
        self.exposures: list[RestoredExposure] = []

    def active_reservations(self) -> tuple[RestoredExposure, ...]:
        return tuple(self.exposures)

    def add(
        self,
        reservation_id: str,
        broker: str,
        account_id: str,
        symbol: str,
        amount: Money,
    ) -> None:
        self.exposures.append(RestoredExposure(reservation_id, broker, account_id, amount, symbol))


def test_canonicalize_symbol() -> None:
    assert canonicalize_symbol("frxEURUSD") == "EURUSD"
    assert canonicalize_symbol("FRXEURUSD") == "EURUSD"
    assert canonicalize_symbol("OTC_EURUSD") == "EURUSD"
    assert canonicalize_symbol("EURUSD") == "EURUSD"
    assert canonicalize_symbol("R_100") == "R_100"


def test_global_risk_config_validation() -> None:
    with pytest.raises(ValueError, match="global_max_exposure_minor_units must be positive"):
        GlobalRiskConfig(global_max_exposure_minor_units=0)

    with pytest.raises(ValueError, match="max_exposure_per_symbol_minor_units must be positive"):
        GlobalRiskConfig(max_exposure_per_symbol_minor_units=-100)

    with pytest.raises(ValueError, match="cannot exceed global_max_exposure"):
        GlobalRiskConfig(
            global_max_exposure_minor_units=10000,
            max_exposure_per_symbol_minor_units=20000,
        )

    with pytest.raises(ValueError, match="reference_currency must be a 3-letter ISO code"):
        GlobalRiskConfig(reference_currency="US")


def test_global_exposure_ceiling_cross_broker() -> None:
    config = GlobalRiskConfig(
        global_max_exposure_minor_units=5000,
        max_exposure_per_symbol_minor_units=5000,
    )
    port = MutableExposurePort()
    ledger = RiskLedger(config, active_exposure_port=port)
    health_gate = HealthGate()

    req_deriv = _make_request(Broker.DERIV, "VRTC1001", "R_100", 3000)
    decision = ledger.reserve(req_deriv, health_gate)
    assert decision.amount.minor_units == 3000
    ledger.register_active_reservation(
        "res_1", Broker.DERIV.value, "VRTC1001", "R_100", Money(3000, "USD")
    )
    port.add("res_1", Broker.DERIV.value, "VRTC1001", "R_100", Money(3000, "USD"))

    req_iq = _make_request(Broker.IQ_OPTION, "PRACTICE_01", "EURUSD", 2500)
    with pytest.raises(RiskLimitExceededError) as exc_info:
        ledger.reserve(req_iq, health_gate)
    assert exc_info.value.reason_code == "HG_GLOBAL_EXPOSURE_EXCEEDED"

    req_iq_ok = _make_request(Broker.IQ_OPTION, "PRACTICE_01", "EURUSD", 2000)
    dec_ok = ledger.reserve(req_iq_ok, health_gate)
    assert dec_ok.amount.minor_units == 2000
    ledger.register_active_reservation(
        "res_2", Broker.IQ_OPTION.value, "PRACTICE_01", "EURUSD", Money(2000, "USD")
    )
    port.add("res_2", Broker.IQ_OPTION.value, "PRACTICE_01", "EURUSD", Money(2000, "USD"))

    assert ledger.active_exposure_minor_units == 5000


def test_symbol_exposure_ceiling_cross_broker() -> None:
    config = GlobalRiskConfig(
        global_max_exposure_minor_units=10000,
        max_exposure_per_symbol_minor_units=3000,
    )
    port = MutableExposurePort()
    ledger = RiskLedger(config, active_exposure_port=port)
    health_gate = HealthGate()

    req_deriv = _make_request(Broker.DERIV, "VRTC1001", "frxEURUSD", 2000)
    ledger.reserve(req_deriv, health_gate)
    ledger.register_active_reservation(
        "res_1", Broker.DERIV.value, "VRTC1001", "frxEURUSD", Money(2000, "USD")
    )
    port.add("res_1", Broker.DERIV.value, "VRTC1001", "frxEURUSD", Money(2000, "USD"))

    req_iq = _make_request(Broker.IQ_OPTION, "PRACTICE_01", "EURUSD", 1500)
    with pytest.raises(RiskLimitExceededError) as exc_info:
        ledger.reserve(req_iq, health_gate)
    assert exc_info.value.reason_code == "HG_SYMBOL_EXPOSURE_LIMIT_EXCEEDED"

    req_r100 = _make_request(Broker.DERIV, "VRTC1001", "R_100", 2500)
    dec_r100 = ledger.reserve(req_r100, health_gate)
    assert dec_r100.amount.minor_units == 2500


def test_consolidated_daily_stop_loss() -> None:
    config = GlobalRiskConfig(
        consolidated_daily_stop_loss_minor_units=5000,
    )
    ledger = RiskLedger(config, active_exposure_port=MutableExposurePort())
    health_gate = HealthGate()

    ledger.apply_realized_pnl(Broker.DERIV.value, "VRTC1001", -3000, "USD", health_gate)
    assert ledger.risk_state is RiskState.NORMAL
    assert health_gate.contains("HG_DAILY_STOP_REACHED") is False

    ledger.apply_realized_pnl(Broker.IQ_OPTION.value, "PRACTICE_01", -2500, "USD", health_gate)
    assert ledger.risk_state is RiskState.RISK_LOCKED
    assert health_gate.contains("HG_DAILY_STOP_REACHED") is True

    req = _make_request(Broker.DERIV, "VRTC1001", "R_100", 1000)
    with pytest.raises(RiskLimitExceededError) as exc_info:
        ledger.reserve(req, health_gate)
    assert exc_info.value.reason_code == "HG_DAILY_STOP_REACHED"

    ledger.reset_daily_pnl(health_gate)
    assert ledger.risk_state is RiskState.NORMAL
    assert health_gate.contains("HG_DAILY_STOP_REACHED") is False
    dec = ledger.reserve(req, health_gate)
    assert dec.amount.minor_units == 1000


def test_consecutive_loss_cooldown() -> None:
    config = GlobalRiskConfig(
        max_consecutive_losses=2,
        consolidated_daily_stop_loss_minor_units=50000,
    )
    ledger = RiskLedger(config, active_exposure_port=MutableExposurePort())
    health_gate = HealthGate()

    ledger.apply_realized_pnl(Broker.DERIV.value, "VRTC1001", -1000, "USD", health_gate)
    assert ledger.risk_state is RiskState.NORMAL

    ledger.apply_realized_pnl(Broker.IQ_OPTION.value, "PRACTICE_01", -1000, "USD", health_gate)
    assert ledger.risk_state is RiskState.COOLDOWN
    assert health_gate.contains("HG_COOLDOWN_ACTIVE") is True

    req = _make_request(Broker.DERIV, "VRTC1001", "R_100", 1000)
    with pytest.raises(RiskLimitExceededError) as exc_info:
        ledger.reserve(req, health_gate)
    assert exc_info.value.reason_code == "HG_COOLDOWN_ACTIVE"

    ledger.apply_realized_pnl(Broker.DERIV.value, "VRTC1001", 1500, "USD", health_gate)
    ledger.reset_cooldown(health_gate)
    assert ledger.risk_state is RiskState.NORMAL
    assert health_gate.contains("HG_COOLDOWN_ACTIVE") is False


def test_restore_reservations_and_metrics() -> None:
    config = GlobalRiskConfig(global_max_exposure_minor_units=20000)
    port = MutableExposurePort()
    ledger = RiskLedger(config, active_exposure_port=port)

    reservations = [
        {
            "reservation_id": "res_1",
            "broker": "DERIV",
            "account_id": "VRTC1001",
            "amount_minor": 4000,
            "currency": "USD",
            "symbol": "frxEURUSD",
        },
        {
            "reservation_id": "res_2",
            "broker": "IQ_OPTION",
            "account_id": "PRACTICE_01",
            "amount_minor": 6000,
            "currency": "USD",
            "symbol": "EURUSD",
        },
    ]
    ledger.restore(reservations)
    port.exposures.extend(RiskLedger.validate_restored_exposures(reservations))
    assert len(ledger.restored_exposure) == 2
    assert ledger.active_exposure_minor_units == 10000
    assert ledger.active_symbol_exposure_minor_units("EURUSD") == 10000

    metrics = ledger.get_metrics()
    assert metrics.global_exposure_minor_units == 10000
    assert metrics.global_max_exposure_minor_units == 20000
    assert metrics.risk_state is RiskState.NORMAL
