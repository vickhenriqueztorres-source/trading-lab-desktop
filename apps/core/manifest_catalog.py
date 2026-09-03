"""Dynamic strategy catalog populated by signed manifests (R-BOT-5, R-BOT-8, R-BOT-9)."""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from apps.core.families import FAMILY_CLASSES, FamilyStrategyBase, is_within_trading_hours
from apps.core.payout_gate import PayoutGate, PayoutGateResult

logger = logging.getLogger("core.manifest_catalog")


@dataclass(frozen=True, slots=True)
class ValidatedStats:
    p_hat: Decimal
    wilson_lower: Decimal
    p_min_at_validation: Decimal
    payout_min: Decimal
    ops_per_day: Decimal
    worst_streak: int
    result_1000_ops_stake10: Decimal
    score: Decimal


@dataclass(frozen=True, slots=True)
class StrategyCatalogEntry:
    key: str
    family: str
    display_name_pt: str
    asset: str
    timeframe: str
    hours_utc: tuple[int, int]
    params: dict[str, Any]
    validated: ValidatedStats
    status: str  # "approved" | "observation" | "rejected"
    reason_pt: str = ""


def parse_strategy_entry(raw: dict[str, Any] | Any) -> StrategyCatalogEntry:
    """Parse raw dict or object into typed StrategyCatalogEntry."""
    if isinstance(raw, StrategyCatalogEntry):
        return raw

    if isinstance(raw, dict):
        val = raw.get("validated", {})
        val_stats = ValidatedStats(
            p_hat=Decimal(str(val.get("p_hat", "0"))),
            wilson_lower=Decimal(str(val.get("wilson_lower", "0"))),
            p_min_at_validation=Decimal(str(val.get("p_min_at_validation", "0"))),
            payout_min=Decimal(str(val.get("payout_min", "0"))),
            ops_per_day=Decimal(str(val.get("ops_per_day", "0"))),
            worst_streak=int(val.get("worst_streak", 0)),
            result_1000_ops_stake10=Decimal(str(val.get("result_1000_ops_stake10", "0"))),
            score=Decimal(str(val.get("score", "0"))),
        )
        h = raw.get("hours_utc", ())
        hours_tuple = tuple(int(x) for x in h) if len(h) >= 2 else (0, 24)
        return StrategyCatalogEntry(
            key=str(raw.get("key", "")),
            family=str(raw.get("family", "")),
            display_name_pt=str(raw.get("display_name_pt", "")),
            asset=str(raw.get("asset", "")),
            timeframe=str(raw.get("timeframe", "M1")),
            hours_utc=(hours_tuple[0], hours_tuple[1]),
            params=dict(raw.get("params", {})),
            validated=val_stats,
            status=str(raw.get("status", "observation")),
            reason_pt=str(raw.get("reason_pt", "")),
        )

    # Object with attributes
    val = raw.validated
    val_stats = ValidatedStats(
        p_hat=Decimal(str(val.p_hat)),
        wilson_lower=Decimal(str(val.wilson_lower)),
        p_min_at_validation=Decimal(str(val.p_min_at_validation)),
        payout_min=Decimal(str(val.payout_min)),
        ops_per_day=Decimal(str(val.ops_per_day)),
        worst_streak=int(val.worst_streak),
        result_1000_ops_stake10=Decimal(str(val.result_1000_ops_stake10)),
        score=Decimal(str(val.score)),
    )
    h = getattr(raw, "hours_utc", ())
    hours_tuple = tuple(int(x) for x in h) if len(h) >= 2 else (0, 24)
    return StrategyCatalogEntry(
        key=str(raw.key),
        family=str(raw.family),
        display_name_pt=str(raw.display_name_pt),
        asset=str(raw.asset),
        timeframe=str(raw.timeframe),
        hours_utc=(hours_tuple[0], hours_tuple[1]),
        params=dict(raw.params),
        validated=val_stats,
        status=str(raw.status),
        reason_pt=str(getattr(raw, "reason_pt", "")),
    )


@dataclass
class CatalogStrategyInfo:
    entry: StrategyCatalogEntry
    instance: FamilyStrategyBase
    status: str  # "approved" | "observation" | "retiring"
    added_at: datetime


