from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import StrEnum

from packages.audit import DecisionRecord
from packages.domain.canonical import canonical_bytes
from packages.domain.models import Broker, Money
from packages.market_data import ClosedCandle
from packages.strategies import RuntimeContext


def configuration_hash_for(
    configuration_version: str,
    parameters: tuple[tuple[str, str], ...],
) -> str:
    return hashlib.sha256(
        canonical_bytes(
            {"configuration_version": configuration_version, "parameters": list(parameters)}
        )
    ).hexdigest()


@dataclass(frozen=True, slots=True)
class ReplayRequest:
    strategy_id: str
    strategy_version: str
    broker: Broker
    account_id: str
    product: str
    symbol: str
    timeframe_seconds: int
    configuration_version: str
    parameters: tuple[tuple[str, str], ...]
    configuration_hash: str
    manifest_hash: str
    entitled_packs: frozenset[str]
    requested_amount: Money
    strategy_remaining: Money
    account_remaining: Money
    global_remaining: Money
    candles: tuple[ClosedCandle, ...]

    def __post_init__(self) -> None:
        for field_name in (
            "strategy_id",
            "strategy_version",
            "account_id",
            "product",
            "symbol",
            "configuration_version",
        ):
            if not getattr(self, field_name).strip():
                raise ValueError(f"{field_name} cannot be empty")
        if self.timeframe_seconds <= 0 or not self.candles:
            raise ValueError("replay requires a timeframe and at least one candle")
        for digest_name in ("configuration_hash", "manifest_hash"):
            digest = getattr(self, digest_name)
            if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
                raise ValueError(f"{digest_name} must be a lowercase SHA-256 digest")
        expected_configuration = configuration_hash_for(self.configuration_version, self.parameters)
        if self.configuration_hash != expected_configuration:
            raise ValueError("configuration hash does not match replay parameters")
        if not self.entitled_packs:
            raise ValueError("replay entitlement set cannot be empty")
        parameter_names = tuple(name for name, _ in self.parameters)
        if any(not name.strip() for name in parameter_names) or len(set(parameter_names)) != len(
            parameter_names
        ):
            raise ValueError("replay parameters must have unique non-empty names")
        currencies = {
            self.requested_amount.currency,
            self.strategy_remaining.currency,
            self.account_remaining.currency,
            self.global_remaining.currency,
        }
        if len(currencies) != 1:
            raise ValueError("replay budgets must use one currency")
        if self.requested_amount.minor_units <= 0 or any(
            amount.minor_units < 0
            for amount in (
                self.strategy_remaining,
                self.account_remaining,
                self.global_remaining,
            )
        ):
            raise ValueError("replay budgets are outside the valid range")
        for candle in self.candles:
            if (
                candle.broker is not self.broker
                or candle.symbol != self.symbol
                or candle.timeframe_seconds != self.timeframe_seconds
            ):
                raise ValueError("replay candle series does not match request context")

    @property
    def context(self) -> RuntimeContext:
        return RuntimeContext(
            strategy_id=self.strategy_id,
            strategy_version=self.strategy_version,
            broker=self.broker,
            account_id=self.account_id,
            product=self.product,
            symbol=self.symbol,
            timeframe_seconds=self.timeframe_seconds,
            configuration_version=self.configuration_version,
            parameters=self.parameters,
        )

    @property
    def run_id(self) -> str:
        payload = {
            "account_id": self.account_id,
            "broker": self.broker.value,
            "candle_ids": sorted(candle.candle_id for candle in self.candles),
            "configuration_hash": self.configuration_hash,
            "currency": self.requested_amount.currency,
            "entitled_packs": sorted(self.entitled_packs),
            "global_remaining_minor": self.global_remaining.minor_units,
            "manifest_hash": self.manifest_hash,
            "product": self.product,
            "requested_minor": self.requested_amount.minor_units,
            "strategy_remaining_minor": self.strategy_remaining.minor_units,
            "strategy_id": self.strategy_id,
            "strategy_version": self.strategy_version,
            "symbol": self.symbol,
            "timeframe_seconds": self.timeframe_seconds,
            "account_remaining_minor": self.account_remaining.minor_units,
        }
        return hashlib.sha256(canonical_bytes(payload)).hexdigest()


@dataclass(frozen=True, slots=True)
class ReplayRiskDecision:
    correlation_id: str
    amount: Money
    intent_id: str
    reservation_id: str
    order_id: str


@dataclass(frozen=True, slots=True)
class ReplayResult:
    run_id: str
    signal_ids: tuple[str, ...]
    arbitration_reasons: tuple[str, ...]
    allocation_reasons: tuple[str, ...]
    risk_decisions: tuple[ReplayRiskDecision, ...]
    journal: tuple[DecisionRecord, ...]
    final_hash: str

    @property
    def result_sha256(self) -> str:
        payload = {
            "allocation_reasons": list(self.allocation_reasons),
            "arbitration_reasons": list(self.arbitration_reasons),
            "final_hash": self.final_hash,
            "risk_decisions": [
                {
                    "amount_minor": item.amount.minor_units,
                    "correlation_id": item.correlation_id,
                    "currency": item.amount.currency,
                    "intent_id": item.intent_id,
                    "order_id": item.order_id,
                    "reservation_id": item.reservation_id,
                }
                for item in self.risk_decisions
            ],
            "run_id": self.run_id,
            "signal_ids": list(self.signal_ids),
        }
        return hashlib.sha256(canonical_bytes(payload)).hexdigest()


class ReplayStatus(StrEnum):
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


@dataclass(frozen=True, slots=True)
class ReplayRecord:
    run_id: str
    strategy_id: str
    strategy_version: str
    manifest_sha256: str
    config_sha256: str
    first_candle_id: str
    last_candle_id: str
    candle_count: int
    final_journal_sha256: str
    result_sha256: str
    status: ReplayStatus
    completed_at_ms: int

    @classmethod
    def completed(cls, request: ReplayRequest, result: ReplayResult) -> ReplayRecord:
        ordered = tuple(
            sorted(request.candles, key=lambda item: (item.close_time_ms, item.candle_id))
        )
        return cls(
            run_id=result.run_id,
            strategy_id=request.strategy_id,
            strategy_version=request.strategy_version,
            manifest_sha256=request.manifest_hash,
            config_sha256=request.configuration_hash,
            first_candle_id=ordered[0].candle_id,
            last_candle_id=ordered[-1].candle_id,
            candle_count=len({candle.candle_id for candle in ordered}),
            final_journal_sha256=result.final_hash,
            result_sha256=result.result_sha256,
            status=ReplayStatus.COMPLETED,
            completed_at_ms=ordered[-1].close_time_ms,
        )
