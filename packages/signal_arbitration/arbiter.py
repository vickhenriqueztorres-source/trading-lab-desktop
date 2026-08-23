from __future__ import annotations

import hashlib
from collections import defaultdict, deque
from datetime import datetime

from packages.signal_arbitration.models import (
    ArbitratedSignal,
    ArbitrationDecision,
    ArbitrationReason,
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
    def __init__(self, catalog: StrategyCatalog, *, audit_capacity: int = 1024) -> None:
        if audit_capacity <= 0:
            raise ValueError("audit_capacity must be positive")
        self._catalog = catalog
        self._audit: deque[ArbitrationDecision] = deque(maxlen=audit_capacity)

    @property
    def audit(self) -> tuple[ArbitrationDecision, ...]:
        return tuple(self._audit)

    def arbitrate_all(
        self,
        signals: tuple[StrategySignal, ...],
        *,
        now: datetime,
    ) -> tuple[ArbitrationDecision, ...]:
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
