from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from packages.domain.models import Direction, require_aware_utc
from packages.strategies.models import ArbitrationKey, RuntimeContext


class ArbitrationReason(StrEnum):
    SINGLE_SIGNAL = "SINGLE_SIGNAL"
    CONSENSUS_NO_STAKE_SUM = "CONSENSUS_NO_STAKE_SUM"
    OPPOSING_SIGNALS_CANCELLED = "OPPOSING_SIGNALS_CANCELLED"
    ALL_SIGNALS_EXPIRED = "ALL_SIGNALS_EXPIRED"
    ALL_STRATEGIES_INELIGIBLE = "ALL_STRATEGIES_INELIGIBLE"
    RANKED_HIGHER_MARGIN = "RANKED_HIGHER_MARGIN"


class RankedRejectionReason(StrEnum):
    LOST_TO_HIGHER_MARGIN = "ARBITRATION_LOST_TO_HIGHER_MARGIN"
    LOST_TO_LARGER_SAMPLE = "ARBITRATION_LOST_TO_LARGER_SAMPLE"
    LOST_TO_STABLE_STRATEGY_ID = "ARBITRATION_LOST_TO_STABLE_STRATEGY_ID"


@dataclass(frozen=True, slots=True)
class RankedSignalCandidate:
    signal_id: str
    strategy_id: str
    symbol: str
    conservative_margin: Decimal
    conditional_sample: int

    def __post_init__(self) -> None:
        if not self.signal_id.strip() or not self.strategy_id.strip() or not self.symbol.strip():
            raise ValueError("ranked signal identity cannot be empty")
        if not self.conservative_margin.is_finite() or self.conditional_sample < 0:
            raise ValueError("ranked signal evidence is invalid")


@dataclass(frozen=True, slots=True)
class RankedSignalRejection:
    signal_id: str
    reason: RankedRejectionReason


@dataclass(frozen=True, slots=True)
class RankedArbitrationDecision:
    considered_signal_ids: tuple[str, ...]
    winner_signal_id: str | None
    rejected: tuple[RankedSignalRejection, ...]


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
