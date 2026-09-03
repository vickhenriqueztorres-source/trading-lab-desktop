"""R-MAN-1..7: statement coverage with stdlib trace, no added coverage dependency."""

import sys
import trace
from pathlib import Path

import pytest


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    package = root / "packages/manifest_schema/manifest_schema"
    tracer = trace.Trace(count=True, trace=False, ignoredirs=(sys.prefix, sys.base_prefix))
    status = tracer.runfunc(pytest.main, [str(root / "packages/manifest_schema/tests"), "-q"])
    if status:
        return int(status)
    counts = tracer.results().counts
    hit, total = 0, 0
    for path in sorted(package.glob("*.py")):
        # trace exposes its own executable-line analysis; this is a local diagnostic CLI.
        executable = set(trace._find_executable_linenos(str(path)))
        visited = {line for (name, line), count in counts.items() if count and name == str(path)}
        hit += len(executable & visited)
        total += len(executable)
        missing = sorted(executable - visited)
        if missing:
            print(f"{path.name}: uncovered={missing}")
    hundredths = hit * 10000 // total
    print(f"MANIFEST_STATEMENT_COVERAGE={hit}/{total}={hundredths // 100}.{hundredths % 100:02d}%")
    return 0 if hit * 100 >= total * 90 else 1


if __name__ == "__main__":
    sys.exit(main())
