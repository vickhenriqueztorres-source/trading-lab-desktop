from __future__ import annotations

import contextlib
import queue
from collections.abc import Mapping
from decimal import Decimal
from enum import StrEnum

from apps.deriv_worker.request_allowlist import DerivOperation, validate_read_only_request
from apps.deriv_worker.schema import DerivErrorCategory, DerivWorkerError
from apps.deriv_worker.websocket_client import TransportHealthSnapshot


class FakeDerivScenario(StrEnum):
    NORMAL = "NORMAL"
    DISCONNECT = "DISCONNECT"
    SCHEMA_CHANGED = "SCHEMA_CHANGED"
    RATE_LIMIT = "RATE_LIMIT"
    TICK_GAP = "TICK_GAP"
    DUPLICATE_TICK = "DUPLICATE_TICK"
    OUT_OF_ORDER_TICK = "OUT_OF_ORDER_TICK"
    MALFORMED_JSON = "MALFORMED_JSON"
    SLOW_RESPONSE = "SLOW_RESPONSE"
    CRASH_AFTER_HANDSHAKE = "CRASH_AFTER_HANDSHAKE"
    STREAMING_TICKS = "STREAMING_TICKS"
    SHADOW_CANDLES = "SHADOW_CANDLES"
    BUY_TIMEOUT = "BUY_TIMEOUT"
    BUY_DISCONNECT = "BUY_DISCONNECT"
    BUY_REJECTED = "BUY_REJECTED"
    BUY_SETTLE_WIN = "BUY_SETTLE_WIN"
    BUY_SETTLE_LOSS = "BUY_SETTLE_LOSS"


