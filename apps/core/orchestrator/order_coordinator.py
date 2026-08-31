"""Single-writer order coordinator for one local worker process."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime

from apps.core.orchestrator.order_queue import OrderQueue
from apps.core.resilience.circuit_breakers import CircuitBreaker
from apps.core.state.order_machine import can_transition
from apps.iqoption_worker.broker_adapter import BrokerAdapterError, BrokerAdapterWrapper
from packages.domain.orders import ExecutionResult, Order, OrderIntent, OrderState
from packages.persistence.sqlite_store import SQLiteStateStore


class OrderAdmissionError(RuntimeError):
    pass


class OrderCoordinator:
    def __init__(
        self,
        store: SQLiteStateStore,
        adapter: BrokerAdapterWrapper,
        *,
        account_id: str,
        leadership_check: Callable[[], bool] = lambda: True,
        connection_check: Callable[[], bool] | None = None,
        submit_breaker: CircuitBreaker | None = None,
        queue: OrderQueue | None = None,
        fencing_token: str = "local",
    ) -> None:
        self.store = store
        self.adapter = adapter
        self.account_id = account_id
        self._leadership_check = leadership_check
        self._connection_check = connection_check or (lambda: True)
        self._submit_breaker = submit_breaker
        self._queue = queue or OrderQueue()
        self.fencing_token = fencing_token
        self._account_lock = asyncio.Lock()
        self._asset_locks: dict[str, asyncio.Lock] = {}
        self._queue_lock = asyncio.Lock()

    async def submit_order(self, intent: OrderIntent) -> ExecutionResult:
        if intent.account_id != self.account_id:
            raise OrderAdmissionError("ACCOUNT_MISMATCH")
        async with self._asset_lock(intent.asset):
            async with self._queue_lock:
                await self._queue.enqueue(intent)
                queued = await self._queue.dequeue(timeout=1.0)
            return await self._submit_serialized(queued)

    def get_pending_unknown(self) -> list[Order]:
        return [
            order
            for order in self.store.list_orders(self.account_id)
            if order.state in {OrderState.UNKNOWN, OrderState.RECONCILING}
        ]

    def is_trading_allowed(self) -> bool:
        if not self._leadership_check() or not self._connection_check():
            return False
        if self._submit_breaker is not None and not self._submit_breaker.can_execute():
            return False
        return not self.get_pending_unknown()

    async def _submit_serialized(self, intent: OrderIntent) -> ExecutionResult:
        existing = next(
            (
                order
                for order in self.store.list_orders(self.account_id)
                if order.dedupe_key == intent.dedupe_key
            ),
            None,
        )
        if existing is not None:
            return ExecutionResult(existing.state, existing.internal_order_id)
        if not self.is_trading_allowed():
            raise OrderAdmissionError("TRADING_NOT_ALLOWED")
        if not self.store.save_idempotency_key(intent.dedupe_key, intent.intent_id):
            return ExecutionResult(OrderState.CREATED, intent.intent_id)

        now = datetime.now(UTC)
        order = Order(
            internal_order_id=intent.intent_id,
            dedupe_key=intent.dedupe_key,
            account_id=intent.account_id,
            strategy_id=intent.strategy_id,
            asset=intent.asset,
            direction=intent.direction,
            amount=intent.amount,
            duration=intent.duration,
            state=OrderState.CREATED,
            timestamps={"created": now},
            fencing_token=self.fencing_token,
        )
        for next_state in (
            OrderState.ADMITTED,
            OrderState.RESERVED,
            OrderState.SUBMITTING,
        ):
            if not can_transition(order.state, next_state):
                raise OrderAdmissionError("INVALID_ORDER_TRANSITION")
            order = replace(
                order,
                state=next_state,
                timestamps={**order.timestamps, next_state.value.lower(): datetime.now(UTC)},
            )
            self.store.save_order(order)
        self.store.save_reservation(
            f"reservation:{order.internal_order_id}",
            order.internal_order_id,
            order.amount,
            str(intent.metadata.get("currency", "USD")),
        )
        try:
            result = await self.adapter.submit_order(intent)
        except BrokerAdapterError as exc:
            if self._submit_breaker is not None:
                self._submit_breaker.record_failure()
            state = OrderState.UNKNOWN if "TIMEOUT" in exc.code else OrderState.REJECTED_REMOTE
            order = replace(order, state=state)
            self.store.save_order(order)
            if state is OrderState.REJECTED_REMOTE:
                self.store.release_reservation(f"reservation:{order.internal_order_id}")
            return ExecutionResult(
                state,
                order.internal_order_id,
                error_code=exc.code,
                retry_allowed=False,
                reconciliation_required=state is OrderState.UNKNOWN,
            )
        if self._submit_breaker is not None:
            self._submit_breaker.record_success()
        order = replace(order, state=result.state)
        self.store.save_order(order)
        if result.state is OrderState.REJECTED_REMOTE:
            self.store.release_reservation(f"reservation:{order.internal_order_id}")
        return result

    def _asset_lock(self, asset: str) -> asyncio.Lock:
        return self._asset_locks.setdefault(asset, asyncio.Lock())


__all__ = ["OrderAdmissionError", "OrderCoordinator"]
