"""Regression from the 2026-09-09 read-only broker observation; no external requests."""

import json
from decimal import Decimal

import pytest

from apps.core.worker_client import DeliveryCertainty, WorkerDispatchError
from apps.iqoption_connection_worker.server import IQOptionReadOnlyWorkerServer
from packages.brokers.iqoption.community_read_only import IQOptionExternalError
from packages.domain.models import OrderState
from packages.protocol.errors import ProtocolErrorCode
from scripts.iqoption_payout_probe import ProbeSession, describe
from tests.unit.test_iqoption_failure_recovery import setup_trader
from tests.unit.test_iqoption_session_regressions import PayoutSocket, session_for


def test_broker_missing_asset_is_not_a_generic_payout_error():
    session = session_for(PayoutSocket())
    try:
        with pytest.raises(IQOptionExternalError, match="IQOPTION_ACTIVE_UNAVAILABLE"):
            session.get_binary_payout("GBPUSD-OTC")
        assert session.is_connected
    finally:
        session.close()


@pytest.mark.parametrize("reason", ["IQOPTION_ACTIVE_SUSPENDED", "IQOPTION_ACTIVE_UNAVAILABLE"])
def test_suspended_payout_does_not_disconnect_disarm_or_starve_other_asset(reason):
    trader, runtime, client, clock, _, quotes, _ = setup_trader(auto=True)
    runtime.reader.state = OrderState.ACCEPTED
    recovery = []
    trader._recovery_notifier = recovery.append
    unavailable = [True]

    def payout(symbol):
        quotes.append(symbol)
        if symbol == "EURUSD-OTC" and unavailable[0]:
            raise WorkerDispatchError(
                ProtocolErrorCode(reason), "broker availability", DeliveryCertainty.NOT_SENT
            )
        return Decimal("0.85")

    client.iqoption_binary_payout = payout
    trader._evaluate_cycle()
    assert runtime.requests == []
    assert trader._unavailable_assets["EURUSD-OTC"][1] == reason
    trader._evaluate_cycle()
    assert [request.symbol for request in runtime.requests] == ["GBPUSD-OTC"]
    assert recovery == []
    assert not trader._failures.failures  # availability is not a financial rejection
    clock[0] = 59
    trader._scan_cursor = 0
    trader._evaluate_cycle()
    assert quotes.count("EURUSD-OTC") == 1
    unavailable[0] = False
    clock[0] = 60
    trader._scan_cursor = 0
    trader._evaluate_cycle()
    assert quotes.count("EURUSD-OTC") == 2  # fresh quote, not cached permission
    assert [request.symbol for request in runtime.requests] == ["GBPUSD-OTC", "EURUSD-OTC"]
    assert len({request.correlation_id for request in runtime.requests}) == 2


@pytest.mark.parametrize("reason", ["IQOPTION_ACTIVE_SUSPENDED", "IQOPTION_ACTIVE_UNAVAILABLE"])
def test_availability_reason_survives_ipc(reason):
    _, payload = IQOptionReadOnlyWorkerServer._error_payload(reason)
    assert payload["reason_code"] == reason


@pytest.mark.parametrize(
    "payload",
    [
        {"name": "buy"},
        {"name": "sell"},
        {"name": "sendMessage", "msg": {"name": "binary-options.open-option"}},
        {"name": "unknown"},
    ],
)
def test_probe_denies_all_non_read_only_sends(payload):
    # No socket or credentials are needed to prove rejection before transport.
    probe = ProbeSession.__new__(ProbeSession)
    with pytest.raises(RuntimeError, match="PROBE_WRITE_FORBIDDEN"):
        probe._send(payload)


def test_probe_evidence_never_contains_raw_secrets():
    response = {
        "msg": {
            "email": "sensitive-value",
            "token": "sensitive-value",
            "turbo": {
                "actives": {
                    "1": {
                        "name": "front.EURUSD",
                        "enabled": True,
                        "is_suspended": False,
                        "password": "sensitive-value",
                        "option": {"profit": {"commission": 15}},
                    }
                }
            },
        }
    }
    encoded = json.dumps(describe(response, "EURUSD"))
    assert "sensitive-value" not in encoded
    assert "password" not in encoded
    assert "token" not in encoded


def test_unavailable_radar_is_not_neutral_or_triggered():
    from PySide6.QtWidgets import QApplication

    from apps.ui.components.iqoption_asset_radar import IqOptionAssetRadarWidget
    from packages.protocol.ui_messages import UiIqOptionAssetRank

    app = QApplication.instance() or QApplication([])
    widget = IqOptionAssetRadarWidget()
    widget.update_ranking(
        [
            UiIqOptionAssetRank(
                symbol="GBPUSD-OTC",
                display_name="GBP/USD OTC",
                rsi="20",
                direction=None,
                condition="IQOPTION_ACTIVE_SUSPENDED",
                selected=False,
                status="MARKET_UNAVAILABLE",
            )
        ]
    )
    assert "Suspenso pela corretora" in widget._table.item(0, 3).text()
    assert widget._table.item(0, 4).text() == "MERCADO INDISPONÍVEL"
    widget.close()
    assert app is not None


def test_observed_nzdusd_payout_is_below_published_recipe_requirements():
    from apps.core.payout_gate import PayoutGate

    result = PayoutGate.check_payout(
        current_payout=Decimal("0.82"),
        wilson_lower=Decimal("0.557"),
        payout_min=Decimal("0.85"),
    )
    assert not result.allowed
    assert result.reason_code == "PAYOUT_BELOW_VALIDATED_EDGE"
    assert result.payout == Decimal("0.82")
