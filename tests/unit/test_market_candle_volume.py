from datetime import UTC, datetime, timedelta
from decimal import Decimal

from packages.domain.market import MarketCandle
from packages.domain.models import Broker


def test_market_candle_tick_volume_roundtrip_and_legacy_default() -> None:
    opened = datetime(2026, 9, 3, 12, 0, tzinfo=UTC)
    candle = MarketCandle(
        broker=Broker.IQ_OPTION,
        broker_symbol="EURUSD-OTC",
        timeframe_seconds=60,
        open_time=opened,
        close_time=opened + timedelta(minutes=1),
        open=Decimal("1.1000"),
        high=Decimal("1.1010"),
        low=Decimal("1.0990"),
        close=Decimal("1.1005"),
        is_closed=True,
        tick_volume=37,
    )

    assert MarketCandle.from_payload(candle.to_payload()) == candle

    legacy_payload = candle.to_payload()
    legacy_payload.pop("tick_volume")
    assert MarketCandle.from_payload(legacy_payload).tick_volume is None
