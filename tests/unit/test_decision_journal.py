from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

from packages.audit import (
    DecisionEventType,
    DecisionJournal,
    verify_decision_chain,
)


def test_decision_hash_chain_detects_tampering() -> None:
    journal = DecisionJournal("run-1", max_events=2)
    record = journal.append(
        DecisionEventType.CANDLE_ACCEPTED,
        occurred_at=datetime(2026, 8, 20, tzinfo=UTC),
        correlation_id="candle-1",
        causation_id=None,
        strategy_id="strategy-a",
        strategy_version="1.0.0",
        manifest_hash="a" * 64,
        configuration_hash="b" * 64,
        candle_id="c" * 64,
        payload=(("source", "FAKE"),),
    )
    records = journal.records
    assert verify_decision_chain(records)

    changed_event = replace(record.event, strategy_id="tampered-strategy")
    tampered = (replace(record, event=changed_event),)
    assert not verify_decision_chain(tampered)
