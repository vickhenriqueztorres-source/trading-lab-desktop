"""Integration test for strategy selection and execution in AutoTrader."""

from __future__ import annotations

from apps.core.iqoption_auto_trader import IqOptionAutoTrader
from apps.core.iqoption_risk_config import IqOptionRiskConfig
from apps.core.manifest_catalog import DynamicManifestCatalog


def test_iqoption_auto_trader_executes_catalog_family_strategy() -> None:
    catalog = DynamicManifestCatalog()
    catalog.apply_manifest(
        {
            "manifest_version": 1,
            "strategies": [
                {
                    "key": "f1:EURUSD-OTC:M1:00-24:test",
                    "family": "F1",
                    "display_name_pt": "F1 Test",
                    "asset": "EURUSD-OTC",
                    "timeframe": "M1",
                    "hours_utc": [0, 24],
                    "params": {
                        "rsi_period": 14,
                        "rsi_overbought": 70,
                        "rsi_oversold": 30,
                        "bb_period": 20,
                        "bb_std": 2.0,
                    },
                    "validated": {
                        "p_hat": "0.66",
                        "wilson_lower": "0.63",
                        "p_min_at_validation": "0.55",
                        "payout_min": "0.85",
                        "ops_per_day": "20",
                        "worst_streak": 4,
                        "result_1000_ops_stake10": "2000",
                        "score": "0.75",
                    },
                    "status": "approved",
                }
            ],
        }
    )

    risk_config = IqOptionRiskConfig(
        strategy_id="f1:EURUSD-OTC:M1:00-24:test",
        symbol="EURUSD-OTC",
    )

    trader = IqOptionAutoTrader(
        supervisor_provider=lambda: None,
        runtime_provider=lambda: None,
        risk_config_provider=lambda: risk_config,
        operator_armed=lambda: True,
        catalog_provider=lambda: catalog,
    )

    assert trader._catalog_provider is not None
    active = catalog.active_strategies.get(risk_config.strategy_id)
    assert active is not None
    assert active.instance.family_name == "F1"
