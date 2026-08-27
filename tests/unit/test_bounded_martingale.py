from __future__ import annotations

from decimal import Decimal

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
