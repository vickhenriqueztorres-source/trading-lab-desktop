from __future__ import annotations

import hashlib
from collections import defaultdict, deque
from datetime import datetime

from packages.signal_arbitration.models import (
    ArbitratedSignal,
    ArbitrationDecision,
    ArbitrationReason,
    RankedArbitrationDecision,
    RankedRejectionReason,
    RankedSignalCandidate,
    RankedSignalRejection,
)
from packages.strategies.models import ArbitrationKey, StrategySignal
from packages.strategy_catalog.catalog import StrategyCatalog


def canonicalize_symbol(symbol: str) -> str:
    clean = symbol.strip().upper()
    if clean.startswith("FRX"):
        return clean[3:]
    if clean.startswith("OTC_"):
        return clean[4:]
    return clean


class SignalArbiter:
    def __init__(self, catalog: StrategyCatalog | None, *, audit_capacity: int = 1024) -> None:
        if audit_capacity <= 0:
            raise ValueError("audit_capacity must be positive")
        self._catalog = catalog
        self._audit: deque[ArbitrationDecision] = deque(maxlen=audit_capacity)
        self._ranked_audit: deque[RankedArbitrationDecision] = deque(maxlen=audit_capacity)

    @property
    def audit(self) -> tuple[ArbitrationDecision, ...]:
        return tuple(self._audit)

    @property
    def ranked_audit(self) -> tuple[RankedArbitrationDecision, ...]:
        return tuple(self._ranked_audit)

    def arbitrate_ranked(
        self, candidates: tuple[RankedSignalCandidate, ...]
    ) -> RankedArbitrationDecision:
        """Select one digit candidate deterministically without multiplying exposure."""

        unique = {item.signal_id: item for item in candidates}
        ordered = tuple(
            sorted(
                unique.values(),
                key=lambda item: (
                    -item.conservative_margin,
                    -item.conditional_sample,
                    item.strategy_id,
                    item.symbol,
                    item.signal_id,
                ),
            )
        )
        if not ordered:
            decision = RankedArbitrationDecision((), None, ())
            self._ranked_audit.append(decision)
            return decision
        winner = ordered[0]
        rejected: list[RankedSignalRejection] = []
        for item in ordered[1:]:
            if item.conservative_margin < winner.conservative_margin:
                reason = RankedRejectionReason.LOST_TO_HIGHER_MARGIN
            elif item.conditional_sample < winner.conditional_sample:
                reason = RankedRejectionReason.LOST_TO_LARGER_SAMPLE
            else:
                reason = RankedRejectionReason.LOST_TO_STABLE_STRATEGY_ID
            rejected.append(RankedSignalRejection(item.signal_id, reason))
        decision = RankedArbitrationDecision(
            tuple(item.signal_id for item in ordered),
            winner.signal_id,
            tuple(rejected),
        )
        self._ranked_audit.append(decision)
        return decision

    def arbitrate_all(
        self,
        signals: tuple[StrategySignal, ...],
        *,
        now: datetime,
    ) -> tuple[ArbitrationDecision, ...]:
        if self._catalog is None:
            raise RuntimeError("catalog is required for governed signal arbitration")
        groups: dict[ArbitrationKey, list[StrategySignal]] = defaultdict(list)
        seen: set[str] = set()
        for signal in signals:
            if signal.signal_id in seen:
                continue
            seen.add(signal.signal_id)
            groups[signal.context.arbitration_key].append(signal)
        decisions = tuple(
            self._arbitrate_group(key, tuple(group), now)
            for key, group in sorted(groups.items(), key=lambda item: str(item[0]))
        )
        self._audit.extend(decisions)
        return decisions

    def arbitrate_cross_broker(
        self,
        signals: tuple[StrategySignal, ...],
        *,
        now: datetime,
    ) -> tuple[ArbitrationDecision, ...]:
        """Cross-broker arbitration grouping by canonical symbol and timeframe."""
        if self._catalog is None:
            raise RuntimeError("catalog is required for governed signal arbitration")
        groups: dict[tuple[str, int], list[StrategySignal]] = defaultdict(list)
        seen: set[str] = set()
        for signal in signals:
            if signal.signal_id in seen:
                continue
            seen.add(signal.signal_id)
            canonical = canonicalize_symbol(signal.context.symbol)
            groups[(canonical, signal.context.timeframe_seconds)].append(signal)

        decisions: list[ArbitrationDecision] = []
        for (_canonical, _tf), group in sorted(
            groups.items(), key=lambda item: (item[0][0], item[0][1])
        ):
            considered = tuple(sorted(s.signal_id for s in group))
            eligible = tuple(
                s
                for s in group
                if s.valid_until > now
                and self._catalog.is_signal_eligible(
                    s.context.strategy_id, s.context.strategy_version
                )
            )
            rejected = tuple(sorted(set(considered) - {s.signal_id for s in eligible}))
            if not eligible:
                has_unexpired = any(s.valid_until > now for s in group)
                reason = (
                    ArbitrationReason.ALL_STRATEGIES_INELIGIBLE
                    if has_unexpired
                    else ArbitrationReason.ALL_SIGNALS_EXPIRED
                )
                arbitration_key = group[0].context.arbitration_key
                decisions.append(
                    ArbitrationDecision(arbitration_key, reason, None, considered, rejected)
                )
                continue

            directions = {s.direction for s in eligible}
            arbitration_key = eligible[0].context.arbitration_key
            if len(directions) > 1:
                decisions.append(
                    ArbitrationDecision(
                        arbitration_key,
                        ArbitrationReason.OPPOSING_SIGNALS_CANCELLED,
                        None,
                        considered,
                        rejected,
                    )
                )
                continue

            ordered = tuple(
                sorted(
                    eligible,
                    key=lambda s: (
                        s.context.strategy_id,
                        s.context.strategy_version,
                        s.context.broker.value,
                        s.signal_id,
                    ),
                )
            )
            source_ids = tuple(s.signal_id for s in ordered)
            source_strategies = tuple(
                dict.fromkeys((s.context.strategy_id, s.context.strategy_version) for s in ordered)
            )
            digest = hashlib.sha256("|".join(source_ids).encode("ascii")).hexdigest()
            arbitrated = ArbitratedSignal(
                arbitration_id=digest,
                correlation_id=f"ARBITER-XB-{digest}",
                arbitration_key=arbitration_key,
                primary_context=ordered[0].context,
                direction=ordered[0].direction,
                valid_until=min(s.valid_until for s in ordered),
                source_signal_ids=source_ids,
                source_strategy_keys=source_strategies,
            )
            reason = (
                ArbitrationReason.SINGLE_SIGNAL
                if len(ordered) == 1
                else ArbitrationReason.CONSENSUS_NO_STAKE_SUM
            )
            decisions.append(
                ArbitrationDecision(arbitration_key, reason, arbitrated, considered, rejected)
            )

        result = tuple(decisions)
        self._audit.extend(result)
        return result

    def _arbitrate_group(
        self,
        key: ArbitrationKey,
        signals: tuple[StrategySignal, ...],
        now: datetime,
    ) -> ArbitrationDecision:
        assert self._catalog is not None
        considered = tuple(sorted(signal.signal_id for signal in signals))
        eligible = tuple(
            signal
            for signal in signals
            if signal.valid_until > now
            and self._catalog.is_signal_eligible(
                signal.context.strategy_id, signal.context.strategy_version
            )
        )
        rejected = tuple(sorted(set(considered) - {signal.signal_id for signal in eligible}))
        if not eligible:
            has_unexpired = any(signal.valid_until > now for signal in signals)
            reason = (
                ArbitrationReason.ALL_STRATEGIES_INELIGIBLE
                if has_unexpired
                else ArbitrationReason.ALL_SIGNALS_EXPIRED
            )
            return ArbitrationDecision(key, reason, None, considered, rejected)
        directions = {signal.direction for signal in eligible}
        if len(directions) > 1:
            return ArbitrationDecision(
                key,
                ArbitrationReason.OPPOSING_SIGNALS_CANCELLED,
                None,
                considered,
                rejected,
            )
        ordered = tuple(
            sorted(
                eligible,
                key=lambda signal: (
                    signal.context.strategy_id,
                    signal.context.strategy_version,
                    signal.signal_id,
                ),
            )
        )
        source_ids = tuple(signal.signal_id for signal in ordered)
        source_strategies = tuple(
            dict.fromkeys(
                (signal.context.strategy_id, signal.context.strategy_version) for signal in ordered
            )
        )
        digest = hashlib.sha256("|".join(source_ids).encode("ascii")).hexdigest()
        arbitrated = ArbitratedSignal(
            arbitration_id=digest,
            correlation_id=f"ARBITER-{digest}",
            arbitration_key=key,
            primary_context=ordered[0].context,
            direction=ordered[0].direction,
            valid_until=min(signal.valid_until for signal in ordered),
            source_signal_ids=source_ids,
            source_strategy_keys=source_strategies,
        )
        reason = (
            ArbitrationReason.SINGLE_SIGNAL
            if len(ordered) == 1
            else ArbitrationReason.CONSENSUS_NO_STAKE_SUM
        )
        return ArbitrationDecision(key, reason, arbitrated, considered, rejected)
