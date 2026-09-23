from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

from apps.core.iqoption_auto_trader import IQOPTION_RADAR_SYMBOLS, IqOptionAutoTrader
from apps.core.iqoption_risk_config import IqOptionRiskConfig
from packages.domain.market import (
    BrokerInstrument,
    BrokerInstrumentAvailability,
    BrokerInstrumentCatalog,
    BrokerInstrumentProduct,
    BrokerMarketKind,
    MarketCandle,
)
from packages.domain.models import Broker, Direction, OrderState
from packages.protocol.ui_messages import (
    UiIqOptionAssetRank,
)
from tests.unit.test_iqoption_auto_trader import _safe_entry_clock, explicit_signal_catalog


def _make_candles_for_symbol(
    symbol: str, prices: list[float], timeframe: int = 60
) -> list[MarketCandle]:
    now = datetime.now(UTC)
    candles = []
    for i, p in enumerate(prices):
        open_time = now - timedelta(seconds=(len(prices) - i) * timeframe)
        close_time = open_time + timedelta(seconds=timeframe)
        dec_p = Decimal(str(p))
        candles.append(
            MarketCandle(
                broker=Broker.IQ_OPTION,
                broker_symbol=symbol,
                timeframe_seconds=timeframe,
                open_time=open_time,
                close_time=close_time,
                open=dec_p,
                high=dec_p + Decimal("0.00010"),
                low=dec_p - Decimal("0.00010"),
                close=dec_p,
                is_closed=True,
            )
        )
    return candles


def test_iqoption_radar_scans_all_assets_and_selects_triggered():
    orders_submitted = []

    class FakeClient:
        def iqoption_binary_payout(self, symbol):
            return Decimal("0.85")

    fake_supervisor = SimpleNamespace(client=FakeClient())

    # Mode: AUTO (All assets)
    risk_config = IqOptionRiskConfig(
        stake_minor_units=100,
        max_daily_trades=10,
        symbol="AUTO",
    )

    armed = True
    reader = SimpleNamespace(
        one=lambda *_args: {"state": OrderState.ACCEPTED.value},
        outbox_for_intent=lambda *_args: None,
        list_nonterminal_orders=lambda: [],
    )
    health_gate = SimpleNamespace(
        block_scope=lambda *_args: None,
        clear_scope=lambda *_args: None,
    )

    def submit(request):
        orders_submitted.append(request)
        return SimpleNamespace(order_id="iq-order-1", intent_id="iq-intent-1")

    runtime = SimpleNamespace(
        reader=reader,
        health_gate=health_gate,
        submit=submit,
        event_sink=SimpleNamespace(emit=lambda *a, **kw: None),
    )
    trader = IqOptionAutoTrader(
        supervisor_provider=lambda: fake_supervisor,
        runtime_provider=lambda: runtime,
        risk_config_provider=lambda: risk_config,
        operator_armed=lambda: armed,
        utc_clock=_safe_entry_clock,
        evaluation_interval_seconds=0.01,
        catalog_provider=lambda: explicit_signal_catalog(("GBPUSD-OTC",)),
        monitor_provider=lambda: SimpleNamespace(ready=True),
    )

    # Mock candle provider:
    # Let "GBPUSD-OTC" have falling prices (oversold -> CALL)
    # Others have flat neutral prices
    def custom_candles(symbol: str, tf: int):
        if symbol == "GBPUSD-OTC":
            falling = [1.2500 - (i * 0.0010) for i in range(20)]
            return _make_candles_for_symbol(symbol, falling, tf)
        flat = [1.0850 + ((i % 2) * 0.00001) for i in range(20)]
        return _make_candles_for_symbol(symbol, flat, tf)

    trader._fetch_candles = lambda _supervisor, symbol, tf, *, warmup_need: custom_candles(
        symbol, tf
    )
    for _ in IQOPTION_RADAR_SYMBOLS:
        trader._evaluate_cycle()

    # Verify all radar symbols are present in ranking
    ranking = trader.asset_ranking
    assert len(ranking) == len(IQOPTION_RADAR_SYMBOLS)

    # Check that GBPUSD-OTC was detected and triggered
    gbp_rank = next(item for item in ranking if item.symbol == "GBPUSD-OTC")
    assert gbp_rank.direction == "CALL"
    assert gbp_rank.condition == "OVERSOLD"
    assert gbp_rank.status == "TRIGGERED"
    assert gbp_rank.selected is True

    # Order must have been submitted immediately on GBPUSD-OTC
    assert len(orders_submitted) == 1
    assert orders_submitted[0].symbol == "GBPUSD-OTC"
    assert orders_submitted[0].direction is Direction.CALL


