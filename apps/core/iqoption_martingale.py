from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any
from uuid import uuid4

from apps.core.iqoption_risk_config import IqOptionRiskConfig
from packages.domain.market import MarketCandle
from packages.domain.models import Broker, Direction
from packages.portfolio_allocation.martingale import (
    BoundedMartingaleAllocator,
    BoundedMartingaleState,
)

IQOPTION_MARTINGALE_ENTRY_WINDOW_SECONDS = 20
IQOPTION_MARTINGALE_RULE_VERSION = "candle-color-v1"


class IqOptionCandleOutcome(StrEnum):
    PENDING = "PENDING"
    WIN = "WIN"
    LOSS = "LOSS"
    TIE = "TIE"


def next_binary_expiry(value: datetime) -> datetime:
    """Return the next M1 expiry for an entry admitted before second 30."""

    value = value.astimezone(UTC)
    return value.replace(second=0, microsecond=0) + timedelta(minutes=1)


@dataclass(frozen=True, slots=True)
class IqOptionMartingaleCycle:
    """Durable IQ-only recovery cycle decided by one exact closed M1 candle."""

    cycle_id: str
    strategy_id: str
    symbol: str
    direction: Direction
    base_stake_minor_units: int
    multiplier_basis_points: int
    max_steps: int
    max_stake_minor_units: int
    currency: str
    step: int
    cumulative_loss_minor_units: int
    last_order_id: str
    last_stake_minor_units: int
    target_candle_close_utc: datetime
    technical_outcome: IqOptionCandleOutcome = IqOptionCandleOutcome.PENDING
    technical_evidence_id: str | None = None
    recovery_pending: bool = False
    recovery_not_before_utc: datetime | None = None
    recovery_deadline_utc: datetime | None = None

    def __post_init__(self) -> None:
        if not self.cycle_id or not self.strategy_id or not self.symbol or not self.last_order_id:
            raise ValueError("IQOPTION_MARTINGALE_STATE_INVALID")
        if self.currency != "USD" or self.base_stake_minor_units <= 0:
            raise ValueError("IQOPTION_MARTINGALE_STATE_INVALID")
        if not 11_000 <= self.multiplier_basis_points <= 30_000:
            raise ValueError("IQOPTION_MARTINGALE_STATE_INVALID")
        if self.max_steps not in {1, 2} or not 0 <= self.step <= self.max_steps:
            raise ValueError("IQOPTION_MARTINGALE_STATE_INVALID")
        if self.max_stake_minor_units < self.base_stake_minor_units:
            raise ValueError("IQOPTION_MARTINGALE_STATE_INVALID")
        if not 0 < self.last_stake_minor_units <= self.max_stake_minor_units:
            raise ValueError("IQOPTION_MARTINGALE_STATE_INVALID")
        if self.cumulative_loss_minor_units < 0:
            raise ValueError("IQOPTION_MARTINGALE_STATE_INVALID")
        self._require_utc(self.target_candle_close_utc)
        if (
            self.target_candle_close_utc.second != 0
            or self.target_candle_close_utc.microsecond != 0
        ):
            raise ValueError("IQOPTION_MARTINGALE_STATE_INVALID")
        if self.recovery_pending:
            if (
                self.technical_outcome is not IqOptionCandleOutcome.LOSS
                or self.technical_evidence_id is None
                or self.recovery_not_before_utc is None
                or self.recovery_deadline_utc is None
                or self.recovery_deadline_utc <= self.recovery_not_before_utc
            ):
                raise ValueError("IQOPTION_MARTINGALE_STATE_INVALID")
            self._require_utc(self.recovery_not_before_utc)
            self._require_utc(self.recovery_deadline_utc)
        elif any(
            value is not None
            for value in (self.recovery_not_before_utc, self.recovery_deadline_utc)
        ):
            raise ValueError("IQOPTION_MARTINGALE_STATE_INVALID")
        if self.technical_outcome is IqOptionCandleOutcome.PENDING:
            if self.technical_evidence_id is not None:
                raise ValueError("IQOPTION_MARTINGALE_STATE_INVALID")
        elif self.technical_evidence_id is None:
            raise ValueError("IQOPTION_MARTINGALE_STATE_INVALID")

    @staticmethod
    def _require_utc(value: datetime) -> None:
        offset = value.utcoffset()
        if value.tzinfo is None or offset is None or offset.total_seconds() != 0:
            raise ValueError("IQOPTION_MARTINGALE_STATE_INVALID")

    @classmethod
    def start(
        cls,
        *,
        config: IqOptionRiskConfig,
        strategy_id: str,
        symbol: str,
        direction: Direction,
        order_id: str,
        target_candle_close_utc: datetime,
    ) -> IqOptionMartingaleCycle:
        if not config.martingale_enabled:
            raise ValueError("IQOPTION_MARTINGALE_DISABLED")
        return cls(
            cycle_id=str(uuid4()),
            strategy_id=strategy_id,
            symbol=symbol,
            direction=direction,
            base_stake_minor_units=config.stake_minor_units,
            multiplier_basis_points=config.martingale_multiplier_basis_points,
            max_steps=config.martingale_max_steps,
            max_stake_minor_units=config.martingale_max_stake_minor_units,
            currency=config.currency,
            step=0,
            cumulative_loss_minor_units=0,
            last_order_id=order_id,
            last_stake_minor_units=config.stake_minor_units,
            target_candle_close_utc=target_candle_close_utc.astimezone(UTC),
        )

    def expected_stake_minor_units(self, config: IqOptionRiskConfig) -> int:
        if not self.recovery_pending or self.step <= 0:
            raise ValueError("IQOPTION_MARTINGALE_RECOVERY_NOT_PENDING")
        if not self.matches_policy(config):
            raise ValueError("IQOPTION_MARTINGALE_POLICY_CHANGED")
        return BoundedMartingaleAllocator.stake_for_step(
            config.martingale_config,
            BoundedMartingaleState(self.step),
        ).minor_units

    def matches_policy(self, config: IqOptionRiskConfig) -> bool:
        return (
            config.martingale_enabled
            and config.stake_minor_units == self.base_stake_minor_units
            and config.martingale_multiplier_basis_points == self.multiplier_basis_points
            and config.martingale_max_steps == self.max_steps
            and config.martingale_max_stake_minor_units == self.max_stake_minor_units
            and config.currency == self.currency
        )

    def bind_recovery_order(
        self,
        order_id: str,
        *,
        stake_minor_units: int,
        target_candle_close_utc: datetime,
    ) -> IqOptionMartingaleCycle:
        if not self.recovery_pending or self.step <= 0 or not order_id:
            raise ValueError("IQOPTION_MARTINGALE_RECOVERY_NOT_PENDING")
        return replace(
            self,
            last_order_id=order_id,
            last_stake_minor_units=stake_minor_units,
            target_candle_close_utc=target_candle_close_utc.astimezone(UTC),
            technical_outcome=IqOptionCandleOutcome.PENDING,
            technical_evidence_id=None,
            recovery_pending=False,
            recovery_not_before_utc=None,
            recovery_deadline_utc=None,
        )

    @staticmethod
    def outcome_for_candle(direction: Direction, candle: MarketCandle) -> IqOptionCandleOutcome:
        if candle.close == candle.open:
            return IqOptionCandleOutcome.TIE
        moved_up = candle.close > candle.open
        wins = moved_up if direction is Direction.CALL else not moved_up
        return IqOptionCandleOutcome.WIN if wins else IqOptionCandleOutcome.LOSS

    def evidence_id_for_candle(self, candle: MarketCandle) -> str:
        material = {
            "rule": IQOPTION_MARTINGALE_RULE_VERSION,
            "cycle_id": self.cycle_id,
            "order_id": self.last_order_id,
            "step": self.step,
            "symbol": self.symbol,
            "direction": self.direction.value,
            "timeframe_seconds": candle.timeframe_seconds,
            "open_time": candle.open_time.isoformat(),
            "close_time": candle.close_time.isoformat(),
            "open": str(candle.open),
            "close": str(candle.close),
        }
        encoded = json.dumps(material, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(encoded).hexdigest()

    def after_candle_close(self, candle: MarketCandle) -> IqOptionMartingaleCycle | None:
        if (
            candle.broker is not Broker.IQ_OPTION
            or candle.broker_symbol != self.symbol
            or candle.timeframe_seconds != 60
            or not candle.is_closed
            or candle.close_time != self.target_candle_close_utc
        ):
            raise ValueError("IQOPTION_MARTINGALE_TARGET_CANDLE_INVALID")
        if self.technical_outcome is not IqOptionCandleOutcome.PENDING:
            return self
        outcome = self.outcome_for_candle(self.direction, candle)
        evidence_id = self.evidence_id_for_candle(candle)
        if outcome is not IqOptionCandleOutcome.LOSS or self.step >= self.max_steps:
            return None
        return replace(
            self,
            step=self.step + 1,
            cumulative_loss_minor_units=(
                self.cumulative_loss_minor_units + self.last_stake_minor_units
            ),
            technical_outcome=outcome,
            technical_evidence_id=evidence_id,
            recovery_pending=True,
            recovery_not_before_utc=self.target_candle_close_utc,
            recovery_deadline_utc=self.target_candle_close_utc
            + timedelta(seconds=IQOPTION_MARTINGALE_ENTRY_WINDOW_SECONDS),
        )

    def to_payload(self) -> dict[str, object]:
        return {
            "payload_version": 2,
            "cycle_id": self.cycle_id,
            "strategy_id": self.strategy_id,
            "symbol": self.symbol,
            "direction": self.direction.value,
            "base_stake_minor_units": self.base_stake_minor_units,
            "multiplier_basis_points": self.multiplier_basis_points,
            "max_steps": self.max_steps,
            "max_stake_minor_units": self.max_stake_minor_units,
            "currency": self.currency,
            "step": self.step,
            "cumulative_loss_minor_units": self.cumulative_loss_minor_units,
            "last_order_id": self.last_order_id,
            "last_stake_minor_units": self.last_stake_minor_units,
            "target_candle_close_utc": self.target_candle_close_utc.isoformat(),
            "technical_outcome": self.technical_outcome.value,
            "technical_evidence_id": self.technical_evidence_id,
            "recovery_pending": self.recovery_pending,
            "recovery_not_before_utc": (
                None
                if self.recovery_not_before_utc is None
                else self.recovery_not_before_utc.isoformat()
            ),
            "recovery_deadline_utc": (
                None
                if self.recovery_deadline_utc is None
                else self.recovery_deadline_utc.isoformat()
            ),
        }

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> IqOptionMartingaleCycle:
        expected = {
            "payload_version",
            "cycle_id",
            "strategy_id",
            "symbol",
            "direction",
            "base_stake_minor_units",
            "multiplier_basis_points",
            "max_steps",
            "max_stake_minor_units",
            "currency",
            "step",
            "cumulative_loss_minor_units",
            "last_order_id",
            "last_stake_minor_units",
            "target_candle_close_utc",
            "technical_outcome",
            "technical_evidence_id",
            "recovery_pending",
            "recovery_not_before_utc",
            "recovery_deadline_utc",
        }
        if set(payload) != expected or payload.get("payload_version") != 2:
            raise ValueError("IQOPTION_MARTINGALE_STATE_INVALID")
        integer_fields = {
            "payload_version",
            "base_stake_minor_units",
            "multiplier_basis_points",
            "max_steps",
            "max_stake_minor_units",
            "step",
            "cumulative_loss_minor_units",
            "last_stake_minor_units",
        }
        if any(type(payload.get(field)) is not int for field in integer_fields):
            raise ValueError("IQOPTION_MARTINGALE_STATE_INVALID")
        if type(payload.get("recovery_pending")) is not bool:
            raise ValueError("IQOPTION_MARTINGALE_STATE_INVALID")
        required_text = {
            "cycle_id",
            "strategy_id",
            "symbol",
            "direction",
            "currency",
            "last_order_id",
            "target_candle_close_utc",
            "technical_outcome",
        }
        if any(not isinstance(payload.get(field), str) for field in required_text):
            raise ValueError("IQOPTION_MARTINGALE_STATE_INVALID")
        evidence_id = payload.get("technical_evidence_id")
        if evidence_id is not None and not isinstance(evidence_id, str):
            raise ValueError("IQOPTION_MARTINGALE_STATE_INVALID")

        def optional_datetime(field: str) -> datetime | None:
            value = payload.get(field)
            if value is None:
                return None
            if not isinstance(value, str):
                raise ValueError("IQOPTION_MARTINGALE_STATE_INVALID")
            return datetime.fromisoformat(value).astimezone(UTC)

        return cls(
            cycle_id=str(payload["cycle_id"]),
            strategy_id=str(payload["strategy_id"]),
            symbol=str(payload["symbol"]),
            direction=Direction(str(payload["direction"])),
            base_stake_minor_units=int(payload["base_stake_minor_units"]),
            multiplier_basis_points=int(payload["multiplier_basis_points"]),
            max_steps=int(payload["max_steps"]),
            max_stake_minor_units=int(payload["max_stake_minor_units"]),
            currency=str(payload["currency"]),
            step=int(payload["step"]),
            cumulative_loss_minor_units=int(payload["cumulative_loss_minor_units"]),
            last_order_id=str(payload["last_order_id"]),
            last_stake_minor_units=int(payload["last_stake_minor_units"]),
            target_candle_close_utc=datetime.fromisoformat(
                str(payload["target_candle_close_utc"])
            ).astimezone(UTC),
            technical_outcome=IqOptionCandleOutcome(str(payload["technical_outcome"])),
            technical_evidence_id=evidence_id,
            recovery_pending=bool(payload["recovery_pending"]),
            recovery_not_before_utc=optional_datetime("recovery_not_before_utc"),
            recovery_deadline_utc=optional_datetime("recovery_deadline_utc"),
        )


__all__ = [
    "IQOPTION_MARTINGALE_ENTRY_WINDOW_SECONDS",
    "IQOPTION_MARTINGALE_RULE_VERSION",
    "IqOptionCandleOutcome",
    "IqOptionMartingaleCycle",
    "next_binary_expiry",
]
