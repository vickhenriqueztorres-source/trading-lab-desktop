from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Self

_DECIMAL_PATTERN = re.compile(r"^[0-9]+(?:\.[0-9]+)?$")


@dataclass(frozen=True, slots=True)
class DerivCandleEvent:
    symbol: str
    granularity_seconds: int
    epoch_seconds: int
    open_text: str
    high_text: str
    low_text: str
    close_text: str
    is_closed: bool
    source_event_id: str
    received_at_ms: int

    def __post_init__(self) -> None:
        if not self.symbol.strip() or not self.source_event_id.strip():
            raise ValueError("Deriv candle identity is required")
        if self.granularity_seconds <= 0 or self.epoch_seconds < 0 or self.received_at_ms < 0:
            raise ValueError("Deriv candle timestamps are invalid")
        for value in self.price_texts:
            if not _DECIMAL_PATTERN.fullmatch(value) or set(value) <= {"0", "."}:
                raise ValueError("Deriv candle price must be a positive fixed decimal string")

    @property
    def price_texts(self) -> tuple[str, str, str, str]:
        return (self.open_text, self.high_text, self.low_text, self.close_text)

    @classmethod
    def from_external_payload(cls, payload: object) -> Self:
        if not isinstance(payload, dict):
            raise ValueError("Deriv candle payload must be an object")
        required = {
            "symbol",
            "granularity",
            "epoch",
            "open",
            "high",
            "low",
            "close",
            "is_closed",
            "source_event_id",
            "received_at_ms",
        }
        if set(payload) != required:
            raise ValueError("Deriv candle payload schema is invalid")
        strings = ("symbol", "open", "high", "low", "close", "source_event_id")
        if any(not isinstance(payload[name], str) for name in strings):
            raise ValueError("Deriv candle string field is invalid")
        if any(
            type(payload[name]) is not int for name in ("granularity", "epoch", "received_at_ms")
        ):
            raise ValueError("Deriv candle integer field is invalid")
        if type(payload["is_closed"]) is not bool:
            raise ValueError("Deriv candle closure flag is invalid")
        return cls(
            symbol=payload["symbol"],
            granularity_seconds=payload["granularity"],
            epoch_seconds=payload["epoch"],
            open_text=payload["open"],
            high_text=payload["high"],
            low_text=payload["low"],
            close_text=payload["close"],
            is_closed=payload["is_closed"],
            source_event_id=payload["source_event_id"],
            received_at_ms=payload["received_at_ms"],
        )
