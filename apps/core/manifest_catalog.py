"""Dynamic strategy catalog populated by signed manifests (R-BOT-5, R-BOT-8, R-BOT-9)."""

from __future__ import annotations

import logging
import threading
from collections import deque
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from decimal import Decimal
from functools import wraps
from typing import Any, Concatenate

from apps.core.families import FAMILY_CLASSES, FamilyStrategyBase, is_within_trading_hours
from apps.core.payout_gate import PayoutGate, PayoutGateResult

logger = logging.getLogger("core.manifest_catalog")


def _locked[R, **P](
    method: Callable[Concatenate[DynamicManifestCatalog, P], R],
) -> Callable[Concatenate[DynamicManifestCatalog, P], R]:
    @wraps(method)
    def call(self: DynamicManifestCatalog, /, *args: P.args, **kwargs: P.kwargs) -> R:
        with self.execution_lock:
            return method(self, *args, **kwargs)

    return call


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
    warmup_required: int | None = None


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
            warmup_required=(
                None if raw.get("warmup_required") is None else int(raw["warmup_required"])
            ),
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
        warmup_required=(
            None if getattr(raw, "warmup_required", None) is None else int(raw.warmup_required)
        ),
    )


@dataclass
class CatalogStrategyInfo:
    entry: StrategyCatalogEntry
    instance: FamilyStrategyBase
    status: str  # "approved" | "observation" | "retiring"
    added_at: datetime


@dataclass(frozen=True, slots=True)
class PreparedCatalogStrategy:
    """A fully parsed and constructed recipe, still invisible to evaluation."""

    entry: StrategyCatalogEntry
    instance: FamilyStrategyBase


@dataclass(frozen=True, slots=True)
class PreparedCatalogManifest:
    """Catalog generation prepared off the execution lock and ready for one swap."""

    manifest_version: int | None
    expires_at: int | None
    published_at: int | None
    strategies: tuple[PreparedCatalogStrategy, ...]


