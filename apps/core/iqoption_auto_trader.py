"""Core-owned IQ Option RSI execution using broker candles and durable orders."""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

from apps.core.iqoption_connection_safety import IQOptionMessageBudget
from apps.core.iqoption_risk_config import IqOptionRiskConfig
from apps.core.read_only_worker_supervisor import ReadOnlyWorkerSupervisor
from apps.core.runtime import CoreRuntime
from packages.domain.market import MarketCandle
from packages.domain.models import (
    Broker,
    BrokerOrderEvent,
    Direction,
    Money,
    OrderRequest,
    OrderState,
)
from packages.persistence.writer import BrokerEventApplyResult, BrokerEventApplyStatus
from packages.protocol.ui_messages import UiIqOptionAssetRank
from packages.strategies.iqoption_rsi import IQOptionRsiDemoStrategy
from packages.strategies.models import RuntimeContext

logger = logging.getLogger("core.iqoption_auto_trader")

# Assets whose protocol identifiers are verified by the worker adapter. AUTO
# rotates one asset per cycle instead of creating a burst of WebSocket requests.
IQOPTION_RADAR_SYMBOLS: tuple[tuple[str, str], ...] = (
    ("EURUSD-OTC", "EUR/USD OTC"),
    ("GBPUSD-OTC", "GBP/USD OTC"),
    ("USDJPY-OTC", "USD/JPY OTC"),
    ("EURJPY-OTC", "EUR/JPY OTC"),
    ("GBPJPY-OTC", "GBP/JPY OTC"),
    ("AUDCAD-OTC", "AUD/CAD OTC"),
    ("NZDUSD-OTC", "NZD/USD OTC"),
    ("USDCHF-OTC", "USD/CHF OTC"),
    ("EURUSD", "EUR/USD"),
    ("GBPUSD", "GBP/USD"),
    ("USDJPY", "USD/JPY"),
    ("AUDUSD", "AUD/USD"),
    ("EURJPY", "EUR/JPY"),
)

IQOPTION_PRACTICE_ACCOUNT_ID = "IQOPTION_PRACTICE"
IQOPTION_ACTIVE_SUSPENSION_COOLDOWN_SECONDS = 5 * 60


@dataclass(frozen=True, slots=True)
class _DispatchResult:
    order_id: str | None
    state: OrderState | None
    reason_code: str

    @property
    def financially_accepted(self) -> bool:
        return self.state in {OrderState.ACCEPTED, OrderState.OPEN, OrderState.SETTLED}


