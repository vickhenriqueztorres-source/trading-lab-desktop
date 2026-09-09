from __future__ import annotations

import json
from decimal import Decimal

import pytest

from apps.core.catalog_benchmark import (
    DEFAULT_SCENARIOS,
    CatalogBenchmarkLimits,
    CatalogBenchmarkScenario,
    run_catalog_benchmark,
)
from apps.core.catalog_benchmark_cli import main


def test_high_reuse_measures_one_node_for_many_recipes_without_financial_actions() -> None:
    result = run_catalog_benchmark(DEFAULT_SCENARIOS[0], epochs=21, rss_probe=lambda: Decimal("10"))

    assert result.recipes == 10
    assert result.unique_series == 1
    assert result.active_nodes == 1
    assert result.node_reuse_ratio == Decimal("10")
    assert result.synthetic_series_updates == 21
    assert result.financial_actions == 0
    assert result.local_samples == 21


def test_no_reuse_scenario_keeps_nodes_distinct_and_reports_admission_failure() -> None:
    scenario = CatalogBenchmarkScenario("strict", 3, 3, 3, (60,))
    result = run_catalog_benchmark(
        scenario,
        epochs=21,
        limits=CatalogBenchmarkLimits(
            p95_local_ms=Decimal("100"),
            p99_local_ms=Decimal("500"),
            max_rss_mib=1,
        ),
        rss_probe=lambda: Decimal("2"),
    )

    assert result.active_nodes == 3
    assert result.node_reuse_ratio == Decimal("1")
    assert not result.admitted
    assert result.blocking_reasons == ("CATALOG_BENCHMARK_RSS_EXCEEDED",)


def test_invalid_scenarios_and_short_workloads_are_rejected() -> None:
    with pytest.raises(ValueError):
        CatalogBenchmarkScenario("bad", 1, 1, 2, (60,))
    with pytest.raises(ValueError):
        run_catalog_benchmark(DEFAULT_SCENARIOS[0], epochs=20)


def test_cli_is_local_and_reports_zero_network_and_financial_actions(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["--scenario", "10_high_reuse", "--epochs", "21"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["network_calls"] == 0
    assert payload["financial_actions"] == 0
