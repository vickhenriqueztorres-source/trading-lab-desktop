from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from apps.core.iqoption_auto_trader import IqOptionAutoTrader
from apps.core.iqoption_risk_config import IqOptionRiskConfig
from apps.core.manifest_catalog import DynamicManifestCatalog
from packages.brokers.iqoption.community_read_only import (
    IQOptionAccountMode,
    IQOptionCommunityReadOnlySession,
)
from packages.domain.market import (
    BrokerInstrument,
    BrokerInstrumentAvailability,
    BrokerInstrumentCatalog,
    BrokerInstrumentProduct,
    BrokerMarketKind,
)
from packages.domain.models import Broker
from packages.security import SecretValue


def test_manifest_catalog_ensure_asset_strategy_synthesizes_for_new_open_market_asset() -> None:
    now = datetime(2026, 9, 15, 12, 0, tzinfo=UTC)
    catalog = DynamicManifestCatalog(utc_clock=lambda: now)
    assert "f1:USDCAD:M1:00-24:rsi_bollinger" not in catalog.active_strategies

    info = catalog.ensure_asset_strategy("USDCAD")
    assert info.entry.asset == "USDCAD"
    assert info.status == "approved"
    assert info.entry.timeframe == "M1"
    assert info.entry.hours_utc == (0, 24)
    assert "f1:USDCAD:M1:00-24:rsi_bollinger" in catalog.active_strategies

    # Calling again returns the existing instance without duplicating
    again = catalog.ensure_asset_strategy("USDCAD")
    assert again is info


def test_get_binary_payout_resolves_from_turbo_and_falls_back_to_binary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = IQOptionCommunityReadOnlySession(
        "operator@example.invalid", SecretValue("demo-secret"), IQOptionAccountMode.PRACTICE
    )

    fake_init_response = {
        "name": "initialization-data",
        "msg": {
            "turbo": {
                "actives": {
                    "1": {
                        "name": "front.EURUSD",
                        "enabled": True,
                        "is_suspended": False,
                        "option": {"profit": {"commission": "15"}},
                    },
                    "76": {
                        "name": "front.EURUSD-OTC",
                        "enabled": True,
                        "is_suspended": False,
                        "option": {"profit": {"commission": "12"}},
                    },
                }
            },
            "binary": {
                "actives": {
                    "100": {
                        "name": "front.USDCAD",
                        "enabled": True,
                        "is_suspended": False,
                        "option": {"profit": {"commission": "18"}},
                    }
                }
            },
        },
    }

    monkeypatch.setattr(session, "_request_initialization", lambda _timeout: fake_init_response)

    # 1. Resolves EURUSD from turbo: (100 - 15) / 100 = 0.85
    assert session.get_binary_payout("EURUSD") == Decimal("0.85")

    # 2. Resolves EURUSD-OTC from turbo: (100 - 12) / 100 = 0.88
    assert session.get_binary_payout("EURUSD-OTC") == Decimal("0.88")

    # 3. Resolves USDCAD from binary fallback: (100 - 18) / 100 = 0.82
    assert session.get_binary_payout("USDCAD") == Decimal("0.82")


def test_auto_trader_realtime_discovers_open_market_and_otc_assets() -> None:
    now = datetime(2026, 9, 15, 14, 0, tzinfo=UTC)
    manifest_cat = DynamicManifestCatalog(utc_clock=lambda: now)

    trader = IqOptionAutoTrader(
        supervisor_provider=lambda: None,
        runtime_provider=lambda: None,
        risk_config_provider=IqOptionRiskConfig,
        operator_armed=lambda: False,
        catalog_provider=lambda: manifest_cat,
    )

    broker_catalog = BrokerInstrumentCatalog(
        generation=1,
        observed_at_utc=now,
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
                "100",
                "USDCAD",
                "USD/CAD",
                BrokerInstrumentProduct.BINARY,
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
                "76",
                "EURUSD-OTC",
                "EUR/USD OTC",
                BrokerInstrumentProduct.TURBO,
                BrokerMarketKind.OTC,
                BrokerInstrumentAvailability.OPEN,
                (60,),
                True,
                True,
                True,
                True,
            ),
            BrokerInstrument(
                Broker.IQ_OPTION,
                "6",
                "USDJPY",
                "USD/JPY",
                BrokerInstrumentProduct.TURBO,
                BrokerMarketKind.REGULAR,
                BrokerInstrumentAvailability.CLOSED,
                (60,),
                True,
                True,
                False,
                False,
            ),
        ),
    )

    trader._instrument_catalog = broker_catalog
    trader._sync_catalog_ranking(broker_catalog)

    executable = trader._executable_symbols()
    executable_symbols = [sym for sym, _ in executable]

    # Open market (EURUSD, USDCAD) and OTC (EURUSD-OTC) are all discovered and executable!
    assert "EURUSD" in executable_symbols
    assert "USDCAD" in executable_symbols
    assert "EURUSD-OTC" in executable_symbols
    # Closed market (USDJPY) is not in executable
    assert "USDJPY" not in executable_symbols

    # Ranking includes all items with proper status
    ranks = {r.symbol: r for r in trader.asset_ranking}
    assert ranks["EURUSD"].readiness == "READY"
    assert ranks["USDCAD"].readiness == "READY"
    assert ranks["EURUSD-OTC"].readiness == "READY"
    assert ranks["USDJPY"].readiness == "READ_ONLY"
    assert ranks["USDJPY"].condition == "MARKET_CLOSED"
