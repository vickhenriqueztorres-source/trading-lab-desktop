from __future__ import annotations

import argparse
import os
import time

from apps.deriv_worker.demo_session import (
    DemoDerivSession,
    DemoReadOnlyDerivSession,
    DerivOptionsRestClient,
    EnvironmentDerivTokenProvider,
)
from apps.deriv_worker.fake_transport import FakeDerivScenario, FakeDerivTransport
from apps.deriv_worker.public_session import PublicDerivSession
from apps.deriv_worker.schema import DerivWorkerError
from apps.deriv_worker.server import DerivWorkerServer
from apps.deriv_worker.validators import PUBLIC_WS_URL
from apps.deriv_worker.websocket_client import DerivReadTransport, DerivWebSocketClient


def main() -> int:
    parser = argparse.ArgumentParser(description="DualTrade Deriv read-only worker")
    parser.add_argument("--host", required=True)
    parser.add_argument("--port", required=True, type=int)
    parser.add_argument("--protocol-version", required=True, type=int)
    parser.add_argument(
        "--scenario",
        choices=[item.value for item in FakeDerivScenario],
        default=FakeDerivScenario.NORMAL.value,
    )
    parser.add_argument(
        "--external-public",
        action="store_true",
        help="explicitly opt in to the Deriv public read-only websocket",
    )
    parser.add_argument(
        "--deriv-transport",
        choices=("fake-public", "fake-demo", "live-public", "live-demo"),
        default="fake-public",
        help="external modes are explicit opt-in; fake-public remains the default",
    )
    arguments = parser.parse_args()
    scenario = FakeDerivScenario(arguments.scenario)
    mode = "live-public" if arguments.external_public else str(arguments.deriv_transport)
    try:
        session = _build_session(mode, scenario)
    except (DerivWorkerError, ValueError):
        return 2
    return DerivWorkerServer(
        arguments.host,
        arguments.port,
        protocol_version=arguments.protocol_version,
        session=session,
        scenario=scenario,
    ).run()


def _build_session(mode: str, scenario: FakeDerivScenario) -> PublicDerivSession:
    if mode == "fake-public":
        return PublicDerivSession(FakeDerivTransport(scenario))
    if mode == "fake-demo":
        transport = FakeDerivTransport(
            scenario,
            demo_authenticated=True,
            server_epoch=int(time.time()),
        )
        return DemoReadOnlyDerivSession(transport)
    if mode == "live-public":
        return PublicDerivSession(DerivWebSocketClient(PUBLIC_WS_URL))
    if mode != "live-demo":
        raise ValueError("DERIV_TRANSPORT_INVALID")
    if os.environ.get("DUALTRADE_RUN_EXTERNAL_DERIV_DEMO") != "1":
        raise ValueError("DERIV_DEMO_OPT_IN_REQUIRED")
    app_id = os.environ.get("DUALTRADE_DERIV_APP_ID", "").strip()
    account_id = os.environ.get("DUALTRADE_DERIV_DEMO_ACCOUNT_ID", "").strip()
    if not app_id or not account_id:
        raise ValueError("DERIV_DEMO_CONFIGURATION_REQUIRED")
    bootstrap = DemoDerivSession(
        EnvironmentDerivTokenProvider(),
        DerivOptionsRestClient(),
        app_id,
    )
    demo_transport: DerivReadTransport = bootstrap.open(account_id)
    return DemoReadOnlyDerivSession(demo_transport)


if __name__ == "__main__":
    raise SystemExit(main())
