from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path

IQOPTION_RSI_STRATEGY_ID = "iqoption-rsi-demo"
IQOPTION_MIN_STAKE_MINOR_UNITS = 100
IQOPTION_ALLOWED_SYMBOLS = frozenset(
    {
        "AUTO",
        "EURUSD-OTC",
        "EURUSD",
        "GBPUSD-OTC",
        "GBPUSD",
        "USDJPY-OTC",
        "USDJPY",
        "AUDUSD-OTC",
        "AUDUSD",
        "EURJPY-OTC",
        "EURJPY",
        "GBPJPY-OTC",
        "GBPJPY",
        "AUDCAD-OTC",
        "AUDCAD",
        "NZDUSD-OTC",
        "NZDUSD",
        "USDCAD-OTC",
        "USDCAD",
        "USDCHF-OTC",
        "USDCHF",
    }
)


@dataclass(frozen=True, slots=True)
class IqOptionRiskConfig:
    """Persisted Practice-only controls for the IQ Option RSI laboratory."""

    strategy_id: str = IQOPTION_RSI_STRATEGY_ID
    symbol: str = "EURUSD-OTC"
    timeframe_seconds: int = 60
    duration_seconds: int = 60
    stake_minor_units: int = 100
    daily_stop_loss_minor_units: int = 1_000
    daily_take_profit_minor_units: int = 1_000
    max_consecutive_losses: int = 3
    cooldown_seconds_after_loss: int = 30
    max_daily_trades: int = 10
    max_concurrent_positions: int = 1
    currency: str = "USD"

    @property
    def active_strategy_key(self) -> str:
        """Canonical selector; strategy_id remains the legacy constructor/wire alias."""
        return self.strategy_id

    def __post_init__(self) -> None:
        if not self.strategy_id or len(self.strategy_id) > 128:
            raise ValueError("IQOPTION_STRATEGY_UNSUPPORTED")
        if self.strategy_id not in {
            IQOPTION_RSI_STRATEGY_ID,
            "AUTO",
        } and not self.strategy_id.startswith(("f1:", "f2:", "f3:", "f4:", "f5:")):
            raise ValueError("IQOPTION_STRATEGY_UNSUPPORTED")
        if self.symbol not in IQOPTION_ALLOWED_SYMBOLS:
            raise ValueError("IQOPTION_SYMBOL_UNSUPPORTED")
        # Deprecated input: manifest routing owns candle TF, not this saved hint.
        if self.timeframe_seconds not in {60, 300, 900} or self.duration_seconds != 60:
            raise ValueError("IQOPTION_RSI_INTERVAL_UNSUPPORTED")
        if (
            type(self.stake_minor_units) is not int
            or not IQOPTION_MIN_STAKE_MINOR_UNITS <= self.stake_minor_units <= 10_000
        ):
            raise ValueError("IQOPTION_STAKE_INVALID")
        for value in (self.daily_stop_loss_minor_units, self.daily_take_profit_minor_units):
            if type(value) is not int or value < self.stake_minor_units or value > 1_000_000:
                raise ValueError("IQOPTION_DAILY_LIMIT_INVALID")
        if (
            type(self.max_consecutive_losses) is not int
            or not 1 <= self.max_consecutive_losses <= 10
        ):
            raise ValueError("IQOPTION_CONSECUTIVE_LOSS_LIMIT_INVALID")
        if (
            type(self.cooldown_seconds_after_loss) is not int
            or not 0 <= self.cooldown_seconds_after_loss <= 3_600
        ):
            raise ValueError("IQOPTION_COOLDOWN_INVALID")
        if type(self.max_daily_trades) is not int or not 1 <= self.max_daily_trades <= 100:
            raise ValueError("IQOPTION_DAILY_TRADE_LIMIT_INVALID")
        if self.max_concurrent_positions != 1:
            raise ValueError("IQOPTION_SINGLE_POSITION_REQUIRED")
        if self.currency != "USD":
            raise ValueError("IQOPTION_CURRENCY_UNSUPPORTED")


class IqOptionRiskConfigStore:
    """Small atomic JSON store; it never contains credentials or account data."""

    def __init__(self, profile_dir: Path) -> None:
        self._path = Path(profile_dir) / "iqoption-risk-config.json"

    def load(self) -> IqOptionRiskConfig:
        if not self._path.exists():
            return IqOptionRiskConfig()
        try:
            payload = json.loads(self._path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("IQOPTION_RISK_CONFIG_INVALID")
            migrated = False
            if "active_strategy_key" in payload:
                key = payload.pop("active_strategy_key")
                if "strategy_id" in payload and payload["strategy_id"] != key:
                    raise ValueError("IQOPTION_STRATEGY_ALIAS_CONFLICT")
                payload["strategy_id"] = key
            stake = payload.get("stake_minor_units")
            if type(stake) is int and stake < IQOPTION_MIN_STAKE_MINOR_UNITS:
                payload["stake_minor_units"] = IQOPTION_MIN_STAKE_MINOR_UNITS
                migrated = True
            config = IqOptionRiskConfig(**payload)
            if migrated:
                self.save(config)
            return config
        except (OSError, TypeError, json.JSONDecodeError) as exc:
            raise ValueError("IQOPTION_RISK_CONFIG_INVALID") from exc

    def save(self, config: IqOptionRiskConfig) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._path.with_suffix(".tmp")
        payload = asdict(config)
        payload["active_strategy_key"] = payload.pop("strategy_id")
        temporary.write_text(
            json.dumps(payload, sort_keys=True, separators=(",", ":")),
            encoding="utf-8",
        )
        os.replace(temporary, self._path)
