"""24h synthetic AUTO routing replay; no broker, prices, orders or return claims."""

from collections import Counter
from datetime import UTC, datetime, timedelta

from apps.core.iqoption_auto_trader import IQOPTION_RADAR_SYMBOLS
from apps.core.iqoption_candidates import resolve_candidates
from tests.unit.test_iqoption_candidates import catalog, entry


def test_auto_routing_24h(capsys):
    cat = catalog(
        entry("f5:otc-m1", hours_utc=[8, 16]),
        entry("f5:otc-m5", timeframe="M5", hours_utc=[8, 16]),
        entry("f5:spot", asset="EURUSD", hours_utc=[12, 20]),
    )
    start = datetime(2026, 9, 3, tzinfo=UTC)
    counts = {symbol: Counter() for symbol, _ in IQOPTION_RADAR_SYMBOLS}
    for minute in range(1440):
        for symbol in counts:
            choices, rejected = resolve_candidates(
                catalog=cat,
                symbol=symbol,
                mode="AUTO",
                active_strategy_key=None,
                account_type="PRACTICE",
                now_utc=start + timedelta(minutes=minute),
            )
            counts[symbol]["NO_CANDIDATE"] += not choices
            for reason in {"ASSET_MISMATCH", "OUTSIDE_HOURS"}:
                counts[symbol][reason] += reason in rejected.values()
            counts[symbol]["CANDIDATE"] += bool(choices)
    for symbol, row in counts.items():
        matching = symbol in {"EURUSD", "EURUSD-OTC"}
        assert row["NO_CANDIDATE"] == (960 if matching else 1440)
        assert row["OUTSIDE_HOURS"] == (960 if matching else 0)
        assert row["ASSET_MISMATCH"] == 1440
        assert row["CANDIDATE"] == (480 if matching else 0)
    with capsys.disabled():
        print("\n24h AUTO: symbol | NO_CANDIDATE | ASSET_MISMATCH | OUTSIDE_HOURS")
        for symbol, row in counts.items():
            print(symbol, row["NO_CANDIDATE"], row["ASSET_MISMATCH"], row["OUTSIDE_HOURS"])
