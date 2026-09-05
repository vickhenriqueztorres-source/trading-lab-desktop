from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest

from apps.core.families import EvalResult, F4SqueezeBreak, F5Quadrant
from apps.core.iqoption_auto_trader import IqOptionAutoTrader
from apps.core.iqoption_connection_safety import IQOptionMessageBudget
from apps.core.iqoption_risk_config import IqOptionRiskConfig
from apps.core.manifest_catalog import parse_strategy_entry
from packages.domain.market import MarketCandle
from packages.domain.models import Broker, Direction, OrderRequest, OrderState


def _make_candles(
    prices: list[str],
    *,
    symbol: str = "EURUSD-OTC",
) -> list[MarketCandle]:
    base = datetime(2026, 1, 1, tzinfo=UTC)
    candles: list[MarketCandle] = []
    for index, raw_price in enumerate(prices):
        open_time = base + timedelta(minutes=index)
        price = Decimal(raw_price)
        candles.append(
            MarketCandle(
                broker=Broker.IQ_OPTION,
                broker_symbol=symbol,
                timeframe_seconds=60,
                open_time=open_time,
                close_time=open_time + timedelta(minutes=1),
                open=price,
                high=price + Decimal("0.0001"),
                low=price - Decimal("0.0001"),
                close=price,
                is_closed=True,
            )
        )
    return candles


class FakeReader:
    def __init__(self, state: OrderState, outbox_reason: str | None = None) -> None:
        self.state = state
        self.outbox_reason = outbox_reason

    def one(self, _table: str, _key: str, _value: str) -> dict[str, str]:
        return {"state": self.state.value}

    def outbox_for_intent(self, _intent_id: str) -> dict[str, str] | None:
        if self.outbox_reason is None:
            return None
        return {"state_reason": self.outbox_reason}

    def list_nonterminal_orders(self) -> list[dict[str, str]]:
        return []


class FakeHealthGate:
    def block_scope(self, *_args: str) -> None:
        return None

    def clear_scope(self, *_args: str) -> None:
        return None


class FakeRuntime:
    def __init__(
        self,
        state: OrderState = OrderState.ACCEPTED,
        outbox_reason: str | None = None,
    ) -> None:
        self.reader = FakeReader(state, outbox_reason)
        self.health_gate = FakeHealthGate()
        self.requests: list[OrderRequest] = []
        self.failure: Exception | None = None
        self.events: list[tuple[str, dict[str, object]]] = []
        self.event_sink = SimpleNamespace(
            emit=lambda name, **fields: self.events.append((name, fields))
        )

    def submit(self, request: OrderRequest) -> SimpleNamespace:
        self.requests.append(request)
        if self.failure is not None:
            raise self.failure
        return SimpleNamespace(order_id="iq-order-1", intent_id="iq-intent-1")


class FakeClient:
    def iqoption_binary_payout(self, symbol: str) -> Decimal:
        return Decimal("0.85")

    def __init__(self, candles: list[MarketCandle]) -> None:
        self.candles = candles
        self.market_requests: list[tuple[str, str, int, int]] = []

    def market_history(
        self,
        symbol: str,
        *,
        style: str,
        count: int,
        timeframe_seconds: int,
    ) -> tuple[list[object], list[MarketCandle]]:
        self.market_requests.append((symbol, style, count, timeframe_seconds))
        return [], self.candles


def _catalog_strategy(key: str, warmup: int) -> SimpleNamespace:
    return SimpleNamespace(
        status="approved",
        entry=parse_strategy_entry(
            {
                "key": key,
                "family": "F4" if warmup == 39 else "F5",
                "asset": "EURUSD-OTC",
                "timeframe": "M1",
                "status": "approved",
                "display_name_pt": key,
            }
        ),
        instance=(F4SqueezeBreak(key, {}) if warmup == 39 else F5Quadrant(key, {})),
    )


class FakeCatalog:
    def __init__(self, version: int, strategies: dict[str, SimpleNamespace]) -> None:
        self.manifest_version = version
        self.active_strategies = strategies

    def get_strategy(self, key):
        return self.active_strategies.get(key)

    def is_eligible(self, key, **kwargs):
        from apps.core.manifest_catalog import DynamicManifestCatalog

        catalog = DynamicManifestCatalog()
        catalog.apply_manifest(
            {"strategies": [info.entry for info in self.active_strategies.values()]}
        )
        return catalog.is_eligible(key, **kwargs)


