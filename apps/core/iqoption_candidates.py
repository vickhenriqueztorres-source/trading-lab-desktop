"""Pure manifest routing. No broker, database, clock reads or financial authority."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Literal

from apps.core.families import is_within_trading_hours
from apps.core.iqoption_risk_config import (
    IQOPTION_BODY_GAP_FILL_STRATEGY_ID,
    IQOPTION_EXTREME_REJECTION_STRATEGY_ID,
    IQOPTION_HACK_CHINO_STRATEGY_ID,
    IQOPTION_HOUR_OF_DAY_STRATEGY_ID,
    IQOPTION_LIQUIDITY_GAP_STRATEGY_ID,
    IQOPTION_MICROTREND_SCALPER_STRATEGY_ID,
    IQOPTION_PATTERN_REVERSAL_STRATEGY_ID,
    IQOPTION_RSI_STRATEGY_ID,
)
from apps.core.manifest_catalog import (
    DynamicManifestCatalog,
    StrategyCatalogEntry,
    ValidatedStats,
)
from packages.domain.models import Direction

TIMEFRAMES = {"M1": 60, "M5": 300, "M15": 900}
HACK_CHINO_LABEL = "Hack Chino · 5 Modelos Probabilísticos (M1 · Exp. 1m)"
RSI_DEMO_LABEL = "RSI 30/70 (não validado · apenas Demo)"
LIQUIDITY_GAP_LABEL = "Liquidity Gap · Varredura de Extremo (M1 · Exp. 2m)"
PATTERN_REVERSAL_LABEL = "Pattern Reversal · Engolfo de 2 Velas (M1 · Exp. 1m)"
EXTREME_REJECTION_LABEL = "Varredura e Rejeição de Extremo (M1 · Exp. 1m)"
MICROTREND_SCALPER_LABEL = "Microtendência 3 Velas + RSI(5) (M1 · Exp. 1m)"
HOUR_OF_DAY_LABEL = "Hour of Day · Probabilidade Horária (M1 · Exp. 1m)"
BODY_GAP_FILL_LABEL = "Body Gap Fill · Separação ATR (M1 · Exp. 1m)"


@dataclass(frozen=True, slots=True)
class Candidate:
    key: str
    entry: StrategyCatalogEntry
    timeframe_seconds: int
    warmup_required: int


def local_hack_chino_entry(symbol: str) -> StrategyCatalogEntry:
    """Explicit Hack Chino probabilistic ensemble candidate recipe."""
    zero = Decimal(0)
    return StrategyCatalogEntry(
        key=IQOPTION_HACK_CHINO_STRATEGY_ID,
        family="local_hack_chino",
        display_name_pt=HACK_CHINO_LABEL,
        asset=symbol,
        timeframe="M1",
        hours_utc=(0, 24),
        params={},
        validated=ValidatedStats(zero, zero, zero, zero, zero, 0, zero, zero),
        status="approved",
        warmup_required=45,
    )


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


def local_liquidity_gap_entry(symbol: str) -> StrategyCatalogEntry:
    """Explicit Liquidity Gap extreme sweep candidate recipe."""
    zero = Decimal(0)
    return StrategyCatalogEntry(
        key=IQOPTION_LIQUIDITY_GAP_STRATEGY_ID,
        family="local_liquidity_gap",
        display_name_pt=LIQUIDITY_GAP_LABEL,
        asset=symbol,
        timeframe="M1",
        hours_utc=(0, 24),
        params={},
        validated=ValidatedStats(zero, zero, zero, zero, zero, 0, zero, zero),
        status="demo_only",
        warmup_required=2,
    )


def local_pattern_reversal_entry(symbol: str) -> StrategyCatalogEntry:
    """Explicit Pattern Reversal engulfing candidate recipe."""
    zero = Decimal(0)
    return StrategyCatalogEntry(
        key=IQOPTION_PATTERN_REVERSAL_STRATEGY_ID,
        family="local_pattern_reversal",
        display_name_pt=PATTERN_REVERSAL_LABEL,
        asset=symbol,
        timeframe="M1",
        hours_utc=(0, 24),
        params={},
        validated=ValidatedStats(zero, zero, zero, zero, zero, 0, zero, zero),
        status="demo_only",
        warmup_required=2,
    )


def local_extreme_rejection_entry(symbol: str) -> StrategyCatalogEntry:
    """Explicit Extreme Rejection liquidity sweep candidate recipe."""
    zero = Decimal(0)
    return StrategyCatalogEntry(
        key=IQOPTION_EXTREME_REJECTION_STRATEGY_ID,
        family="local_extreme_rejection",
        display_name_pt=EXTREME_REJECTION_LABEL,
        asset=symbol,
        timeframe="M1",
        hours_utc=(0, 24),
        params={},
        validated=ValidatedStats(zero, zero, zero, zero, zero, 0, zero, zero),
        status="approved",
        warmup_required=35,
    )


def local_microtrend_scalper_entry(symbol: str) -> StrategyCatalogEntry:
    """Explicit Microtrend Scalper 3-candle + RSI(5) candidate recipe."""
    zero = Decimal(0)
    return StrategyCatalogEntry(
        key=IQOPTION_MICROTREND_SCALPER_STRATEGY_ID,
        family="local_microtrend_scalper",
        display_name_pt=MICROTREND_SCALPER_LABEL,
        asset=symbol,
        timeframe="M1",
        hours_utc=(0, 24),
        params={},
        validated=ValidatedStats(zero, zero, zero, zero, zero, 0, zero, zero),
        status="approved",
        warmup_required=35,
    )


def local_hour_of_day_entry(symbol: str) -> StrategyCatalogEntry:
    """Explicit Hour of Day statistical candidate recipe."""
    zero = Decimal(0)
    return StrategyCatalogEntry(
        key=IQOPTION_HOUR_OF_DAY_STRATEGY_ID,
        family="local_hour_of_day",
        display_name_pt=HOUR_OF_DAY_LABEL,
        asset=symbol,
        timeframe="M1",
        hours_utc=(0, 24),
        params={},
        validated=ValidatedStats(zero, zero, zero, zero, zero, 0, zero, zero),
        status="demo_only",
        warmup_required=60,
    )


def local_body_gap_fill_entry(symbol: str) -> StrategyCatalogEntry:
    """Explicit Body Gap Fill ATR separation candidate recipe."""
    zero = Decimal(0)
    return StrategyCatalogEntry(
        key=IQOPTION_BODY_GAP_FILL_STRATEGY_ID,
        family="local_body_gap_fill",
        display_name_pt=BODY_GAP_FILL_LABEL,
        asset=symbol,
        timeframe="M1",
        hours_utc=(0, 24),
        params={},
        validated=ValidatedStats(zero, zero, zero, zero, zero, 0, zero, zero),
        status="demo_only",
        warmup_required=16,
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

    local_generators = {
        IQOPTION_HACK_CHINO_STRATEGY_ID: (local_hack_chino_entry, 45),
        IQOPTION_RSI_STRATEGY_ID: (local_rsi_entry, 15),
        IQOPTION_LIQUIDITY_GAP_STRATEGY_ID: (local_liquidity_gap_entry, 2),
        IQOPTION_PATTERN_REVERSAL_STRATEGY_ID: (local_pattern_reversal_entry, 2),
        IQOPTION_EXTREME_REJECTION_STRATEGY_ID: (local_extreme_rejection_entry, 35),
        IQOPTION_MICROTREND_SCALPER_STRATEGY_ID: (local_microtrend_scalper_entry, 35),
        IQOPTION_HOUR_OF_DAY_STRATEGY_ID: (local_hour_of_day_entry, 60),
        IQOPTION_BODY_GAP_FILL_STRATEGY_ID: (local_body_gap_fill_entry, 16),
    }
    if mode == "SINGLE" and active_strategy_key in local_generators:
        gen_fn, warmup = local_generators[active_strategy_key]
        entry = gen_fn(symbol)
        if not demo and entry.status == "demo_only":
            return [], {active_strategy_key: "DEMO_ONLY"}
        if not symbol or symbol == "AUTO":
            return [], {active_strategy_key: "ASSET_MISMATCH"}
        return [Candidate(entry.key, entry, 60, warmup)], {}

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