def test_ui_iqoption_asset_rank_roundtrip():
    rank = UiIqOptionAssetRank(
        symbol="EURUSD-OTC",
        display_name="EUR/USD OTC",
        rsi="28.4",
        direction="CALL",
        condition="OVERSOLD",
        selected=True,
        status="TRIGGERED",
    )
    payload = rank.to_payload()
    recovered = UiIqOptionAssetRank.from_payload(payload)
    assert recovered == rank


def test_dynamic_radar_executes_only_open_turbo_and_exposes_digital_read_only():
    trader = IqOptionAutoTrader(
        supervisor_provider=lambda: None,
        runtime_provider=lambda: None,
        risk_config_provider=IqOptionRiskConfig,
        operator_armed=lambda: False,
        catalog_provider=lambda: explicit_signal_catalog(("EURUSD", "GBPUSD-OTC")),
    )
    catalog = BrokerInstrumentCatalog(
        generation=1,
        observed_at_utc=datetime.now(UTC),
        instruments=(
            BrokerInstrument(
                Broker.IQ_OPTION,
                "777",
                "EURUSD",
                "EUR/USD",
                BrokerInstrumentProduct.TURBO,
                BrokerMarketKind.REGULAR,
                BrokerInstrumentAvailability.OPEN,
                (60,),
                True,
                True,
                True,
                True,
            ),
            BrokerInstrument(
                Broker.IQ_OPTION,
                "778",
                "GBPUSD-OTC",
                "GBP/USD OTC",
                BrokerInstrumentProduct.TURBO,
                BrokerMarketKind.OTC,
                BrokerInstrumentAvailability.SUSPENDED,
                (60,),
                True,
                True,
                False,
                False,
            ),
            BrokerInstrument(
                Broker.IQ_OPTION,
                "901",
                "EURUSD",
                "EUR/USD",
                BrokerInstrumentProduct.DIGITAL,
                BrokerMarketKind.REGULAR,
                BrokerInstrumentAvailability.OPEN,
                (),
                True,
                False,
                False,
                False,
            ),
        ),
    )
    trader._instrument_catalog = catalog
    trader._sync_catalog_ranking(catalog)

    assert trader._symbols_for_cycle("AUTO") == (("EURUSD", "EUR/USD · DIGITAL/TURBO"),)
    assert trader._symbols_for_cycle("GBPUSD-OTC") == ()
    eur = next(item for item in trader.asset_ranking if item.symbol == "EURUSD")
    assert "DIGITAL: OPEN · somente detecção" in eur.candidate_details


def test_dynamic_radar_hides_broker_assets_without_signed_strategy():
    trader = IqOptionAutoTrader(
        supervisor_provider=lambda: None,
        runtime_provider=lambda: None,
        risk_config_provider=IqOptionRiskConfig,
        operator_armed=lambda: False,
        catalog_provider=lambda: explicit_signal_catalog(("EURUSD",)),
    )
    catalog = BrokerInstrumentCatalog(
        generation=1,
        observed_at_utc=datetime.now(UTC),
        instruments=(
            BrokerInstrument(
                Broker.IQ_OPTION,
                "1",
                "EURUSD",
                "EUR/USD",
                BrokerInstrumentProduct.TURBO,
                BrokerMarketKind.REGULAR,
                BrokerInstrumentAvailability.OPEN,
                (60,),
                True,
                True,
                True,
                True,
            ),
            BrokerInstrument(
                Broker.IQ_OPTION,
                "41",
                "AIG-OP",
                "AIG-OP",
                BrokerInstrumentProduct.BINARY,
                BrokerMarketKind.REGULAR,
                BrokerInstrumentAvailability.OPEN,
                (),
                True,
                False,
                False,
                False,
            ),
        ),
    )

    trader._sync_catalog_ranking(catalog)

    assert tuple(item.symbol for item in trader.asset_ranking) == ("EURUSD",)


