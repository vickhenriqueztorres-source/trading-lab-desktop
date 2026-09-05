"""Live performance monitoring with SPRT and dynamic demotion (R-BOT-7, R-BOT-8)."""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from dataclasses import asdict
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
        self._revision_cache: dict[str, str] = {}
        self._expiry_announced = False
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._guard = threading.RLock()
        self._last_success: float | None = None

    @staticmethod
    def binding(entry: StrategyCatalogEntry) -> dict[str, Any]:
        # Status/version are not numeric revisions. A republish must not reset SPRT.
        identity = {
            "key": entry.key,
            "asset": entry.asset,
            "timeframe": entry.timeframe,
            "family": entry.family,
            "params": entry.params,
            "hours_utc": entry.hours_utc,
            "validated": asdict(entry.validated),
        }
        revision = hashlib.sha256(
            json.dumps(identity, sort_keys=True, default=str, separators=(",", ":")).encode()
        ).hexdigest()
        return {
            "strategy_key": entry.key,
            "revision": revision,
            "asset": entry.asset,
            "timeframe": entry.timeframe,
            "p0": str(entry.validated.wilson_lower),
            "p1": str(entry.validated.p_min_at_validation),
        }

    @property
    def ready(self) -> bool:
        # Lock-free snapshot avoids inversion with the catalog execution lock.
        last = self._last_success
        return last is not None and 0 <= time.monotonic() - last < 5

    def poll_persisted(self) -> None:
        """Consume durable outcomes, including reconciliation and restart, exactly once.

        The writer commits the statistical state and consumption marker atomically.
        No anonymous upload is enabled by this integration.
        """
        if self._writer is None:
            raise RuntimeError("MANIFEST_MONITOR_UNAVAILABLE")
        with self._guard, self._catalog.execution_lock:
            self._last_success = None
            self.sync_from_catalog()
            if self._catalog.expired:
                if not self._expiry_announced:
                    self.on_manifest_expired()
                    self._expiry_announced = True
            else:
                self._expiry_announced = False

            def update(
                binding: dict[str, Any], prior: dict[str, Any] | None, pnl: int
            ) -> dict[str, Any]:
                monitor = (
                    SPRT(binding["p0"], binding["p1"]) if prior is None else SPRT.from_dict(prior)
                )
                monitor.update(pnl > 0)
                return monitor.to_dict()

            rows = self._writer.consume_manifest_orders(update)
            # Restore demotions before permitting another entry. Old revisions never
            # contaminate the newly validated strategy that reuses the same key.
            for row in self._writer.manifest_monitor_states():
                info = self._catalog.get_strategy(str(row["strategy_key"]))
                if info is None or self.binding(info.entry)["revision"] != row["revision"]:
                    continue
                monitor = SPRT.from_dict(json.loads(row["state_json"]))
                self._monitors[info.entry.key] = monitor
                if monitor.decision is Decision.REJECT_H0:
                    was_approved = info.status == "approved"
                    self._catalog.demote_to_observation(info.entry.key)
                    if was_approved:
                        self._event_sink.emit(
                            "strategy_demoted",
                            strategy_key=info.entry.key,
                            reason_code=STRATEGY_DEMOTED_BY_SPRT,
                            n=monitor.n,
                            llr=str(monitor.llr),
                        )
            for row in rows:
                key, order_id = str(row["strategy_key"]), str(row["order_id"])
                if row["state"] in {"SETTLED", "REJECTED", "SEND_BLOCKED", "CANCELLED", "EXPIRED"}:
                    self._catalog.notify_order_settled(key, order_id)
                else:
                    self._catalog.notify_order_opened(key, order_id)
            self._last_success = time.monotonic()

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        try:
            self.poll_persisted()
        except Exception:
            self._event_sink.emit(
                "manifest_monitor_blocked", reason_code="MANIFEST_MONITOR_UNAVAILABLE"
            )
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="manifest-live-monitor", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        while not self._stop.wait(0.5):
            try:
                self.poll_persisted()
            except Exception:
                with self._guard:
                    self._last_success = None
                self._event_sink.emit(
                    "manifest_monitor_blocked", reason_code="MANIFEST_MONITOR_UNAVAILABLE"
                )

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
            if self._thread.is_alive():
                raise RuntimeError("MANIFEST_MONITOR_SHUTDOWN_TIMEOUT")
        self._last_success = None

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
        revision = str(self.binding(entry)["revision"])
        if self._revision_cache.get(key, revision) != revision:
            monitor = None
        self._revision_cache[key] = revision

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
                    raise RuntimeError("MANIFEST_MONITOR_RESTORE_FAILED") from exc

            if monitor is None:
                monitor = SPRT(p0=p0, p1=p1)

            self._monitors[key] = monitor
            self._validated_cache[key] = (p0, p1)

        if monitor.decision is Decision.REJECT_H0:
            self._catalog.demote_to_observation(key)

        return monitor

    def on_settlement(
        self,
        strategy_key: str,
        won: bool,
        ts: int,
        payout_pct: Decimal | str | float | int,
        order_id: str | None = None,
    ) -> Decision:
        """Consume persistent evidence, or feed a writer-free statistical test.

        In production, callback arguments are never a second source of outcomes.
        A duplicate callback only rechecks the durable consumption cursor.
        """
        if self._writer is not None:
            self.poll_persisted()
            persisted_monitor = self._monitors.get(strategy_key)
            return Decision.CONTINUE if persisted_monitor is None else persisted_monitor.decision
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

        # 6. Forward outcome to background anonymous uploader (R-BOT-10)
        if self._uploader is not None:
            self._uploader.enqueue(
                strategy_key=strategy_key,
                ts=ts,
                won=won,
                payout_pct=payout_pct,
            )

        return dec
