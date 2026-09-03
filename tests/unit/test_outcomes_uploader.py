"""Unit tests for OutcomesUploader and 5-field anonymous payload (R-BOT-10)."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from apps.core.outcomes_uploader import (
    REQUIRED_OUTCOME_FIELDS,
    OutcomesUploader,
    format_outcome_item,
)
from packages.persistence.database import connect_database
from packages.persistence.migrations import apply_migrations
from packages.persistence.writer import SingleDatabaseWriter


@pytest.fixture
def test_db_and_writer(tmp_path: Path) -> SingleDatabaseWriter:
    db_file = tmp_path / "state.db"
    conn = connect_database(db_file)
    apply_migrations(conn)
    conn.close()
    return SingleDatabaseWriter(db_file)


def test_payload_strictly_contains_only_five_fields() -> None:
    """R-BOT-10: Payload contains strictly the 5 anonymous audit fields."""
    item = format_outcome_item(
        client_id="f81d4fae-7dec-11d0-a765-00a0c91e6bf6",
        strategy_key="eurusd_f1",
        ts=1756684800,
        won=True,
        payout_pct=Decimal("85.0"),
    )

    # Check exact keys
    assert set(item.keys()) == REQUIRED_OUTCOME_FIELDS
    assert item["client_id"] == "f81d4fae-7dec-11d0-a765-00a0c91e6bf6"
    assert item["strategy_key"] == "eurusd_f1"
    assert item["ts"] == 1756684800
    assert item["won"] is True
    assert item["payout_pct"] == "85.0"

    # Schema must reject any missing or extraneous field
    with pytest.raises(TypeError):
        format_outcome_item(  # type: ignore[call-arg]
            client_id="123",
            strategy_key="s1",
            ts=100,
            won=True,
            payout_pct="80.0",
            extra_leak="forbidden_data",
        )


def test_client_id_persistence_and_reuse(
    tmp_path: Path, test_db_and_writer: SingleDatabaseWriter
) -> None:
    id_file = tmp_path / "client_identity.json"

    # First instance creates identity
    uploader1 = OutcomesUploader(
        writer=test_db_and_writer,
        identity_file=id_file,
    )
    cid1 = uploader1.client_id
    assert cid1
    assert id_file.exists()

    # Second instance loads same identity
    uploader2 = OutcomesUploader(
        writer=test_db_and_writer,
        identity_file=id_file,
    )
    assert uploader2.client_id == cid1


def test_outcomes_enqueue_and_successful_flush(
    tmp_path: Path, test_db_and_writer: SingleDatabaseWriter
) -> None:
    id_file = tmp_path / "client_identity.json"
    uploaded_payloads: list[bytes] = []

    def mock_post(url: str, headers: dict[str, str], data: bytes) -> int:
        uploaded_payloads.append(data)
        return 200

    uploader = OutcomesUploader(
        writer=test_db_and_writer,
        identity_file=id_file,
        endpoint_url="https://example.com/api/outcomes",
        http_post_fn=mock_post,
    )

    assert uploader.pending_count() == 0

    # Enqueue 3 outcomes
    uploader.enqueue("s1", ts=1756684801, won=True, payout_pct="85.0")
    uploader.enqueue("s1", ts=1756684802, won=False, payout_pct="85.0")
    uploader.enqueue("s2", ts=1756684803, won=True, payout_pct="82.0")

    assert uploader.pending_count() == 3

    # Flush batch
    sent_count = uploader.flush_once()
    assert sent_count == 3
    assert uploader.pending_count() == 0
    assert len(uploaded_payloads) == 1

    payload_items = json.loads(uploaded_payloads[0].decode("utf-8"))
    assert len(payload_items) == 3
    for p in payload_items:
        assert set(p.keys()) == REQUIRED_OUTCOME_FIELDS
        assert p["client_id"] == uploader.client_id


def test_uploader_fail_silent_when_server_is_offline(
    tmp_path: Path, test_db_and_writer: SingleDatabaseWriter
) -> None:
    """Network failure or server crash must fail silently and keep queue intact."""
    id_file = tmp_path / "client_identity.json"

    def mock_failing_post(url: str, headers: dict[str, str], data: bytes) -> int:
        raise ConnectionRefusedError("Simulated remote server is down")

    uploader = OutcomesUploader(
        writer=test_db_and_writer,
        identity_file=id_file,
        endpoint_url="https://offline.server/api",
        http_post_fn=mock_failing_post,
    )

    uploader.enqueue("s1", ts=1756684800, won=True, payout_pct="85.0")
    assert uploader.pending_count() == 1

    # Flush must NOT raise any exception
    sent = uploader.flush_once()
    assert sent == 0

    # Queue remains intact for future retry
    assert uploader.pending_count() == 1


def test_prolonged_offline_simulation_leaves_operations_intact(
    tmp_path: Path, test_db_and_writer: SingleDatabaseWriter
) -> None:
    """Acceptance criteria: 30 days offline simulation leaves operation intact."""
    id_file = tmp_path / "client_identity.json"
    server_online = False
    received_batches: list[list[dict[str, Any]]] = []

    def controllable_post(url: str, headers: dict[str, str], data: bytes) -> int:
        if not server_online:
            raise TimeoutError("Network timeout during offline period")
        received_batches.append(json.loads(data.decode("utf-8")))
        return 201

    uploader = OutcomesUploader(
        writer=test_db_and_writer,
        identity_file=id_file,
        endpoint_url="https://example.com/api/outcomes",
        http_post_fn=controllable_post,
    )

    # Simulate 30 days of trading while network is completely dead (e.g. 100 trades)
    base_ts = 1756684800
    for day in range(30):
        for trade_num in range(5):
            ts = base_ts + day * 86400 + trade_num * 60
            won = (day + trade_num) % 2 == 0
            # Enqueue must always succeed without throwing
            uploader.enqueue(
                strategy_key="f1_eurusd",
                ts=ts,
                won=won,
                payout_pct="85.0",
            )
        # Attempted uploads during offline period fail silently
        uploader.flush_once()

    assert uploader.pending_count() == 150
    assert len(received_batches) == 0

    # Network is restored!
    server_online = True
    sent = uploader.flush_once(batch_size=500)
    assert sent == 150
    assert uploader.pending_count() == 0
    assert len(received_batches) == 1
    assert len(received_batches[0]) == 150


def test_no_outcomes_queue_access_in_strategy_evaluation(
    tmp_path: Path, test_db_and_writer: SingleDatabaseWriter
) -> None:
    from apps.core.manifest_catalog import (
        DynamicManifestCatalog,
        StrategyCatalogEntry,
        ValidatedStats,
    )

    catalog = DynamicManifestCatalog()
    entry = StrategyCatalogEntry(
        key="s1",
        family="F1",
        display_name_pt="Estratégia F1",
        asset="EURUSD",
        timeframe="M1",
        hours_utc=(0, 24),
        params={
            "adx_len": 14,
            "adx_max": "25.0",
            "bb_len": 20,
            "bb_k": "2.0",
            "rsi_len": 14,
            "rsi_lo": "30.0",
            "rsi_hi": "70.0",
        },
        validated=ValidatedStats(
            p_hat=Decimal("0.60"),
            wilson_lower=Decimal("0.58"),
            p_min_at_validation=Decimal("0.46"),
            payout_min=Decimal("0.80"),
            ops_per_day=Decimal("15"),
            worst_streak=3,
            result_1000_ops_stake10=Decimal("1500"),
            score=Decimal("5.0"),
        ),
        status="approved",
    )
    catalog.apply_manifest({"manifest_version": 1, "strategies": (entry,)})

    # Record initial outcomes queue count
    initial_pending = test_db_and_writer.count_pending_outcomes()

    # Simulate 5,000 tick evaluations in memory
    for _ in range(5_000):
        ok, reason, _ = catalog.is_eligible(
            "s1", account_type="DEMO", current_payout=Decimal("0.85")
        )
        assert ok is True

    # Outcomes queue was never touched during signal evaluation
    assert test_db_and_writer.count_pending_outcomes() == initial_pending
