from __future__ import annotations

from pathlib import Path

import pytest

from packages.brokers.deriv import DerivCandleAdapter, DerivCandleIngressBridge
from packages.domain.models import Broker
from packages.market_data import CandleIngress, CandleIngressStatus, InMemoryCandleStore
from packages.persistence.candle_repository import SqliteCandleRepository
from packages.persistence.strategy_data import StrategyDataDatabase


def deriv_event(**changes: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "symbol": "R_100",
        "granularity": 60,
        "epoch": 1_700_000_000,
        "open": "123.450",
        "high": "124.000",
        "low": "123.000",
        "close": "123.750",
        "is_closed": True,
        "source_event_id": "deriv-candle-1",
        "received_at_ms": 1_700_000_060_005,
    }
    payload.update(changes)
    return payload


def test_deriv_closed_candle_maps_to_canonical_model() -> None:
    candle = DerivCandleAdapter(frozenset({"R_100"})).convert(deriv_event())

    assert candle is not None
    assert candle.broker is Broker.DERIV
    assert candle.symbol == "R_100"
    assert candle.timeframe_seconds == 60
    assert candle.open_time_ms == 1_700_000_000_000
    assert candle.close_time_ms == 1_700_000_060_000
    assert candle.price_scale == 1_000
    assert candle.price_units == (123_450, 124_000, 123_000, 123_750)


def test_partial_duplicate_timestamp_and_bridge_are_fail_safe() -> None:
    adapter = DerivCandleAdapter(frozenset({"R_100"}))
    assert adapter.convert(deriv_event(is_closed=False)) is None
    first = adapter.convert(deriv_event(source_event_id="delivery-a"))
    second = adapter.convert(deriv_event(source_event_id="delivery-b"))
    assert first is not None and second is not None
    assert first.candle_id == second.candle_id

    bridge = DerivCandleIngressBridge(
        adapter,
        CandleIngress(InMemoryCandleStore(max_candles=4)),
    )
    accepted = bridge.ingest(deriv_event(source_event_id="delivery-a"))
    duplicate = bridge.ingest(deriv_event(source_event_id="delivery-b"))
    assert accepted is not None and accepted.status is CandleIngressStatus.ACCEPTED
    assert duplicate is not None and duplicate.status is CandleIngressStatus.DUPLICATE


def test_invalid_ohlc_unconfirmed_close_and_unknown_symbol_are_rejected() -> None:
    adapter = DerivCandleAdapter(frozenset({"R_100"}))
    with pytest.raises(ValueError, match="OHLC"):
        adapter.convert(deriv_event(low="125.000"))
    with pytest.raises(ValueError, match="CLOSE_NOT_CONFIRMED"):
        adapter.convert(deriv_event(received_at_ms=1_700_000_059_999))
    with pytest.raises(ValueError, match="SYMBOL_NOT_ALLOWED"):
        adapter.convert(deriv_event(symbol="UNKNOWN"))


def test_adapter_bridge_can_feed_persistent_ingress_without_financial_path(
    tmp_path: Path,
) -> None:
    database = StrategyDataDatabase(tmp_path / "strategy_data.db")
    repository = SqliteCandleRepository(database)
    bridge = DerivCandleIngressBridge(
        DerivCandleAdapter(frozenset({"R_100"})),
        CandleIngress(repository),
    )
    try:
        result = bridge.ingest(deriv_event())
        assert result is not None and result.status is CandleIngressStatus.ACCEPTED
        assert result.candle is not None
        assert repository.get(result.candle.candle_id) == result.candle
    finally:
        database.close()


def test_adapter_source_has_no_execution_or_credential_dependency() -> None:
    source = Path(__file__).parents[2] / "packages" / "brokers" / "deriv" / "candle_adapter.py"
    text = source.read_text(encoding="utf-8").casefold()
    forbidden = (
        "riskledger",
        "portfolioallocator",
        "orderintent",
        "submit_order",
        "buy",
        "sell",
        "credential",
        "token",
        "websocket",
        "apps.deriv_worker",
    )
    assert not any(value in text for value in forbidden)
