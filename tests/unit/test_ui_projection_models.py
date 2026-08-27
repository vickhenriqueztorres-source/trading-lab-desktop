from __future__ import annotations

from datetime import UTC, datetime

import pytest

from apps.ui.view_model import DashboardViewModel
from packages.protocol import (
    BrokerCardStatus,
    HealthGateStatus,
    OrderSummary,
    ProtocolError,
    UiAccountMode,
    UiDerivAssetRank,
    UiDerivStrategyStatus,
    UiGlobalState,
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
    assert snapshot.deriv_asset_ranking[0].selected is True


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