def explicit_signal_catalog(symbols: tuple[str, ...]) -> FakeCatalog:
    """Controlled family signals for routing tests, not an implicit RSI fallback."""
    from dataclasses import replace

    entries = {}
    for symbol in symbols:
        key = f"f5:{symbol}"
        info = _catalog_strategy(key, 15)
        info.entry = replace(
            info.entry,
            asset=symbol,
            validated=replace(
                info.entry.validated,
                wilson_lower=Decimal("0.60"),
                p_min_at_validation=Decimal("0.55"),
                payout_min=Decimal("0.80"),
            ),
        )
        info.instance = SimpleNamespace(
            warmup_required=15,
            evaluate_detailed=lambda candles, context: EvalResult(
                Direction.CALL, "OK", len(candles), 15, None, None, None
            ),
        )
        entries[key] = info
    return FakeCatalog(1, entries)


def _falling_prices() -> list[str]:
    return [str(Decimal("1.1000") - Decimal(index) * Decimal("0.0010")) for index in range(20)]


def test_signal_uses_broker_candles_and_persists_through_core() -> None:
    client = FakeClient(_make_candles(_falling_prices()))
    runtime = FakeRuntime()
    trader = IqOptionAutoTrader(
        supervisor_provider=lambda: SimpleNamespace(client=client),
        runtime_provider=lambda: runtime,  # type: ignore[arg-type]
        risk_config_provider=lambda: IqOptionRiskConfig(symbol="EURUSD-OTC"),
        operator_armed=lambda: True,
    )

    trader._evaluate_cycle()

    assert client.market_requests == [("EURUSD-OTC", "candles", 18, 60)]
    assert len(runtime.requests) == 1
    request = runtime.requests[0]
    assert request.broker is Broker.IQ_OPTION
    assert request.direction is Direction.CALL
    assert request.product == "BINARY_OPTION"
    assert trader.status_reason.startswith("ORDEM_ACEITA:")

    trader._evaluate_cycle()
    assert len(runtime.requests) == 1
    assert trader.status_reason.startswith("SINAL_CONSUMIDO:")


def test_core_submission_failure_is_never_reported_as_success() -> None:
    client = FakeClient(_make_candles(_falling_prices()))
    runtime = FakeRuntime()
    runtime.failure = RuntimeError("dispatch failed")
    trader = IqOptionAutoTrader(
        supervisor_provider=lambda: SimpleNamespace(client=client),
        runtime_provider=lambda: runtime,  # type: ignore[arg-type]
        risk_config_provider=lambda: IqOptionRiskConfig(symbol="EURUSD-OTC"),
        operator_armed=lambda: True,
    )

    trader._evaluate_cycle()

    assert len(runtime.requests) == 1
    assert trader.status_reason == "IQOPTION_ORDER_SUBMISSION_FAILED"
    assert "ACEITA" not in trader.status_reason

    trader._evaluate_cycle()
    assert trader.status_reason == "IQOPTION_ORDER_SUBMISSION_FAILED"


def test_market_data_budget_blocks_request_before_internal_limit_is_exceeded() -> None:
    client = FakeClient(_make_candles(_falling_prices()))
    runtime = FakeRuntime()
    now = [0.0]
    trader = IqOptionAutoTrader(
        supervisor_provider=lambda: SimpleNamespace(client=client),
        runtime_provider=lambda: runtime,  # type: ignore[arg-type]
        risk_config_provider=lambda: IqOptionRiskConfig(symbol="EURUSD-OTC"),
        operator_armed=lambda: False,
        monotonic=lambda: now[0],
        message_budget=IQOptionMessageBudget(limit=1, pressure_at=1),
    )

    supervisor = SimpleNamespace(client=client)
    first = trader._candles_for_closed_interval(
        supervisor=supervisor,
        runtime=runtime,  # type: ignore[arg-type]
        symbol="EURUSD-OTC",
        timeframe=60,
        warmup_need=15,
    )
    second = trader._candles_for_closed_interval(
        supervisor=supervisor,
        runtime=runtime,  # type: ignore[arg-type]
        symbol="GBPUSD-OTC",
        timeframe=60,
        warmup_need=15,
    )

    assert len(client.market_requests) == 1
    assert first is not None
    assert second is None
    assert runtime.events == [
        (
            "iqoption_message_budget_pressure",
            {"used_in_window": 1, "limit": 1},
        )
    ]


