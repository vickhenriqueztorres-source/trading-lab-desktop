from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime

import pytest

from apps.ui.view_model import DashboardViewModel
from packages.protocol import (
    MAX_FRAME_SIZE,
    BrokerCardStatus,
    HealthGateStatus,
    OrderSummary,
    ProtocolError,
    UiAccountMode,
    UiBotWaitingStatus,
    UiDerivAssetRank,
    UiDerivStrategyStatus,
    UiGlobalState,
    UiLogLevel,
    UiMultiStrategyMetrics,
    UiOperationalLogEntry,
    UiProjectionSnapshot,
)


def _snapshot() -> UiProjectionSnapshot:
    return UiProjectionSnapshot(
        UiGlobalState.SAFE_STOPPED,
        True,
        (
            HealthGateStatus(
                "GLOBAL_ENTRY_GATE",
                False,
                "HG_SAFE_STOP",
                "Entradas pausadas; acompanhamento preservado.",
            ),
        ),
        (BrokerCardStatus("SIMULATED", UiAccountMode.PRACTICE, True, None, None, False),),
        (
            OrderSummary(
                "order-1",
                "SIMULATED",
                "R_100",
                "BUY",
                1234,
                "USD",
                "OPEN",
                datetime(2026, 8, 21, tzinfo=UTC),
            ),
        ),
        -25,
        "USD",
        deriv_strategies=(
            UiDerivStrategyStatus(
                "tail-probability-edge",
                "Tail Probability Edge",
                "R_100 · 1 tick",
                "RESEARCH_SHADOW",
                "MONITORING",
                "TAIL_EDGE_NO_CONSERVATIVE_ADVANTAGE",
                500,
                500,
                signals_emitted_total=7,
                signals_executed_total=2,
                signals_lost_to_arbitration_total=5,
                analysis_latency_microseconds_p95=120,
                conditional_sample=249,
            ),
        ),
        deriv_asset_ranking=(
            UiDerivAssetRank(
                "R_100",
                "CANDIDATE",
                "ASSET_SHADOW_CANDIDATE",
                500,
                500,
                selected=True,
                strategy_id="tail-probability-edge",
                contract_type="DIGITOVER",
                barrier=4,
                estimated_probability_pct="75.00",
                required_probability_pct="72.00",
                conservative_margin_pct="3.00",
                analysis_latency_microseconds=9,
            ),
        ),
        deriv_bot_waiting_status=UiBotWaitingStatus(
            "BOT_WAITING_FOR_NEW_TICK",
            "Aguardando um novo tick após o acionamento.",
            7,
            "R_100",
            123,
            True,
        ),
        multi_strategy_metrics=UiMultiStrategyMetrics(
            12.5,
            5,
            3,
            8,
            900,
        ),
        iqoption_entry_ready=False,
        iqoption_entry_blocker="PAYOUT_UNAVAILABLE",
        operational_logs=(
            UiOperationalLogEntry(
                occurred_at_utc=datetime(2026, 9, 9, 12, 30, tzinfo=UTC),
                level=UiLogLevel.WARNING,
                source="IQOPTION",
                event_name="health_gate_blocked",
                reason_code="MD_CLOCK_UNTRUSTED",
                detail="symbol=EURUSD-OTC",
            ),
        ),
    )


def test_ui_projection_round_trip_and_view_model_use_minor_units() -> None:
    snapshot = UiProjectionSnapshot.from_payload(_snapshot().to_payload())
    view = DashboardViewModel.from_snapshot(snapshot)

    assert view.global_state == "SAFE_STOPPED"
    assert view.daily_pnl == "USD -0.25"
    assert "USD 12.34" in view.order_lines[0]
    assert "INDISPONÍVEL" in view.broker_lines[0]
    assert view.can_resume is True
    assert snapshot.deriv_strategies[0].strategy_id == "tail-probability-edge"
    assert snapshot.deriv_strategies[0].signals_executed_total == 2
    assert snapshot.deriv_strategies[0].signals_lost_to_arbitration_total == 5
    assert snapshot.deriv_asset_ranking[0].selected is True
    assert snapshot.deriv_bot_waiting_status is not None
    assert snapshot.deriv_bot_waiting_status.waiting_since_seconds == 7
    assert snapshot.deriv_bot_waiting_status.rearm_notice is True
    assert snapshot.multi_strategy_metrics is not None
    assert snapshot.multi_strategy_metrics.active_engines == 5
    assert snapshot.deriv_bot_armed is False
    assert snapshot.iqoption_entry_ready is False
    assert snapshot.iqoption_entry_blocker == "PAYOUT_UNAVAILABLE"
    assert snapshot.operational_logs[0].reason_code == "MD_CLOCK_UNTRUSTED"


