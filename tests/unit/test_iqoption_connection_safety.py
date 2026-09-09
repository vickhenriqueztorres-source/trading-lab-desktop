from __future__ import annotations

import json
from pathlib import Path

import pytest

from apps.core.iqoption_connection_safety import (
    IQOPTION_CONNECTION_QUARANTINE_SECONDS,
    IQOptionConnectionSafetyController,
    IQOptionConnectionSafetyStateError,
    IQOptionConnectionSafetyStore,
    IQOptionMessageBudget,
)


def test_login_limit_is_persistent_across_controller_restart(tmp_path: Path) -> None:
    now = [1_000_000.0]
    store = IQOptionConnectionSafetyStore(tmp_path)
    first = IQOptionConnectionSafetyController(store, wall_time=lambda: now[0])

    assert first.admit_http_login().allowed is True
    assert first.admit_http_login().allowed is True
    assert first.admit_http_login().allowed is True

    restarted = IQOptionConnectionSafetyController(store, wall_time=lambda: now[0])
    denied = restarted.admit_http_login()
    assert denied.allowed is False
    assert denied.reason_code == "IQOPTION_CONNECTION_QUARANTINED"
    assert denied.retry_after_seconds == IQOPTION_CONNECTION_QUARANTINE_SECONDS


@pytest.mark.parametrize(
    "reason",
    ["IQOPTION_AUTH_FAILED", "IQOPTION_2FA_REQUIRED", "IQOPTION_RATE_LIMITED"],
)
def test_terminal_auth_responses_open_quarantine_immediately(
    tmp_path: Path,
    reason: str,
) -> None:
    now = [2_000_000.0]
    controller = IQOptionConnectionSafetyController(
        IQOptionConnectionSafetyStore(tmp_path),
        wall_time=lambda: now[0],
    )
    assert controller.admit_http_login().allowed is True
    controller.record_failure(reason)

    denied = controller.admit_http_login()
    assert denied.allowed is False
    assert denied.retry_after_seconds == IQOPTION_CONNECTION_QUARANTINE_SECONDS


def test_success_does_not_erase_rolling_login_budget(tmp_path: Path) -> None:
    controller = IQOptionConnectionSafetyController(
        IQOptionConnectionSafetyStore(tmp_path),
        wall_time=lambda: 3_000_000.0,
    )
    for _ in range(3):
        assert controller.admit_http_login().allowed is True
        controller.record_success()
    assert controller.admit_http_login().allowed is False


def test_quarantine_expires_after_cooldown(tmp_path: Path) -> None:
    now = [4_000_000.0]
    controller = IQOptionConnectionSafetyController(
        IQOptionConnectionSafetyStore(tmp_path),
        wall_time=lambda: now[0],
    )
    assert controller.admit_http_login().allowed is True
    controller.record_failure("IQOPTION_RATE_LIMITED")
    now[0] += IQOPTION_CONNECTION_QUARANTINE_SECONDS + 1
    assert controller.admit_http_login().allowed is True


def test_corrupt_safety_state_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "iqoption-connection-safety.json"
    path.write_text(json.dumps({"schema_version": 1, "attempt_epochs": "invalid"}))
    with pytest.raises(IQOptionConnectionSafetyStateError):
        IQOptionConnectionSafetyController(IQOptionConnectionSafetyStore(tmp_path))


def test_market_message_budget_blocks_before_exceeding_limit() -> None:
    budget = IQOptionMessageBudget(limit=2, pressure_at=2)
    assert budget.try_acquire(10.0).allowed is True
    second = budget.try_acquire(10.1)
    assert second.allowed is True
    assert second.pressure is True
    denied = budget.try_acquire(10.2)
    assert denied.allowed is False
    assert denied.used_in_window == 2
    assert budget.try_acquire(70.1).allowed is True


def test_market_lane_cannot_consume_operational_reserve() -> None:
    budget = IQOptionMessageBudget(
        limit=2,
        pressure_at=2,
        total_limit=4,
        total_pressure_at=4,
    )

    assert budget.try_acquire(10.0).allowed is True
    assert budget.try_acquire(10.1).allowed is True
    assert budget.try_acquire(10.2).allowed is False
    assert budget.try_acquire_operational(10.3).allowed is True
    assert budget.try_acquire_operational(10.4).allowed is True
    assert budget.try_acquire_operational(10.5).allowed is False


def test_total_budget_window_expires_monotonically() -> None:
    budget = IQOptionMessageBudget(limit=1, total_limit=1)

    assert budget.try_acquire_operational(5.0).allowed is True
    assert budget.try_acquire(5.1).allowed is False
    assert budget.try_acquire(65.1).allowed is True