class FakeDerivTransport:
    """Deterministic subset of Deriv public and demo API for testing and simulations."""

    def __init__(
        self,
        scenario: FakeDerivScenario = FakeDerivScenario.NORMAL,
        *,
        demo_authenticated: bool = False,
        server_epoch: int = 1_700_000_100,
        demo_balance: str = "10000.00",
        demo_currency: str = "USD",
    ) -> None:
        self.scenario = scenario
        self.demo_authenticated = demo_authenticated
        self.server_epoch = server_epoch
        self.demo_balance = demo_balance
        self.demo_currency = demo_currency
        self.public_read_requests = 0
        self.demo_read_requests = 0
        self.trading_write_requests = 0
        self.reconnect_count = 0
        self.operation_counts: dict[DerivOperation, int] = {}
        self._failed_once = False
        self._tick_index = 0
        self._next_contract_id = 200_000_001
        self._contracts: dict[int, dict[str, object]] = {}
        self._proposals: dict[str, dict[str, object]] = {}
        self._transactions: list[dict[str, object]] = []
        self._stream_events: queue.Queue[dict[str, object]] = queue.Queue(maxsize=128)
        self._account_events: queue.Queue[dict[str, object]] = queue.Queue(maxsize=32)
        self._contract_events: queue.Queue[dict[str, object]] = queue.Queue(maxsize=64)
        self._proposal_events: queue.Queue[dict[str, object]] = queue.Queue(maxsize=64)
        self.stream_events_dropped = 0

    def reconnect(self) -> None:
        self.reconnect_count += 1

    def health_snapshot(self) -> TransportHealthSnapshot:
        return TransportHealthSnapshot(0, 0, 0, 0, 0, 0, 0, None)

    def request(
        self,
        operation: DerivOperation,
        payload: Mapping[str, object],
        *,
        timeout: float,
    ) -> dict[str, object]:
        del timeout
        validate_read_only_request(operation, payload, demo_authenticated=self.demo_authenticated)
        if self.demo_authenticated:
            self.demo_read_requests += 1
        else:
            self.public_read_requests += 1
        self.operation_counts[operation] = self.operation_counts.get(operation, 0) + 1
        if self.scenario is FakeDerivScenario.MALFORMED_JSON:
            raise DerivWorkerError(
                DerivErrorCategory.SCHEMA_INCOMPATIBLE,
                "DERIV_SCHEMA_INCOMPATIBLE",
            )
        if self.scenario is FakeDerivScenario.SLOW_RESPONSE:
            raise DerivWorkerError(
                DerivErrorCategory.NETWORK_ERROR,
                "DERIV_REQUEST_TIMEOUT",
            )
        if self.scenario is FakeDerivScenario.DISCONNECT and not self._failed_once:
            self._failed_once = True
            raise DerivWorkerError(DerivErrorCategory.NETWORK_ERROR, "DERIV_NETWORK_ERROR")
        if self.scenario is FakeDerivScenario.RATE_LIMIT and not self._failed_once:
            self._failed_once = True
            raise DerivWorkerError(DerivErrorCategory.RATE_LIMITED, "DERIV_RATE_LIMITED")
        if operation is DerivOperation.PING:
            return {"msg_type": "ping", "ping": "pong"}
        if operation is DerivOperation.TIME:
            return {"msg_type": "time", "time": self.server_epoch}
        if operation is DerivOperation.BALANCE:
            return {
                "msg_type": "balance",
                "balance": {
                    "balance": self.demo_balance,
                    "currency": self.demo_currency,
                    "loginid": "VRTC_REDACTED",
                },
                "subscription": {"id": "fake-balance-subscription"},
            }
        if operation is DerivOperation.ACTIVE_SYMBOLS:
            item: dict[str, object] = {
                "underlying_symbol": "frxEURUSD",
                "underlying_symbol_name": "EUR/USD",
                "underlying_symbol_type": "forex",
                "market": "forex",
                "submarket": "major_pairs",
                "pip_size": "0.0001",
                "exchange_is_open": 1,
                "is_trading_suspended": 0,
                "extra_safe_field": "tolerated",
            }
            if self.scenario is FakeDerivScenario.SCHEMA_CHANGED:
                item.pop("underlying_symbol")
            synthetic = [
                {
                    "underlying_symbol": symbol,
                    "underlying_symbol_name": f"Volatility {symbol[2:]} Index",
                    "underlying_symbol_type": "synthetic_index",
                    "market": "synthetic_index",
                    "submarket": "continuous_indices",
                    "pip_size": "0.01",
                    "exchange_is_open": 1,
                    "is_trading_suspended": 0,
                }
                for symbol in ("R_10", "R_25", "R_50", "R_75", "R_100")
            ]
            return {"msg_type": "active_symbols", "active_symbols": [item, *synthetic]}
        if operation is DerivOperation.CONTRACTS_LIST:
            return {"msg_type": "contracts_list", "contracts_list": ["callput"]}
        if operation is DerivOperation.CONTRACTS_FOR:
            symbol = str(payload["contracts_for"])
            contract_types = (
                ("DIGITOVER", "DIGITUNDER", "DIGITDIFF", "DIGITEVEN", "DIGITODD")
                if symbol.startswith(("R_", "1HZ"))
                else ("CALL",)
            )
            return {
                "msg_type": "contracts_for",
                "contracts_for": {
                    "available": [
                        {
                            "contract_type": contract_type,
                            "underlying_symbol": symbol,
                            "contract_category": "digits",
                        }
                        for contract_type in contract_types
                    ],
                    "hit_count": len(contract_types),
                },
            }
        if operation is DerivOperation.TICKS:
            epochs = [1_700_000_100, 1_700_000_101, 1_700_000_103]
            if self.scenario is FakeDerivScenario.OUT_OF_ORDER_TICK:
                epochs = [1_700_000_101, 1_700_000_100]
            index = min(self._tick_index, len(epochs) - 1)
            self._tick_index += 1
            epoch = epochs[index]
            subscription_id = f"fake-sub-{self._tick_index:024d}"
            if self.scenario is FakeDerivScenario.STREAMING_TICKS:
                self.emit_tick(
                    epoch=epoch + 1,
                    quote="1.08502",
                    symbol=str(payload["ticks"]),
                    subscription_id=subscription_id,
                )
            if self.scenario is FakeDerivScenario.SHADOW_CANDLES:
                for offset in range(1, 61):
                    self.emit_tick(
                        epoch=epoch + offset,
                        quote="1.08502" if offset % 2 else "1.08500",
                        symbol=str(payload["ticks"]),
                        subscription_id=subscription_id,
                    )
            return {
                "msg_type": "tick",
                "tick": {"epoch": epoch, "quote": "1.08501", "symbol": payload["ticks"]},
                "subscription": {"id": subscription_id},
            }
        if operation is DerivOperation.TICKS_HISTORY:
            symbol = str(payload["ticks_history"])
            if payload.get("style") == "candles":
                end = payload.get("end")
                granularity_value = payload.get("granularity")
                if isinstance(granularity_value, bool) or not isinstance(granularity_value, int):
                    raise ValueError("fake candle granularity must be an integer")
                granularity = granularity_value
                epoch = int(end) - granularity if isinstance(end, int) else 1_699_999_980
                return {
                    "msg_type": "candles",
                    "candles": [
                        {
                            "epoch": epoch,
                            "open": "1.08490",
                            "high": "1.08520",
                            "low": "1.08480",
                            "close": "1.08501",
                        }
                    ],
                    "symbol": symbol,
                }
            prices: list[object] = ["1.08499", "1.08500", "1.08501"]
            times = [1_700_000_098, 1_700_000_099, 1_700_000_100]
            if self.scenario is FakeDerivScenario.DUPLICATE_TICK:
                prices.append("1.08501")
                times.append(1_700_000_100)
            return {
                "msg_type": "history",
                "history": {"prices": prices, "times": times},
                "symbol": symbol,
            }
        if operation is DerivOperation.FORGET:
            return {"msg_type": "forget", "forget": 1}
        if operation is DerivOperation.FORGET_ALL:
            return {"msg_type": "forget_all", "forget_all": 1}
        if operation is DerivOperation.BUY:
            if self.scenario is FakeDerivScenario.BUY_REJECTED:
                return {
                    "msg_type": "buy",
                    "error": {"code": "MarketClosed", "message": "Market is closed for trading"},
                }
            contract_id = self._next_contract_id
            self._next_contract_id += 1
            stake = str(payload.get("price", "10.00"))
            proposal_id = str(payload.get("buy", ""))
            direct_parameters = payload.get("parameters")
            parameters = (
                dict(direct_parameters)
                if isinstance(direct_parameters, Mapping)
                else self._proposals.get(proposal_id, {})
            )
            symbol = str(parameters.get("symbol", parameters.get("underlying_symbol", "frxEURUSD")))
            direction = str(parameters.get("contract_type", "CALL"))
            currency = str(parameters.get("currency", "USD"))
            barrier = parameters.get("barrier")
            raw_pt = payload.get("passthrough")
            passthrough = raw_pt if isinstance(raw_pt, dict) else {}

            contract_record: dict[str, object] = {
                "contract_id": contract_id,
                "underlying": symbol,
                "contract_type": direction,
                "currency": currency,
                "buy_price": stake,
                "payout": str(Decimal(stake) * Decimal("1.95")),
                "status": "open",
                "is_sold": 0,
                "is_expired": 0,
                "date_start": self.server_epoch,
                "date_expiry": self.server_epoch + 60,
                "passthrough": passthrough,
            }
            if barrier is not None:
                contract_record["barrier"] = str(barrier)
                contract_record["date_expiry"] = self.server_epoch + 1
            self._contracts[contract_id] = contract_record
            self._transactions.append(
                {
                    "contract_id": contract_id,
                    "transaction_id": contract_id + 500_000_000,
                    "amount": f"-{stake}",
                    "balance_after": "9990.00",
                    "action_type": "buy",
                    "transaction_time": self.server_epoch,
                    "passthrough": passthrough,
                }
            )

            if self.scenario is FakeDerivScenario.BUY_TIMEOUT:
                raise DerivWorkerError(
                    DerivErrorCategory.NETWORK_ERROR,
                    "DERIV_REQUEST_TIMEOUT",
                )
            if self.scenario is FakeDerivScenario.BUY_DISCONNECT:
                raise DerivWorkerError(
                    DerivErrorCategory.NETWORK_ERROR,
                    "DERIV_NETWORK_ERROR",
                )

            if self.scenario in (
                FakeDerivScenario.BUY_SETTLE_WIN,
                FakeDerivScenario.BUY_SETTLE_LOSS,
                FakeDerivScenario.NORMAL,
            ):
                open_poc: dict[str, object] = {
                    "msg_type": "proposal_open_contract",
                    "proposal_open_contract": {
                        **contract_record,
                        "status": "open",
                        "is_sold": 0,
                    },
                    "subscription": {"id": f"fake-poc-sub-{contract_id}"},
                }
                self.emit_contract_event(open_poc)

                is_win = self.scenario is not FakeDerivScenario.BUY_SETTLE_LOSS
                profit = str(Decimal(stake) * Decimal("0.95")) if is_win else f"-{stake}"
                payout = str(Decimal(stake) * Decimal("1.95")) if is_win else "0.00"
                settled_record = {
                    **contract_record,
                    "status": "won" if is_win else "lost",
                    "is_sold": 1,
                    "is_expired": 1,
                    "profit": profit,
                    "payout": payout,
                }
                if direction.startswith("DIGIT"):
                    predicted = int(str(barrier)) if barrier is not None else None
                    if direction == "DIGITDIFF" and predicted is not None:
                        exit_digit = (predicted + 1) % 10 if is_win else predicted
                    elif direction == "DIGITOVER" and predicted is not None:
                        exit_digit = min(9, predicted + 1) if is_win else predicted
                    elif direction == "DIGITUNDER" and predicted is not None:
                        exit_digit = max(0, predicted - 1) if is_win else predicted
                    elif direction == "DIGITEVEN":
                        exit_digit = 2 if is_win else 3
                    elif direction == "DIGITODD":
                        exit_digit = 3 if is_win else 2
                    else:
                        exit_digit = 0
                    settled_record["exit_tick"] = f"100.0{exit_digit}"
                self._contracts[contract_id] = settled_record
                settled_poc: dict[str, object] = {
                    "msg_type": "proposal_open_contract",
                    "proposal_open_contract": settled_record,
                    "subscription": {"id": f"fake-poc-sub-{contract_id}"},
                }
                self.emit_contract_event(settled_poc)

            return {
                "msg_type": "buy",
                "buy": {
                    "contract_id": contract_id,
                    "buy_price": stake,
                    "balance_after": "9990.00",
                    "shortcode": f"{direction}_{symbol}",
                    "start_time": self.server_epoch,
                },
            }
        if operation is DerivOperation.PROPOSAL_OPEN_CONTRACT:
            cid = int(str(payload.get("contract_id", 0)))
            record = self._contracts.get(cid)
            if record is None:
                record = {
                    "contract_id": cid,
                    "underlying": "frxEURUSD",
                    "contract_type": "CALL",
                    "currency": "USD",
                    "buy_price": "10.00",
                    "payout": "19.50",
                    "profit": "9.50",
                    "status": "won",
                    "is_sold": 1,
                    "is_expired": 1,
                }
            return {
                "msg_type": "proposal_open_contract",
                "proposal_open_contract": record,
                "subscription": {"id": f"fake-poc-sub-{cid}"},
            }
        if operation is DerivOperation.PORTFOLIO:
            contracts = [
                dict(record)
                for record in self._contracts.values()
                if int(str(record.get("is_sold", 0))) != 1
                and int(str(record.get("is_expired", 0))) != 1
            ]
            return {
                "msg_type": "portfolio",
                "portfolio": {"contracts": contracts},
            }
        if operation is DerivOperation.STATEMENT:
            return {
                "msg_type": "statement",
                "statement": {
                    "count": len(self._transactions),
                    "transactions": list(self._transactions),
                },
            }
        if operation is DerivOperation.PROFIT_TABLE:
            return {
                "msg_type": "profit_table",
                "profit_table": {
                    "count": len(self._contracts),
                    "transactions": list(self._contracts.values()),
                },
            }
        if operation is DerivOperation.PROPOSAL:
            proposal_id = f"fake-proposal-{len(self._proposals) + 1}"
            self._proposals[proposal_id] = dict(payload)
            ask = Decimal(str(payload.get("amount", "10.00")))
            contract_type = str(payload.get("contract_type", "")).upper()
            net_ratio = Decimal("0.10") if contract_type == "DIGITDIFF" else Decimal("0.95")
            response: dict[str, object] = {
                "msg_type": "proposal",
                "proposal": {
                    "id": proposal_id,
                    "ask_price": str(ask),
                    "payout": str(ask * (Decimal("1") + net_ratio)),
                    "spot": "1.08500",
                    "spot_time": self.server_epoch,
                },
            }
            if payload.get("subscribe") == 1:
                response["subscription"] = {"id": proposal_id}
                with contextlib.suppress(queue.Full):
                    self._proposal_events.put_nowait(dict(response))
            return response
        raise AssertionError(f"fake operation not implemented: {operation}")

    def emit_tick(
        self,
        *,
        epoch: int,
        quote: str,
        symbol: str = "frxEURUSD",
        subscription_id: str = "fake-sub-000000000000000000000001",
    ) -> None:
        payload: dict[str, object] = {
            "msg_type": "tick",
            "tick": {"epoch": epoch, "quote": quote, "symbol": symbol},
            "subscription": {"id": subscription_id},
        }
        try:
            self._stream_events.put_nowait(payload)
        except queue.Full:
            self.stream_events_dropped += 1

    def emit_contract_event(self, event: dict[str, object]) -> None:
        with contextlib.suppress(queue.Full):
            self._contract_events.put_nowait(event)

    def settle_latest_contract(self, *, won: bool) -> str:
        """Settle the newest fake contract and publish its deterministic stream event."""

        if not self._contracts:
            raise ValueError("no fake contract is available for settlement")
        contract_id = max(self._contracts)
        current = self._contracts[contract_id]
        buy_price = Decimal(str(current["buy_price"]))
        profit = buy_price * Decimal("0.95") if won else -buy_price
        payout = buy_price * Decimal("1.95") if won else Decimal("0")
        settled = {
            **current,
            "status": "won" if won else "lost",
            "is_sold": 1,
            "is_expired": 1,
            "profit": str(profit),
            "payout": str(payout),
        }
        self._contracts[contract_id] = settled
        self.emit_contract_event(
            {
                "msg_type": "proposal_open_contract",
                "proposal_open_contract": settled,
                "subscription": {"id": f"fake-poc-sub-{contract_id}"},
            }
        )
        return str(contract_id)

    def receive(self, *, timeout: float) -> dict[str, object] | None:
        if timeout <= 0:
            raise ValueError("fake Deriv receive timeout must be positive")
        try:
            return self._stream_events.get(timeout=timeout)
        except queue.Empty:
            return None

    def receive_account(self, *, timeout: float) -> dict[str, object] | None:
        if timeout < 0:
            raise ValueError("fake Deriv account receive timeout cannot be negative")
        try:
            return self._account_events.get(timeout=timeout)
        except queue.Empty:
            return None

    def receive_contract(self, *, timeout: float) -> dict[str, object] | None:
        if timeout < 0:
            raise ValueError("fake Deriv contract receive timeout cannot be negative")
        try:
            return self._contract_events.get(timeout=timeout)
        except queue.Empty:
            return None

    def receive_proposal(self, *, timeout: float) -> dict[str, object] | None:
        if timeout < 0:
            raise ValueError("fake Deriv proposal receive timeout cannot be negative")
        try:
            return self._proposal_events.get(timeout=timeout)
        except queue.Empty:
            return None

    def emit_balance(self, balance: str, currency: str = "USD") -> None:
        self._account_events.put_nowait(
            {
                "msg_type": "balance",
                "balance": {
                    "balance": balance,
                    "currency": currency,
                    "loginid": "VRTC_REDACTED",
                },
                "subscription": {"id": "fake-balance-subscription"},
            }
        )

    def close(self) -> None:
        return
