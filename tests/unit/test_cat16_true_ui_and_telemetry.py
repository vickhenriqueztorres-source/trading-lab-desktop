"""CAT-16: truthful IQ projection and opt-in terminal telemetry."""

from __future__ import annotations

import time
from pathlib import Path

from apps.core.outcomes_uploader import (
    OUTCOME_V2_FIELDS,
    OutcomesUploader,
    deterministic_event_id,
    format_outcome_v2_item,
)
from packages.persistence.database import connect_database
from packages.persistence.migrations import apply_migrations
from packages.persistence.writer import SingleDatabaseWriter
from packages.protocol import UiIqOptionAssetRank, UiIqOptionExecutionMetrics


def _writer(tmp_path: Path) -> SingleDatabaseWriter:
    path = tmp_path / "state.db"
    connection = connect_database(path)
    apply_migrations(connection)
    connection.close()
    return SingleDatabaseWriter(path)


def test_iq_projection_keeps_signal_and_execution_evidence_separate() -> None:
    rank = UiIqOptionAssetRank(
        symbol="EURUSD-OTC",
        display_name="EUR/USD OTC",
        rsi="24.1",
        direction="CALL",
        condition="OVERSOLD",
        status="TRIGGERED",
        source="IQOPTION_BROKER_CLOSED_CANDLES",
        mode="INCREMENTAL",
        revision="recipe-1",
        readiness="READY",
        signal_observed=True,
        candidate_eligible=True,
        order_submitted=False,
    )
    recovered = UiIqOptionAssetRank.from_payload(rank.to_payload())
    assert recovered == rank
    assert recovered.signal_observed is True
    assert recovered.order_submitted is False


def test_metrics_round_trip_is_bounded_and_explicit() -> None:
    metrics = UiIqOptionExecutionMetrics(
        source="IQOPTION_BROKER_CLOSED_CANDLES",
        mode="SHADOW",
        strategy_count=3,
        series_count=2,
        unique_indicator_nodes=4,
        cache_reuse_hits=8,
        fetches=2,
        indicator_updates=12,
        decisions=12,
        decision_p95_ms=4,
        catalog_status="SIGNED",
        evidence_n=1200,
        evidence_oos=300,
        evidence_validity="SEALED",
    )
    assert UiIqOptionExecutionMetrics.from_payload(metrics.to_payload()) == metrics


def test_outcomes_opt_in_disabled_never_posts(tmp_path: Path) -> None:
    writer = _writer(tmp_path)
    posts: list[bytes] = []
    uploader = OutcomesUploader(
        writer=writer,
        identity_file=tmp_path / "identity.json",
        endpoint_url="https://example.invalid/outcomes",
        opt_in=False,
        http_post_fn=lambda _url, _headers, body: posts.append(body) or 202,
    )
    uploader.enqueue("s", int(time.time()) // 60 * 60, True, "90.0")
    assert uploader.flush_once() == 0
    assert posts == []
    assert uploader.pending_count() == 1


def test_v2_event_is_exact_and_idempotent(tmp_path: Path) -> None:
    writer = _writer(tmp_path)
    ts = int(time.time()) // 60 * 60
    uploader = OutcomesUploader(
        writer=writer,
        identity_file=tmp_path / "identity.json",
        endpoint_url="https://example.invalid/outcomes",
        token_provider=lambda _client: "test-token",
        http_post_fn=lambda _url, _headers, _body: 202,
    )
    kwargs = dict(
        order_id="order-1",
        strategy_key="recipe",
        recipe_revision=1,
        manifest_version=2,
        execution_semantics_version="exec-v1",
        primitives_version="1.0.0",
        asset="EURUSD",
        timeframe_s=60,
        product="binary",
        account_environment="practice",
        ts=ts,
        won=True,
        payout_pct="90.00",
    )
    assert uploader.enqueue_terminal_v2(**kwargs) is True
    assert uploader.enqueue_terminal_v2(**kwargs) is True
    assert writer.count_pending_outcomes() == 1
    rows = writer.fetch_pending_outcomes()
    payload = format_outcome_v2_item(
        client_id=uploader.client_id,
        event_id=str(rows[0]["event_id"]),
        strategy_key="recipe",
        recipe_revision=1,
        manifest_version=2,
        execution_semantics_version="exec-v1",
        primitives_version="1.0.0",
        asset="EURUSD",
        timeframe_s=60,
        product="binary",
        account_environment="practice",
        source="desktop_bot",
        signal_group_id=str(rows[0]["signal_group_id"]),
        ts=ts,
        won=True,
        payout_pct="90.00",
    )
    assert set(payload) == OUTCOME_V2_FIELDS
    assert rows[0]["event_id"] == deterministic_event_id("order-1")
    assert uploader.flush_once() == 1
    assert writer.count_pending_outcomes() == 0
