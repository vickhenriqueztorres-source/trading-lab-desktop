from __future__ import annotations

from decimal import Decimal

import pytest

from apps.core.iqoption_auto_trader import IqOptionAutoTrader
from apps.core.iqoption_risk_config import IqOptionRiskConfig
from apps.core.ui_service import _UI_LOG_FIELD_ALLOWLIST, _to_ui_operational_log
from packages.observability.events import OperationalEvent
from tests.unit.test_iqoption_candidates import NOW, catalog, entry


def test_ui_log_allowlist_contains_payout_and_decision_fields():
    assert "phase" in _UI_LOG_FIELD_ALLOWLIST
    assert "stage_rejected" in _UI_LOG_FIELD_ALLOWLIST
    assert "payout" in _UI_LOG_FIELD_ALLOWLIST
    assert "payout_min" in _UI_LOG_FIELD_ALLOWLIST
    assert "reason_code" in _UI_LOG_FIELD_ALLOWLIST
    assert "strategy_key" in _UI_LOG_FIELD_ALLOWLIST


def test_ui_log_formats_iqoption_decision_with_reason_and_details():
    event = OperationalEvent(
        event_name="iqoption_decision",
        occurred_at=NOW,
        reason_code="PAYOUT_BELOW_VALIDATED_EDGE",
        fields=(
            ("broker", "IQ_OPTION"),
            ("phase", "PAYOUT_GATE"),
            ("symbol", "USDCHF-OTC"),
            ("payout", "0.84"),
            ("payout_min", "0.85"),
            ("stage_rejected", "PAYOUT_BELOW_VALIDATED_EDGE"),
        ),
    )
    ui_log = _to_ui_operational_log(event)
    assert ui_log.source == "IQ_OPTION"
    assert ui_log.event_name == "iqoption_decision"
    assert ui_log.reason_code == "PAYOUT_BELOW_VALIDATED_EDGE"
    assert "payout=0.84" in (ui_log.detail or "")
    assert "payout_min=0.85" in (ui_log.detail or "")
    assert "phase=PAYOUT_GATE" in (ui_log.detail or "")


def test_practice_mode_permits_realistic_otc_payout():
    # Strategy demanding 0.85 payout in manifest
    manifest_entry = entry(
        "f1:USDCHF-OTC",
        asset="USDCHF-OTC",
        validated={
            "wilson_lower": "0.557",
            "p_min_at_validation": "0.541",
            "payout_min": "0.85",
        },
    )
    cat = catalog(manifest_entry)
    risk_config = IqOptionRiskConfig(symbol="AUTO", strategy_id="AUTO")

    trader = IqOptionAutoTrader.__new__(IqOptionAutoTrader)
    trader._account_type_provider = lambda: "PRACTICE"
    trader._operator_armed = lambda: True
    trader._monotonic = lambda: 100.0
    trader._utc_clock = lambda: NOW
    trader._catalog_provider = lambda: cat
    trader._risk_config_provider = lambda: risk_config
    trader._last_payout_gate = None
    trader._execution_ticket = None

    # Realistic OTC payout of 84% (0.84)
    payout = Decimal("0.84")
    context = trader._check_manifest_execution("USDCHF-OTC", "f1:USDCHF-OTC", payout)
    assert context is not None
    assert trader._last_payout_gate is not None
    assert trader._last_payout_gate["payout_allowed"] is True
    assert trader._last_payout_gate["payout"] == "0.84"
    assert trader._last_payout_gate["payout_min"] == "0.85"


def test_practice_mode_blocks_unacceptable_low_payout():
    # Strategy demanding 0.85 payout in manifest
    manifest_entry = entry(
        "f1:USDCHF-OTC",
        asset="USDCHF-OTC",
        validated={
            "wilson_lower": "0.557",
            "p_min_at_validation": "0.541",
            "payout_min": "0.85",
        },
    )
    cat = catalog(manifest_entry)
    risk_config = IqOptionRiskConfig(symbol="AUTO", strategy_id="AUTO")

    trader = IqOptionAutoTrader.__new__(IqOptionAutoTrader)
    trader._account_type_provider = lambda: "PRACTICE"
    trader._operator_armed = lambda: True
    trader._monotonic = lambda: 100.0
    trader._utc_clock = lambda: NOW
    trader._catalog_provider = lambda: cat
    trader._risk_config_provider = lambda: risk_config
    trader._last_payout_gate = None
    trader._execution_ticket = None

    # Unacceptable low payout of 50% (0.50)
    payout = Decimal("0.50")
    with pytest.raises(RuntimeError) as exc_info:
        trader._check_manifest_execution("USDCHF-OTC", "f1:USDCHF-OTC", payout)
    assert exc_info.value.args[0] == "PAYOUT_BELOW_VALIDATED_EDGE"


def test_real_mode_permits_realistic_otc_payout():
    # Strategy demanding 0.85 payout in manifest
    manifest_entry = entry(
        "f1:USDCHF-OTC",
        asset="USDCHF-OTC",
        validated={
            "wilson_lower": "0.557",
            "p_min_at_validation": "0.541",
            "payout_min": "0.85",
        },
    )
    cat = catalog(manifest_entry)
    risk_config = IqOptionRiskConfig(symbol="AUTO", strategy_id="AUTO")

    trader = IqOptionAutoTrader.__new__(IqOptionAutoTrader)
    trader._account_type_provider = lambda: "REAL"
    trader._operator_armed = lambda: True
    trader._monotonic = lambda: 100.0
    trader._utc_clock = lambda: NOW
    trader._catalog_provider = lambda: cat
    trader._risk_config_provider = lambda: risk_config
    trader._last_payout_gate = None
    trader._execution_ticket = None

    # Realistic OTC payout of 84% (0.84) in REAL mode
    payout = Decimal("0.84")
    context = trader._check_manifest_execution("USDCHF-OTC", "f1:USDCHF-OTC", payout)
    assert context is not None
    assert trader._last_payout_gate is not None
    assert trader._last_payout_gate["payout_allowed"] is True
    assert trader._last_payout_gate["payout"] == "0.84"
    assert trader._last_payout_gate["payout_min"] == "0.85"