def test_dynamic_radar_with_empty_manifest_fails_closed_instead_of_showing_all_assets():
    empty_manifest = SimpleNamespace(active_strategies={})
    trader = IqOptionAutoTrader(
        supervisor_provider=lambda: None,
        runtime_provider=lambda: None,
        risk_config_provider=IqOptionRiskConfig,
        operator_armed=lambda: False,
        catalog_provider=lambda: empty_manifest,
    )
    catalog = BrokerInstrumentCatalog(
        generation=1,
        observed_at_utc=datetime.now(UTC),
        instruments=(
            BrokerInstrument(
                Broker.IQ_OPTION,
                "1",
                "EURUSD",
                "EUR/USD",
                BrokerInstrumentProduct.TURBO,
                BrokerMarketKind.REGULAR,
                BrokerInstrumentAvailability.OPEN,
                (60,),
                True,
                True,
                True,
                True,
            ),
        ),
    )

    trader._instrument_catalog = catalog
    trader._sync_catalog_ranking(catalog)

    assert trader.asset_ranking == ()
    assert trader._executable_symbols() == ()


def test_single_asset_timeout_does_not_stop_radar_or_block_other_assets():
    orders_submitted = []

    class FakeClient:
        def iqoption_binary_payout(self, symbol):
            return Decimal("0.85")

    fake_supervisor = SimpleNamespace(client=FakeClient())

    risk_config = IqOptionRiskConfig(
        stake_minor_units=100,
        max_daily_trades=10,
        symbol="AUTO",
    )

    blocked_scopes = set()
    health_gate = SimpleNamespace(
        block_scope=lambda broker, scope, reason: blocked_scopes.add(reason),
        clear_scope=lambda broker, scope, reason: blocked_scopes.discard(reason),
    )
    reader = SimpleNamespace(
        one=lambda *_args: {"state": OrderState.ACCEPTED.value},
        outbox_for_intent=lambda *_args: None,
        list_nonterminal_orders=lambda: [],
    )

    def submit(request):
        orders_submitted.append(request)
        return SimpleNamespace(order_id="iq-order-2", intent_id="iq-intent-2")

    runtime = SimpleNamespace(
        reader=reader,
        health_gate=health_gate,
        submit=submit,
        event_sink=SimpleNamespace(emit=lambda *a, **kw: None),
    )
    trader = IqOptionAutoTrader(
        supervisor_provider=lambda: fake_supervisor,
        runtime_provider=lambda: runtime,
        risk_config_provider=lambda: risk_config,
        operator_armed=lambda: True,
        utc_clock=_safe_entry_clock,
        evaluation_interval_seconds=0.01,
        catalog_provider=lambda: explicit_signal_catalog(("EURUSD-OTC", "GBPUSD-OTC")),
        monitor_provider=lambda: SimpleNamespace(ready=True),
    )

    from apps.core.execution_state import ExecutionState
    from apps.core.worker_client import DeliveryCertainty, WorkerDispatchError
    from packages.protocol.errors import ProtocolErrorCode

    def custom_candles(symbol: str, tf: int):
        if symbol == "EURUSD-OTC":
            raise WorkerDispatchError(
                ProtocolErrorCode.IQOPTION_REQUEST_TIMEOUT,
                DeliveryCertainty.NOT_SENT,
                "Request timeout on get-candles",
            )
        falling = [1.2500 - (i * 0.0010) for i in range(20)]
        return _make_candles_for_symbol(symbol, falling, tf)

    trader._fetch_candles = lambda _supervisor, symbol, tf, *, warmup_need: custom_candles(
        symbol, tf
    )

    # Cycle 1: Scans EURUSD-OTC which times out / fails to respond
    trader._evaluate_cycle()

    # Verify system did NOT halt, did NOT degrade transport, and did NOT block health gate
    assert trader._transport_supervisor.state is ExecutionState.ARMED
    assert trader.status_reason != "TRANSPORT_DOWN"
    assert "HG_MARKET_DATA_DISCONNECTED" not in blocked_scopes

    ranking = trader.asset_ranking
    eur_rank = next(item for item in ranking if item.symbol == "EURUSD-OTC")
    assert eur_rank.condition == "SEM_RESPOSTA"
    assert eur_rank.status == "TIMEOUT"
    assert eur_rank.rsi == "--"

    # Cycle 2: Scans GBPUSD-OTC which returns valid candles and triggers CALL
    trader._evaluate_cycle()

    assert len(orders_submitted) == 1
    assert orders_submitted[0].symbol == "GBPUSD-OTC"
    assert orders_submitted[0].direction is Direction.CALL

    gbp_rank = next(item for item in trader.asset_ranking if item.symbol == "GBPUSD-OTC")
    assert gbp_rank.direction == "CALL"
    assert gbp_rank.condition == "OVERSOLD"