def test_health_gate_failure_reason_remains_visible_after_scan_continues() -> None:
    client = FakeClient(_make_candles(_falling_prices()))
    runtime = FakeRuntime()
    runtime.failure = RuntimeError("Health Gate blocked: HG_AUTH_REQUIRED")
    trader = IqOptionAutoTrader(
        supervisor_provider=lambda: SimpleNamespace(client=client),
        runtime_provider=lambda: runtime,  # type: ignore[arg-type]
        risk_config_provider=lambda: IqOptionRiskConfig(symbol="EURUSD-OTC"),
        operator_armed=lambda: True,
    )

    trader._evaluate_cycle()
    assert trader.status_reason == "HG_AUTH_REQUIRED"

    trader._evaluate_cycle()
    assert len(runtime.requests) == 1
    assert trader.status_reason == "HG_AUTH_REQUIRED"


def test_remote_minimum_rejection_is_stable_and_blocks_more_scan_dispatches() -> None:
    client = FakeClient(_make_candles(_falling_prices()))
    runtime = FakeRuntime(
        OrderState.REJECTED,
        "Cannot purchase an option: your investment amount is smaller than the allowed minimum.",
    )
    trader = IqOptionAutoTrader(
        supervisor_provider=lambda: SimpleNamespace(client=client),
        runtime_provider=lambda: runtime,  # type: ignore[arg-type]
        risk_config_provider=lambda: IqOptionRiskConfig(symbol="EURUSD-OTC"),
        operator_armed=lambda: True,
    )

    trader._evaluate_cycle()
    trader._evaluate_cycle()

    assert len(runtime.requests) == 1
    assert trader.status_reason == "IQOPTION_STAKE_BELOW_BROKER_MINIMUM"

    trader.begin_new_run()
    trader._evaluate_cycle()
    assert len(runtime.requests) == 1  # ARM cannot erase a rejected signal/configuration.


@pytest.mark.parametrize(
    "broker_reason",
    [
        "Cannot purchase an option (active is suspended)",
        "Cannot purchase an option (the asset is not available at the moment).",
    ],
)
def test_suspended_or_unavailable_auto_asset_does_not_freeze_other_assets(
    broker_reason: str,
) -> None:
    class DynamicClient:
        def iqoption_binary_payout(self, symbol: str) -> Decimal:
            return Decimal("0.85")

        def __init__(self) -> None:
            self.market_requests: list[str] = []

        def market_history(
            self,
            symbol: str,
            *,
            style: str,
            count: int,
            timeframe_seconds: int,
        ) -> tuple[list[object], list[MarketCandle]]:
            assert (style, count, timeframe_seconds) == ("candles", 18, 60)
            self.market_requests.append(symbol)
            return [], _make_candles(_falling_prices(), symbol=symbol)

    client = DynamicClient()
    runtime = FakeRuntime(
        OrderState.REJECTED,
        broker_reason,
    )
    trader = IqOptionAutoTrader(
        supervisor_provider=lambda: SimpleNamespace(client=client),
        runtime_provider=lambda: runtime,  # type: ignore[arg-type]
        risk_config_provider=lambda: IqOptionRiskConfig(symbol="AUTO"),
        operator_armed=lambda: True,
        catalog_provider=lambda: explicit_signal_catalog(("EURUSD-OTC", "GBPUSD-OTC")),
        monitor_provider=lambda: SimpleNamespace(ready=True),
    )

    trader._evaluate_cycle()
    assert trader.status_reason.startswith("IQOPTION_ACTIVE_SUSPENDED")
    assert [request.symbol for request in runtime.requests] == ["EURUSD-OTC"]

    runtime.reader.state = OrderState.ACCEPTED
    runtime.reader.outbox_reason = None
    trader._evaluate_cycle()

    assert client.market_requests == ["EURUSD-OTC", "GBPUSD-OTC"]
    assert [request.symbol for request in runtime.requests] == ["EURUSD-OTC", "GBPUSD-OTC"]
    assert trader.status_reason.startswith("ORDEM_ACEITA: GBP/USD OTC")


