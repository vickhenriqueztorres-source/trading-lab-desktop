"""Async lifecycle controller for the isolated IQ Option worker."""

from __future__ import annotations

import asyncio
import time
from enum import StrEnum

from apps.core.orchestrator.leader_lease import LeaderLease
from apps.iqoption_worker.connection_manager import ConnectionManager
from apps.iqoption_worker.order_reconciler import OrderReconciler


class WorkerState(StrEnum):
    STARTING = "STARTING"
    CONNECTING = "CONNECTING"
    AUTHENTICATING = "AUTHENTICATING"
    SYNCING = "SYNCING"
    READY = "READY"
    DEGRADED = "DEGRADED"
    READ_ONLY = "READ_ONLY"
    RECONCILING = "RECONCILING"
    HALTED = "HALTED"
    SHUTTING_DOWN = "SHUTTING_DOWN"


class WorkerProcess:
    """Owns worker lifecycle; no broker SDK or strategy runs in this class."""

    def __init__(
        self,
        connection_manager: ConnectionManager,
        reconciler: OrderReconciler,
        *,
        startup_timeout: float = 30.0,
        leader_lease: LeaderLease | None = None,
    ) -> None:
        self.connection_manager = connection_manager
        self.reconciler = reconciler
        self.startup_timeout = startup_timeout
        self.leader_lease = leader_lease
        self._state = WorkerState.HALTED
        self._task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()
        self._ready_event = asyncio.Event()
        self._started_at = 0.0
        self.last_error: str | None = None

    async def start(self) -> None:
        if self.is_running():
            return
        self._stop_event.clear()
        self._ready_event.clear()
        self.last_error = None
        self._started_at = time.monotonic()
        self._task = asyncio.create_task(self._run(), name="iqoption-worker")
        try:
            await asyncio.wait_for(self._ready_event.wait(), self.startup_timeout)
        except TimeoutError:
            self._state = WorkerState.DEGRADED
            raise RuntimeError("IQOPTION_WORKER_START_TIMEOUT") from None

    async def stop(self) -> None:
        if self._task is None:
            self._state = WorkerState.HALTED
            return
        self._state = WorkerState.SHUTTING_DOWN
        self._stop_event.set()
        try:
            await self._task
        finally:
            self._task = None
            self._state = WorkerState.HALTED

    def is_running(self) -> bool:
        return self._task is not None and not self._task.done()

    def get_state(self) -> WorkerState:
        return self._state

    def health_check(self) -> dict[str, object]:
        state = self._state
        return {
            "liveness": self.is_running() and state is not WorkerState.HALTED,
            "readiness": state in {WorkerState.READY, WorkerState.READ_ONLY},
            "trading_readiness": state is WorkerState.READY,
            "state": state.value,
            "uptime": max(0.0, time.monotonic() - self._started_at) if self._started_at else 0.0,
            "last_error": self.last_error,
        }

    async def _run(self) -> None:
        try:
            self._state = WorkerState.STARTING
            self._state = WorkerState.CONNECTING
            if not await self.connection_manager.connect():
                self._state = WorkerState.DEGRADED
                self.last_error = "IQOPTION_CONNECTION_FAILED"
                self._ready_event.set()
                await self._stop_event.wait()
                return
            self._state = WorkerState.AUTHENTICATING
            self._state = WorkerState.SYNCING
            self._state = WorkerState.RECONCILING
            result = await self.reconciler.reconcile()
            if result.trading_allowed and (
                self.leader_lease is None or await self.leader_lease.acquire()
            ):
                self.connection_manager.mark_synchronized()
                self._state = WorkerState.READY
            else:
                self._state = WorkerState.READ_ONLY
            self._ready_event.set()
            await self._standby_or_leader_loop()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.last_error = type(exc).__name__
            self._state = WorkerState.DEGRADED
            self._ready_event.set()
            await self._stop_event.wait()
        finally:
            if self.leader_lease is not None:
                await self.leader_lease.release()
            if self.connection_manager.is_connected():
                await self.connection_manager.disconnect()

    async def _standby_or_leader_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                await asyncio.wait_for(self._stop_event.wait(), 1.0)
            except TimeoutError:
                if self.leader_lease is None:
                    continue
                if self.leader_lease.is_leader():
                    if not await self.leader_lease.renew():
                        self._state = WorkerState.READ_ONLY
                    continue
                if await self.leader_lease.acquire():
                    self._state = WorkerState.RECONCILING
                    result = await self.reconciler.reconcile()
                    if result.trading_allowed:
                        self.connection_manager.mark_synchronized()
                        self._state = WorkerState.READY


__all__ = ["WorkerProcess", "WorkerState"]
