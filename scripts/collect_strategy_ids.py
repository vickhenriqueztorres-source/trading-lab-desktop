"""Utility to collect all strategy IDs across the repository and export entitlements.

Scans:
- Deriv digit strategies in packages/strategies/deriv_digits.py
- IQ Option demo strategies in packages/strategies/iqoption_rsi.py
- Strategy manifest definitions in data/manifest.json
- Family definitions in apps/core/families
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def collect_strategy_ids(repo_root: Path) -> list[str]:
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))

    ids: set[str] = set()

    # 1. Deriv Digit Strategies
    deriv_file = repo_root / "packages" / "strategies" / "deriv_digits.py"
    if deriv_file.exists():
        from packages.strategies.deriv_digits import DerivDigitStrategyId

        for s in DerivDigitStrategyId:
            ids.add(s.value)

    # 2. IQ Option RSI Demo
    iq_rsi_file = repo_root / "packages" / "strategies" / "iqoption_rsi.py"
    if iq_rsi_file.exists():
        from packages.strategies.iqoption_rsi import IQOPTION_RSI_STRATEGY_ID

        ids.add(IQOPTION_RSI_STRATEGY_ID)

    # 3. Strategy Manifest (data/manifest.json)
    manifest_file = repo_root / "data" / "manifest.json"
    if manifest_file.exists():
        manifest_data = json.loads(manifest_file.read_text(encoding="utf-8"))
        for item in manifest_data.get("strategies", []):
            if "key" in item and item["key"].strip():
                ids.add(item["key"].strip())
            if "family" in item and item["family"].strip():
                ids.add(item["family"].strip())

    # 4. Core legacy / test fallback packs
    ids.add("core")
    ids.add("strategy-test")

    return sorted(ids)


def generate_entitlements_module(strategy_ids: list[str]) -> str:
    lines = [
        '"""Entitlements and default strategy packs for Trading Lab licenses."""',
        "",
        "from __future__ import annotations",
        "",
        'PRO_BROKERS: tuple[str, ...] = ("DERIV", "IQ_OPTION")',
        "",
        "PRO_STRATEGY_PACKS: tuple[str, ...] = (",
    ]
    for strategy_id in strategy_ids:
        lines.append(f'    "{strategy_id}",')
    lines.extend(
        [
            ")",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    repo_root = Path(__file__).resolve().parent.parent
    strategy_ids = collect_strategy_ids(repo_root)

    print(f"Collected {len(strategy_ids)} unique strategy IDs:")
    for s_id in strategy_ids:
        print(f"  - {s_id}")

    target_file = repo_root / "apps" / "license_server" / "entitlements.py"
    target_file.parent.mkdir(parents=True, exist_ok=True)
    content = generate_entitlements_module(strategy_ids)
    target_file.write_text(content, encoding="utf-8")
    print(f"\nWritten to {target_file}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
