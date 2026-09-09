from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from apps.core.ui_service import _ui_operational_logs
from packages.observability.events import OperationalEvent
from packages.protocol import UiLogLevel


def _event(
    index: int,
    *,
    event_name: str = "iqoption_health_gate_blocked",
    reason_code: str | None = "MD_CLOCK_UNTRUSTED",
    **fields: str | int | bool | None,
) -> OperationalEvent:
    return OperationalEvent(
        event_name=event_name,
        occurred_at=datetime(2026, 9, 9, 12, tzinfo=UTC) + timedelta(seconds=index),
        reason_code=reason_code,
        fields=tuple(sorted(fields.items())),
    )


def test_ui_operational_logs_are_bounded_classified_and_allowlisted() -> None:
    events = tuple(
        _event(
            index,
            broker="IQOPTION",
            symbol="EURUSD-OTC",
            password="SENSITIVE_VALUE_MUST_NOT_CROSS_UI",
            raw_payload="SENSITIVE_VALUE_MUST_NOT_CROSS_UI",
        )
        for index in range(200)
    )
    runtime = SimpleNamespace(event_sink=SimpleNamespace(recent_events=events))

    projected = _ui_operational_logs(runtime)  # type: ignore[arg-type]

    assert len(projected) == 160
    assert projected[0].occurred_at_utc == events[40].occurred_at
    assert projected[-1].level is UiLogLevel.WARNING
    assert projected[-1].source == "IQOPTION"
    assert projected[-1].detail == "broker=IQOPTION symbol=EURUSD-OTC"
    assert "SENSITIVE_VALUE" not in repr(projected)


def test_ui_operational_log_marks_remote_rejection_as_error() -> None:
    runtime = SimpleNamespace(
        event_sink=SimpleNamespace(
            recent_events=(
                _event(
                    1,
                    event_name="iqoption_order_rejected",
                    reason_code="IQOPTION_ORDER_REJECTED_REMOTE",
                    order_id="4f5f65ce-17c6-4d86-a742-52dc5445e747",
                ),
            )
        )
    )

    projected = _ui_operational_logs(runtime)  # type: ignore[arg-type]

    assert projected[0].level is UiLogLevel.ERROR
    assert projected[0].detail == "order_id=4f5f65ce-17c6-4d86-a742-52dc5445e747"
