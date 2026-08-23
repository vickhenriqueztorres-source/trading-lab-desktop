from __future__ import annotations

import os

import pytest

from apps.deriv_worker.public_session import PublicDerivSession
from apps.deriv_worker.websocket_client import DerivWebSocketClient


@pytest.mark.external_deriv_public
@pytest.mark.skipif(
    os.environ.get("DUALTRADE_RUN_EXTERNAL_DERIV_PUBLIC") != "1",
    reason="Deriv public external smoke test is explicitly opt-in",
)
def test_public_read_only_smoke() -> None:
    session = PublicDerivSession(DerivWebSocketClient(), request_timeout=5.0)
    try:
        session.connect()
        symbols = session.active_symbols()
        assert symbols
        tick = session.subscribe_ticks(symbols[0].broker_symbol)
        assert session.unsubscribe(tick.subscription_id) in {True, False}
        assert session.capabilities.can_trade is False
    finally:
        session.close()
