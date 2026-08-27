from __future__ import annotations

import json
import signal
import sys
from datetime import timedelta
from pathlib import Path
from typing import TextIO

from apps.auth_agent.server import AuthAgentServer
from packages.identity import OtpCode
from packages.security import SecretValue

_MAX_STARTUP_BYTES = 16 * 1024


class AuthAgentStartupError(RuntimeError):
    reason_code = "AUTH_AGENT_STARTUP_INVALID"

    def __init__(self) -> None:
        super().__init__(self.reason_code)


def _read_startup(
    stream: TextIO,
) -> tuple[SecretValue, Path, bool, bool, OtpCode | None, timedelta]:
    line = stream.readline(_MAX_STARTUP_BYTES + 1)
    if not line or len(line.encode("utf-8")) > _MAX_STARTUP_BYTES:
        raise AuthAgentStartupError()
    try:
        document = json.loads(line)
    except json.JSONDecodeError as exc:
        raise AuthAgentStartupError() from exc
    if not isinstance(document, dict) or set(document) != {
        "force_simulation",
        "allow_real_mode",
        "lease_ttl_seconds",
        "profile_dir",
        "session_token",
        "test_otp",
    }:
        raise AuthAgentStartupError()
    token = document["session_token"]
    profile_dir = document["profile_dir"]
    force_simulation = document["force_simulation"]
    allow_real_mode = document["allow_real_mode"]
    test_otp = document["test_otp"]
    lease_ttl_seconds = document["lease_ttl_seconds"]
    if (
        not isinstance(token, str)
        or len(token) != 64
        or not isinstance(profile_dir, str)
        or not profile_dir.strip()
        or not isinstance(force_simulation, bool)
        or not isinstance(allow_real_mode, bool)
        or (test_otp is not None and not isinstance(test_otp, str))
        or isinstance(lease_ttl_seconds, bool)
        or not isinstance(lease_ttl_seconds, int | float)
        or not 0 < lease_ttl_seconds <= 7 * 24 * 60 * 60
    ):
        raise AuthAgentStartupError()
    try:
        bytes.fromhex(token)
        parsed_otp = None if test_otp is None else OtpCode(test_otp)
    except ValueError as exc:
        raise AuthAgentStartupError() from exc
    return (
        SecretValue.from_text(token),
        Path(profile_dir),
        force_simulation,
        allow_real_mode,
        parsed_otp,
        timedelta(seconds=float(lease_ttl_seconds)),
    )


def main() -> int:
    try:
        (
            session_token,
            profile_dir,
            force_simulation,
            allow_real_mode,
            test_otp,
            lease_ttl,
        ) = _read_startup(sys.stdin)
        server = AuthAgentServer(
            session_token,
            profile_dir,
            force_simulation=force_simulation,
            test_otp=test_otp,
            lease_ttl=lease_ttl,
            allow_real_mode=allow_real_mode,
        )
    except (AuthAgentStartupError, OSError, RuntimeError, ValueError):
        print("AUTH_AGENT_STARTUP_FAILED", file=sys.stderr, flush=True)
        return 2
    print(json.dumps({"port": server.port}, separators=(",", ":")), flush=True)
    signal.signal(signal.SIGTERM, lambda _signum, _frame: server.stop())
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
