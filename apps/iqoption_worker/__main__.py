from __future__ import annotations

import argparse
import sys

from apps.iqoption_worker.order_session import IQOptionOrderSession
from apps.iqoption_worker.reconciliation import IQOptionReconciliationHandler
from apps.iqoption_worker.schema import IQOptionWorkerError
from apps.iqoption_worker.server import IQOptionWorkerServer
from packages.brokers.iqoption.fake_transport import FakeIQOptionScenario, FakeIQOptionTransport
from packages.brokers.iqoption.session import IQOptionPracticeSession


def main() -> int:
    parser = argparse.ArgumentParser(description="DualTrade IQ Option practice worker")
    parser.add_argument("--host", required=True)
    parser.add_argument("--port", required=True, type=int)
    parser.add_argument("--protocol-version", required=True, type=int)
    parser.add_argument(
        "--scenario",
        choices=[item.value for item in FakeIQOptionScenario],
        default=FakeIQOptionScenario.NORMAL.value,
    )
    arguments = parser.parse_args()
    scenario = FakeIQOptionScenario(arguments.scenario)

    transport = FakeIQOptionTransport(scenario=scenario, practice_mode=True)
    session = IQOptionPracticeSession(transport)
    order_session = IQOptionOrderSession(transport, practice_mode=True)
    reconciliation = IQOptionReconciliationHandler(transport, order_session)

    server = IQOptionWorkerServer(
        host=arguments.host,
        port=arguments.port,
        protocol_version=arguments.protocol_version,
        session=session,
        order_session=order_session,
        reconciliation_handler=reconciliation,
        scenario=scenario,
    )
    try:
        return server.run()
    except (IQOptionWorkerError, ValueError):
        return 2


if __name__ == "__main__":
    sys.exit(main())
