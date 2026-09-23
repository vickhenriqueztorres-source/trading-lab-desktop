from __future__ import annotations

import threading
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from apps.core.execution_state import ExecutionState, OperatorIntentStore, TransportSupervisor
from apps.core.health import HealthState
from apps.core.iqoption_risk_config import IqOptionRiskConfig, IqOptionRiskConfigStore
from apps.core.lifecycle_service import CoreLifecycleService
from apps.core.worker_supervisor import WorkerHealthState
from packages.domain.market import BrokerAccountBalance
from packages.domain.models import Broker
from packages.observability.events import InMemoryEventSink
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
    service._iqoption_session_invalidated = False
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
    service._iqoption_session_invalidated = False
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
    service._iqoption_session_invalidated = False
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


def test_iqoption_bot_arms_pending_intent_while_unknown_order_reconciles(tmp_path) -> None:
    service = CoreLifecycleService.__new__(CoreLifecycleService)
    service._iqoption_session_invalidated = False
    service._iqoption_switch_lock = threading.RLock()
    service._iqoption_bot_armed = False
    service._iqoption_bot_reason = "IQOPTION_BOT_DISARMED"
    service._transport_supervisor = TransportSupervisor(intent_store=OperatorIntentStore(tmp_path))
    service._iqoption_auto_trader = MagicMock()
    service._schedule_iqoption_reconciliation = MagicMock()
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
    events = InMemoryEventSink()
    service._runtime = SimpleNamespace(
        resume_new_entries_for=MagicMock(return_value=False),
        health_gate=SimpleNamespace(
            state_for=lambda *_args: HealthState(False, "HG_ORDER_UNKNOWN")
        ),
        event_sink=events,
    )

    accepted, reason = service.control_iqoption_bot(True)

    assert accepted is True
    assert reason == "IQOPTION_BOT_ARMED_RECONCILING"
    assert service._iqoption_bot_armed is True
    assert service._transport_supervisor.armed_intent is True
    assert OperatorIntentStore(tmp_path).load_armed() is True
    service._iqoption_auto_trader.begin_new_run.assert_called_once_with()
    service._iqoption_auto_trader.start.assert_called_once_with()
    service._schedule_iqoption_reconciliation.assert_called_once_with()
    assert any(
        event.event_name == "iqoption_operator_intent_armed"
        and event.reason_code == "HG_ORDER_UNKNOWN"
        for event in events.events
    )


def test_iqoption_reconciliation_completion_resumes_armed_projection() -> None:
    service = CoreLifecycleService.__new__(CoreLifecycleService)
    service._iqoption_switch_lock = threading.RLock()
    service._iqoption_bot_armed = True
    service._iqoption_bot_reason = "IQOPTION_BOT_ARMED_RECONCILING"
    events = InMemoryEventSink()
    service._runtime = SimpleNamespace(
        health_gate=SimpleNamespace(state_for=lambda *_args: HealthState(True, None)),
        event_sink=events,
    )

    service._on_iqoption_reconciliation_completed()

    assert service._iqoption_bot_reason == "IQOPTION_BOT_ARMED"
    assert any(
        event.event_name == "iqoption_automatic_recovery_completed" for event in events.events
    )


def test_iqoption_bot_accepts_armed_degraded_intent_while_transport_is_down(
    tmp_path, monkeypatch
) -> None:
    class FakeVault:
        def __init__(self, _directory) -> None:
            pass

        @staticmethod
        def configured_account_mode() -> str:
            return "practice"

    monkeypatch.setattr("apps.core.lifecycle_service.IQOptionCredentialVault", FakeVault)
    service = CoreLifecycleService.__new__(CoreLifecycleService)
    service._profile_dir = tmp_path
    service._iqoption_session_invalidated = True
    service._iqoption_switch_lock = threading.RLock()
    service._iqoption = None
    service._iqoption_balance = None
    service._iqoption_bot_armed = False
    service._iqoption_bot_reason = "IQOPTION_BOT_DISARMED"
    service._transport_supervisor = TransportSupervisor(intent_store=OperatorIntentStore(tmp_path))
    service._iqoption_auto_trader = MagicMock()
    service._request_iqoption_recovery = MagicMock()
    scoped_resume = MagicMock(return_value=False)
    health_gate = SimpleNamespace(
        global_state=HealthState(True, None),
        state_for=lambda *_args: HealthState(False, "HG_WORKER_DISCONNECTED"),
    )
    events = InMemoryEventSink()
    service._runtime = SimpleNamespace(
        health_gate=health_gate,
        event_sink=events,
        resume_new_entries_for=scoped_resume,
        stop_new_entries_for=MagicMock(),
    )

    accepted, reason = service.control_iqoption_bot(True)

    assert accepted is True
    assert reason == "IQOPTION_BOT_ARMED_DEGRADED"
    assert service._iqoption_bot_armed is True
    assert service._transport_supervisor.state is ExecutionState.ARMED_DEGRADED
    assert OperatorIntentStore(tmp_path).load_armed() is True
    service._iqoption_auto_trader.on_transport_down.assert_called_once_with(
        "IQOPTION_CONNECTION_REQUIRED"
    )
    service._iqoption_auto_trader.start.assert_called_once_with()
    service._request_iqoption_recovery.assert_called_once_with("IQOPTION_OPERATOR_ARMED_DEGRADED")
    assert any(
        event.event_name == "iqoption_operator_intent_armed"
        and event.reason_code == "TRANSPORT_DOWN"
        for event in events.events
    )


