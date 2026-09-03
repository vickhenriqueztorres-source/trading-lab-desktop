"""Typed foundations for deterministic incremental indicators (R-PRIM-1..3)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from decimal import ROUND_HALF_EVEN, Decimal, getcontext
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

getcontext().prec = 28
getcontext().rounding = ROUND_HALF_EVEN


class Category(StrEnum):
    REGIME = "regime"
    TRIGGER = "trigger"
    CONFIRM = "confirm"


class Candle(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    ts: int
    o: Decimal
    h: Decimal
    l: Decimal  # noqa: E741 - canonical OHLC field required by R-PRIM-1
    c: Decimal
    tick_vol: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_candle(self) -> Candle:
        if self.ts % 60 != 0:
            raise ValueError("candle timestamp must be a multiple of 60")
        if self.l > min(self.o, self.c) or max(self.o, self.c) > self.h:
            raise ValueError("candle OHLC bounds are invalid")
        return self


class ParamRange(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    min: Decimal | int
    max: Decimal | int
    step: Decimal | int
    kind: Literal["int", "decimal"]

    @model_validator(mode="after")
    def validate_range(self) -> ParamRange:
        if self.min > self.max or self.step <= 0:
            raise ValueError("parameter range is invalid")
        if self.kind == "int" and not all(
            isinstance(item, int) for item in (self.min, self.max, self.step)
        ):
            raise ValueError("integer parameter range requires integer values")
        if self.kind == "decimal" and not all(
            isinstance(item, Decimal) for item in (self.min, self.max, self.step)
        ):
            raise ValueError("decimal parameter range requires Decimal values")
        return self


type Direction = Literal["call", "put", "none"]


class Output(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    direction: Direction
    value: Decimal | None
    meta: dict[str, Decimal]


class Indicator(ABC):
    category: Category
    name: str
    param_spec: dict[str, ParamRange]

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
