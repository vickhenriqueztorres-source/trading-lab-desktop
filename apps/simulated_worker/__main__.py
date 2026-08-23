from __future__ import annotations

import argparse
import tempfile
from pathlib import Path

from apps.simulated_worker.scenarios import WorkerScenario
from apps.simulated_worker.server import SimulatedWorkerServer


def main() -> int:
    parser = argparse.ArgumentParser(description="DualTrade simulated IPC worker")
    parser.add_argument("--host", required=True)
    parser.add_argument("--port", required=True, type=int)
    parser.add_argument(
        "--scenario",
        choices=[item.value for item in WorkerScenario],
        required=True,
    )
    parser.add_argument("--protocol-version", type=int, required=True)
    parser.add_argument("--broker-store", type=Path)
    arguments = parser.parse_args()
    if arguments.broker_store is not None:
        return SimulatedWorkerServer(
            arguments.host,
            arguments.port,
            protocol_version=arguments.protocol_version,
            scenario=WorkerScenario(arguments.scenario),
            broker_store_path=arguments.broker_store,
        ).run()
    with tempfile.TemporaryDirectory(prefix="dualtrade-simulated-broker-") as directory:
        return SimulatedWorkerServer(
            arguments.host,
            arguments.port,
            protocol_version=arguments.protocol_version,
            scenario=WorkerScenario(arguments.scenario),
            broker_store_path=Path(directory) / "broker_state.db",
        ).run()


if __name__ == "__main__":
    raise SystemExit(main())
