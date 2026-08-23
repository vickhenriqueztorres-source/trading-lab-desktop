from __future__ import annotations

import platform
import time
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

from packages.observability.diagnostic import (
    DiagnosticBundleBuilder,
    DiagnosticBundleResult,
    DiagnosticContext,
)
from packages.observability.events import InMemoryEventSink, OperationalEvent

if TYPE_CHECKING:
    from apps.core.runtime import CoreRuntime


class CoreDiagnosticService:
    """Aggregates runtime telemetry and triggers safe redacted bundle creation."""

    def __init__(
        self,
        runtime: CoreRuntime,
        reports_dir: Path | None = None,
        *,
        app_version: str = "1.0.0",
        start_time_monotonic: float | None = None,
        builder: DiagnosticBundleBuilder | None = None,
    ) -> None:
        self._runtime = runtime
        self._reports_dir = reports_dir or (runtime.profile_directory / "reports" / "diagnostics")
        self._app_version = app_version
        self._start_time_monotonic = (
            start_time_monotonic if start_time_monotonic is not None else time.monotonic()
        )
        self._builder = builder or DiagnosticBundleBuilder()

    def generate_bundle(self, *, max_events: int = 1000) -> DiagnosticBundleResult:
        context = self._collect_context()
        return self._builder.build_bundle(
            self._reports_dir,
            context,
            max_events=max_events,
        )

    def _collect_context(self) -> DiagnosticContext:
        uptime = max(0.0, time.monotonic() - self._start_time_monotonic)

        # 1. Health Gates snapshot
        health_snapshot: dict[str, Any] = {}
        try:
            snap = self._runtime.health_gate.get_snapshot()
            health_snapshot = {
                "global_state": {
                    "is_open": snap.global_state.is_open,
                    "reason_code": snap.global_state.reason_code,
                },
                "scoped_states": {
                    f"{b}:{a}": {"is_open": s.is_open, "reason_code": s.reason_code}
                    for (b, a), s in snap.scoped_states.items()
                },
            }
        except Exception as exc:
            health_snapshot = {"error": str(exc)}

        # 2. Risk Metrics snapshot
        risk_metrics: dict[str, Any] = {}
        try:
            metrics = self._runtime.risk_ledger.get_metrics()
            risk_metrics = {
                "consecutive_losses": metrics.consecutive_losses,
                "consolidated_daily_pnl_minor_units": metrics.consolidated_daily_pnl_minor_units,
                "global_exposure_minor_units": metrics.global_exposure_minor_units,
                "global_max_exposure_minor_units": metrics.global_max_exposure_minor_units,
                "reference_currency": self._runtime.risk_ledger.config.reference_currency,
                "risk_state": metrics.risk_state.value,
            }
        except Exception as exc:
            risk_metrics = {"error": str(exc)}

        # 3. Events from InMemoryEventSink
        recent_events: Sequence[OperationalEvent] = ()
        event_sink = self._runtime.event_sink
        if isinstance(event_sink, InMemoryEventSink):
            recent_events = event_sink.events

        # 4. Environment & Process tree info
        process_tree: list[dict[str, Any]] = []
        if self._runtime.worker_supervisor is not None:
            supervisor = self._runtime.worker_supervisor
            process_tree.append(
                {
                    "role": "SIMULATED_WORKER",
                    "status": supervisor.health_state.value,
                }
            )

        return DiagnosticContext(
            app_version=self._app_version,
            python_version=platform.python_version(),
            os_name=platform.system(),
            os_release=platform.release(),
            os_version=platform.version(),
            uptime_seconds=uptime,
            environment_meta={
                "profile_directory": str(self._runtime.profile_directory.name),
                "database_exists": self._runtime.database_path.exists(),
            },
            health_snapshot=health_snapshot,
            risk_metrics=risk_metrics,
            recent_events=recent_events,
            process_tree=process_tree,
        )
