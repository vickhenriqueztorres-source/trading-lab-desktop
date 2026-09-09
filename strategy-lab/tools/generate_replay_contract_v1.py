from __future__ import annotations

import json
from pathlib import Path

from strategy_lab.research.replay_contract import build_replay_contract

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "contracts" / "replay_contract_vectors.v1.json"


def main() -> None:
    payload = build_replay_contract()
    OUTPUT.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"wrote {OUTPUT} sha256={payload['sha256']}")


if __name__ == "__main__":
    main()
