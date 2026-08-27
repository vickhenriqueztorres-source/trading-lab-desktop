from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol

from packages.domain.market import MarketHistoryBatch
from packages.domain.models import OrderCommand
from packages.market_data import ClosedCandle


class DerivClosedCandlePort(Protocol):
    def convert(self, payload: object) -> ClosedCandle | None: ...


class DerivCandleHistorySource(Protocol):
    def market_history_batch(
        self,
        symbol: str,
        *,
        style: str,
        count: int = 100,
        timeframe_seconds: int | None = None,
        end_epoch: int | None = None,
    ) -> MarketHistoryBatch: ...


@dataclass(frozen=True, slots=True)
class DigitDiffContractParameters:
    amount: Decimal
    currency: str
    symbol: str
    prediction_digit: int

    def __post_init__(self) -> None:
        if not self.amount.is_finite() or self.amount <= 0:
            raise ValueError("DIGITDIFF amount must be positive and finite")
        if self.currency != "USD":
            raise ValueError("DIGITDIFF currently requires USD")
        if not self.symbol:
            raise ValueError("DIGITDIFF symbol is required")
        if type(self.prediction_digit) is not int or not 0 <= self.prediction_digit <= 9:
            raise ValueError("DIGITDIFF prediction must be between zero and nine")

    def to_buy_payload(self, command: OrderCommand) -> dict[str, object]:
        return {
            "buy": 1,
            "price": self.amount,
            "parameters": {
                "amount": self.amount,
                "barrier": str(self.prediction_digit),
                "basis": "stake",
                "contract_type": "DIGITDIFF",
                "currency": self.currency,
                "duration": 1,
                "duration_unit": "t",
                "symbol": self.symbol,
            },
            "passthrough": {
                "correlation_id": command.correlation_id,
                "order_id": command.order_id,
            },
        }


def build_digit_diff_buy_payload(command: OrderCommand, prediction_digit: int) -> dict[str, object]:
    if command.product.upper() != "DIGITDIFF":
        raise ValueError("digit contract builder requires product DIGITDIFF")
    if command.duration != 1 or command.duration_unit != "t":
        raise ValueError("DIGITDIFF contract duration must be exactly one tick")
    parameters = DigitDiffContractParameters(
        amount=Decimal(command.amount.minor_units) / Decimal(100),
        currency=command.amount.currency,
        symbol=command.symbol,
        prediction_digit=prediction_digit,
    )
    return parameters.to_buy_payload(command)
