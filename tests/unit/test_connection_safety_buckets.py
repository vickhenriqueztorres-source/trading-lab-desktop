from pathlib import Path

from apps.core.iqoption_connection_safety import (
    IQOptionConnectionSafetyController,
    IQOptionConnectionSafetyStore,
)


def test_manual_login_has_separate_budget_and_resets_auto(tmp_path: Path) -> None:
    now = [10_000.0]
    controller = IQOptionConnectionSafetyController(
        IQOptionConnectionSafetyStore(tmp_path),
        wall_time=lambda: now[0],
    )

    for _ in range(3):
        assert controller.admit_http_login(source="auto").allowed
    assert not controller.admit_http_login(source="auto").allowed

    assert controller.admit_http_login(source="manual").allowed
    assert controller.admit_http_login(source="auto").allowed


def test_manual_login_is_limited_once_per_two_minutes(tmp_path: Path) -> None:
    now = [20_000.0]
    controller = IQOptionConnectionSafetyController(
        IQOptionConnectionSafetyStore(tmp_path),
        wall_time=lambda: now[0],
    )

    assert controller.admit_http_login(source="manual").allowed
    denied = controller.admit_http_login(source="manual")
    assert not denied.allowed
    assert denied.reason_code == "IQOPTION_MANUAL_LOGIN_THROTTLED"
    assert denied.retry_after_seconds == 120

    now[0] += 121
    assert controller.admit_http_login(source="manual").allowed