def test_iqoption_degraded_arm_does_not_bypass_database_failure(tmp_path, monkeypatch) -> None:
    class FakeVault:
        def __init__(self, _directory) -> None:
            pass

        @staticmethod
        def configured_account_mode() -> str:
            return "practice"

    monkeypatch.setattr("apps.core.lifecycle_service.IQOptionCredentialVault", FakeVault)
    service = CoreLifecycleService.__new__(CoreLifecycleService)
    service._profile_dir = tmp_path
    service._iqoption_session_invalidated = True
    service._iqoption_switch_lock = threading.RLock()
    service._iqoption = None
    service._iqoption_balance = None
    service._iqoption_bot_armed = False
    service._iqoption_bot_reason = "IQOPTION_BOT_DISARMED"
    service._transport_supervisor = TransportSupervisor(intent_store=OperatorIntentStore(tmp_path))
    service._runtime = SimpleNamespace(
        health_gate=SimpleNamespace(global_state=HealthState(False, "DB_WRITE_FAILED"))
    )

    accepted, reason = service.control_iqoption_bot(True)

    assert accepted is False
    assert reason == "DB_WRITE_FAILED"
    assert service._transport_supervisor.state is ExecutionState.DISARMED
    assert not (tmp_path / "operator_intent.json").exists()


def test_shutdown_and_control_bot_false_disarms_and_resets_analyses(tmp_path) -> None:
    service = CoreLifecycleService.__new__(CoreLifecycleService)
    service._profile_dir = tmp_path
    service._iqoption_switch_lock = threading.RLock()
    service._deriv_account_id = None
    service._safe_stop = False
    service._ui_shutdown_requested = False
    intent_store = OperatorIntentStore(tmp_path)
    intent_store.save_armed(True)
    assert intent_store.load_armed() is True
    service._transport_supervisor = TransportSupervisor(intent_store=intent_store)
    service._transport_supervisor.arm()
    assert service._transport_supervisor.armed_intent is True
    service._iqoption_bot_armed = True
    service._iqoption_bot_reason = "IQOPTION_BOT_ARMED"

    mock_auto_trader = MagicMock()
    service._iqoption_auto_trader = mock_auto_trader
    mock_runtime = MagicMock()
    service._runtime = mock_runtime

    # When UI shutdown is requested:
    service._request_ui_shutdown()

    assert service._ui_shutdown_requested is True
    assert service._iqoption_bot_armed is False
    assert service._transport_supervisor.armed_intent is False
    # operator_intent.json must have armed=False
    assert intent_store.load_armed() is False
    # auto_trader.reset_market_analyses must have been called
    mock_auto_trader.reset_market_analyses.assert_called_once()
    mock_runtime.stop_new_entries.assert_called_once()


def test_update_iqoption_risk_config_accepts_local_strategies_with_any_asset(tmp_path) -> None:
    service = CoreLifecycleService.__new__(CoreLifecycleService)
    service._iqoption_switch_lock = threading.RLock()
    service._iqoption_bot_armed = False
    service._iqoption_risk_store = IqOptionRiskConfigStore(tmp_path)
    service._iqoption_risk_config = IqOptionRiskConfig()
    service._manifest_catalog = SimpleNamespace(active_strategies={})

    # Local strategy with specific asset
    cfg_pr = IqOptionRiskConfig(
        strategy_id="iqoption-pattern-reversal",
        symbol="EURUSD-OTC",
    )
    ok, err = service.update_iqoption_risk_config(cfg_pr)
    assert ok is True
    assert err is None
    assert service._iqoption_risk_config.strategy_id == "iqoption-pattern-reversal"
    assert service._iqoption_risk_config.symbol == "EURUSD-OTC"

    # Local strategy with AUTO asset
    cfg_lg = IqOptionRiskConfig(
        strategy_id="iqoption-liquidity-gap",
        symbol="AUTO",
        duration_seconds=120,
    )
    ok, err = service.update_iqoption_risk_config(cfg_lg)
    assert ok is True
    assert err is None
    assert service._iqoption_risk_config.strategy_id == "iqoption-liquidity-gap"
    assert service._iqoption_risk_config.symbol == "AUTO"

    # Manifest strategy with missing entry in catalog should return NO_CANDIDATE
    cfg_manifest = IqOptionRiskConfig(
        strategy_id="f5:custom",
        symbol="EURUSD-OTC",
    )
    ok, err = service.update_iqoption_risk_config(cfg_manifest)
    assert ok is False
    assert err == "NO_CANDIDATE"
