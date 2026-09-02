from __future__ import annotations

import argparse
import sys
from pathlib import Path

from apps.iqoption_connection_worker.server import IQOptionReadOnlyWorkerServer
from packages.brokers.iqoption.community_read_only import (
    IQOptionAccountMode,
    IQOptionCommunityReadOnlySession,
)
from packages.brokers.iqoption.credentials import IQOptionCredentialVault


def main() -> int:
    parser = argparse.ArgumentParser(description="Trading Lab IQ Option read-only worker")
    parser.add_argument("--host", required=True)
    parser.add_argument("--port", required=True, type=int)
    parser.add_argument("--protocol-version", required=True, type=int)
    parser.add_argument("--vault-dir", required=True, type=Path)
    parser.add_argument(
        "--account-mode",
        required=True,
        choices=tuple(item.value for item in IQOptionAccountMode),
    )
    arguments = parser.parse_args()

    try:
        credentials = IQOptionCredentialVault(arguments.vault_dir).load()
        if credentials is None:
            return 4
        mode = IQOptionAccountMode(arguments.account_mode)
        if credentials.account_mode != mode.value:
            return 5
        session = IQOptionCommunityReadOnlySession(
            credentials.email,
            credentials.password,
            mode,
        )
        server = IQOptionReadOnlyWorkerServer(
            arguments.host,
            arguments.port,
            arguments.protocol_version,
            session,
            connection_mode=(
                "DEMO_AUTH_READ_ONLY"
                if mode is IQOptionAccountMode.PRACTICE
                else "REAL_AUTH_READ_ONLY"
            ),
        )
        return server.run()
    except (OSError, RuntimeError, ValueError):
        return 6


if __name__ == "__main__":
    sys.exit(main())