def test_ui_projection_rejects_unbounded_or_multiline_operational_logs() -> None:
    entry = UiOperationalLogEntry(
        datetime(2026, 9, 9, 12, 30, tzinfo=UTC),
        UiLogLevel.INFO,
        "CORE",
        "core_started",
    )
    with pytest.raises(ValueError, match="count is outside bounds"):
        UiProjectionSnapshot(
            UiGlobalState.READY,
            False,
            (HealthGateStatus("GLOBAL_ENTRY_GATE", True, None, "Ready"),),
            (BrokerCardStatus("SIMULATED", UiAccountMode.PRACTICE, True, None, None, False),),
            (),
            0,
            None,
            operational_logs=(entry,) * 161,
        )
    with pytest.raises(ValueError, match="text is invalid"):
        UiOperationalLogEntry(
            datetime(2026, 9, 9, 12, 30, tzinfo=UTC),
            UiLogLevel.ERROR,
            "CORE",
            "core_failed",
            detail="line one\nline two",
        )
    with pytest.raises(TypeError, match="level is invalid"):
        UiOperationalLogEntry(
            datetime(2026, 9, 9, 12, 30, tzinfo=UTC),
            "ERROR",  # type: ignore[arg-type]
            "CORE",
            "core_failed",
        )


def test_maximum_operational_log_projection_stays_inside_ipc_frame() -> None:
    entry = UiOperationalLogEntry(
        datetime(2026, 9, 9, 12, 30, tzinfo=UTC),
        UiLogLevel.WARNING,
        "IQOPTION",
        "iqoption_operational_warning",
        "IQOPTION_PAYOUT_UNAVAILABLE",
        "symbol=EURUSD-OTC status=" + "A" * 340,
    )
    snapshot = replace(_snapshot(), operational_logs=(entry,) * 160)

    encoded_payload = json.dumps(snapshot.to_payload(), separators=(",", ":")).encode()

    assert len(encoded_payload) < MAX_FRAME_SIZE


def test_legacy_projection_infers_deriv_state_only_when_explicit_field_is_absent() -> None:
    payload = _snapshot().to_payload()
    payload["safe_stop_active"] = False
    payload.pop("deriv_bot_armed")

    snapshot = UiProjectionSnapshot.from_payload(payload)

    assert snapshot.deriv_bot_armed is True


def test_ui_projection_rejects_float_money_and_unproven_currency() -> None:
    payload = _snapshot().to_payload()
    payload["daily_pnl_minor_units"] = 1.5
    with pytest.raises(ProtocolError, match="invalid"):
        UiProjectionSnapshot.from_payload(payload)

    with pytest.raises(ValueError, match="proven currency"):
        UiProjectionSnapshot(
            UiGlobalState.READY,
            False,
            (HealthGateStatus("GLOBAL_ENTRY_GATE", True, None, "Ready"),),
            (BrokerCardStatus("SIMULATED", UiAccountMode.PRACTICE, True, None, None, False),),
            (),
            1,
            None,
        )


def test_ui_projection_rejects_invalid_asset_ranking_decimal() -> None:
    payload = _snapshot().to_payload()
    ranking = payload["deriv_asset_ranking"]
    assert isinstance(ranking, list)
    assert isinstance(ranking[0], dict)
    ranking[0]["conservative_margin_pct"] = "NaN"

    with pytest.raises(ProtocolError, match="invalid"):
        UiProjectionSnapshot.from_payload(payload)
