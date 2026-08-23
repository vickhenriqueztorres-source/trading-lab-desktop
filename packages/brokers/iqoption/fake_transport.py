from __future__ import annotations

import contextlib
import queue
import time
from collections.abc import Mapping
from decimal import Decimal
from enum import StrEnum
from typing import Any

from apps.iqoption_worker.schema import IQOptionErrorCategory, IQOptionWorkerError
from packages.brokers.iqoption.validators import PRACTICE_BALANCE_TYPE, REAL_BALANCE_TYPE


class FakeIQOptionScenario(StrEnum):
    NORMAL = "normal"
    AUTH_REJECTED = "auth-rejected"
    REAL_ACCOUNT_REJECTED = "real-account-rejected"
    BUY_REJECTED = "buy-rejected"
    BUY_TIMEOUT = "buy-timeout"
    BUY_DISCONNECT = "buy-disconnect"
    BUY_SETTLE_WIN = "buy-settle-win"
    BUY_SETTLE_LOSS = "buy-settle-loss"
    DUPLICATE_TICK = "duplicate-tick"


class FakeIQOptionTransport:
    def __init__(
        self,
        scenario: FakeIQOptionScenario = FakeIQOptionScenario.NORMAL,
        *,
        practice_mode: bool = True,
        server_epoch: int | None = None,
        initial_balance: Decimal = Decimal("10000.00"),
        currency: str = "USD",
    ) -> None:
        self.scenario = scenario
        self.practice_mode = practice_mode
        self._server_epoch = server_epoch or int(time.time())
        self._balance = initial_balance
        self._currency = currency
        self._next_contract_id = 300_000_001
        self._contracts: dict[int, dict[str, Any]] = {}
        self._transactions: list[dict[str, Any]] = []
        self._contract_events: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=256)
        self._stream_events: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=1024)
        self._connected = True

    @property
    def server_epoch(self) -> int:
        return self._server_epoch

    @property
    def is_connected(self) -> bool:
        return self._connected

    def request(
        self,
        name: str,
        msg: Mapping[str, Any],
        *,
        timeout: float = 2.0,
    ) -> dict[str, Any]:
        if not self._connected:
            raise IQOptionWorkerError(
                IQOptionErrorCategory.NETWORK_ERROR,
                "IQOPTION_DISCONNECTED",
            )
        if self.scenario is FakeIQOptionScenario.AUTH_REJECTED and name in ("ssid", "auth"):
            return {"isSuccessful": False, "message": "Invalid session or credentials"}

        if name in ("ssid", "auth"):
            balance_type = (
                REAL_BALANCE_TYPE
                if self.scenario is FakeIQOptionScenario.REAL_ACCOUNT_REJECTED
                or not self.practice_mode
                else PRACTICE_BALANCE_TYPE
            )
            return {
                "isSuccessful": True,
                "result": {
                    "user_id": 987654321,
                    "balance_type": balance_type,
                    "is_demo": balance_type == PRACTICE_BALANCE_TYPE,
                },
            }

        if name in ("get-balances", "profile", "balance"):
            balance_type = (
                REAL_BALANCE_TYPE
                if self.scenario is FakeIQOptionScenario.REAL_ACCOUNT_REJECTED
                or not self.practice_mode
                else PRACTICE_BALANCE_TYPE
            )
            return {
                "balance_type": balance_type,
                "balance": str(self._balance),
                "currency": self._currency,
                "is_demo": balance_type == PRACTICE_BALANCE_TYPE,
                "account_type": "practice" if balance_type == PRACTICE_BALANCE_TYPE else "real",
            }

        if name in ("get_server_time", "time"):
            return {
                "server_time": self._server_epoch,
                "microsecond": 123456,
            }

        if name in ("buyV2", "buy", "binary_options_buy", "digital_option_buy"):
            if self.scenario is FakeIQOptionScenario.BUY_REJECTED:
                return {
                    "status": False,
                    "message": "Market is closed for trading",
                    "reason": "MarketClosed",
                }

            contract_id = self._next_contract_id
            self._next_contract_id += 1

            price = str(msg.get("price", "10.00"))
            symbol = str(msg.get("active", msg.get("symbol", "EURUSD")))
            direction = str(msg.get("direction", "call")).lower()
            exp_time = int(msg.get("exp_time", self._server_epoch + 60))
            client_order_id = str(msg.get("client_order_id", msg.get("order_id", "")))
            correlation_id = str(msg.get("correlation_id", ""))

            contract_record: dict[str, Any] = {
                "id": contract_id,
                "contract_id": contract_id,
                "active": symbol,
                "symbol": symbol,
                "direction": direction,
                "amount": price,
                "currency": self._currency,
                "open_time": self._server_epoch,
                "close_time": exp_time,
                "status": "open",
                "win": "equal",
                "win_amount": "0.00",
                "client_order_id": client_order_id,
                "correlation_id": correlation_id,
            }
            self._contracts[contract_id] = contract_record
            self._transactions.append(
                {
                    "id": contract_id,
                    "contract_id": contract_id,
                    "type": "buy",
                    "amount": f"-{price}",
                    "balance_after": str(self._balance - Decimal(price)),
                    "client_order_id": client_order_id,
                    "correlation_id": correlation_id,
                }
            )

            if self.scenario is FakeIQOptionScenario.BUY_TIMEOUT:
                raise IQOptionWorkerError(
                    IQOptionErrorCategory.NETWORK_ERROR,
                    "IQOPTION_REQUEST_TIMEOUT",
                )
            if self.scenario is FakeIQOptionScenario.BUY_DISCONNECT:
                raise IQOptionWorkerError(
                    IQOptionErrorCategory.NETWORK_ERROR,
                    "IQOPTION_NETWORK_ERROR",
                )

            # Emit initial open event
            open_event = {
                "name": "option-opened",
                "msg": {
                    "id": contract_id,
                    "status": "open",
                    "client_order_id": client_order_id,
                    "correlation_id": correlation_id,
                    "active": symbol,
                    "direction": direction,
                    "amount": price,
                    "currency": self._currency,
                    "open_time": self._server_epoch,
                    "close_time": exp_time,
                },
            }
            self.emit_contract_event(open_event)

            # Emit settle event if scenario demands
            if self.scenario in (
                FakeIQOptionScenario.BUY_SETTLE_WIN,
                FakeIQOptionScenario.NORMAL,
            ):
                payout = str(Decimal(price) * Decimal("1.95"))
                contract_record["status"] = "win"
                contract_record["win"] = "win"
                contract_record["win_amount"] = payout
                settle_event = {
                    "name": "option-closed",
                    "msg": {
                        "id": contract_id,
                        "status": "win",
                        "win": "win",
                        "win_amount": payout,
                        "client_order_id": client_order_id,
                        "correlation_id": correlation_id,
                        "active": symbol,
                        "direction": direction,
                        "amount": price,
                        "currency": self._currency,
                        "open_time": self._server_epoch,
                        "close_time": exp_time,
                    },
                }
                self.emit_contract_event(settle_event)
            elif self.scenario is FakeIQOptionScenario.BUY_SETTLE_LOSS:
                contract_record["status"] = "loose"
                contract_record["win"] = "loose"
                contract_record["win_amount"] = "0.00"
                settle_event = {
                    "name": "option-closed",
                    "msg": {
                        "id": contract_id,
                        "status": "loose",
                        "win": "loose",
                        "win_amount": "0.00",
                        "client_order_id": client_order_id,
                        "correlation_id": correlation_id,
                        "active": symbol,
                        "direction": direction,
                        "amount": price,
                        "currency": self._currency,
                        "open_time": self._server_epoch,
                        "close_time": exp_time,
                    },
                }
                self.emit_contract_event(settle_event)

            return {
                "status": True,
                "id": contract_id,
                "result": {"id": contract_id, "status": "open"},
            }

        if name in ("get_options", "get_option_history", "get_position", "options"):
            contract_id_arg = msg.get("id", msg.get("contract_id"))
            lookup_client_id = msg.get("client_order_id")
            if contract_id_arg is not None:
                contract = self._contracts.get(int(contract_id_arg))
                if contract is not None:
                    return {"isSuccessful": True, "result": contract}
            if lookup_client_id is not None:
                for contract in self._contracts.values():
                    if contract.get("client_order_id") == str(lookup_client_id):
                        return {"isSuccessful": True, "result": contract}
            return {"isSuccessful": False, "message": "Option not found"}

        return {"isSuccessful": True, "result": {}}

    def emit_contract_event(self, event: dict[str, Any]) -> None:
        with contextlib.suppress(queue.Full):
            self._contract_events.put_nowait(event)

    def receive_contract(self, *, timeout: float = 0.1) -> dict[str, Any] | None:
        if timeout <= 0:
            raise ValueError("receive timeout must be positive")
        try:
            return self._contract_events.get(timeout=timeout)
        except queue.Empty:
            return None

    def close(self) -> None:
        self._connected = False
