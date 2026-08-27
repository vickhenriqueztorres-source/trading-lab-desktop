from __future__ import annotations

from collections.abc import Mapping

import pytest

from apps.deriv_worker.__main__ import _build_session
from apps.deriv_worker.fake_transport import FakeDerivScenario
from apps.deriv_worker.schema import DerivWorkerError
from apps.deriv_worker.validators import (
    validate_deriv_account,
    validate_deriv_ws_url,
    validate_outbound_deriv_request,
)


@pytest.mark.parametrize(
    "url,reason_code",
    [
        (
            "wss://api.derivws.com/trading/v1/options/ws/real?otp=placeholder",
            "DERIV_REAL_WS_FORBIDDEN",
        ),
        (
            "wss://lookalike.example/trading/v1/options/ws/demo?otp=placeholder",
            "DERIV_WS_HOST_FORBIDDEN",
        ),
        (
            "wss://api.derivws.com/trading/v1/options/ws/demo",
            "DERIV_DEMO_OTP_MISSING",
        ),
        (
            "wss://api.derivws.com/trading/v1/options/ws/public?otp=placeholder",
            "DERIV_PUBLIC_WS_QUERY_FORBIDDEN",
        ),
    ],
)
def test_websocket_endpoint_guard_fails_closed(url: str, reason_code: str) -> None:
    with pytest.raises(DerivWorkerError) as captured:
        validate_deriv_ws_url(url)

    assert captured.value.reason_code == reason_code


def test_only_explicit_demo_account_is_accepted() -> None:
    validate_deriv_account({"account_id": "virtual-placeholder", "account_type": "demo"})

    with pytest.raises(DerivWorkerError) as captured:
        validate_deriv_account({"account_id": "real-placeholder", "account_type": "real"})
    assert captured.value.reason_code == "DERIV_ACCOUNT_TYPE_MISMATCH"

    validate_deriv_account(
        {"account_id": "real-placeholder", "account_type": "real"},
        expected_account_type="real",
    )


def test_real_websocket_requires_explicit_real_expectation() -> None:
    url = "wss://api.derivws.com/trading/v1/options/ws/real?otp=placeholder"
    assert validate_deriv_ws_url(url, expected_account_type="real") == url


@pytest.mark.parametrize("opcode", ["buy", "sell", "proposal", "deposit", "withdraw"])
def test_trading_denylist_runs_before_any_transport(opcode: str) -> None:
    payload: Mapping[str, object] = {opcode: "placeholder"}

    with pytest.raises(DerivWorkerError) as captured:
        validate_outbound_deriv_request(opcode, payload)

    assert captured.value.reason_code == "DERIV_TRADING_OPERATION_DISABLED"


def test_live_demo_requires_explicit_external_opt_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DUALTRADE_RUN_EXTERNAL_DERIV_DEMO", raising=False)

    with pytest.raises(ValueError, match="DERIV_DEMO_OPT_IN_REQUIRED"):
        _build_session("live-demo", FakeDerivScenario.NORMAL)
