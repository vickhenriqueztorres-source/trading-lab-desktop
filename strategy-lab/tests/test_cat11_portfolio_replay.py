"""CAT-11: executable portfolio replay separates raw signals from executable operations."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from primitives.base import Direction
from strategy_lab.research.portfolio_replay import (
    PortfolioDecisionReason,
    PortfolioOpportunity,
    PortfolioReplayConfig,
    compare_portfolio_sizes,
    opportunities_from_replay_contract,
    portfolio_result_to_markdown,
    run_portfolio_replay,
)

BASE_TS = 1_700_000_100


def _config(
    *,
    snapshot_id: str = "snapshot-cat11",
    seed: int = 11,
    starting_capital: Decimal = Decimal("1000"),
    stake: Decimal = Decimal("1"),
    stop_loss_abs: Decimal | None = None,
    take_profit_abs: Decimal | None = None,
    max_consecutive_losses: int | None = None,
    cooldown_after_loss_s: int = 0,
    execution_delay_s: int = 0,
    signal_ttl_s: int = 30,
    period_start_ts: int | None = BASE_TS,
    period_end_ts: int | None = BASE_TS + 600,
) -> PortfolioReplayConfig:
    return PortfolioReplayConfig(
        snapshot_id=snapshot_id,
        seed=seed,
        starting_capital=starting_capital,
        stake=stake,
        stop_loss_abs=stop_loss_abs,
        take_profit_abs=take_profit_abs,
        max_consecutive_losses=max_consecutive_losses,
        cooldown_after_loss_s=cooldown_after_loss_s,
        execution_delay_s=execution_delay_s,
        signal_ttl_s=signal_ttl_s,
        period_start_ts=period_start_ts,
        period_end_ts=period_end_ts,
    )


def _op(
    recipe_key: str,
    *,
    ts_offset: int = 0,
    asset: str = "EURUSD-OTC",
    direction: Direction = "call",
    payout: str | None = "0.87",
    won: bool | None = True,
    duration_s: int = 60,
    pre_gate_reason: PortfolioDecisionReason | None = None,
    execution_failure_reason: PortfolioDecisionReason | None = None,
    priority: int = 0,
) -> PortfolioOpportunity:
    return PortfolioOpportunity(
        recipe_key=recipe_key,
        asset=asset,
        timeframe_s=60,
        signal_ts=BASE_TS + ts_offset,
        direction=direction,
        payout_return_ratio=None if payout is None else Decimal(payout),
        won=won,
        duration_s=duration_s,
        priority=priority,
        pre_gate_reason=pre_gate_reason,
        execution_failure_reason=execution_failure_reason,
    )


def test_raw_signals_are_not_counted_as_executable_operations() -> None:
    """R-RES-9: uma ordem em voo limita frequência, mesmo com vários sinais no mesmo minuto."""
    opportunities = [
        _op("recipe-a", asset="EURUSD-OTC", priority=10),
        _op("recipe-b", asset="GBPUSD-OTC"),
        _op("recipe-c", asset="USDJPY-OTC"),
        _op("recipe-d", ts_offset=120, asset="EURJPY-OTC"),
    ]

    result = run_portfolio_replay(opportunities, _config())

    assert result.signals_total == 4
    assert result.executed_total == 2
    assert result.raw_signals_per_day == Decimal("576")
    assert result.executable_operations_per_day == Decimal("288")
    assert result.rejections_by_reason[PortfolioDecisionReason.ORDER_IN_FLIGHT] == 2
    assert result.violations == ()


def test_arbitration_conflict_blocks_opposite_and_duplicate_contexts() -> None:
    """R-RES-5: arbitragem pública não soma sinais incompatíveis como amostra independente."""
    opposite = run_portfolio_replay(
        [
            _op("recipe-call", direction="call"),
            _op("recipe-put", direction="put"),
        ],
        _config(),
    )
    assert opposite.executed_total == 0
    assert opposite.rejections_by_reason[PortfolioDecisionReason.CONFLICT] == 2

    duplicate = run_portfolio_replay(
        [
            _op("recipe-a", direction="call", priority=5),
            _op("recipe-b", direction="call", priority=1),
            _op("recipe-c", direction="call", priority=0),
        ],
        _config(),
    )
    assert duplicate.executed_total == 1
    assert duplicate.rejections_by_reason[PortfolioDecisionReason.CONFLICT] == 2


def test_all_required_block_reasons_are_recorded_per_opportunity() -> None:
    """R-RES-11: cada oportunidade recebe motivo rastreável, sem preencher ordem recusada."""
    guarded = run_portfolio_replay(
        [
            _op("outside", pre_gate_reason=PortfolioDecisionReason.OUTSIDE_HOURS),
            _op("warmup", ts_offset=60, pre_gate_reason=PortfolioDecisionReason.WARMUP),
            _op("stale", ts_offset=120, pre_gate_reason=PortfolioDecisionReason.STALE),
            _op("risk", ts_offset=180, pre_gate_reason=PortfolioDecisionReason.RISK),
            _op("reconnect", ts_offset=240, pre_gate_reason=PortfolioDecisionReason.RECONNECTING),
            _op("delayed", ts_offset=300, pre_gate_reason=PortfolioDecisionReason.CANDLE_DELAYED),
            _op("missing-payout", ts_offset=360, payout=None),
            _op("execution", ts_offset=420, won=None),
            _op(
                "execution-rejected",
                ts_offset=480,
                execution_failure_reason=PortfolioDecisionReason.EXECUTION,
            ),
        ],
        _config(period_end_ts=BASE_TS + 700),
    )
    deadline = run_portfolio_replay(
        [_op("deadline", ts_offset=540)],
        _config(execution_delay_s=31, signal_ttl_s=30, period_end_ts=BASE_TS + 700),
    )

    assert guarded.executed_total == 0
    assert deadline.executed_total == 0
    for reason in (
        PortfolioDecisionReason.OUTSIDE_HOURS,
        PortfolioDecisionReason.WARMUP,
        PortfolioDecisionReason.STALE,
        PortfolioDecisionReason.RISK,
        PortfolioDecisionReason.RECONNECTING,
        PortfolioDecisionReason.CANDLE_DELAYED,
    ):
        assert guarded.rejections_by_reason[reason] >= 1
    assert guarded.rejections_by_reason[PortfolioDecisionReason.MISSING_PAYOUT] == 1
    assert guarded.rejections_by_reason[PortfolioDecisionReason.EXECUTION] == 2
    assert deadline.rejections_by_reason[PortfolioDecisionReason.DEADLINE_EXPIRED] == 1


def test_financial_result_uses_observed_payout_and_loss_is_full_stake() -> None:
    """R-RES-4: resultado usa payout observado; perda, inclusive empate upstream, é stake cheio."""
    result = run_portfolio_replay(
        [
            _op("win", ts_offset=0, payout="0.87", won=True),
            _op("loss", ts_offset=120, payout="0.91", won=False),
        ],
        _config(stake=Decimal("10")),
    )

    assert [trade.pnl for trade in result.trades] == [Decimal("8.70"), Decimal("-10")]
    assert result.net_pnl == Decimal("-1.30")
    assert result.trades[0].payout_return_ratio == Decimal("0.87")
    assert result.trades[1].payout_return_ratio == Decimal("0.91")


def test_risk_and_cooldown_are_applied_before_future_entries() -> None:
    """R-RES-9: cooldown e limite de perdas bloqueiam entradas futuras sem sticky global eterno."""
    result = run_portfolio_replay(
        [
            _op("loss-a", ts_offset=0, won=False),
            _op("cooldown-hit", ts_offset=90, won=True),
            _op("loss-b", ts_offset=180, won=False),
            _op("risk-hit", ts_offset=300, won=True),
        ],
        _config(cooldown_after_loss_s=120, max_consecutive_losses=2, period_end_ts=BASE_TS + 420),
    )

    assert result.executed_total == 2
    assert result.rejections_by_reason[PortfolioDecisionReason.RISK] == 2
    assert result.net_pnl == Decimal("-2")


def test_public_replay_contract_vectors_seed_portfolio_replay() -> None:
    """CAT-04/CAT-11: portfolio replay consumes the public decision-vector artifact."""
    contract_path = (
        Path(__file__).resolve().parents[1] / "contracts/replay_contract_vectors.v1.json"
    )
    opportunities = opportunities_from_replay_contract(contract_path, default_stake=Decimal("1"))

    first = run_portfolio_replay(
        opportunities,
        _config(snapshot_id="replay-contract-v1", seed=404, period_end_ts=BASE_TS + 1000),
    )
    second = run_portfolio_replay(
        opportunities,
        _config(snapshot_id="replay-contract-v1", seed=404, period_end_ts=BASE_TS + 1000),
    )

    assert len(opportunities) == 2
    assert first.executed_total == 1
    assert first.signals_total == 2
    assert first.rejections_by_reason[PortfolioDecisionReason.ORDER_IN_FLIGHT] == 1
    assert first.violations == ()
    assert first.reproducibility_hash == second.reproducibility_hash


def test_compare_10_20_30_50_does_not_multiply_duplicate_events() -> None:
    """R-RES-9: biblioteca que duplica mesmo evento não multiplica operações executáveis."""
    opportunities = [
        _op(f"recipe-{index:02d}", direction="call", priority=50 - index) for index in range(50)
    ]

    results = compare_portfolio_sizes(opportunities, _config(period_end_ts=BASE_TS + 3600))

    assert set(results) == {10, 20, 30, 50}
    assert {size: result.signals_total for size, result in results.items()} == {
        10: 10,
        20: 20,
        30: 30,
        50: 50,
    }
    assert {size: result.executed_total for size, result in results.items()} == {
        10: 1,
        20: 1,
        30: 1,
        50: 1,
    }
    assert results[50].rejections_by_reason[PortfolioDecisionReason.CONFLICT] == 49


def test_report_is_reproducible_by_snapshot_and_seed() -> None:
    """R-RES-11: relatório tem hash estável e muda quando snapshot/seed mudam."""
    opportunities = [_op("recipe-a"), _op("recipe-b", ts_offset=120, won=False)]
    first = run_portfolio_replay(opportunities, _config(snapshot_id="snapshot-a", seed=1))
    second = run_portfolio_replay(opportunities, _config(snapshot_id="snapshot-a", seed=1))
    changed = run_portfolio_replay(opportunities, _config(snapshot_id="snapshot-b", seed=2))

    assert first.reproducibility_hash == second.reproducibility_hash
    assert first.reproducibility_hash != changed.reproducibility_hash
    rendered = portfolio_result_to_markdown(first)
    assert "Sinais brutos" in rendered
    assert first.reproducibility_hash in rendered
