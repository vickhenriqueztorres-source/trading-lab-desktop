from __future__ import annotations

from decimal import ROUND_CEILING, Decimal

import pytest

from packages.domain.models import Money
from packages.portfolio_allocation import (
    BoundedMartingaleAllocator,
    BoundedMartingaleConfig,
    BoundedMartingaleState,
)


def _config(*, cap: int = 400) -> BoundedMartingaleConfig:
    return BoundedMartingaleConfig(
        base_stake=Money(100, "USD"),
        multiplier=Decimal("2.00"),
        max_steps=2,
        max_stake=Money(cap, "USD"),
        daily_stop_loss=Money(1000, "USD"),
    )


def test_bounded_martingale_projects_finite_sequence_and_drawdown() -> None:
    projection = BoundedMartingaleAllocator().project(_config())

    assert tuple(item.minor_units for item in projection.stakes) == (100, 200, 400)
    assert projection.maximum_sequence_loss == Money(700, "USD")


def test_bounded_martingale_caps_stake_and_resets_after_final_step() -> None:
    allocator = BoundedMartingaleAllocator()
    config = _config(cap=250)
    state = BoundedMartingaleState()

    state = allocator.after_settlement(config, state, -100)
    assert allocator.stake_for_step(config, state) == Money(200, "USD")
    state = allocator.after_settlement(config, state, -200)
    assert allocator.stake_for_step(config, state) == Money(250, "USD")
    assert allocator.after_settlement(config, state, -250) == BoundedMartingaleState()


def test_bounded_martingale_resets_after_profit_or_break_even() -> None:
    allocator = BoundedMartingaleAllocator()
    config = _config()
    progressed = BoundedMartingaleState(2)

    assert allocator.after_settlement(config, progressed, 75) == BoundedMartingaleState()
    assert allocator.after_settlement(config, progressed, 0) == BoundedMartingaleState()


@pytest.mark.parametrize(
    ("ratio", "expected"),
    ((Decimal("0.10"), 1000), (Decimal("0.09"), 1112)),
)
def test_quote_aware_recovery_covers_the_full_outstanding_loss(
    ratio: Decimal,
    expected: int,
) -> None:
    config = BoundedMartingaleConfig(
        base_stake=Money(100, "USD"),
        multiplier=Decimal("2.00"),
        max_steps=2,
        max_stake=Money(2000, "USD"),
        daily_stop_loss=Money(5000, "USD"),
    )

    stake = BoundedMartingaleAllocator.recovery_stake(
        config,
        BoundedMartingaleState(1),
        outstanding_loss_minor_units=100,
        net_profit_ratio=ratio,
        remaining_loss_budget_minor_units=4900,
    )

    assert stake == Money(expected, "USD")
    assert Decimal(stake.minor_units) * ratio >= Decimal(100)


def test_quote_aware_recovery_never_falls_below_broker_valid_base_stake() -> None:
    config = _config(cap=1_000)

    stake = BoundedMartingaleAllocator.recovery_stake(
        config,
        BoundedMartingaleState(2),
        outstanding_loss_minor_units=3,
        net_profit_ratio=Decimal("0.09"),
        remaining_loss_budget_minor_units=5_000,
    )

    assert stake == Money(100, "USD")
    assert Decimal(stake.minor_units) * Decimal("0.09") >= Decimal(3)


def test_quote_aware_recovery_divides_safely_or_rejects_instead_of_clamping() -> None:
    config = BoundedMartingaleConfig(
        base_stake=Money(100, "USD"),
        multiplier=Decimal("2.00"),
        max_steps=2,
        max_stake=Money(600, "USD"),
        daily_stop_loss=Money(1000, "USD"),
    )
    allocator = BoundedMartingaleAllocator()

    assert allocator.recovery_stake(
        config,
        BoundedMartingaleState(1),
        outstanding_loss_minor_units=100,
        net_profit_ratio=Decimal("0.10"),
        remaining_loss_budget_minor_units=900,
    ) == Money(500, "USD")
    with pytest.raises(ValueError, match="safety limits"):
        allocator.recovery_stake(
            config,
            BoundedMartingaleState(1),
            outstanding_loss_minor_units=200,
            net_profit_ratio=Decimal("0.10"),
            remaining_loss_budget_minor_units=500,
        )


@pytest.mark.parametrize(
    "ratio",
    [Decimal("0.05"), Decimal("0.09"), Decimal("0.10"), Decimal("0.50"), Decimal("0.95")],
)
@pytest.mark.parametrize("outstanding", [35, 100, 1_000, 10_000])
@pytest.mark.parametrize("step", [1, 2, 3, 4])
def test_quote_aware_recovery_stress_never_under_recovers_or_exceeds_caps(
    ratio: Decimal,
    outstanding: int,
    step: int,
) -> None:
    config = BoundedMartingaleConfig(
        base_stake=Money(35, "USD"),
        multiplier=Decimal("2.00"),
        max_steps=4,
        max_stake=Money(50_000, "USD"),
        daily_stop_loss=Money(50_000, "USD"),
    )
    remaining_budget = 40_000
    remaining_attempts = config.max_steps - step + 1
    minimum_target = int(
        (Decimal(outstanding) / Decimal(remaining_attempts)).to_integral_value(
            rounding=ROUND_CEILING
        )
    )
    try:
        result = BoundedMartingaleAllocator.recovery_stake(
            config,
            BoundedMartingaleState(step),
            outstanding_loss_minor_units=outstanding,
            net_profit_ratio=ratio,
            remaining_loss_budget_minor_units=remaining_budget,
        )
    except ValueError:
        assert Decimal(minimum_target) / ratio > Decimal(remaining_budget)
        return
    assert 0 < result.minor_units <= remaining_budget
    assert result.minor_units >= config.base_stake.minor_units
    assert Decimal(result.minor_units) * ratio >= Decimal(minimum_target)


@pytest.mark.parametrize(
    ("multiplier", "steps", "cap"),
    ((Decimal("1.00"), 2, 400), (Decimal("3.10"), 2, 400), (Decimal("2"), 0, 400)),
)
def test_bounded_martingale_rejects_unbounded_configuration(
    multiplier: Decimal,
    steps: int,
    cap: int,
) -> None:
    with pytest.raises(ValueError):
        BoundedMartingaleConfig(
            base_stake=Money(100, "USD"),
            multiplier=multiplier,
            max_steps=steps,
            max_stake=Money(cap, "USD"),
            daily_stop_loss=Money(1000, "USD"),
        )
