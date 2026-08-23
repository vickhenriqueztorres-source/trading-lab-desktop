from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from packages.domain.models import Direction, require_aware_utc
from packages.strategies.models import ArbitrationKey, RuntimeContext


class ArbitrationReason(StrEnum):
    SINGLE_SIGNAL = "SINGLE_SIGNAL"
    CONSENSUS_NO_STAKE_SUM = "CONSENSUS_NO_STAKE_SUM"
    OPPOSING_SIGNALS_CANCELLED = "OPPOSING_SIGNALS_CANCELLED"
    ALL_SIGNALS_EXPIRED = "ALL_SIGNALS_EXPIRED"
    ALL_STRATEGIES_INELIGIBLE = "ALL_STRATEGIES_INELIGIBLE"


@dataclass(frozen=True, slots=True)
class ArbitratedSignal:
    arbitration_id: str
    correlation_id: str
    arbitration_key: ArbitrationKey
    primary_context: RuntimeContext
    direction: Direction
    valid_until: datetime
    source_signal_ids: tuple[str, ...]
    source_strategy_keys: tuple[tuple[str, str], ...]

    def __post_init__(self) -> None:
        require_aware_utc(self.valid_until, "valid_until")
        if not self.arbitration_id.strip() or not self.correlation_id.strip():
            raise ValueError("arbitrated signal identity cannot be empty")
        if not self.source_signal_ids or len(set(self.source_signal_ids)) != len(
            self.source_signal_ids
        ):
            raise ValueError("source signals must be non-empty and unique")
        if not self.source_strategy_keys or len(set(self.source_strategy_keys)) != len(
            self.source_strategy_keys
        ):
            raise ValueError("source strategies must be non-empty and unique")


@dataclass(frozen=True, slots=True)
class ArbitrationDecision:
    arbitration_key: ArbitrationKey
    reason: ArbitrationReason
    arbitrated_signal: ArbitratedSignal | None
    considered_signal_ids: tuple[str, ...]
    rejected_signal_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        success = self.reason in {
            ArbitrationReason.SINGLE_SIGNAL,
            ArbitrationReason.CONSENSUS_NO_STAKE_SUM,
        }
        if success != (self.arbitrated_signal is not None):
            raise ValueError("arbitration decision reason/signal mismatch")