class DynamicManifestCatalog:
    """Manages strategy instances loaded dynamically from applied manifests."""

    def __init__(
        self,
        utc_clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        event_sink: Callable[[str, dict[str, object]], None] | None = None,
    ) -> None:
        self._utc_clock = utc_clock
        self.execution_lock = threading.RLock()
        self._event_sink = event_sink
        self._events: deque[tuple[str, dict[str, object]]] = deque(maxlen=128)
        self._active_strategies: dict[str, CatalogStrategyInfo] = {}
        self._retiring_strategies: dict[str, CatalogStrategyInfo] = {}
        self._in_flight_orders: dict[str, set[str]] = {}
        self._manifest_version: int | None = None
        self._expires_at: int | None = None
        self._published_at: int | None = None
        self._demotions: dict[str, ValidatedStats] = {}

    @property
    def manifest_version(self) -> int | None:
        return self._manifest_version

    @property
    def expired(self) -> bool:
        return self._expires_at is not None and self._utc_clock().timestamp() >= self._expires_at

    @property
    def active_strategies(self) -> dict[str, CatalogStrategyInfo]:
        return dict(self._active_strategies)

    @property
    def retiring_strategies(self) -> dict[str, CatalogStrategyInfo]:
        return dict(self._retiring_strategies)

    @property
    def events(self) -> tuple[tuple[str, dict[str, object]], ...]:
        return tuple(self._events)

    def _emit(self, event: str, **fields: object) -> None:
        payload = dict(fields)
        self._events.append((event, payload))
        logger.warning("%s %s", event, payload)
        if self._event_sink is not None:
            self._event_sink(event, payload)

    def prepare_manifest(self, manifest: Any) -> Callable[[], None]:
        """Compile every executable recipe without changing the active generation.

        The returned commit is intentionally tiny and performs one catalog swap under
        ``execution_lock``.  A malformed or unsupported recipe therefore cannot leave
        a partially updated catalog visible to an evaluation or financial admission.
        """
        expiry = (
            manifest.get("expires_at")
            if isinstance(manifest, dict)
            else getattr(manifest, "expires_at", None)
        )
        published = (
            manifest.get("published_at")
            if isinstance(manifest, dict)
            else getattr(manifest, "published_at", None)
        )
        manifest_version: int | None = None
        if hasattr(manifest, "manifest_version"):
            manifest_version = int(manifest.manifest_version)
        elif hasattr(manifest, "version"):
            manifest_version = int(manifest.version)
        elif isinstance(manifest, dict):
            raw_version = manifest.get("manifest_version", manifest.get("version"))
            manifest_version = None if raw_version is None else int(raw_version)

        raw_strategies: Sequence[Any]
        if hasattr(manifest, "strategies"):
            raw_strategies = manifest.strategies
        elif isinstance(manifest, dict):
            raw_strategies = manifest.get("strategies", ())
        else:
            raw_strategies = ()

        prepared: list[PreparedCatalogStrategy] = []
        for raw in raw_strategies:
            entry = parse_strategy_entry(raw)
            if entry.status == "rejected":
                continue
            cls = FAMILY_CLASSES.get(entry.family)
            if cls is None:
                raise ValueError("MANIFEST_FAMILY_UNSUPPORTED")

            instance = cls(
                strategy_key=entry.key,
                params=entry.params,
                hours_utc=entry.hours_utc,
                asset=entry.asset,
                timeframe=entry.timeframe,
            )
            if (
                entry.warmup_required is not None
                and entry.warmup_required != instance.warmup_required
            ):
                self._emit(
                    "WARMUP_MISMATCH",
                    strategy_key=entry.key,
                    declared=entry.warmup_required,
                    calculated=instance.warmup_required,
                )
                continue
            prepared.append(PreparedCatalogStrategy(entry=entry, instance=instance))

        generation = PreparedCatalogManifest(
            manifest_version=manifest_version,
            expires_at=None if expiry is None else int(expiry),
            published_at=None if published is None else int(published),
            strategies=tuple(prepared),
        )
        return lambda: self.commit_prepared(generation)

    @_locked
    def commit_prepared(self, prepared: PreparedCatalogManifest) -> None:
        """Atomically replace active recipes while retaining in-flight revisions."""
        now = self._utc_clock()
        previous_active = self._active_strategies
        next_active: dict[str, CatalogStrategyInfo] = {}
        incoming_keys: set[str] = set()

        for candidate in prepared.strategies:
            entry = candidate.entry
            if self._demotions.get(entry.key) == entry.validated:
                entry = replace(entry, status="observation")
            existing = previous_active.get(entry.key)
            unchanged = (
                existing is not None
                and existing.entry.family == entry.family
                and existing.entry.params == entry.params
                and existing.entry.hours_utc == entry.hours_utc
                and existing.entry.asset == entry.asset
                and existing.entry.timeframe == entry.timeframe
            )
            next_active[entry.key] = CatalogStrategyInfo(
                entry=entry,
                instance=(
                    existing.instance if unchanged and existing is not None else candidate.instance
                ),
                status=entry.status,
                added_at=existing.added_at if unchanged and existing is not None else now,
            )
            incoming_keys.add(entry.key)

        # Removed or behaviour-changing revisions stay available only for settlement
        # bookkeeping while their durable orders are non-terminal.
        retired_candidates = {
            key
            for key, old in previous_active.items()
            if key not in incoming_keys
            or (
                key in next_active
                and (
                    old.entry.family != next_active[key].entry.family
                    or old.entry.params != next_active[key].entry.params
                    or old.entry.hours_utc != next_active[key].entry.hours_utc
                    or old.entry.asset != next_active[key].entry.asset
                    or old.entry.timeframe != next_active[key].entry.timeframe
                )
            )
        }
        next_retiring = dict(self._retiring_strategies)
        for key in retired_candidates:
            info = previous_active[key]
            open_orders = self._in_flight_orders.get(key, set())
            if open_orders:
                info.status = "retiring"
                next_retiring[key] = info
                logger.info(
                    "Strategy %s marked retiring with %d orders in flight",
                    key,
                    len(open_orders),
                )
            else:
                logger.info("Strategy %s discarded immediately upon manifest update", key)

        self._manifest_version = prepared.manifest_version
        self._expires_at = prepared.expires_at
        self._published_at = prepared.published_at
        self._active_strategies = next_active
        self._retiring_strategies = next_retiring

    def apply_manifest(self, manifest: Any) -> None:
        """Prepare off-lock, then atomically expose one complete generation."""
        self.prepare_manifest(manifest)()

    @_locked
    def notify_order_opened(self, strategy_key: str, order_id: str) -> None:
        """Register an order currently in-flight for a strategy."""
        self._in_flight_orders.setdefault(strategy_key, set()).add(order_id)

    @_locked
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

    @_locked
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
        mode = account_type.strip().upper()
        if mode not in {"REAL", "LIVE", "DEMO", "PRACTICE"}:
            return False, "ACCOUNT_TYPE_UNCONFIRMED", None
        if info.status not in {"approved", "observation"}:
            return False, "STRATEGY_STATUS_INELIGIBLE", None
        if mode in {"REAL", "LIVE"} and info.status == "observation":
            return False, "OBSERVATION_ONLY_DEMO", None

        # Trading hours check in UTC
        eval_time = now_utc if now_utc is not None else self._utc_clock()
        if eval_time.utcoffset() is None:
            return False, "CLOCK_UNTRUSTED", None
        if self._published_at is not None and eval_time.timestamp() < self._published_at:
            return False, "MANIFEST_NOT_YET_VALID", None
        if self._expires_at is not None and eval_time.timestamp() >= self._expires_at:
            return False, "MANIFEST_EXPIRED", None
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

    @_locked
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
        self._demotions[strategy_key] = info.entry.validated
        info.entry = replace(info.entry, status="observation")
        logger.info("Strategy %s demoted to observation: %s", strategy_key, reason)
        return True

    @_locked
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
