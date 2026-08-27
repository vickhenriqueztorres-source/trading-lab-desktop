from __future__ import annotations

import argparse
import os
import time
from dataclasses import dataclass
from pathlib import Path

from apps.deriv_worker.demo_session import (
    DemoDerivSession,
    DemoReadOnlyDerivSession,
    DerivAccessTokenProvider,
    DerivOptionsRestClient,
    EnvironmentDerivTokenProvider,
    SecretValue,
)
from apps.deriv_worker.fake_transport import FakeDerivScenario, FakeDerivTransport
from apps.deriv_worker.order_session import DerivLiveOrderSession
from apps.deriv_worker.public_session import PublicDerivSession
from apps.deriv_worker.reconciliation import DerivLiveReconciliationHandler
from apps.deriv_worker.schema import DerivWorkerError
from apps.deriv_worker.server import DerivWorkerServer
from apps.deriv_worker.validators import PUBLIC_WS_URL
from apps.deriv_worker.websocket_client import DerivReadTransport, DerivWebSocketClient
from packages.brokers.deriv.credentials import DerivCredentialVault
from packages.brokers.deriv.product_config import deriv_product_app_id


@dataclass(frozen=True, slots=True)
class _StoredDerivTokenProvider:
    token: SecretValue

    def get_access_token(self) -> SecretValue:
        return self.token


def main() -> int:
    parser = argparse.ArgumentParser(description="DualTrade Deriv worker")
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
        choices=("fake-public", "fake-demo", "live-public", "live-demo", "live-real"),
        default="fake-public",
        help="external modes are explicit opt-in; fake-public remains the default",
    )
    parser.add_argument(
        "--credential-vault-dir",
        type=Path,
        help="DPAPI vault directory used only by the authenticated Deriv worker",
    )
    arguments = parser.parse_args()
    scenario = FakeDerivScenario(arguments.scenario)
    mode = "live-public" if arguments.external_public else str(arguments.deriv_transport)
    try:
        session = _build_session(mode, scenario, arguments.credential_vault_dir)
    except (DerivWorkerError, ValueError):
        return 2
    order_session: DerivLiveOrderSession | None = None
    reconciliation_handler: DerivLiveReconciliationHandler | None = None
    # Real accounts remain read-only in this release. A connected real session must never
    # advertise or construct an order-submission capability.
    if mode == "live-demo":
        account_id = getattr(session, "account_id", "")
        order_session = DerivLiveOrderSession(
            session.transport,
            account_id=str(account_id),
            demo_authenticated=True,
            account_type="demo",
        )
        reconciliation_handler = DerivLiveReconciliationHandler(
            session.transport,
            order_session,
        )
    return DerivWorkerServer(
        arguments.host,
        arguments.port,
        protocol_version=arguments.protocol_version,
        session=session,
        scenario=scenario,
        order_session=order_session,
        reconciliation_handler=reconciliation_handler,
    ).run()


def _build_session(
    mode: str,
    scenario: FakeDerivScenario,
    credential_vault_dir: Path | None = None,
) -> PublicDerivSession:
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
    if mode not in {"live-demo", "live-real"}:
        raise ValueError("DERIV_TRANSPORT_INVALID")
    if credential_vault_dir is not None:
        credentials = DerivCredentialVault(credential_vault_dir).load()
        if credentials is None:
            raise ValueError("DERIV_DEMO_CONFIGURATION_REQUIRED")
        token_provider: DerivAccessTokenProvider = _StoredDerivTokenProvider(
            SecretValue(credentials.access_token.reveal_text())
        )
        expected_type = "real" if mode == "live-real" else "demo"
        if credentials.account_type != expected_type:
            raise ValueError("DERIV_SELECTED_ACCOUNT_TYPE_MISMATCH")
        app_id = deriv_product_app_id()
        account_id = credentials.account_id
    else:
        if os.environ.get("DUALTRADE_RUN_EXTERNAL_DERIV_DEMO") != "1":
            raise ValueError("DERIV_DEMO_OPT_IN_REQUIRED")
        app_id = os.environ.get("DUALTRADE_DERIV_APP_ID", "").strip()
        account_id = os.environ.get("DUALTRADE_DERIV_DEMO_ACCOUNT_ID", "").strip()
        if not app_id or not account_id:
            raise ValueError("DERIV_DEMO_CONFIGURATION_REQUIRED")
        token_provider = EnvironmentDerivTokenProvider()
    bootstrap = DemoDerivSession(
        token_provider,
        DerivOptionsRestClient(),
        app_id,
        expected_account_type="real" if mode == "live-real" else "demo",
    )
    demo_transport: DerivReadTransport = bootstrap.open(account_id)
    return DemoReadOnlyDerivSession(
        demo_transport,
        account_id=account_id,
        account_type="real" if mode == "live-real" else "demo",
    )


if __name__ == "__main__":
    raise SystemExit(main())
