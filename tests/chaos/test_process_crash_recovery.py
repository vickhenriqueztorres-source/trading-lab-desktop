from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

from apps.core.instance import CoreInstanceAlreadyRunning, CoreInstanceGuard
from apps.core.runtime import CoreRuntime
from apps.simulated_worker.worker import SimulatedWorker
from packages.observability.events import InMemoryEventSink
from packages.persistence.reader import StateReader
from packages.persistence.writer import SingleDatabaseWriter

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def start_actor(action: str, profile: Path) -> tuple[subprocess.Popen[str], dict[str, str]]:
    ready_path = profile / f"{action}.ready.json"
    process = subprocess.Popen(
        [
            sys.executable,
            "-u",
            "-m",
            "tests.helpers.crash_actor",
            action,
            str(profile),
            str(ready_path),
        ],
        cwd=PROJECT_ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if ready_path.exists():
            payload = json.loads(ready_path.read_text(encoding="utf-8"))
            assert isinstance(payload, dict)
            return process, {str(key): str(value) for key, value in payload.items()}
        if process.poll() is not None:
            stdout, stderr = process.communicate(timeout=1)
            raise AssertionError(
                f"crash actor exited before ready: {process.returncode}; {stdout}; {stderr}"
            )
        time.sleep(0.02)
    process.kill()
    process.wait(timeout=5)
    raise AssertionError(f"crash actor did not become ready: {action}")


def kill_actor(process: subprocess.Popen[str]) -> None:
    if process.poll() is None:
        process.kill()
    process.wait(timeout=5)


def test_second_core_instance_is_blocked_before_database_start(tmp_path: Path) -> None:
    profile = tmp_path / "profile"
    process, _ = start_actor("hold_lock", profile)
    events = InMemoryEventSink()
    worker = SimulatedWorker()
    runtime = CoreRuntime(profile, worker, events)
    try:
        with pytest.raises(CoreInstanceAlreadyRunning) as captured:
            runtime.start()
        assert captured.value.reason_code == "CORE_INSTANCE_ALREADY_RUNNING"
        assert runtime.dispatcher_started is False
        assert worker.received == []
        assert not (profile / "state.db").exists()
        assert any(event.event_name == "core_instance_lock_rejected" for event in events.events)
    finally:
        kill_actor(process)


def test_abandoned_os_lock_is_released_after_owner_is_killed(tmp_path: Path) -> None:
    profile = tmp_path / "profile"
    process, _ = start_actor("hold_lock", profile)
    kill_actor(process)

    guard = CoreInstanceGuard(profile)
    guard.acquire()
    assert guard.is_acquired is True
    guard.release()


def test_uncommitted_transaction_disappears_after_abrupt_kill_and_wal_recovery(
    tmp_path: Path,
) -> None:
    profile = tmp_path / "profile"
    process, _ = start_actor("before_commit", profile)
    assert (profile / "state.db-wal").exists()
    kill_actor(process)

    writer = SingleDatabaseWriter(profile / "state.db")
    reader = StateReader(profile / "state.db")
    assert reader.count("trade_intents") == 0
    assert reader.count("risk_reservations") == 0
    assert reader.count("outbox_messages") == 0
    assert reader.count("orders") == 0
    writer.close()


def test_committed_bundle_survives_abrupt_kill_through_wal(tmp_path: Path) -> None:
    profile = tmp_path / "profile"
    process, identifiers = start_actor("after_commit", profile)
    wal_path = profile / "state.db-wal"
    assert wal_path.exists()
    assert wal_path.stat().st_size > 0
    kill_actor(process)

    runtime = CoreRuntime(profile)
    report = runtime.start()
    try:
        assert runtime.reader.count("trade_intents") == 1
        assert runtime.reader.count("risk_reservations") == 1
        assert runtime.reader.count("outbox_messages") == 1
        assert runtime.reader.count("orders") == 1
        assert report.safe_pending_message_ids == (identifiers["message_id"],)
        reservation = runtime.reader.one(
            "risk_reservations", "reservation_id", identifiers["reservation_id"]
        )
        assert reservation is not None
        assert reservation["state"] == "ACTIVE"
    finally:
        runtime.shutdown()


def test_claim_interrupted_by_kill_recovers_as_unknown_without_retry(tmp_path: Path) -> None:
    profile = tmp_path / "profile"
    process, identifiers = start_actor("during_claim", profile)
    kill_actor(process)

    runtime = CoreRuntime(profile)
    report = runtime.start()
    try:
        assert report.ambiguous_message_ids == (identifiers["message_id"],)
        outbox = runtime.reader.one("outbox_messages", "message_id", identifiers["message_id"])
        order = runtime.reader.one("orders", "order_id", identifiers["order_id"])
        reservation = runtime.reader.one(
            "risk_reservations", "reservation_id", identifiers["reservation_id"]
        )
        assert outbox is not None and outbox["state"] == "AMBIGUOUS"
        assert order is not None and order["state"] == "UNKNOWN"
        assert reservation is not None and reservation["state"] == "ACTIVE"
        assert runtime.health_gate.state.reason_code == "HG_ORDER_UNKNOWN"
        assert runtime.dispatcher_started is False
        assert outbox["attempt_count"] == 1
    finally:
        runtime.shutdown()


def test_locally_confirmed_acceptance_never_regresses_after_kill(tmp_path: Path) -> None:
    profile = tmp_path / "profile"
    process, identifiers = start_actor("accepted", profile)
    kill_actor(process)

    runtime = CoreRuntime(profile)
    runtime.start()
    try:
        outbox = runtime.reader.one("outbox_messages", "message_id", identifiers["message_id"])
        order = runtime.reader.one("orders", "order_id", identifiers["order_id"])
        assert outbox is not None and outbox["state"] == "DISPATCHED"
        assert order is not None and order["state"] == "ACCEPTED"
        assert runtime.dispatcher_started is False
        assert outbox["attempt_count"] == 1
    finally:
        runtime.shutdown()


def test_rec_21_kill_before_reconciliation_commit_rolls_back_all_resolution(
    tmp_path: Path,
) -> None:
    profile = tmp_path / "profile"
    process, identifiers = start_actor("reconciliation_before_commit", profile)
    kill_actor(process)

    writer = SingleDatabaseWriter(profile / "state.db")
    reader = StateReader(profile / "state.db")
    try:
        order = reader.one("orders", "order_id", identifiers["order_id"])
        outbox = reader.one("outbox_messages", "message_id", identifiers["message_id"])
        reservation = reader.one(
            "risk_reservations", "reservation_id", identifiers["reservation_id"]
        )
        assert order is not None and order["state"] == "UNKNOWN"
        assert order["resolution_evidence_id"] is None
        assert outbox is not None and outbox["state"] == "AMBIGUOUS"
        assert reservation is not None and reservation["state"] == "ACTIVE"
        assert reader.count("reconciliation_evidence") == 0
    finally:
        writer.close()


def test_rec_22_kill_after_reconciliation_commit_preserves_complete_resolution(
    tmp_path: Path,
) -> None:
    profile = tmp_path / "profile"
    process, identifiers = start_actor("reconciliation_after_commit", profile)
    kill_actor(process)

    writer = SingleDatabaseWriter(profile / "state.db")
    reader = StateReader(profile / "state.db")
    try:
        order = reader.one("orders", "order_id", identifiers["order_id"])
        outbox = reader.one("outbox_messages", "message_id", identifiers["message_id"])
        reservation = reader.one(
            "risk_reservations", "reservation_id", identifiers["reservation_id"]
        )
        assert order is not None and order["state"] == "ACCEPTED"
        assert order["resolution_evidence_id"] is not None
        assert outbox is not None and outbox["state"] == "RECONCILED"
        assert reservation is not None and reservation["state"] == "ACTIVE"
        assert reader.count("reconciliation_evidence") == 1
    finally:
        writer.close()


def test_evt_24_kill_before_settlement_commit_rolls_back_inbox_pnl_and_release(
    tmp_path: Path,
) -> None:
    profile = tmp_path / "profile"
    process, identifiers = start_actor("settlement_before_commit", profile)
    kill_actor(process)

    writer = SingleDatabaseWriter(profile / "state.db")
    reader = StateReader(profile / "state.db")
    try:
        order = reader.one("orders", "order_id", identifiers["order_id"])
        reservation = reader.one(
            "risk_reservations", "reservation_id", identifiers["reservation_id"]
        )
        assert order is not None and order["state"] == "ACCEPTED"
        assert order["realized_pnl_minor"] is None
        assert reservation is not None and reservation["state"] == "ACTIVE"
        assert reader.count("broker_order_events") == 0
        assert reader.financial_effect_counts(identifiers["order_id"]) == {
            "pnl_application_count": 0,
            "reservation_release_count": 0,
        }
    finally:
        writer.close()


def test_evt_25_kill_after_settlement_commit_preserves_one_complete_effect(
    tmp_path: Path,
) -> None:
    profile = tmp_path / "profile"
    process, identifiers = start_actor("settlement_after_commit", profile)
    kill_actor(process)

    writer = SingleDatabaseWriter(profile / "state.db")
    reader = StateReader(profile / "state.db")
    try:
        order = reader.one("orders", "order_id", identifiers["order_id"])
        reservation = reader.one(
            "risk_reservations", "reservation_id", identifiers["reservation_id"]
        )
        assert order is not None and order["state"] == "SETTLED"
        assert order["realized_pnl_minor"] == 250
        assert reservation is not None and reservation["state"] == "RELEASED"
        assert reader.count("broker_order_events") == 1
        assert reader.financial_effect_counts(identifiers["order_id"]) == {
            "pnl_application_count": 1,
            "reservation_release_count": 1,
        }
    finally:
        writer.close()
