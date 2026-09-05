"""Pure manifest routing. No broker, database, clock reads or financial authority."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Literal

from apps.core.families import is_within_trading_hours
from apps.core.iqoption_risk_config import IQOPTION_RSI_STRATEGY_ID
from apps.core.manifest_catalog import (
    DynamicManifestCatalog,
    StrategyCatalogEntry,
    ValidatedStats,
)
from packages.domain.models import Direction

TIMEFRAMES = {"M1": 60, "M5": 300, "M15": 900}
RSI_DEMO_LABEL = "RSI 30/70 (não validado · apenas Demo)"


@dataclass(frozen=True, slots=True)
class Candidate:
    key: str
    entry: StrategyCatalogEntry
    timeframe_seconds: int
    warmup_required: int


def local_rsi_entry(symbol: str) -> StrategyCatalogEntry:
    """Explicit local laboratory recipe; zero stats are NOT validation evidence."""
    zero = Decimal(0)
    return StrategyCatalogEntry(
        key=IQOPTION_RSI_STRATEGY_ID,
        family="local_rsi",
        display_name_pt=RSI_DEMO_LABEL,
        asset=symbol,
        timeframe="M1",
        hours_utc=(0, 24),
        params={},
        validated=ValidatedStats(zero, zero, zero, zero, zero, 0, zero, zero),
        status="demo_only",
        warmup_required=15,
    )


def next_open_utc(hours: tuple[int, int], now: datetime) -> datetime:
    now = now.astimezone(UTC)
    opening = now.replace(hour=hours[0], minute=0, second=0, microsecond=0)
    return opening if opening > now else opening + timedelta(days=1)


def resolve_candidates(
    *,
    catalog: DynamicManifestCatalog | None,
    symbol: str,
    mode: Literal["SINGLE", "AUTO"],
    active_strategy_key: str | None,
    account_type: str,
    now_utc: datetime,
) -> tuple[list[Candidate], dict[str, str]]:
    """Static eligibility only. Payout/risk/reconciliation are separate gates."""
    if now_utc.tzinfo is None or mode not in {"SINGLE", "AUTO"}:
        raise ValueError("CANDIDATE_CONTEXT_INVALID")
    demo = account_type.upper() in {"DEMO", "PRACTICE"}
    if account_type.upper() not in {"DEMO", "PRACTICE", "REAL", "LIVE"}:
        return [], {active_strategy_key or "": "ACCOUNT_UNCONFIRMED"}
    if mode == "SINGLE" and active_strategy_key == IQOPTION_RSI_STRATEGY_ID:
        if not demo:
            return [], {IQOPTION_RSI_STRATEGY_ID: "DEMO_ONLY"}
        if not symbol or symbol == "AUTO":
            return [], {IQOPTION_RSI_STRATEGY_ID: "ASSET_MISMATCH"}
        entry = local_rsi_entry(symbol)
        return [Candidate(entry.key, entry, 60, 15)], {}

    active = {} if catalog is None else catalog.active_strategies
    keys = [active_strategy_key or ""] if mode == "SINGLE" else sorted(active)
    candidates: list[Candidate] = []
    rejected: dict[str, str] = {}
    for key in keys:
        info = active.get(key)
        if info is None:
            rejected[key] = "NO_CANDIDATE"
            continue
        entry = info.entry
        if entry.asset != symbol:
            rejected[key] = "ASSET_MISMATCH"
        elif info.status not in {"approved", "observation"}:
            rejected[key] = "STATUS_NOT_ELIGIBLE"
        elif info.status == "observation" and not demo:
            rejected[key] = "OBSERVATION_ONLY_DEMO"
        elif not is_within_trading_hours(entry.hours_utc, now_utc):
            rejected[key] = "OUTSIDE_HOURS"
        elif entry.timeframe not in TIMEFRAMES:
            rejected[key] = "TIMEFRAME_UNSUPPORTED"
        else:
            candidates.append(
                Candidate(key, entry, TIMEFRAMES[entry.timeframe], info.instance.warmup_required)
            )
    return candidates, rejected


def candidate_priority(candidate: Candidate) -> tuple[Decimal, str]:
    stats = candidate.entry.validated
    return (-(stats.wilson_lower - stats.p_min_at_validation), candidate.key)


@dataclass(frozen=True, slots=True)
class CandidateSignal:
    candidate: Candidate
    direction: Direction
    rsi: Decimal
    epoch: int


def arbitrate(signals: list[CandidateSignal]) -> CandidateSignal | None:
    """Cancel opposite signals in the same context; rank remaining edges, then keys."""
    directions: dict[tuple[str, int], set[Direction]] = {}
    for signal in signals:
        context = (signal.candidate.entry.asset, signal.candidate.timeframe_seconds)
        directions.setdefault(context, set()).add(signal.direction)
    eligible = [
        signal
        for signal in signals
        if len(directions[(signal.candidate.entry.asset, signal.candidate.timeframe_seconds)]) == 1
    ]
    return min(eligible, key=lambda s: candidate_priority(s.candidate)) if eligible else None
