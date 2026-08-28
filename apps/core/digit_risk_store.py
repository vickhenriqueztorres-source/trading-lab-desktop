from __future__ import annotations

import json
import os
from decimal import Decimal
from pathlib import Path
from tempfile import NamedTemporaryFile

from apps.core.digit_risk_config import DigitRiskConfig, validate_digit_risk_config


class DigitRiskConfigStore:
    """Small atomic Core-owned store for the operator's active digit risk configuration."""

    def __init__(self, path: Path) -> None:
        self._path = path

    def load(self) -> DigitRiskConfig:
        if not self._path.exists():
            return DigitRiskConfig()
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict) or raw.get("schema_version") != 1:
                raise ValueError("unsupported digit risk config schema")
            config = DigitRiskConfig(
                stake_minor_units=raw["stake_minor_units"],
                daily_stop_loss_minor_units=raw["daily_stop_loss_minor_units"],
                daily_take_profit_minor_units=raw["daily_take_profit_minor_units"],
                max_consecutive_losses=raw["max_consecutive_losses"],
                cooldown_seconds_after_loss=raw["cooldown_seconds_after_loss"],
                min_quantum_confidence_pct=Decimal(raw["min_quantum_confidence_pct"]),
                selected_symbol=raw["selected_symbol"],
                currency=raw["currency"],
                auto_select_symbol=raw.get("auto_select_symbol", True),
                active_strategy_id=raw.get("active_strategy_id", "tail-probability-edge"),
                enabled_strategy_ids=frozenset(
                    raw.get(
                        "enabled_strategy_ids",
                        (raw.get("active_strategy_id", "tail-probability-edge"),),
                    )
                ),
                stress_test_all_strategies_enabled=raw.get(
                    "stress_test_all_strategies_enabled", False
                ),
                martingale_enabled=raw["martingale_enabled"],
                martingale_multiplier=Decimal(raw["martingale_multiplier"]),
                martingale_max_steps=raw["martingale_max_steps"],
                martingale_max_stake_minor_units=raw["martingale_max_stake_minor_units"],
            )
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
            return DigitRiskConfig()
        valid, _reason = validate_digit_risk_config(config)
        return config if valid else DigitRiskConfig()

    def save(self, config: DigitRiskConfig) -> None:
        valid, reason = validate_digit_risk_config(config)
        if not valid:
            raise ValueError(reason or "invalid digit risk configuration")
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
            "auto_select_symbol": config.auto_select_symbol,
            "active_strategy_id": config.active_strategy_id,
            "enabled_strategy_ids": sorted(config.enabled_strategy_ids),
            "stress_test_all_strategies_enabled": config.stress_test_all_strategies_enabled,
            "martingale_enabled": config.martingale_enabled,
            "martingale_multiplier": str(config.martingale_multiplier),
            "martingale_max_steps": config.martingale_max_steps,
            "martingale_max_stake_minor_units": config.martingale_max_stake_minor_units,
        }
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temporary_name: str | None = None
        try:
            with NamedTemporaryFile(
                "w",
                encoding="utf-8",
                dir=self._path.parent,
                prefix=f".{self._path.name}.",
                suffix=".tmp",
                delete=False,
            ) as temporary:
                temporary_name = temporary.name
                json.dump(payload, temporary, sort_keys=True, separators=(",", ":"))
                temporary.flush()
                os.fsync(temporary.fileno())
            os.replace(temporary_name, self._path)
        finally:
            if temporary_name is not None:
                Path(temporary_name).unlink(missing_ok=True)
