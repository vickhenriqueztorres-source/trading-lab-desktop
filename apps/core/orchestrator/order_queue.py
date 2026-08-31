"""Bounded priority queue for order intents."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from itertools import count

from packages.domain.orders import OrderIntent


class QueueBackpressureError(RuntimeError):
    pass


@dataclass(order=True, slots=True)
class _QueuedIntent:
    priority: int
    sequence: int
    intent: OrderIntent = field(compare=False)


class OrderQueue:
    def __init__(self, maxsize: int = 128) -> None:
        if maxsize <= 0:
            raise ValueError("maxsize must be positive")
        self._queue: asyncio.PriorityQueue[_QueuedIntent] = asyncio.PriorityQueue(maxsize=maxsize)
        self._sequence = count()

    async def enqueue(
        self, intent: OrderIntent, *, priority: int = 100, timeout: float = 0.0
    ) -> None:
        item = _QueuedIntent(priority, next(self._sequence), intent)
        try:
            if timeout > 0:
                await asyncio.wait_for(self._queue.put(item), timeout)
            else:
                self._queue.put_nowait(item)
        except (asyncio.QueueFull, TimeoutError) as exc:
            raise QueueBackpressureError("ORDER_QUEUE_FULL") from exc

    async def dequeue(self, timeout: float = 1.0) -> OrderIntent:
        try:
            item = await asyncio.wait_for(self._queue.get(), timeout)
        except TimeoutError as exc:
            raise TimeoutError("ORDER_QUEUE_TIMEOUT") from exc
        self._queue.task_done()
        return item.intent

    def is_empty(self) -> bool:
        return self._queue.empty()

    def size(self) -> int:
        return self._queue.qsize()


__all__ = ["OrderQueue", "QueueBackpressureError"]