def test_fetch_count_derives_from_active_strategies() -> None:
    client = FakeClient([])
    supervisor = SimpleNamespace(client=client)
    catalog = FakeCatalog(1, {"f4:test": _catalog_strategy("f4:test", 39)})
    config = [IqOptionRiskConfig(strategy_id="f4:test")]
    trader = IqOptionAutoTrader(
        supervisor_provider=lambda: supervisor,
        runtime_provider=lambda: FakeRuntime(),  # type: ignore[arg-type]
        risk_config_provider=lambda: config[0],
        operator_armed=lambda: False,
        catalog_provider=lambda: catalog,
    )

    trader._evaluate_cycle()

    catalog.manifest_version = 2
    catalog.active_strategies = {"f5:test": _catalog_strategy("f5:test", 15)}
    config[0] = IqOptionRiskConfig(strategy_id="f5:test")
    trader._evaluate_cycle()

    assert client.market_requests == [
        ("EURUSD-OTC", "candles", 42, 60),
        ("EURUSD-OTC", "candles", 18, 60),
    ]
    catalog.active_strategies["f4:test"] = _catalog_strategy("f4:test", 39)
    config[0] = IqOptionRiskConfig(symbol="AUTO")
    trader._evaluate_cycle()
    assert client.market_requests[-1] == ("EURUSD-OTC", "candles", 42, 60)


def test_no_active_strategy_does_not_fetch_market_data() -> None:
    client = FakeClient([])
    catalog = FakeCatalog(1, {})
    trader = IqOptionAutoTrader(
        supervisor_provider=lambda: SimpleNamespace(client=client),
        runtime_provider=lambda: FakeRuntime(),  # type: ignore[arg-type]
        risk_config_provider=lambda: IqOptionRiskConfig(strategy_id="f1:not-active"),
        operator_armed=lambda: False,
        catalog_provider=lambda: catalog,
    )

    trader._evaluate_cycle()

    assert client.market_requests == []
    assert trader.status_reason == "IQOPTION_BOT_DISARMED"
    assert trader.asset_ranking[0].condition == "NO_CANDIDATE"


def test_message_budget_unchanged_with_derived_window() -> None:
    client = FakeClient([])
    runtime = FakeRuntime()
    supervisor = SimpleNamespace(client=client)
    now = [120.0]
    trader = IqOptionAutoTrader(
        supervisor_provider=lambda: supervisor,
        runtime_provider=lambda: runtime,  # type: ignore[arg-type]
        risk_config_provider=IqOptionRiskConfig,
        operator_armed=lambda: False,
        monotonic=lambda: now[0],
        message_budget=IQOptionMessageBudget(limit=1, pressure_at=1),
    )

    first = trader._candles_for_closed_interval(
        supervisor=supervisor,
        runtime=runtime,  # type: ignore[arg-type]
        symbol="EURUSD-OTC",
        timeframe=60,
        warmup_need=39,
    )
    second = trader._candles_for_closed_interval(
        supervisor=supervisor,
        runtime=runtime,  # type: ignore[arg-type]
        symbol="EURUSD-OTC",
        timeframe=60,
        warmup_need=39,
    )

    assert first == second == []
    assert client.market_requests == [("EURUSD-OTC", "candles", 42, 60)]
    assert runtime.events == [
        (
            "iqoption_message_budget_pressure",
            {"used_in_window": 1, "limit": 1},
        )
    ]


def test_warming_up_reason_survives_end_of_armed_cycle() -> None:
    client = FakeClient(_make_candles(_falling_prices()))
    runtime = FakeRuntime()
    catalog = FakeCatalog(1, {"f4:test": _catalog_strategy("f4:test", 39)})
    trader = IqOptionAutoTrader(
        supervisor_provider=lambda: SimpleNamespace(client=client),
        runtime_provider=lambda: runtime,  # type: ignore[arg-type]
        risk_config_provider=lambda: IqOptionRiskConfig(strategy_id="f4:test"),
        operator_armed=lambda: True,
        catalog_provider=lambda: catalog,
    )
    trader._evaluate_cycle()
    assert trader.status_reason == "AQUECENDO 20/39"
    assert trader.asset_ranking[0].status == "WARMING_UP"
    assert runtime.requests == []


def test_worker_replacement_invalidates_candle_cache() -> None:
    first_client, replacement = FakeClient([]), FakeClient([])
    runtime = FakeRuntime()
    supervisor = SimpleNamespace(client=first_client)
    trader = IqOptionAutoTrader(
        supervisor_provider=lambda: supervisor,
        runtime_provider=lambda: runtime,  # type: ignore[arg-type]
        risk_config_provider=IqOptionRiskConfig,
        operator_armed=lambda: False,
        monotonic=lambda: 120.0,
    )
    for client in (first_client, replacement):
        supervisor.client = client
        trader._candles_for_closed_interval(
            supervisor=supervisor,
            runtime=runtime,  # type: ignore[arg-type]
            symbol="EURUSD-OTC",
            timeframe=60,
            warmup_need=39,
        )
    assert len(first_client.market_requests) == len(replacement.market_requests) == 1
