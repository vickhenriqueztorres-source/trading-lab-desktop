from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import pytest

from apps.core.digit_risk_config import (
    DigitRiskConfig,
    is_bounded_digit_product,
    validate_digit_risk_config,
)
from apps.core.health import HealthGate
from apps.core.risk import RiskLedger, StaticActiveExposurePort
from packages.domain.models import Broker, Direction, Money, OrderRequest, utc_now
from packages.persistence.writer import RiskLimitExceededError


class _Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


def test_digit_risk_config_validates_stable_boundaries() -> None:
    config = DigitRiskConfig()
    assert validate_digit_risk_config(config) == (True, None)
    assert validate_digit_risk_config(replace(config, stake_minor_units=-1)) == (
        False,
        "DIGIT_RISK_STAKE_BELOW_MINIMUM",
    )
    assert validate_digit_risk_config(replace(config, daily_stop_loss_minor_units=0)) == (
        False,
        "DIGIT_RISK_STOP_LOSS_INVALID",
    )
    assert validate_digit_risk_config(replace(config, selected_symbol="UNKNOWN")) == (
        False,
        "DIGIT_RISK_SYMBOL_NOT_ALLOWED",
    )
    assert validate_digit_risk_config(
        replace(config, min_quantum_confidence_pct=Decimal("99.1"))
    ) == (False, "DIGIT_RISK_CONFIDENCE_INVALID")
    assert validate_digit_risk_config(replace(config, active_strategy_id="unknown-strategy")) == (
        False,
        "DIGIT_RISK_STRATEGY_NOT_ALLOWED",
    )


def test_digit_take_profit_and_stop_loss_fail_closed() -> None:
    gate = HealthGate()
    take_ledger = RiskLedger(
        digit_config=replace(DigitRiskConfig(), daily_take_profit_minor_units=300)
    )
    take_ledger.apply_digit_realized_pnl(300, "USD", gate)
    assert take_ledger.check_digit_entry(take_ledger.digit_config, gate) == (
        False,
        "HG_DAILY_TAKE_PROFIT_REACHED",
    )
    assert gate.contains("HG_DAILY_TAKE_PROFIT_REACHED")

    stop_gate = HealthGate()
    stop_ledger = RiskLedger(
        digit_config=replace(DigitRiskConfig(), daily_stop_loss_minor_units=400)
    )
    stop_ledger.apply_digit_realized_pnl(-400, "USD", stop_gate)
    assert stop_ledger.check_digit_entry(stop_ledger.digit_config, stop_gate) == (
        False,
        "HG_DAILY_STOP_REACHED",
    )
    assert stop_gate.contains("HG_DAILY_STOP_REACHED")


def test_digit_loss_cooldown_uses_monotonic_deadline_and_expires() -> None:
    clock = _Clock()
    gate = HealthGate()
    config = replace(
        DigitRiskConfig(),
        daily_stop_loss_minor_units=5000,
        max_consecutive_losses=1,
        cooldown_seconds_after_loss=30.0,
    )
    ledger = RiskLedger(digit_config=config, monotonic_clock=clock)

    ledger.apply_digit_realized_pnl(-100, "USD", gate)
    assert ledger.check_digit_entry(config, gate) == (False, "HG_COOLDOWN_ACTIVE")
    assert ledger.get_digit_metrics().cooldown_remaining_seconds == 30

    clock.now = 130.0
    ledger.refresh_digit_health_gate(gate)
    gate.ensure_open()
    assert ledger.check_digit_entry(config, gate) == (True, None)
    assert ledger.get_digit_metrics().cooldown_remaining_seconds == 0
    assert not gate.contains("HG_COOLDOWN_ACTIVE")


def test_digit_bounded_martingale_progresses_and_resets_only_from_settlements() -> None:
    config = replace(
        DigitRiskConfig(),
        martingale_enabled=True,
        martingale_multiplier=Decimal("2.00"),
        martingale_max_steps=2,
        martingale_max_stake_minor_units=400,
        max_consecutive_losses=5,
        daily_stop_loss_minor_units=1000,
    )
    ledger = RiskLedger(
        digit_config=config,
        active_exposure_port=StaticActiveExposurePort(),
    )

    assert ledger.get_digit_metrics().next_stake_minor_units == 100
    ledger.apply_digit_realized_pnl(-100, "USD")
    assert ledger.get_digit_metrics().martingale_step == 1
    assert ledger.digit_entry_stake(net_profit_ratio=Decimal("0.50")) == Money(200, "USD")
    ledger.apply_digit_realized_pnl(-200, "USD")
    assert ledger.get_digit_metrics().martingale_step == 2
    assert ledger.digit_entry_stake(net_profit_ratio=Decimal("0.75")) == Money(400, "USD")
    ledger.apply_digit_realized_pnl(-400, "USD")
    assert ledger.get_digit_metrics().martingale_step == 0
    assert ledger.get_digit_metrics().next_stake_minor_units == 100

    ledger.apply_digit_realized_pnl(-100, "USD")
    ledger.apply_digit_realized_pnl(80, "USD")
    assert ledger.get_digit_metrics().martingale_step == 2
    assert ledger.get_digit_metrics().cumulative_sequence_loss_minor_units == 20
    assert ledger.digit_entry_stake(net_profit_ratio=Decimal("0.10")) == Money(200, "USD")


