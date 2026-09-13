from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace

from apps.core.deriv_auto_trader import DerivDigitAutoTrader
from apps.core.deriv_telemetry import DerivTelemetrySnapshot, DerivTelemetrySource
from apps.core.digit_risk_config import DigitRiskConfig
from apps.core.health import HealthGate
from apps.core.iqoption_auto_trader import (
    IQOPTION_PRACTICE_ACCOUNT_ID,
    IqOptionAutoTrader,
)
from apps.core.iqoption_risk_config import IqOptionRiskConfig
from apps.core.ui_service import CoreUiProjectionBuilder
from apps.core.worker_supervisor import WorkerHealthState
from packages.domain.market import (
    BrokerClockSnapshot,
    BrokerInstrument,
    BrokerInstrumentAvailability,
    BrokerInstrumentCatalog,
    BrokerInstrumentProduct,
    BrokerMarketKind,
)
from packages.domain.models import Broker, Money, OrderRequest
from packages.market_data import DigitFrequencySnapshot
from packages.strategies.deriv_digits import (
    DerivDigitStrategyId,
    DigitStrategyProjection,
    ShadowSignalState,
)


class _MockReader:
    def list_reconciliation_candidates(self) -> list[dict[str, object]]:
        return []

    def list_nonterminal_orders(self) -> list[dict[str, object]]:
        return []

    def list_reconciliation_orders(self) -> list[dict[str, object]]:
        return []

    def deriv_recent_strategy_settlements(self, *, limit_per_scope: int) -> list[dict[str, object]]:
        return []


def _mock_deriv_telemetry() -> DerivTelemetrySnapshot:
    signal = DigitStrategyProjection(
        strategy_id=DerivDigitStrategyId.TAIL_PROBABILITY_EDGE,
        display_name="Tail Probability Edge",
        markets="R_100 · 1 tick",
        lifecycle_status="PRACTICE_VALIDATION",
        signal_state=ShadowSignalState.SHADOW_SIGNAL,
        reason_code="TAIL_EDGE_SIGNAL",
        warmup_current=500,
        warmup_required=500,
        last_signal_epoch=123,
        last_signal_symbol="R_100",
        last_contract_type="DIGITOVER",
        last_direction="OVER",
        last_barrier=2,
        estimated_probability_pct=Decimal("75"),
        required_probability_pct=Decimal("72"),
        analysis_latency_microseconds=5,
    )
    return DerivTelemetrySnapshot(
        DerivTelemetrySource.DEMO_LIVE,
        "DEMO",
        True,
        None,
        BrokerClockSnapshot(
            1_900_000_000,
            datetime(2030, 3, 17, tzinfo=UTC),
            0.01,
            Decimal("0.1"),
        ),
        None,
        DigitFrequencySnapshot(
            "R_100",
            500,
            (50,) * 10,
            tuple(Decimal("10") for _ in range(10)),
            0,
        ),
        (signal,),
    )


class _MockRiskLedger:
    def __init__(self) -> None:
        self.digit_config = DigitRiskConfig(auto_select_symbol=False)

    def digit_entry_stake(self, _health_gate: object) -> Money:
        return Money(self.digit_config.stake_minor_units, self.digit_config.currency)


def test_health_gate_active_blockers_for_isolates_brokers() -> None:
    gate = HealthGate()
    gate.block_scope(Broker.IQ_OPTION.value, IQOPTION_PRACTICE_ACCOUNT_ID, "HG_SAFE_STOP")
    gate.block_scope(Broker.IQ_OPTION.value, "market-data", "HG_MARKET_DATA_DISCONNECTED")
    gate.block_scope(Broker.DERIV.value, "VRTC1001", "HG_DAILY_STOP_REACHED")

    deriv_blockers = gate.active_blockers_for(Broker.DERIV.value, "VRTC1001")
    assert "HG_SAFE_STOP" not in deriv_blockers
    assert "HG_MARKET_DATA_DISCONNECTED" not in deriv_blockers
    assert "HG_DAILY_STOP_REACHED" in deriv_blockers

    iq_blockers = gate.active_blockers_for(Broker.IQ_OPTION.value, IQOPTION_PRACTICE_ACCOUNT_ID)
    assert "HG_SAFE_STOP" in iq_blockers
    assert "HG_MARKET_DATA_DISCONNECTED" in iq_blockers
    assert "HG_DAILY_STOP_REACHED" not in iq_blockers


def test_deriv_unblocked_when_iqoption_safe_stop_active() -> None:
    gate = HealthGate()
    # IQ Option is stopped/disarmed in its scope
    gate.block_scope(Broker.IQ_OPTION.value, IQOPTION_PRACTICE_ACCOUNT_ID, "HG_SAFE_STOP")
    gate.block_scope(Broker.IQ_OPTION.value, "market-data", "MD_CLOCK_UNTRUSTED")

    requests: list[OrderRequest] = []
    runtime = SimpleNamespace(
        health_gate=gate,
        dispatcher_started=True,
        reader=_MockReader(),
        risk_ledger=_MockRiskLedger(),
        submit=lambda req: requests.append(req),
        event_sink=SimpleNamespace(emit=lambda *args, **kwargs: None),
    )

    trader = DerivDigitAutoTrader(
        runtime,  # type: ignore[arg-type]
        "VRTC1001",
        _mock_deriv_telemetry,
        monotonic_clock=lambda: 10.0,
        operator_armed=lambda: True,
    )

    # Deriv's own scoped state is open
    deriv_state = gate.state_for(Broker.DERIV.value, "VRTC1001")
    assert deriv_state.is_open is True
    # Whole-system gate.state is False because of IQ Option
    assert gate.state.is_open is False

    # Trader evaluate_once must NOT skip with BOT_DISABLED_OR_HEALTH_BLOCKED
    trader.reload_runtime_caches()
    result = trader.evaluate_once()
    assert result is True
    assert len(requests) == 1
    assert trader.last_reason != "BOT_DISABLED_OR_HEALTH_BLOCKED"


