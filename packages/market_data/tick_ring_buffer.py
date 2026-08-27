from __future__ import annotations

import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class DigitTick:
    epoch: int
    quote_decimal: Decimal
    last_digit: int
    received_at_monotonic: float

    def __post_init__(self) -> None:
        if type(self.epoch) is not int or self.epoch <= 0:
            raise ValueError("tick epoch must be a positive integer")
        if not self.quote_decimal.is_finite() or self.quote_decimal <= 0:
            raise ValueError("tick quote must be a positive finite Decimal")
        if type(self.last_digit) is not int or not 0 <= self.last_digit <= 9:
            raise ValueError("last digit must be between zero and nine")
        if self.received_at_monotonic < 0:
            raise ValueError("monotonic receipt time cannot be negative")


@dataclass(frozen=True, slots=True)
class DigitFrequencySnapshot:
    symbol: str
    total_ticks: int
    frequency_counts: tuple[int, ...]
    frequency_percentages: tuple[Decimal, ...]
    transport_latency_microseconds: int

    def __post_init__(self) -> None:
        if not self.symbol or len(self.symbol) > 32:
            raise ValueError("frequency snapshot symbol is invalid")
        if type(self.total_ticks) is not int or self.total_ticks < 0:
            raise ValueError("frequency snapshot total is invalid")
        if len(self.frequency_counts) != 10 or any(
            type(value) is not int or value < 0 for value in self.frequency_counts
        ):
            raise ValueError("frequency snapshot counts are invalid")
        if sum(self.frequency_counts) != self.total_ticks:
            raise ValueError("frequency snapshot total does not match counts")
        if len(self.frequency_percentages) != 10 or any(
            not value.is_finite() or value < 0 or value > 100
            for value in self.frequency_percentages
        ):
            raise ValueError("frequency snapshot percentages are invalid")
        if (
            type(self.transport_latency_microseconds) is not int
            or self.transport_latency_microseconds < 0
        ):
            raise ValueError("transport latency is invalid")

    def to_payload(self) -> dict[str, object]:
        return {
            "frequency_counts": list(self.frequency_counts),
            "frequency_percentages": [str(value) for value in self.frequency_percentages],
            "symbol": self.symbol,
            "total_ticks": self.total_ticks,
            "transport_latency_microseconds": self.transport_latency_microseconds,
        }

    @classmethod
    def from_payload(cls, payload: Mapping[str, object]) -> DigitFrequencySnapshot:
        expected = {
            "frequency_counts",
            "frequency_percentages",
            "symbol",
            "total_ticks",
            "transport_latency_microseconds",
        }
        if set(payload) != expected:
            raise ValueError("frequency snapshot payload fields are invalid")
        counts = payload.get("frequency_counts")
        percentages = payload.get("frequency_percentages")
        symbol = payload.get("symbol")
        total = payload.get("total_ticks")
        latency = payload.get("transport_latency_microseconds")
        if (
            not isinstance(counts, list)
            or not all(type(value) is int for value in counts)
            or not isinstance(percentages, list)
            or not all(isinstance(value, str) for value in percentages)
            or not isinstance(symbol, str)
            or type(total) is not int
            or type(latency) is not int
        ):
            raise ValueError("frequency snapshot payload types are invalid")
        return cls(
            symbol=symbol,
            total_ticks=total,
            frequency_counts=tuple(counts),
            frequency_percentages=tuple(Decimal(value) for value in percentages),
            transport_latency_microseconds=latency,
        )


class TickRingBuffer:
    """Fixed-size digit window with constant-time tick insertion and eviction."""

    def __init__(
        self,
        capacity: int = 500,
        *,
        monotonic_clock: Callable[[], float] = time.perf_counter,
    ) -> None:
        if type(capacity) is not int or capacity <= 0:
            raise ValueError("tick ring-buffer capacity must be a positive integer")
        self.capacity = capacity
        self._monotonic_clock = monotonic_clock
        self._ticks: list[DigitTick | None] = [None] * capacity
        self._frequency_counts = [0] * 10
        self._transition_counts = [[0] * 10 for _ in range(10)]
        self._start = 0
        self._size = 0
        self._lock = threading.Lock()

    def __len__(self) -> int:
        with self._lock:
            return self._size

    @property
    def frequency_counts(self) -> list[int]:
        with self._lock:
            return list(self._frequency_counts)

    @property
    def transition_counts(self) -> list[list[int]]:
        with self._lock:
            return [list(row) for row in self._transition_counts]

    def push_tick(self, quote: Decimal, epoch: int) -> DigitTick:
        if not isinstance(quote, Decimal):
            raise TypeError("tick quote must use Decimal")
        if not quote.is_finite() or quote <= 0:
            raise ValueError("tick quote must be positive and finite")
        if type(epoch) is not int or epoch <= 0:
            raise ValueError("tick epoch must be a positive integer")
        digit = int(quote.as_tuple().digits[-1])
        tick = DigitTick(epoch, quote, digit, self._monotonic_clock())

        with self._lock:
            previous: DigitTick | None = None
            if self._size > 0:
                previous = self._ticks[(self._start + self._size - 1) % self.capacity]

            if self._size == self.capacity:
                insertion_index = self._start
                evicted = self._ticks[insertion_index]
                assert evicted is not None
                self._frequency_counts[evicted.last_digit] -= 1
                if self.capacity > 1:
                    successor = self._ticks[(self._start + 1) % self.capacity]
                    assert successor is not None
                    self._transition_counts[evicted.last_digit][successor.last_digit] -= 1
                else:
                    previous = None
                self._start = (self._start + 1) % self.capacity
            else:
                insertion_index = (self._start + self._size) % self.capacity
                self._size += 1

            self._ticks[insertion_index] = tick
            self._frequency_counts[digit] += 1
            if previous is not None:
                self._transition_counts[previous.last_digit][digit] += 1
        return tick

    def get_frequency_percentage(self) -> dict[int, Decimal]:
        with self._lock:
            if self._size == 0:
                return {digit: Decimal(0) for digit in range(10)}
            total = Decimal(self._size)
            return {
                digit: (Decimal(count) * Decimal(100)) / total
                for digit, count in enumerate(self._frequency_counts)
            }

    def get_digit_history(self, limit: int = 20) -> list[int]:
        if type(limit) is not int or limit < 0:
            raise ValueError("digit history limit must be a non-negative integer")
        with self._lock:
            count = min(limit, self._size)
            first = self._size - count
            result: list[int] = []
            for offset in range(first, self._size):
                tick = self._ticks[(self._start + offset) % self.capacity]
                assert tick is not None
                result.append(tick.last_digit)
            return result