def test_partial_recovery_residual_uses_valid_base_stake_instead_of_rejection_loop() -> None:
    config = replace(
        DigitRiskConfig(),
        martingale_enabled=True,
        martingale_max_steps=2,
        martingale_max_stake_minor_units=10_000,
        max_consecutive_losses=5,
        daily_stop_loss_minor_units=10_000,
    )
    ledger = RiskLedger(digit_config=config)

    ledger.apply_digit_realized_pnl(-100, "USD")
    assert ledger.digit_entry_stake(net_profit_ratio=Decimal("0.09")) == Money(1112, "USD")
    ledger.apply_digit_realized_pnl(97, "USD")

    metrics = ledger.get_digit_metrics()
    assert metrics.martingale_step == 2
    assert metrics.cumulative_sequence_loss_minor_units == 3
    assert ledger.digit_entry_stake(net_profit_ratio=Decimal("0.09")) == Money(100, "USD")


def test_digit_bounded_martingale_blocks_projected_daily_stop_breach() -> None:
    gate = HealthGate()
    config = replace(
        DigitRiskConfig(),
        martingale_enabled=True,
        martingale_max_steps=2,
        martingale_max_stake_minor_units=400,
        max_consecutive_losses=5,
        daily_stop_loss_minor_units=700,
    )
    ledger = RiskLedger(digit_config=config)

    ledger.apply_digit_realized_pnl(-600, "USD", gate)

    assert ledger.check_digit_entry(config, gate) == (True, None)
    with pytest.raises(RiskLimitExceededError) as error:
        ledger.digit_entry_stake(gate, net_profit_ratio=Decimal("0.10"))
    assert error.value.reason_code == "DIGIT_MARTINGALE_RECOVERY_UNAFFORDABLE"


def test_digit_bounded_martingale_rejects_unbounded_or_mid_sequence_changes() -> None:
    base = DigitRiskConfig()
    assert validate_digit_risk_config(
        replace(
            base,
            martingale_enabled=True,
            max_consecutive_losses=3,
            daily_stop_loss_minor_units=600,
        )
    ) == (True, None)

    active = replace(
        base,
        martingale_enabled=True,
        max_consecutive_losses=3,
        daily_stop_loss_minor_units=1000,
    )
    ledger = RiskLedger(digit_config=active)
    ledger.apply_digit_realized_pnl(-100, "USD")
    assert ledger.update_digit_risk_config(active) == (True, None)
    assert ledger.get_digit_metrics().martingale_step == 1
    assert ledger.update_digit_risk_config(
        replace(active, martingale_multiplier=Decimal("1.5"))
    ) == (
        False,
        "DIGIT_MARTINGALE_SEQUENCE_ACTIVE",
    )
    assert ledger.update_digit_risk_config(
        replace(active, active_strategy_id="selective-differs-edge")
    ) == (
        False,
        "DIGIT_MARTINGALE_SEQUENCE_ACTIVE",
    )
    disabled = replace(active, martingale_enabled=False)
    assert ledger.update_digit_risk_config(disabled) == (True, None)
    assert ledger.get_digit_metrics().martingale_step == 0


def test_operator_reconfiguration_can_explicitly_reset_demo_recovery_state() -> None:
    gate = HealthGate()
    active = replace(
        DigitRiskConfig(),
        martingale_enabled=True,
        max_consecutive_losses=3,
        daily_stop_loss_minor_units=5000,
        martingale_max_stake_minor_units=5000,
    )
    ledger = RiskLedger(digit_config=active)
    ledger.apply_digit_realized_pnl(-100, "USD", gate)
    assert ledger.get_digit_metrics().martingale_step == 1

    changed = replace(active, stake_minor_units=1000)
    assert ledger.update_digit_risk_config(
        changed,
        gate,
        reset_active_sequence=True,
    ) == (True, None)
    metrics = ledger.get_digit_metrics()
    assert metrics.active_config == changed
    assert metrics.martingale_step == 0
    assert metrics.consecutive_losses == 0
    assert metrics.cumulative_sequence_loss_minor_units == 0
    assert metrics.daily_pnl_minor_units == -100
    assert not gate.contains("HG_COOLDOWN_ACTIVE")


@pytest.mark.parametrize(
    "product",
    ("DIGITOVER", "DIGITUNDER", "DIGITDIFF", "DIGITEVEN", "DIGITODD"),
)
def test_all_three_digit_contract_families_use_core_owned_progressive_stake(
    product: str,
) -> None:
    assert is_bounded_digit_product(product)
    config = replace(
        DigitRiskConfig(),
        martingale_enabled=True,
        max_consecutive_losses=5,
        daily_stop_loss_minor_units=1000,
        martingale_max_stake_minor_units=1000,
    )
    ledger = RiskLedger(
        digit_config=config,
        active_exposure_port=StaticActiveExposurePort(),
    )
    ledger.apply_digit_realized_pnl(-100, "USD")
    request = OrderRequest(
        correlation_id=f"corr-{product}",
        broker=Broker.DERIV,
        account_id="DEMO",
        product=product,
        symbol="R_100",
        direction=Direction.CALL,
        amount=Money(100, "USD"),
        strategy_id="digit-edge",
        strategy_version="1.0.0",
        deadline_at=utc_now() + timedelta(seconds=10),
        duration=1,
        duration_unit="t",
        prediction_digit=(0 if product in {"DIGITDIFF", "DIGITOVER", "DIGITUNDER"} else None),
    )

    with pytest.raises(RiskLimitExceededError) as error:
        ledger.reserve(request)
    assert error.value.reason_code == "DIGIT_MARTINGALE_QUOTE_REQUIRED"

    ratio = Decimal("0.10") if product == "DIGITDIFF" else Decimal("0.90")
    approved_amount = ledger.digit_entry_stake(net_profit_ratio=ratio)
    approved = replace(request, amount=approved_amount)
    assert ledger.reserve(approved).amount == approved_amount