def test_deriv_trading_readiness_isolated_from_iqoption_blockers() -> None:
    gate = HealthGate()
    # Simulate IQ Option having multiple serious scoped blockers
    gate.block_scope(Broker.IQ_OPTION.value, IQOPTION_PRACTICE_ACCOUNT_ID, "HG_SAFE_STOP")
    gate.block_scope(Broker.IQ_OPTION.value, "market-data", "HG_MARKET_DATA_DISCONNECTED")
    gate.block_scope(Broker.IQ_OPTION.value, "market-data", "MD_CLOCK_UNTRUSTED")

    runtime = SimpleNamespace(
        health_gate=gate,
        reader=_MockReader(),
        safe_stop_active=False,
        dispatcher_started=True,
    )

    builder = CoreUiProjectionBuilder(
        runtime,  # type: ignore[arg-type]
        deriv_health=lambda: WorkerHealthState.READY,
        deriv_telemetry=_mock_deriv_telemetry,
    )

    readiness = builder.trading_readiness()
    assert readiness.core_available is True
    assert readiness.market_healthy is True
    assert readiness.risk_ready is True
    assert readiness.clock_trusted is True
    assert readiness.safe_stop is False
    assert readiness.armed is True
    assert readiness.ready_to_arm is True
    assert readiness.ready_to_trade is True
    assert "HG_SAFE_STOP" not in readiness.blocking_reasons
    assert "HG_MARKET_DATA_DISCONNECTED" not in readiness.blocking_reasons


def test_iqoption_unblocked_when_deriv_safe_stop_active() -> None:
    gate = HealthGate()
    # Deriv is disarmed/stopped
    gate.block_scope(Broker.DERIV.value, "VRTC1001", "HG_SAFE_STOP")

    # IQ Option entry authority is completely unhindered
    allowed, blocker = gate.can_enter_order(Broker.IQ_OPTION.value, IQOPTION_PRACTICE_ACCOUNT_ID)
    assert allowed is True
    assert blocker is None

    iq_state = gate.state_for(Broker.IQ_OPTION.value, IQOPTION_PRACTICE_ACCOUNT_ID)
    assert iq_state.is_open is True


def test_global_blocker_fails_closed_for_both_brokers() -> None:
    gate = HealthGate()
    gate.block("DB_SCHEMA_CORRUPT")

    deriv_allowed, deriv_blocker = gate.can_enter_order(Broker.DERIV.value, "VRTC1001")
    assert deriv_allowed is False
    assert deriv_blocker == "DB_SCHEMA_CORRUPT"

    iq_allowed, iq_blocker = gate.can_enter_order(
        Broker.IQ_OPTION.value, IQOPTION_PRACTICE_ACCOUNT_ID
    )
    assert iq_allowed is False
    assert iq_blocker == "DB_SCHEMA_CORRUPT"

    assert "DB_SCHEMA_CORRUPT" in gate.active_blockers_for(Broker.DERIV.value)
    assert "DB_SCHEMA_CORRUPT" in gate.active_blockers_for(Broker.IQ_OPTION.value)


def test_iqoption_symbols_for_cycle_status_closed_vs_unsupported() -> None:
    current_config = [IqOptionRiskConfig(symbol="AUTO")]
    closed_instrument = BrokerInstrument(
        broker=Broker.IQ_OPTION,
        broker_id="1",
        broker_symbol="EURUSD",
        display_name="EUR/USD",
        product=BrokerInstrumentProduct.TURBO,
        market_kind=BrokerMarketKind.REGULAR,
        availability=BrokerInstrumentAvailability.CLOSED,
        duration_seconds=(60,),
        detectable=True,
        analyzable=True,
        quotable=False,
        executable=False,
    )
    catalog = BrokerInstrumentCatalog(
        generation=1,
        observed_at_utc=datetime.now(UTC),
        instruments=(closed_instrument,),
    )
    client = SimpleNamespace(
        iqoption_instrument_catalog=lambda: catalog,
    )
    supervisor = SimpleNamespace(client=client)
    runtime = SimpleNamespace(
        health_gate=HealthGate(),
        event_sink=SimpleNamespace(emit=lambda *args, **kwargs: None),
    )
    trader = IqOptionAutoTrader(
        supervisor_provider=lambda: supervisor,
        runtime_provider=lambda: runtime,  # type: ignore[arg-type]
        risk_config_provider=lambda: current_config[0],
        operator_armed=lambda: True,
        execution_flags_provider=lambda: SimpleNamespace(
            legacy_entries_enabled=True,
            incremental_entries_enabled=True,
            fingerprint="test",
        ),
    )

    # 1. AUTO mode with no open markets -> IQOPTION_ALL_MARKETS_CLOSED
    current_config[0] = IqOptionRiskConfig(symbol="AUTO")
    trader._evaluate_cycle()
    assert trader.status_reason == "IQOPTION_ALL_MARKETS_CLOSED"

    # 2. Selected symbol in catalog but closed -> IQOPTION_MARKET_CLOSED
    current_config[0] = IqOptionRiskConfig(symbol="EURUSD")
    trader._evaluate_cycle()
    assert trader.status_reason == "IQOPTION_MARKET_CLOSED"

    # 3. Selected symbol missing from broker catalog -> IQOPTION_SYMBOL_UNSUPPORTED
    current_config[0] = IqOptionRiskConfig(symbol="GBPUSD")
    trader._evaluate_cycle()
    assert trader.status_reason == "IQOPTION_SYMBOL_UNSUPPORTED"
