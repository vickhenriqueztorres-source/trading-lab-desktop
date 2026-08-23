from __future__ import annotations

import json
import signal
import sys
import time
from pathlib import Path

from apps.launcher.supervisor import ProcessTreeSupervisor


def main() -> int:
    if len(sys.argv) != 2:
        return 2
    supervisor = ProcessTreeSupervisor(Path(sys.argv[1]))
    if not supervisor.start_all():
        return 3
    snapshot = supervisor.snapshot()
    print(
        json.dumps(
            {
                role.value: status.pid
                for role, status in snapshot.processes.items()
                if status.pid is not None
            },
            sort_keys=True,
            separators=(",", ":"),
        ),
        flush=True,
    )
    signal.signal(signal.SIGTERM, lambda _signum, _frame: None)
    while True:
        time.sleep(0.1)


if __name__ == "__main__":
    raise SystemExit(main())