def test_single_asset_mode_timeout_continues_analyzing_on_next_cycle():
    orders_submitted = []

    class FakeClient:
        def iqoption_binary_payout(self, symbol):
            return Decimal("0.85")

    fake_supervisor = SimpleNamespace(client=FakeClient())

    risk_config = IqOptionRiskConfig(
        stake_minor_units=100,
        max_daily_trades=10,
        symbol="EURUSD-OTC",
    )

    reader = SimpleNamespace(
        one=lambda *_args: {"state": OrderState.ACCEPTED.value},
        outbox_for_intent=lambda *_args: None,
        list_nonterminal_orders=lambda: [],
    )

    def submit(request):
        orders_submitted.append(request)
        return SimpleNamespace(order_id="iq-order-3", intent_id="iq-intent-3")

    runtime = SimpleNamespace(
        reader=reader,
        health_gate=SimpleNamespace(block_scope=lambda *a: None, clear_scope=lambda *a: None),
        submit=submit,
        event_sink=SimpleNamespace(emit=lambda *a, **kw: None),
    )
    trader = IqOptionAutoTrader(
        supervisor_provider=lambda: fake_supervisor,
        runtime_provider=lambda: runtime,
        risk_config_provider=lambda: risk_config,
        operator_armed=lambda: True,
        utc_clock=_safe_entry_clock,
        evaluation_interval_seconds=0.01,
        catalog_provider=lambda: explicit_signal_catalog(("EURUSD-OTC",)),
        monitor_provider=lambda: SimpleNamespace(ready=True),
    )

    from apps.core.execution_state import ExecutionState

    # Attempt 1: Timeout / connection failure
    trader._fetch_candles = lambda *_args, **_kwargs: (_ for _ in ()).throw(
        TimeoutError("Connection dropped by broker")
    )
    trader._evaluate_cycle()

    # System must NOT stop; transport must remain ARMED
    assert trader._transport_supervisor.state is ExecutionState.ARMED
    assert trader.status_reason == "IQOPTION_MARKET_DATA_UNAVAILABLE"
    assert len(orders_submitted) == 0

    # Attempt 2: Next cycle connection works and prices trigger CALL
    falling = [1.2500 - (i * 0.0010) for i in range(20)]
    trader._fetch_candles = lambda *_args, **_kwargs: _make_candles_for_symbol(
        "EURUSD-OTC", falling
    )
    trader._evaluate_cycle()

    assert len(orders_submitted) == 1
    assert orders_submitted[0].symbol == "EURUSD-OTC"
    assert orders_submitted[0].direction is Direction.CALL


def test_armed_degraded_self_heals_when_client_clock_responds():
    from apps.core.execution_state import ExecutionState
    from packages.domain.market import BrokerClockSnapshot

    now = datetime.now(UTC)
    clock_snapshot = BrokerClockSnapshot(
        server_epoch=int(now.timestamp()),
        local_received_at=now,
        round_trip_seconds=0.05,
        estimated_offset_seconds=Decimal("0.0"),
        connection_generation=1,
        source_age_seconds=0.05,
    )

    class FakeClient:
        def broker_clock(self):
            return clock_snapshot

    fake_supervisor = SimpleNamespace(client=FakeClient())
    trader = IqOptionAutoTrader(
        supervisor_provider=lambda: fake_supervisor,
        runtime_provider=lambda: SimpleNamespace(
            event_sink=SimpleNamespace(emit=lambda *a, **kw: None),
            health_gate=SimpleNamespace(block_scope=lambda *a: None, clear_scope=lambda *a: None),
        ),
        risk_config_provider=lambda: IqOptionRiskConfig(symbol="AUTO"),
        operator_armed=lambda: True,
        utc_clock=_safe_entry_clock,
    )

    # Force degraded transport state
    trader.on_transport_down("TEST_GLITCH")
    assert trader._transport_supervisor.state is ExecutionState.ARMED_DEGRADED

    # Cycle evaluates and automatically recovers because broker_clock responds
    trader._evaluate_cycle()
    assert trader._transport_supervisor.state is ExecutionState.ARMED
