from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

from apps.core.iqoption_auto_trader import IQOPTION_RADAR_SYMBOLS, IqOptionAutoTrader
from apps.core.iqoption_risk_config import IqOptionRiskConfig
from packages.domain.market import MarketCandle
from packages.domain.models import Broker, Direction, OrderState
from packages.protocol.ui_messages import (
    UiIqOptionAssetRank,
)
from tests.unit.test_iqoption_auto_trader import explicit_signal_catalog


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
