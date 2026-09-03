"""Live performance monitoring with SPRT and dynamic demotion (R-BOT-7, R-BOT-8)."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from apps.core.manifest_catalog import DynamicManifestCatalog, StrategyCatalogEntry
from apps.core.outcomes_uploader import OutcomesUploader
from packages.observability.events import EventSink, NullEventSink
from packages.persistence.writer import SingleDatabaseWriter
from packages.sprt import SPRT, Decision

logger = logging.getLogger("core.live_monitor")

STRATEGY_DEMOTED_BY_SPRT = "STRATEGY_DEMOTED_BY_SPRT"
MANIFEST_EXPIRED = "MANIFEST_EXPIRED"


class LiveMonitor:
    """Manages online sequential probability ratio tests (SPRT) per strategy.

    - Computes LLR upon each trade settlement.
    - Demotes failing strategies to observation status when H0 is rejected.
    - Demotes all strategies to observation when the current manifest expires.
    - Resets tests when strategies receive updated validated stats.
    - Persists state durably via SingleDatabaseWriter outside the evaluation cycle.
    - Feeds anonymous outcomes to OutcomesUploader outside the evaluation cycle.
    """

    def __init__(
        self,
        catalog: DynamicManifestCatalog,
        *,
        writer: SingleDatabaseWriter | None = None,
        event_sink: EventSink | None = None,
        uploader: OutcomesUploader | None = None,
    ) -> None:
        self._catalog = catalog
        self._writer = writer
        self._event_sink = event_sink or NullEventSink()
        self._uploader = uploader
        self._monitors: dict[str, SPRT] = {}
        self._validated_cache: dict[str, tuple[Decimal, Decimal]] = {}

    @property
    def monitors(self) -> dict[str, SPRT]:
        return dict(self._monitors)

    def sync_from_catalog(self) -> None:
        """Synchronize active strategies from the catalog into SPRT monitors."""
        active = self._catalog.active_strategies
        for _key, info in active.items():
            self._ensure_monitor(info.entry)

    def on_manifest_applied(self, manifest: Any) -> None:
        """Handle new manifest application; reset monitor if validated stats changed."""
        self.sync_from_catalog()
        active = self._catalog.active_strategies
        for key, info in active.items():
            current_p0 = info.entry.validated.wilson_lower
            current_p1 = info.entry.validated.p_min_at_validation
            cached = self._validated_cache.get(key)
            if cached is not None and cached != (current_p0, current_p1):
                logger.info(
                    "Strategy %s validated stats changed; resetting SPRT monitor",
                    key,
                )
                self._monitors[key] = SPRT(p0=current_p0, p1=current_p1)
            self._validated_cache[key] = (current_p0, current_p1)

    def on_manifest_expired(self) -> int:
        """Handle manifest expiration: demote all strategies to observation (R-BOT-8)."""
        logger.warning(
            "Manifest expired without replacement: demoting all strategies to observation"
        )
        count = self._catalog.demote_all_to_observation(reason=MANIFEST_EXPIRED)
        self._event_sink.emit(
            "manifest_expired",
            demoted_count=count,
            reason_code=MANIFEST_EXPIRED,
        )
        return count

    def _ensure_monitor(self, entry: StrategyCatalogEntry) -> SPRT:
        key = entry.key
        monitor = self._monitors.get(key)
        p0 = entry.validated.wilson_lower
        p1 = entry.validated.p_min_at_validation

        if monitor is None:
            # Check database for existing persisted state
            if self._writer is not None:
                try:
                    row = self._writer.get_sprt_monitor(key)
                    if (
                        row is not None
                        and Decimal(str(row["p0"])) == p0
                        and Decimal(str(row["p1"])) == p1
                    ):
                        monitor = SPRT.from_dict(row)
                except Exception as exc:
                    logger.warning("Failed to restore SPRT state for %s: %s", key, exc)

            if monitor is None:
                monitor = SPRT(p0=p0, p1=p1)

            self._monitors[key] = monitor
            self._validated_cache[key] = (p0, p1)

        return monitor

    def on_settlement(
        self,
        strategy_key: str,
        won: bool,
        ts: int,
        payout_pct: Decimal | str | float | int,
        order_id: str | None = None,
    ) -> Decision:
        """Process trade settlement (called outside critical tick evaluation cycle)."""
        # 1. If an order ID is given, inform catalog to settle in-flight count
        if order_id is not None:
            self._catalog.notify_order_settled(strategy_key, order_id)

        # 2. Lookup or instantiate monitor
        info = self._catalog.get_strategy(strategy_key)
        monitor: SPRT | None
        if info is not None:
            monitor = self._ensure_monitor(info.entry)
        else:
            monitor = self._monitors.get(strategy_key)

        if monitor is None:
            logger.warning(
                "Settlement for untracked strategy %s; skipping SPRT update",
                strategy_key,
            )
            return Decision.CONTINUE

        # 3. Update SPRT
        dec = monitor.update(won)

        # 4. Handle rejection / demotion (R-BOT-7)
        if dec == Decision.REJECT_H0:
            logger.warning(
                "SPRT rejected H0 for strategy %s at n=%d, LLR=%s; demoting to observation",
                strategy_key,
                monitor.n,
                monitor.llr,
            )
            self._catalog.demote_to_observation(
                strategy_key,
                reason=STRATEGY_DEMOTED_BY_SPRT,
            )
            self._event_sink.emit(
                "strategy_demoted",
                strategy_key=strategy_key,
                n=monitor.n,
                llr=str(monitor.llr),
                reason_code=STRATEGY_DEMOTED_BY_SPRT,
            )

        # 5. Persist monitor state durably outside evaluation cycle
        if self._writer is not None:
            try:
                now_iso = datetime.now(UTC).isoformat()
                current_strat = self._catalog.get_strategy(strategy_key)
                current_status = (
                    current_strat.status if current_strat is not None else "observation"
                )
                self._writer.save_sprt_monitor(
                    strategy_key=strategy_key,
                    p0=str(monitor.p0),
                    p1=str(monitor.p1),
                    alpha=str(monitor.alpha),
                    beta=str(monitor.beta),
                    llr=str(monitor.llr),
                    n=monitor.n,
                    wins=monitor.wins,
                    decision=monitor.decision.value,
                    status=current_status,
                    updated_at=now_iso,
                )
            except Exception as exc:
                logger.warning("Failed to persist SPRT state for %s: %s", strategy_key, exc)

        # 6. Forward outcome to background anonymous uploader (R-BOT-10)
        if self._uploader is not None:
            self._uploader.enqueue(
                strategy_key=strategy_key,
                ts=ts,
                won=won,
                payout_pct=payout_pct,
            )

        return dec
