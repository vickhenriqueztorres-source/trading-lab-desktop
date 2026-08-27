from __future__ import annotations

import json
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

from apps.core.digit_risk_config import DigitRiskConfig
from apps.core.digit_risk_store import DigitRiskConfigStore


def test_digit_risk_store_round_trips_martingale_configuration(tmp_path: Path) -> None:
    store = DigitRiskConfigStore(tmp_path / "core" / "digit_risk_config.json")
    config = replace(
        DigitRiskConfig(),
        martingale_enabled=True,
        martingale_multiplier=Decimal("2.00"),
        martingale_max_steps=2,
        martingale_max_stake_minor_units=400,
        max_consecutive_losses=3,
    )

    store.save(config)

    assert store.load() == config


def test_digit_risk_store_fails_closed_to_defaults_for_corrupt_content(tmp_path: Path) -> None:
    path = tmp_path / "digit_risk_config.json"
    path.write_text("not-json", encoding="utf-8")

    assert DigitRiskConfigStore(path).load() == DigitRiskConfig()


def test_digit_risk_store_migrates_existing_profile_to_demo_auto_selection(
    tmp_path: Path,
) -> None:
    path = tmp_path / "digit_risk_config.json"
    config = DigitRiskConfig()
    payload = {
        "schema_version": 1,
        "stake_minor_units": config.stake_minor_units,
        "daily_stop_loss_minor_units": config.daily_stop_loss_minor_units,
        "daily_take_profit_minor_units": config.daily_take_profit_minor_units,
        "max_consecutive_losses": config.max_consecutive_losses,
        "cooldown_seconds_after_loss": config.cooldown_seconds_after_loss,
        "min_quantum_confidence_pct": str(config.min_quantum_confidence_pct),
        "selected_symbol": config.selected_symbol,
        "currency": config.currency,
        "martingale_enabled": config.martingale_enabled,
        "martingale_multiplier": str(config.martingale_multiplier),
        "martingale_max_steps": config.martingale_max_steps,
        "martingale_max_stake_minor_units": config.martingale_max_stake_minor_units,
    }
    path.write_text(json.dumps(payload), encoding="utf-8")

    assert DigitRiskConfigStore(path).load().auto_select_symbol is True
