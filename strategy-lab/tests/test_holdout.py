"""Tests for sealed holdout management, single-opening, and burned ranges (R-RES-2)."""

from __future__ import annotations

from decimal import Decimal

import pytest
from primitives import Candle
from strategy_lab.research.holdout import (
    THREE_MONTHS_S,
    HoldoutManager,
    compute_candles_hash,
    separate_holdout,
)
from strategy_lab.research.synthetic import BASE_TS


def _generate_candles(count: int, step_s: int = 60) -> list[Candle]:
    candles = []
    for i in range(count):
        ts = BASE_TS + i * step_s
        o = Decimal("100.0") + Decimal(i % 10) / Decimal("100")
        c = o + Decimal("0.05")
        candles.append(
            Candle(
                ts=ts,
                o=o,
                h=c + Decimal("0.05"),
                l=o - Decimal("0.05"),
                c=c,
                tick_vol=100,
            )
        )
    return candles


def test_holdout_separation_and_hash_stability() -> None:
    """R-RES-2: separa últimos 3 meses e calcula hash determinístico."""
    # 200 days of data: 200 * 1440 candles
    candles = _generate_candles(200, step_s=86400)  # 1 candle per day for test speed
    split = separate_holdout(candles, duration_s=THREE_MONTHS_S)

    assert len(split.train_val_candles) > 0
    assert len(split.holdout_candles) > 0
    assert split.holdout_range[0] < split.holdout_range[1]
    assert len(split.holdout_hash) == 64

    # Hash determinístico
    h2 = compute_candles_hash(split.holdout_candles)
    assert split.holdout_hash == h2


def test_holdout_open_once_fails_on_second_attempt() -> None:
    """R-RES-2: open_once(run_id) só pode ser chamado uma vez; 2ª chamada lança RuntimeError."""
    manager = HoldoutManager()
    candles = _generate_candles(50, step_s=60)

    run_id = "test_run_001"
    assert manager.is_opened(run_id) is False

    # First open: success
    opened = manager.open_once(run_id, candles)
    assert len(opened) == len(candles)
    assert manager.is_opened(run_id) is True

    # Second open on same run_id: MUST fail closed
    with pytest.raises(RuntimeError, match="Double-opening is prohibited"):
        manager.open_once(run_id, candles)


def test_holdout_burned_range_prohibits_reuse() -> None:
    """R-RES-2: burn(range) registra faixa queimada e impede seu reuso na próxima rodada."""
    manager = HoldoutManager()
    range1 = (1_700_000_000, 1_707_776_000)

    assert manager.is_burned(range1) is False

    # Burn the range
    manager.burn(range1, run_id="run_burned_01")
    assert manager.is_burned(range1) is True

    # Attempting to use the burned range raises ValueError
    with pytest.raises(ValueError, match="is burned"):
        manager.refuse_if_burned(range1)

    # Overlapping range also detected as burned
    overlapping_range = (1_705_000_000, 1_710_000_000)
    assert manager.is_burned(overlapping_range) is True

    # Completely different range is permitted
    new_range = (1_720_000_000, 1_727_776_000)
    assert manager.is_burned(new_range) is False
    manager.refuse_if_burned(new_range)  # no exception
