from __future__ import annotations

import os

import pytest

from apps.deriv_worker.__main__ import _build_session
from apps.deriv_worker.demo_session import DemoReadOnlyDerivSession
from apps.deriv_worker.fake_transport import FakeDerivScenario


@pytest.mark.external_deriv_demo
@pytest.mark.skipif(
    os.environ.get("DUALTRADE_RUN_EXTERNAL_DERIV_DEMO") != "1",
    reason="Deriv demo external smoke test is explicitly opt-in",
)
def test_demo_live_read_only_clock_and_balance_smoke() -> None:
    session = _build_session("live-demo", FakeDerivScenario.NORMAL)
    assert isinstance(session, DemoReadOnlyDerivSession)
    try:
        assert session.capabilities.can_trade is False
        assert session.clock().server_epoch > 0
        assert session.account_balance().account_type == "DEMO"
    finally:
        session.close()
