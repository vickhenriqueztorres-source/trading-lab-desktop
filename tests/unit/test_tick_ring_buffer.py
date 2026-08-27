from __future__ import annotations

import time
from decimal import Decimal

from packages.market_data import TickRingBuffer


def test_last_digit_preserves_decimal_scale() -> None:
    buffer = TickRingBuffer(10)
    assert buffer.push_tick(Decimal("1234.56"), 1).last_digit == 6
    assert buffer.push_tick(Decimal("109.80"), 2).last_digit == 0
    assert buffer.push_tick(Decimal("0.00194"), 3).last_digit == 4


def test_frequency_and_transition_counts_follow_sliding_window() -> None:
    buffer = TickRingBuffer(3)
    for epoch, quote in enumerate((Decimal("1.01"), Decimal("1.02"), Decimal("1.03")), start=1):
        buffer.push_tick(quote, epoch)
    assert buffer.frequency_counts == [0, 1, 1, 1, 0, 0, 0, 0, 0, 0]
    assert buffer.transition_counts[1][2] == 1
    assert buffer.transition_counts[2][3] == 1

    buffer.push_tick(Decimal("1.04"), 4)
    assert buffer.get_digit_history() == [2, 3, 4]
    assert buffer.frequency_counts == [0, 0, 1, 1, 1, 0, 0, 0, 0, 0]
    assert buffer.transition_counts[1][2] == 0
    assert buffer.transition_counts[2][3] == 1
    assert buffer.transition_counts[3][4] == 1
    assert sum(buffer.frequency_counts) == 3


def test_frequency_percentages_sum_to_one_hundred() -> None:
    buffer = TickRingBuffer(4)
    for epoch, quote in enumerate(
        (Decimal("1.01"), Decimal("1.01"), Decimal("1.02"), Decimal("1.03")), start=1
    ):
        buffer.push_tick(quote, epoch)
    percentages = buffer.get_frequency_percentage()
    assert sum(percentages.values(), Decimal(0)) == Decimal(100)
    assert percentages[1] == Decimal(50)


def test_push_tick_average_is_below_one_hundred_microseconds() -> None:
    buffer = TickRingBuffer(500)
    quotes = tuple(Decimal(f"100.0{digit}") for digit in range(10))
    started = time.perf_counter()
    for index in range(10_000):
        buffer.push_tick(quotes[index % 10], 1_700_000_000 + index)
    average_seconds = (time.perf_counter() - started) / 10_000
    assert average_seconds < 0.0001
    assert len(buffer) == 500
    assert sum(buffer.frequency_counts) == 500
