from __future__ import annotations

import threading
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from apps.core.iqoption_risk_config import IqOptionRiskConfig, IqOptionRiskConfigStore
from apps.core.lifecycle_service import CoreLifecycleService
from apps.core.worker_supervisor import WorkerHealthState
from packages.domain.market import BrokerAccountBalance
from packages.domain.models import Broker
from packages.protocol import UiIqOptionBotControlCommand, UiIqOptionRiskConfig


def test_iqoption_risk_config_round_trip_and_persistence(tmp_path) -> None:
    config = IqOptionRiskConfig(
        stake_minor_units=250,
        daily_stop_loss_minor_units=2_000,
        daily_take_profit_minor_units=3_000,
        max_daily_trades=8,
    )
    store = IqOptionRiskConfigStore(tmp_path)
    store.save(config)

    assert store.load() == config
    wire = UiIqOptionRiskConfig.from_payload(UiIqOptionRiskConfig().to_payload())
    assert wire.strategy_id == "iqoption-rsi-demo"
    assert wire.symbol == "EURUSD-OTC"


def test_legacy_subminimum_stake_is_migrated_to_broker_minimum(tmp_path) -> None:
    path = tmp_path / "iqoption-risk-config.json"
    path.write_text(
        '{"strategy_id":"iqoption-rsi-demo","symbol":"AUTO",'
        '"timeframe_seconds":60,"duration_seconds":60,"stake_minor_units":20,'
        '"daily_stop_loss_minor_units":1000,"daily_take_profit_minor_units":1000,'
        '"max_consecutive_losses":3,"cooldown_seconds_after_loss":30,'
        '"max_daily_trades":10,"max_concurrent_positions":1,"currency":"USD"}',
        encoding="utf-8",
    )

    migrated = IqOptionRiskConfigStore(tmp_path).load()

    assert migrated.stake_minor_units == 100
    assert '"stake_minor_units":100' in path.read_text(encoding="utf-8")


def test_iqoption_stake_below_broker_minimum_is_rejected() -> None:
    with pytest.raises(ValueError, match="IQOPTION_STAKE_INVALID"):
        IqOptionRiskConfig(stake_minor_units=99)


def test_iqoption_bot_command_requires_explicit_boolean() -> None:
    assert UiIqOptionBotControlCommand.from_payload({"enabled": True}).enabled is True


def test_iqoption_bot_fails_closed_for_read_only_practice_connector() -> None:
    service = CoreLifecycleService.__new__(CoreLifecycleService)
    service._iqoption_switch_lock = threading.RLock()
    service._iqoption_bot_armed = False
    service._iqoption_bot_reason = "IQOPTION_BOT_DISARMED"
    capabilities = SimpleNamespace(
        can_submit_orders=False,
        supports_market_data=False,
        supports_reconciliation=False,
        supports_order_events=False,
    )
    service._iqoption = SimpleNamespace(
        health_state=WorkerHealthState.READY,
        client=SimpleNamespace(capabilities=capabilities),
    )
    service._iqoption_balance = BrokerAccountBalance(
        10_000,
        "USD",
        "DEMO",
        datetime.now(UTC),
    )
    service._runtime = SimpleNamespace(
        resume_new_entries_for=lambda *_args: True,
    )

    accepted, reason = service.control_iqoption_bot(True)

    assert accepted is False
    assert reason == "IQOPTION_PRACTICE_TRADING_CAPABILITY_UNAVAILABLE"
    assert service._iqoption_bot_armed is False


def test_iqoption_real_is_never_armed_even_if_capabilities_claim_ready() -> None:
    service = CoreLifecycleService.__new__(CoreLifecycleService)
    service._iqoption_switch_lock = threading.RLock()
    service._iqoption_bot_armed = False
    service._iqoption_bot_reason = "IQOPTION_BOT_DISARMED"
    capabilities = SimpleNamespace(
        can_submit_orders=True,
        supports_market_data=True,
        supports_reconciliation=True,
        supports_order_events=True,
    )
    service._iqoption = SimpleNamespace(
        health_state=WorkerHealthState.READY,
        client=SimpleNamespace(capabilities=capabilities),
    )
    service._iqoption_balance = BrokerAccountBalance(
        10_000,
        "USD",
        "REAL",
        datetime.now(UTC),
    )

    accepted, reason = service.control_iqoption_bot(True)

    assert accepted is False
    assert reason == "IQOPTION_PRACTICE_REQUIRED"
    assert service._iqoption_bot_armed is False


def test_iqoption_bot_arms_successfully_in_practice_when_capabilities_ready() -> None:
    service = CoreLifecycleService.__new__(CoreLifecycleService)
    service._iqoption_switch_lock = threading.RLock()
    service._iqoption_bot_armed = False
    service._iqoption_bot_reason = "IQOPTION_BOT_DISARMED"
    capabilities = SimpleNamespace(
        can_submit_orders=True,
        supports_market_data=True,
        supports_reconciliation=True,
        supports_order_events=True,
    )
    service._iqoption = SimpleNamespace(
        health_state=WorkerHealthState.READY,
        client=SimpleNamespace(capabilities=capabilities),
    )
    service._iqoption_balance = BrokerAccountBalance(
        10_000,
        "USD",
        "DEMO",
        datetime.now(UTC),
    )
    service._safe_stop = True
    scoped_resume = MagicMock(return_value=True)
    service._runtime = SimpleNamespace(
        resume_new_entries_for=scoped_resume,
    )

    accepted, reason = service.control_iqoption_bot(True)

    assert accepted is True
    assert reason == "IQOPTION_BOT_ARMED"
    assert service._iqoption_bot_armed is True
    assert service._safe_stop is True
    scoped_resume.assert_called_once_with(Broker.IQ_OPTION, "IQOPTION_PRACTICE")