class DynamicManifestCatalog:
    """Manages strategy instances loaded dynamically from applied manifests."""

    def __init__(
        self,
        utc_clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._utc_clock = utc_clock
        self._active_strategies: dict[str, CatalogStrategyInfo] = {}
        self._retiring_strategies: dict[str, CatalogStrategyInfo] = {}
        self._in_flight_orders: dict[str, set[str]] = {}
        self._manifest_version: int | None = None

    @property
    def manifest_version(self) -> int | None:
        return self._manifest_version

    @property
    def active_strategies(self) -> dict[str, CatalogStrategyInfo]:
        return dict(self._active_strategies)

    @property
    def retiring_strategies(self) -> dict[str, CatalogStrategyInfo]:
        return dict(self._retiring_strategies)

    def apply_manifest(self, manifest: Any) -> None:
        """Dynamically instantiate strategies from the applied manifest."""
        now = self._utc_clock()
        if hasattr(manifest, "manifest_version"):
            self._manifest_version = manifest.manifest_version
        elif hasattr(manifest, "version"):
            self._manifest_version = manifest.version
        elif isinstance(manifest, dict):
            self._manifest_version = manifest.get("manifest_version", manifest.get("version"))

        raw_strategies: Sequence[Any]
        if hasattr(manifest, "strategies"):
            raw_strategies = manifest.strategies
        elif isinstance(manifest, dict):
            raw_strategies = manifest.get("strategies", ())
        else:
            raw_strategies = ()

        incoming_keys = set()
        for raw in raw_strategies:
            entry = parse_strategy_entry(raw)
            incoming_keys.add(entry.key)
            if entry.status == "rejected":
                continue

            existing = self._active_strategies.get(entry.key)
            if (
                existing is not None
                and existing.entry.family == entry.family
                and existing.entry.params == entry.params
                and existing.entry.hours_utc == entry.hours_utc
            ):
                existing.entry = entry
                existing.status = entry.status
                continue

            # Build new family instance
            cls = FAMILY_CLASSES.get(entry.family)
            if cls is None:
                logger.warning(
                    "Unknown strategy family %s for key %s; skipping",
                    entry.family,
                    entry.key,
                )
                continue

            instance = cls(
                strategy_key=entry.key,
                params=entry.params,
                hours_utc=entry.hours_utc,
                asset=entry.asset,
                timeframe=entry.timeframe,
            )
            self._active_strategies[entry.key] = CatalogStrategyInfo(
                entry=entry,
                instance=instance,
                status=entry.status,
                added_at=now,
            )

        # Process removed entries (R-BOT-9)
        removed_keys = set(self._active_strategies.keys()) - incoming_keys
        for key in removed_keys:
            info = self._active_strategies.pop(key)
            open_orders = self._in_flight_orders.get(key, set())
            if open_orders:
                # Strategy has in-flight orders: mark retiring until orders settle
                info.status = "retiring"
                self._retiring_strategies[key] = info
                logger.info(
                    "Strategy %s marked retiring with %d orders in flight",
                    key,
                    len(open_orders),
                )
            else:
                # No orders in flight: discard immediately
                logger.info("Strategy %s discarded immediately upon manifest update", key)

    def notify_order_opened(self, strategy_key: str, order_id: str) -> None:
        """Register an order currently in-flight for a strategy."""
        self._in_flight_orders.setdefault(strategy_key, set()).add(order_id)

    def notify_order_settled(self, strategy_key: str, order_id: str) -> None:
        """Notify that an in-flight order has settled. Discard retiring strategies when clear."""
        orders = self._in_flight_orders.get(strategy_key)
        if orders is not None:
            orders.discard(order_id)
            if not orders:
                self._in_flight_orders.pop(strategy_key, None)

        if strategy_key in self._retiring_strategies:
            remaining = self._in_flight_orders.get(strategy_key, set())
            if not remaining:
                self._retiring_strategies.pop(strategy_key, None)
                logger.info(
                    "Retiring strategy %s fully settled all in-flight orders and was discarded",
                    strategy_key,
                )

    def get_strategy(self, strategy_key: str) -> CatalogStrategyInfo | None:
        """Retrieve strategy info from active or retiring sets."""
        return self._active_strategies.get(strategy_key) or self._retiring_strategies.get(
            strategy_key
        )

    def is_eligible(
        self,
        strategy_key: str,
        *,
        account_type: str,
        current_payout: Decimal | float | str | int,
        now_utc: datetime | None = None,
    ) -> tuple[bool, str, PayoutGateResult | None]:
        """Check all execution gates: retirement, account mode, trading hours, and payout edge."""
        if strategy_key in self._retiring_strategies:
            return False, "STRATEGY_RETIRING", None

        info = self._active_strategies.get(strategy_key)
        if info is None:
            return False, "STRATEGY_NOT_FOUND", None

        # R-BOT-8: Observation strategies are only eligible on Demo accounts
        is_real = account_type.strip().upper() in {"REAL", "LIVE"}
        if is_real and info.status == "observation":
            return False, "OBSERVATION_ONLY_DEMO", None

        # Trading hours check in UTC
        eval_time = now_utc if now_utc is not None else self._utc_clock()
        if not is_within_trading_hours(info.entry.hours_utc, eval_time):
            return False, "OUTSIDE_TRADING_HOURS", None

        # R-BOT-6: Payout gate check
        payout_res = PayoutGate.check_payout(
            current_payout=current_payout,
            wilson_lower=info.entry.validated.wilson_lower,
            payout_min=info.entry.validated.payout_min,
        )
        if not payout_res.allowed:
            return False, payout_res.reason_code, payout_res

        return True, "ELIGIBLE", payout_res

    def demote_to_observation(
        self,
        strategy_key: str,
        reason: str = "STRATEGY_DEMOTED_BY_SPRT",
    ) -> bool:
        """Demote an active strategy to observation status (R-BOT-7)."""
        info = self._active_strategies.get(strategy_key)
        if info is None:
            return False
        info.status = "observation"
        info.entry = replace(info.entry, status="observation")
        logger.info("Strategy %s demoted to observation: %s", strategy_key, reason)
        return True

    def demote_all_to_observation(
        self,
        reason: str = "MANIFEST_EXPIRED",
    ) -> int:
        """Demote all active strategies to observation status upon manifest expiration (R-BOT-8)."""
        count = 0
        for _key, info in self._active_strategies.items():
            if info.status != "observation":
                info.status = "observation"
                info.entry = replace(info.entry, status="observation")
                count += 1
        logger.warning(
            "Demoted %d active strategies to observation: %s",
            count,
            reason,
        )
        return count
