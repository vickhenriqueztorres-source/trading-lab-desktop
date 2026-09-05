"""Typed foundations for deterministic incremental indicators (local bot implementation)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from decimal import ROUND_HALF_EVEN, Decimal, getcontext
from enum import StrEnum
from typing import Literal

getcontext().prec = 28
getcontext().rounding = ROUND_HALF_EVEN

type Direction = Literal["call", "put", "none"]


class Category(StrEnum):
    REGIME = "regime"
    TRIGGER = "trigger"
    CONFIRM = "confirm"


@dataclass(frozen=True, slots=True)
class Candle:
    ts: int
    o: Decimal
    h: Decimal
    l: Decimal  # noqa: E741 - canonical OHLC field required by R-PRIM-1
    c: Decimal
    tick_vol: int | None

    def __post_init__(self) -> None:
        if self.ts % 60 != 0:
            raise ValueError("candle timestamp must be a multiple of 60")
        if self.tick_vol is not None and (type(self.tick_vol) is not int or self.tick_vol < 0):
            raise ValueError("tick_vol must be non-negative")
        if self.l > min(self.o, self.c) or max(self.o, self.c) > self.h:
            raise ValueError("candle OHLC bounds are invalid")


@dataclass(frozen=True, slots=True)
class ParamRange:
    min: Decimal | int
    max: Decimal | int
    step: Decimal | int
    kind: Literal["int", "decimal"]


@dataclass(frozen=True, slots=True)
class Output:
    direction: Direction
    value: Decimal | None
    meta: dict[str, Decimal] = field(default_factory=dict)


class Indicator(ABC):
    category: Category
    name: str
    param_spec: dict[str, ParamRange]
    requires_tick_volume = False

    @abstractmethod
    def update(self, candle: Candle) -> Output | None:
        """Consume one closed candle and return None only during warm-up."""

    @property
    @abstractmethod
    def warmup_required(self) -> int:
        """Return the minimum number of candles needed for the first output."""

    @abstractmethod
    def reset(self) -> None:
        """Return the indicator to its initial state."""


def int_range(minimum: int, maximum: int, step: int = 1) -> ParamRange:
    return ParamRange(min=minimum, max=maximum, step=step, kind="int")


def decimal_range(minimum: str, maximum: str, step: str) -> ParamRange:
    return ParamRange(
        min=Decimal(minimum),
        max=Decimal(maximum),
        step=Decimal(step),
        kind="decimal",
    )
