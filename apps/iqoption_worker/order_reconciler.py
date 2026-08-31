"""Evidence-based reconciliation after reconnect or restart."""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta

from apps.iqoption_worker.broker_adapter import BrokerAdapterError, BrokerAdapterWrapper
from packages.domain.orders import Order, OrderState
from packages.persistence.sqlite_store import SQLiteStateStore


@dataclass(frozen=True, slots=True)
class ReconciliationResult:
    reconciliation_id: str
    start_time: datetime
    end_time: datetime
    local_orders: int
    remote_orders: int
    matched: int
    recovered: int
    unknown: int
    divergences: tuple[str, ...]
    trading_allowed: bool


class OrderReconciler:
    def __init__(
        self,
        store: SQLiteStateStore,
        adapter: BrokerAdapterWrapper,
        *,
        account_id: str,
        window_seconds: int = 900,
    ) -> None:
        if window_seconds <= 0:
            raise ValueError("window_seconds must be positive")
        self.store = store
        self.adapter = adapter
        self.account_id = account_id
        self.window_seconds = window_seconds
        self.last_result: ReconciliationResult | None = None

    async def reconcile(self) -> ReconciliationResult:
        started = datetime.now(UTC)
        reconciliation_id = str(uuid.uuid4())
        local = self.store.list_orders(self.account_id)
        since = started - timedelta(seconds=self.window_seconds)
        divergences: list[str] = []
        try:
            remote_open = list(await self.adapter.get_open_orders())
            remote_settled = list(await self.adapter.get_settled_orders(since))
            await self.adapter.get_balance()
            await self.adapter.get_positions()
        except BrokerAdapterError as exc:
            ended = datetime.now(UTC)
            result = ReconciliationResult(
                reconciliation_id,
                started,
                ended,
                len(local),
                0,
                0,
                0,
                len(local),
                (f"QUERY_FAILED:{exc.code}",),
                False,
            )
            self.last_result = result
            return result
        remote = remote_open + remote_settled
        matched, recovered, unknown = self.match_local_remote(local, remote, divergences)
        self.rebuild_projections(local, remote)
        ended = datetime.now(UTC)
        result = ReconciliationResult(
            reconciliation_id,
            started,
            ended,
            len(local),
            len(remote),
            matched,
            recovered,
            unknown,
            tuple(divergences),
            unknown == 0 and not divergences,
        )
        self.last_result = result
        return result

    def match_local_remote(
        self,
        local_orders: Iterable[Order],
        remote_orders: Iterable[Order],
        divergences: list[str] | None = None,
    ) -> tuple[int, int, int]:
        notes = divergences if divergences is not None else []
        local_by_key = {order.internal_order_id: order for order in local_orders}
        remote_by_key = {order.internal_order_id: order for order in remote_orders}
        matched = recovered = unknown = 0
        for order_id, local in local_by_key.items():
            remote = remote_by_key.get(order_id)
            if remote is None:
                if local.state not in {OrderState.REJECTED_REMOTE, OrderState.MANUAL_REVIEW}:
                    unknown += 1
                    notes.append(f"LOCAL_WITHOUT_REMOTE:{order_id}")
                continue
            if local.state is remote.state:
                matched += 1
            else:
                matched += 1
                notes.append(f"STATUS_DIVERGENCE:{order_id}")
        for order_id in remote_by_key.keys() - local_by_key.keys():
            recovered += 1
            notes.append(f"REMOTE_WITHOUT_LOCAL:{order_id}")
        return matched, recovered, unknown

    def rebuild_projections(
        self, local_orders: Iterable[Order], remote_orders: Iterable[Order]
    ) -> None:
        local_by_key = {order.internal_order_id: order for order in local_orders}
        for remote in remote_orders:
            local = local_by_key.get(remote.internal_order_id)
            if local is None:
                self.store.save_order(remote)
            elif local.state is not remote.state:
                self.store.save_order(replace(local, state=remote.state))


__all__ = ["OrderReconciler", "ReconciliationResult"]
