"""Async, timeout-bounded wrapper around the isolated broker adapter."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterable
from datetime import datetime
from typing import Any, cast

from packages.brokers.port import BrokerError, BrokerPort
from packages.domain.orders import ExecutionResult, Order, OrderIntent


class BrokerAdapterError(RuntimeError):
    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(message or code)
        self.code = code


class BrokerAdapterWrapper:
    def __init__(self, adapter: BrokerPort, *, timeout_seconds: float = 5.0) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self.adapter = adapter
        self.timeout_seconds = timeout_seconds

    async def connect(self) -> None:
        await self._call(self.adapter.connect)

    async def disconnect(self) -> None:
        await self._call(self.adapter.disconnect)

    async def get_balance(self) -> Any:
        return await self._call(self.adapter.get_balance)

    async def get_open_orders(self) -> Iterable[Order]:
        return cast(Iterable[Order], await self._call(self.adapter.get_open_orders))

    async def get_settled_orders(self, since: datetime) -> Iterable[Order]:
        return cast(
            Iterable[Order], await self._call(lambda: self.adapter.get_settled_orders(since))
        )

    async def get_positions(self) -> Any:
        return await self._call(self.adapter.get_positions)

    async def submit_order(self, intent: OrderIntent) -> ExecutionResult:
        return cast(ExecutionResult, await self._call(lambda: self.adapter.submit_order(intent)))

    async def subscribe_candles(
        self, asset: str, timeframe: str, callback: Callable[[Any], None]
    ) -> str:
        return cast(
            str,
            await self._call(lambda: self.adapter.subscribe_candles(asset, timeframe, callback)),
        )

    async def _call(self, function: Callable[[], Any]) -> Any:
        try:
            return await asyncio.wait_for(asyncio.to_thread(function), self.timeout_seconds)
        except TimeoutError as exc:
            raise BrokerAdapterError("BROKER_OPERATION_TIMEOUT") from exc
        except BrokerError as exc:
            raise BrokerAdapterError(type(exc).__name__.upper(), str(exc)) from exc


__all__ = ["BrokerAdapterError", "BrokerAdapterWrapper"]
