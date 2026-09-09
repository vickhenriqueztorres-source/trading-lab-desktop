from __future__ import annotations

import json
from decimal import Decimal

import pytest

from apps.core.catalog_benchmark import DEFAULT_SCENARIOS
from apps.core.catalog_soak import (
    run_catalog_fault_matrix,
    run_catalog_process_soak,
    run_catalog_replay,
)
from apps.core.catalog_soak_cli import main


def test_replay_has_exactly_once_outputs_and_shadow_parity() -> None:
    result = run_catalog_replay(simulated_minutes=5)

    assert result.passed
    assert result.local_outputs == 15
    assert result.unique_output_identities == 15
    assert result.ready_outputs == 15
    assert result.shadow_matches == 3
    assert result.shadow_mismatches == 0
    assert result.network_calls == 0
    assert result.financial_actions == 0


def test_all_faults_recover_or_fail_closed_with_explicit_accounting() -> None:
    evidence = run_catalog_fault_matrix()

    assert [item.name for item in evidence] == [
        "reconnect_generation_fencing",
        "suspension_gap_fail_closed_then_recover",
        "atomic_catalog_swap_retirement",
        "slow_market_no_duplicate_then_resume",
        "bounded_queue_explicit_backpressure",
        "shutdown_during_bootstrap_cleanup",
    ]
    assert all(item.passed for item in evidence)
    queue = evidence[4].details
    assert queue["requested"] == queue["accepted"] + queue["explicitly_rejected"]


def test_process_soak_runs_at_least_one_admitted_batch_without_financial_actions() -> None:
    ticks = iter((0.0, 0.0, 1.0, 1.0))
    result = run_catalog_process_soak(
        wall_seconds=Decimal(0),
        batch_epochs=21,
        scenario=DEFAULT_SCENARIOS[0],
        monotonic=lambda: next(ticks),
    )

    assert result.passed
    assert result.batches == 1
    assert result.rejected_batches == 0
    assert result.financial_actions == 0


def test_cli_emits_machine_readable_local_evidence(
    tmp_path, capsys: pytest.CaptureFixture[str]
) -> None:
    output = tmp_path / "cat19-soak.json"

    assert (
        main(
            [
                "--simulated-minutes",
                "2",
                "--wall-seconds",
                "0",
                "--batch-epochs",
                "21",
                "--output",
                str(output),
            ]
        )
        == 0
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload == json.loads(output.read_text(encoding="utf-8"))
    assert payload["passed"] is True
    assert payload["network_calls"] == 0
    assert payload["financial_actions"] == 0


def test_invalid_durations_are_rejected() -> None:
    with pytest.raises(ValueError):
        run_catalog_replay(simulated_minutes=0)
    with pytest.raises(ValueError):
        run_catalog_process_soak(wall_seconds=Decimal("-1"))
