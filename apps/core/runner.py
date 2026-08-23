from __future__ import annotations

import json
import signal
import sys
from pathlib import Path
from typing import TextIO

from apps.core.lifecycle_server import CoreLifecycleServer
from apps.core.lifecycle_service import CoreLifecycleService
from packages.security import SecretValue

_MAX_STARTUP_BYTES = 16 * 1024


class CoreStartupError(RuntimeError):
    reason_code = "CORE_STARTUP_INVALID"


def _read_startup(
    stream: TextIO,
) -> tuple[Path, tuple[str, ...], SecretValue, SecretValue, bool, str]:
    line = stream.readline(_MAX_STARTUP_BYTES + 1)
    if not line or len(line.encode("utf-8")) > _MAX_STARTUP_BYTES:
        raise CoreStartupError(CoreStartupError.reason_code)
    try:
        document = json.loads(line)
    except json.JSONDecodeError as exc:
        raise CoreStartupError(CoreStartupError.reason_code) from exc
    if not isinstance(document, dict) or set(document) != {
        "force_auth_simulation",
        "deriv_transport",
        "profile_dir",
        "session_token",
        "ui_session_token",
        "workers",
    }:
        raise CoreStartupError(CoreStartupError.reason_code)
    profile = document["profile_dir"]
    workers = document["workers"]
    token = document["session_token"]
    ui_token = document["ui_session_token"]
    force_simulation = document["force_auth_simulation"]
    deriv_transport = document["deriv_transport"]
    if (
        not isinstance(profile, str)
        or not profile.strip()
        or "\x00" in profile
        or not isinstance(workers, list)
        or not 1 <= len(workers) <= 3
        or not all(isinstance(item, str) for item in workers)
        or not isinstance(token, str)
        or len(token) != 64
        or not isinstance(ui_token, str)
        or len(ui_token) != 64
        or not isinstance(force_simulation, bool)
        or deriv_transport
        not in {
            "fake-public",
            "fake-demo",
            "live-public",
            "live-demo",
        }
    ):
        raise CoreStartupError(CoreStartupError.reason_code)
    try:
        bytes.fromhex(token)
        bytes.fromhex(ui_token)
        parsed_workers = tuple(str(item) for item in workers)
        if "simulated" not in parsed_workers or len(parsed_workers) != len(set(parsed_workers)):
            raise ValueError
        if not set(parsed_workers) <= {"simulated", "deriv_read_only", "iqoption"}:
            raise ValueError
    except ValueError as exc:
        raise CoreStartupError(CoreStartupError.reason_code) from exc
    return (
        Path(profile),
        parsed_workers,
        SecretValue.from_text(token),
        SecretValue.from_text(ui_token),
        force_simulation,
        str(deriv_transport),
    )


def main() -> int:
    service: CoreLifecycleService | None = None
    server: CoreLifecycleServer | None = None
    try:
        (
            profile,
            workers,
            session_token,
            ui_session_token,
            force_simulation,
            deriv_transport,
        ) = _read_startup(sys.stdin)
        service = CoreLifecycleService(
            profile,
            workers,
            force_auth_simulation=force_simulation,
            ui_session_token=ui_session_token,
            deriv_transport=deriv_transport,
        )
        service.start()
        server = CoreLifecycleServer(service, session_token)
    except (CoreStartupError, OSError, RuntimeError, ValueError):
        if service is not None:
            service.emergency_shutdown()
        print("CORE_STARTUP_FAILED", file=sys.stderr, flush=True)
        return 2
    print(
        json.dumps({"port": server.port, "ui_port": service.ui_port}, separators=(",", ":")),
        flush=True,
    )
    signal.signal(signal.SIGTERM, lambda _signum, _frame: server.stop())
    try:
        server.serve_forever()
    finally:
        if service.state.value != "STOPPED":
            service.emergency_shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
