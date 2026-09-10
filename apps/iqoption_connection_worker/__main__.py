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
from packages.brokers.iqoption.ssid_store import SsidStore


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
    parser.add_argument(
        "--http-login-policy",
        choices=("allow", "deny"),
        default="allow",
        help="Whether this worker may use broker HTTP login after cached-session recovery",
    )
    arguments = parser.parse_args()

    try:
        credentials = IQOptionCredentialVault(arguments.vault_dir).load()
        if credentials is None:
            return 4
        mode = IQOptionAccountMode(arguments.account_mode)
        if credentials.account_mode != mode.value:
            return 5
        ssid_store = SsidStore(arguments.vault_dir)
        stored_session = ssid_store.load(mode.value)
        session = IQOptionCommunityReadOnlySession(
            credentials.email,
            credentials.password,
            mode,
            initial_ssid=None if stored_session is None else stored_session.ssid,
            allow_http_login=arguments.http_login_policy == "allow",
            on_ssid_ready=lambda ssid: ssid_store.save(ssid, mode.value),
            on_ssid_invalid=lambda: ssid_store.clear(mode.value),
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
