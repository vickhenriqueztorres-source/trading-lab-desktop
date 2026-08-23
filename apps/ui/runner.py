from __future__ import annotations

import json
import sys
import time
from typing import TextIO

from apps.ui.app import TradingLabMainWindow
from apps.ui.controller import UiController
from apps.ui.ipc_client import UiIpcClient, UiIpcError
from packages.security import SecretValue

_MAX_STARTUP_BYTES = 4096


def _read_startup(stream: TextIO) -> tuple[int, SecretValue, bool]:
    line = stream.readline(_MAX_STARTUP_BYTES + 1)
    if not line or len(line.encode("utf-8")) > _MAX_STARTUP_BYTES:
        raise ValueError("UI_STARTUP_INVALID")
    document = json.loads(line)
    if not isinstance(document, dict) or set(document) != {"headless", "port", "session_token"}:
        raise ValueError("UI_STARTUP_INVALID")
    port = document["port"]
    token = document["session_token"]
    headless = document["headless"]
    if (
        type(port) is not int
        or not 0 < port <= 65535
        or not isinstance(token, str)
        or len(token) != 64
        or not isinstance(headless, bool)
    ):
        raise ValueError("UI_STARTUP_INVALID")
    bytes.fromhex(token)
    return port, SecretValue.from_text(token), headless


def main() -> int:
    controller: UiController | None = None
    try:
        port, token, headless = _read_startup(sys.stdin)
        client = UiIpcClient.connect(port, token)
        controller = UiController(client)
        controller.start()
    except (json.JSONDecodeError, OSError, UiIpcError, ValueError):
        print("UI_STARTUP_FAILED", file=sys.stderr, flush=True)
        return 2
    print(json.dumps({"status": "ready"}, separators=(",", ":")), flush=True)
    try:
        if headless:
            while controller.connected:
                time.sleep(0.1)
        else:
            from PySide6.QtWidgets import QApplication

            app = QApplication.instance() or QApplication(sys.argv)
            window = TradingLabMainWindow(controller)
            window.show()
            app.exec()
    finally:
        controller.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