class IqOptionAutoTrader:
    """Evaluate real closed candles and submit through the Core financial pipeline."""

    def __init__(
        self,
        supervisor_provider: Callable[[], ReadOnlyWorkerSupervisor | None],
        runtime_provider: Callable[[], CoreRuntime | None],
        risk_config_provider: Callable[[], IqOptionRiskConfig],
        operator_armed: Callable[[], bool],
        *,
        evaluation_interval_seconds: float = 1.0,
        monotonic: Callable[[], float] = time.monotonic,
        message_budget: IQOptionMessageBudget | None = None,
    ) -> None:
        if evaluation_interval_seconds <= 0:
            raise ValueError("IQ Option evaluation interval must be positive")
        self._supervisor_provider = supervisor_provider
        self._runtime_provider = runtime_provider
        self._risk_config_provider = risk_config_provider
        self._operator_armed = operator_armed
        self._evaluation_interval = evaluation_interval_seconds
        self._monotonic = monotonic
        self._message_budget = message_budget or IQOptionMessageBudget()
        self._message_budget_pressure_reported = False
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.RLock()
        self._status_reason = "IQOPTION_BOT_DISARMED"
        self._sticky_failure_reason: str | None = None
        self._last_evaluated_epochs: dict[str, int] = {}
        self._symbol_suspended_until: dict[str, float] = {}
        self._daily_trades_count = 0
        self._daily_profit_loss = Decimal(0)
        self._consecutive_losses = 0
        self._cooldown_until = 0.0
        self._last_rsi_value: Decimal | None = None
        self._scan_cursor = 0
        self._asset_ranking_by_symbol = {
            symbol: UiIqOptionAssetRank(
                symbol=symbol,
                display_name=display_name,
                rsi="--",
                condition="WAITING_DATA",
                status="WAITING_DATA",
            )
            for symbol, display_name in IQOPTION_RADAR_SYMBOLS
        }
        self._asset_ranking = self._ordered_ranking()
        self._strategy = IQOptionRsiDemoStrategy()

    @property
    def status_reason(self) -> str:
        with self._lock:
            return self._status_reason

    @property
    def last_rsi(self) -> Decimal | None:
        with self._lock:
            return self._last_rsi_value

    @property
    def asset_ranking(self) -> tuple[UiIqOptionAssetRank, ...]:
        with self._lock:
            return self._asset_ranking

    def start(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = threading.Thread(
                target=self._run_loop,
                name="iqoption-auto-trader",
                daemon=True,
            )
            self._thread.start()

    def begin_new_run(self) -> None:
        """Invalidate prior signals and clear a terminal dispatch failure on explicit ARM."""

        with self._lock:
            self._last_evaluated_epochs.clear()
            self._sticky_failure_reason = None
            self._status_reason = "IQOPTION_BOT_ARMED"

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2.0)
        self._thread = None
        with self._lock:
            self._status_reason = "IQOPTION_BOT_DISARMED"

    def _run_loop(self) -> None:
        while not self._stop.is_set():
            try:
                self._evaluate_cycle()
            except Exception as exc:
                logger.warning("IQ Option evaluation failed: %s", type(exc).__name__)
                self._set_status("IQOPTION_EVALUATION_FAILED")
            self._stop.wait(self._evaluation_interval)

    def _evaluate_cycle(self) -> None:
        risk_config = self._risk_config_provider()
        selected_symbol = risk_config.symbol or "AUTO"
        automatic = selected_symbol == "AUTO"
        supervisor = self._supervisor_provider()
        runtime = self._runtime_provider()
        if supervisor is None or supervisor.client is None or runtime is None:
            self._set_status("IQOPTION_CONNECTION_REQUIRED")
            return

        symbols = self._symbols_for_cycle(selected_symbol)
        if not symbols:
            self._set_status("IQOPTION_SYMBOL_UNSUPPORTED")
            return

        candidate: tuple[str, str, Direction, Decimal, int] | None = None
        for symbol, display_name in symbols:
            suspension_remaining = self._symbol_suspended_until.get(symbol, 0.0) - self._monotonic()
            if suspension_remaining > 0:
                self._update_rank(
                    symbol,
                    display_name,
                    rsi="--",
                    condition="ACTIVE_SUSPENDED",
                    selected=not automatic,
                    status="WAITING_ACTIVE_REOPEN",
                )
                self._set_status(
                    f"IQOPTION_ACTIVE_SUSPENDED ({display_name}; {int(suspension_remaining) + 1}s)"
                )
                continue
            self._symbol_suspended_until.pop(symbol, None)
            budget = self._message_budget.try_acquire(self._monotonic())
            if budget.pressure and not self._message_budget_pressure_reported:
                self._message_budget_pressure_reported = True
                runtime.event_sink.emit(
                    "iqoption_message_budget_pressure",
                    used_in_window=budget.used_in_window,
                    limit=budget.limit,
                )
            elif not budget.pressure:
                self._message_budget_pressure_reported = False
            if not budget.allowed:
                self._update_rank(
                    symbol,
                    display_name,
                    rsi="--",
                    condition="MESSAGE_BUDGET_EXHAUSTED",
                    selected=not automatic,
                    status="WAITING_BUDGET",
                )
                self._set_status("IQOPTION_MESSAGE_BUDGET_EXHAUSTED")
                return
            try:
                candles = self._fetch_candles(
                    supervisor,
                    symbol,
                    risk_config.timeframe_seconds,
                )
            except Exception as exc:
                logger.info(
                    "IQ Option candle request failed for %s: %s",
                    symbol,
                    type(exc).__name__,
                )
                runtime.health_gate.block_scope(
                    Broker.IQ_OPTION.value,
                    "market-data",
                    "HG_MARKET_DATA_DISCONNECTED",
                )
                self._update_rank(
                    symbol,
                    display_name,
                    rsi="--",
                    condition="DATA_UNAVAILABLE",
                    selected=not automatic,
                    status="DATA_UNAVAILABLE",
                )
                self._set_status("IQOPTION_MARKET_DATA_UNAVAILABLE")
                continue

            if len(candles) < 15:
                self._update_rank(
                    symbol,
                    display_name,
                    rsi="--",
                    condition="WARMING_UP",
                    selected=not automatic,
                    status="WARMING_UP",
                )
                self._set_status("IQOPTION_MARKET_DATA_WARMUP")
                continue

            runtime.health_gate.clear_scope(
                Broker.IQ_OPTION.value,
                "market-data",
                "HG_MARKET_DATA_DISCONNECTED",
            )
            context = RuntimeContext(
                strategy_id=risk_config.strategy_id,
                strategy_version="1.0.0",
                broker=Broker.IQ_OPTION,
                account_id=IQOPTION_PRACTICE_ACCOUNT_ID,
                product="BINARY_OPTION",
                symbol=symbol,
                timeframe_seconds=risk_config.timeframe_seconds,
                configuration_version="1.0.0",
            )
            try:
                decision = self._strategy.evaluate_decision(candles, context)
            except (TypeError, ValueError):
                self._update_rank(
                    symbol,
                    display_name,
                    rsi="--",
                    condition="INVALID_DATA",
                    selected=not automatic,
                    status="INVALID_DATA",
                )
                continue

            direction = decision.direction
            condition = "NEUTRAL"
            direction_text: str | None = None
            if direction is Direction.CALL:
                condition = "OVERSOLD"
                direction_text = Direction.CALL.value
            elif direction is Direction.PUT:
                condition = "OVERBOUGHT"
                direction_text = Direction.PUT.value
            triggered = direction in {Direction.CALL, Direction.PUT}
            self._update_rank(
                symbol,
                display_name,
                rsi=f"{decision.rsi:.1f}",
                direction=direction_text,
                condition=condition,
                selected=(symbol == selected_symbol) or (automatic and triggered),
                status="TRIGGERED" if triggered else "MONITORING",
            )
            if triggered and candidate is None and direction is not None:
                candidate = (
                    symbol,
                    display_name,
                    direction,
                    decision.rsi,
                    int(candles[-1].close_time.timestamp()),
                )

        if not self._operator_armed():
            self._set_status("IQOPTION_BOT_DISARMED")
            return
        if candidate is None:
            with self._lock:
                sticky_failure = self._sticky_failure_reason
            if sticky_failure is not None:
                self._set_status(sticky_failure)
                return
            if automatic:
                self._set_status(f"AUTO_SCAN_REAL_DATA ({len(IQOPTION_RADAR_SYMBOLS)} ASSETS)")
            else:
                self._set_status(f"IQOPTION_WAITING_RSI_SIGNAL ({selected_symbol})")
            return

        symbol, display_name, direction, rsi, candle_epoch = candidate
        with self._lock:
            self._last_rsi_value = rsi
            sticky_failure = self._sticky_failure_reason
        if sticky_failure is not None:
            self._set_status(sticky_failure)
            return
        if self._last_evaluated_epochs.get(symbol) == candle_epoch:
            self._set_status(f"SINAL_CONSUMIDO: {display_name} {direction.value} @ RSI={rsi:.1f}")
            return
        if self._has_nonterminal_iq_order(runtime):
            self._set_status("IQOPTION_ORDER_IN_FLIGHT")
            return

        risk_reason = self._risk_block_reason(risk_config)
        if risk_reason is not None:
            self._set_status(risk_reason)
            return

        # Consume before dispatch. A rejection or ambiguous response must never
        # turn the same market signal into an automatic financial retry.
        self._last_evaluated_epochs[symbol] = candle_epoch
        dispatch = self._dispatch_order(
            runtime,
            symbol=symbol,
            direction=direction,
            risk_config=risk_config,
        )
        if dispatch.financially_accepted:
            with self._lock:
                self._sticky_failure_reason = None
            self._daily_trades_count += 1
            stake = Decimal(risk_config.stake_minor_units) / Decimal(100)
            self._set_status(
                f"ORDEM_ACEITA: {display_name} {direction.value} @ RSI={rsi:.1f} (USD {stake:.2f})"
            )
        elif dispatch.state in {
            OrderState.UNKNOWN,
            OrderState.RECONCILING,
            OrderState.SETTLEMENT_UNKNOWN,
        }:
            self._daily_trades_count += 1
            self._set_status("IQOPTION_ORDER_UNKNOWN_RECONCILIATION_REQUIRED")
        else:
            if dispatch.reason_code == "IQOPTION_ACTIVE_SUSPENDED":
                self._symbol_suspended_until[symbol] = (
                    self._monotonic() + IQOPTION_ACTIVE_SUSPENSION_COOLDOWN_SECONDS
                )
                self._update_rank(
                    symbol,
                    display_name,
                    rsi=f"{rsi:.1f}",
                    direction=direction.value,
                    condition="ACTIVE_SUSPENDED",
                    selected=True,
                    status="WAITING_ACTIVE_REOPEN",
                )
                self._set_status(f"IQOPTION_ACTIVE_SUSPENDED ({display_name})")
                return
            with self._lock:
                self._sticky_failure_reason = dispatch.reason_code
            self._set_status(dispatch.reason_code)

    def _dispatch_order(
        self,
        runtime: CoreRuntime,
        *,
        symbol: str,
        direction: Direction,
        risk_config: IqOptionRiskConfig,
    ) -> _DispatchResult:
        try:
            persisted = runtime.submit(
                OrderRequest(
                    correlation_id=str(uuid4()),
                    broker=Broker.IQ_OPTION,
                    account_id=IQOPTION_PRACTICE_ACCOUNT_ID,
                    product="BINARY_OPTION",
                    symbol=symbol,
                    direction=direction,
                    amount=Money(risk_config.stake_minor_units, risk_config.currency),
                    strategy_id=risk_config.strategy_id,
                    strategy_version="1.0.0",
                    deadline_at=datetime.now(UTC) + timedelta(seconds=15),
                    duration=1,
                    duration_unit="m",
                )
            )
            row = runtime.reader.one("orders", "order_id", persisted.order_id)
            if row is None:
                return _DispatchResult(
                    persisted.order_id,
                    None,
                    "IQOPTION_ORDER_PROJECTION_MISSING",
                )
            state = OrderState(str(row["state"]))
            if state in {OrderState.REJECTED, OrderState.SEND_BLOCKED}:
                outbox = runtime.reader.outbox_for_intent(persisted.intent_id)
                reason = None if outbox is None else outbox.get("state_reason")
                return _DispatchResult(
                    persisted.order_id,
                    state,
                    self._stable_rejection_reason(reason),
                )
            return _DispatchResult(
                persisted.order_id,
                state,
                f"IQOPTION_ORDER_{state.value}",
            )
        except Exception as exc:
            logger.warning("IQ Option Core submission failed: %s", type(exc).__name__)
            reason_code = str(getattr(exc, "reason_code", "")).strip()
            if not reason_code and isinstance(exc, RuntimeError):
                marker = "Health Gate blocked: "
                message = str(exc)
                if message.startswith(marker):
                    reason_code = message[len(marker) :].strip()
            return _DispatchResult(
                None,
                None,
                reason_code or "IQOPTION_ORDER_SUBMISSION_FAILED",
            )

    @staticmethod
    def _stable_rejection_reason(reason: object) -> str:
        raw = "" if reason is None else str(reason).strip()
        normalized = raw.lower()
        if "investment amount is smaller" in normalized or "allowed minimum" in normalized:
            return "IQOPTION_STAKE_BELOW_BROKER_MINIMUM"
        if any(
            marker in normalized
            for marker in (
                "active is suspended",
                "active suspended",
                "asset is suspended",
                "asset is not available",
                "active is not available",
                "instrument is not available",
                "instrument is closed",
                "market is closed",
            )
        ):
            return "IQOPTION_ACTIVE_SUSPENDED"
        if (
            raw
            and len(raw) <= 64
            and all(
                character.isupper() or character.isdigit() or character == "_" for character in raw
            )
        ):
            return raw
        return "IQOPTION_ORDER_REJECTED_REMOTE"

    @staticmethod
    def _fetch_candles(
        supervisor: ReadOnlyWorkerSupervisor,
        symbol: str,
        timeframe: int,
    ) -> list[MarketCandle]:
        _ticks, candles = supervisor.client.market_history(
            symbol,
            style="candles",
            count=20,
            timeframe_seconds=timeframe,
        )
        return [
            candle
            for candle in candles
            if candle.is_closed
            and candle.broker is Broker.IQ_OPTION
            and candle.broker_symbol == symbol
            and candle.timeframe_seconds == timeframe
        ]

    @staticmethod
    def _has_nonterminal_iq_order(runtime: CoreRuntime) -> bool:
        exposure_states = {
            OrderState.OUTBOXED.value,
            OrderState.DISPATCHING.value,
            OrderState.ACCEPTED.value,
            OrderState.OPEN.value,
            OrderState.UNKNOWN.value,
            OrderState.RECONCILING.value,
            OrderState.SETTLEMENT_UNKNOWN.value,
            OrderState.MANUAL_REVIEW.value,
        }
        return any(
            str(row.get("broker")) == Broker.IQ_OPTION.value
            and str(row.get("state")) in exposure_states
            for row in runtime.reader.list_nonterminal_orders()
        )

    def notify_order_event(
        self,
        event: BrokerOrderEvent,
        result: BrokerEventApplyResult,
    ) -> None:
        if (
            event.broker is not Broker.IQ_OPTION
            or result.status
            not in {BrokerEventApplyStatus.APPLIED, BrokerEventApplyStatus.APPLIED_WITH_GAP}
            or result.order_state is not OrderState.SETTLED
            or event.result_minor is None
        ):
            return
        config = self._risk_config_provider()
        pnl = Decimal(event.result_minor) / Decimal(100)
        with self._lock:
            self._daily_profit_loss += pnl
            if event.result_minor < 0:
                self._consecutive_losses += 1
                self._cooldown_until = self._monotonic() + config.cooldown_seconds_after_loss
                self._status_reason = "IQOPTION_ORDER_SETTLED_LOSS"
            else:
                self._consecutive_losses = 0
                self._cooldown_until = 0.0
                self._status_reason = "IQOPTION_ORDER_SETTLED_WIN"

    def _symbols_for_cycle(self, selected_symbol: str) -> tuple[tuple[str, str], ...]:
        if selected_symbol != "AUTO":
            return tuple(item for item in IQOPTION_RADAR_SYMBOLS if item[0] == selected_symbol)
        item = IQOPTION_RADAR_SYMBOLS[self._scan_cursor % len(IQOPTION_RADAR_SYMBOLS)]
        self._scan_cursor = (self._scan_cursor + 1) % len(IQOPTION_RADAR_SYMBOLS)
        return (item,)

    def _risk_block_reason(self, config: IqOptionRiskConfig) -> str | None:
        if self._daily_trades_count >= config.max_daily_trades:
            return "IQOPTION_MAX_TRADES_REACHED"
        stop_loss = Decimal(config.daily_stop_loss_minor_units) / Decimal(100)
        if self._daily_profit_loss <= -stop_loss:
            return "IQOPTION_STOP_LOSS_REACHED"
        take_profit = Decimal(config.daily_take_profit_minor_units) / Decimal(100)
        if self._daily_profit_loss >= take_profit:
            return "IQOPTION_TAKE_PROFIT_REACHED"
        if self._consecutive_losses >= config.max_consecutive_losses:
            return "IQOPTION_CONSECUTIVE_LOSS_LIMIT_REACHED"
        remaining = self._cooldown_until - self._monotonic()
        if remaining > 0:
            return f"IQOPTION_LOSS_COOLDOWN ({int(remaining) + 1}s)"
        return None

    def _update_rank(
        self,
        symbol: str,
        display_name: str,
        *,
        rsi: str,
        condition: str,
        selected: bool,
        status: str,
        direction: str | None = None,
    ) -> None:
        rank = UiIqOptionAssetRank(
            symbol=symbol,
            display_name=display_name,
            rsi=rsi,
            direction=direction,
            condition=condition,
            selected=selected,
            status=status,
        )
        with self._lock:
            self._asset_ranking_by_symbol[symbol] = rank
            self._asset_ranking = self._ordered_ranking()

    def _set_status(self, reason: str) -> None:
        with self._lock:
            self._status_reason = reason

    def _ordered_ranking(self) -> tuple[UiIqOptionAssetRank, ...]:
        return tuple(self._asset_ranking_by_symbol[symbol] for symbol, _ in IQOPTION_RADAR_SYMBOLS)


__all__ = [
    "IQOPTION_ACTIVE_SUSPENSION_COOLDOWN_SECONDS",
    "IQOPTION_PRACTICE_ACCOUNT_ID",
    "IQOPTION_RADAR_SYMBOLS",
    "IqOptionAutoTrader",
]
